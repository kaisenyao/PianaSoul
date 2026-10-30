"""Moving a library from a checkout's output/ folder: put in order, files where they belong, data kept, and never
half done."""
import contextlib
import io
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import places  # noqa: F401  (first: the library, data and cache folders are throwaway ones)
from pianasoul import library, migrate as migrate_module
from pianasoul.conversations import Conversations
from pianasoul.folder import Folder
from pianasoul.library import Library
from pianasoul.migrate import NOTE, MigrationError, migrate
from test_library import make_midi


class MigrateTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.old = self.root / 'output'
        self.music, self.data, self.cache = self.root / 'Music', self.root / 'Data', self.root / 'Cache'
        self.make_old_library()

    def make_old_library(self):
        """As the MacBook's is: pieces made from videos whose files moved away with the old project folder, the same
        files listed again from MIDI Files/, a piece imported by hand, one whose file is nowhere, and the cache."""
        files = self.old / 'MIDI Files'
        lib = Library(self.old / 'library.db', self.root / 'unused')
        sunny = make_midi(files / '周杰伦 - 晴天 (MappleZS).mid', note=60)
        moon = make_midi(files / 'Clair de Lune.mid', note=62)
        mine = make_midi(files / 'Mine.mid', note=64)
        make_midi(files / 'Stray.mid', note=65)  # in the folder, in no record
        (files / 'playlist.m3u').write_text('#EXTM3U\n')
        elsewhere = self.root / 'piano2midi' / 'output' / 'MIDI Files'
        made = {}
        for src in (sunny, moon):  # recorded where the project used to be
            moved = elsewhere / src.name
            moved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, moved)
            made[src.name] = lib.add(moved, src.stem, source_type='url', source_url=f'https://youtu.be/{src.stem}')
            moved.unlink()
        self.sunny = made[sunny.name]
        lib.edit(self.sunny['id'], '晴天', '周杰伦', 'MappleZS')
        lib.favorite(self.sunny['id'], True)
        lib.set_video(self.sunny['id'], {'video_title': '晴天 钢琴版', 'channel': 'MappleZS', 'views': 9})
        twins = [lib.add(f, f.stem, source_type='folder') for f in (sunny, moon)]
        lib.played(twins[0]['id'])  # played under the duplicate record
        lib.add(mine, 'Mine', source_type='import')
        gone = make_midi(self.root / 'gone.mid', note=70)
        self.gone = lib.add(gone, 'Gone Piece', source_type='url')
        gone.unlink()
        conversations = Conversations(lib)
        cid = conversations.create()
        conversations.add(cid, 'user', '弹晴天')
        conversations.add(cid, 'piece', '周杰伦 - 晴天 (MappleZS)', self.sunny['id'])
        lib.execute('INSERT INTO requests (asked_at, text, recording_id) VALUES (?, ?, ?)',
                    ('2026-10-20T10:00:00+00:00', '晴天', twins[0]['id']))
        lib.close()
        cache = self.old / '.cache'
        for item in ('phone-tls/ca.pem', 'voice/hello.wav', 'models/vad.onnx', 'downloads/a.m4a', 'extras/a.json'):
            (cache / item).parent.mkdir(parents=True, exist_ok=True)
            (cache / item).write_text(item)
        (cache / 'phone-token').write_text('token')

    def run_migration(self):
        return migrate(self.old, self.music, self.data, self.cache)

    def snapshot(self, folder):
        return {str(p.relative_to(folder)): p.read_bytes() for p in sorted(folder.rglob('*')) if p.is_file()}

    def test_the_library_is_put_in_order_and_everything_is_kept(self):
        before = self.snapshot(self.old)
        report = self.run_migration()
        self.assertEqual((report.pieces, report.merged, report.missing), (3, 2, ['Gone Piece']))
        lib = Library(self.data / 'library.db', self.music)
        self.addCleanup(lib.close)
        shown = {r['title']: r for r in lib.all() if r['file_exists']}
        self.assertEqual(set(shown), {'周杰伦 - 晴天 (MappleZS)', 'Clair de Lune', 'Mine'})
        sunny = shown['周杰伦 - 晴天 (MappleZS)']
        self.assertEqual(sunny['id'], self.sunny['id'], 'The record with the most about it stays')
        self.assertTrue(sunny['favorited_at'] and sunny['last_played_at'], 'Its twin\'s play comes along')
        self.assertEqual((sunny['video_title'], sunny['source_url'], sunny['origin']),
                         ('晴天 钢琴版', 'https://youtu.be/周杰伦 - 晴天 (MappleZS)', 'transcribed'))
        self.assertEqual(shown['Mine']['origin'], 'yours')
        self.assertEqual(sorted(p.relative_to(self.music).as_posix() for p in self.music.rglob('*.mid')),
                         ['Mine.mid', 'Stray.mid', 'Transcribed/Clair de Lune.mid', 'Transcribed/周杰伦 - 晴天 (MappleZS).mid'])
        self.assertEqual(lib.execute('SELECT recording_id FROM messages WHERE role = "piece"')[0][0], sunny['id'])
        self.assertEqual(lib.execute('SELECT recording_id FROM requests')[0][0], sunny['id'], 'Moved to the one kept')
        gone = lib.get(self.gone['id'])
        self.assertFalse(gone['file_exists'], 'Kept, out of sight')
        self.assertEqual(sorted(p.name for p in self.data.iterdir()),
                         ['library.db', 'models', 'phone-tls', 'phone-token', 'voice'])
        self.assertEqual(sorted(p.name for p in self.cache.iterdir()), ['downloads', 'extras'])
        after = self.snapshot(self.old)
        self.assertIn('Nothing here was changed', after.pop(NOTE).decode())
        self.assertEqual(after, before, 'The old folder is a full backup, as it was')

    def test_a_scan_afterwards_changes_nothing(self):
        self.run_migration()
        lib = Library(self.data / 'library.db', self.music)
        self.addCleanup(lib.close)
        before = lib.all()
        folder = Folder(lib)
        folder.scan(), folder.scan()
        after = {r['id']: (r['midi_path'], r['stamp'], r['file_exists']) for r in lib.all()}
        self.assertEqual({i: after[i] for i in (r['id'] for r in before)},
                         {r['id']: (r['midi_path'], r['stamp'], r['file_exists']) for r in before})
        self.assertEqual(len(after), len(before) + 1, 'Only the stray file, which no record had, is new')

    def test_names_already_taken_in_the_library_folder_get_a_number(self):
        make_midi(self.music / 'Mine.mid', note=40)
        self.run_migration()
        self.assertTrue((self.music / 'Mine (2).mid').is_file())

    def test_a_library_already_there_is_never_overwritten(self):
        self.data.mkdir()
        (self.data / 'library.db').write_text('mine')
        with self.assertRaisesRegex(MigrationError, 'already exists'):
            self.run_migration()
        self.assertEqual((self.data / 'library.db').read_text(), 'mine')
        self.assertFalse(self.music.exists())

    def test_a_failure_halfway_leaves_nothing_and_it_can_be_done_again(self):
        before = self.snapshot(self.old)
        real, moved = shutil.move, []

        def move(src, dst):
            if len(moved) == 3:
                raise OSError(28, 'No space left on device')
            moved.append(dst)
            return real(src, dst)
        with patch.object(migrate_module.shutil, 'move', side_effect=move):
            with self.assertRaisesRegex(MigrationError, 'Nothing was left'):
                self.run_migration()
        self.assertEqual(len(moved), 3, 'Some files were in place when it failed')
        for place in (self.music, self.data, self.cache):
            self.assertFalse(place.exists(), place)
        self.assertEqual(self.snapshot(self.old), before)
        self.assertEqual(self.run_migration().pieces, 3)

    def test_done_once_on_its_own_only_from_the_real_places(self):
        db = self.data / 'library.db'
        quiet = contextlib.redirect_stdout(io.StringIO())
        with patch.object(library, 'OLD_OUTPUT', self.old), patch.object(library, 'DB_PATH', db), quiet:
            Library(db, self.music).close()  # PIANASOUL_DATA is set, as in every test and development run
            self.assertFalse(db.exists() and len(sqlite3.connect(db).execute('SELECT * FROM recordings').fetchall()))
            db.unlink(missing_ok=True)
            env = {k: v for k, v in os.environ.items() if k != 'PIANASOUL_DATA'}
            with patch.dict(os.environ, env, clear=True):
                Library(db, self.music).close()
        self.assertEqual(len(sqlite3.connect(db).execute('SELECT * FROM recordings').fetchall()), 4, 'Gone Piece too')
        self.assertTrue((self.music / 'Transcribed' / 'Clair de Lune.mid').is_file())


if __name__ == '__main__':
    unittest.main()
