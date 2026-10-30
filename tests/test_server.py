"""The phone app's server: who may use it, what it answers, and her ears and voice; no paid APIs or devices."""
import base64
import contextlib
import io
from pathlib import Path
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import mido
import numpy as np
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from pianasoul import server, voice
from pianasoul.chat import Worker
from pianasoul.playback import Playback
from test_app import Scripted, make_midi

ORIGIN = {'origin': 'http://testserver'}


def signed_in(client):
    client.cookies.set(server.COOKIE, server.TOKEN)
    return client


class AppTests(unittest.TestCase):
    """A real Core on a library of its own, with a scripted chat model and no MIDI going out."""

    def setUp(self):
        from pianasoul.core import Core
        from pianasoul.library import Library
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        lib = Library(root / 'library.db')
        self.ids = [lib.add(make_midi(root / 'MIDI Files' / f'{t}.mid'), t)['id']
                    for t in ('周杰伦 - 晴天 (MappleZS)', '周杰伦 - 搁浅 (MappleZS)')]
        for patcher in (patch.object(Playback, 'play'), patch.object(Playback, 'check_connection')):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.core = Core(lib=lib, chat=(Scripted(), 'test'))
        self.core.playback.port = Mock()
        self.addCleanup(lib.close)
        self.addCleanup(self.core.close)
        patcher = patch.object(server, 'core', self.core)
        patcher.start()
        self.addCleanup(patcher.stop)
        quiet = contextlib.redirect_stdout(io.StringIO())
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)
        self.client = TestClient(server.app, headers=ORIGIN)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def test_everything_but_the_page_needs_the_cookie(self):
        self.assertEqual(self.client.get('/api/state').status_code, 401)
        self.assertEqual(self.client.post('/api/session', json={'token': 'wrong'}).status_code, 401)
        response = self.client.post('/api/session', json={'token': server.TOKEN})
        self.assertEqual(response.status_code, 200)
        cookie = response.headers['set-cookie']
        for part in ('HttpOnly', 'Secure', 'SameSite=strict'):
            self.assertIn(part.lower(), cookie.lower())
        signed_in(self.client)
        self.assertEqual(self.client.get('/api/state').status_code, 200)
        elsewhere = self.client.post('/api/playback', json={'action': 'toggle'}, headers={'origin': 'https://evil.example'})
        self.assertEqual(elsewhere.status_code, 403, 'Changes come from the page only')

    def test_the_page_and_its_files_and_nothing_else(self):
        self.assertEqual(self.client.get('/').headers['cache-control'], 'no-store')
        for path in server.WEB.iterdir():
            if path.suffix in server.ASSET_TYPES:
                self.assertEqual(self.client.get('/' + path.name).status_code, 200, path.name)
        for hidden in ('CUE_SOURCES.md', 'server.py', '.env', 'docs', 'openapi.json', '..%2Fserver.py'):
            self.assertEqual(self.client.get('/' + hidden).status_code, 404, hidden)
        self.assertNotIn('start_url', self.client.get('/manifest.webmanifest').json(),
                         'Add to Home Screen must keep the address with its #token')

    def test_state_has_what_the_page_shows(self):
        state = signed_in(self.client).get('/api/state').json()
        self.assertEqual({r['piece'] for r in state['recordings']}, {'晴天', '搁浅'})
        for key in ('jobs', 'playback', 'conversations', 'current', 'busy', 'settings', 'devices', 'version', 'search'):
            self.assertIn(key, state)

    def test_tapping_a_piece_plays_it_in_its_list_and_the_player_works(self):
        a, b = self.ids
        signed_in(self.client)
        snap = self.client.post('/api/play', json={'id': a, 'ids': self.ids, 'scope': {'kind': 'library'}}).json()
        self.assertEqual((snap['id'], snap['state'], snap['ids']), (a, 'playing', self.ids))
        self.assertEqual(self.client.post('/api/playback', json={'action': 'next'}).json()['id'], b)
        self.assertEqual(self.client.post('/api/playback', json={'action': 'mode', 'value': 'one'}).json()['mode'], 'one')
        self.assertEqual(self.client.post('/api/playback', json={'action': 'toggle'}).json()['state'], 'paused')
        self.assertEqual(self.client.post('/api/playback', json={'action': 'seek', 'value': 0.5}).json()['position'], 0.5)
        self.assertEqual(self.client.post('/api/playback', json={'action': 'jump'}).status_code, 400)
        with patch.object(self.core.playback, 'check_connection', side_effect=LookupError('gone')):
            self.assertEqual(self.client.post('/api/playback', json={'action': 'toggle'}).json(), {'error': 'piano'})
        stopped = self.client.post('/api/playback', json={'action': 'stop'}).json()
        self.assertEqual((stopped['id'], stopped['state'], stopped['position']), (b, 'stopped', 0))
        self.assertEqual(self.client.post('/api/play', json={'id': b, 'ids': self.ids}).json()['state'], 'playing',
                         'Played again, from the start')

    def test_the_volume_slider_sets_the_pianos_volume(self):
        signed_in(self.client)
        self.assertEqual(self.client.get('/api/state').json()['playback']['volume'], 80, 'Until one is chosen')
        self.assertEqual(self.client.post('/api/playback', json={'action': 'volume', 'value': 37.4}).json()['volume'], 37)
        self.core.playback.port.send.assert_called_with(mido.Message('control_change', channel=15, control=7, value=47))
        for wrong in (101, -1, 'loud', None):
            self.assertEqual(self.client.post('/api/playback', json={'action': 'volume', 'value': wrong}).status_code, 400)
        self.assertEqual(self.core.playback.volume, 37)

    def test_without_the_piano_a_tap_has_her_say_so_in_the_conversation_once(self):
        signed_in(self.client)
        with patch.object(self.core.playback, 'check_connection', side_effect=LookupError('gone')):
            for _ in range(3):  # tapped again and again
                self.assertEqual(self.client.post('/api/play', json={'id': self.ids[0]}).status_code, 409)
        said = self.core.conversations.messages(self.core.assistant.conversation_id)
        self.assertEqual([(m['role'], m['text']) for m in said],
                         [('assistant', self.core.assistant.playback_reply({'error': 'playback_connection_unavailable'}))])

    def test_a_piece_is_renamed_favorited_and_deleted(self):
        a, b = self.ids
        signed_in(self.client)
        rec = self.client.patch(f'/api/recordings/{a}', json={'piece': '晴天', 'artist': 'Jay Chou', 'performer': 'X'}).json()
        self.assertEqual((rec['artist'], rec['piece'], rec['performer']), ('Jay Chou', '晴天', 'X'))
        self.assertTrue(self.client.patch(f'/api/recordings/{a}', json={'favorite': True}).json()['favorited_at'])
        self.client.post('/api/play', json={'id': a, 'ids': self.ids})
        self.client.request('DELETE', '/api/recordings', json={'ids': [a]})
        snap = self.client.get('/api/state').json()
        self.assertEqual([r['id'] for r in snap['recordings']], [b])
        self.assertIsNone(snap['playback']['id'], 'It was playing: nothing is now')

    def test_a_typed_message_is_answered_in_its_conversation(self):
        signed_in(self.client)
        done = threading.Event()
        self.core.listeners.append(lambda e: e['type'] == 'conversations' and done.set())
        self.client.post('/api/messages', json={'text': '你好'})
        self.assertTrue(done.wait(2))
        conversations = self.client.get('/api/conversations').json()
        cid = conversations['current']
        messages = self.client.get(f'/api/conversations/{cid}').json()['messages']
        self.assertEqual([(m['role'], m['text']) for m in messages], [('user', '你好'), ('assistant', '好。')])
        self.client.patch(f'/api/conversations/{cid}', json={'title': '打招呼', 'pinned': True})
        self.assertEqual(self.client.get(f'/api/conversations/{cid}').json()['title'], '打招呼')
        done.clear()
        self.client.delete(f'/api/conversations/{cid}')
        self.assertTrue(done.wait(2))
        self.assertEqual(self.client.get('/api/conversations').json(), {'conversations': [], 'current': None},
                         'Deleting the current conversation is starting afresh')

    def test_search_finds_conversations_by_what_was_said(self):
        signed_in(self.client).post('/api/messages', json={'text': '弹一下晴天'})
        for _ in range(50):
            if self.core.conversations.search('晴天'):
                break
            time.sleep(.05)
        found = self.client.get('/api/search', params={'q': '晴天'}).json()
        self.assertEqual(found[0]['message']['text'], '弹一下晴天')
        self.assertEqual(self.client.get('/api/search', params={'q': ' '}).json(), [])

    def test_settings_take_only_what_exists(self):
        signed_in(self.client)
        self.assertEqual(self.client.patch('/api/settings', json={'language': 'en', 'appearance': 'light'}).json(),
                         {'language': 'en', 'appearance': 'light'})
        self.assertEqual(self.client.patch('/api/settings', json={'language': 'fr'}).json()['language'], 'en')

    def test_the_midi_file_is_shared_by_its_title(self):
        response = signed_in(self.client).get(f'/api/recordings/{self.ids[0]}/midi')
        self.assertEqual(response.headers['content-type'], 'audio/midi')
        self.assertIn('filename*=utf-8', response.headers['content-disposition'])
        notes = self.client.get(f'/api/recordings/{self.ids[0]}/notes').json()
        self.assertEqual(notes, [[0.0, 1.0, 60, 64]])

    def test_a_search_offers_only_the_piano_versions_she_would_choose(self):
        from pianasoul import youtube
        videos = [('aaaaaaaaaaa', '江南 钢琴版', 'Kassia'), ('bbbbbbbbbbb', '林俊杰 江南 官方 MV', 'Taihe Music'),
                  ('ccccccccccc', '江南 Piano Tutorial', 'Lessons'), ('ddddddddddd', 'River South', 'Pianella Piano')]
        asked = []

        def get(endpoint, params, key):
            asked.append(params.get('q'))
            if endpoint == 'search':
                return {'items': [{'id': {'videoId': v}} for v, _, _ in videos]}
            return {'items': [{'id': v, 'snippet': {'title': t, 'channelTitle': c},
                               'contentDetails': {'duration': 'PT4M'}} for v, t, c in videos]}
        with patch.object(youtube, 'api_key', return_value='test'), patch.object(youtube, '_get', side_effect=get):
            found = signed_in(self.client).get('/api/youtube', params={'q': '江南'}).json()
        self.assertEqual(asked[0], '江南 piano')
        self.assertEqual([r['video_title'] for r in found], ['江南 钢琴版', 'River South'])
        self.assertNotIn('flags', found[0])
        videos[:] = [('eeeeeeeeeee', 'Adele - Easy On Me (Piano Cover)', 'Pianella Piano'),
                     ('fffffffffff', 'Easy On Me - Easy Piano Tutorial', 'Lessons')]
        with patch.object(youtube, 'api_key', return_value='test'), patch.object(youtube, '_get', side_effect=get):
            found = signed_in(self.client).get('/api/youtube', params={'q': 'Easy On Me'}).json()
        self.assertEqual([r['video_title'] for r in found], ['Adele - Easy On Me (Piano Cover)'],
                         'A warning word in the song\'s own name is not a warning')

    def test_a_pasted_link_is_that_one_video_with_its_length_and_channel(self):
        from pianasoul import youtube
        pages = [{'items': [{'id': 'abcdefghijk', 'contentDetails': {'duration': 'PT4M2S'},
                             'snippet': {'title': '晴天 钢琴版', 'channelTitle': 'MappleZS', 'channelId': 'c'}}]},
                 {'items': [{'id': 'c', 'snippet': {'thumbnails': {'default': {'url': 'https://yt3/c.jpg'}}}}]}]
        with patch.object(youtube, 'api_key', return_value='test'), patch.object(youtube, '_get', side_effect=pages):
            found = signed_in(self.client).get('/api/youtube', params={'q': 'https://youtu.be/abcdefghijk?si=x'}).json()
        self.assertEqual([(r['url'], r['duration_s'], r['channel_avatar']) for r in found],
                         [('https://www.youtube.com/watch?v=abcdefghijk', 242, 'https://yt3/c.jpg')])

    def test_events_tell_the_page_what_changed(self):
        signed_in(self.client)
        with self.client.websocket_connect('/events', headers=ORIGIN) as socket:
            self.client.post('/api/play', json={'id': self.ids[0], 'ids': self.ids})
            event = socket.receive_json()
            self.assertEqual(event['type'], 'playback')
        with self.assertRaises(WebSocketDisconnect):
            with TestClient(server.app).websocket_connect('/events', headers=ORIGIN) as socket:
                socket.receive_json()


