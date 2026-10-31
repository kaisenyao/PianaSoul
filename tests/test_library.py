"""The library: its folder (yours, and Transcribed/ for the pieces PianaSoul makes), titles in parts, and what the app
changes."""
from pathlib import Path
import tempfile
import unittest

import mido

import places  # noqa: F401  (first: the library, data and cache folders are throwaway ones)
from pianasoul.library import Library, enrich, file_result, make_title, title_parts


def make_midi(path: Path, note: int = 60) -> Path:
    midi = mido.MidiFile()
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.append(mido.Message('note_on', note=note, velocity=64, time=0))
    track.append(mido.Message('note_off', note=note, time=480))
    path.parent.mkdir(parents=True, exist_ok=True)
    midi.save(path)
    return path


class LibraryTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.music = self.root / 'Music'
        self.transcribed = self.music / 'Transcribed'
        self.lib = Library(self.root / 'data' / 'library.db', self.music)
        self.addCleanup(self.lib.close)

    def test_import_copies_into_the_library_folder_as_yours(self):
        for name in ('Nocturne', 'Waltz', 'Etude'):
            make_midi(self.root / 'elsewhere' / f'{name}.mid')
        make_midi(self.music / 'Waltz.mid', note=62)  # another Waltz, already there
        added = self.lib.import_folder(self.root / 'elsewhere')
        self.assertEqual(len(added), 3)
        self.assertEqual(sorted(p.name for p in self.music.iterdir()),
                         ['Etude.mid', 'Nocturne.mid', 'Waltz (2).mid', 'Waltz.mid'])
        self.assertEqual(len(self.lib.import_folder(self.root / 'elsewhere')), 3)
        self.assertEqual(len(list(self.music.iterdir())), 4, 'The same files again are not copied twice')
        self.assertTrue((self.root / 'elsewhere' / 'Waltz.mid').is_file(), 'The originals stay')

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
        midi = make_midi(self.transcribed / 'Song.mid')
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

    def test_a_piece_renamed_takes_its_file_along_in_transcribed_only(self):
        folder = self.transcribed
        rec = self.lib.add(make_midi(folder / 'Song.mid'), 'Song')
        rec = self.lib.edit(rec['id'], '小幸运', '田馥甄', "Melody's Piano Garden")
        name = "田馥甄 - 小幸运 (Melody's Piano Garden)"
        self.assertEqual(Path(rec['midi_path']), (folder / f'{name}.mid').resolve())
        self.assertTrue(rec['file_exists'])
        self.assertFalse((folder / 'Song.mid').exists())
        self.lib.update(rec['id'], tags='华语')
        self.assertEqual(self.lib.get(rec['id'])['midi_path'], rec['midi_path'], 'Other changes leave it')
        twin = self.lib.add(make_midi(folder / 'Twin.mid'), 'Twin')
        twin = self.lib.edit(twin['id'], '小幸运', '田馥甄', "Melody's Piano Garden")
        self.assertEqual(Path(twin['midi_path']).name, f'{name} (2).mid', 'Another piece with the same title')
        yours = self.lib.add(make_midi(self.music / 'Mine.mid'), 'Mine')
        yours = self.lib.edit(yours['id'], '晴天', '周杰伦')
        self.assertEqual(Path(yours['midi_path']).name, 'Mine.mid', 'Your file keeps its name')
        self.assertEqual(yours['title'], '周杰伦 - 晴天')

    def test_a_name_still_held_by_a_piece_whose_file_is_gone_is_not_taken(self):
        folder = self.transcribed
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
        rec = self.lib.add(make_midi(self.transcribed / 'Song.mid'), 'Song')
        self.lib.execute('UPDATE recordings SET title = ? WHERE id = ?', ('周杰伦 - 晴天 (MappleZS)', rec['id']))
        self.lib.close()
        self.lib = Library(self.root / 'data' / 'library.db', self.music)
        self.addCleanup(self.lib.close)
        rec = self.lib.get(rec['id'])
        self.assertEqual(Path(rec['midi_path']).name, '周杰伦 - 晴天 (MappleZS).mid')
        self.assertTrue(rec['file_exists'])

    def test_search_puts_the_best_match_first_then_a_favorite_then_the_one_played_last(self):
        ids = [self.lib.add(make_midi(self.music / f'{n}.mid'), f'Chopin - Waltz in A Minor ({n})')['id']
               for n in ('Barton', 'Kolesnikov', 'Rubinstein')]
        self.lib.played(ids[0])
        self.lib.favorite(ids[1], True)
        self.assertEqual([r['id'] for r in self.lib.search('waltz in a minor')], [ids[1], ids[0], ids[2]])
        self.assertEqual(self.lib.search('Kolesnikov waltz')[0]['id'], ids[1])

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

    def test_pieces_are_named_in_other_languages_once_each(self):
        nocturne = self.lib.add(make_midi(self.music / 'Chopin - Nocturne Op. 9 No. 2.mid'), 'Chopin - Nocturne Op. 9 No. 2')
        unknown = self.lib.add(make_midi(self.music / 'track 7.mid', note=62), 'track 7')
        asked = []
        def model(prompt):
            asked.append(prompt)
            return {str(nocturne['id']): {'artist': 'Chopin', 'aliases': '降E大调夜曲; 夜曲', 'tags': 'classical'},
                    str(unknown['id']): {'artist': '', 'aliases': '', 'tags': ''}}
        self.assertEqual(enrich(self.lib, model), 2)
        self.assertIn('Chinese and in English', asked[0])
        self.assertEqual(self.lib.search('夜曲')[0]['id'], nocturne['id'], 'Found by its name in the other language')
        self.assertEqual(enrich(self.lib, model), 0)
        self.assertEqual(len(asked), 1, 'Not asked again, about the one the model did not know either')

    def test_settings_are_kept(self):
        self.assertEqual(self.lib.setting('language', 'system'), 'system')
        self.lib.set_setting('language', 'en')
        self.lib.set_setting('language', 'zh')
        self.assertEqual(self.lib.setting('language'), 'zh')


if __name__ == '__main__':
    unittest.main()
