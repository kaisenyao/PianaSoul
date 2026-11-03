"""What the app keeps on the Mac: the play list, her conversations, new pieces, and the core they share.
No paid APIs, models or MIDI devices."""
import contextlib
import io
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
from pianasoul import chat as chat_module
from pianasoul.chat import Assistant
from pianasoul.conversations import QUIET_S, Conversations
from pianasoul.downloads import Downloads
from pianasoul.library import Library
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
        self.assertEqual(c.pieces(cid), [self.ids[0]])
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

    def test_her_list_is_the_pieces_of_the_conversation(self):
        for rec, name in zip(self.ids, ('晴天', '搁浅')):
            chat = Scripted([self.call('search_library', query=name)], [self.call('play_recording', id=rec)], '好。')
            self.a.ask(chat, f'弹{name}')
        self.assertEqual(self.p.ids, self.ids[:2])
        self.assertEqual(self.p.current_id, self.ids[1])

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

    def test_she_names_a_new_piece_as_the_app_does(self):
        self.a.downloads = Mock()
        self.a.downloads.run.return_value = self.lib.get(self.ids[0])
        self.a.request = '我想听小幸运'
        self.a.run_tool('transcribe_url', {'url': 'https://youtu.be/abcdefghijk', 'piece': '小幸运', 'artist': '田馥甄',
                                           'performer': "Melody's Piano Garden", 'aliases': 'A Little Happiness',
                                           'title': 'ignored'})
        self.a.downloads.run.assert_called_once_with('https://youtu.be/abcdefghijk', query='我想听小幸运', piece='小幸运',
                                                     artist='田馥甄', performer="Melody's Piano Garden",
                                                     aliases='A Little Happiness', tags='')
        brief = json.loads(self.a.run_tool('search_library', {'query': '晴天'}))['results'][0]
        self.assertEqual((brief['piece'], brief['artist'], brief['performer']), ('晴天', '周杰伦', 'MappleZS'))
        self.assertNotIn('title', brief)

    def test_she_changes_one_name_and_the_title_follows(self):
        rec = json.loads(self.a.run_tool('update_recording', {'id': self.ids[0], 'performer': 'Fei Wang', 'tags': 'pop'}))
        self.assertEqual((rec['piece'], rec['artist'], rec['performer'], rec['tags']), ('晴天', '周杰伦', 'Fei Wang', 'pop'))
        stored = self.lib.get(self.ids[0])
        self.assertEqual((stored['title'], stored['composer']), ('周杰伦 - 晴天 (Fei Wang)', '周杰伦'))


class DownloadTests(Base):
    def downloads(self, **kw):
        engine = Mock()
        d = Downloads(self.lib, engine=engine, **kw)
        return d

    def test_pieces_are_made_one_at_a_time_and_filed_with_their_video(self):
        made = []

        def convert(engine, audio, out, options, output_name):
            made.append((output_name, threading.current_thread().name))
            midi = make_midi(Path(out) / f'{output_name}.mid')
            return {'files': {'midi': str(midi)}, 'transcribe_seconds': 1.0, 'audio_duration_s': 2.0}

        audio = self.root / 'a.m4a'
        audio.write_bytes(b'x')
        details = {'video_title': '稻香 钢琴版', 'channel': 'MappleZS', 'views': 520000, 'published': '2020-01-02',
                   'thumbnail': 'https://i.ytimg.com/x.jpg'}
        with patch('pianasoul.download.download_audio', return_value=(audio, details)), \
             patch('pianasoul.pipeline.convert_file', side_effect=convert), \
             patch('pianasoul.downloads._video_page', return_value={'channel_avatar': 'https://yt3/a.jpg'}):
            d = self.downloads(name_piece=lambda query, video, artists: {'piece': '稻香', 'artist': '周杰伦', 'performer': 'MappleZS',
                                                                'aliases': 'Rice Field'})
            rec = d.run('https://youtu.be/abcdefghijk', query='稻香')
        self.assertEqual((rec['title'], rec['composer'], rec['performer'], rec['aliases']),
                         ('周杰伦 - 稻香 (MappleZS)', '周杰伦', 'MappleZS', 'Rice Field'))
        self.assertEqual((rec['channel'], rec['views'], rec['channel_avatar']), ('MappleZS', 520000, 'https://yt3/a.jpg'))
        self.assertEqual(rec['source_url'], 'https://www.youtube.com/watch?v=abcdefghijk')
        self.assertEqual(d.snapshot(), [])
        again = d.add('https://www.youtube.com/watch?v=abcdefghijk')
        self.assertEqual(again.status, 'done', 'Not made twice')

    def test_a_recording_of_yours_is_named_from_its_file(self):
        audio = self.root / '想见你想见你想见你 钢琴.mp3'
        audio.write_bytes(b'x')
        seen = []
        convert = lambda engine, audio, out, options, output_name: {'files': {'midi': str(make_midi(Path(out) / 'x.mid'))}}
        with patch('pianasoul.pipeline.convert_file', side_effect=convert):
            d = self.downloads(name_piece=lambda query, video, artists: seen.append(video) or {'piece': '想见你', 'artist': '八三夭'})
            rec = d.run(str(audio))
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
        convert = lambda engine, audio, out, options, output_name: {'files': {'midi': str(make_midi(Path(out) / 'x.mid'))}}
        with patch('pianasoul.download.download_audio', return_value=(audio, {'video_title': '小幸運 鋼琴版'})), \
             patch('pianasoul.pipeline.convert_file', side_effect=convert), \
             patch('pianasoul.downloads._video_page', return_value={}):
            d = self.downloads(name_piece=lambda query, video, artists: {'piece': '小幸运', 'artist': 'JerryC', 'performer': 'Melody'})
            rec = d.run('https://youtu.be/abcdefghijk', artist='田馥甄', query='我想听小幸运')
        self.assertEqual((rec['title'], rec['composer'], rec['performer']), ('田馥甄 - 小幸运 (Melody)', '田馥甄', 'Melody'))

    def test_a_piece_stopped_while_it_is_named_is_not_made(self):
        audio = self.root / 'a.m4a'
        audio.write_bytes(b'x')
        jobs = []

        def name_piece(query, video, artists):  # the stop comes while the model names it
            d.stop(jobs[0].id)
            return {'piece': '稻香', 'artist': '周杰伦', 'performer': 'MappleZS'}
        with patch('pianasoul.download.download_audio', return_value=(audio, {})), \
             patch('pianasoul.pipeline.convert_file') as convert, \
             patch('pianasoul.downloads._video_page', return_value={}):
            d = self.downloads(name_piece=name_piece)
            jobs.append(d.add('https://youtu.be/abcdefghijk', query='稻香'))
            jobs[0].finished.wait(2)
        self.assertEqual(jobs[0].status, 'stopped')
        convert.assert_not_called()
        self.assertEqual(self.lib.count(), 3)

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
        self.assertEqual(core.settings(), {'language': '', 'appearance': 'dark', 'output': None},
                         "The phone's language and dark, until others are chosen")
        self.assertEqual(core.set_settings(language='en', appearance='nonsense'),
                         {'language': 'en', 'appearance': 'dark', 'output': None})
        self.assertEqual(core.set_settings(language='system')['language'], 'en', 'Only Chinese or English')

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
        line = core.assistant.playback_reply({'error': 'playback_connection_lost'})
        said = core.conversations.messages(core.assistant.conversation_id)
        self.assertEqual([m['text'] for m in said], [line, '好了吗', line])

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
        self.assertEqual(core.conversations.pieces(cid), [])
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