class VoiceTests(unittest.TestCase):
    def setUp(self):
        self.client = signed_in(TestClient(server.app))
        stdout = patch('sys.stdout', new_callable=io.StringIO)  # the app's lines as a phone comes and goes
        stdout.start()
        self.addCleanup(stdout.stop)

    def test_her_voice_goes_out_in_pieces_and_she_has_finished_when_it_has_been_heard(self):
        events, hushed = [], threading.Event()
        phone = server.Phone()
        phone.connect(events.append)
        started = time.monotonic()
        phone.speak(iter([np.arange(2400, dtype=np.int16), np.zeros(1200, dtype=np.int16)]), hushed)
        self.assertGreaterEqual(time.monotonic() - started, .2, '0.15 s of speech, and room for the network')
        self.assertEqual([e['type'] for e in events], ['audio', 'audio'])
        self.assertEqual(np.frombuffer(base64.b64decode(events[0]['data']), dtype='<i2')[:3].tolist(), [0, 1, 2])
        hushed.set()
        started = time.monotonic()
        phone.speak(iter([np.zeros(240000, dtype=np.int16)]), hushed)
        self.assertLess(time.monotonic() - started, .1, 'Hushed, nothing more is said or waited for')
        phone.disconnect()
        phone.status('waiting')
        self.assertEqual(len(events), 2, 'No phone, nowhere to go')

    def test_only_the_page_with_the_cookie_gets_her_ears(self):
        with patch.object(server, 'her') as her:
            for client, origin in [(TestClient(server.app), ORIGIN), (self.client, {'origin': 'https://other.example'})]:
                with self.assertRaises(WebSocketDisconnect):
                    with client.websocket_connect('/voice', headers=origin) as socket:
                        socket.receive_json()
            her.assert_not_called()

    def her(self, threads=None, answering=None):
        """A stand-in for her, kept like the real one for the server's life: quiet until a phone is with her. A
        frame starts a request, which answers once answering is set."""
        phone = server.Phone()

        class Ears:
            state = 'waiting'
            hushed = threading.Event()

            def status(self, state):
                self.state = state
                phone.status(state)

            def frame(self, pcm):
                (threads if threads is not None else []).append(threading.get_ident())
                phone.send({'type': 'frame', 'size': len(pcm)})
                if answering is not None:
                    self.status('thinking')
                    answering.wait(10)
                    self.status('speaking')
                    phone.speak([np.zeros(240, dtype=np.int16)], self.hushed)
                    phone.turn_end(self, False)

            def resume(self):
                self.status('conversation')

            def hush(self):
                if self.hushed.is_set():
                    phone.send({'type': 'hushed'})

            def unhush(self):
                if not self.hushed.is_set() and self.state != 'speaking':
                    phone.send({'type': 'unhushed'})

        ears = Ears()
        ears.hushed.set()
        return patch.object(server, '_her', (ears, phone, Worker()))

    def test_frames_and_commands_reach_her_in_order_on_one_thread(self):
        threads = []
        with self.her(threads), self.client.websocket_connect('/voice', headers=ORIGIN) as socket:
            self.assertEqual(socket.receive_json()['type'], 'ready')
            self.assertEqual(socket.receive_json(), {'type': 'state', 'state': 'waiting'})
            socket.send_bytes(b'\0' * voice.FRAME * 2)  # quiet until the phone is with her: not even passed on
            socket.send_json({'type': 'continue_session'})
            self.assertEqual(socket.receive_json()['type'], 'unhushed')
            socket.send_bytes(b'\0' * voice.FRAME * 2)
            self.assertEqual(socket.receive_json()['size'], voice.FRAME, 'PCM16 samples')
            socket.send_json({'type': 'pause_session'})
            self.assertEqual(socket.receive_json()['type'], 'hushed')
            socket.send_bytes(b'\0' * voice.FRAME * 2)
            socket.send_json({'type': 'continue_session'})
            self.assertEqual(socket.receive_json()['type'], 'unhushed')
            socket.send_bytes(b'\0' * voice.FRAME * 2)
            self.assertEqual(socket.receive_json()['type'], 'frame')
        self.assertEqual(len(threads), 2)
        self.assertEqual(len(set(threads)), 1)

    def test_a_reply_she_is_still_making_is_heard_by_the_phone_that_is_back(self):
        answering = threading.Event()
        with self.her(answering=answering):
            with self.client.websocket_connect('/voice', headers=ORIGIN) as first:
                first.receive_json(), first.receive_json()
                first.send_json({'type': 'continue_session'})
                self.assertEqual(first.receive_json()['type'], 'unhushed')
                first.send_bytes(b'\0' * voice.FRAME * 2)  # a request, then the screen locks
                self.assertEqual(first.receive_json()['type'], 'frame')
                self.assertEqual(first.receive_json(), {'type': 'state', 'state': 'thinking'})
            with self.client.websocket_connect('/voice', headers=ORIGIN) as second:
                self.assertEqual(second.receive_json()['type'], 'ready')
                self.assertEqual(second.receive_json(), {'type': 'state', 'state': 'thinking'}, 'Still busy with it')
                second.send_json({'type': 'continue_session'})
                time.sleep(.1)
                answering.set()
                self.assertEqual(second.receive_json(), {'type': 'state', 'state': 'speaking'})
                self.assertEqual(second.receive_json()['type'], 'audio', 'Heard by the phone that is back')
                self.assertEqual(second.receive_json(), {'type': 'turn_end', 'sleep': False})
                second.send_json({'type': 'resume'})
                self.assertEqual(second.receive_json(), {'type': 'state', 'state': 'conversation'})

    def test_a_second_phone_is_turned_away_and_unknown_messages_close(self):
        with self.her():
            with self.client.websocket_connect('/voice', headers=ORIGIN) as first:
                self.assertEqual(first.receive_json()['type'], 'ready')
                with self.client.websocket_connect('/voice', headers=ORIGIN) as second:
                    with self.assertRaises(WebSocketDisconnect) as error:
                        second.receive_json()
                    self.assertEqual(error.exception.code, 1013)
                first.send_json({'type': 'text', 'text': 'old client'})
                with self.assertRaises(WebSocketDisconnect) as error:
                    while True:
                        first.receive_json()
                self.assertEqual(error.exception.code, 1008)
            with self.client.websocket_connect('/voice', headers=ORIGIN) as third:
                self.assertEqual(third.receive_json()['type'], 'ready')


