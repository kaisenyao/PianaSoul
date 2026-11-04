"""The phone app's server: who may use it, what it answers, and her ears and voice; no paid APIs or devices."""
import base64
import contextlib
import io
import json
import os
from pathlib import Path
import signal
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import mido
import numpy as np
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import places  # noqa: F401  (first: the library, data and cache folders are throwaway ones)
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
        lib = Library(root / 'library.db', root / 'Music')
        self.ids = [lib.add(make_midi(root / 'Music' / f'{t}.mid'), t)['id']
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
        for key in ('jobs', 'playback', 'conversations', 'current', 'busy', 'settings', 'devices', 'version', 'features'):
            self.assertIn(key, state)
        self.assertEqual(set(state['features']), {'transcribe', 'assistant'}, 'Online search comes with transcription')
        self.assertTrue(state['features']['assistant'])

    def test_tapping_a_piece_plays_it_in_its_list_and_the_player_works(self):
        a, b = self.ids
        signed_in(self.client)
        snap = self.client.post('/api/play', json={'id': a, 'ids': self.ids, 'scope': {'kind': 'library'}}).json()
        self.assertEqual((snap['id'], snap['state'], snap['ids']), (a, 'playing', self.ids))
        self.assertEqual(self.client.post('/api/playback', json={'action': 'next'}).json()['id'], b)
        self.assertEqual(self.client.post('/api/playback', json={'action': 'mode', 'value': 'one'}).json()['mode'], 'one')
        self.assertEqual(self.client.post('/api/playback', json={'action': 'toggle'}).json()['state'], 'paused')
        self.assertEqual(self.client.post('/api/playback', json={'action': 'pause'}).json()['state'], 'paused',
                         'As the button showed it: a page behind on what plays never does the opposite')
        self.assertEqual(self.client.post('/api/playback', json={'action': 'play'}).json()['state'], 'playing')
        self.assertEqual(self.client.post('/api/playback', json={'action': 'play'}).json()['state'], 'playing')
        self.client.post('/api/playback', json={'action': 'pause'})
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
                         {'language': 'en', 'appearance': 'light', 'output': None})
        self.assertEqual(self.client.patch('/api/settings', json={'language': 'fr'}).json()['language'], 'en')

    def test_the_midi_file_is_shared_by_its_title(self):
        response = signed_in(self.client).get(f'/api/recordings/{self.ids[0]}/midi')
        self.assertEqual(response.headers['content-type'], 'audio/midi')
        self.assertIn('filename*=utf-8', response.headers['content-disposition'])
        notes = self.client.get(f'/api/recordings/{self.ids[0]}/notes').json()
        self.assertEqual(notes, [[0.0, 1.0, 60, 64]])

    def test_a_search_offers_the_piano_versions_she_would_choose_first(self):
        from pianasoul import online
        videos = [('aaaaaaaaaaa', 'Yiruma - River Flows in You', 'YIRUMA official'),
                  ('bbbbbbbbbbb', 'River Flows in You 钢琴版', 'Kassia'),
                  ('ccccccccccc', 'River Flows in You Piano Tutorial', 'Lessons'),
                  ('ddddddddddd', 'River Flows in You', 'Pianella Piano')]
        asked = []

        def extract(url, **options):  # yt-dlp's list of what YouTube found, in its order
            asked.append(url)
            return {'entries': [{'id': v, 'title': t, 'channel': c, 'duration': 240} for v, t, c in videos]}
        with patch.object(online, '_extract', side_effect=extract):
            found = signed_in(self.client).get('/api/online', params={'q': 'River Flows in You'}).json()
        self.assertEqual(asked[0], 'ytsearch15:River Flows in You piano')
        self.assertEqual([r['video_title'] for r in found],
                         ['River Flows in You 钢琴版', 'River Flows in You', 'Yiruma - River Flows in You'],
                         'With few piano versions, the others follow them: here the composer\'s own')
        self.assertNotIn('flags', found[0])
        videos[:] = [(f'{n:011d}', f'江南 Piano Cover {n}', 'Covers') for n in range(5)] + [
            ('bbbbbbbbbbb', '林俊杰 江南 官方 MV', 'Taihe Music'), ('ccccccccccc', '江南 簡易鋼琴教學', '簡單鋼琴')]
        with patch.object(online, '_extract', side_effect=extract):
            found = signed_in(self.client).get('/api/online', params={'q': '江南'}).json()
        self.assertEqual(len(found), 5, 'Enough piano versions: the song\'s own, sung, is not offered, nor a lesson')
        videos[:] = [('eeeeeeeeeee', 'Adele - Easy On Me (Piano Cover)', 'Pianella Piano'),
                     ('fffffffffff', 'Easy On Me - Easy Piano Tutorial', 'Lessons')]
        with patch.object(online, '_extract', side_effect=extract):
            found = signed_in(self.client).get('/api/online', params={'q': 'Easy On Me'}).json()
        self.assertEqual([r['video_title'] for r in found], ['Adele - Easy On Me (Piano Cover)'],
                         'A warning word in the song\'s own name is not a warning')

    def test_a_search_that_fails_says_why(self):
        from yt_dlp.utils import ExtractorError
        from pianasoul import online

        def pages():  # a search's pages are fetched as its entries are read
            raise ExtractorError('Unable to download API page: timed out')
            yield

        class Offline:
            def __init__(self, options):
                self.options = options

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def extract_info(self, url, download, process):
                return {'entries': pages()}
        with patch('yt_dlp.YoutubeDL', Offline), self.assertRaises(online.OnlineError) as failed:
            online.search('晴天')
        self.assertIn('timed out', str(failed.exception))

    def test_a_song_named_simple_is_not_an_easy_version(self):
        from pianasoul import online
        self.assertEqual(online.flags_for('周杰倫 簡單愛 鋼琴版 Piano Cover', 'Pianist', '', 240, '简单爱'), [])
        self.assertEqual(online.flags_for('简单爱 钢琴简单版', 'Pianist', '', 240, '简单爱'), ['easy'])

    def test_a_pasted_link_is_that_one_video_with_its_length_and_channel(self):
        from pianasoul import online
        channel = 'https://www.youtube.com/channel/c'

        def extract(url, **options):  # the video's page, then its channel's: a banner, and the square picture
            if url == channel:
                return {'thumbnails': [{'url': 'https://yt3/banner.jpg', 'width': 1060, 'height': 175},
                                       {'url': 'https://yt3/c.jpg', 'width': 900, 'height': 900}]}
            return {'title': '晴天 钢琴版', 'channel': 'MappleZS', 'channel_url': channel, 'duration': 242,
                    'upload_date': '20240102', 'thumbnails': [{'url': 'https://i.ytimg.com/s.jpg', 'width': 120},
                                                              {'url': 'https://i.ytimg.com/l.jpg', 'width': 1280}]}
        with patch.object(online, '_extract', side_effect=extract):
            found = signed_in(self.client).get('/api/online', params={'q': 'https://youtu.be/abcdefghijk?si=x'}).json()
        self.assertEqual([(r['url'], r['duration_s'], r['channel_avatar'], r['thumbnail'], r['published']) for r in found],
                         [('https://www.youtube.com/watch?v=abcdefghijk', 242, 'https://yt3/c.jpg',
                           'https://i.ytimg.com/l.jpg', '2024-01-02')])

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
        with_her = patch.object(server, 'core', SimpleNamespace(assistant=Mock()))  # her itself: server.her, patched
        with_her.start()
        self.addCleanup(with_her.stop)
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

            def turn_on(self):
                if not self.hushed.is_set() and self.state != 'speaking':
                    phone.send({'type': 'turned on'})

        ears = Ears()
        ears.hushed.set()
        return patch.object(server, '_her', (ears, phone, Worker()))

    def test_pairing_anew_closes_the_connections_open_and_lets_none_in_with_the_old_cookie(self):
        old = server.TOKEN
        mac = TestClient(server.app, headers=ORIGIN, client=('127.0.0.1', 50000))
        core = SimpleNamespace(assistant=Mock(), listeners=[])
        with self.her(), patch.object(server, 'core', core), patch.object(server, 'TOKEN', old), \
                patch.object(server, 'saved_token', return_value='the new token'), patch.object(server, 'write_pairing'):
            with self.client.websocket_connect('/events', headers=ORIGIN) as events, \
                    self.client.websocket_connect('/voice', headers=ORIGIN) as her:
                her.receive_json(), her.receive_json()  # ready, and her state
                self.assertEqual(signed_in(mac).post('/api/pairing/reset').json(), {'ok': True})
                for socket in (events, her):
                    with self.assertRaises(WebSocketDisconnect) as closed:
                        socket.receive_json()
                    self.assertEqual(closed.exception.code, 1008)
            self.assertEqual(self.client.get('/api/state').status_code, 401, 'The old cookie opens nothing')
            for path in ('/events', '/voice'):
                with self.assertRaises(WebSocketDisconnect, msg=path), \
                        self.client.websocket_connect(path, headers=ORIGIN) as socket:
                    socket.receive_json()
        self.assertEqual(server._open, set())

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
            socket.send_json({'type': 'pause_session'})
            self.assertEqual(socket.receive_json()['type'], 'hushed')
            socket.send_json({'type': 'start_session'})  # tapped on: with her at once, and no name needed
            self.assertEqual(socket.receive_json()['type'], 'turned on')
            socket.send_bytes(b'\0' * voice.FRAME * 2)
            self.assertEqual(socket.receive_json()['type'], 'frame')
        self.assertEqual(len(threads), 3)
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


