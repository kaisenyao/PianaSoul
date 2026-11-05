"""Her ears and voice, on the Mac and on the phone; no paid APIs or audio devices."""
import base64
import io
import json
from pathlib import Path
import queue
import tarfile
import tempfile
from types import SimpleNamespace
import threading
import time
import unittest
from unittest.mock import Mock, patch
import urllib.error
import wave

import numpy as np

import places  # noqa: F401  (first: the library, data and cache folders are throwaway ones)
from pianasoul import voice
from pianasoul.lines import LINES


class ModelStudio:
    """Model Studio's speech recognition, made up, in place of urlopen: each request kept, each answered in turn
    with the text heard, or an HTTP error (status, its body)."""

    def __init__(self, *answers):
        self.answers, self.sent = list(answers), []

    def __call__(self, request, timeout):
        self.sent.append((request, json.loads(request.data), timeout))
        answer = self.answers.pop(0)
        if isinstance(answer, str):
            return io.BytesIO(json.dumps({'output': {'text': answer}, 'usage': {'duration': 2}}).encode())
        status, body = answer
        raise urllib.error.HTTPError(request.full_url, status, 'error', {}, io.BytesIO(json.dumps(body).encode()))


NO_WORDS = (400, {'code': 'CLIENT_ERROR', 'message': 'ASR_RESPONSE_HAVE_NO_WORDS', 'request_id': 'r'})


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
        self.assistant = Mock(player=None, touched_playback=False, taps=0, **{'piece_to_start.return_value': False})
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
        self.assistant.ask.assert_called_once_with(self.chat, '小茗同学，换一首李斯特的爱之梦。', speak=self.c.say,
                                                   paused_for_her=False)
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
        self.assistant.ask.assert_called_once_with(self.chat, 'play moonlight', speak=self.c.say, paused_for_her=False)
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

    def test_with_a_piece_of_hers_about_to_start_only_her_name_is_listened_for(self):
        # Her news of a piece she found, and it follows: listening on, she would keep it waiting (may_play).
        self.assistant.ask.return_value = '找到啦，是 Jason Piano 弹的《晴天》～'
        self.assistant.piece_to_start.return_value = True
        self.c.respond('弹晴天')
        self.parts.speaker.stream.assert_called_once_with('找到啦，是 Jason Piano 弹的《晴天》～')
        self.assertEqual(self.out.events[-1], ('turn_end', True), 'No name-free follow-up')

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
        self.assistant.piano_news.return_value = LINES['piano_lost']['zh'][1]
        player = self.assistant.player = Mock(state='paused', completed=None, current=('p', '搁浅'), position=41.0,
                                              lost='no MIDI output matching "pianasoul"')
        self.frames(1)
        self.assertEqual(len(self.out.spoken), 1)
        # Said as the line in the conversation: the one Core sent there already (piano_news makes it once)
        self.assistant.piano_news.assert_called_once_with({'error': 'playback_connection_lost'})
        self.parts.speaker.stream.assert_called_once_with(LINES['piano_lost']['zh'][1])
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

    def test_the_volume_commands_are_spelling_hints_as_the_short_commands_have_them(self):
        # 小声一点 was heard as 小心一点. The hints are short commands, word for word; Model Studio's go with its requests too.
        from pianasoul.playback_commands import COMMANDS
        self.assertTrue(all(any(w in words for words in COMMANDS.values()) for w in voice.STT_COMMAND_HINTS))
        stt = voice.Transcriber.__new__(voice.Transcriber)
        stt.model = 'gpt-transcribe'
        keywords = stt.hints(Mock(playable=Mock(return_value=[])))['keywords']
        for word in ('大声一点', '小声一点', 'louder', 'softer'):
            self.assertIn(word, keywords)
        self.assertTrue({'大声一点', 'louder'} <= COMMANDS['louder'] and {'小声一点', 'softer'} <= COMMANDS['softer'])

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

    def test_her_name_then_nothing_model_studio_hears_words_in_she_greets(self):
        with patch.dict('os.environ', {'DASHSCOPE_API_KEY': 'test-key'}):
            self.parts.stt = voice.BailianTranscriber()
        self.assistant.lib.playable.return_value = []
        self.c = voice.Conversation(self.assistant, self.chat, self.parts, self.out)
        with patch('urllib.request.urlopen', ModelStudio(NO_WORDS)) as studio:
            self.frames(1, speaking=True, name='小茗同学')
            self.frames(25, speaking=False)
        self.assertEqual(len(studio.sent), 1, 'Heard whole, once')
        self.assertIn(self.parts.speaker.stream.call_args.args[0], LINES['greet']['zh'], 'Not a failure')
        self.assertEqual(self.out.events[-1], ('turn_end', False), 'Then she listens for the request')

    def test_she_never_reads_out_an_emoji(self):
        for text, said in (('一共听了 3 次。🎹', '一共听了 3 次。'), ('好呀\u2764\ufe0f，弹给你听\u2728', '好呀，弹给你听'),
                           ('Bravo 👏🏽 well played', 'Bravo well played'), ('第 1\ufe0f\u20e3 首', '第 1 首')):
            self.assertEqual(voice.speakable(text), said)

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
        self.assistant.ask.side_effect = lambda chat, text, speak, paused_for_her: (self.c.hushed.set(), 'Hello')[1]
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
        self.c.say('好呀，我去找一版《红豆》～', meanwhile=True)
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

        def ask(chat, text, speak, paused_for_her):
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

    def test_she_is_told_the_piece_playing_was_paused_for_her_to_listen(self):
        self.assistant.player = Mock(state='playing', completed=None, lost=None)
        self.assistant.ask.return_value = ''
        self.c.begin()
        self.assistant.player.state = 'paused'
        self.c.respond('这首先停一下，我想说句话')
        self.assertTrue(self.assistant.ask.call_args.kwargs['paused_for_her'], 'It plays on after her reply')
        self.assistant.player.go_on.assert_called_once()
        self.c.begin()  # paused since: only paused
        self.c.respond('继续吧')
        self.assertFalse(self.assistant.ask.call_args.kwargs['paused_for_her'])

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


