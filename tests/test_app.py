"""What the app keeps on the Mac: the play list, her conversations, new pieces, and the core they share.
No paid APIs, models or MIDI devices."""
import contextlib
import io
import sqlite3
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import mido

import places  # noqa: F401  (first: the library, data and cache folders are throwaway ones)
from pianasoul import chat as chat_module, play
from pianasoul.chat import Assistant
from pianasoul.conversations import QUIET_S, Conversations
from pianasoul.downloads import Downloads, Job
from pianasoul.library import Library
from pianasoul.lines import LINES, every
from pianasoul.play import GAP_S, Player
from pianasoul.playback import Playback


def make_midi(path: Path, seconds: float = 1.0) -> Path:
    midi = mido.MidiFile()
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.append(mido.Message('note_on', note=60, velocity=64, time=0))
    track.append(mido.Message('note_off', note=60, time=round(mido.second2tick(seconds, 480, 500000))))
    path.parent.mkdir(parents=True, exist_ok=True)
    midi.save(path)
    return path


def piece_made(downloads: Downloads, source: str, **details) -> dict:
    """Queue a piece and wait for it: its library record, or {'error': ...}."""
    job = downloads.add(source, **details)
    job.finished.wait(10)
    return job.result or {'error': job.error or job.status}


class Scripted:
    """A chat model that answers from a script."""
    def __init__(self, *steps):
        self.steps, self.loaded, self.resets = iter(steps), None, 0

    def send_user(self, text):
        step = next(self.steps, '好。')
        return SimpleNamespace(text=step if isinstance(step, str) else '', tool_calls=[] if isinstance(step, str) else step)

    send_tool_results = lambda self, results: self.send_user('')

    def reset(self):
        self.resets += 1

    def load(self, lines):
        self.loaded = lines

    def discard_turn(self):
        pass


class Base(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.lib = Library(self.root / 'library.db', self.root / 'Music')
        self.addCleanup(self.lib.close)
        self.ids = [self.lib.add(make_midi(self.root / 'Music' / f'{t}.mid'), t)['id']
                    for t in ('周杰伦 - 晴天 (MappleZS)', '周杰伦 - 搁浅 (MappleZS)', 'Chopin - Nocturne (Lang Lang)')]
        quiet = contextlib.redirect_stdout(io.StringIO())
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)