class WithoutHerTests(unittest.TestCase):
    """A Mac without her (no key, or no advanced extra): the library, the piano and the page work as ever; what
    needs her, or transcription, says so."""

    def setUp(self):
        from pianasoul.core import Core
        from pianasoul.library import Library
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        lib = Library(root / 'library.db', root / 'Music')
        self.addCleanup(lib.close)
        self.ids = [lib.add(make_midi(root / 'Music' / f'{t}.mid'), t)['id'] for t in ('晴天', '搁浅')]
        self.core = Core(lib=lib, assistant=False)
        self.addCleanup(self.core.close)
        self.core.playback.port = Mock()
        for patcher in (patch.object(server, 'core', self.core), patch.object(Playback, 'play'),
                        contextlib.redirect_stdout(io.StringIO())):
            patcher.__enter__()
            self.addCleanup(patcher.__exit__, None, None, None)
        self.client = signed_in(TestClient(server.app, headers=ORIGIN))

    def test_the_library_and_the_piano_work(self):
        state = self.client.get('/api/state').json()
        self.assertFalse(state['features']['assistant'])
        self.assertEqual((state['conversations'], state['current']), ([], None))
        self.assertEqual(len(state['recordings']), 2)
        with patch.object(Playback, 'check_connection'):
            self.assertEqual(self.client.post('/api/play', json={'id': self.ids[0], 'ids': self.ids}).json()['state'],
                             'playing')
        self.assertEqual(self.client.patch(f'/api/recordings/{self.ids[1]}', json={'favorite': True}).status_code, 200)

    def test_without_the_piano_the_page_hears_why(self):
        with patch.object(Playback, 'check_connection', side_effect=LookupError('no MIDI output')):
            answer = self.client.post('/api/play', json={'id': self.ids[0]})
        self.assertEqual((answer.status_code, answer.json()), (409, {'error': 'piano'}))
        self.assertIsNone(self.core.playback.current_id)

    def test_what_needs_her_says_she_is_off(self):
        for method, path, body in (('post', '/api/messages', {'text': '晴天'}), ('get', '/api/conversations', None),
                                   ('post', '/api/conversations/new', None), ('get', '/api/conversations/1', None),
                                   ('get', '/api/search?q=x', None), ('post', '/api/dictation', None)):
            answer = getattr(self.client, method)(path, **({'json': body} if body else {}))
            self.assertEqual((answer.status_code, answer.json()), (409, {'error': 'assistant_off'}), path)
        with self.assertRaises(WebSocketDisconnect), self.client.websocket_connect('/voice') as socket:
            socket.receive_json()

    def test_what_needs_transcription_says_it_is_off(self):
        with patch('pianasoul.core.installed', return_value=False):
            self.assertFalse(self.client.get('/api/state').json()['features']['transcribe'])
            for method, path, body in (('get', '/api/online?q=x', None),
                                       ('post', '/api/downloads', {'url': 'https://youtu.be/abcdefghijk'})):
                answer = getattr(self.client, method)(path, **({'json': body} if body else {}))
                self.assertEqual((answer.status_code, answer.json()), (409, {'error': 'transcribe_off'}), path)
        self.assertEqual(self.core.downloads.snapshot(), [])

    def test_transcription_turned_off_is_off_though_installed(self):
        """The Mac app's Online Search & Transcription unticked (PIANASOUL_TRANSCRIBE=0): what needs it is off, as
        without the advanced extra, and nothing is downloaded."""
        with patch('pianasoul.core.installed', return_value=True), patch.dict(os.environ, {'PIANASOUL_TRANSCRIBE': '0'}):
            self.assertFalse(self.client.get('/api/state').json()['features']['transcribe'])
            for method, path, body in (('get', '/api/online?q=x', None),
                                       ('post', '/api/downloads', {'url': 'https://youtu.be/abcdefghijk'})):
                answer = getattr(self.client, method)(path, **({'json': body} if body else {}))
                self.assertEqual((answer.status_code, answer.json()), (409, {'error': 'transcribe_off'}), path)
        self.assertEqual(self.core.downloads.snapshot(), [])