class StartTests(unittest.TestCase):
    def test_one_command_serves_the_iphone_at_the_macs_address(self):
        parts = SimpleNamespace(stt=SimpleNamespace(label='OpenAI x'), speaker=SimpleNamespace(label='ElevenLabs y'))
        with tempfile.TemporaryDirectory() as tmp:
            with patch('pianasoul.library.CACHE_DIR', Path(tmp)), \
                 patch('pianasoul.core.Core', return_value=Mock(label='test')), \
                 patch('pianasoul.core.version', return_value={'commit': 'abc', 'repo': ''}), \
                 patch.object(server, 'voice_parts', return_value=SimpleNamespace(
                     describe=lambda model, name, **more: voice.VoiceParts.describe(parts, model, name, **more),
                     wake=SimpleNamespace(words=['小茗同学', 'Hey Jasmine']))), \
                 patch('pianasoul.web_tls.prepare_https', return_value=('192.168.1.20', True)), \
                 patch('pianasoul.web_tls.local_name', return_value='Test-Mac.local'), \
                 patch('uvicorn.run') as run, patch('builtins.print') as shown, \
                 patch.object(server, 'TOKEN', server.TOKEN), patch.object(server, 'core', None), \
                 patch.object(server.sys, 'platform', 'darwin'), patch.object(server.subprocess, 'run') as opened:
                server.main([])
                token = server.TOKEN
        self.assertEqual(run.call_args.kwargs['host'], '0.0.0.0')
        self.assertEqual(run.call_args.kwargs['ssl_certfile'], str(Path(tmp) / 'phone-tls' / 'cert.pem'))
        self.assertLessEqual(run.call_args.kwargs['timeout_graceful_shutdown'], 3, 'One Control+C is enough')
        printed = ' '.join(str(c.args[0]) for c in shown.call_args_list if c.args)
        self.assertIn(f'https://192.168.1.20:8765/#token={token}', printed)
        self.assertIn('"PianaSoul on Test-Mac"', printed, 'The certificate to trust, by its name')
        opened.assert_called_once_with(['open', '-R', str(Path(tmp) / 'phone-tls' / 'PianaSoul-CA.cer')], check=False)

    def test_token_is_kept_privately_across_restarts(self):
        with tempfile.TemporaryDirectory() as tmp, patch('pianasoul.library.CACHE_DIR', Path(tmp)):
            first = server.saved_token()
            self.assertGreaterEqual(len(first), 32)
            self.assertEqual(server.saved_token(), first)
            self.assertEqual((Path(tmp) / 'phone-token').stat().st_mode & 0o777, 0o600)
            (Path(tmp) / 'phone-token').write_text('')  # emptied: never a token that lets anyone in
            self.assertGreaterEqual(len(server.saved_token()), 32)


if __name__ == '__main__':
    unittest.main()