class BailianHearingTests(unittest.TestCase):
    """voice.BailianTranscriber: a request heard whole on Model Studio, a made-up one."""

    def setUp(self):
        for patcher in (patch.dict('os.environ', {'DASHSCOPE_API_KEY': 'test-key', 'DASHSCOPE_REGION': ''}),
                        patch('builtins.print')):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.lib = Mock()
        self.lib.playable.return_value = [{'title': t, 'composer': '', 'performer': ''}
                                          for t in ('周杰伦 - 晴天 (MappleZS)', 'Chopin - Nocturne Op. 9 No. 2')]

    def hear(self, *answers):
        """What Model Studio's answers come to, and what was sent: (request, body, timeout) each."""
        with patch('urllib.request.urlopen', ModelStudio(*answers)) as studio:
            return voice.BailianTranscriber()(np.zeros(1600, dtype=np.float32), self.lib), studio.sent

    def test_the_request_goes_whole_with_the_words_to_expect_and_no_language(self):
        text, sent = self.hear('小明同学，弹晴天。')
        self.assertEqual(text, '小茗同学，弹晴天。', 'Her name written as configured')
        request, body, timeout = sent[0]
        self.assertEqual(request.full_url,
                         'https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation')
        self.assertEqual((request.get_header('Authorization'), request.get_header('X-dashscope-sse')),
                         ('Bearer test-key', 'disable'))
        self.assertLessEqual(timeout, 20, 'Bounded')
        self.assertEqual(body['model'], 'qwen-audio-3.1-asr-flash')
        expected = ['小茗同学', 'Hey Jasmine', '暂停', '停止播放', '继续播放', '从头弹',  # her names, the commands
                    '大声一点', '小声一点', 'louder', 'softer',  # (小声一点 was heard as 小心一点)
                    'Chopin', '周杰伦', 'MappleZS', 'Nocturne Op. 9 No. 2', '晴天']  # the people, then the pieces
        context, audio = body['input']['messages']
        self.assertEqual(context, {'role': 'user', 'content': [{'type': 'input_text', 'text': '，'.join(expected)}]})
        data = audio['content'][0]['input_audio']['data']
        self.assertTrue(data.startswith('data:audio/wav;base64,'), 'The audio last')
        with wave.open(io.BytesIO(base64.b64decode(data.split(',', 1)[1]))) as w:
            self.assertEqual((w.getframerate(), w.getnchannels(), w.getnframes()), (16000, 1, 1600))
        self.assertEqual(body['parameters'], {'format': 'wav', 'sample_rate': '16000',
                                              'vocabulary': dict.fromkeys(expected, 4)}, 'No language hints')

    def test_the_context_is_what_fits_in_400_characters_and_the_hot_words_go_up_to_2000(self):
        self.lib.playable.return_value = [{'title': f'作曲家{i} - 曲子{i}', 'composer': '', 'performer': ''}
                                          for i in range(2500)]
        _, sent = self.hear('弹曲子7。')
        context = sent[0][1]['input']['messages'][0]['content'][0]['text']
        vocabulary = list(sent[0][1]['parameters']['vocabulary'])
        said = context.split('，')
        self.assertEqual(said, vocabulary[:len(said)], 'The first words, whole')
        self.assertLessEqual(len(context), 400)
        self.assertGreater(len(context + '，' + vocabulary[len(said)]), 400, 'As many as fit')
        self.assertEqual((len(vocabulary), vocabulary[10], vocabulary[-1]), (2000, '作曲家2499', '作曲家510'),
                         'After her names and the commands, people first, the newest first')

    def test_no_words_heard_is_nothing_said_not_a_failure(self):
        text, sent = self.hear(NO_WORDS)
        self.assertEqual((text, len(sent)), ('', 1))

    def test_refused_with_the_words_to_expect_it_is_heard_again_without_them(self):
        text, sent = self.hear((400, {'code': 'InvalidParameter', 'message': 'vocabulary is invalid'}), '弹晴天。')
        self.assertEqual(text, '弹晴天。')
        again = sent[1][1]
        self.assertEqual(([m['content'][0]['type'] for m in again['input']['messages']], again['parameters']),
                         (['input_audio'], {'format': 'wav', 'sample_rate': '16000'}))

    def test_a_refused_key_says_why_and_is_not_tried_again(self):
        with self.assertRaises(RuntimeError) as raised:
            self.hear((401, {'code': 'InvalidApiKey', 'message': 'Invalid API-key provided.'}))
        self.assertEqual(str(raised.exception), 'Model Studio error 401: InvalidApiKey: Invalid API-key provided.')

    def test_an_account_that_owes_money_is_not_asked_again_without_the_words_to_expect(self):
        studio = ModelStudio((400, {'code': 'Arrearage', 'message': 'Access denied, please make sure your account is '
                                                                    'in good standing.'}), '弹晴天。')
        with patch('urllib.request.urlopen', studio), self.assertRaises(RuntimeError) as raised:
            voice.BailianTranscriber()(np.zeros(1600, dtype=np.float32), self.lib)
        self.assertEqual(len(studio.sent), 1)
        self.assertTrue(str(raised.exception).startswith('Model Studio error 400: Arrearage'))

    def test_a_key_made_in_singapore_is_heard_there(self):
        with patch.dict('os.environ', {'DASHSCOPE_REGION': 'singapore'}):
            _, sent = self.hear('你好。')
        self.assertTrue(sent[0][0].full_url.startswith('https://dashscope-intl.aliyuncs.com/api/v1/'))

    def test_her_ears_follow_her_model_line(self):
        for values, made in (({'PIANASOUL_MODEL': 'bailian', 'OPENAI_API_KEY': ''}, voice.BailianTranscriber),
                             ({'PIANASOUL_MODEL': 'anthropic', 'OPENAI_API_KEY': 'test'}, voice.Transcriber),
                             ({'PIANASOUL_MODEL': '', 'OPENAI_API_KEY': 'test'}, voice.Transcriber)):
            with patch.dict('os.environ', values):
                self.assertIs(type(voice.make_transcriber()), made, values)
        with patch.dict('os.environ', {'PIANASOUL_MODEL': 'bailian'}):
            stt = voice.make_transcriber()
        self.assertEqual(stt.label, 'Model Studio qwen-audio-3.1-asr-flash, once you have finished')
        self.assertIsNone(stt.listen(self.lib), 'Heard whole')
        with patch.dict('os.environ', {'PIANASOUL_MODEL': 'bailian', 'DASHSCOPE_API_KEY': '',
                                       'OPENAI_API_KEY': 'test'}), \
                self.assertRaisesRegex(ValueError, '^DASHSCOPE_API_KEY is not set$'):
            voice.make_transcriber()


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

    def test_elevenlabs_is_asked_as_it_always_was_and_her_recordings_keep_their_names(self):
        with patch.dict('os.environ', {'PIANASOUL_VOICE': '', 'ELEVENLABS_API_KEY': 'test-key'}), \
                patch.multiple(voice, VOICE_ID='test-voice-id', TTS_MODEL='eleven_v4_turbo'):
            speaker = voice.Speaker()
            with patch('urllib.request.urlopen', return_value=self.respond(b'\x01\x00')) as urlopen:
                list(speaker.stream('你好，Jasmine'))
            names = [speaker.clip_path(text, how).name for text, how in (('暂停了。', voice.GREETING_SETTINGS),
                     ('Paused.', voice.VOICE_SETTINGS), ('Paused.', voice.STEADY_SETTINGS))]
        request = urlopen.call_args.args[0]
        self.assertEqual((speaker.cloud, speaker.label), (True, 'ElevenLabs eleven_v4_turbo'))
        self.assertEqual(request.full_url,
                         'https://api.elevenlabs.io/v1/text-to-speech/test-voice-id/stream?output_format=pcm_24000')
        self.assertEqual(request.header_items(), [('Xi-api-key', 'test-key'), ('Content-type', 'application/json')])
        self.assertEqual(request.data, b'{"text": "\\u4f60\\u597d\\uff0cJasmine", "model_id": "eleven_v4_turbo", '
                                       b'"voice_settings": {"stability": 0.5, "similarity_boost": 0.75, "style": 0.0, '
                                       b'"use_speaker_boost": true, "speed": 1.0}}')
        self.assertEqual(names, ['216f5c72789ea5d2.wav', '0f4734ad11025d04.wav', 'b2662dd05e37e438.wav'],
                         'The recordings made before are found, none made again')


