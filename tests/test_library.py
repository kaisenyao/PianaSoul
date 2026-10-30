"""The library: MIDI files and playlist next to the index, titles in parts, and what the app changes."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import mido

from pianasoul.library import Library, file_result, make_title, title_parts


def make_midi(path: Path) -> Path:
    midi = mido.MidiFile()
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.append(mido.Message('note_on', note=60, velocity=64, time=0))
    track.append(mido.Message('note_off', note=60, time=480))
    path.parent.mkdir(parents=True, exist_ok=True)
    midi.save(path)
    return path


class LibraryTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.lib = Library(self.root / 'library.db')
        self.addCleanup(self.lib.close)
        self.playlist = self.root / 'MIDI Files' / 'playlist.m3u'

    def test_import_copies_next_to_the_index_and_writes_the_playlist_once(self):
        for name in ('Nocturne', 'Waltz', 'Etude'):
            make_midi(self.root / 'elsewhere' / f'{name}.mid')
        with patch.object(self.lib, 'write_playlist', wraps=self.lib.write_playlist) as write:
            added = self.lib.import_folder(self.root / 'elsewhere')
        self.assertEqual(len(added), 3)
        write.assert_called_once()
        self.assertTrue((self.root / 'MIDI Files' / 'Waltz.mid').is_file())
        self.assertEqual(self.playlist.read_text().count('#EXTINF'), 3)

    def test_startup_picks_up_files_put_in_by_hand_and_drops_deleted_ones(self):
        kept = make_midi(self.root / 'MIDI Files' / 'Kept.mid')
        gone = make_midi(self.root / 'MIDI Files' / 'Gone.mid')
        with patch.object(self.lib, 'write_playlist', wraps=self.lib.write_playlist) as write:
            self.assertEqual(self.lib.index_midi_folder(), 2)
        write.assert_called_once()
        gone.unlink()
        self.assertEqual(self.lib.index_midi_folder(), 0)
        self.assertIn(kept.name, self.playlist.read_text())
        self.assertNotIn(gone.name, self.playlist.read_text())

    def test_a_title_has_its_parts_and_is_made_from_them(self):
        for title, parts in [('周杰伦 - 搁浅 (MappleZS)', ('周杰伦', '搁浅', 'MappleZS')),
                             ('贝加尔湖畔（赵海洋）', ('李健', '贝加尔湖畔', '赵海洋')),
                             ('Chopin - Waltz in A Minor, B. 150 (Paul Barton)',
                              ('Chopin', 'Waltz in A Minor, B. 150', 'Paul Barton')),
                             ('Greensleeves', ('李健', 'Greensleeves', ''))]:
            found = title_parts(title, composer='李健')
            self.assertEqual((found['artist'], found['piece'], found['performer']), parts)
        self.assertEqual(make_title('搁浅', '周杰伦', 'MappleZS'), '周杰伦 - 搁浅 (MappleZS)')
        self.assertEqual(make_title('Greensleeves'), 'Greensleeves')

    def test_the_app_names_favorites_and_deletes_a_piece(self):
        midi = make_midi(self.root / 'MIDI Files' / 'Song.mid')
        rec = self.lib.add(midi, 'Song')
        rec = self.lib.edit(rec['id'], '晴天', '周杰伦', 'MappleZS')
        self.assertEqual((rec['title'], rec['composer'], rec['performer']), ('周杰伦 - 晴天 (MappleZS)', '周杰伦', 'MappleZS'))
        self.lib.favorite(rec['id'], True)
        self.lib.played(rec['id'])
        rec = self.lib.get(rec['id'])
        self.assertTrue(rec['favorited_at'] and rec['last_played_at'])
        self.lib.played(rec['id'], False)
        self.assertIsNone(self.lib.get(rec['id'])['last_played_at'])
        self.assertTrue(self.lib.delete(rec['id']))
        self.assertIsNone(self.lib.get(rec['id']))
        self.assertFalse(Path(rec['midi_path']).exists(), 'Its file goes too')

    def test_a_piece_renamed_takes_its_file_along(self):
        folder = self.root / 'MIDI Files'
        rec = self.lib.add(make_midi(folder / 'Song.mid'), 'Song')
        rec = self.lib.edit(rec['id'], '小幸运', '田馥甄', "Melody's Piano Garden")
        name = "田馥甄 - 小幸运 (Melody's Piano Garden)"
        self.assertEqual(Path(rec['midi_path']), (folder / f'{name}.mid').resolve())
        self.assertTrue(rec['file_exists'])
        self.assertFalse((folder / 'Song.mid').exists())
        self.assertIn(f'{name}.mid', self.playlist.read_text())
        self.lib.update(rec['id'], tags='华语')
        self.assertEqual(self.lib.get(rec['id'])['midi_path'], rec['midi_path'], 'Other changes leave it')
        twin = self.lib.add(make_midi(folder / 'Twin.mid'), 'Twin')
        twin = self.lib.edit(twin['id'], '小幸运', '田馥甄', "Melody's Piano Garden")
        self.assertEqual(Path(twin['midi_path']).name, f'{name} (2).mid', 'Another piece with the same title')
        outside = make_midi(self.root / 'elsewhere' / 'Mine.mid')
        mine = self.lib.import_folder(self.root / 'elsewhere', copy=False)[0]
        self.lib.edit(mine['id'], '晴天', '周杰伦')
        self.assertTrue(outside.is_file(), 'A file imported in place keeps its name')

    def test_a_name_still_held_by_a_piece_whose_file_is_gone_is_not_taken(self):
        folder = self.root / 'MIDI Files'
        a = self.lib.add(make_midi(folder / 'A.mid'), 'A')
        b = self.lib.add(make_midi(folder / 'B.mid'), 'B')
        Path(a['midi_path']).unlink()  # deleted by hand
        b = self.lib.edit(b['id'], 'A')
        self.assertEqual(Path(b['midi_path']).name, 'A (2).mid')
        self.assertTrue(b['file_exists'])
        self.assertEqual(self.lib.get(a['id'])['midi_path'], a['midi_path'], 'The other piece keeps its own')

    def test_versions_of_one_piece_each_keep_their_own_file_and_record(self):
        made = self.root / 'made'
        title = 'Chopin - Nocturne Op. 9 No. 2 (Lang Lang)'
        first = file_result(self.lib, make_midi(made / '1.mid'), title, source_type='url', source_url='https://y/1')
        self.lib.favorite(first['id'], True)
        again = file_result(self.lib, make_midi(made / '2.mid'), title, source_type='url', source_url='https://y/1')
        self.assertEqual((again['id'], again['midi_path']), (first['id'], first['midi_path']), 'The same video')
        Path(first['midi_path']).unlink()  # deleted by hand
        other = file_result(self.lib, make_midi(made / '3.mid'), title, source_type='url', source_url='https://y/2')
        self.assertNotEqual(other['id'], first['id'], 'Another video does not take over the record left behind')
        self.assertEqual(Path(other['midi_path']).name, f'{title} (2).mid')
        back = file_result(self.lib, make_midi(made / '4.mid'), title, source_type='url', source_url='https://y/1')
        self.assertEqual((back['id'], back['midi_path']), (first['id'], first['midi_path']), 'Put back in its place')
        self.assertTrue(back['file_exists'])
        self.assertIsNotNone(self.lib.get(first['id'])['favorited_at'])

    def test_a_library_from_before_files_followed_their_titles_catches_up_when_opened(self):
        rec = self.lib.add(make_midi(self.root / 'MIDI Files' / 'Song.mid'), 'Song')
        self.lib.execute('UPDATE recordings SET title = ? WHERE id = ?', ('周杰伦 - 晴天 (MappleZS)', rec['id']))
        self.lib.close()
        self.lib = Library(self.root / 'library.db')
        self.addCleanup(self.lib.close)
        rec = self.lib.get(rec['id'])
        self.assertEqual(Path(rec['midi_path']).name, '周杰伦 - 晴天 (MappleZS).mid')
        self.assertTrue(rec['file_exists'])
        self.assertIn('周杰伦 - 晴天 (MappleZS).mid', self.playlist.read_text())

    def test_search_puts_the_best_match_first_then_a_favorite_then_the_one_played_last(self):
        ids = [self.lib.add(make_midi(self.root / 'MIDI Files' / f'{n}.mid'), f'Chopin - Waltz in A Minor ({n})')['id']
               for n in ('Barton', 'Kolesnikov', 'Rubinstein')]
        self.lib.played(ids[0])
        self.lib.favorite(ids[1], True)
        self.assertEqual([r['id'] for r in self.lib.search('waltz in a minor')], [ids[1], ids[0], ids[2]])
        self.assertEqual(self.lib.search('Kolesnikov waltz')[0]['id'], ids[1])

    def test_a_file_imported_in_place_stays_when_its_piece_is_deleted(self):
        outside = make_midi(self.root / 'elsewhere' / 'Mine.mid')
        rec = self.lib.import_folder(self.root / 'elsewhere', copy=False)[0]
        self.lib.delete(rec['id'])
        self.assertTrue(outside.exists())

    def test_an_older_library_gets_the_new_columns_and_keeps_its_records(self):
        import sqlite3
        path = self.root / 'old.db'
        old = sqlite3.connect(path)
        old.executescript("""CREATE TABLE recordings (id INTEGER PRIMARY KEY, title TEXT NOT NULL,
            composer TEXT NOT NULL DEFAULT '', performer TEXT NOT NULL DEFAULT '', aliases TEXT NOT NULL DEFAULT '',
            tags TEXT NOT NULL DEFAULT '', source_type TEXT NOT NULL DEFAULT 'local', source_url TEXT NOT NULL DEFAULT '',
            midi_path TEXT NOT NULL UNIQUE, notes INTEGER, velocity_min INTEGER, velocity_max INTEGER,
            pedal_presses INTEGER, duration_s REAL, added_at TEXT NOT NULL);
            INSERT INTO recordings (title, midi_path, added_at) VALUES ('Old', '/nowhere.mid', '2026-10-25T06:16:26+00:00');""")
        old.commit(); old.close()
        lib = Library(path)
        self.addCleanup(lib.close)
        rec = lib.get(1)
        self.assertEqual(rec['title'], 'Old')
        self.assertIsNone(rec['favorited_at'])
        self.assertEqual(rec['channel_avatar'], '')

    def test_settings_are_kept(self):
        self.assertEqual(self.lib.setting('language', 'system'), 'system')
        self.lib.set_setting('language', 'en')
        self.lib.set_setting('language', 'zh')
        self.assertEqual(self.lib.setting('language'), 'zh')


if __name__ == '__main__':
    unittest.main()