class PlaybackTests(Base):
    def setUp(self):
        super().setUp()
        self.p = Playback(self.lib, port=Mock())
        self.addCleanup(self.p.close)
        sent = patch.object(Playback, 'play')  # no MIDI goes out; the thread ends at once
        sent.start()
        self.addCleanup(sent.stop)
        patch.object(self.p, 'check_connection').start()
        self.addCleanup(patch.stopall)
        self.changes = []
        self.p.listeners.append(lambda: self.changes.append(self.p.snapshot()))

    def test_a_piece_plays_from_its_list(self):
        a, b, c = self.ids
        self.p.select(b, self.ids, {'kind': 'library'})
        snap = self.p.snapshot()
        self.assertEqual((snap['id'], snap['state'], snap['ids'], snap['scope']), (b, 'playing', self.ids, {'kind': 'library'}))
        self.assertTrue(self.lib.get(b)['last_played_at'], 'It is in 最近播放')
        self.p.step(1)
        self.assertEqual(self.p.current_id, c)

    def test_a_piece_whose_file_is_gone_is_passed_over(self):
        a, b, c = self.ids
        self.p.select(a, self.ids)
        Path(self.lib.get(b)['midi_path']).unlink()
        self.p.step(1)
        self.assertEqual(self.p.current_id, c)

    def test_a_piece_whose_file_is_gone_does_not_start(self):
        self.p.select(self.ids[0], self.ids)
        self.p.pause()
        Path(self.lib.get(self.ids[0])['midi_path']).unlink()
        with self.assertRaises(FileNotFoundError):
            self.p.toggle()
        self.assertEqual(self.p.state, 'paused', 'Nothing changed')

    def test_a_paused_piece_renamed_meanwhile_goes_on_from_its_new_file(self):
        a = self.ids[0]
        self.p.select(a, self.ids)
        self.p.state, self.p._offset = 'paused', 12.0
        renamed = self.lib.edit(a, '晴天', '周杰伦', 'Fei Wang')
        with patch.object(self.p, 'start') as start:
            self.p.toggle()
        self.assertEqual(start.call_args.args, ([(Path(renamed['midi_path']), '周杰伦 - 晴天 (Fei Wang)')], 12.0))
        self.assertTrue(Path(renamed['midi_path']).is_file())

    def test_next_on_the_last_piece_ends_the_list_and_never_wraps(self):
        a, b, c = self.ids
        self.p.select(c, self.ids)
        self.p.step(1)
        snap = self.p.snapshot()
        self.assertEqual((snap['id'], snap['state'], snap['ended'], snap['position']), (a, 'stopped', True, 0))
        self.p.toggle()  # ▶ plays the list again from its first piece
        self.assertEqual((self.p.current_id, self.p.state, self.p.ended), (a, 'playing', False))

    def test_previous_restarts_after_three_seconds_and_stops_at_the_first(self):
        a, b, c = self.ids
        self.p.select(b, self.ids)
        with patch.object(Playback, 'position', 5.0):
            self.p.step(-1)
        self.assertEqual(self.p.current_id, b)
        self.p.step(-1)
        self.assertEqual(self.p.current_id, a)
        self.p.step(-1)
        self.assertEqual(self.p.current_id, a)

    def test_repeat_one_never_ends(self):
        c = self.ids[2]
        self.p.select(c, self.ids)
        self.p.set_mode('one')
        self.p.step(1)
        self.assertEqual((self.p.current_id, self.p.ended), (c, False))

    def test_shuffle_puts_the_playing_piece_first_and_off_restores_the_order(self):
        b = self.ids[1]
        self.p.select(b, self.ids)
        self.p.set_mode('shuffle')
        self.assertEqual(self.p.ids[0], b)
        self.assertEqual(sorted(self.p.ids), sorted(self.ids))
        self.p.remove(self.ids[0])
        self.p.set_mode('order')
        self.assertEqual(self.p.ids, self.ids[1:])

    def test_the_list_is_edited_but_the_playing_piece_stays(self):
        a, b, c = self.ids
        self.p.select(a, self.ids)
        self.p.remove(a)
        self.assertIn(a, self.p.ids)
        self.p.move(c, 0)
        self.assertEqual(self.p.ids, [c, a, b])
        self.p.remove(b)
        self.assertEqual(self.p.ids, [c, a])

    def test_a_piece_lined_up_comes_after_the_pieces_of_its_request_still_to_come(self):
        a, b, c = self.ids
        self.p.select(a, [a, b], {'kind': 'conversation', 'id': 1})
        listed = self.p.listed
        self.assertTrue(self.p.line_up(c, [a, b], listed))
        self.assertEqual(self.p.ids, [a, b, c])
        self.p.select(a, [a, b, c], {'kind': 'library'})
        self.assertTrue(self.p.line_up(c, [a], self.p.listed), 'One in the list already moves there')
        self.assertEqual(self.p.ids, [a, c, b], 'Next, when none of its request is still to come')
        self.assertFalse(self.p.line_up(a, [a], self.p.listed), 'Not the piece playing')

    def test_a_piece_lined_up_keeps_its_place_in_the_list_s_own_order_while_shuffled(self):
        a, b, c = self.ids
        self.p.select(a, [a, b], {'kind': 'conversation', 'id': 1})
        self.p.set_mode('shuffle')
        self.p.line_up(c, [a], self.p.listed)
        self.assertEqual(self.p.ids, [a, c, b])
        self.p.set_mode('order')
        self.assertEqual(self.p.ids, [a, c, b])

    def test_only_the_list_it_was_lined_up_for_takes_it_while_it_goes_on(self):
        a, b, c = self.ids
        self.p.select(a, [a], {'kind': 'conversation', 'id': 1})
        listed = self.p.listed
        self.p.pause()
        self.assertTrue(self.p.line_up(b, [a], listed), 'Paused, it goes on later')
        self.p.state, self.p.completed = 'stopped', ('晴天', time.perf_counter())
        self.assertTrue(self.p.line_up(c, [a, b], listed), 'From one piece to the next')
        self.p.show(a, [a], {'kind': 'favorites'})
        self.assertFalse(self.p.line_up(c, [a], listed), 'Another list, tapped on the phone')
        for over in (self.p.stop, self.p._end_list):
            self.p.select(a, [a], {'kind': 'conversation', 'id': 1})
            over()
            self.assertFalse(self.p.line_up(b, [a], self.p.listed), over.__name__)
            self.assertEqual(self.p.ids, [a])

    def test_a_natural_finish_plays_the_next_piece_after_a_short_silence(self):
        a, b, c = self.ids
        self.p.select(a, self.ids)
        self.p.state, self.p.completed = 'stopped', ('晴天', time.perf_counter())
        with patch.object(self.p, 'start') as start:
            self.p._next(self.p._run)
        self.assertEqual(self.p.current_id, b)
        self.assertLess(start.call_args.args[1], 0, 'A moment of silence first')

    def test_a_finish_after_another_choice_does_nothing(self):
        a, b, c = self.ids
        self.p.select(a, self.ids)
        late = self.p._run
        self.p.select(c, self.ids)
        self.p.state, self.p.completed = 'stopped', ('晴天', time.perf_counter())
        self.p._next(late)
        self.assertEqual(self.p.current_id, c)

    def test_the_end_of_the_list_is_kept_for_her_closing_word(self):
        c = self.ids[2]
        self.p.select(c, self.ids)
        self.p.state, self.p.completed = 'stopped', ('Nocturne', time.perf_counter())
        self.p._next(self.p._run)
        self.assertTrue(self.p.ended)
        self.assertIsNotNone(self.p.completed)

    def test_tapping_the_playing_piece_in_another_list_changes_only_the_list(self):
        a, b, c = self.ids
        self.p.select(a, self.ids)
        run = self.p._run
        self.p.show(a, [c, a], {'kind': 'favorites'})
        self.assertEqual((self.p.ids, self.p.scope, self.p._run), ([c, a], {'kind': 'favorites'}, run))

    def test_a_missing_piano_leaves_everything_as_it_was(self):
        a, b, c = self.ids
        self.p.select(a, self.ids)
        with patch.object(self.p, 'check_connection', side_effect=LookupError('gone')):
            with self.assertRaises(LookupError):
                self.p.select(c, [c])
            with self.assertRaises(LookupError):
                self.p.step(1)
        self.assertEqual((self.p.current_id, self.p.ids), (a, self.ids))

    def test_a_deleted_piece_leaves_the_list_and_stops_if_it_was_playing(self):
        a, b, c = self.ids
        self.p.select(a, self.ids)
        self.p.forget(b)
        self.assertEqual(self.p.ids, [a, c])
        self.p.forget(a)
        self.assertEqual((self.p.current_id, self.p.state), (None, 'stopped'))

    def test_seeking_while_paused_waits_there(self):
        self.p.select(self.ids[0], self.ids)
        self.p.pause()
        self.p.seek(0.5)
        self.assertEqual((self.p.state, self.p.position), ('paused', 0.5))
        self.p.seek(99)
        self.assertEqual(self.p.position, self.p.duration, 'Not past the end')

    def test_a_piano_that_goes_away_is_told(self):
        self.p.select(self.ids[0], self.ids)

        def gone(path, from_s):  # the playing thread finds the piano gone and pauses there
            self.p.state, self.p.lost = 'paused', 'gone'
            return False
        with patch.object(self.p, '_play_file', side_effect=gone):
            Player.play(self.p, [self.p.item(self.ids[0])])
        self.assertEqual(self.changes[-1]['state'], 'paused')

    def test_notes_that_no_longer_go_out_are_a_piano_that_went_away(self):
        player = Player(port=Mock())
        player.port.send.side_effect = OSError('CoreMIDI could not send')
        told = []
        player.after_run = lambda: told.append((player.state, player.lost))
        player.play([self.p.item(self.ids[0])])
        self.assertEqual(told, [('paused', 'CoreMIDI could not send')])

    def test_notes_that_leave_heard_late_are_said_once_in_the_log(self):
        # The sending held up a third of a second at the second note: the notes after it hurry out late too, and it
        # is said once, where it began. On time, nothing is said.
        midi = mido.MidiFile()
        track = mido.MidiTrack()
        midi.tracks.append(track)
        step = round(mido.second2tick(0.05, 480, 500000))
        for i in range(8):
            track.append(mido.Message('note_on', note=60 + i, velocity=64, time=0 if i == 0 else step))
        path = self.root / 'steady.mid'
        midi.save(path)
        sends = iter(range(100))
        port = Mock()
        port.send.side_effect = lambda *a, **k: next(sends) == 1 and time.sleep(0.35)
        player = Player(port=port)
        with patch('builtins.print') as said:
            player.play([(path, 'steady')])
        lines = [c.args[0] for c in said.call_args_list if 'late' in str(c.args[0])]
        self.assertEqual(len(lines), 1, lines)
        self.assertIn('notes left up to 0.', lines[0])
        self.assertIn('at 0:00', lines[0])
        with patch('builtins.print') as said:
            Player(port=Mock()).play([(path, 'steady')])
        self.assertFalse([c for c in said.call_args_list if 'late' in str(c.args[0])])

    def test_a_piece_from_its_start_skips_the_silence_before_its_first_note_but_a_moment(self):
        midi = mido.MidiFile()
        track = mido.MidiTrack()
        midi.tracks.append(track)
        track.append(mido.Message('note_on', note=60, velocity=64, time=round(mido.second2tick(3.0, 480, 500000))))
        track.append(mido.Message('note_off', note=60, time=240))
        path = self.root / 'Music' / 'late start.mid'
        midi.save(path)
        rec = self.lib.add(path, 'Late start')['id']
        self.assertAlmostEqual(play.begin(path, 0.0), 3.0 - play.LEAD_IN_S)
        self.assertAlmostEqual(play.begin(path, -2.0), 1.0 - play.LEAD_IN_S, msg='After a gap, the gap first')
        self.assertEqual(play.begin(path, 1.5), 1.5, 'From where it was: as it is')
        self.assertAlmostEqual(play.begin(path, 0.0, lead=1.0), 3.0, msg='Sent a second early: heard after it anyway')
        self.assertAlmostEqual(play.begin(path, 0.0, lead=0.5), 3.0 - play.LEAD_IN_S + 0.5)
        self.assertEqual(play.begin(self.lib.get(self.ids[0])['midi_path'], 0.0), 0.0, 'One that begins at once')
        self.p.select(rec, [rec])
        self.assertAlmostEqual(self.p.snapshot()['position'], 3.0 - play.LEAD_IN_S, delta=0.1)
        sent = []
        port = Mock()
        port.send.side_effect = lambda msg, *a, **k: msg.type == 'note_on' and sent.append(time.perf_counter())
        started = time.perf_counter()
        Player(port=port).play([(path, 'late start')])
        self.assertAlmostEqual(sent[0] - started, play.LEAD_IN_S, delta=0.3)
        self.p.select(rec, [self.ids[0], rec])
        self.p.step(-1)
        self.assertEqual(self.p.current_id, self.ids[0], '⏮ at once: the one before, its 3 s from where it began')

    def test_the_pedals_an_opening_skipped_set_are_sent_before_its_first_note(self):
        midi = mido.MidiFile()
        track = mido.MidiTrack()
        midi.tracks.append(track)
        half = round(mido.second2tick(0.5, 480, 500000))
        track.append(mido.Message('control_change', control=67, value=127, time=half))  # una corda
        track.append(mido.Message('control_change', control=64, value=127, time=0))
        track.append(mido.Message('note_on', note=60, velocity=40, time=5 * half))
        track.append(mido.Message('note_off', note=60, time=240))
        path = self.root / 'soft.mid'
        midi.save(path)
        port = Mock()
        Player(port=port).play([(path, 'soft')])
        sent = [(m.type, getattr(m, 'control', None), getattr(m, 'value', None)) for m in
                (c.args[0] for c in port.send.call_args_list)]
        first_note = sent.index(('note_on', None, None))
        self.assertIn(('control_change', 67, 127), sent[:first_note])
        self.assertIn(('control_change', 64, 127), sent[:first_note])

    def test_the_phone_is_told_when_the_position_is_heard(self):
        self.p.lead = 1.0  # the notes leave a second early
        self.p.select(self.ids[0], self.ids)
        self.assertAlmostEqual(self.p.snapshot()['starts_in'], 1.0, delta=.1)
        self.p.start([self.p.item(self.ids[1])], -GAP_S)  # the next piece, after a short silence
        self.assertAlmostEqual(self.p.snapshot()['starts_in'], 1.0 + GAP_S, delta=.1)
        self.p.pause()
        self.assertEqual(self.p.snapshot()['starts_in'], 0)

    def test_after_a_pause_the_phone_goes_on_until_the_notes_already_sent_stop(self):
        self.p.lead = 1.0
        self.p.select(self.ids[0], self.ids)
        self.p._sent_to, self.p._last_when = 5.0, time.perf_counter() + 1.0  # sent up to 0:05, heard for a second yet
        self.p.pause()
        snap = self.p.snapshot()
        self.assertEqual(snap['until'], 5.0)
        self.assertAlmostEqual(snap['position'], 4.0, delta=.1)
        self.p._last_when = time.perf_counter()  # and has sounded
        snap = self.p.snapshot()
        self.assertEqual(snap['position'], snap['until'])

    def test_the_volume_is_told_kept_and_heard_before_each_piece(self):
        self.assertEqual(self.p.volume, 80, 'Until one is chosen')
        self.p.set_volume(150)
        self.assertEqual((self.changes[-1]['volume'], self.lib.setting('volume')), (100, '100'))
        self.p.set_volume(40)
        self.assertEqual([c.args[0] for c in self.p.port.send.call_args_list[-16:]],
                         [mido.Message('control_change', channel=ch, control=7, value=51) for ch in range(16)],
                         'On every channel')
        again = Playback(self.lib, port=Mock())
        self.addCleanup(again.close)
        self.assertEqual(again.volume, 40, 'Kept for the next run')

        midi = mido.MidiFile(self.lib.get(self.ids[0])['midi_path'])
        midi.tracks[0].insert(0, mido.Message('control_change', control=7, value=127))  # a file with a volume of its own
        midi.save(self.root / 'loud.mid')
        for volume, first in ((40, [('control_change', 7, 51)] * 16), (None, [])):  # None: the piano's own (./run.sh play)
            player = Player(port=Mock())
            player.volume = volume
            player.play([(self.root / 'loud.mid', 'loud')])
            sent = [m for (m, *_), _ in player.port.send.call_args_list]
            self.assertEqual([(m.type, m.control, m.value) for m in sent if m.type == 'control_change' and m.control == 7],
                             first, 'Only the listener\'s volume, before the first note')
            self.assertEqual(sent[len(first)].type, 'note_on')

    def test_what_plays_is_kept_as_the_service_stops_and_comes_back_paused_there(self):
        a, b, c = self.ids
        long = self.lib.add(make_midi(self.root / 'Music' / 'long.mid', seconds=120), 'Liszt - Liebestraum')['id']
        ids = [a, long, b, c]
        self.p.select(long, ids, {'kind': 'playlist', 'id': 7})
        self.p.set_mode('shuffle')
        shuffled, unshuffled = list(self.p.ids), list(self.p.unshuffled)
        self.p._sent_to = 42.0  # sent up to 42 s: heard to there as the notes already sent finish
        self.p.close()  # the service stops: the Mac app quits, say
        again = Playback(self.lib, port=Mock())
        self.addCleanup(again.close)
        self.assertEqual(again.snapshot()['id'], None, 'Taken once the service is up (Core), not by any player')
        again.resume()
        snap = again.snapshot()
        self.assertEqual((snap['id'], snap['state'], snap['ids'], snap['scope'], snap['mode'], again.unshuffled),
                         (long, 'paused', shuffled, {'kind': 'playlist', 'id': 7}, 'shuffle', unshuffled))
        self.assertEqual(snap['position'], 42.0, 'Where its sound stopped, not a lead before it')
        self.assertEqual(self.lib.setting('kept_playback'), '', 'Taken once: a crash later brings back nothing')
        with patch.object(again, 'start') as start:
            again.toggle()
        self.assertEqual((start.call_args.args[0][0][1], start.call_args.args[1]), ('Liszt - Liebestraum', 42.0),
                         '▶ goes on from there')
        again.set_mode('order')
        self.assertEqual(again.ids, unshuffled, 'Shuffle off: the list in its own order again')

    def test_what_comes_back_is_the_piece_in_the_library_whose_file_may_be_away_for_now(self):
        a, b, c = self.ids
        self.p.select(a, self.ids)
        self.p.stop()
        self.p.close()
        stopped = Playback(self.lib, port=Mock())
        stopped.resume()
        self.assertEqual(stopped.snapshot()['id'], None, 'Stopped: nothing kept')
        for deleted in (False, True):
            self.p.select(b, self.ids)
            self.p.pause()
            self.p.close()
            if deleted:  # deleted on the phone: no longer in the library
                self.lib.delete(b)
            else:  # renamed in Finder, say: the folder finds it again once it has been scanned
                Path(self.lib.get(b)['midi_path']).unlink()
            again = Playback(self.lib, port=Mock())
            again.resume()
            self.assertEqual((again.current_id, again.state), (None, 'stopped') if deleted else (b, 'paused'))
            if not deleted:
                with self.assertRaises(FileNotFoundError):  # as for any paused piece whose file is away
                    again.toggle()
                self.assertEqual(again.state, 'paused')
                make_midi(Path(self.lib.get(b)['midi_path']))

    def test_the_piano_stops_even_when_what_plays_cannot_be_kept(self):
        self.p.select(self.ids[0], self.ids)
        with patch.object(self.lib, 'set_setting', side_effect=sqlite3.OperationalError('database or disk is full')):
            self.p.close()
        self.assertEqual(self.p.state, 'stopped')

    def test_every_change_is_told(self):
        self.p.select(self.ids[0], self.ids)
        self.p.set_mode('one')
        self.assertTrue(self.changes)
        self.assertEqual(self.changes[-1]['mode'], 'one')


