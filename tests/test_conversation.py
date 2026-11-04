"""Her ears and voice, on the Mac and on the phone; no paid APIs or audio devices."""
import base64
from pathlib import Path
import queue
import tempfile
from types import SimpleNamespace
import threading
import time
import unittest
from unittest.mock import Mock, patch
import urllib.error

import numpy as np

import places  # noqa: F401  (first: the library, data and cache folders are throwaway ones)
from pianasoul import voice
from pianasoul.lines import LINES


class Out:
    """Records what she does, like the phone; a reply ends the turn when the test says so."""

    def __init__(self):
        self.events, self.spoken = [], []

    def status(self, state):
        self.events.append(('state', state))

    def speak(self, pieces, hushed):
        self.spoken.append([np.asarray(p) for p in pieces])
        self.events.append(('audio',))

    def turn_end(self, conversation, sleep):
        self.events.append(('turn_end', sleep))


class FakeLive:
    """Her ears as the request is said (voice.Live): what is sent to be heard, and the news the test gives."""

    def __init__(self):
        self.sent, self.queued, self.closed = [], [], False

    def send(self, pcm):
        self.sent.append(pcm)

    def close(self):
        self.closed = True

    def news(self):
        news, self.queued = self.queued, []
        return news


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.out = Out()
        self.parts = SimpleNamespace(stt=Mock(return_value='play moonlight'), wake=Mock(), speech=Mock(return_value=False),
                                     speaker=Mock())
        self.parts.detectors = lambda: (self.parts.wake, self.parts.speech)
        self.parts.wake.heard.return_value = None
        self.parts.stt.listen.return_value = None  # heard whole, unless a test hears it as it is said (FakeLive)
        self.parts.speaker.stream.side_effect = lambda text: iter([np.zeros(240, dtype=np.int16)])
        self.assistant = Mock(player=None, touched_playback=False, taps=0)
        self.chat = Mock()
        self.c = voice.Conversation(self.assistant, self.chat, self.parts, self.out)
        self.pcm = np.full(voice.FRAME, 1200, dtype='<i2')
        quiet = patch('builtins.print')
        quiet.start()
        self.addCleanup(quiet.stop)

    def frames(self, n, speaking=None, name=None):
        if speaking is not None:
            self.parts.speech.return_value = speaking
        self.parts.wake.heard.return_value = name
        for _ in range(n):
            self.c.frame(self.pcm)

    def test_waiting_never_sends_ambient_audio_to_the_cloud(self):
        self.frames(30, speaking=True)
        self.parts.stt.assert_not_called()
        self.parts.stt.listen.assert_not_called()
        self.assertEqual(self.c.state, 'waiting')

    def test_name_and_request_in_one_breath_are_sent_together(self):
        self.frames(1)
        self.frames(1, speaking=True, name='小茗同学')
        self.assertEqual(self.c.state, 'listening')
        self.assertEqual(len(self.c.frames), 2, 'The pre-roll keeps the name')
        self.frames(8, speaking=True)
        self.assistant.ask.return_value = ''
        self.frames(15, speaking=False)
        self.assertEqual(self.assistant.ask.call_args.args[1], 'play moonlight')
        self.assertEqual(len(self.parts.stt.call_args.args[0]), (2 + 8 + 9) * voice.FRAME, 'All of it, her name too')
        self.parts.speaker.stream.assert_not_called()  # no greeting

    def test_heard_as_it_is_said_a_pause_does_not_end_the_request(self):
        live = self.parts.stt.listen.return_value = FakeLive()
        self.frames(1)
        self.frames(1, speaking=True, name='小茗同学')
        self.assertEqual(len(live.sent), 2, 'From the moments before her name')
        self.frames(5, speaking=True)
        self.frames(15, speaking=False)  # a pause longer than the fixed rule: OpenAI decides where it ends
        self.assertEqual(self.c.state, 'listening')
        self.assertEqual(len(live.sent), 22, 'Every frame')
        live.queued = [('speech', ''), ('text', '小茗同学，换一首李斯特的爱之梦。')]
        self.assistant.ask.return_value = ''
        self.frames(1)
        self.assistant.ask.assert_called_once_with(self.chat, '小茗同学，换一首李斯特的爱之梦。', speak=self.c.say)
        self.parts.stt.assert_not_called()
        self.assertTrue(live.closed)

    def test_her_name_alone_she_listens_on_and_what_follows_is_the_same_request(self):
        live = self.parts.stt.listen.return_value = FakeLive()
        self.frames(1, speaking=True, name='Hey Jasmine')
        live.queued = [('text', 'Hey Jasmine.')]
        self.frames(int(voice.GREET_AFTER_S / voice.FRAME_S) - 2, speaking=False)
        self.assertEqual(self.c.state, 'listening')
        self.parts.speaker.stream.assert_not_called()
        live.queued = [('speech', '')]
        self.frames(3, speaking=True)
        self.frames(10, speaking=False)
        self.assistant.ask.assert_not_called()  # while it is not all written out
        live.queued = [('text', 'Hey Jasmine. 弹一首德彪西的月光。')]
        self.assistant.ask.return_value = ''
        self.frames(1)
        self.assistant.ask.assert_called_once()
        self.assertEqual(self.assistant.ask.call_args.args[1], 'Hey Jasmine. 弹一首德彪西的月光。')
        self.parts.speaker.stream.assert_not_called()  # no greeting in between

    def test_a_broken_connection_hears_the_request_whole_once(self):
        live = self.parts.stt.listen.return_value = FakeLive()
        self.parts.stt.return_value = '小茗同学，弹晴天。'
        self.frames(1, speaking=True, name='小茗同学')
        self.frames(3, speaking=True)
        live.queued = [('broken', 'the connection closed')]
        self.frames(1, speaking=True)
        self.frames(12, speaking=False)
        self.assertTrue(live.closed)
        self.parts.stt.assert_called_once()
        self.assertEqual(len(self.parts.stt.call_args.args[0]), (1 + 3 + 1 + 9) * voice.FRAME, 'All of it')
        self.assistant.ask.assert_called_once()

    def test_no_words_back_after_you_stopped_it_is_heard_whole(self):
        live = self.parts.stt.listen.return_value = FakeLive()
        self.frames(1, speaking=True, name='小茗同学')
        self.frames(3, speaking=True)
        self.frames(int(voice.STALL_S / voice.FRAME_S) - 1, speaking=False)
        self.parts.stt.assert_not_called()
        self.frames(2, speaking=False)
        self.assertTrue(live.closed)
        self.parts.stt.assert_called_once()
        self.assistant.ask.assert_called_once()

    def test_a_wake_that_openai_hears_nothing_of_is_heard_whole_soon(self):
        live = self.parts.stt.listen.return_value = FakeLive()
        self.parts.stt.return_value = ''
        self.frames(1, speaking=False, name='小茗同学')  # her name heard as the detector went quiet: noise, say
        self.frames(int(voice.STALL_S / voice.FRAME_S) + 1, speaking=False)
        self.assertTrue(live.closed)
        self.parts.stt.assert_called_once()

    def test_heard_whole_the_phone_is_told_she_is_busy_meanwhile_and_listening_again_after_her_name(self):
        states = []
        self.parts.stt.side_effect = lambda audio, lib: (states.append(self.c.state), '小茗同学。')[1]
        self.frames(1, speaking=True, name='小茗同学')
        self.frames(16, speaking=False)  # heard whole at 1.2 s; not yet quiet for her greeting
        self.assertEqual(states, ['thinking'], 'So the phone sends nothing while her thread uploads')
        self.assertEqual(self.c.state, 'listening', 'Her name alone: she listens on')

    def test_the_longest_request_is_heard_whole_at_its_end(self):
        live = self.parts.stt.listen.return_value = FakeLive()
        self.frames(1, speaking=True, name='小茗同学')
        self.frames(int(voice.MAX_REQUEST_S / voice.FRAME_S) + 1, speaking=True)
        self.assertTrue(live.closed)
        self.parts.stt.assert_called_once()
        self.assistant.ask.assert_called_once()

    def test_quiet_while_she_listens_closes_her_ears(self):
        live = self.parts.stt.listen.return_value = FakeLive()
        self.frames(1, speaking=True, name='小茗同学')
        self.c.hushed.set()
        self.c.hush()
        self.assertTrue(live.closed)
        self.assertEqual(self.c.state, 'waiting')

    def test_her_name_then_quiet_she_greets_once_she_has_waited_then_listens_for_the_request(self):
        self.parts.stt.return_value = '小茗同学。'
        self.frames(1, speaking=True, name='小茗同学')  # the name, still sounding
        self.frames(int(voice.GREET_AFTER_S / voice.FRAME_S), speaking=False)  # then quiet
        self.parts.speaker.stream.assert_not_called()  # not at once: the request may follow
        self.frames(1, speaking=False)
        self.assertIn(self.parts.speaker.stream.call_args.args[0], LINES['greet']['zh'])
        self.assertEqual(len(self.out.spoken), 1)
        self.assertEqual(self.out.events[-1], ('turn_end', False))
        self.assistant.ask.assert_not_called()
        self.c.resume()  # the greeting has been played: listen for the request without the name
        self.assertEqual(self.c.state, 'conversation')
        self.assertAlmostEqual(self.c.dialogue_until - time.monotonic(), voice.START_TIMEOUT_S, delta=.1)

    def test_she_greets_in_the_language_of_the_name_used(self):
        self.parts.stt.return_value = 'Hey Jasmine.'
        self.frames(1, name='Hey Jasmine')
        self.frames(25)
        self.assertIn(self.parts.speaker.stream.call_args.args[0], LINES['greet']['en'])
        self.assertEqual(self.out.events[-1], ('turn_end', False))
        self.assistant.keep.assert_not_called()  # a greeting says nothing, so it is not kept

    def test_a_request_goes_to_the_shared_assistant_and_her_answer_is_spoken(self):
        self.assistant.ask.return_value = 'Hello'
        self.c.respond('play moonlight')
        self.assistant.ask.assert_called_once_with(self.chat, 'play moonlight', speak=self.c.say)
        self.parts.speaker.stream.assert_called_once_with('Hello')
        self.assertEqual(self.out.events[-1], ('turn_end', False))
        self.c.frame(self.pcm)
        self.assertEqual(self.c.state, 'speaking', 'Nothing is heard until her answer has been played')
        self.c.resume()
        self.frames(1, speaking=True)
        self.assertEqual(self.c.state, 'listening', 'After a reply, no name is needed for a while')

    def test_a_piece_she_prepared_starts_only_while_she_waits_for_her_name(self):
        # Not while she listens on without her name after her reply either: its sound would be taken for speech.
        self.assistant.may_play = may_play = threading.Event()
        may_play.set()
        self.frames(1, speaking=True, name='小明同学')
        self.assertFalse(may_play.is_set(), 'Her name heard')
        self.assistant.ask.return_value = 'Hello'
        self.c.respond('play moonlight')
        self.c.resume()  # her reply heard: she listens on
        self.assertEqual(self.c.state, 'conversation')
        self.assertFalse(may_play.is_set())
        self.c.sleep()
        self.assertTrue(may_play.is_set(), 'She waits for her name again')
        self.c.piano_unavailable()  # a remark of hers, and the phone has yet to say it was heard
        self.assertFalse(may_play.is_set())

    def test_nothing_to_answer_stays_silent_and_waits_for_her_name(self):
        for heard in ('', '嗯。'):
            self.out.events.clear()
            self.parts.stt.return_value = heard
            self.c.resume()  # listening on after a reply: no name
            self.frames(3, speaking=True)
            self.frames(25, speaking=False)
            self.assistant.ask.assert_not_called()
            self.assertEqual(self.c.state, 'waiting')
            self.assertFalse(any(e[0] in {'audio', 'turn_end'} for e in self.out.events))

    def test_while_the_piano_plays_only_her_name_is_listened_for(self):
        self.assistant.ask.return_value = '好啦，这就给你弹《传奇》。'
        self.assistant.player = Mock(state='playing', completed=None, lost=None)
        self.c.respond('弹传奇')
        self.assertEqual(self.out.events[-1], ('turn_end', True), 'No name-free follow-up')
        self.c.sleep()
        self.frames(30, speaking=True)  # the piano, taken for speech
        self.assertEqual(self.c.state, 'waiting')
        self.parts.stt.assert_not_called()

    def test_followup_ends_after_its_window(self):
        self.c.resume()
        self.c.dialogue_until = time.monotonic() - 1
        self.frames(1, speaking=True)
        self.assertEqual(self.c.state, 'waiting')
        self.parts.stt.assert_not_called()

    def test_turned_on_the_first_request_needs_no_name(self):
        # Tapping the orb on is as good as her name. After that request, as after any reply, she listens on for a
        # while, then waits for her name; coming back to the orb does not count as turning her on.
        self.c.hushed.clear()
        self.c.turn_on()
        self.assertEqual(self.c.state, 'conversation')
        self.assertAlmostEqual(self.c.dialogue_until - time.monotonic(), voice.START_TIMEOUT_S, delta=.5,
                               msg='As long as after her greeting')
        self.frames(1, speaking=True)
        self.assertEqual(self.c.state, 'listening', 'No name needed')
        self.parts.wake.heard.assert_not_called()
        self.c.sleep()
        self.c.unhush()
        self.frames(30, speaking=True)
        self.assertEqual(self.c.state, 'waiting', 'Back on the orb, her name again')

    def test_turned_on_her_name_alone_gets_her_greeting(self):
        # Tapped on (or listening on after a reply), only her name said: she greets, in its language, and listens
        # for the request, as when her name is said while she waits for it.
        for heard, language in (('小明同学。', 'zh'), ('Jasmine?', 'en')):
            self.c.hushed.clear()
            self.c.turn_on()
            self.parts.stt.return_value = heard
            self.frames(2, speaking=True)
            self.frames(25, speaking=False)
            self.assistant.ask.assert_not_called()
            self.assertIn(self.parts.speaker.stream.call_args.args[0], LINES['greet'][language])
            self.assertEqual(self.out.events[-1], ('turn_end', False), 'Then she listens for the request')
            self.c.sleep()

    def test_turned_on_and_not_spoken_to_she_waits_for_her_name(self):
        self.c.hushed.clear()
        self.c.turn_on()
        self.c.dialogue_until = time.monotonic() - 1
        self.frames(1, speaking=True)
        self.assertEqual(self.c.state, 'waiting')
        self.parts.stt.assert_not_called()

    def test_turned_on_while_the_piano_plays_the_piece_pauses_as_for_her_name(self):
        player = self.assistant.player = Mock(state='playing', completed=None, lost=None, lead=0.0)
        player.pause.side_effect = lambda: setattr(player, 'state', 'paused')
        self.assistant.touched_playback = True  # from an earlier request
        self.c.hushed.clear()
        self.c.turn_on()
        player.pause.assert_called_once()
        self.assertEqual(self.c.state, 'conversation', 'No name needed')
        self.assertAlmostEqual(self.c.dialogue_until - time.monotonic(), voice.START_TIMEOUT_S, delta=.5)
        self.c.dialogue_until = time.monotonic() - 1  # not spoken to
        self.frames(1)
        self.assertEqual(self.c.state, 'waiting')
        player.go_on.assert_called_once()  # the piece plays on
        self.parts.stt.assert_not_called()

    def test_turned_on_while_a_network_piano_plays_its_last_notes_are_not_taken_for_speech(self):
        player = self.assistant.player = Mock(state='playing', completed=None, lost=None, lead=1.0)
        player.pause.side_effect = lambda: setattr(player, 'state', 'paused')
        self.c.hushed.clear()
        self.c.turn_on()
        self.frames(3, speaking=True)  # the notes already sent, still sounding
        self.assertEqual(self.c.state, 'conversation', 'Not a request yet')
        self.assertAlmostEqual(self.c.dialogue_until - time.monotonic(), voice.START_TIMEOUT_S + 1.0, delta=.5)
        self.c.deaf_until = time.monotonic() - .01  # the lead has passed
        self.frames(1, speaking=True)
        self.assertEqual(self.c.state, 'listening')

    def test_goodbye_is_answered_as_any_request_then_she_waits_for_her_name(self):
        self.assistant.ask.return_value = '再见，需要我时再叫我。'
        self.c.respond('再见')
        self.assistant.ask.assert_called_once()  # kept in the conversation, as typed
        self.parts.speaker.stream.assert_called_once_with('再见，需要我时再叫我。')
        self.assertEqual(self.out.events[-1], ('turn_end', True))

    def test_a_piece_that_ends_on_its_own_gets_one_closing_remark(self):
        self.c.listening_since -= 60  # she has been listening for a while
        self.assistant.player = Mock(state='stopped', completed=('传奇', time.perf_counter() - 2), lost=None)
        self.assistant.chinese.return_value = True
        self.frames(1)
        self.assertEqual(len(self.out.spoken), 1)
        remark = self.parts.speaker.stream.call_args.args[0]
        self.assertIn(remark, LINES['finished']['zh'])
        self.assistant.keep.assert_called_once_with('assistant', remark)  # in the conversation, as she said it
        self.assertEqual(self.out.events[-1], ('turn_end', False), 'She may be answered without her name')
        self.c.sleep()
        self.frames(5)
        self.assertEqual(len(self.out.spoken), 1, 'Once')

    def test_a_follow_up_starts_with_each_frame_once(self):
        self.c.resume()
        heard = [np.full(voice.FRAME, i, dtype='<i2') for i in range(3)]
        for frame, speaking in zip(heard, (False, True, True)):
            self.parts.speech.return_value = speaking
            self.c.frame(frame)
        self.assertEqual([int(f[0]) for f in self.c.frames], [0, 1, 2])

    def test_what_happened_while_she_was_quiet_is_not_said_later(self):
        player = self.assistant.player = Mock(state='stopped', completed=None, lost=None)
        self.c.hush()
        player.completed, player.lost = ('传奇', time.perf_counter() - 2), 'gone'  # meanwhile, away from the orb
        self.c.unhush()
        self.frames(5)
        self.assertEqual(self.out.spoken, [])
        self.assertIsNone(player.lost)

    def test_the_piano_going_away_while_playing_is_said_once(self):
        from pianasoul.chat import Assistant
        self.assistant.playback_reply = Assistant(Mock(), conversations=Mock()).playback_reply
        self.assistant.request = '我想听搁浅'
        player = self.assistant.player = Mock(state='paused', completed=None, current=('p', '搁浅'), position=41.0,
                                              lost='no MIDI output matching "pianasoul"')
        self.frames(1)
        self.assertEqual(len(self.out.spoken), 1)
        self.assistant.piano_news.assert_not_called()  # the conversation has her line already (Core)
        self.parts.speaker.stream.assert_called_once_with(
            Assistant(Mock(), conversations=Mock()).playback_reply({'error': 'playback_connection_lost'}))
        self.assertIsNone(player.lost)
        self.c.sleep()
        self.frames(5)
        self.assertEqual(len(self.out.spoken), 1, 'Once')

    def test_the_start_up_lines_name_what_does_each_job(self):
        parts = SimpleNamespace(stt=SimpleNamespace(label='OpenAI x'), speaker=SimpleNamespace(label='ElevenLabs y'))
        self.assertEqual(voice.VoiceParts.describe(parts, 'OpenAI m').splitlines(), [  # yt-dlp is installed here
            'PianaSoul voice', '  chat     OpenAI m', '  hearing  OpenAI x', '  voice    ElevenLabs y',
            '  search   online (yt-dlp)'])
        with patch('importlib.util.find_spec', return_value=None):  # without the advanced extra: no key to set
            self.assertIn('  search   off (the advanced extra is not installed)',
                          voice.VoiceParts.describe(parts, 'OpenAI m'))
        with patch.dict('os.environ', {'PIANASOUL_TRANSCRIBE': '0'}):  # the Mac app's Online Search & Transcription off
            self.assertIn('  search   off (turned off)', voice.VoiceParts.describe(parts, 'OpenAI m'))

    def test_her_chinese_name_is_written_as_configured_whatever_recognition_hears(self):
        stt = voice.Transcriber.__new__(voice.Transcriber)
        stt.client, stt.model = Mock(), 'whisper-1'
        stt.client.audio.transcriptions.create.return_value = '小明同学，我想听小明的歌。'
        self.assertEqual(stt(np.zeros(1600, dtype=np.float32), Mock()), '小茗同学，我想听小明的歌。')

    def test_bad_pcm_is_rejected(self):
        with self.assertRaises(ValueError):
            self.c.frame(np.zeros(10, dtype=np.int16))

    def test_a_short_request_in_the_same_breath_is_not_lost(self):
        # "小茗同学，停止": her name is heard half a second late, and the request is over soon after
        self.parts.stt.return_value = '小茗同学，停止。'
        self.frames(1, speaking=True, name='小茗同学')
        self.frames(2, speaking=True)
        with patch.object(self.assistant, 'ask', return_value='') as ask:
            self.frames(12, speaking=False)
        self.parts.stt.assert_called_once()
        ask.assert_called_once()
        self.assertEqual(ask.call_args.args[1], '小茗同学，停止。', 'The request, not a greeting')
        self.parts.speaker.stream.assert_not_called()

    def test_only_her_name_lingering_gets_a_greeting(self):
        self.parts.stt.return_value = '小茗同学'
        self.frames(1, speaking=True, name='小茗同学')
        self.frames(8, speaking=True)
        self.frames(25, speaking=False)
        self.assertIn(self.parts.speaker.stream.call_args.args[0], LINES['greet']['zh'])
        self.assertEqual(self.out.events[-1], ('turn_end', False), 'Then she listens for the request')

    def test_a_recognition_failure_loses_the_request_not_the_conversation(self):
        self.parts.stt.side_effect = RuntimeError('offline')
        self.frames(1, speaking=True, name='小茗同学')
        self.frames(20, speaking=False)
        self.chat.reset.assert_not_called()
        self.assertEqual(self.c.state, 'waiting')

    def test_a_model_failure_drops_the_request_and_the_piano_plays_on(self):
        player = self.assistant.player = Mock(state='playing', completed=None, lost=None, current=('p', '传奇'), position=3.0)
        self.c.begin()
        player.state = 'paused'
        self.assistant.ask.side_effect = RuntimeError('model down')
        self.c.respond('弹传奇')
        self.chat.reset.assert_not_called()
        player.go_on.assert_called_once_with(3.0)  # at once, not after the follow-up window

    def test_a_piece_paused_while_she_listened_stays_as_the_phone_left_it(self):
        # The phone's player used since the piece was paused for her (paused, a piece chosen): her reply does not
        # undo it. Used before (the piece chosen there, then the orb tapped), the piece plays on as ever.
        player = self.assistant.player = Mock(state='playing', completed=None, lost=None, current=('p', '传奇'),
                                              position=3.0, lead=0.0)
        self.assistant.taps = 1  # the piece chosen on the phone, before she was asked anything
        self.c.hushed.clear()
        self.c.turn_on()
        player.state = 'paused'
        self.c.dialogue_until = time.monotonic() - 1
        self.frames(1)  # not spoken to: she waits for her name, and the piece plays on
        player.go_on.assert_called_once_with(3.0)
        player.reset_mock()
        player.state = 'playing'
        self.c.begin()
        player.state = 'paused'
        self.assistant.taps += 1  # the phone's player, while she listened and answered
        self.assistant.ask.return_value = '《晴天》准备好啦，想听的时候告诉我～'
        self.c.respond('弹晴天')
        player.go_on.assert_not_called()

    def test_hushed_she_neither_listens_nor_speaks_and_the_piano_plays_on(self):
        player = self.assistant.player = Mock(state='playing', completed=None, lost=None, current=('p', '传奇'),
                                              position=3.0)
        self.frames(1, speaking=True, name='小茗同学')
        self.assertEqual(self.c.state, 'listening')
        player.pause.assert_called_once()  # paused while she listens
        player.state = 'paused'
        self.c.hushed.set()
        self.c.hush()
        player.go_on.assert_called_once_with(3.0)  # the piece goes on: a gray orb does not stop the piano
        self.assertEqual(self.c.state, 'waiting')
        self.frames(5, speaking=True, name='小茗同学')
        self.assertEqual(self.c.state, 'waiting', 'Nothing is heard while hushed')
        self.c.say('hello')
        self.assertEqual(self.out.spoken, [], 'Nor said')
        self.c.hushed.clear()
        self.c.unhush()
        self.frames(1, speaking=True, name='小茗同学')
        self.assertEqual(self.c.state, 'listening')

    def test_a_request_answered_while_hushed_ends_quietly(self):
        self.assistant.ask.side_effect = lambda chat, text, speak: (self.c.hushed.set(), 'Hello')[1]
        self.c.respond('play moonlight')
        self.assertEqual(self.out.spoken, [])
        self.assertEqual(self.c.state, 'waiting')
        self.assertNotIn(('turn_end', False), self.out.events)

    def test_a_line_said_meanwhile_lets_her_work_and_what_follows_waits_for_it(self):
        heard = threading.Event()
        def speak(pieces, hushed):
            list(pieces)
            heard.wait(2)
            self.out.events.append(('heard',))
        self.out.speak = speak
        started = time.monotonic()
        self.c.say('我去找找看，稍等我一下～', meanwhile=True)
        self.assertLess(time.monotonic() - started, 0.5, 'Back at once, to do what she said')
        self.assertNotIn(('heard',), self.out.events)
        heard.set()
        self.c.end_turn(sleep=True)
        self.assertLess(self.out.events.index(('heard',)), self.out.events.index(('turn_end', True)),
                        'Her turn ends once it is heard')

    def test_what_she_says_after_a_line_said_meanwhile_comes_after_it(self):
        order = []
        def speak(pieces, hushed):
            text = next(iter(pieces))
            time.sleep(0.05 if text == 'first' else 0)
            order.append(text)
        self.out.speak = speak
        self.parts.speaker.stream.side_effect = lambda text: iter([text])
        self.c.say('first', meanwhile=True)
        self.c.say('second')
        self.assertEqual(order, ['first', 'second'])

    def test_what_she_does_not_say_aloud_is_in_the_details(self):
        self.c.hushed.set()
        with self.assertLogs('pianasoul.voice', 'DEBUG') as details:
            self.c.say('好啦，这就给你弹《水星记》。')
        self.assertEqual(self.out.spoken, [])
        self.assertIn('not said aloud, no one is with her: 好啦，这就给你弹《水星记》。', details.output[0])

    def test_back_while_she_is_still_answering_she_says_it(self):
        self.c.hushed.set()  # the phone went (the screen locked) while she worked on the request

        def ask(chat, text, speak):
            self.c.hushed.clear()  # a phone is back with her before she has the answer
            return 'Hello'
        self.assistant.ask.side_effect = ask
        self.c.respond('play moonlight')
        self.assertEqual(len(self.out.spoken), 1, 'Said aloud')
        self.assertEqual(self.out.events[-1], ('turn_end', False))
        self.c.hush()  # what the phone that went left to do: she is back, so nothing
        self.c.unhush()  # and the one that came: her reply is out, the phone says when it has been heard
        self.assertEqual(self.c.state, 'speaking')
        self.c.resume()
        self.assertEqual(self.c.state, 'conversation')

    def test_a_paused_piece_stays_paused_through_a_greeting(self):
        self.assistant.player = Mock(state='playing', completed=None, lost=None)
        self.c.begin()
        self.assistant.player.pause.assert_called_once()
        self.assistant.player.state = 'paused'
        self.c.resume()
        self.c.begin()
        self.assertTrue(self.c.was_playing)

    def test_mac_speakers_drop_her_own_voice_then_listen_on(self):
        mic = Mock()
        speakers = voice.Speakers(mic)
        conversation = Mock()
        speakers.turn_end(conversation, False)
        mic.drain.assert_called_once()
        conversation.resume.assert_called_once()
        speakers.turn_end(conversation, True)
        conversation.sleep.assert_called_once()