def events(*events, status=200):
    """Server-sent events as Model Studio streams speech: each with its id, its kind and HTTP status, then its data;
    one with a code is an error, with the status given."""
    lines = []
    for i, event in enumerate(events, 1):
        error = 'code' in event
        lines += [f'id:{i}', f'event:{"error" if error else "result"}', f':HTTP_STATUS/{status if error else 200}',
                  f'data:{json.dumps(event)}', '']
    return '\n'.join(lines).encode()


def audio(pcm):
    """An event with a piece of her voice in it."""
    return {'output': {'type': 'sentence-synthesis', 'finish_reason': 'null',
                       'audio': {'data': base64.b64encode(pcm).decode(), 'id': 'a', 'expires_at': 0}},
            'request_id': 'r'}


END = {'output': {'type': 'sentence-end', 'finish_reason': 'stop',
                  'audio': {'data': '', 'url': 'https://example.com/voice.wav', 'id': 'a', 'expires_at': 0}},
       'usage': {'characters': 4}, 'request_id': 'r'}
REFUSED_KEY = (401, {'code': 'InvalidApiKey', 'message': 'Invalid API-key provided.', 'request_id': 'r'})
NOT_ALLOWED = (403, {'code': 'AccessDenied.Unpurchased', 'message': 'Access to model denied.', 'request_id': 'r'})
DEBT = (400, {'code': 'Arrearage', 'message': 'Access denied, please make sure your account is in good standing.',
              'request_id': 'r'})