class ConversationTests(Base):
    def test_lines_and_pieces_are_kept_and_read_back(self):
        c = Conversations(self.lib)
        cid = c.create()
        c.add(cid, 'user', '小茗同学，弹一下晴天')
        c.add(cid, 'assistant', '好呀，晴天。')
        c.add(cid, 'piece', '周杰伦 - 晴天 (MappleZS)', self.ids[0])
        self.assertEqual([m['recording_id'] for m in c.messages(cid) if m['role'] == 'piece'], [self.ids[0]])
        self.assertEqual(c.history(cid), [('user', '小茗同学，弹一下晴天'), ('assistant', '好呀，晴天。'),
                                          ('assistant', '[played: 周杰伦 - 晴天 (MappleZS)]')])
        found = c.all()[0]
        self.assertEqual((found['first'], found['pieces']), ('小茗同学，弹一下晴天', ['周杰伦 - 晴天 (MappleZS)']))
        c.rename(cid, '晴天')
        c.pin(cid, True)
        self.assertEqual(c.get(cid)['title'], '晴天')
        self.assertTrue(c.get(cid)['pinned_at'])
        c.delete(cid)
        self.assertEqual(c.all(), [])

    def test_the_pieces_played_in_it_by_number_in_their_order_each_once(self):
        a, b, c = self.ids
        conv = Conversations(self.lib)
        cid = conv.create()
        conv.add(cid, 'user', '弹晴天')
        for rec in (b, a, b, None, c):
            conv.add(cid, 'piece', 'a title', rec)
        self.assertEqual(conv.played(cid), [b, a, c])
        conv.forget(a)
        self.assertEqual(conv.played(cid), [b, c], 'A piece that left the library is not among them')

    def test_search_finds_what_was_said_and_the_name(self):
        c = Conversations(self.lib)
        sunny = c.create()
        c.add(sunny, 'user', '小茗同学，弹一下周杰伦的晴天')
        c.add(sunny, 'assistant', '好呀，晴天来啦')
        moon = c.create()
        c.add(moon, 'user', 'Play Clair de Lune')
        c.rename(moon, 'Debussy night')
        c.create()  # nothing said: not a conversation yet
        self.assertEqual([(f['id'], f['message']['text']) for f in c.search('晴天')], [(sunny, '好呀，晴天来啦')],
                         'The latest thing said that has the words')
        self.assertEqual(c.search('CLAIR')[0]['message']['text'], 'Play Clair de Lune')
        self.assertEqual(c.search('debussy'), [{'id': moon, 'message': None}], 'Only the name has it')
        self.assertEqual(c.search('夜曲'), [])

    def test_the_request_log_becomes_conversations_once(self):
        for at, text, rec in [('2026-10-25T06:16:26+00:00', 'play someone like you', self.ids[0]),
                              ('2026-10-25T06:30:00+00:00', '再来一首', self.ids[1]),
                              ('2026-10-25T09:00:00+00:00', '我想听夜曲', self.ids[2])]:
            self.lib.execute('INSERT INTO requests (asked_at, text, recording_id) VALUES (?, ?, ?)', (at, text, rec))
        c = Conversations(self.lib)
        found = c.all()
        self.assertEqual(len(found), 2, 'A quiet half hour starts a new one')
        self.assertEqual([m['role'] for m in c.messages(found[1]['id'])], ['user', 'piece', 'user', 'piece'])
        Conversations(self.lib)
        self.assertEqual(len(c.all()), 2, 'Only once')


