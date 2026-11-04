"""The library folder and the library in step: files put in, moved, renamed, changed, gone and back; recordings to
transcribe, each once on its own. No network, no transcription model."""
import contextlib
import io
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import mido

import places  # noqa: F401  (first: the library, data and cache folders are throwaway ones)
from pianasoul import folder as folder_module
from pianasoul.core import Core
from pianasoul.downloads import Downloads
from pianasoul.folder import Folder
from pianasoul.library import Library
from test_app import piece_made, transcription
from test_library import make_midi


class FolderTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.music = self.root / 'Music'
        self.music.mkdir()
        self.lib = Library(self.root / 'library.db', self.music)
        self.addCleanup(self.lib.close)
        self.folder = Folder(self.lib)

    def settle(self):
        """Two looks: a new file is taken in once it has kept its size between them."""
        self.folder.scan()
        self.assertFalse(self.folder.scan(), 'Nothing still settling')

    def titles(self):
        return sorted(r['title'] for r in self.lib.all() if r['file_exists'])

    def test_every_music_file_in_the_folder_and_its_subfolders(self):
        make_midi(self.music / 'Chopin - Nocturne.mid')
        make_midi(self.music / 'Bach' / 'Prelude.midi', note=61)
        make_midi(self.music / 'Transcribed' / 'Clair de Lune.mid', note=62)
        (self.music / 'Clair de Lune.mp3').write_bytes(b'mp3')
        for passed in ('.hidden.mid', 'half.mid.part', 'song.mp3.crdownload', 'notes.txt', '.cache/x.mid'):
            (self.music / passed).parent.mkdir(exist_ok=True)
            (self.music / passed).write_bytes(b'x')
        self.assertTrue(self.folder.scan(), 'New files settle first')
        self.assertEqual(self.lib.all(), [])
        self.assertFalse(self.folder.scan())
        self.assertEqual(self.titles(), ['Chopin - Nocturne', 'Clair de Lune', 'Prelude'])
        origins = {r['title']: r['origin'] for r in self.lib.all()}
        self.assertEqual(origins, {'Chopin - Nocturne': 'yours', 'Prelude': 'yours', 'Clair de Lune': 'transcribed'})
        self.assertEqual([Path(a['path']).name for a in self.lib.audio()], ['Clair de Lune.mp3'])
        self.assertEqual(self.lib.audio()[0]['status'], 'waiting')

    def test_a_file_still_being_copied_is_not_read_halfway(self):
        growing = self.music / 'Big.mid'
        make_midi(growing)
        self.folder.scan()
        with growing.open('ab') as f:
            f.write(b'\0' * 100)  # more arrived since the last look
        self.assertTrue(self.folder.scan(), 'Still settling')
        self.assertEqual(self.lib.all(), [])

    def test_a_moved_or_renamed_file_keeps_its_piece(self):
        make_midi(self.music / 'Song.mid')
        self.settle()
        rec = self.lib.all()[0]
        self.lib.edit(rec['id'], '晴天', '周杰伦')
        self.lib.favorite(rec['id'], True)
        (self.music / 'Jay').mkdir()
        (self.music / 'Song.mid').rename(self.music / 'Jay' / 'Sunny.mid')
        self.settle()
        moved = self.lib.all()
        self.assertEqual(len(moved), 1)
        self.assertEqual((moved[0]['id'], moved[0]['title'], Path(moved[0]['midi_path']).name),
                         (rec['id'], '周杰伦 - 晴天', 'Sunny.mid'))
        self.assertTrue(moved[0]['favorited_at'] and moved[0]['file_exists'])

    def test_a_file_gone_is_out_of_sight_and_back_as_it_was(self):
        song = make_midi(self.music / 'Song.mid')
        self.settle()
        rec = self.lib.all()[0]
        self.lib.favorite(rec['id'], True)
        kept = self.root / 'Song.mid'
        song.rename(kept)  # out of the folder
        self.settle()
        self.assertEqual(self.titles(), [], 'Not shown')
        self.assertIsNotNone(self.lib.get(rec['id'])['favorited_at'], 'Still in the library, with everything')
        kept.rename(song)
        self.settle()
        back = self.lib.get(rec['id'])
        self.assertTrue(back['file_exists'] and back['favorited_at'])
        self.assertEqual(len(self.lib.all()), 1)

    def test_a_copy_is_a_piece_of_its_own(self):
        make_midi(self.music / 'Song.mid')
        self.settle()
        (self.music / 'Copy.mid').write_bytes((self.music / 'Song.mid').read_bytes())
        self.settle()
        self.assertEqual(self.titles(), ['Copy', 'Song'])

    def test_a_file_changed_where_it_is_is_read_again(self):
        song = make_midi(self.music / 'Song.mid')
        self.settle()
        before = self.lib.all()[0]
        longer = mido.MidiFile(song)
        longer.tracks[0].append(mido.Message('note_on', note=64, velocity=64, time=0))
        longer.tracks[0].append(mido.Message('note_off', note=64, time=480))
        longer.save(song)
        self.settle()
        after = self.lib.get(before['id'])
        self.assertEqual((after['notes'], len(self.lib.all())), (2, 1))
        self.assertNotEqual(after['fingerprint'], before['fingerprint'])

    def test_what_is_not_midi_after_all_is_passed_over_once(self):
        (self.music / 'Broken.mid').write_bytes(b'not midi')
        with patch.object(self.lib, 'add', wraps=self.lib.add) as add:
            self.settle()
            self.folder.scan()
        self.assertEqual(add.call_count, 1, 'Not read again while it is the same')
        self.assertEqual(self.lib.all(), [])

    def test_a_recording_moved_keeps_how_far_it_got(self):
        song = self.music / 'Song.mp3'
        song.write_bytes(b'mp3')
        self.settle()
        item = self.lib.audio()[0]
        self.lib.set_audio(item['id'], status='done')
        song.rename(self.music / 'Renamed.mp3')
        self.settle()
        self.assertEqual([(a['id'], Path(a['path']).name, a['status']) for a in self.lib.audio()],
                         [(item['id'], 'Renamed.mp3', 'done')])

    def test_icloud_files_not_yet_downloaded_are_passed_over(self):
        song = make_midi(self.music / 'Song.mid')
        with patch('pianasoul.folder.present', return_value=False):
            self.settle()
        self.assertEqual(self.lib.all(), [])
        self.settle()
        self.assertEqual(self.titles(), ['Song'], 'Once it is downloaded')

    def test_a_file_gone_or_back_is_told(self):
        told = []
        self.folder = Folder(self.lib, lambda: told.append(1))
        song = make_midi(self.music / 'Song.mid')
        self.settle()
        told.clear()
        kept = song.rename(self.root / 'Song.mid')
        self.folder.scan()
        self.assertEqual(len(told), 1, 'Gone: the phone is told')
        kept.rename(song)
        self.folder.scan()
        self.assertEqual(len(told), 2, 'Back: told again')
        self.folder.scan()
        self.assertEqual(len(told), 2, 'Nothing new: nothing told')

    def test_a_scan_never_deletes(self):
        for name in ('A', 'B'):
            make_midi(self.music / f'{name}.mid', note=60 + len(name))
        self.settle()
        for path in self.music.iterdir():
            path.unlink()
        self.settle()
        self.assertEqual(len(self.lib.all()), 2)
        self.assertEqual(self.titles(), [])

    def test_another_library_folder_shows_only_what_is_in_it(self):
        song = make_midi(self.music / 'Song.mid')
        self.settle()
        rec = self.lib.all()[0]
        self.lib.close()
        elsewhere = self.root / 'iCloud Music'
        self.lib = Library(self.root / 'library.db', elsewhere)
        self.folder = Folder(self.lib)
        self.settle()
        self.assertEqual(self.titles(), [], 'The old folder is not the library any more')
        elsewhere.mkdir(exist_ok=True)
        song.rename(elsewhere / 'Song.mid')  # moved over by hand
        self.settle()
        self.assertEqual([r['id'] for r in self.lib.all() if r['file_exists']], [rec['id']], 'Known by its content')

    def test_changes_are_seen_while_running(self):
        changed = threading.Event()
        self.folder = Folder(self.lib, changed.set)
        with patch.object(folder_module, 'SETTLE_S', 0.2):
            self.folder.watch()
            self.addCleanup(self.folder.close)
            time.sleep(0.3)
            make_midi(self.music / 'Dropped.mid')
            self.assertTrue(changed.wait(5), 'Taken in without a restart')
        self.assertEqual(self.titles(), ['Dropped'])


