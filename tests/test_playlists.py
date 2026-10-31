"""Playlists (歌单): kept in the library, changed from the phone, played from the phone and by her. A playlist is
not the list that plays (播放列表): playing one fills that list, and changing either leaves the other alone."""
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

import places  # noqa: F401  (first: the library, data and cache folders are throwaway ones)
from pianasoul import server
from pianasoul.chat import Assistant
from pianasoul.library import Library
from pianasoul.playback import Playback
from test_app import Base, Scripted
from test_server import ORIGIN, signed_in


class LibraryPlaylistTests(Base):
    def test_a_playlist_keeps_each_piece_once_in_its_order(self):
        a, b, c = self.ids
        made = self.lib.new_playlist(' 睡前 ', [b, a, b, 9999])
        self.assertEqual((made['name'], made['ids']), ('睡前', [b, a]), 'Trimmed; each piece once; only pieces there are')
        pid = made['id']
        self.assertEqual(self.lib.add_to_playlist(pid, [a, c, c]), [c], 'Only what is not there yet')
        self.assertEqual(self.lib.playlist(pid)['ids'], [b, a, c])
        self.assertTrue(self.lib.move_in_playlist(pid, c, 0))
        self.assertEqual(self.lib.playlist(pid)['ids'], [c, b, a])
        self.assertTrue(self.lib.move_in_playlist(pid, c, 99))
        self.assertEqual(self.lib.playlist(pid)['ids'], [b, a, c], 'Past the end: last')
        self.assertTrue(self.lib.remove_from_playlist(pid, b))
        self.assertEqual(self.lib.playlist(pid)['ids'], [a, c])
        self.assertFalse(self.lib.remove_from_playlist(pid, b), 'Not in it any more')
        self.assertFalse(self.lib.move_in_playlist(pid, b, 0))

    def test_newest_first_named_as_given_and_kept(self):
        first = self.lib.new_playlist('早晨')
        second = self.lib.new_playlist('早晨')  # a name may be used twice
        self.assertEqual([p['id'] for p in self.lib.playlists()], [second['id'], first['id']])
        self.assertEqual(self.lib.rename_playlist(first['id'], ' 晚上 ')['name'], '晚上')
        for bad in ('', '   '):
            with self.assertRaises(ValueError):
                self.lib.new_playlist(bad)
            with self.assertRaises(ValueError):
                self.lib.rename_playlist(first['id'], bad)
        again = Library(self.root / 'library.db', self.root / 'Music')
        self.addCleanup(again.close)
        self.assertEqual([p['name'] for p in again.playlists()], ['早晨', '晚上'], 'Kept in the library')

    def test_a_deleted_piece_leaves_every_playlist_and_a_deleted_playlist_leaves_the_pieces(self):
        a, b, c = self.ids
        one, two = self.lib.new_playlist('一', [a, b]), self.lib.new_playlist('二', [b])
        self.lib.delete(b)
        self.assertEqual(self.lib.playlist(one['id'])['ids'], [a])
        self.assertEqual(self.lib.playlist(two['id'])['ids'], [])
        self.assertTrue(self.lib.delete_playlist(one['id']))
        self.assertIsNone(self.lib.playlist(one['id']))
        self.assertIsNotNone(self.lib.get(a), 'Its pieces stay in the library')
        self.assertFalse(self.lib.delete_playlist(one['id']))

    def test_a_playlist_that_is_not_there_changes_nothing(self):
        self.assertIsNone(self.lib.rename_playlist(42, 'x'))
        self.assertIsNone(self.lib.add_to_playlist(42, self.ids))
        self.assertFalse(self.lib.remove_from_playlist(42, self.ids[0]))
        self.assertFalse(self.lib.move_in_playlist(42, self.ids[0], 0))


class PlaylistApiTests(Base):
    def setUp(self):
        super().setUp()
        from pianasoul.core import Core
        for patcher in (patch.object(Playback, 'play'), patch.object(Playback, 'check_connection')):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.core = Core(lib=self.lib, chat=(Scripted(), 'test'))
        self.core.playback.port = Mock()
        self.addCleanup(self.core.close)
        patcher = patch.object(server, 'core', self.core)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.events = []
        self.core.listeners.append(self.events.append)
        self.client = signed_in(TestClient(server.app, headers=ORIGIN))

    def test_made_changed_and_deleted_from_the_phone_which_hears_each_change(self):
        a, b, c = self.ids
        made = self.client.post('/api/playlists', json={'name': '睡前', 'ids': [a]}).json()
        pid = made['id']
        self.assertEqual(self.client.get('/api/state').json()['playlists'], [made])
        for change, ids in (({'add': [b, c, a]}, [a, b, c]), ({'move': c, 'index': 0}, [c, a, b]),
                            ({'remove': a}, [c, b])):
            self.events.clear()
            self.assertEqual(self.client.patch(f'/api/playlists/{pid}', json=change).json()['ids'], ids, change)
            self.assertEqual(self.events, [{'type': 'playlists', 'playlists': self.lib.playlists()}])
        self.assertEqual(self.client.patch(f'/api/playlists/{pid}', json={'name': '晚安'}).json()['name'], '晚安')
        self.assertEqual(self.client.delete(f'/api/playlists/{pid}').json(), {'ok': True})
        self.assertEqual(self.client.get('/api/playlists').json(), [])

    def test_what_cannot_be_done_is_refused(self):
        pid = self.client.post('/api/playlists', json={'name': 'x'}).json()['id']
        self.assertEqual(self.client.post('/api/playlists', json={'name': '  '}).status_code, 400)
        self.assertEqual(self.client.patch(f'/api/playlists/{pid}', json={'name': ''}).status_code, 400)
        self.assertEqual(self.client.patch(f'/api/playlists/{pid}', json={'move': self.ids[0], 'index': 0}).status_code, 404,
                         'Not in it')
        self.assertEqual(self.client.patch(f'/api/playlists/{pid}', json={'shuffle': True}).status_code, 400)
        for request in (lambda: self.client.patch('/api/playlists/999', json={'name': 'y'}),
                        lambda: self.client.delete('/api/playlists/999')):
            self.assertEqual(request().status_code, 404)

    def test_play_plays_it_in_order_and_shuffle_shuffles_it(self):
        a, b, c = self.ids
        pid = self.client.post('/api/playlists', json={'name': '睡前', 'ids': [c, a, b]}).json()['id']
        scope = {'kind': 'playlist', 'id': pid}
        snap = self.client.post('/api/play', json={'id': c, 'ids': [c, a, b], 'scope': scope, 'mode': 'shuffle'}).json()
        self.assertEqual((snap['id'], snap['mode'], snap['scope'], sorted(snap['ids'])), (c, 'shuffle', scope, sorted([a, b, c])))
        self.assertEqual(snap['ids'][0], c, 'The piece chosen first')
        snap = self.client.post('/api/play', json={'id': c, 'ids': [c, a, b], 'scope': scope, 'mode': 'order'}).json()
        self.assertEqual((snap['mode'], snap['ids']), ('order', [c, a, b]))
        self.assertEqual(self.client.post('/api/play', json={'id': c, 'mode': 'loud'}).status_code, 400)
        self.client.post('/api/playback', json={'action': 'remove', 'value': b})
        self.assertEqual(self.lib.playlist(pid)['ids'], [c, a, b], 'The list that plays is not the playlist')