class AssistantTests(Base):
    def setUp(self):
        super().setUp()
        self.p = Playback(self.lib, port=Mock())
        self.addCleanup(self.p.close)
        patch.object(Playback, 'play').start()
        patch.object(self.p, 'check_connection').start()
        self.addCleanup(patch.stopall)
        self.a = Assistant(self.lib, self.p)
        self.kept = []
        self.a.on_message = self.kept.append

    def call(self, name, **arguments):
        return SimpleNamespace(name=name, arguments=arguments)

    def test_a_request_her_lines_and_the_piece_are_kept_in_order(self):
        chat = Scripted([self.call('search_library', query='晴天')], [self.call('play_recording', id=self.ids[0])],
                        '好呀，晴天。')
        self.a.ask(chat, '弹一下晴天', typed=True)
        self.assertEqual([(m['role'], m['text']) for m in self.kept],
                         [('user', '弹一下晴天'), ('assistant', '好呀，晴天。'), ('piece', '周杰伦 - 晴天 (MappleZS)')])
        self.assertEqual(self.p.scope, {'kind': 'conversation', 'id': self.a.conversation_id})

    def test_typed_to_she_replies_first_then_the_piece_starts(self):
        events = []
        self.a.on_message = lambda m: events.append(m['role'])
        self.a.on_busy = lambda busy: events.append(f'busy {busy}')
        select = self.p.select
        plays = lambda *a, **k: (events.append('plays'), select(*a, **k))
        with patch.object(self.p, 'select', side_effect=plays), \
             patch.object(chat_module, 'TYPED_REPLY_S', 0.3), patch.object(chat_module, 'TYPED_PLAY_S', 0.2):
            chat = Scripted([self.call('play_recording', id=self.ids[0])], '好呀，晴天。')
            started = time.monotonic()
            self.a.ask(chat, '弹一下晴天', typed=True)
        self.assertEqual(events, ['user', 'busy True', 'assistant', 'busy False', 'plays', 'piece', 'busy False'])
        self.assertGreaterEqual(time.monotonic() - started, 0.5, 'A moment to write it, then a little after')
        events.clear()
        chat = Scripted([self.call('play_recording', id=self.ids[1])], '好。')
        with patch.object(self.p, 'select', side_effect=plays):
            self.a.ask(chat, '弹搁浅')  # said, not typed: it plays as she speaks
        self.assertLess(events.index('plays'), events.index('assistant'))

    def test_typed_to_her_piece_still_plays_when_her_reply_fails(self):
        class Failing(Scripted):
            def send_tool_results(self, results):
                raise OSError('the model did not answer')
        with self.assertRaises(OSError):
            self.a.ask(Failing([self.call('play_recording', id=self.ids[0])]), '弹一下晴天', typed=True)
        self.assertEqual(self.p.current_id, self.ids[0], 'As it would have, said rather than typed')
        self.assertIsNone(self.a.pending)

    def test_typed_to_her_piece_gives_way_to_a_tap_on_the_phone(self):
        def busy(on):
            if not on:
                self.a.taps += 1  # the phone's player, used while her reply was coming
        self.a.on_busy = busy
        self.a.ask(Scripted([self.call('play_recording', id=self.ids[0])], '好呀，晴天。'), '弹一下晴天', typed=True)
        self.assertIsNone(self.p.current_id, 'The tap wins, as over a spoken request')

    def test_what_she_gets_ready_gives_way_to_the_phone_used_meanwhile(self):
        # She finds and transcribes a piece; meanwhile another is chosen on the phone. When hers is ready, the
        # user's choice stands: it is not started, and she is told why, to say it is ready.
        found = self.a.search_library
        def search_library(query):
            result = found(query)
            self.a.taps += 1  # the phone's player, used while she was getting it
            return result
        results = []
        class Told(Scripted):
            def send_tool_results(self, given):
                results.extend(json.loads(raw) for _, raw in given)
                return super().send_tool_results(given)
        with patch.object(self.a, 'search_library', search_library):  # its own signature: run_tool keeps the query
            self.a.ask(Told([self.call('search_library', query='晴天')], [self.call('play_recording', id=self.ids[0])],
                            '准备好了，想听时告诉我。'), '弹一下晴天')
        self.assertIsNone(self.p.current_id, 'What the phone plays stays')
        self.assertEqual(results[-1]['status'], 'not_started')
        self.a.ask(Scripted([self.call('search_library', query='晴天')], [self.call('play_recording', id=self.ids[0])],
                            '好呀。'), '现在弹晴天吧')
        self.assertEqual(self.p.current_id, self.ids[0], 'Asked again, it plays')

    def test_giving_way_she_says_it_is_ready_unless_it_is_that_very_piece(self):
        # Not started: her line, when she has no words of her own, says it is ready. The very piece chosen on the
        # phone meanwhile: it plays, and she says so.
        self.a.selectable_ids.add(self.ids[0])
        self.a.taps_asked, self.a.taps = 0, 1
        self.assertTrue(self.a.phone_used())
        result = self.a.play_recording(self.ids[0])
        self.assertEqual(result['status'], 'not_started')
        self.assertIn('准备好啦', self.a.playback_reply(result))
        self.p.select(self.ids[0], self.ids, None)  # the phone's player: this very piece
        self.assertEqual(self.a.play_recording(self.ids[0])['how'], 'now_playing')

    def test_a_request_that_waited_its_turn_gives_way_to_the_phone_used_after_it_was_sent(self):
        sent = self.a.taps
        self.a.taps += 1  # the phone's player, used while it waited behind another request
        self.a.ask(Scripted([self.call('search_library', query='晴天')], [self.call('play_recording', id=self.ids[0])],
                            '好呀。'), '弹一下晴天', typed=True, taps=sent)
        self.assertIsNone(self.p.current_id)

    def test_a_piece_she_chooses_plays_alone_the_chat_is_not_a_play_queue(self):
        for rec, name in zip(self.ids, ('晴天', '搁浅')):
            chat = Scripted([self.call('search_library', query=name)], [self.call('play_recording', id=rec)], '好。')
            self.a.ask(chat, f'弹{name}')
        self.assertEqual((self.p.ids, self.p.current_id), ([self.ids[1]], self.ids[1]))
        self.assertEqual(self.p.scope, {'kind': 'conversation', 'id': self.a.conversation_id})
        said = self.a.ask(Scripted(), '下一首')
        self.assertIn(said, LINES['nothing_next']['zh'])
        self.assertEqual((self.p.current_id, self.p.state), (self.ids[1], 'playing'), 'Nothing changes')
        self.assertIn(self.a.ask(Scripted(), '上一首'), LINES['restart']['zh'], 'Only this one: from its start')

    def test_her_words_before_a_lookup_are_said_at_once_and_once_but_not_before_an_action(self):
        class Ahead(Scripted):  # a streamed model: the words go to before_tool as it starts on its first tool
            def __init__(self, words, *steps):
                super().__init__(*steps)
                self.words = words

            def send_user(self, text):
                reply = super().send_user(text)
                if reply.tool_calls:
                    self.before_tool(self.words, reply.tool_calls[0].name)
                    return SimpleNamespace(text=self.words, tool_calls=reply.tool_calls)
                return reply
        said = []
        speak = lambda text, meanwhile=False: said.append((text, meanwhile))
        self.a.ask(Ahead('我找找晴天～', [self.call('search_library', query='晴天')], '没找到呢。'), '弹一下晴天', speak=speak)
        self.assertEqual(said, [('我找找晴天～', True)], 'At once, while she looks; not again')
        said.clear()
        answer = self.a.ask(Ahead('我找找晴天～', [self.call('search_library', query='晴天')],
                                  [self.call('play_recording', id=self.ids[0])], '找到啦。'), '弹一下晴天', speak=speak)
        self.assertEqual((said, answer), ([('我找找晴天～', True), ('', False)], ''),
                         'It named the piece: no opening line after it, only her words heard to the end first')
        self.assertEqual(self.p.current_id, self.ids[0])
        said.clear()
        self.a.ask(Ahead('我找找肖邦的曲子～', [self.call('search_library', query='Chopin')],
                         [self.call('play_recording', id=self.ids[2])], '好。'), '弹一首肖邦', speak=speak)
        self.assertEqual(said[0], ('我找找肖邦的曲子～', True))
        self.assertIn('Nocturne', said[1][0], 'Her words did not name the piece she chose: her line does')
        said.clear()
        with patch.object(chat_module, 'MAX_TOOL_ROUNDS', 1):  # she gives up, still looking
            answer = self.a.ask(Ahead('我再找找～', [self.call('library_info', about='overview')],
                                      [self.call('library_info', about='overview')]), '我都有什么曲子', speak=speak)
        self.assertEqual((len(said), answer), (2, ''), 'Said with each of her two looks; not again as her answer')
        said.clear()
        self.a.ask(Ahead('好，这就弹～', [self.call('play_recording', id=self.ids[0])], '好。'), '弹晴天', speak=speak)
        self.assertNotIn(('好，这就弹～', True), said, 'Words that come with an action: her line follows it')
        self.assertTrue(said and '晴天' in said[0][0])
        said.clear()
        plan = '《晴天》先弹给你，再给你找一首夜曲～'
        answer = self.a.ask(Ahead(plan, [self.call('search_library', query='Nocturne'),
                                         self.call('play_recording', id=self.ids[0]),
                                         self.call('play_recording', id=self.ids[2])], '找到啦，晴天之后就是夜曲。'),
                            '弹晴天，再来一首夜曲', speak=speak)
        self.assertEqual((said, answer), ([(plan, True), ('', False)], ''),
                         'Her sentence about both, said as she looks, introduces the first: nothing after it')
        self.assertEqual(self.p.ids, [self.ids[0], self.ids[2]])

    def test_the_piece_she_found_starts_once_her_words_as_she_looked_have_been_heard(self):
        events, saying = [], []

        def speak(text, meanwhile=False):  # as the voice conversation says lines: one at a time, meanwhile or not
            while saying:
                saying.pop().join()
            if text:
                line = threading.Thread(target=lambda: (time.sleep(0.2), events.append(('heard', text))))
                line.start()
                (saying.append if meanwhile else lambda t: t.join())(line)

        class Ahead(Scripted):
            def send_user(self, text):
                reply = super().send_user(text)
                if reply.tool_calls and reply.tool_calls[0].name == 'search_library':
                    self.before_tool('我找找晴天～', 'search_library')
                    return SimpleNamespace(text='我找找晴天～', tool_calls=reply.tool_calls)
                return reply
        select = self.p.select
        with patch.object(self.p, 'select', side_effect=lambda *a, **k: (events.append(('plays',)), select(*a, **k))):
            self.a.ask(Ahead([self.call('search_library', query='晴天')], [self.call('play_recording', id=self.ids[0])],
                             '好。'), '弹一下晴天', speak=speak)
        self.assertEqual(events, [('heard', '我找找晴天～'), ('plays',)], 'Not under her words')

    def test_her_next_in_shuffle_follows_the_shuffled_order_and_in_repeat_one_as_the_button(self):
        self.lib.new_playlist('睡前', self.ids)
        self.a.ask(Scripted([SimpleNamespace(name='play_playlist', arguments={'name': '睡前', 'shuffle': True})],
                            '好。'), '随机放我的睡前歌单')
        order = list(self.p.ids)
        self.assertEqual(self.p.current_id, order[0])
        for following in order[1:]:
            self.a.ask(Scripted(), '下一首')
            self.assertEqual(self.p.current_id, following)
        self.assertIn(self.a.ask(Scripted(), '下一首'), LINES['from_first']['zh'])
        self.assertEqual(self.p.current_id, order[0], 'The shuffled order, from its first again')
        self.p.set_mode('one')  # (back to the playlist's order)
        ids = list(self.p.ids)
        self.p.go_to(ids[0])
        self.a.ask(Scripted(), '下一首')
        self.assertEqual(self.p.current_id, ids[1], 'Repeat one: on to the next, as the button (Apple Music)')
        self.p.go_to(ids[-1])
        self.assertIn(self.a.ask(Scripted(), '下一首'), LINES['restart']['zh'], 'At the last: this one again')
        self.assertEqual(self.p.current_id, ids[-1])

    def test_her_next_is_said_before_the_piece_changes_and_typed_it_changes_after_her_reply(self):
        self.lib.new_playlist('睡前', self.ids)
        self.a.ask(Scripted([SimpleNamespace(name='play_playlist', arguments={'name': '睡前'})], '好。'), '放我的睡前歌单')
        events = []
        go_to = self.p.go_to
        with patch.object(self.p, 'go_to', side_effect=lambda rec: (events.append('plays'), go_to(rec))):
            self.a.ask(Scripted(), '下一首', speak=lambda text, meanwhile=False: events.append(text))
            self.assertEqual(len(events), 2)
            self.assertIn(events[0], LINES['next']['zh'], 'Said, then played, as a new piece is')
            self.assertEqual(events[1], 'plays')
            events.clear()
            self.a.on_message = lambda m: events.append(m['role'])
            self.a.ask(Scripted(), '下一首', typed=True)
        self.assertLess(events.index('assistant'), events.index('plays'), 'Typed: after her reply')
        self.assertEqual(self.p.current_id, self.ids[2])

    def test_next_and_previous_move_in_a_playlist_and_past_its_end_it_starts_again(self):
        self.lib.new_playlist('睡前', self.ids)
        self.a.ask(Scripted([SimpleNamespace(name='play_playlist', arguments={'name': '睡前'})], '好。'), '放我的睡前歌单')
        self.assertEqual(self.p.current_id, self.ids[0])
        self.assertIn(self.a.ask(Scripted(), '下一首'), LINES['next']['zh'])
        self.assertEqual(self.p.current_id, self.ids[1])
        self.assertIn(self.a.ask(Scripted(), '上一首吧'), LINES['previous']['zh'])
        self.assertEqual(self.p.current_id, self.ids[0])
        self.p.step(1), self.p.step(1)
        self.assertEqual(self.p.current_id, self.ids[2])
        self.assertIn(self.a.ask(Scripted(), '小茗同学，下一首'), LINES['from_first']['zh'])
        self.assertEqual((self.p.current_id, self.p.state), (self.ids[0], 'playing'), 'Not ended, as ⏭ would')
        self.p.step(1), self.p.step(1), self.p.step(1)
        self.assertEqual((self.p.state, self.p.current_id), ('stopped', self.ids[0]), 'The phone\'s ⏭ ends the list')
        moved = json.loads(self.a.run_tool('control_playback', {'action': 'next'}))  # the model's way to it
        self.assertEqual((moved['how'], self.p.current_id), ('next', self.ids[1]))

    def test_a_quiet_half_hour_starts_a_new_conversation(self):
        now = [1000.0]
        self.a.clock = lambda: now[0]
        chat = Scripted()
        self.a.ask(chat, '你好')
        first = self.a.conversation_id
        now[0] += QUIET_S + 1
        self.a.ask(chat, '我回来了')
        self.assertNotEqual(self.a.conversation_id, first)
        self.assertEqual(chat.resets, 1)

    def test_an_earlier_conversation_is_picked_up_as_it_was(self):
        chat = Scripted()
        self.a.ask(chat, '你好')
        first = self.a.conversation_id
        self.a.new_conversation(chat)
        self.a.ask(chat, '新的话题')
        self.assertTrue(self.a.pick_up(chat, first))
        self.assertEqual(self.a.conversation_id, first)
        self.assertEqual(chat.loaded, [('user', '你好'), ('assistant', '好。')])
        self.a.ask(chat, '接着聊')
        self.assertEqual(Conversations(self.lib).messages(first)[-2]['text'], '接着聊')

    def test_a_recent_conversation_is_remembered_after_a_restart(self):
        chat = Scripted()
        self.a.ask(chat, '你好')
        again = Assistant(self.lib, self.p)
        again.restore(chat)
        self.assertEqual(again.conversation_id, self.a.conversation_id)

    def test_typed_requests_say_so(self):
        chat = Mock()
        chat.send_user.return_value = SimpleNamespace(text='好。', tool_calls=[])
        self.a.ask(chat, '你好', typed=True)
        self.assertIn('"typed": true', chat.send_user.call_args.args[0])

    def test_she_is_told_the_language_to_answer_in_as_her_own_lines_take_it(self):
        chat = Mock()
        chat.send_user.return_value = SimpleNamespace(text='好。', tool_calls=[])
        for text, language in (('弹一首 Viva la Vida', '"answer_in": "Chinese"'),
                               ('Play Viva la Vida by Coldplay', '"answer_in": "the language of the request, which is not Chinese"')):
            self.a.ask(chat, text)
            self.assertIn(language, chat.send_user.call_args.args[0])

    def test_a_request_in_english_that_names_a_chinese_piece_or_playlist_is_in_english(self):
        self.lib.new_playlist('雨天', self.ids[:1])
        chat = Mock()
        chat.send_user.return_value = SimpleNamespace(text='OK.', tool_calls=[])
        for text, chinese in (('play 晴天', False), ('Play my playlist 雨天', False), ('Hey Jasmine, play 周杰伦', False),
                              ('弹 Nocturne', True), ('晴天', True)):
            self.a.ask(chat, text)
            self.assertEqual(self.a.chinese(), chinese, text)
            self.assertEqual('"answer_in": "Chinese"' in chat.send_user.call_args.args[0], chinese, text)

    def test_the_language_comes_back_with_its_conversation(self):
        chat = Scripted()
        self.a.ask(chat, 'Play something calm, please')
        english = self.a.conversation_id
        again = Assistant(self.lib, self.p)  # the service started again
        self.assertTrue(again.chinese(), 'Nothing asked yet')
        again.restore(chat)
        self.assertFalse(again.chinese(), 'As the conversation it picked up')
        again.new_conversation(chat)
        again.ask(chat, '弹一首轻松的')
        chinese = again.conversation_id
        again.pick_up(chat, english)
        self.assertFalse(again.chinese())
        self.assertIn(again.playback_reply({'error': 'playback_connection_unavailable'}), LINES['piano_missing']['en'])
        again.pick_up(chat, chinese)
        self.assertTrue(again.chinese())

    def test_results_in_other_languages_say_which_to_answer_in(self):
        self.a.request = 'play 晴天'
        english = '"answer_in": "the language of the request, which is not Chinese"'
        with patch('pianasoul.online.search', return_value=[]):
            for name, arguments in (('search_library', {'query': '晴天'}), ('library_info', {'about': 'overview'}),
                                    ('search_online', {'query': '晴天 piano'})):
                self.assertIn(english, self.a.run_tool(name, arguments), name)
        self.assertNotIn('answer_in', self.a.run_tool('library_info', {'about': 'artist', 'name': 'Liszt'}))
        self.a.selectable_ids.add(self.ids[0])
        self.a.taps_asked, self.a.taps = 0, 1  # the phone's player, used while she got it
        self.assertIn('Answer in the language of the request', self.a.play_recording(self.ids[0])['instruction'])

    def test_her_line_about_the_piano_is_made_once(self):
        # The piano gone while playing: Core sends her line, and voice says it; tapped again and again on the phone,
        # the same. Not again while hers is the last thing there, and the same line, whichever of hers it was.
        said = [self.a.piano_news({'error': 'playback_connection_lost'}) for _ in range(3)]
        self.assertEqual(len(set(said)), 1)
        self.assertEqual([(m['role'], m['text']) for m in self.kept], [('assistant', said[0])])
        self.a.keep('user', '好了吗')
        self.a.piano_news({'error': 'playback_connection_lost'})
        self.assertEqual(len(self.kept), 3, 'After something else, again')
        self.a.piano_news({'error': 'playback_connection_unavailable'})
        self.assertIn(self.kept[-1]['text'], LINES['piano_missing']['zh'], 'Other news is told')

    def test_a_model_failure_is_said_and_shown_unless_what_was_asked_plays(self):
        chat = Mock()
        chat.send_user.side_effect = TimeoutError('model down')
        with self.assertRaises(TimeoutError):
            self.a.ask(chat, '弹晴天')
        said = self.a.could_not_answer()
        self.assertIn(said, LINES['no_answer']['zh'])
        self.assertEqual(self.kept[-1]['text'], said, 'In the conversation, for the phone')

        class Failing(Scripted):
            def send_tool_results(self, results):
                raise OSError('the model did not answer')
        with self.assertRaises(OSError):
            self.a.ask(Failing([self.call('play_recording', id=self.ids[0])]), '弹一下晴天', typed=True)
        self.assertEqual(self.a.could_not_answer(), '', 'It plays: nothing to excuse')

    def test_her_lines_for_what_went_wrong_say_what_did(self):
        self.assertIn(self.a.playback_reply({'error': 'recording_not_found'}), LINES['not_in_library']['zh'])
        self.assertIn('曲库', LINES['not_in_library']['zh'][0], 'A piece gone from the library, not one misheard')
        for text in [*LINES['ready']['zh'], *LINES['playlist_ready']['zh']]:
            self.assertIn('没弹', text, 'Why what she said would play did not')
        for text in [*LINES['ready']['en'], *LINES['playlist_ready']['en']]:
            self.assertIn("didn't want to interrupt", text)

    def test_her_instructions_ask_her_to_wait_once_speak_as_herself_and_give_both_languages(self):
        from pianasoul import voice
        from pianasoul.chat import INSTRUCTIONS, instructions
        told = instructions(True) + voice.VOICE_NOTE
        self.assertIn('the only sentence of yours that does', told)
        self.assertIn('Only the sentence before search_online asks the listener to wait', voice.VOICE_NOTE)
        self.assertNotIn('takes a moment', voice.VOICE_NOTE)
        for text in LINES['searching']['zh']:
            self.assertNotIn(text.split('，')[-1], told, "Her line before looking online does not end as the model's")
        for text in LINES['searching']['en']:
            self.assertNotIn(text.split('. ')[-1], told)
        for given in (instructions(True), instructions(False), told):  # ./run.sh chat, online or not; voice, the app
            self.assertEqual(given.count('Your names are 小茗同学 in Chinese and Jasmine in English'), 1)
            self.assertEqual(given.count('never in the third person'), 1)
        self.assertIn('without Markdown or emoji', INSTRUCTIONS)
        for english in ('"play this\nchat\'s songs again from the start"', '"save these songs as a playlist"',
                        'Rename it anytime."'):
            self.assertIn(english, INSTRUCTIONS)
        self.assertIn('as "在线搜索与转谱" when you answer in Chinese and "Online Search & Transcription" otherwise',
                      ' '.join(instructions(False).split()), 'The setting she names, as the Mac names it')

    def test_spoken_a_piece_that_begins_to_play_is_said_with_her_line_and_pause_and_cancel_in_her_words(self):
        """As VOICE_NOTE tells her: through the model, resume, retry, restart, next and previous are introduced by
        her line before the piece sounds, and her words after them are not said; pause and cancel are said in hers."""
        from pianasoul.voice import VOICE_NOTE
        note = ' '.join(VOICE_NOTE.split())
        self.assertIn("resume, retry, restart, next, previous) is introduced by the application's own line", note)
        self.assertIn('pause and cancel, you confirm yourself', note)
        self.lib.new_playlist('睡前', self.ids)
        self.a.ask(Scripted([SimpleNamespace(name='play_playlist', arguments={'name': '睡前'})], '好。'), '放我的睡前歌单')
        introduced = {'resume': ['resume', 'playing'], 'retry': ['resume', 'playing'], 'restart': ['restart'],
                      'next': ['next'], 'previous': ['previous']}  # (no notes go out: resumed at 0 s, as begun)
        for action in ('pause', 'resume', 'pause', 'retry', 'restart', 'next', 'previous', 'cancel'):
            with self.subTest(action=action):
                said = []
                answer = self.a.ask(Scripted([self.call('control_playback', action=action)], '嗯，好的。'),
                                    '麻烦你帮我操作一下', speak=lambda text, meanwhile=False: said.append(text))
                if action in introduced:
                    piece = chat_module.spoken_title(self.p.current[1])
                    self.assertEqual(answer, '')
                    self.assertEqual(len(said), 1)
                    self.assertIn(said[0], [t for key in introduced[action] for t in every(key, True, piece)])
                else:
                    self.assertEqual((said, answer), ([], '嗯，好的。'))

    def test_a_short_answer_is_an_answer_typed_or_in_the_terminal(self):
        for typed in (True, False):
            self.kept.clear()
            self.a.ask(Scripted(), '好的', typed=typed)
            self.assertEqual([(m['role'], m['text']) for m in self.kept], [('user', '好的'), ('assistant', '好。')])

    def test_a_model_failure_forgets_that_request_only(self):
        chat = Scripted()
        self.a.ask(chat, '你好')
        first = self.a.conversation_id
        chat = Mock()
        chat.send_user.side_effect = TimeoutError('model down')
        with self.assertRaises(TimeoutError):
            self.a.ask(chat, '弹晴天')
        chat.discard_turn.assert_called_once()
        chat.reset.assert_not_called()
        self.assertEqual(self.a.conversation_id, first)

    def test_busy_while_she_answers(self):
        busy = []
        self.a.on_busy = busy.append
        self.a.ask(Scripted(), '你好')
        self.assertEqual(busy, [True, False])

    def test_the_phone_is_not_kept_waiting_while_she_speaks(self):
        waited = []

        def speak(line, meanwhile=False):  # meanwhile the phone asks what plays
            asking = threading.Thread(target=self.p.snapshot)
            asking.start()
            asking.join(0.5)
            waited.append(asking.is_alive())
        self.a.speak, self.a.request = speak, '弹晴天'
        self.a.selectable_ids = {self.ids[0]}
        self.assertEqual(self.a.play_recording(self.ids[0])['status'], 'playing')
        self.assertEqual(waited, [False])

    def test_her_ears_are_given_the_people_s_names_first_then_the_pieces(self):
        from pianasoul import voice
        words = voice.hints(self.lib)
        self.assertEqual(set(words[:4]), {'周杰伦', 'MappleZS', 'Chopin', 'Lang Lang'})
        self.assertEqual(set(words[4:]), {'晴天', '搁浅', 'Nocturne'})
        self.assertEqual(voice.hints(self.lib, limit=2), words[:2])

    def test_she_names_a_new_piece_as_the_app_does(self):
        made = Job(1, 'https://www.youtube.com/watch?v=abcdefghijk', status='done', result=self.lib.get(self.ids[0]))
        made.finished.set()
        self.a.downloads = Mock(add=Mock(return_value=made))
        self.a.request = '我想听小幸运'
        done = json.loads(self.a.run_tool('transcribe_url', {
            'url': 'https://youtu.be/abcdefghijk', 'piece': '小幸运', 'artist': '田馥甄',
            'performer': "Melody's Piano Garden", 'aliases': 'A Little Happiness', 'title': 'ignored'}))
        self.a.downloads.add.assert_called_once_with('https://youtu.be/abcdefghijk', query='我想听小幸运', piece='小幸运',
                                                     artist='田馥甄', performer="Melody's Piano Garden",
                                                     aliases='A Little Happiness', tags='', versions=[])
        self.assertEqual((done['status'], done['id']), ('done', self.ids[0]), 'Made before: there at once, to play')
        brief = json.loads(self.a.run_tool('search_library', {'query': '晴天'}))['results'][0]
        self.assertEqual((brief['piece'], brief['artist'], brief['performer']), ('晴天', '周杰伦', 'MappleZS'))
        self.assertNotIn('title', brief)

    def test_she_hands_over_at_most_two_other_versions_and_only_links(self):
        made = Job(1, 'https://www.youtube.com/watch?v=abcdefghijk', status='done', result=self.lib.get(self.ids[0]))
        made.finished.set()
        self.a.downloads = Mock(add=Mock(return_value=made))
        self.a.run_tool('transcribe_url', {'url': 'https://youtu.be/abcdefghijk', 'piece': '晴天', 'alternatives': [
            {'url': 'https://youtu.be/bbbbbbbbbbb', 'performer': ' Kassia '}, 'https://youtu.be/ccccccccccc',
            {'url': 'not a link', 'performer': 'C'}, {'url': 'https://youtu.be/ddddddddddd'},
            {'url': 'https://youtu.be/eeeeeeeeeee', 'performer': 'E'}]})
        self.assertEqual(self.a.downloads.add.call_args.kwargs['versions'],
                         [{'url': 'https://youtu.be/bbbbbbbbbbb', 'performer': 'Kassia'},
                          {'url': 'https://youtu.be/ddddddddddd', 'performer': ''}])

    def test_she_changes_one_name_and_the_title_follows(self):
        rec = json.loads(self.a.run_tool('update_recording', {'id': self.ids[0], 'performer': 'Fei Wang', 'tags': 'pop'}))
        self.assertEqual((rec['piece'], rec['artist'], rec['performer'], rec['tags']), ('晴天', '周杰伦', 'Fei Wang', 'pop'))
        stored = self.lib.get(self.ids[0])
        self.assertEqual((stored['title'], stored['composer']), ('周杰伦 - 晴天 (Fei Wang)', '周杰伦'))


