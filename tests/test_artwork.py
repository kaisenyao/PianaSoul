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
        self.assertEqual(self.events[-1], {'type': 'library'})
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

    def test_a_playlists_and_an_artists_from_the_phone(self):
        pid = self.client.post('/api/playlists', json={'name': '睡前'}).json()['id']
        self.client.put(f'/api/playlists/{pid}/artwork', content=JPEG)
        self.assertTrue(self.events[-1]['playlists'][0]['artwork'])
        self.assertEqual(self.client.get(f'/api/playlists/{pid}/artwork').content, JPEG)
        self.client.put('/api/pictures', params={'name': '周杰伦'}, content=JPEG)
        self.assertEqual(list(self.events[-1]['pictures']), ['周杰伦'])
        self.assertEqual(self.client.get('/api/state').json()['pictures'], self.events[-1]['pictures'])
        self.assertEqual(self.client.get('/api/pictures', params={'name': '周杰伦'}).content, JPEG)
        self.client.delete('/api/pictures', params={'name': '周杰伦'})
        self.assertEqual(self.events[-1], {'type': 'pictures', 'pictures': {}})