class UploadTests(unittest.TestCase):
    """Files from the phone go into the library folder as if put there by hand; deleting sends them to the Trash."""

    def setUp(self):
        from pianasoul.core import Core
        from pianasoul.library import Library
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.music = self.root / 'Music'
        lib = Library(self.root / 'library.db', self.music)
        self.addCleanup(lib.close)
        self.core = Core(lib=lib, assistant=False)
        self.addCleanup(self.core.close)
        for patcher in (patch.object(server, 'core', self.core), patch('pianasoul.core.installed', return_value=False),
                        contextlib.redirect_stdout(io.StringIO())):
            patcher.__enter__()
            self.addCleanup(patcher.__exit__, None, None, None)
        self.client = signed_in(TestClient(server.app, headers=ORIGIN))

    def send(self, name, content):
        return self.client.post('/api/uploads', params={'name': name}, content=content)

    def left_over(self):
        return [p.name for p in self.music.rglob('.*')]

    def test_a_midi_file_becomes_a_piece_of_yours_in_uploaded(self):
        midi = make_midi(self.root / 'x.mid').read_bytes()
        self.assertFalse((self.music / 'Uploaded').exists(), 'Made with the first file, not before')
        self.assertEqual(self.send('Clair de Lune.mid', midi).json(), {'name': 'Clair de Lune.mid'})
        self.assertEqual(self.send('Clair de Lune.mid', midi).json(), {'name': 'Clair de Lune (2).mid'},
                         'A name taken: as Finder would')
        self.assertEqual(sorted(p.name for p in (self.music / 'Uploaded').iterdir()),
                         ['Clair de Lune (2).mid', 'Clair de Lune.mid'])
        pieces = self.client.get('/api/state').json()['recordings']
        self.assertEqual(sorted((r['title'], r['origin']) for r in pieces),
                         [('Clair de Lune', 'uploaded'), ('Clair de Lune (2)', 'uploaded')])
        self.assertEqual(self.send('../../evil:name.mid', midi).json(), {'name': 'evil_name.mid'}, 'Only a name')
        self.assertTrue((self.music / 'Uploaded' / 'evil_name.mid').is_file())

    def test_a_recording_waits_to_be_transcribed(self):
        self.assertEqual(self.send('Nocturne.mp3', b'ID3' + b'\0' * 100).status_code, 200)
        self.assertEqual([(a['name'], a['status']) for a in self.client.get('/api/state').json()['audio']],
                         [('Nocturne', 'waiting')])

    def test_what_is_not_music_or_too_large_leaves_nothing(self):
        self.assertEqual(self.send('notes.txt', b'hello').json(), {'error': 'type'})
        self.assertEqual(self.send('fake.mid', b'not midi at all').status_code, 415)
        self.assertEqual(self.send('broken.mid', b'MThd' + b'\0' * 10).status_code, 415, 'MIDI in name only')
        self.assertEqual(self.send('empty.mp3', b'').status_code, 415)
        with patch.dict(server.UPLOAD_LIMITS, {'audio': 1000}):
            told = self.client.post('/api/uploads', params={'name': 'big.mp3'}, content=b'\0' * 2000)
            self.assertEqual(told.status_code, 413, 'Refused by its declared size')
            streamed = self.client.post('/api/uploads', params={'name': 'big.mp3'},
                                        content=(b'\0' * 400 for _ in range(5)))  # no size given: chunked
            self.assertEqual(streamed.json(), {'error': 'too_large'}, 'Stopped as it comes')
        self.assertEqual(sorted(p.name for p in self.music.rglob('*') if p.is_file()), [])
        self.assertEqual(self.left_over(), [])

    def test_uploads_need_the_cookie_and_the_page(self):
        anyone = TestClient(server.app, headers=ORIGIN)
        self.assertEqual(anyone.post('/api/uploads', params={'name': 'x.mid'}, content=b'MThd').status_code, 401)
        elsewhere = self.client.post('/api/uploads', params={'name': 'x.mid'}, content=b'MThd',
                                     headers={'origin': 'https://evil.example'})
        self.assertEqual(elsewhere.status_code, 403)

    def test_deleting_sends_files_to_the_trash_and_a_recording_comes_back_to_transcribe(self):
        from pianasoul.library import file_result
        self.send('Mine.mid', make_midi(self.root / 'x.mid').read_bytes())
        self.send('Clair de Lune.mp3', b'ID3' + b'\0' * 100)
        recording = self.core.lib.audio()[0]
        made = file_result(self.core.lib, make_midi(self.root / 'made.mid'), 'Clair de Lune',
                           source_type='local', source_url=recording['path'])
        self.core.lib.set_audio(recording['id'], status='done', recording_id=made['id'])
        self.assertEqual(self.client.get('/api/state').json()['audio'], [], 'Its piece stands for it')
        trashed = len(list(places.TRASH.rglob('*'))) if places.TRASH.exists() else 0
        ids = [r['id'] for r in self.client.get('/api/state').json()['recordings']]
        self.assertEqual(self.client.request('DELETE', '/api/recordings', json={'ids': ids}).status_code, 200)
        self.assertEqual(self.client.get('/api/state').json()['recordings'], [])
        self.assertEqual(sorted(p.name for p in self.music.rglob('*.mid')), [])
        self.assertGreaterEqual(len(list(places.TRASH.rglob('*.mid'))), 2, 'In the Trash, not deleted')
        self.assertEqual([a['name'] for a in self.client.get('/api/state').json()['audio']], ['Clair de Lune'],
                         'The recording is yours: it stays, to be transcribed again')
        self.assertEqual(self.client.delete(f'/api/audio/{recording["id"]}').status_code, 200)
        self.assertEqual(self.client.get('/api/state').json()['audio'], [])
        self.assertFalse((self.music / 'Clair de Lune.mp3').exists())
        self.assertGreater(len(list(places.TRASH.rglob('*'))), trashed)
        self.assertEqual(self.client.delete(f'/api/audio/{recording["id"]}').status_code, 404)