class Queue:
    """The downloads queue as she sees it: a piece is queued once (asked for again, the same one), and is made, or
    not, when the test says."""
    def __init__(self):
        self.jobs = {}

    def add(self, source, **names):
        if source not in self.jobs:
            self.jobs[source] = Job(len(self.jobs) + 1, source, piece=names.get('piece', ''))
        return self.jobs[source]


class PreparedTests(Base):
    """A piece she finds online is prepared in the background, and she is free meanwhile. Once it is there it plays
    if nothing has changed since it was asked for; if something has, she says it is ready."""
    def setUp(self):
        super().setUp()
        patch.object(Playback, 'play').start()
        self.addCleanup(patch.stopall)
        self.fresh()

    def fresh(self):
        """The piano idle, and she with nothing prepared."""
        self.p = Playback(self.lib, port=Mock())
        self.addCleanup(self.p.close)
        patch.object(self.p, 'check_connection').start()
        self.a = Assistant(self.lib, self.p)
        self.a.downloads = self.queue = Queue()
        self.kept = []
        self.a.on_message = self.kept.append
        self.addCleanup(self.settled, self.a)

    def settled(self, a):
        """What she does with the pieces made is done (before the library closes)."""
        self.eventually(lambda: all(not job.finished.is_set() for job, _ in a.preparing.values()))
        with a._turn:
            pass

    def call(self, name, **arguments):
        return SimpleNamespace(name=name, arguments=arguments)

    def prepare(self, *pieces, text='弹一首新的'):
        """One request in which she sends these pieces (named as their links are) to be prepared."""
        calls = [self.call('transcribe_url', url=f'https://youtu.be/{piece}', piece=piece) for piece in pieces]
        self.a.ask(Scripted(calls, '我去准备，好了就弹给你～'), text)

    def made(self, piece, rec_id=None, status='done'):
        job = self.queue.jobs[f'https://youtu.be/{piece}']
        job.status, job.result = status, self.lib.get(rec_id) if rec_id else None
        job.finished.set()

    def eventually(self, check) -> bool:
        deadline = time.monotonic() + 5
        while not check() and time.monotonic() < deadline:
            time.sleep(0.01)
        return check()

    def said_last(self, words) -> bool:
        return self.eventually(lambda: self.kept and words in self.kept[-1]['text'])

    def joined(self, title) -> bool:
        """It joined the list that plays: she says so, and its card follows."""
        return self.eventually(lambda: len(self.kept) > 1 and self.kept[-1]['text'] == title
                               and '已经排上了' in self.kept[-2]['text'])

    def play_and_prepare(self):
        """One request: 搁浅 from the library, played now, and 晴天 sent to be prepared."""
        self.a.ask(Scripted([self.call('search_library', query='搁浅')],
                            [self.call('play_recording', id=self.ids[1]),
                             self.call('transcribe_url', url='https://youtu.be/qingtian', piece='qingtian')], '好。'),
                   '弹搁浅，再帮我找晴天')

    def test_it_plays_once_ready_if_nothing_changed_meanwhile(self):
        self.prepare('qingtian')
        self.assertIsNone(self.p.current_id, 'Not there yet: she answered at once')
        self.a.ask(Scripted('是呀，天气真好。'), '今天天气不错')  # talking with her changes nothing
        self.made('qingtian', self.ids[0])
        self.assertTrue(self.eventually(lambda: self.kept[-1]['role'] == 'piece'))
        self.assertEqual(self.p.current_id, self.ids[0])
        said, piece = self.kept[-2:]
        self.assertEqual(said['role'], 'assistant')
        self.assertIn('晴天', said['text'])
        self.assertEqual(piece['text'], '周杰伦 - 晴天 (MappleZS)')

    def test_once_something_else_was_chosen_it_is_only_said_to_be_ready_and_remembered(self):
        changes = {
            'another piece': lambda: self.a.ask(Scripted([self.call('search_library', query='搁浅')],
                                                         [self.call('play_recording', id=self.ids[1])], '好。'), '先弹搁浅'),
            'a stop': lambda: self.a.ask(Scripted([self.call('control_playback', action='cancel')], '好。'), '算了不要了'),
            'the phone': lambda: setattr(self.a, 'taps', self.a.taps + 1),
            'another to prepare': lambda: self.prepare('banyuemiao', text='还是弹半月谣吧'),
        }
        for change, make in changes.items():
            with self.subTest(change=change):
                self.fresh()
                self.prepare('qingtian')
                make()
                self.made('qingtian', self.ids[0])
                self.assertTrue(self.said_last('准备好啦'))
                self.assertNotEqual(self.p.current_id, self.ids[0], 'What was chosen since stands')
                if change == 'another to prepare':
                    self.made('banyuemiao', self.ids[2])
                    self.assertTrue(self.eventually(lambda: self.p.current_id == self.ids[2]), 'The latest asked for plays')
                self.a.ask(Scripted([self.call('play_recording', id=self.ids[0])], '好呀。'), '弹刚才准备好的那首')
                self.assertEqual(self.p.current_id, self.ids[0], 'She remembers it, to play when asked')

    def test_of_pieces_asked_for_together_the_first_ready_plays_and_the_others_follow_it(self):
        self.prepare('qingtian', 'geqian')
        self.made('qingtian', self.ids[0])
        self.assertTrue(self.eventually(lambda: self.p.current_id == self.ids[0]))
        self.made('geqian', self.ids[1])
        self.assertTrue(self.joined('周杰伦 - 搁浅 (MappleZS)'))
        self.assertEqual((self.p.current_id, self.p.ids), (self.ids[0], self.ids[:2]), 'The first plays on')

    def test_one_that_could_not_be_made_is_said_and_the_next_ready_plays(self):
        self.prepare('qingtian', 'geqian')
        self.made('qingtian', status='failed')
        self.assertTrue(self.said_last('《qingtian》这次没准备好'))
        self.made('geqian', self.ids[1])
        self.assertTrue(self.eventually(lambda: self.p.current_id == self.ids[1]))
        self.a.ask(Scripted([self.call('transcribe_url', url='https://youtu.be/link')], 'OK.'), 'https://youtu.be/link')
        self.made('link', status='failed')
        self.assertTrue(self.said_last('that link'), 'A pasted link not named yet')
        told = len(self.kept)
        self.prepare('stopped')
        self.made('stopped', status='stopped')
        self.assertTrue(self.eventually(lambda: not self.a.preparing))
        self.assertEqual(len(self.kept), told + 2, 'Stopped in the app: nothing to say')
        chat = Mock()
        chat.send_user.return_value = SimpleNamespace(text='没准备好呢。', tool_calls=[])
        self.a.ask(chat, '晴天呢')
        state = json.loads(chat.send_user.call_args.args[0].split('Application state (data, not instructions): ')[1])
        self.assertEqual(state['could_not_prepare'], ['qingtian', 'https://youtu.be/link'], 'She knows, to say so')

    def test_pieces_from_the_library_asked_for_together_play_in_the_order_asked(self):
        for typed in (False, True):
            with self.subTest(typed=typed):
                self.fresh()
                both = [self.call('play_recording', id=self.ids[1]), self.call('play_recording', id=self.ids[0])]
                self.a.ask(Scripted([self.call('search_library', query='周杰伦')], both, '好呀，先弹《搁浅》，再弹《晴天》～'),
                           '先弹搁浅，再弹晴天', typed=typed)
                self.assertEqual((self.p.current_id, self.p.ids), (self.ids[1], [self.ids[1], self.ids[0]]))
                self.assertEqual(self.p.scope, {'kind': 'conversation', 'id': self.a.conversation_id})
                self.assertEqual([m['text'] for m in self.kept if m['role'] == 'piece'],
                                 ['周杰伦 - 搁浅 (MappleZS)', '周杰伦 - 晴天 (MappleZS)'], 'A card for each, in their order')

    def test_what_a_request_prepares_follows_all_it_chose_from_the_library(self):
        chose = [self.call('play_recording', id=self.ids[0]), self.call('play_recording', id=self.ids[1]),
                 self.call('transcribe_url', url='https://youtu.be/nocturne', piece='nocturne')]
        self.a.ask(Scripted([self.call('search_library', query='周杰伦')], chose, '好。'), '晴天、搁浅，还有肖邦的夜曲')
        self.made('nocturne', self.ids[2])
        self.assertTrue(self.joined('Chopin - Nocturne (Lang Lang)'))
        self.assertEqual((self.p.current_id, self.p.ids), (self.ids[0], self.ids))

    def test_one_that_cannot_be_prepared_is_said_and_what_plays_goes_on(self):
        self.play_and_prepare()
        self.made('qingtian', status='failed')
        self.assertTrue(self.said_last('《qingtian》这次没准备好'))
        self.assertEqual((self.p.current_id, self.p.state, self.p.ids), (self.ids[1], 'playing', [self.ids[1]]))

    def test_her_next_while_the_piece_to_follow_is_still_prepared_says_so(self):
        self.play_and_prepare()
        self.assertIn(self.a.ask(Scripted(), '下一首'), every('coming', True, 'qingtian'))
        self.assertEqual(self.p.current_id, self.ids[1], 'Nothing changes')
        self.made('qingtian', self.ids[0])
        self.assertTrue(self.joined('周杰伦 - 晴天 (MappleZS)'))
        self.a.ask(Scripted(), '下一首')
        self.assertEqual(self.p.current_id, self.ids[0])

    def test_paused_meanwhile_its_request_s_list_still_takes_it(self):
        pauses = {'by her': lambda: self.a.ask(Scripted(), '暂停'),
                  'on the phone': lambda: (setattr(self.a, 'taps', self.a.taps + 1), self.p.pause())}
        for pause, make in pauses.items():
            with self.subTest(pause=pause):
                self.fresh()
                self.play_and_prepare()
                make()
                self.made('qingtian', self.ids[0])
                self.assertTrue(self.joined('周杰伦 - 晴天 (MappleZS)'))
                self.assertEqual((self.p.state, self.p.ids), ('paused', [self.ids[1], self.ids[0]]))

    def test_ready_once_its_request_s_list_has_played_to_its_end_it_plays_at_its_end(self):
        self.play_and_prepare()
        self.a.taps += 1  # the phone's player used meanwhile, within that list
        self.p._end_list()
        self.made('qingtian', self.ids[0])
        self.assertTrue(self.eventually(lambda: self.kept[-1]['text'] == '周杰伦 - 晴天 (MappleZS)'))
        self.assertEqual((self.p.current_id, self.p.state, self.p.ids), (self.ids[0], 'playing', [self.ids[1], self.ids[0]]))
        self.assertIn('晴天', self.kept[-2]['text'])

    def test_once_another_piece_was_chosen_it_is_only_said_to_be_ready(self):
        library = [self.ids[2], self.ids[1]]
        changes = {
            'on the phone': (lambda: (setattr(self.a, 'taps', self.a.taps + 1),
                                      self.p.select(self.ids[2], library, {'kind': 'library'})), library),
            'by her': (lambda: self.a.ask(Scripted([self.call('search_library', query='Nocturne')],
                                                   [self.call('play_recording', id=self.ids[2])], '好。'), '弹夜曲'),
                       [self.ids[2]]),
            'cancelled': (lambda: self.a.ask(Scripted(), '取消'), [self.ids[1]]),
        }
        for change, (make, ids) in changes.items():
            with self.subTest(change=change):
                self.fresh()
                self.play_and_prepare()
                make()
                self.made('qingtian', self.ids[0])
                self.assertTrue(self.said_last('准备好啦，怕打断你'))
                self.assertEqual(self.p.ids, ids, 'What was chosen since stands')

    def test_what_a_request_prepares_follows_the_piece_it_plays(self):
        play = [self.call('search_library', query='搁浅')], [self.call('play_recording', id=self.ids[1])]
        prepare = [self.call('transcribe_url', url='https://youtu.be/qingtian', piece='qingtian')]
        go_on = [self.call('control_playback', action='resume')]
        for order in ('play first', 'prepare first', 'go on with the paused one'):
            with self.subTest(order=order):
                self.fresh()
                if order == 'go on with the paused one':
                    self.p.select(self.ids[1], [self.ids[2], self.ids[1], self.ids[0]], {'kind': 'library'})
                    self.p.pause()
                steps = {'play first': [*play, prepare], 'prepare first': [prepare, *play],
                         'go on with the paused one': [go_on, prepare]}[order]
                self.a.ask(Scripted(*steps, '好。'), '弹搁浅，再帮我找晴天')
                self.made('qingtian', self.ids[0])
                self.assertTrue(self.joined('周杰伦 - 晴天 (MappleZS)'))
                self.assertEqual(self.p.current_id, self.ids[1], 'The piece it played plays on')
                self.assertEqual(self.p.ids[self.p.ids.index(self.ids[1]) + 1], self.ids[0], 'The other comes next')

    def test_stopped_in_the_same_request_it_still_plays_once_ready(self):
        self.a.ask(Scripted([self.call('transcribe_url', url='https://youtu.be/daoxiang', piece='daoxiang')],
                            [self.call('control_playback', action='cancel')], '好。'), '帮我找稻香，现在这首先停了吧')
        self.made('daoxiang', self.ids[2])
        self.assertTrue(self.eventually(lambda: self.p.current_id == self.ids[2]))

    def test_one_made_while_she_was_busy_is_hers_to_play_and_then_needs_no_note(self):
        asked = []
        class Seen(Scripted):
            def send_user(self, text):
                asked.append(text)
                return super().send_user(text)
        self.prepare('qingtian')
        self.a.may_play.clear()  # voice: she is listening
        self.made('qingtian', self.ids[0])
        self.a.ask(Seen([self.call('play_recording', id=self.ids[0])], '好呀。'), '好了吗，好了就弹')
        state = json.loads(asked[0].split('Application state (data, not instructions): ')[1])
        self.assertNotIn('preparing', state, 'It is made')
        self.assertEqual([p['id'] for p in state['prepared']], [self.ids[0]])
        self.assertEqual(self.p.current_id, self.ids[0])
        self.a.may_play.set()
        self.assertTrue(self.eventually(lambda: not self.a.preparing))
        time.sleep(0.1)
        self.assertEqual(self.kept[-1]['role'], 'piece')
        self.assertFalse(any('准备好啦' in m['text'] for m in self.kept), 'It plays already: nothing to say')

    def test_one_that_failed_while_she_was_busy_is_known_to_have_failed(self):
        self.prepare('qingtian')
        self.a.may_play.clear()  # voice: she is listening
        self.made('qingtian', status='failed')
        chat = Mock()
        chat.send_user.return_value = SimpleNamespace(text='这次没准备好。', tool_calls=[])
        self.a.ask(chat, '晴天好了吗')
        state = json.loads(chat.send_user.call_args.args[0].split('Application state (data, not instructions): ')[1])
        self.assertNotIn('preparing', state)
        self.assertEqual(state['could_not_prepare'], ['qingtian'])
        self.a.may_play.set()

    def test_a_start_that_fails_is_said(self):
        self.prepare('qingtian')
        with patch.object(self.p, 'select', side_effect=OSError('CoreMIDI did not open an output')):
            self.made('qingtian', self.ids[0])
            self.assertTrue(self.said_last('没弹起来'))
        self.assertIn(self.ids[0], self.a.prepared, 'It can still be asked for')

    def test_asked_for_again_while_it_is_prepared_it_plays_once(self):
        self.prepare('qingtian')
        self.prepare('qingtian', text='晴天好了吗')
        self.assertEqual(len(self.a.preparing), 1)
        self.made('qingtian', self.ids[0])
        self.assertTrue(self.eventually(lambda: self.kept[-1]['role'] == 'piece'))
        time.sleep(0.1)
        self.assertEqual([m['role'] for m in self.kept].count('piece'), 1)
        self.assertNotIn('准备好啦', self.kept[-2]['text'])

    def test_while_voice_listens_it_waits(self):
        self.prepare('qingtian')
        self.a.may_play.clear()  # voice listens to a request (hold_piece)
        self.made('qingtian', self.ids[0])
        time.sleep(0.2)
        self.assertIsNone(self.p.current_id)
        self.a.may_play.set()  # she has answered (finish_playback)
        self.assertTrue(self.eventually(lambda: self.p.current_id == self.ids[0]))

    def test_she_is_told_what_she_is_preparing_and_what_she_prepared(self):
        chat = Mock()
        chat.send_user.return_value = SimpleNamespace(text='还在准备呢。', tool_calls=[])
        state = lambda: json.loads(chat.send_user.call_args.args[0].split('Application state (data, not instructions): ')[1])
        self.prepare('qingtian')
        self.a.ask(chat, '好了吗')
        self.assertEqual(state()['preparing'], ['qingtian'])
        self.made('qingtian', self.ids[0])
        self.assertTrue(self.eventually(lambda: not self.a.preparing))
        self.a.ask(chat, '刚才那首是谁弹的')
        self.assertNotIn('preparing', state())
        self.assertEqual([(p['id'], p['piece']) for p in state()['prepared']], [(self.ids[0], '晴天')])