class HerPlaylistTests(Base):
    def setUp(self):
        super().setUp()
        self.p = Playback(self.lib, port=Mock())
        self.addCleanup(self.p.close)
        patch.object(Playback, 'play').start()
        self.connection = patch.object(self.p, 'check_connection').start()
        self.addCleanup(patch.stopall)
        self.a = Assistant(self.lib, self.p)
        self.a.on_message = lambda message: None
        self.asked = []

    def call(self, tool, **arguments):
        return SimpleNamespace(name=tool, arguments=arguments)

    def ask(self, text, *steps):
        chat = Scripted(*steps)
        send = chat.send_user
        chat.send_user = lambda message: (self.asked.append(message), send(message))[1]
        return self.a.ask(chat, text, typed=True)

    def test_she_knows_the_playlists_and_plays_one_in_its_order(self):
        a, b, c = self.ids
        pid = self.lib.new_playlist('睡前', [c, a])['id']
        self.ask('放我的睡前歌单', [self.call('play_playlist', name='睡前')], '好。')
        self.assertIn('"playlists": ["睡前"]', self.asked[0])
        self.assertEqual((self.p.current_id, self.p.ids, self.p.scope, self.p.mode),
                         (c, [c, a], {'kind': 'playlist', 'id': pid}, 'order'))

    def test_shuffled_when_asked_and_found_whatever_the_case_or_spaces(self):
        a, b, c = self.ids
        self.lib.new_playlist('Morning Calm', [a, b, c])
        self.ask('shuffle morning calm', [self.call('play_playlist', name=' morning calm ', shuffle=True)], 'OK.')
        self.assertEqual((self.p.mode, sorted(self.p.ids)), ('shuffle', sorted(self.ids)))
        self.assertEqual(self.p.ids[0], self.p.current_id)
        self.ask('play morning calm', [self.call('play_playlist', name='Morning Calm')], 'OK.')
        self.assertEqual((self.p.mode, self.p.ids, self.p.current_id), ('order', [a, b, c], a), 'Play turns shuffle off')

    def test_of_two_with_one_name_the_one_changed_last(self):
        a, b, c = self.ids
        older = self.lib.new_playlist('睡前', [a])['id']
        self.lib.new_playlist('睡前', [b])
        self.lib.add_to_playlist(older, [c])
        self.ask('放睡前', [self.call('play_playlist', name='睡前')], '好。')
        self.assertEqual(self.p.scope['id'], older)

    def test_a_playlist_not_there_empty_or_with_no_file_left_plays_nothing(self):
        a, b, c = self.ids
        self.lib.new_playlist('空的')
        gone = self.lib.new_playlist('文件没了', [a])['id']
        Path(self.lib.get(a)['midi_path']).unlink()
        results = {name: self.a.play_playlist(name) for name in ('不存在', '空的', '文件没了')}
        self.assertEqual(results['不存在'], {'error': 'playlist_not_found', 'playlists': ['文件没了', '空的']})
        self.assertEqual(results['空的'], {'error': 'playlist_empty'})
        self.assertEqual(results['文件没了'], {'error': 'playlist_empty'})
        self.assertIsNone(self.p.current_id)
        self.assertTrue(self.lib.playlist(gone)['ids'], 'The playlist keeps it, for when its file is back')

    def test_with_the_piano_away_she_plays_the_playlist_once_it_is_back(self):
        a, b, c = self.ids
        pid = self.lib.new_playlist('睡前', [b, c])['id']
        with patch.object(self.p, 'look_for_piano', side_effect=LookupError('no piano')):
            result = self.a.play_playlist('睡前')
        self.assertEqual(result['error'], 'playback_connection_unavailable')
        self.assertIsNone(self.p.current_id)
        self.a.control_playback('retry')
        self.assertEqual((self.p.current_id, self.p.ids, self.p.scope), (b, [b, c], {'kind': 'playlist', 'id': pid}))


if __name__ == '__main__':
    unittest.main()