class FakeConnection:
    """An OpenAI realtime session as the SDK gives it: what is sent, and the events the test puts in."""

    def __init__(self):
        self.events, self.sent, self.updates = queue.Queue(), [], []
        self.session = SimpleNamespace(update=lambda session: self.updates.append(session))
        self.input_audio_buffer = SimpleNamespace(append=lambda audio: self.sent.append(audio))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.events.put(None)

    def __iter__(self):
        while (event := self.events.get(timeout=5)) is not None:
            yield event


class LiveTests(unittest.TestCase):
    """voice.Live: one request heard as it is said, against a made-up session."""

    def setUp(self):
        self.conn = FakeConnection()
        self.connects = []
        client = SimpleNamespace(realtime=SimpleNamespace(
            connect=lambda **kw: (self.connects.append(kw), self.conn)[1]))
        self.live = voice.Live(client, {'model': 'gpt-transcribe', 'prompt': 'p', 'keywords': ['小茗同学']})
        self.addCleanup(self.live.close)

    def news(self, *events):
        for event in events:
            self.conn.events.put(event)
        deadline, news = time.monotonic() + 2, []
        while time.monotonic() < deadline and not news:
            news = self.live.news()
            time.sleep(0.01)
        return news

    def event(self, kind, **fields):
        return SimpleNamespace(type=kind, **fields)

    def test_the_session_is_her_hearing_with_openai_deciding_the_end(self):
        self.live.send(np.zeros(voice.FRAME, dtype='<i2'))
        deadline = time.monotonic() + 2
        while not self.conn.sent and time.monotonic() < deadline:
            time.sleep(0.01)
        audio = self.conn.updates[0]['audio']['input']
        self.assertEqual(audio['transcription'], {'model': 'gpt-transcribe', 'prompt': 'p', 'keywords': ['小茗同学']})
        self.assertEqual(audio['turn_detection'], {'type': 'semantic_vad', 'eagerness': 'high'})
        self.assertEqual(audio['format'], {'type': 'audio/pcm', 'rate': 24000})
        self.assertNotIn('on_reconnecting', self.connects[0], 'Connected again, what was said would be lost')
        self.assertEqual(len(base64.b64decode(self.conn.sent[0])), 1920 * 2, '80 ms at 24 kHz')

    def test_the_text_is_all_said_so_far_in_its_order_once_they_pause(self):
        started = self.event('input_audio_buffer.speech_started')
        committed = lambda i: self.event('input_audio_buffer.committed', item_id=i)
        written = lambda i, t: self.event('conversation.item.input_audio_transcription.completed', item_id=i,
                                          transcript=t)
        self.assertEqual(self.news(started), [('speech', '')])
        self.assertEqual(self.news(committed('a'), started), [('speech', '')], 'Talking again: not yet')
        self.assertEqual(self.news(committed('b'), written('b', '弹一首德彪西的月光。')), [])
        self.assertEqual(self.news(written('a', '小明同学。')), [('text', '小茗同学。 弹一首德彪西的月光。')])

    def test_an_error_or_a_closed_connection_is_broken(self):
        self.assertEqual(self.news(self.event('error', error=SimpleNamespace(message='bad session'))),
                         [('broken', 'bad session')])
        live = voice.Live(SimpleNamespace(realtime=SimpleNamespace(connect=Mock(side_effect=OSError('offline')))), {})
        deadline, news = time.monotonic() + 2, []
        while time.monotonic() < deadline and not news:
            news = live.news()
        self.assertEqual(news, [('broken', 'offline')])

    def test_an_80_ms_frame_goes_on_at_24_khz_joined_to_the_last(self):
        live = voice.Live.__new__(voice.Live)
        live.outbox, live.last = queue.SimpleQueue(), 0.0
        ramp = np.arange(2 * voice.FRAME, dtype=np.float32)
        live.send(ramp[:voice.FRAME].astype('<i2'))
        live.send(ramp[voice.FRAME:].astype('<i2'))
        up = np.r_[live.outbox.get(), live.outbox.get()]
        self.assertEqual(len(up), 2 * 1920)
        self.assertTrue((np.diff(up.astype(np.int32)) >= 0).all(), 'Smooth across the two frames')