INSPECTED = (400, {'code': 'DataInspectionFailed', 'message': 'Output data may contain inappropriate content.',
                   'request_id': 'r'})


class Streamed(io.BytesIO):
    """A streamed answer as urlopen gives it: its status, then its lines."""
    status = 200


class Synthesizer:
    """Model Studio's speech synthesis, made up, in place of urlopen: each request kept, each answered in turn with
    the events given, or an HTTP error (status, its body)."""

    def __init__(self, *answers):
        self.answers, self.sent, self.streams = list(answers), [], []

    def __call__(self, request, timeout):
        self.sent.append((request, json.loads(request.data), timeout))
        answer = self.answers.pop(0)
        if isinstance(answer, bytes):
            self.streams.append(Streamed(answer))
            return self.streams[-1]
        status, body = answer
        raise urllib.error.HTTPError(request.full_url, status, 'error', {}, io.BytesIO(json.dumps(body).encode()))


class BailianVoiceTests(unittest.TestCase):
    """voice.Speaker with PIANASOUL_VOICE=bailian: her voice on Model Studio, a made-up one."""

    def setUp(self):
        values = {'PIANASOUL_VOICE': 'bailian', 'DASHSCOPE_VOICE_API_KEY': 'test-voice-key',
                  'DASHSCOPE_VOICE_REGION': '', 'DASHSCOPE_API_KEY': 'test-model-key', 'DASHSCOPE_REGION': ''}
        for patcher in (patch.dict('os.environ', values),
                        patch.object(voice, 'VOICE_ID', ''), patch('builtins.print'),
                        patch.object(voice, 'mac_voice', return_value=np.array([7], dtype=np.int16))):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.speaker = voice.Speaker()

    def say(self, text, *answers):
        """What she says of text, in pieces, and what was sent: (request, body, timeout) each."""
        with patch('urllib.request.urlopen', Synthesizer(*answers)) as studio:
            return [p.tolist() for p in self.speaker.stream(text)], studio.sent

    def test_her_voice_comes_in_pieces_as_model_studio_makes_it_an_odd_byte_waiting_for_its_other_half(self):
        said, sent = self.say('你好，Jasmine', events(audio(b'\x01\x00\x02'), audio(b'\x00\x03\x00'), END))
        self.assertEqual(said, [[1], [2, 3]])
        request, body, timeout = sent[0]
        self.assertEqual(request.full_url, 'https://dashscope.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer')
        self.assertEqual((request.get_header('Authorization'), request.get_header('X-dashscope-sse')),
                         ('Bearer test-voice-key', 'enable'), "Its own key, never the model's")
        self.assertLessEqual(timeout, 30, 'Bounded')
        self.assertEqual(body, {'model': 'qwen-audio-3.1-tts-flash', 'input': {
            'text': '你好，Jasmine', 'voice': 'longanlingxin_v3.1', 'format': 'pcm', 'sample_rate': 24000}})
        self.assertEqual(self.speaker.label, 'Model Studio qwen-audio-3.1-tts-flash, longanlingxin_v3.1')

    def test_the_voice_named_speaks_in_the_region_its_key_was_made_in(self):
        with patch.dict('os.environ', {'DASHSCOPE_VOICE_REGION': 'singapore'}), \
                patch.object(voice, 'VOICE_ID', 'xunanchuan_v3.1'):
            self.speaker = voice.Speaker()
        _, sent = self.say('你好', events(audio(b'\x01\x00'), END))
        self.assertTrue(sent[0][0].full_url.startswith('https://dashscope-intl.aliyuncs.com/api/v1/'))
        self.assertEqual(sent[0][1]['input']['voice'], 'xunanchuan_v3.1')
        with patch.dict('os.environ', {'DASHSCOPE_REGION': 'singapore'}):  # her model's region is not her voice's
            self.speaker = voice.Speaker()
        _, sent = self.say('你好', events(audio(b'\x01\x00'), END))
        self.assertTrue(sent[0][0].full_url.startswith('https://dashscope.aliyuncs.com/api/v1/'))

    def test_without_a_key_of_its_own_the_mac_voice_speaks(self):
        with patch.dict('os.environ', {'DASHSCOPE_VOICE_API_KEY': ''}):
            speaker = voice.Speaker()
        self.assertEqual((speaker.cloud, speaker.label), (False, 'macOS (set DASHSCOPE_VOICE_API_KEY)'),
                         "Never with the model's key")

    def test_every_line_her_greetings_too_is_said_at_the_voice_s_own_pace(self):
        rates = []
        with tempfile.TemporaryDirectory() as data, patch('pianasoul.library.DATA_DIR', Path(data)):
            for text, how in (('你好', voice.GREETING_SETTINGS), ('暂停了。', voice.VOICE_SETTINGS),
                              ('大声一点了。', voice.STEADY_SETTINGS)):
                with patch('urllib.request.urlopen', Synthesizer(events(audio(b'\x01\x00'), END))) as studio:
                    self.speaker.clip(text, how)
                rates.append(studio.sent[0][1]['input'].get('rate'))
        self.assertEqual(rates, [None, None, None])
        self.assertEqual(self.speaker.clip_path('你好', voice.GREETING_SETTINGS), self.speaker.clip_path('你好'),
                         'One recording of a line, however ElevenLabs would say it')

    def test_a_ready_made_line_is_made_whole_from_its_events_once_and_kept(self):
        greeting = LINES['greet']['zh'][0]
        with tempfile.TemporaryDirectory() as data, patch('pianasoul.library.DATA_DIR', Path(data)):
            said, sent = self.say(greeting, events(audio(b'\x01\x00\x02'), audio(b'\x00'), END))
            again, asked = self.say(greeting)  # nothing to answer with: it is not asked for
            with wave.open(str(self.speaker.clip_path(greeting, voice.GREETING_SETTINGS))) as w:
                kept = w.getframerate(), w.getnchannels(), w.getsampwidth()
        self.assertEqual((said, again, len(sent), asked), ([[1, 2]], [[1, 2]], 1, []))
        self.assertEqual(kept, (24000, 1, 2))

    def test_her_recordings_in_a_qwen_voice_are_its_own_and_each_voice_s_are_kept(self):
        text = LINES['pause']['zh'][0]
        qwen = self.speaker.clip_path(text)
        with patch.object(voice, 'VOICE_ID', 'xunanchuan_v3.1'):
            other = voice.Speaker().clip_path(text)
        with patch.dict('os.environ', {'PIANASOUL_VOICE': 'elevenlabs', 'ELEVENLABS_API_KEY': 'k'}), \
                patch.object(voice, 'VOICE_ID', 'longanlingxin_v3.1'):
            elevenlabs = voice.Speaker().clip_path(text)
        self.assertEqual(len({qwen, other, elevenlabs}), 3, "None is another's")
        self.assertEqual(voice.Speaker().clip_path(text), qwen, 'Back to a voice, its recordings are there')

    def test_a_refused_key_a_permission_or_a_debt_turns_her_cloud_voice_off_for_the_session(self):
        for answer in (REFUSED_KEY, NOT_ALLOWED, DEBT, events(REFUSED_KEY[1], status=401), events(DEBT[1], status=400)):
            self.speaker.cloud = True
            said, _ = self.say('你好', answer)
            self.assertEqual((said, self.speaker.cloud), ([[7]], False), answer)
        self.assertEqual(self.say('你好'), ([[7]], []), 'Not tried again')

    def test_words_that_do_not_pass_inspection_are_said_by_the_mac_voice_that_once(self):
        for answer in (INSPECTED, events(INSPECTED[1], status=400)):
            said, _ = self.say('你好', answer)
            self.assertEqual((said, self.speaker.cloud), ([[7]], True), answer)
        said, _ = self.say('再见', events(audio(b'\x01\x00'), END))
        self.assertEqual(said, [[1]], 'The next sentence in her voice')

    def test_an_error_halfway_does_not_start_the_sentence_again(self):
        said, _ = self.say('你好', events(audio(b'\x01\x00'), INSPECTED[1], status=400))
        self.assertEqual(said, [[1]])

    def test_an_answer_without_audio_is_said_by_the_mac_voice(self):
        said, _ = self.say('你好', events(END))
        self.assertEqual(said, [[7]])

    def test_her_voice_no_longer_wanted_closes_its_connection(self):
        studio = Synthesizer(events(audio(b'\x01\x00'), audio(b'\x02\x00'), END))
        with patch('urllib.request.urlopen', studio):
            pieces = self.speaker.stream('你好')
            next(pieces)
            pieces.close()  # hushed: the phone asks for no more
        self.assertTrue(studio.streams[0].closed)

    def test_her_lines_stop_being_made_on_a_key_or_payment_error_and_go_on_after_any_other(self):
        lines = len(voice.READY)
        with tempfile.TemporaryDirectory() as data, patch('pianasoul.library.DATA_DIR', Path(data)):
            for answer, asked in ((REFUSED_KEY, 1), (DEBT, 1), (INSPECTED, lines)):
                with patch('urllib.request.urlopen', Synthesizer(*[answer] * lines)) as studio:
                    voice.prepare_lines(self.speaker)
                self.assertEqual(len(studio.sent), asked, answer)