class TranscribeOnceTests(unittest.TestCase):
    """Each recording put in the library folder is transcribed once on its own; after that only when asked."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.music = self.root / 'Music'
        self.lib = Library(self.root / 'library.db', self.music)
        self.addCleanup(self.lib.close)
        quiet = contextlib.redirect_stdout(io.StringIO())
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)

    def core(self, transcribe: bool) -> Core:
        with patch('pianasoul.core.installed', return_value=transcribe):
            core = Core(lib=self.lib, assistant=False)
        self.addCleanup(core.close)
        return core

    def put(self, name: str) -> dict:
        (self.music / name).parent.mkdir(parents=True, exist_ok=True)
        (self.music / name).write_bytes(name.encode())
        folder = Folder(self.lib)
        folder.scan(), folder.scan()
        return self.lib.audio_at(self.music / name)

    def convert(self, audio, out, options, output_name, stopped):
        return {'files': {'midi': str(make_midi(Path(out) / f'{output_name}.mid'))}}

    def test_without_transcription_it_waits_and_shows_as_needing_it(self):
        item = self.put('Clair de Lune.mp3')
        core = self.core(transcribe=False)
        self.assertEqual(core.audio(), [{'id': item['id'], 'name': 'Clair de Lune', 'status': 'waiting', 'error': '',
                                         'added_at': item['added_at']}])
        self.assertEqual(core.downloads.snapshot(), [])

    def test_transcription_turned_off_it_waits_as_without_the_extra(self):
        """Online Search & Transcription unticked in the Mac app: a recording waits, shown as needing it, as when the
        extra is missing; what was transcribed before stays and plays (it is in the library)."""
        item = self.put('Clair de Lune.mp3')
        with patch.dict(os.environ, {'PIANASOUL_TRANSCRIBE': '0'}):
            core = self.core(transcribe=True)
        self.assertEqual([a['status'] for a in core.audio()], ['waiting'])
        self.assertEqual(core.downloads.snapshot(), [])
        self.assertEqual(self.lib.audio_item(item['id'])['status'], 'waiting')

    def test_once_this_mac_can_transcribe_each_is_made_once_and_then_only_when_asked(self):
        item = self.put('Clair de Lune.mp3')
        with transcription(self.convert), \
             patch('pianasoul.core.installed', return_value=True):
            core = self.core(transcribe=True)
            deadline = time.monotonic() + 5
            while self.lib.audio_item(item['id'])['status'] != 'done' and time.monotonic() < deadline:
                time.sleep(0.05)
            made = self.lib.audio_item(item['id'])
            self.assertEqual(made['status'], 'done')
            piece = self.lib.get(made['recording_id'])
            self.assertEqual((piece['origin'], piece['source_type'], piece['source_url']),
                             ('transcribed', 'local', str((self.music / 'Clair de Lune.mp3').resolve())))
            self.assertEqual(core.audio(), [], 'Its piece stands for it')
            core.lib.delete(piece['id'])
            core._folder_changed()
            self.assertEqual([a['status'] for a in core.audio()], ['done'], 'Back, to transcribe again when asked')
            self.assertEqual(core.downloads.snapshot(), [], 'Not made again on its own')
            self.assertTrue(core.transcribe(item['id']))
            deadline = time.monotonic() + 5
            while core.audio() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertEqual(core.audio(), [], 'Made again, as asked')

    def test_what_was_asked_for_goes_before_what_is_transcribed_on_its_own(self):
        order, gate = [], threading.Event()

        def convert(audio, out, options, output_name, stopped):
            gate.wait(5)
            order.append(Path(audio).name)
            return self.convert(audio, out, options, output_name, stopped)
        for name in ('a.mp3', 'b.mp3', 'c.mp3'):
            self.put(name)
        with transcription(convert):
            d = Downloads(self.lib)
            for name in ('a.mp3', 'b.mp3'):
                d.add(str(self.music / name), auto=True)
            d.add(str(self.music / 'c.mp3'))  # asked for
            gate.set()
            deadline = time.monotonic() + 5
            while len(order) < 3 and time.monotonic() < deadline:
                time.sleep(0.05)
        self.assertEqual(order, ['a.mp3', 'c.mp3', 'b.mp3'], 'The first had started; then the one asked for')
        self.assertEqual({a['status'] for a in self.lib.audio()}, {'done'})

    def test_a_failed_transcription_says_why(self):
        item = self.put('Noise.mp3')
        with transcription(RuntimeError('could not decode audio')):
            d = Downloads(self.lib)
            piece_made(d, str(self.music / 'Noise.mp3'))
        failed = self.lib.audio_item(item['id'])
        self.assertEqual((failed['status'], failed['error']), ('failed', 'RuntimeError: could not decode audio'))


if __name__ == '__main__':
    unittest.main()
