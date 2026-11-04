"""A piece played while it is made: its parts play as its MIDI file will, message for message; its music waits where
its notes are not there yet; the player goes on, pauses, seeks and stops with it as with any other; and it has its
record from when it plays, in the library once it is made, gone (from the player too) if it is not made after all,
and then its next version, if it has one, plays in its place."""
import math
import random
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import contextlib
import io

import mido
import pretty_midi

import places  # noqa: F401  (first: the library, data and cache folders are throwaway ones)
from pianasoul.downloads import Downloads
from pianasoul.growing import PLAY_AHEAD_S, Growing, NotMade
from pianasoul.library import Library
from pianasoul.playback import Playback


def midi_of(notes, controls=()):
    midi = pretty_midi.PrettyMIDI(resolution=960, initial_tempo=120.0)  # as the clean-up makes every piece
    piano = pretty_midi.Instrument(program=0, name='Piano')
    piano.notes = [pretty_midi.Note(velocity, pitch, start, end) for start, end, pitch, velocity in notes]
    piano.control_changes = [pretty_midi.ControlChange(number, value, at) for at, number, value in controls]
    midi.instruments.append(piano)
    return midi


def in_parts(notes, controls, untils):
    """The notes in parts as the transcription hands them on: each with those that start before its until, a note's
    pedal with it (its down and up), the last with the rest."""
    parts, before = [], -1.0
    for until in [*untils, math.inf]:
        parts.append((midi_of([n for n in notes if before <= n[0] < until],
                              [c[:3] for c in controls if before <= c[3] < until]), until))
        before = until
    return parts


def made_up(seed, count=300, seconds=40.0):
    """Notes as transcribed: a key's notes one after another (one may end where the next begins), chords struck
    together, and the sustain pedal's presses, each [time, number, value, the time it was pressed]."""
    rng, notes = random.Random(seed), []
    for pitch in rng.sample(range(21, 109), 30):
        at = rng.uniform(0, 2)
        while at < seconds and len(notes) < count:
            end = at + rng.choice((0.05, 0.3, 1.0, 4.0))
            notes.append((round(at * 1920) / 1920 if rng.random() < 0.3 else at, end, pitch, rng.randint(1, 127)))
            at = end if rng.random() < 0.3 else end + rng.uniform(0, 3)
    at, controls = 0.5, []
    while at < seconds:
        up = at + rng.uniform(0.2, 3)
        controls += [(at, 64, rng.randint(64, 127), at), (up, 64, 0, at)]
        at = up if rng.random() < 0.3 else up + rng.uniform(0.05, 1)
    return notes, controls


def file_messages(midi):
    with tempfile.TemporaryDirectory() as tmp:
        midi.write(str(Path(tmp) / 'piece.mid'))
        return [m for m in mido.MidiFile(Path(tmp) / 'piece.mid') if not m.is_meta and m.type != 'program_change']


