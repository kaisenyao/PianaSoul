"""Artwork: a piece's (kept in the library folder by its content), a playlist's (beside the playlists), an artist's
picture (in the library folder by name); set and taken away from the phone."""
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

import places  # noqa: F401  (first: the library, data and cache folders are throwaway ones)
from pianasoul import server
from pianasoul.library import Library
from pianasoul.playback import Playback
from test_app import Base, Scripted
from test_library import make_midi
from test_server import ORIGIN, signed_in

JPEG = b'\xff\xd8\xff\xe0' + b'picture' * 10


class LibraryArtworkTests(Base):
    def test_a_pieces_artwork_follows_its_file_wherever_it_goes(self):
        a = self.ids[0]
        self.assertIsNone(self.lib.get(a)['artwork'])
        self.assertTrue(self.lib.set_piece_artwork(a, JPEG))
        rec = self.lib.get(a)
        kept = self.root / 'Music' / 'Artwork' / 'Pieces' / f"{rec['fingerprint']}.jpg"
        self.assertEqual(kept.read_bytes(), JPEG, 'In the library folder, by its content')
        self.assertTrue(rec['artwork'])
        moved = self.root / 'Music' / 'Elsewhere' / 'renamed.mid'
        moved.parent.mkdir()
        Path(rec['midi_path']).rename(moved)
        self.lib.move(a, moved)
        self.assertEqual(self.lib.get(a)['artwork'], rec['artwork'], 'Renamed and moved, it is still its')
        fresh = Library(self.root / 'another.db', self.root / 'Music')  # another Mac, a new library, the same folder
        self.addCleanup(fresh.close)
        self.assertTrue(fresh.get(fresh.add(moved, 'x')['id'])['artwork'])

    def test_changed_content_takes_the_artwork_along_and_a_deleted_piece_takes_it_to_the_trash(self):
        a = self.ids[0]
        self.lib.set_piece_artwork(a, JPEG)
        make_midi(Path(self.lib.get(a)['midi_path']), note=72)  # the file changes where it is
        self.lib.refresh(a)
        artwork = self.lib.piece_artwork(self.lib.get(a))
        self.assertEqual(artwork.read_bytes(), JPEG)
        with patch('pianasoul.library.send2trash') as trash:
            self.lib.delete(a)
        self.assertIn(str(artwork), [c.args[0] for c in trash.call_args_list], 'With its file, to the Trash')

    def test_two_pieces_of_one_content_keep_it_when_one_changes_and_none_takes_anothers(self):
        a, b, c = self.ids
        copy = Path(self.lib.get(a)['midi_path']).with_name('copy.mid')
        copy.write_bytes(Path(self.lib.get(a)['midi_path']).read_bytes())
        twin = self.lib.add(copy, 'copy')['id']  # a copy of a, as the folder takes one in
        self.lib.set_piece_artwork(a, JPEG)
        make_midi(Path(self.lib.get(a)['midi_path']), note=73)  # a is edited
        self.lib.refresh(a)
        self.assertEqual(self.lib.piece_artwork(self.lib.get(a)).read_bytes(), JPEG, 'It goes along...')
        self.assertEqual(self.lib.piece_artwork(self.lib.get(twin)).read_bytes(), JPEG, '...and the copy keeps it')
        self.lib.set_piece_artwork(c, JPEG + b'c')
        own = self.lib.piece_artwork(self.lib.get(a))
        Path(self.lib.get(a)['midi_path']).write_bytes(Path(self.lib.get(c)['midi_path']).read_bytes())  # a becomes c
        with patch('pianasoul.library.send2trash') as trash:
            self.lib.refresh(a)
        self.assertEqual(self.lib.piece_artwork(self.lib.get(c)).read_bytes(), JPEG + b'c', "c's is not overwritten")
        trash.assert_called_once_with(str(own))  # and a's own is not lost: it can be put back

    def test_a_file_added_again_with_new_content_takes_its_artwork_along(self):
        a = self.ids[0]
        self.lib.set_piece_artwork(a, JPEG)
        path = Path(self.lib.get(a)['midi_path'])
        make_midi(path, note=74)  # transcribed again, to the same place
        self.lib.add(path, 'again')
        self.assertEqual(self.lib.piece_artwork(self.lib.get(a)).read_bytes(), JPEG)

    def test_taken_away_and_only_for_a_piece_that_is_there(self):
        a = self.ids[0]
        self.lib.set_piece_artwork(a, JPEG)
        self.lib.set_piece_artwork(a, None)
        self.assertIsNone(self.lib.get(a)['artwork'])
        Path(self.lib.get(a)['midi_path']).unlink()
        self.assertFalse(self.lib.set_piece_artwork(a, JPEG))
        self.assertFalse(self.lib.set_piece_artwork(999, JPEG))

    def test_a_playlists_artwork_and_an_artists_picture(self):
        pid = self.lib.new_playlist('睡前')['id']
        self.assertTrue(self.lib.set_playlist_cover(pid, JPEG))
        self.assertTrue(self.lib.playlist(pid)['artwork'])
        self.assertFalse(self.lib.set_playlist_cover(999, JPEG))
        self.lib.delete_playlist(pid)
        self.assertFalse(self.lib.playlist_cover(pid).exists(), 'It goes with its playlist')
        self.lib.set_artist_picture('AC/DC', JPEG)
        self.assertEqual(self.lib.artist_picture('AC/DC').parent, (self.root / 'Music' / 'Artwork' / 'Artists').resolve())
        self.assertEqual(set(self.lib.pictures(['AC/DC', '周杰伦'])), {'AC/DC'})
        self.lib.set_artist_picture('AC/DC', None)
        self.assertEqual(self.lib.pictures(['AC/DC']), {})
        long = '周杰伦' * 120  # far over a file name's 255 bytes
        self.assertEqual(self.lib.pictures([long]), {}, 'No picture, and no failure')
        self.lib.set_artist_picture(long, JPEG)
        self.assertEqual(list(self.lib.pictures([long])), [long])

    def test_an_artists_description_by_name(self):
        self.lib.set_artist_description('周杰伦', ' 华语流行。\n也写钢琴曲 ')
        self.assertEqual(self.lib.descriptions(['周杰伦', 'Adele']), {'周杰伦': '华语流行。\n也写钢琴曲'})
        self.lib.set_artist_description('周杰伦', '另一段')
        self.assertEqual(self.lib.descriptions(['周杰伦']), {'周杰伦': '另一段'})
        with self.assertRaises(ValueError):
            self.lib.set_artist_description('周杰伦', 'x' * 1001)
        self.lib.set_artist_description('周杰伦', '  ')
        self.assertEqual(self.lib.descriptions(['周杰伦']), {}, 'Empty: taken away')

    def test_the_photo_a_picture_was_made_from_goes_when_the_picture_is_replaced_or_taken_away(self):
        import hashlib
        a = self.ids[0]
        digest = lambda jpeg: hashlib.sha256(jpeg).hexdigest()
        self.lib.set_piece_artwork(a, JPEG)
        self.lib.keep_picture_original(digest(JPEG), JPEG + b'photo', {'x': 10, 'y': 20.5, 'side': 300})
        path, at = self.lib.picture_original(digest(JPEG))
        self.assertEqual((path.read_bytes(), at), (JPEG + b'photo', {'x': 10, 'y': 20.5, 'side': 300}))
        self.lib.set_piece_artwork(a, JPEG)  # the same picture again: its photo stays
        self.assertIsNotNone(self.lib.picture_original(digest(JPEG)))
        self.lib.set_piece_artwork(a, JPEG + b'2')  # another: the old one's photo goes
        self.assertIsNone(self.lib.picture_original(digest(JPEG)))
        self.assertFalse(path.exists())
        self.lib.set_artist_picture('周杰伦', JPEG)
        self.lib.keep_picture_original(digest(JPEG), JPEG, {'x': 0, 'y': 0, 'side': 1})
        self.lib.set_artist_picture('周杰伦', None)
        self.assertIsNone(self.lib.picture_original(digest(JPEG)))
        for bad in (('../x', {'x': 0, 'y': 0, 'side': 1}), (digest(JPEG), {'x': -1, 'y': 0, 'side': 1}),
                    (digest(JPEG), {'x': 0, 'y': 0, 'side': 0})):
            with self.assertRaises(ValueError):
                self.lib.keep_picture_original(*bad[:1], JPEG, bad[1])

    def test_a_new_playlist_does_not_take_the_artwork_of_one_deleted_with_its_id(self):
        made = self.lib.new_playlist('一')
        self.lib.set_playlist_cover(made['id'], JPEG)
        self.lib.execute('DELETE FROM playlists WHERE id = ?', (made['id'],))  # gone without its file, as in a crash
        again = self.lib.new_playlist('二')
        self.assertEqual(again['id'], made['id'], 'The id is used again')
        self.assertIsNone(self.lib.playlist(again['id'])['artwork'])