class SpeakerTests(unittest.TestCase):
    def setUp(self):
        self.speaker = voice.Speaker()
        self.speaker.cloud = True
        quiet = patch('builtins.print')
        quiet.start()
        self.addCleanup(quiet.stop)

    def respond(self, *chunks, error=None):
        resp = Mock()
        resp.__enter__ = Mock(return_value=resp)
        resp.__exit__ = Mock(return_value=False)
        reads = list(chunks) + ([error] if error else []) + [b'']
        def read(size):
            item = reads.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        resp.read.side_effect = read
        return resp

    def test_her_voice_comes_in_pieces_as_it_is_made(self):
        with patch('urllib.request.urlopen', return_value=self.respond(b'\x01\x00\x02', b'\x00\x03\x00')):
            pieces = [p.tolist() for p in self.speaker.stream('hello')]
        self.assertEqual(pieces, [[1], [2, 3]], 'An odd byte waits for its other half')

    def test_a_failure_before_any_audio_uses_the_mac_voice(self):
        with patch('urllib.request.urlopen', side_effect=OSError('offline')), \
                patch.object(voice, 'mac_voice', return_value=np.array([7], dtype=np.int16)) as mac:
            self.assertEqual([p.tolist() for p in self.speaker.stream('你好')], [[7]])
        mac.assert_called_once_with('你好')

    def test_a_refused_key_is_not_tried_again(self):
        refused = urllib.error.HTTPError('url', 401, 'no', {}, None)
        refused.read = lambda: b'invalid key'
        with patch('urllib.request.urlopen', side_effect=refused), \
                patch.object(voice, 'mac_voice', return_value=np.array([7], dtype=np.int16)):
            list(self.speaker.stream('hello'))
        self.assertFalse(self.speaker.cloud)

    def test_a_failure_halfway_does_not_start_the_sentence_again(self):
        with patch('urllib.request.urlopen', return_value=self.respond(b'\x01\x00', error=OSError('lost'))), \
                patch.object(voice, 'mac_voice') as mac:
            self.assertEqual([p.tolist() for p in self.speaker.stream('hello')], [[1]])
        mac.assert_not_called()

    def test_her_ready_made_lines_are_said_from_their_recordings(self):
        pause, greeting, softer = LINES['pause']['zh'][0], LINES['greet']['zh'][0], LINES['softer']['zh'][0]
        with patch.object(self.speaker, 'clip', return_value=np.array([5], dtype=np.int16)) as clip, \
                patch('urllib.request.urlopen') as urlopen:
            self.assertEqual([p.tolist() for p in self.speaker.stream(pause)], [[5]])
            list(self.speaker.stream(greeting))
            list(self.speaker.stream(softer))
        urlopen.assert_not_called()
        self.assertEqual([c.args for c in clip.call_args_list], [(pause, voice.VOICE_SETTINGS),
                         (greeting, voice.GREETING_SETTINGS), (softer, voice.STEADY_SETTINGS)])

    def test_her_lines_are_made_once_and_only_those_not_made_yet(self):
        made = list(voice.READY)[1:]  # all but the first are there already
        with patch.object(self.speaker, 'clip_path', side_effect=lambda text, how: Mock(is_file=lambda: text in made)), \
                patch.object(self.speaker, 'clip') as clip:
            voice.prepare_lines(self.speaker)
        clip.assert_called_once_with(*next(iter(voice.READY.items())))

    def test_a_line_being_made_is_not_made_twice(self):
        started, go = threading.Event(), threading.Event()
        def make(request, timeout):
            started.set()
            go.wait(5)
            resp = Mock(read=Mock(return_value=b'\x01\x00\x02\x00'))  # made whole, read at once
            resp.__enter__ = Mock(return_value=resp)
            resp.__exit__ = Mock(return_value=False)
            return resp
        with tempfile.TemporaryDirectory() as data, patch('pianasoul.library.DATA_DIR', Path(data)), \
                patch('urllib.request.urlopen', side_effect=make) as urlopen:
            background = threading.Thread(target=self.speaker.clip, args=('hello',))
            background.start()
            started.wait(5)  # being made in the background, when she needs it
            said = []
            needed = threading.Thread(target=lambda: said.append(self.speaker.clip('hello').tolist()))
            needed.start()
            go.set()
            background.join(5)
            needed.join(5)
        self.assertEqual(urlopen.call_count, 1, 'Made, and paid for, once')
        self.assertEqual(said, [[1, 2]])

    def test_her_lines_are_made_while_the_service_goes_on(self):
        made, go = threading.Event(), threading.Event()
        with patch.multiple(voice, Transcriber=Mock(), WakeWord=Mock(), vad_model=Mock(), Speaker=Mock(),
                            prepare_lines=Mock(side_effect=lambda speaker: (go.wait(5), made.set()))):
            voice.VoiceParts()  # returns while they are still being made
            self.assertFalse(made.is_set())
            go.set()
            self.assertTrue(made.wait(5))

    def test_nothing_to_say_says_nothing(self):
        with patch('urllib.request.urlopen') as urlopen:
            self.assertEqual(list(self.speaker.stream('https://example.com')), [])
        urlopen.assert_not_called()


if __name__ == '__main__':
    unittest.main()