class GrowingTests(unittest.TestCase):
    def test_it_plays_message_for_message_as_its_file_will(self):
        for seed in range(8):
            with self.subTest(seed=seed):
                notes, controls = made_up(seed)
                piece = Growing()
                for midi, until in in_parts(notes, controls, sorted(random.Random(seed).sample(range(1, 40), 6))):
                    piece.add(midi, until, 41.0)
                file = file_messages(midi_of(notes, [c[:3] for c in controls]))
                self.assertEqual(list(piece.messages(threading.Event())), file)
                self.assertGreater(len(file), 500)

    def test_a_note_ending_where_a_later_part_begins_one_lets_up_first(self):
        piece = Growing()
        piece.add(midi_of([(0.5, 2.0, 60, 80)]), 2.0, 3.0)  # let up at 2.0, where the next part's notes may begin
        piece.add(midi_of([(2.0, 2.5, 60, 90), (2.0, 2.5, 55, 70)], [(2.0, 64, 100)]), math.inf, 3.0)
        got = [(m.control,) if m.type == 'control_change' else (m.note, m.velocity)
               for m in piece.messages(threading.Event())]
        self.assertEqual(got[1:5], [(64,), (55, 70), (60, 0), (60, 90)], 'At 2.0: the pedal, then by key, the key up first')
        self.assertEqual(list(piece.messages(threading.Event())),
                         file_messages(midi_of([(0.5, 2.0, 60, 80), (2.0, 2.5, 60, 90), (2.0, 2.5, 55, 70)],
                                               [(2.0, 64, 100)])))

    def test_its_music_waits_where_its_notes_are_not_there_yet(self):
        piece, got, stopped = Growing(), [], threading.Event()
        piece.add(midi_of([(0.0, 0.2, 60, 80)]), 1.0, 5.0)
        playing = threading.Thread(target=lambda: got.extend(piece.messages(stopped)), daemon=True)
        playing.start()
        self.addCleanup(stopped.set)
        time.sleep(0.2)
        self.assertEqual([m and m.type for m in got[:3]], ['note_on', 'note_on', None], 'It waits for more')
        piece.add(midi_of([(1.0, 1.5, 62, 80)]), math.inf, 5.0)
        playing.join(2)
        self.assertFalse(playing.is_alive(), 'Ended with the piece')
        self.assertEqual([(m.note, m.velocity) for m in got if m], [(60, 80), (60, 0), (62, 80), (62, 0)])
        self.assertAlmostEqual(sum(m.time for m in got if m), 1.5)

    def test_not_made_after_all_it_says_so_and_stopped_it_ends(self):
        piece = Growing()
        piece.add(midi_of([(0.0, 0.2, 60, 80)]), 1.0, 5.0)
        threading.Timer(0.2, piece.fail).start()
        with self.assertRaises(NotMade):
            list(piece.messages(threading.Event()))
        stopped = threading.Event()
        threading.Timer(0.2, stopped.set).start()
        started = time.monotonic()
        self.assertEqual(len([m for m in Growing().messages(stopped) if m]), 0)
        self.assertLess(time.monotonic() - started, 1)

    def test_it_plays_once_there_is_enough_of_it_after_its_first_note(self):
        piece = Growing()
        self.assertIsNone(piece.first_note())
        piece.add(midi_of([]), 3.0, 60.0)  # silence first
        self.assertFalse(piece.plays())
        piece.add(midi_of([(3.2, 3.5, 60, 80)]), 3.2 + PLAY_AHEAD_S - 0.5, 60.0)
        self.assertAlmostEqual(piece.first_note(), 3.2, places=3)
        self.assertFalse(piece.plays(), 'Not enough after it yet')
        piece.add(midi_of([]), 3.2 + PLAY_AHEAD_S, 60.0)
        self.assertTrue(piece.plays())
        short = Growing()
        short.add(midi_of([(0.0, 0.5, 60, 80)]), math.inf, 1.0)
        self.assertTrue(short.plays(), 'All of it there')
        self.assertEqual(piece.notes(), [[3.2, 0.3, 60, 80]])