class LightInstallTests(unittest.TestCase):
    def test_the_server_starts_with_none_of_the_advanced_extra(self):
        """As with only the package's own dependencies installed: each package of the advanced extra fails to
        import. The server and a Core without her start, and the page gets its state."""
        script = """
import sys
HEAVY = ('transkun', 'torch', 'imageio_ffmpeg', 'yt_dlp', 'openai', 'anthropic', 'sherpa_onnx', 'pypinyin',
         'sounddevice', 'rtmidi')  # rtmidi: no dependency at all, CoreMIDI is called directly
for name in HEAVY:
    sys.modules[name] = None  # import fails, find_spec finds nothing
import places
from pathlib import Path
from fastapi.testclient import TestClient
from pianasoul import server
from pianasoul.core import Core
server.core = Core(assistant=False)
client = TestClient(server.app, headers={'origin': 'http://testserver'})
client.cookies.set(server.COOKIE, server.TOKEN)
state = client.get('/api/state').json()
assert state['features'] == {'transcribe': False, 'assistant': False}, state['features']
assert client.post('/api/messages', json={'text': 'x'}).status_code == 409
server.core.close()
print('ok')
"""
        import subprocess, sys
        run = subprocess.run([sys.executable, '-c', script], cwd=Path(__file__).parent, capture_output=True, text=True,
                             timeout=60)
        self.assertEqual(run.stdout.strip().splitlines()[-1:], ['ok'], run.stderr)