class VoiceServiceTests(unittest.TestCase):
    """Her voice as PIANASOUL_VOICE chooses it, and the start-up line that names it."""

    def speaker(self, **values):
        unset = dict.fromkeys(('PIANASOUL_VOICE', 'ELEVENLABS_API_KEY', 'DASHSCOPE_VOICE_API_KEY',
                               'DASHSCOPE_VOICE_REGION'), '')
        with patch.dict('os.environ', {**unset, **values}), patch.object(voice, 'VOICE_ID', 'v'):
            return voice.Speaker()

    def test_her_voice_is_the_one_chosen_and_without_a_choice_as_it_always_was(self):
        both, elevenlabs = {'ELEVENLABS_API_KEY': 'k', 'DASHSCOPE_VOICE_API_KEY': 'k'}, f'ElevenLabs {voice.TTS_MODEL}'
        for values, service, cloud, label in (
                ({'ELEVENLABS_API_KEY': 'k'}, voice.ElevenLabsVoice, True, elevenlabs),
                ({}, voice.ElevenLabsVoice, False, 'macOS (set ELEVENLABS_API_KEY and PIANASOUL_VOICE_ID)'),
                ({'PIANASOUL_VOICE': 'elevenlabs', **both}, voice.ElevenLabsVoice, True, elevenlabs),
                ({'PIANASOUL_VOICE': 'Bailian', **both}, voice.BailianVoice, True,
                 'Model Studio qwen-audio-3.1-tts-flash, v'),
                ({'PIANASOUL_VOICE': 'mac', **both}, type(None), False, 'macOS')):
            speaker = self.speaker(**values)
            self.assertEqual((type(speaker.service), speaker.cloud, speaker.label), (service, cloud, label), values)
        parts = SimpleNamespace(stt=SimpleNamespace(label='Model Studio x'),
                                speaker=self.speaker(PIANASOUL_VOICE='bailian', **both))
        self.assertIn('  voice    Model Studio qwen-audio-3.1-tts-flash, v',
                      voice.VoiceParts.describe(parts, 'Model Studio m'))

    def test_the_mac_voice_chosen_asks_no_cloud(self):
        speaker = self.speaker(PIANASOUL_VOICE='mac', ELEVENLABS_API_KEY='k')
        with patch('urllib.request.urlopen') as urlopen, \
                patch.object(voice, 'mac_voice', return_value=np.array([7], dtype=np.int16)):
            self.assertEqual([p.tolist() for p in speaker.stream(LINES['pause']['zh'][0])], [[7]])
            voice.prepare_lines(speaker)
        urlopen.assert_not_called()

    def test_a_voice_or_a_region_it_does_not_know_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "^PIANASOUL_VOICE must be mac, elevenlabs or bailian, got 'minimax'$"):
            self.speaker(PIANASOUL_VOICE='minimax')
        with self.assertRaisesRegex(ValueError, '^DASHSCOPE_VOICE_REGION must be beijing or singapore'):
            self.speaker(PIANASOUL_VOICE='bailian', DASHSCOPE_VOICE_REGION='virginia')