class DownloadTests(Base):
    def downloads(self, **kw):
        return Downloads(self.lib, **kw)

    def test_pieces_are_made_one_at_a_time_and_filed_with_their_video(self):
        made = []

        def convert(audio, out, options, output_name, stopped):
            made.append((output_name, threading.current_thread().name))
            midi = make_midi(Path(out) / f'{output_name}.mid')
            return {'files': {'midi': str(midi)}, 'transcribe_seconds': 1.0, 'audio_duration_s': 2.0}

        audio = self.root / 'a.m4a'
        audio.write_bytes(b'x')
        details = {'video_title': '稻香 钢琴版', 'channel': 'MappleZS', 'views': 520000, 'published': '2020-01-02',
                   'thumbnail': 'https://i.ytimg.com/x.jpg'}
        with patch('pianasoul.download.download_audio', return_value=(audio, details)), \
             patch('pianasoul.pipeline.convert_apart', side_effect=convert), \
             patch('pianasoul.downloads._video_page', return_value={'channel_avatar': 'https://yt3/a.jpg'}):
            d = self.downloads(name_piece=lambda query, video, artists: {'piece': '稻香', 'artist': '周杰伦', 'performer': 'MappleZS',
                                                                'aliases': 'Rice Field'})
            rec = piece_made(d, 'https://youtu.be/abcdefghijk', query='稻香')
        self.assertEqual((rec['title'], rec['composer'], rec['performer'], rec['aliases']),
                         ('周杰伦 - 稻香 (MappleZS)', '周杰伦', 'MappleZS', 'Rice Field'))
        self.assertEqual((rec['channel'], rec['views'], rec['channel_avatar']), ('MappleZS', 520000, 'https://yt3/a.jpg'))
        self.assertEqual(rec['source_url'], 'https://www.youtube.com/watch?v=abcdefghijk')
        self.assertEqual(d.snapshot(), [])
        again = d.add('https://www.youtube.com/watch?v=abcdefghijk')
        self.assertEqual(again.status, 'done', 'Not made twice')

    def test_a_bilibili_piece_from_a_short_link_is_its_video_with_the_up_s_picture_its_search_found(self):
        from pianasoul import online
        audio = self.root / 'a.m4a'
        audio.write_bytes(b'x')
        convert = lambda audio, out, options, output_name, stopped: {'files': {'midi': str(make_midi(Path(out) / 'x.mid'))}}
        video = 'https://www.bilibili.com/video/BV1We4y1g7sj'
        with patch.object(online, 'resolved', return_value='https://m.bilibili.com/video/BV1We4y1g7sj/?share_source=x'), \
             patch.dict(online._avatars, {video: 'https://i1.hdslb.com/bfs/face/up.jpg'}), \
             patch('pianasoul.download.download_audio', return_value=(audio, {'video_title': '晴天 钢琴', 'channel': 'UP'})) as got, \
             patch('pianasoul.pipeline.convert_apart', side_effect=convert):
            rec = piece_made(self.downloads(), 'https://b23.tv/AbCdEfG')
        self.assertEqual(got.call_args.args[0], video)
        self.assertEqual((rec['source_url'], rec['channel'], rec['channel_avatar']),
                         (video, 'UP', 'https://i1.hdslb.com/bfs/face/up.jpg'))

    def test_a_recording_of_yours_is_named_from_its_file(self):
        audio = self.root / '想见你想见你想见你 钢琴.mp3'
        audio.write_bytes(b'x')
        seen = []
        convert = lambda audio, out, options, output_name, stopped: {'files': {'midi': str(make_midi(Path(out) / 'x.mid'))}}
        with patch('pianasoul.pipeline.convert_apart', side_effect=convert):
            d = self.downloads(name_piece=lambda query, video, artists: seen.append(video) or {'piece': '想见你', 'artist': '八三夭'})
            rec = piece_made(d, str(audio))
        self.assertEqual(seen[0]['file_name'], '想见你想见你想见你 钢琴', 'The model has its name to go on')
        self.assertEqual(rec['title'], '八三夭 - 想见你')

    def test_the_model_is_asked_with_what_there_is(self):
        from pianasoul import core
        with patch('pianasoul.llm.complete_json', return_value={'piece': 'Nocturne'}) as asked:
            core.name_piece('', {'file_name': 'Classicals.de - Chopin - Nocturne Op. 9, No. 2'})
        prompt = asked.call_args.args[0]
        self.assertIn('"file_name": "Classicals.de - Chopin - Nocturne Op. 9, No. 2"', prompt)
        self.assertNotIn('video_title', prompt, 'Nothing empty')

    def test_names_given_stay_and_the_rest_are_found(self):
        audio = self.root / 'a.m4a'
        audio.write_bytes(b'x')
        convert = lambda audio, out, options, output_name, stopped: {'files': {'midi': str(make_midi(Path(out) / 'x.mid'))}}
        with patch('pianasoul.download.download_audio', return_value=(audio, {'video_title': '小幸運 鋼琴版'})), \
             patch('pianasoul.pipeline.convert_apart', side_effect=convert), \
             patch('pianasoul.downloads._video_page', return_value={}):
            d = self.downloads(name_piece=lambda query, video, artists: {'piece': '小幸运', 'artist': 'JerryC', 'performer': 'Melody'})
            rec = piece_made(d, 'https://youtu.be/abcdefghijk', artist='田馥甄', query='我想听小幸运')
        self.assertEqual((rec['title'], rec['composer'], rec['performer']), ('田馥甄 - 小幸运 (Melody)', '田馥甄', 'Melody'))

    def test_a_piece_is_named_and_its_video_read_while_it_is_transcribed(self):
        audio = self.root / 'a.m4a'
        audio.write_bytes(b'x')
        transcribing, named = threading.Event(), threading.Event()

        def convert(audio, out, options, output_name, stopped):  # it waits for the naming, which waits for it
            transcribing.set()
            self.assertTrue(named.wait(2), 'Named meanwhile, not first')
            return {'files': {'midi': str(make_midi(Path(out) / f'{output_name}.mid'))}}

        def name_piece(query, video, artists):
            self.assertTrue(transcribing.wait(2))
            named.set()
            return {'piece': '稻香', 'artist': '周杰伦', 'performer': 'MappleZS'}
        with patch('pianasoul.download.download_audio', return_value=(audio, {'video_title': '稻香 钢琴'})), \
             patch('pianasoul.pipeline.convert_apart', side_effect=convert), \
             patch('pianasoul.downloads._video_page', return_value={'channel_avatar': 'https://yt3/a.jpg'}):
            d = self.downloads(name_piece=name_piece)
            seen = []
            d.listeners.append(lambda: seen.extend((j['status'], j['title']) for j in d.snapshot()))
            rec = piece_made(d, 'https://youtu.be/abcdefghijk', query='稻香')
        self.assertEqual((rec['title'], rec['channel_avatar']), ('周杰伦 - 稻香 (MappleZS)', 'https://yt3/a.jpg'))
        self.assertIn(('converting', '周杰伦 - 稻香 (MappleZS)'), seen, 'The phone is told its name as it is named')

    def test_a_piece_stopped_while_it_is_named_is_not_filed(self):
        audio = self.root / 'a.m4a'
        audio.write_bytes(b'x')
        jobs = []

        def name_piece(query, video, artists):  # the stop comes while the model names it, as it is transcribed
            d.stop(jobs[0].id)
            return {'piece': '稻香', 'artist': '周杰伦', 'performer': 'MappleZS'}
        with patch('pianasoul.download.download_audio', return_value=(audio, {})), \
             patch('pianasoul.pipeline.convert_apart'), \
             patch('pianasoul.downloads._video_page', return_value={}):
            d = self.downloads(name_piece=name_piece)
            jobs.append(d.add('https://youtu.be/abcdefghijk', query='稻香'))
            jobs[0].finished.wait(2)
        self.assertEqual(jobs[0].status, 'stopped')
        self.assertEqual(self.lib.count(), 3)

    def versions(self, *failing):
        """Downloads whose videos with these ids cannot be had (one too long, say); the others are made. Returns
        them, and the videos asked for, in order."""
        audio = self.root / 'a.m4a'
        audio.write_bytes(b'x')
        asked = []

        def download(url, folder, progress):
            asked.append(url[-11:])
            if url[-11:] in failing:
                raise ValueError('the video is longer than 20 minutes')
            return audio, {'video_title': url[-11:]}
        convert = lambda audio, out, options, output_name, stopped: {'files': {'midi': str(make_midi(Path(out) / 'x.mid'))}}
        for made in (patch('pianasoul.download.download_audio', side_effect=download),
                     patch('pianasoul.pipeline.convert_apart', side_effect=convert),
                     patch('pianasoul.downloads._video_page', return_value={})):
            made.start()
            self.addCleanup(made.stop)
        return self.downloads(), asked

    def test_a_piece_that_cannot_be_made_is_made_from_its_next_version_in_its_place_in_the_queue(self):
        d, asked = self.versions('aaaaaaaaaaa', 'bbbbbbbbbbb')
        versions = [{'url': 'https://youtu.be/bbbbbbbbbbb', 'performer': 'B'},
                    {'url': 'https://youtu.be/ccccccccccc', 'performer': 'Kassia'},
                    {'url': 'https://youtu.be/ddddddddddd', 'performer': 'D'}]
        seen = []
        d.listeners.append(lambda: seen.extend(j['title'] for j in d.snapshot()))
        job = d.add('https://youtu.be/aaaaaaaaaaa', piece='卡农', artist='Pachelbel', performer='A', versions=versions)
        after = d.add('https://youtu.be/eeeeeeeeeee', piece='晴天')
        job.finished.wait(5)
        after.finished.wait(5)
        self.assertEqual((job.status, job.result['title'], job.result['source_url']),
                         ('done', 'Pachelbel - 卡农 (Kassia)', 'https://www.youtube.com/watch?v=ccccccccccc'))
        self.assertEqual(asked, ['aaaaaaaaaaa', 'bbbbbbbbbbb', 'ccccccccccc', 'eeeeeeeeeee'], 'Before the next piece')
        self.assertIn('Pachelbel - 卡农 (Kassia)', seen, 'The phone shows the version being made')

    def test_a_piece_fails_only_once_none_of_its_versions_could_be_made(self):
        d, asked = self.versions('aaaaaaaaaaa', 'bbbbbbbbbbb')
        job = d.add('https://youtu.be/aaaaaaaaaaa', piece='卡农',
                    versions=[{'url': 'https://youtu.be/aaaaaaaaaaa', 'performer': ''},
                              {'url': 'https://youtu.be/bbbbbbbbbbb', 'performer': ''}])
        job.finished.wait(5)
        self.assertEqual((job.status, job.result), ('failed', None))
        self.assertEqual(asked, ['aaaaaaaaaaa', 'bbbbbbbbbbb'], 'The version it was is not tried again')
        self.assertIn('longer than 20 minutes', job.error)

    def test_a_version_in_the_library_already_is_the_piece_at_once(self):
        self.lib.execute("UPDATE recordings SET source_type = 'url', source_url = ? WHERE id = ?",
                         ('https://www.youtube.com/watch?v=bbbbbbbbbbb', self.ids[0]))
        d, asked = self.versions('aaaaaaaaaaa')
        job = d.add('https://youtu.be/aaaaaaaaaaa', piece='晴天',
                    versions=[{'url': 'https://youtu.be/bbbbbbbbbbb', 'performer': 'MappleZS'}])
        job.finished.wait(5)
        self.assertEqual((job.status, job.result['id'], asked), ('done', self.ids[0], ['aaaaaaaaaaa']))

    def test_a_piece_stopped_tries_no_other_version(self):
        gate = threading.Event()
        def download(url, folder, progress):
            gate.wait(2)
            raise ValueError('stopped meanwhile')
        with patch('pianasoul.download.download_audio', side_effect=download) as downloaded:
            d = self.downloads()
            job = d.add('https://youtu.be/aaaaaaaaaaa', versions=[{'url': 'https://youtu.be/bbbbbbbbbbb', 'performer': ''}])
            time.sleep(0.1)
            d.stop(job.id)
            gate.set()
            job.finished.wait(2)
        self.assertEqual((job.status, downloaded.call_count), ('stopped', 1))

    def test_a_stopped_piece_leaves_nothing(self):
        gate = threading.Event()
        def download(url, folder, progress):
            gate.wait(2)
            progress(0.5)  # the next step sees the stop
            raise AssertionError('not reached')
        with patch('pianasoul.download.download_audio', side_effect=download):
            d = self.downloads()
            first = d.add('https://www.youtube.com/watch?v=aaaaaaaaaaa')
            second = d.add('https://www.youtube.com/watch?v=bbbbbbbbbbb')
            d.stop(second.id)
            self.assertEqual(second.status, 'stopped', 'A queued piece leaves at once')
            time.sleep(0.1)
            d.stop(first.id)
            self.assertEqual(d.snapshot(), [])
            gate.set()
            first.finished.wait(2)
        self.assertEqual(first.status, 'stopped')
        self.assertEqual(self.lib.count(), 3)