class PlayingTests(unittest.TestCase):
    """Playback of a piece being made, as Downloads gives it: its record (Library.making), and what there is of it."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        self.lib = Library(root / 'library.db', root / 'Music')
        self.addCleanup(self.lib.close)
        self.rec = self.lib.making('晴天', source_type='url', source_url='https://youtu.be/x', duration_s=3.0)
        self.piece = Growing()
        self.sent = []  # (seconds since the first, message)
        port = Mock()
        port.send.side_effect = lambda msg, *when: self.sent.append((time.perf_counter(), msg))
        self.p = Playback(self.lib, port=port)
        self.p.growing[Path(self.rec['midi_path'])] = self.piece
        self.addCleanup(self.p.close)
        patch.object(self.p, 'check_connection').start()
        self.addCleanup(patch.stopall)

    def notes_sent(self):
        start = next(t for t, m in self.sent if m.type == 'note_on')
        return [(round(t - start, 2), m.note) for t, m in self.sent if m.type == 'note_on' and m.velocity]

    def wait(self, check, seconds=3):
        deadline = time.monotonic() + seconds
        while not check() and time.monotonic() < deadline:
            time.sleep(0.01)
        return check()

    def test_it_plays_while_it_is_made_and_where_it_is_not_there_yet_its_music_waits(self):
        self.piece.add(midi_of([(0.0, 0.1, 60, 80), (0.3, 0.4, 62, 80)]), 0.5, 3.0)
        self.p.select(self.rec['id'])
        self.assertTrue(self.wait(lambda: self.p.position >= 0.5))
        time.sleep(0.4)  # its music waits at 0.5, where it is not there yet
        snap = self.p.snapshot()
        self.assertEqual((snap['state'], snap['ready'], snap['until'], snap['duration']), ('playing', 0.5, 0.5, 3.0))
        self.assertLessEqual(self.p.position, 0.5)
        self.piece.add(midi_of([(0.5, 0.6, 64, 80), (0.8, 0.9, 65, 80)]), math.inf, 3.0)
        self.assertTrue(self.wait(lambda: self.p.state == 'stopped'))
        sent = self.notes_sent()
        self.assertEqual([note for _, note in sent], [60, 62, 64, 65])
        self.assertAlmostEqual(sent[1][0], 0.3, delta=0.05)
        self.assertAlmostEqual(sent[2][0], 0.9, delta=0.1, msg='On from where it waited, after the wait')
        self.assertAlmostEqual(sent[3][0] - sent[2][0], 0.3, delta=0.05, msg='And in time from there')
        self.assertIsNone(self.p.snapshot()['ready'], 'All of it there')

    def test_paused_it_goes_on_from_there_and_a_seek_past_what_is_there_waits_there(self):
        self.piece.add(midi_of([(0.0, 0.1, 60, 80), (0.6, 0.7, 62, 80)]), 1.0, 3.0)
        self.p.select(self.rec['id'])
        self.assertTrue(self.wait(lambda: self.p.position >= 0.3))
        self.p.pause()
        self.assertEqual(self.p.state, 'paused')
        self.p.toggle()
        self.assertTrue(self.wait(lambda: any(m.note == 62 for _, m in self.sent if m.type == 'note_on')))
        self.p.seek(2.0)  # past what is there
        time.sleep(0.3)
        snap = self.p.snapshot()
        self.assertEqual((snap['state'], snap['position'], snap['until'], snap['ready']), ('playing', 2.0, 2.0, 1.0),
                         'It waits where it was taken')
        before = len(self.sent)
        self.piece.add(midi_of([(1.5, 1.6, 64, 80), (2.0, 2.1, 65, 80)]), math.inf, 3.0)
        self.assertTrue(self.wait(lambda: self.p.state == 'stopped'))
        self.assertEqual([m.note for _, m in self.sent[before:] if m.type == 'note_on' and m.velocity], [65],
                         'From 2.0 on')

    def test_not_made_after_all_it_stops(self):
        self.piece.add(midi_of([(0.0, 0.1, 60, 80)]), 0.5, 3.0)
        self.p.select(self.rec['id'])
        self.assertTrue(self.wait(lambda: self.p.position >= 0.4))
        self.piece.fail()
        self.assertTrue(self.wait(lambda: self.p.state == 'stopped'))
        self.assertIsNone(self.p.completed, 'Not a piece played to its end')

    def test_once_made_it_plays_from_its_file(self):
        self.piece.add(midi_of([(0.0, 0.1, 60, 80)]), math.inf, 1.0)
        with tempfile.TemporaryDirectory() as tmp:
            midi_of([(0.0, 0.1, 60, 80)]).write(str(Path(tmp) / 'piece.mid'))
            made = self.lib.made(self.rec['id'], Path(tmp) / 'piece.mid')
        del self.p.growing[Path(self.rec['midi_path'])]
        self.assertFalse(made['making'])
        self.assertTrue(made['file_exists'])
        self.assertEqual(Path(made['midi_path']).name, '晴天.mid')
        self.assertEqual(made['notes'], 1)
        self.p.select(made['id'])
        self.assertTrue(self.wait(lambda: self.p.state == 'stopped'))
        self.assertEqual([m.note for _, m in self.sent if m.type == 'note_on' and m.velocity], [60])



class Steps:
    """The transcription's process stood in for (pipeline.Transcription): it hands on parts one at a time, as the
    test lets each go (next), then makes the piece, or fails."""

    def __init__(self, parts, made=None):
        self.parts, self.made, self.go, self.handed = parts, made, threading.Semaphore(0), threading.Semaphore(0)
        self.stopped = None
        steps = self

        class Apart:
            def __init__(self, out, options, *, output_name=None, stopped=None):
                self.out = Path(out)
                steps.stopped = stopped

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                pass

            def convert(self, audio, part=None):
                for midi, until in steps.parts:
                    steps.go.acquire()
                    if steps.stopped.is_set():
                        raise InterruptedError('stopped')
                    part(midi, until, 30.0)
                    steps.handed.release()
                steps.go.acquire()
                if isinstance(steps.made, Exception):
                    raise steps.made
                (self.out / 'piece.mid').parent.mkdir(parents=True, exist_ok=True)
                midi_of([(0.5, 1.0, 60, 80), (9.0, 9.5, 64, 80)]).write(str(self.out / 'piece.mid'))
                return {'files': {'midi': str(self.out / 'piece.mid')}, 'audio_duration_s': 30.0}
        self.patch = patch('pianasoul.pipeline.Transcription', Apart)

    def next(self):
        self.go.release()
        return self.handed.acquire(timeout=2)


class MadeAsItPlaysTests(unittest.TestCase):
    PARTS = [(midi_of([(0.5, 1.0, 60, 80)]), 2.0), (midi_of([]), 0.5 + PLAY_AHEAD_S), (midi_of([(9.0, 9.5, 64, 80)]), math.inf)]

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.lib = Library(self.root / 'library.db', self.root / 'Music')
        self.addCleanup(self.lib.close)
        self.audio = self.root / 'a.m4a'
        self.audio.write_bytes(b'x')
        quiet = contextlib.redirect_stdout(io.StringIO())
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)
        for patcher in (patch('pianasoul.download.download_audio', return_value=(self.audio, {'video_title': '晴天 钢琴'})),
                        patch('pianasoul.downloads._video_page', return_value={})):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.unmade = []

    def downloads(self, steps):
        steps.patch.start()
        self.addCleanup(steps.patch.stop)
        d = Downloads(self.lib, name_piece=lambda query, video, artists: {'piece': '晴天', 'artist': '周杰伦'})
        d.unmade.append(self.unmade.append)
        return d

    def eventually(self, check) -> bool:
        deadline = time.monotonic() + 3
        while not check() and time.monotonic() < deadline:
            time.sleep(0.01)
        return check()

    def test_once_enough_of_it_is_there_it_plays_under_its_names_and_is_in_the_library_once_made(self):
        steps = Steps(self.PARTS)
        d = self.downloads(steps)
        job = d.add('https://youtu.be/abcdefghijk')
        self.assertTrue(steps.next())
        self.assertFalse(job.playable.is_set(), 'Not enough of it after its first note yet')
        self.assertTrue(steps.next())
        self.assertTrue(job.playable.wait(2))
        rec = self.lib.get(job.recording)
        self.assertEqual((rec['title'], rec['making'], rec['file_exists'], rec['duration_s']),
                         ('周杰伦 - 晴天', True, False, 30.0))
        self.assertIn(Path(rec['midi_path']), d.growing)
        self.assertEqual(self.lib.search('晴天'), [], 'Not in the library yet')
        self.assertEqual(d.snapshot()[0]['recording'], rec['id'], 'The phone knows its piece')
        self.assertTrue(steps.next())
        steps.go.release()
        self.assertTrue(job.finished.wait(2))
        made = self.lib.get(job.recording)
        self.assertEqual((job.status, job.result['id'], made['making'], made['file_exists'], made['notes']),
                         ('done', rec['id'], False, True, 2))
        self.assertEqual(Path(made['midi_path']).name, '周杰伦 - 晴天.mid')
        self.assertEqual([r['id'] for r in self.lib.search('晴天')], [rec['id']])
        self.assertEqual(d.growing, {}, 'Its file plays from now on')
        self.assertEqual(self.unmade, [])

    def test_not_made_after_all_it_goes_and_the_player_is_told(self):
        for end in ('failed', 'stopped'):
            with self.subTest(end=end):
                steps = Steps(self.PARTS, RuntimeError('could not decode audio'))
                d = self.downloads(steps)
                job = d.add(f'https://youtu.be/{end:x<11}')
                steps.next(), steps.next()
                self.assertTrue(job.playable.wait(2))
                rec_id, piece = job.recording, d.growing[Path(self.lib.get(job.recording)['midi_path'])]
                if end == 'stopped':
                    d.stop(job.id)
                steps.go.release()
                steps.go.release()
                self.assertTrue(job.finished.wait(2))
                self.assertEqual(job.status, end)
                self.assertIsNone(self.lib.get(rec_id), 'Nothing of it stays')
                self.assertIsNone(job.recording)
                self.assertEqual(self.unmade[-1], rec_id)
                self.assertTrue(piece.failed, 'A player on it stops')
                self.assertEqual(d.growing, {})

    def test_a_version_that_cannot_be_had_gives_way_to_the_next_which_plays_as_it_is_made(self):
        def download(url, folder, progress):
            if url.endswith('aaaaaaaaaaa'):
                raise ValueError('a preview only')
            return self.audio, {'video_title': '晴天 钢琴'}
        steps = Steps(self.PARTS)
        with patch('pianasoul.download.download_audio', side_effect=download):
            d = self.downloads(steps)
            job = d.add('https://youtu.be/aaaaaaaaaaa', piece='晴天', artist='周杰伦', performer='A',
                        versions=[{'url': 'https://youtu.be/bbbbbbbbbbb', 'performer': 'B'}])
            steps.next(), steps.next()
            self.assertTrue(job.playable.wait(2))
            rec = self.lib.get(job.recording)
            self.assertEqual((rec['title'], rec['making'], rec['source_url']),
                             ('周杰伦 - 晴天 (B)', True, 'https://www.youtube.com/watch?v=bbbbbbbbbbb'))
            steps.next()
            steps.go.release()
            self.assertTrue(job.finished.wait(2))
        self.assertEqual((job.status, job.result['id']), ('done', rec['id']))

    def test_not_made_after_all_it_gives_way_to_its_next_version_which_can_play_in_its_turn(self):
        steps = Steps(self.PARTS, RuntimeError('the transcription ended with -9'))
        d = self.downloads(steps)
        job = d.add('https://youtu.be/eeeeeeeeeee', piece='晴天', artist='周杰伦', performer='A',
                    versions=[{'url': 'https://youtu.be/fffffffffff', 'performer': 'B'}])
        steps.next(), steps.next()
        self.assertTrue(job.playable.wait(2))
        first = job.recording
        told = threading.Thread(target=d.wait, args=(lambda: job.recording != first,), daemon=True)
        told.start()
        self.assertTrue(steps.next())
        steps.go.release()  # it fails, all of it there
        told.join(2)
        self.assertFalse(told.is_alive(), 'Those waiting for it to change are told (wait)')
        self.assertEqual(self.unmade, [first])
        self.assertIsNone(self.lib.get(first), 'Nothing of it stays')
        self.assertTrue(self.eventually(lambda: job.source == 'https://www.youtube.com/watch?v=fffffffffff'))
        self.assertFalse(job.playable.is_set(), 'Not while its next version is tried')
        steps.made = None
        steps.next(), steps.next()
        self.assertTrue(job.playable.wait(2))
        rec = self.lib.get(job.recording)
        self.assertEqual((rec['title'], rec['making']), ('周杰伦 - 晴天 (B)', True))
        steps.next()
        steps.go.release()
        self.assertTrue(job.finished.wait(2))
        self.assertEqual((job.status, job.recording, self.lib.get(rec['id'])['file_exists']), ('done', rec['id'], True))

    def test_her_piece_not_made_after_all_plays_from_its_next_version_in_its_place(self):
        from types import SimpleNamespace
        from pianasoul.chat import Assistant
        from pianasoul.core import Core
        from test_app import Scripted
        steps = Steps(self.PARTS, RuntimeError('the transcription ended with -9'))
        steps.patch.start()
        self.addCleanup(steps.patch.stop)
        core = Core(lib=self.lib, assistant=False)
        self.addCleanup(core.close)
        for quiet in (patch.object(Playback, 'play'), patch.object(core.playback, 'check_connection'),
                      patch.object(core.playback, 'look_for_piano')):
            quiet.start()
            self.addCleanup(quiet.stop)
        her = Assistant(self.lib, core.playback, core.conversations, core.downloads)
        said = []
        her.on_message = said.append
        call = SimpleNamespace(name='transcribe_url', arguments={
            'url': 'https://youtu.be/ggggggggggg', 'piece': '晴天', 'artist': '周杰伦', 'performer': 'A',
            'alternatives': [{'url': 'https://youtu.be/hhhhhhhhhhh', 'performer': 'B'}]})
        her.ask(Scripted([call], '找到啦，准备好就弹给你～'), '我想听晴天')
        [job] = core.downloads.jobs
        steps.next(), steps.next()
        self.assertTrue(self.eventually(lambda: job.recording and core.playback.current_id == job.recording))
        steps.next()
        steps.go.release()  # it fails
        self.assertTrue(self.eventually(lambda: job.source.endswith('hhhhhhhhhhh') and her.preparing))
        self.assertIsNone(core.playback.current_id, 'What there was of it stopped')
        steps.made = None
        steps.next(), steps.next()
        self.assertTrue(self.eventually(lambda: job.recording is not None and core.playback.current_id == job.recording
                                        and said[-1]['text'] == '周杰伦 - 晴天 (B)'), 'Its next version, in its place')
        steps.next()
        steps.go.release()
        self.assertTrue(job.finished.wait(2))
        self.assertTrue(self.eventually(lambda: not her.preparing))
        self.assertFalse(any('没准备好' in m['text'] for m in said), 'Made after all: nothing to say of it')

    def test_one_made_before_whose_file_is_gone_is_made_again_whole(self):
        old = self.lib.making('周杰伦 - 晴天', source_type='url', source_url='https://www.youtube.com/watch?v=abcdefghijk',
                              duration_s=30.0)
        self.lib.execute('UPDATE recordings SET making = 0 WHERE id = ?', (old['id'],))  # made once; its file since gone
        steps = Steps(self.PARTS)
        d = self.downloads(steps)
        job = d.add('https://youtu.be/abcdefghijk')
        for _ in self.PARTS:
            steps.next()
        self.assertFalse(job.playable.is_set())
        steps.go.release()
        self.assertTrue(job.finished.wait(2))
        self.assertEqual((job.recording, self.lib.get(old['id'])['file_exists']), (old['id'], True))

    def test_one_being_made_when_the_service_stopped_was_not_made_and_is_gone_when_it_starts(self):
        from pianasoul.core import Core
        left = self.lib.making('晴天', source_type='url', source_url='https://youtu.be/x', duration_s=30.0)
        core = Core(lib=self.lib, assistant=False)
        self.addCleanup(core.close)
        self.assertIsNone(self.lib.get(left['id']))

    def test_the_phone_has_it_apart_from_the_library_and_deleting_it_stops_its_making(self):
        from pianasoul import server
        from pianasoul.core import Core
        steps = Steps(self.PARTS)
        steps.patch.start()
        self.addCleanup(steps.patch.stop)
        core = Core(lib=self.lib, assistant=False)
        self.addCleanup(core.close)
        heard = []
        core.listeners.append(heard.append)
        job = core.downloads.add('https://youtu.be/abcdefghijk')
        steps.next(), steps.next()
        self.assertTrue(job.playable.wait(2))
        self.assertIn({'type': 'library', 'pictures': {}, 'descriptions': {}}, heard, 'The phone reads it in')
        [rec] = [r for r in core.recordings() if r['id'] == job.recording]
        self.assertTrue(rec['making'])
        self.assertIs(core.playback.growing, core.downloads.growing)
        with patch.object(server, 'core', core):
            self.assertEqual(server.notes(rec['id']), [[0.5, 0.5, 60, 80]], 'Its notes so far, for its picture')
        core.delete_recordings([rec['id']])
        steps.go.release()
        steps.go.release()
        self.assertTrue(job.finished.wait(2))
        self.assertEqual(job.status, 'stopped')
        self.assertIsNone(self.lib.get(rec['id']))


if __name__ == '__main__':
    unittest.main()