class ArtworkApiTests(Base):
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

    def test_set_seen_and_taken_away_from_the_phone(self):
        a = self.ids[0]
        self.assertEqual(self.client.get(f'/api/recordings/{a}/artwork').status_code, 404)
        self.assertEqual(self.client.put(f'/api/recordings/{a}/artwork', content=JPEG).json(), {'ok': True})
        self.assertEqual(self.events[-1], {'type': 'library', 'pictures': {}, 'descriptions': {}})
        self.assertEqual(self.client.get(f'/api/recordings/{a}/artwork').content, JPEG)
        self.assertTrue(next(r for r in self.client.get('/api/state').json()['recordings'] if r['id'] == a)['artwork'])
        self.client.delete(f'/api/recordings/{a}/artwork')
        self.assertEqual(self.client.get(f'/api/recordings/{a}/artwork').status_code, 404)

    def test_only_a_jpeg_of_a_sensible_size_for_what_is_there(self):
        a = self.ids[0]
        self.assertEqual(self.client.put(f'/api/recordings/{a}/artwork', content=b'<svg/>').status_code, 400)
        self.assertEqual(self.client.put(f'/api/recordings/{a}/artwork', content=JPEG + b'x' * server.PICTURE_LIMIT).status_code, 413)
        self.assertEqual(self.client.put('/api/recordings/999/artwork', content=JPEG).status_code, 404)
        self.assertEqual(self.client.put('/api/playlists/999/artwork', content=JPEG).status_code, 404)
        self.assertEqual(self.client.put('/api/pictures', params={'name': ' '}, content=JPEG).status_code, 400)
        self.lib.execute("UPDATE recordings SET fingerprint = '' WHERE id = ?", (a,))  # not yet known (in iCloud)
        self.assertEqual(self.client.put(f'/api/recordings/{a}/artwork', content=JPEG).status_code, 409)

    def test_the_photo_a_picture_was_made_from_from_the_phone(self):
        import hashlib
        digest = hashlib.sha256(JPEG).hexdigest()
        self.assertEqual(self.client.get(f'/api/picture-originals/{digest}').status_code, 404)
        put = lambda d, **square: self.client.put(f'/api/picture-originals/{d}', params=square, content=JPEG + b'photo')
        self.assertEqual(put(digest, x=1.5, y=2, side=300).json(), {'ok': True})
        got = self.client.get(f'/api/picture-originals/{digest}')
        self.assertEqual((got.content, got.headers['x-crop']), (JPEG + b'photo', '1.5,2.0,300.0'))
        self.assertEqual(put('nothex', x=0, y=0, side=1).status_code, 400)
        self.assertEqual(put(digest, x=0, y=0, side=-1).status_code, 400)
        self.assertEqual(self.client.put(f'/api/picture-originals/{digest}', params={'x': 0, 'y': 0, 'side': 1},
                                         content=b'<svg/>').status_code, 400)

    def test_a_playlists_and_an_artists_from_the_phone(self):
        pid = self.client.post('/api/playlists', json={'name': '睡前'}).json()['id']
        self.client.put(f'/api/playlists/{pid}/artwork', content=JPEG)
        self.assertTrue(self.events[-1]['playlists'][0]['artwork'])
        self.assertEqual(self.client.get(f'/api/playlists/{pid}/artwork').content, JPEG)
        self.client.put('/api/pictures', params={'name': '周杰伦'}, content=JPEG)
        self.assertEqual((self.events[-1]['type'], list(self.events[-1]['pictures'])), ('library', ['周杰伦']))
        self.assertEqual(self.client.get('/api/state').json()['pictures'], self.events[-1]['pictures'])
        picture = self.client.get('/api/pictures', params={'name': '周杰伦', 'v': 1})
        self.assertEqual((picture.content, picture.headers['cache-control']), (JPEG, 'private, max-age=31536000, immutable'))
        self.assertEqual(self.client.patch('/api/artists', json={'name': '周杰伦', 'description': '华语流行'}).json(), {'ok': True})
        self.assertEqual(self.events[-1]['descriptions'], {'周杰伦': '华语流行'})
        self.assertEqual(self.client.get('/api/state').json()['descriptions'], {'周杰伦': '华语流行'})
        for bad in ({'name': ' ', 'description': 'x'}, {'name': '周杰伦', 'description': 'x' * 1001}, {'name': '周杰伦'}):
            self.assertIn(self.client.patch('/api/artists', json=bad).status_code, (400, 422), bad)
        self.client.patch('/api/artists', json={'name': '周杰伦', 'description': ''})
        self.client.delete('/api/pictures', params={'name': '周杰伦'})
        self.assertEqual(self.events[-1], {'type': 'library', 'pictures': {}, 'descriptions': {}})