class CoreTests(Base):
    def core(self):
        from pianasoul.core import Core
        core = Core(lib=self.lib, chat=(Scripted(), 'test'))
        self.addCleanup(core.close)
        return core

    def test_what_played_when_the_service_stopped_comes_back_once_it_is_up(self):
        p = Playback(self.lib, port=Mock())
        with patch.object(Playback, 'play'), patch.object(p, 'check_connection'):
            p.select(self.ids[1], self.ids)
        p.close()
        core = self.core()
        self.assertEqual((core.playback.current_id, core.playback.state), (self.ids[1], 'paused'))

    def test_she_is_made_with_online_search_only_when_it_is_on(self):
        """Her alone, Online Search & Transcription unticked in the Mac app (PIANASOUL_TRANSCRIBE=0, the advanced extra
        installed): the chat model is given her library-only instructions and tools, so she cannot search or
        transcribe at all, and the page hears transcription is off. Ticked, she has all of it."""
        from pianasoul.chat import ONLINE_TOOLS, TOOLS, instructions
        from pianasoul.core import Core
        for switch, online in (('0', False), (None, True)):
            with self.subTest(online=online), patch('pianasoul.core.installed', return_value=True), \
                    patch.dict(os.environ), patch('pianasoul.llm.make_chat', return_value=(Scripted(), 'test')) as made:
                os.environ.pop('PIANASOUL_TRANSCRIBE', None)
                if switch:
                    os.environ['PIANASOUL_TRANSCRIBE'] = switch
                lib = Library(self.root / f'{online}.db', self.root / f'Music {online}')  # each its own folder
                self.addCleanup(lib.close)
                core = Core(lib=lib)
                self.addCleanup(core.close)
                self.assertEqual(core.features(), {'transcribe': online, 'assistant': True})
                told, given = made.call_args.args
                self.assertEqual(told, instructions(online))
                self.assertEqual(given, core.assistant.tools)
                self.assertEqual(bool({t['name'] for t in given} & ONLINE_TOOLS), online)
                self.assertEqual(given == TOOLS, online)
                self.assertEqual('search_online' in told, online)
                self.assertEqual('Online Search & Transcription' in told, not online)

    def test_one_core_for_chat_voice_and_the_app(self):
        core = self.core()
        events = []
        core.listeners.append(events.append)
        self.assertIs(core.assistant.player, core.playback)
        self.assertIs(core.assistant.downloads, core.downloads)
        names = {r['id']: (r['artist'], r['piece'], r['performer']) for r in core.recordings()}
        self.assertEqual(names[self.ids[0]], ('周杰伦', '晴天', 'MappleZS'))
        done = threading.Event()
        core.listeners.append(lambda e: e['type'] == 'conversations' and done.set())
        core.type('你好')
        self.assertTrue(done.wait(2))
        self.assertEqual([e['message']['role'] for e in events if e['type'] == 'message'], ['user', 'assistant'])
        self.assertEqual(core.settings(), {'language': '', 'appearance': 'dark', 'search_source': 'youtube',
                                           'output': None},
                         "The phone's language, dark and YouTube, until others are chosen")
        self.assertEqual(core.set_settings(language='en', appearance='nonsense', search_source='vimeo'),
                         {'language': 'en', 'appearance': 'dark', 'search_source': 'youtube', 'output': None})
        self.assertEqual(core.set_settings(language='system')['language'], 'en', 'Only Chinese or English')

    def test_typed_to_when_the_model_fails_she_says_so_on_the_phone(self):
        class Failing(Scripted):
            def send_user(self, text):
                raise TimeoutError('model down')
        from pianasoul.core import Core
        core = Core(lib=self.lib, chat=(Failing(), 'test'))
        self.addCleanup(core.close)
        events, done = [], threading.Event()
        core.listeners.append(lambda e: (events.append(e), e['type'] == 'conversations' and done.set()))
        core.type('Play Clair de Lune')
        self.assertTrue(done.wait(2))
        said = [e['message'] for e in events if e['type'] == 'message']
        self.assertEqual([m['role'] for m in said], ['user', 'assistant'])
        self.assertIn(said[1]['text'], LINES['no_answer']['en'], 'Shown, in the language asked in')

    def test_with_her_pieces_get_their_other_names_at_the_start_and_as_they_come(self):
        def model(prompt):
            return {line.split(':')[0]: {'artist': '', 'aliases': '夜曲' if 'Nocturne' in line else 'x', 'tags': ''}
                    for line in prompt.splitlines() if line[:1].isdigit()}
        with patch('pianasoul.llm.complete_json', side_effect=model) as asked:
            core = self.core()
            for _ in range(50):
                if self.lib.get(self.ids[2])['named_at']:
                    break
                time.sleep(0.05)
            self.assertEqual(core.assistant.library_matches('弹一下夜曲')[0]['id'], self.ids[2])
            make_midi(self.root / 'Music' / 'Liszt - Liebestraum.mid')
            while core.folder.scan():  # until the new file has settled
                time.sleep(0.1)
            for _ in range(50):
                if all(r['named_at'] for r in self.lib.all()):
                    break
                time.sleep(0.05)
            self.assertEqual(len(self.lib.all()), 4)
            self.assertTrue(all(r['named_at'] for r in self.lib.all()), 'The new one too')
            self.assertEqual(asked.call_count, 2, 'Each piece once')

    def test_a_piano_that_goes_away_while_playing_is_told_in_the_conversation_once(self):
        core = self.core()
        found = lambda: setattr(core.playback, 'lost', None)  # as check_connection does: an earlier loss is old news
        with patch.object(Playback, 'play'), patch.object(core.playback, 'check_connection', side_effect=found):
            core.playback.port = Mock()
            core.playback.select(self.ids[0], self.ids)
            core.playback.state, core.playback.lost = 'paused', 'no MIDI output matching "pianasoul"'
            core.playback.after_run()
            core.playback.changed()  # anything else meanwhile: still told once
            for _ in range(2):  # it plays again and goes away again: while her line is the last thing, not again;
                core.playback.toggle()  # after something else is said, again
                core.playback.state, core.playback.lost = 'paused', 'no MIDI output matching "pianasoul"'
                core.playback.after_run()
                if not _:
                    core.assistant.keep('user', '好了吗')
        said = [m['text'] for m in core.conversations.messages(core.assistant.conversation_id)]
        self.assertEqual(said[1:2], ['好了吗'])
        self.assertEqual(len(said), 3)
        self.assertTrue(set(said[::2]) <= set(LINES['piano_lost']['zh']))

    def test_pieces_deleted_together_are_one_change(self):
        core = self.core()
        events = []
        core.listeners.append(lambda e: events.append(e['type']))
        core.delete_recordings(self.ids[:2])
        self.assertEqual([r['id'] for r in core.recordings()], self.ids[2:])
        self.assertEqual(events.count('library'), 1)

    def test_a_piece_played_from_the_phone_replaces_the_one_she_meant_to_retry(self):
        core = self.core()
        patch.object(Playback, 'play').start()
        patch.object(core.playback, 'check_connection').start()
        self.addCleanup(patch.stopall)
        core.assistant.pending = Mock()
        core.playback.select(self.ids[1], self.ids)
        self.assertIsNone(core.assistant.pending)

    def test_playing_a_piece_again_puts_it_back_in_recently_played(self):
        core = self.core()
        patch.object(Playback, 'play').start()
        patch.object(core.playback, 'check_connection').start()
        self.addCleanup(patch.stopall)
        events = []
        core.listeners.append(lambda e: events.append(e['type']))
        for _ in range(2):
            core.playback.select(self.ids[0], self.ids)
            core.playback.stop()
        self.assertEqual(events.count('library'), 2)

    def test_typing_in_a_conversation_continues_it_however_long_ago(self):
        core = self.core()
        now = [1000.0]
        core.assistant.clock = lambda: now[0]
        core.type('你好')
        core.worker.submit(lambda: None).result()
        first = core.assistant.conversation_id
        now[0] += QUIET_S + 1
        core.type('弹一下晴天', first)
        core.worker.submit(lambda: None).result()
        self.assertEqual(core.assistant.conversation_id, first)
        self.assertEqual(core.conversations.messages(first)[-2]['text'], '弹一下晴天')

    def test_a_deleted_piece_is_gone_from_her_conversations(self):
        core = self.core()
        cid = core.conversations.create()
        core.conversations.add(cid, 'piece', '周杰伦 - 晴天 (MappleZS)', self.ids[0])
        core.delete_recordings([self.ids[0]])  # its number may be given to a new piece
        self.assertIsNone(core.conversations.messages(cid)[0]['recording_id'])

    def test_the_running_version_comes_from_git(self):
        from pianasoul import core
        answers = {('remote', 'get-url', 'origin'): 'git@github.com:kaisenyao/PianaSoul.git\n',
                   ('rev-parse', 'HEAD'): '341568b002757ebfa63c702065222378ca6757f9\n'}
        with patch.object(core.subprocess, 'run',
                          side_effect=lambda args, **kw: SimpleNamespace(stdout=answers[tuple(args[1:])])):
            self.assertEqual(core.version(), {'commit': '341568b002757ebfa63c702065222378ca6757f9',
                                              'repo': 'https://github.com/kaisenyao/PianaSoul', 'app': ''})
        with patch.dict('os.environ', {'PIANASOUL_COMMIT': 'abc1234', 'PIANASOUL_APP_VERSION': '1.0'}):
            self.assertEqual(core.version(), {'commit': 'abc1234', 'repo': 'https://github.com/kaisenyao/PianaSoul',
                                              'app': '1.0'}, 'As the Mac app says: it has no git')

    def test_devices_say_what_plays_and_whether_it_is_there(self):
        from pianasoul.core import Core
        from pianasoul.play import Output
        core = Core(lib=self.lib, assistant=False)
        self.addCleanup(core.close)
        session = Output(1, 'Network PianaSoul', 'network', True, ('iPad',))
        piano = Output(2, 'Roland Digital Piano', 'usb', True, None)
        with patch('pianasoul.play.outputs', return_value=[session, piano]):
            self.assertEqual(core.devices(), [{'name': 'iPad', 'kind': 'network', 'connected': True}],
                             'Found by PIANASOUL_PORT\'s "pianasoul", until one is chosen')
            core.set_settings(output=2)
            self.assertEqual(core.devices(), [{'name': 'Roland Digital Piano', 'kind': 'usb', 'connected': True}])
        with patch('pianasoul.play.outputs', return_value=[session]), patch('pianasoul.play.output', return_value=None):
            self.assertEqual(core.devices(), [{'name': 'Roland Digital Piano', 'kind': 'other', 'connected': False}])


if __name__ == '__main__':
    unittest.main()