class StartTests(unittest.TestCase):
    def start(self, argv, her: bool, new: bool, serving=None):
        """server.main as far as serving: (what was printed, uvicorn's Config and Server, the ports listened on,
        what was opened in Finder)."""
        parts = SimpleNamespace(stt=SimpleNamespace(label='OpenAI x'), speaker=SimpleNamespace(label='ElevenLabs y'),
                                wake=SimpleNamespace(words=['小茗同学', 'Hey Jasmine']))
        parts.describe = lambda model, name, **more: voice.VoiceParts.describe(parts, model, name, **more)
        made = MagicMock(label='test')
        made.features.return_value = {'transcribe': her, 'search': her, 'assistant': her}
        watching = patch.object(server, 'end_with_the_app')  # stdin here is the test runner's
        watching.start()
        self.addCleanup(watching.stop)
        with tempfile.TemporaryDirectory() as tmp, \
             patch('pianasoul.library.DATA_DIR', Path(tmp)), \
             patch.dict('os.environ', {'OPENAI_API_KEY': 'test' if her else ''}), \
             patch('pianasoul.core.installed', return_value=True), \
             patch('pianasoul.core.Core', return_value=made) as core_made, \
             patch('pianasoul.core.version', return_value={'commit': 'abc', 'repo': '', 'app': ''}), \
             patch.object(server, 'voice_parts', return_value=parts), \
             patch('pianasoul.web_tls.prepare_https', return_value=('192.168.1.20', new)), \
             patch('pianasoul.web_tls.local_name', return_value='Test-Mac.local'), \
             patch('pianasoul.web_tls.fingerprint', return_value='AB CD EF'), \
             patch.object(server, 'listen', side_effect=lambda port: Mock(port=port)) as listened, \
             patch.object(server, 'write_pairing'), patch.object(server, 'certificate_page'), \
             patch('uvicorn.Config') as config, patch.object(server, 'serving') as served, \
             patch('builtins.print') as shown, patch.object(server, 'TOKEN', server.TOKEN), \
             patch.object(server, 'core', None), patch.object(server.sys, 'platform', 'darwin'), \
             patch.object(server.subprocess, 'run') as opened:
            served.return_value.run.side_effect = serving
            self.token, self.tmp, self.core_made = server.TOKEN, Path(tmp), core_made
            server.main(argv)
            self.token = server.TOKEN
        printed = ' '.join(str(c.args[0]) for c in shown.call_args_list if c.args)
        return printed, config, served, [c.args[0] for c in listened.call_args_list], opened

    def test_one_command_serves_the_iphone_at_the_macs_address(self):
        printed, config, served, ports, opened = self.start([], her=True, new=True)
        self.assertEqual(ports, [8765, 8764], 'Both ports first, so nothing starts while another PianaSoul runs')
        self.assertEqual(served.return_value.run.call_args.kwargs['sockets'][0].port, 8765)
        self.assertEqual(config.call_args.kwargs['ssl_certfile'], str(self.tmp / 'phone-tls' / 'cert.pem'))
        self.assertLessEqual(config.call_args.kwargs['timeout_graceful_shutdown'], 3, 'One Control+C is enough')
        self.assertIn(f'https://192.168.1.20:8765/#token={self.token}', printed)
        self.assertIn('"PianaSoul on Test-Mac"', printed, 'The certificate to trust, by its name')
        self.assertIn('http://192.168.1.20:8764/', printed)
        self.assertIn('AB CD EF', printed, 'The fingerprint, where it comes the trusted way: on the Mac itself')
        opened.assert_called_once_with(['open', '-R', str(self.tmp / 'phone-tls' / 'PianaSoul-CA.cer')], check=False)

    def test_without_her_the_app_still_starts_and_says_why(self):
        with patch.dict('os.environ', {'OPENAI_API_KEY': ''}):
            printed, _, served, _, _ = self.start([], her=False, new=False)
        self.assertFalse(self.core_made.call_args.kwargs['assistant'])
        served.return_value.run.assert_called_once()
        self.assertIn('her off (OPENAI_API_KEY is not set)', printed)
        self.assertNotIn('Tap the orb', printed)

    def test_as_the_mac_app_runs_it_the_token_never_shows(self):
        printed, _, served, _, opened = self.start(['--app'], her=True, new=True)
        served.return_value.run.assert_called_once()
        self.assertIn('https://192.168.1.20:8765/', printed)
        self.assertNotIn(self.token, printed, 'Its output goes to a log file')
        self.assertNotIn('First time on this Mac', printed, 'The Mac app shows the way')
        opened.assert_not_called()

    def test_quitting_closes_the_piano_before_the_program_ends(self):
        import signal
        for stop in (signal.SIGTERM, signal.SIGINT):
            self.addCleanup(signal.signal, stop, signal.getsignal(stop))
        handed_back = lambda **kw: signal.raise_signal(signal.SIGTERM)  # as uvicorn does once it has stopped
        with self.assertRaises(SystemExit) as ended:
            self.start(['--app'], her=False, new=False, serving=handed_back)
        self.assertEqual(ended.exception.code, 0)
        self.core_made.return_value.close.assert_called_once()

    def test_as_the_service_begins_to_stop_the_piece_is_paused_where_it_is_heard(self):
        import asyncio
        import uvicorn
        with patch.object(uvicorn.Server, '__init__', return_value=None), \
             patch.object(uvicorn.Server, 'shutdown', new_callable=AsyncMock) as stopped, \
             patch.object(server, 'core') as core:
            asyncio.run(server.serving(Mock()).shutdown())
        core.playback.pause.assert_called_once()
        stopped.assert_awaited_once()

    def test_token_is_kept_privately_across_restarts(self):
        with tempfile.TemporaryDirectory() as tmp, patch('pianasoul.library.DATA_DIR', Path(tmp)):
            first = server.saved_token()
            self.assertGreaterEqual(len(first), 32)
            self.assertEqual(server.saved_token(), first)
            self.assertEqual((Path(tmp) / 'phone-token').stat().st_mode & 0o777, 0o600)
            (Path(tmp) / 'phone-token').write_text('')  # emptied: never a token that lets anyone in
            self.assertGreaterEqual(len(server.saved_token()), 32)
            self.assertNotEqual(server.saved_token(fresh=True), first)