class Spotter:
    """sherpa-onnx's keyword spotter, made up: what it is made with, and its keywords, which it reads from their
    file as it is made (as the real one does)."""

    def __init__(self, **config):
        self.config, self.keywords = config, Path(config['keywords_file']).read_text(encoding='utf-8')


class ModelsTests(unittest.TestCase):
    """Her wake word and voice activity models: from the folder PIANASOUL_MODELS names (the Mac app's own), or
    downloaded once into the data folder."""

    def setUp(self):
        folders = tempfile.TemporaryDirectory()
        self.addCleanup(folders.cleanup)
        self.data, self.given = Path(folders.name) / 'data', Path(folders.name) / 'models'
        data = patch('pianasoul.library.DATA_DIR', self.data)
        data.start()
        self.addCleanup(data.stop)

    def models(self, *left_out):
        """A models folder laid out as the Mac app's, with made-up models, but for those left out."""
        lexicon = b'HEY HH EY1\nJASMINE JH AE1 Z M AH0 N\n'
        for name in [*(f'{voice.WAKE_MODEL}/{part}' for part in voice.WAKE_PARTS.values()), 'silero_vad.onnx']:
            if name not in left_out:
                (self.given / name).parent.mkdir(parents=True, exist_ok=True)
                (self.given / name).write_bytes(lexicon if name.endswith('en.phone') else b'model')
        return self.given

    def test_the_models_folder_given_is_read_as_it_is_and_nothing_is_downloaded_or_written(self):
        folder = self.models()
        contents = lambda: {p: p.is_file() and p.read_bytes() for p in folder.rglob('*')}
        before = contents()
        with patch.dict('os.environ', {'PIANASOUL_MODELS': str(folder)}), patch('urllib.request.urlopen') as urlopen, \
                patch.dict('sys.modules', {'sherpa_onnx': SimpleNamespace(KeywordSpotter=Spotter)}):
            self.assertEqual(voice.wake_model(), folder / voice.WAKE_MODEL)
            self.assertEqual(voice.vad_model(), folder / 'silero_vad.onnx')
            spotter = voice.WakeWord().spotter
        urlopen.assert_not_called()
        self.assertEqual(spotter.config['encoder'], str(folder / voice.WAKE_MODEL / voice.WAKE_PARTS['encoder']))
        self.assertIn(' @Hey_Jasmine\n', spotter.keywords)
        self.assertIn(' @小茗同学\n', spotter.keywords)
        self.assertFalse(Path(spotter.config['keywords_file']).exists(), 'Its keywords are kept only while it is made')
        self.assertEqual(contents(), before, 'Nothing is written to the models folder')
        self.assertFalse(self.data.exists(), 'Nor to the data folder')

    def test_a_models_folder_without_a_model_says_which_and_nothing_is_downloaded(self):
        folder = self.models(f'{voice.WAKE_MODEL}/tokens.txt', 'silero_vad.onnx')
        with patch.dict('os.environ', {'PIANASOUL_MODELS': str(folder)}), patch('urllib.request.urlopen') as urlopen:
            for model in (voice.wake_model, voice.vad_model):
                with self.assertRaises(RuntimeError) as raised:
                    model()
                self.assertEqual(str(raised.exception), f'the models folder {folder} (PIANASOUL_MODELS) lacks '
                                 f'{voice.WAKE_MODEL}/tokens.txt, silero_vad.onnx')
        urlopen.assert_not_called()

    def test_without_a_models_folder_they_are_downloaded_into_the_data_folder_once(self):
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode='w:bz2') as tar:
            for name in [*voice.WAKE_PARTS.values(), 'encoder-epoch-13-avg-2-chunk-16-left-64.int8.onnx']:
                member = tarfile.TarInfo(f'{voice.WAKE_MODEL}/{name}')
                member.size = len(name)
                tar.addfile(member, io.BytesIO(name.encode()))
        answers = {voice.WAKE_MODEL_URL: archive.getvalue(), voice.VAD_URL: b'silero'}
        with patch.dict('os.environ', {'PIANASOUL_MODELS': ''}), patch('builtins.print'), \
                patch('urllib.request.urlopen', side_effect=lambda url, timeout: io.BytesIO(answers[url])) as urlopen:
            for _ in range(2):
                wake, vad = voice.wake_model(), voice.vad_model()
        self.assertEqual(urlopen.call_count, 2, 'Each is downloaded once')
        self.assertEqual(wake, self.data / 'models' / voice.WAKE_MODEL)
        self.assertEqual(sorted(p.name for p in wake.iterdir()), sorted(voice.WAKE_PARTS.values()),
                         'Only the files needed are kept')
        self.assertEqual((vad, vad.read_bytes()), (self.data / 'models' / 'silero_vad.onnx', b'silero'))


if __name__ == '__main__':
    unittest.main()