class PairingTests(unittest.TestCase):
    """How a phone, and the Mac app, reach the server: pairing.json, the certificate's page, and pairing anew."""

    def setUp(self):
        from pianasoul.web_tls import prepare_https
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name)
        for patcher in (patch('pianasoul.library.DATA_DIR', self.data), patch.object(server, 'TOKEN', server.TOKEN),
                        patch('pianasoul.web_tls.local_name', return_value='Test-Mac.local')):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.tls = self.data / 'phone-tls'
        prepare_https(self.tls)
        where = patch.object(server, '_where', ('192.168.1.20', self.tls))
        where.start()
        self.addCleanup(where.stop)
        server.TOKEN = server.saved_token()

    def test_pairing_json_says_how_to_reach_this_mac_privately(self):
        from pianasoul.web_tls import fingerprint
        server.write_pairing()
        path = self.data / 'pairing.json'
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        info = json.loads(path.read_text())
        self.assertEqual(info, {'address': f'https://192.168.1.20:8765/#token={server.TOKEN}',
                                'certificate': 'http://192.168.1.20:8764/', 'local_name': 'Test-Mac.local',
                                'port': 8765, 'token': server.TOKEN, 'ca_sha256': fingerprint(self.tls)})
        self.assertRegex(info['ca_sha256'], r'^([0-9A-F]{2} ){31}[0-9A-F]{2}$')

    def test_the_certificate_page_serves_the_page_and_the_certificate_only(self):
        import hashlib
        import socket as sockets
        import urllib.error
        import urllib.request
        sock = sockets.socket()
        sock.bind(('127.0.0.1', 0))
        sock.listen()
        server.certificate_page(self.tls, sock)
        base = f'http://127.0.0.1:{sock.getsockname()[1]}'
        page = urllib.request.urlopen(base + '/').read().decode()
        cer = urllib.request.urlopen(base + '/PianaSoul-CA.cer')
        self.assertEqual(cer.headers['Content-Type'], 'application/x-x509-ca-cert')
        digest = hashlib.sha256(cer.read()).hexdigest().upper()
        self.assertNotIn(' '.join(digest[i:i + 2] for i in range(0, 64, 2)), page,
                         'Not its fingerprint: that is on the Mac, not on a page that comes the same way')
        self.assertIn('PianaSoul on Test-Mac', page)
        self.assertNotIn(server.TOKEN, page)
        for other in ('/api/state', '/pairing.json', '/../phone-token', '/ca-key.pem'):
            with self.assertRaises(urllib.error.HTTPError, msg=other) as refused:
                urllib.request.urlopen(base + other)
            self.assertEqual(refused.exception.code, 404)

    def test_in_the_app_it_stops_when_the_app_is_gone(self):
        read, write = os.pipe()
        with os.fdopen(read, 'r') as stdin, patch.object(server.os, 'kill') as killed, \
                patch.object(server.os, '_exit') as ended:
            server.end_with_the_app(stdin, patience=0.1)
            time.sleep(0.2)
            killed.assert_not_called()  # the app is there: its end of the pipe is open
            os.close(write)  # the app quits, crashes or is stopped: the system closes its end
            for _ in range(100):
                if ended.called:
                    break
                time.sleep(0.02)
        killed.assert_called_once_with(os.getpid(), signal.SIGTERM)
        ended.assert_called_once_with(1)  # and, should stopping hang, at once after the patience

    def test_from_a_terminal_it_keeps_running_when_stdin_is_not_a_pipe(self):
        with open(os.devnull) as stdin, patch.object(server.threading, 'Thread') as watcher:
            server.end_with_the_app(stdin)
        watcher.assert_not_called()

    def test_a_phone_that_goes_mid_request_leaves_nothing_in_the_log(self):
        import socket as sockets
        import struct
        sock = sockets.socket()
        sock.bind(('127.0.0.1', 0))
        sock.listen()
        server.certificate_page(self.tls, sock)
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            phone = sockets.create_connection(sock.getsockname())
            phone.sendall(b'GET / HT')  # and gone, at once (a reset, not a goodbye)
            phone.setsockopt(sockets.SOL_SOCKET, sockets.SO_LINGER, struct.pack('ii', 1, 0))
            phone.close()
            time.sleep(0.5)
        self.assertEqual(said.getvalue(), '')

    def test_pairing_anew_from_the_mac_only(self):
        server.write_pairing()
        old = server.TOKEN
        phone = signed_in(TestClient(server.app, headers=ORIGIN, client=('192.168.1.30', 50000)))
        self.assertEqual(phone.post('/api/pairing/reset').json(), {'error': 'mac_only'})
        mac = signed_in(TestClient(server.app, headers=ORIGIN, client=('127.0.0.1', 50000)))
        self.assertEqual(mac.post('/api/pairing/reset').json(), {'ok': True})
        self.assertNotEqual(server.TOKEN, old)
        self.assertEqual(json.loads((self.data / 'pairing.json').read_text())['token'], server.TOKEN)
        self.assertEqual((self.data / 'phone-token').read_text(), server.TOKEN)
        self.assertEqual(phone.get('/api/state').status_code, 401, 'The old Home Screen app is signed out')

    def test_a_port_in_use_is_said_plainly(self):
        first = server.listen(0)
        self.addCleanup(first.close)
        said = io.StringIO()
        with contextlib.redirect_stderr(said), self.assertRaises(SystemExit) as ended:
            server.listen(first.getsockname()[1])
        self.assertIn('is PianaSoul already running', said.getvalue())
        self.assertEqual(ended.exception.code, server.PORT_TAKEN, 'The Mac app waits on this one, not a failure')

    def test_stopping_lets_a_connection_that_is_closing_finish(self):
        from websockets.protocol import State
        Protocol = server.websocket_protocol()

        def connection(state):
            ws = Protocol.__new__(Protocol)  # as uvicorn has it while serving: past the handshake, nothing sent to end it
            ws.handshake_complete, ws.close_sent = True, False
            ws.conn, ws.transport, ws.queue, ws.stop_keepalive = Mock(state=state), Mock(), Mock(), Mock()
            ws.conn.data_to_send.return_value = [b'close']
            return ws

        closing = connection(State.CLOSING)  # the page closed it, and was answered, just as the service stops
        closing.shutdown()
        closing.conn.send_close.assert_not_called()  # uvicorn 0.54 would: InvalidState, and the service ends in error
        closing.transport.close.assert_called_once()
        still_open = connection(State.OPEN)
        still_open.shutdown()
        still_open.conn.send_close.assert_called_once_with(1012)  # as uvicorn does: the service is restarting
        still_open.transport.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
