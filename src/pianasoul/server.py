"""The phone app. The Mac serves the page and keeps everything (core.Core): the library, the piano, her
conversations and new pieces. The phone shows them and changes them, and is her ears and voice.

    ./run.sh app

/api/... answers the page, /events tells it what changed, and /voice carries the microphone one way and her voice
the other while the orb is on. All three need the cookie the page gets for the address's token; the page and its
files do not. What needs her, or transcription, answers 409 when this Mac has not got it (Core.features).
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hmac
import json
import logging
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

import numpy as np
from fastapi import Body, FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

from .chat import Worker
from .voice import VOICE_NOTE, Conversation, VoiceParts

WEB = Path(__file__).with_name('web')
ASSET_TYPES = {'.js', '.css', '.png', '.mp3', '.webmanifest'}
PORT = 8765
COOKIE = 'pianasoul'
TYPED_NOTE = """
Requests typed in the app (typed is true in the application state) are answered on screen, not aloud; keep them
just as short."""
NEEDS = {'/api/messages': 'assistant', '/api/conversations': 'assistant', '/api/search': 'assistant',
         '/api/dictation': 'assistant', '/api/youtube': 'transcribe', '/api/downloads': 'transcribe'}
UPLOAD_LIMITS = {'midi': 10 << 20, 'audio': 500 << 20}  # bytes
TOKEN = secrets.token_urlsafe(32)  # main() uses the saved one
app = FastAPI(title='PianaSoul', docs_url=None, redoc_url=None, openapi_url=None)
core = None  # the Core; main() makes it, tests give their own
version = {'commit': '', 'repo': ''}
log = logging.getLogger(__spec__.name)
_voice = asyncio.Lock()  # one phone at a time is her ears and voice
_parts = _stt = _her = None
_naming = threading.Lock()  # one upload at a time takes a name in the library folder
_notes: dict[int, list] = {}


def saved_token() -> str:
    """The phone's access token, kept so the address and a Home Screen app survive restarts."""
    from .library import DATA_DIR
    path = DATA_DIR / 'phone-token'
    if not path.is_file() or not path.read_text().strip():  # an empty token would let anyone in
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(mode=0o600)
        path.write_text(secrets.token_urlsafe(32))
    return path.read_text().strip()


def voice_parts() -> VoiceParts:
    """Loaded once per server, so that the orb starts quickly."""
    global _parts
    if _parts is None:
        _parts = VoiceParts()
    return _parts


def same_origin(headers) -> bool:
    origin = urlsplit(headers.get('origin', ''))
    return origin.netloc == headers.get('host') and origin.scheme in {'http', 'https'}


def signed_in(cookies) -> bool:
    return hmac.compare_digest(cookies.get(COOKIE, '').encode(), TOKEN.encode())


def missing(path: str) -> str | None:
    """What a path needs that this Mac has not got: 'assistant_off' or 'transcribe_off'."""
    need = next((need for prefix, need in NEEDS.items() if path == prefix or path.startswith(prefix + '/')), None)
    return f'{need}_off' if need and not core.features()[need] else None


@app.middleware('http')
async def guard(request: Request, call_next):
    """The page and its files are public; everything under /api needs the cookie, and changes the same origin. A
    request that fails is among the details (--debug)."""
    path, response = request.url.path, None
    if path.startswith('/api/') and path != '/api/session':
        if not signed_in(request.cookies):
            response = JSONResponse({'error': 'signed_out'}, status_code=401)
        elif request.method != 'GET' and not same_origin(request.headers):
            response = JSONResponse({'error': 'origin'}, status_code=403)
        elif off := missing(path):
            response = JSONResponse({'error': off}, status_code=409)
    response = response or await call_next(request)
    if response.status_code >= 400:
        log.debug('%s %s: %d', request.method, path, response.status_code)
    return response


# ---- the page ----------------------------------------------------------------------------------------
@app.get('/')
def index():
    return FileResponse(WEB / 'index.html', headers={'Cache-Control': 'no-store'})


@app.get('/{name}')
def asset(name: str):
    path = WEB / name
    if Path(name).suffix not in ASSET_TYPES or not path.is_file() or path.parent != WEB:
        raise HTTPException(404)
    media = 'application/manifest+json' if name.endswith('.webmanifest') else None
    return FileResponse(path, media_type=media, headers={'Cache-Control': 'no-cache'})


@app.post('/api/session')
def session(request: Request, response: Response, token: str = Body(embed=True)):
    """The address's token, once, for a cookie that the page, its media and its connections then carry."""
    if not same_origin(request.headers) or not hmac.compare_digest(token.encode(), TOKEN.encode()):
        raise HTTPException(401)
    response.set_cookie(COOKIE, TOKEN, max_age=10 * 365 * 86400, httponly=True, secure=True, samesite='strict')
    return {'ok': True}


# ---- what the page shows ------------------------------------------------------------------------------
@app.get('/api/state')
def state():
    her = core.assistant
    return {'recordings': core.recordings(), 'jobs': core.downloads.snapshot(),
            'playback': core.playback.snapshot(), 'conversations': core.conversations.all() if her else [],
            'current': her.conversation_id if her else None, 'busy': core.busy, 'settings': core.settings(),
            'devices': core.devices(), 'version': version, 'features': core.features(), 'audio': core.audio()}


@app.get('/api/recordings')
def recordings():
    return core.recordings()


@app.get('/api/conversations')
def conversations():
    return {'conversations': core.conversations.all(), 'current': core.assistant.conversation_id}


@app.get('/api/conversations/{cid}')
def conversation(cid: int):
    found = core.conversations.get(cid)
    if found is None:
        raise HTTPException(404)
    return {**found, 'messages': core.conversations.messages(cid)}


@app.get('/api/search')
def search(q: str):
    """Conversations with these words in them, each with the latest message that has them."""
    return core.conversations.search(q.strip()) if q.strip() else []


@app.get('/api/devices')
def devices():
    return core.devices()


@app.get('/api/outputs')
def outputs():
    """Every MIDI output this Mac knows: id, name, kind, connected, peers for a network session, and which is
    chosen."""
    chosen = core.playback.target()
    return [o.public() | {'chosen': chosen is not None and o.id == chosen.id} for o in core.outputs()]


@app.post('/api/outputs/test')
def test_output():
    """A short C major arpeggio on the output chosen, at the volume set: is the piano there?"""
    try:
        core.playback.test_tone()
    except LookupError as exc:
        return JSONResponse({'error': 'piano', 'why': str(exc)}, status_code=409)
    except RuntimeError:
        return JSONResponse({'error': 'playing'}, status_code=409)
    return {'ok': True}


@app.get('/api/recordings/{rec_id}/notes')
def notes(rec_id: int):
    """The piece's notes for its picture: [start, length, pitch, velocity], in seconds."""
    rec = _recording(rec_id)
    if rec_id not in _notes:
        import pretty_midi
        midi = pretty_midi.PrettyMIDI(rec['midi_path'])
        _notes[rec_id] = sorted([round(n.start, 3), round(n.end - n.start, 3), n.pitch, n.velocity]
                                for i in midi.instruments for n in i.notes)
    return _notes[rec_id]


@app.get('/api/recordings/{rec_id}/midi')
def midi(rec_id: int):
    rec = _recording(rec_id)
    from .library import safe_name
    return FileResponse(rec['midi_path'], media_type='audio/midi', filename=f"{safe_name(rec['title'])}.mid")


def _recording(rec_id: int) -> dict:
    rec = core.lib.get(rec_id)
    if not rec or not rec['file_exists']:
        raise HTTPException(404)
    return rec


# ---- what the page does -------------------------------------------------------------------------------
@app.post('/api/play')
def play(id: int = Body(), ids: list[int] = Body(default=[]), scope: dict = Body(default={})):
    """A piece tapped in a list: it plays (unless it is the one already playing), and that list plays on."""
    playback = core.playback
    try:
        if id == playback.current_id and not playback.ended and playback.state != 'stopped':
            playback.show(id, ids, scope)
        else:
            playback.select(id, ids, scope)
    except FileNotFoundError:
        raise HTTPException(404) from None
    except LookupError as exc:
        return _piano_missing(exc)
    return playback.snapshot()


@app.post('/api/playback')
def playback(action: str = Body(embed=True), value: float | str | None = Body(default=None, embed=True),
             index: int | None = Body(default=None, embed=True)):
    """▶/⏸ (toggle), ✕ (stop: back to 0:00), ⏮ (prev), ⏭ (next), seek (value: seconds), mode (value), remove or
    move (value: an id), volume (value: percent)."""
    p = core.playback
    try:
        if action == 'toggle':
            p.toggle()
        elif action == 'stop':
            p.stop()
        elif action in {'prev', 'next'}:
            p.step(-1 if action == 'prev' else 1)
        elif action == 'seek':
            p.seek(float(value))
        elif action == 'mode':
            p.set_mode(str(value))
        elif action == 'volume':
            if not 0 <= float(value) <= 100:
                raise ValueError(value)
            p.set_volume(float(value))
        elif action == 'remove':
            p.remove(int(value))
        elif action == 'move':
            p.move(int(value), int(index))
        else:
            raise HTTPException(400)
    except (TypeError, ValueError):
        raise HTTPException(400) from None
    except FileNotFoundError:  # the piece's file is gone
        raise HTTPException(404) from None
    except LookupError as exc:
        return _piano_missing(exc)
    return p.snapshot()


def _piano_missing(exc) -> JSONResponse:
    """The piano is not there: why shows here, and on the page; she tells you in the conversation too."""
    if core.assistant:
        core.assistant.piano_news(core.assistant.piano_missing(exc))
    else:
        print(f'  (cannot play: {exc})', flush=True)
    return JSONResponse({'error': 'piano'}, status_code=409)


@app.patch('/api/recordings/{rec_id}')
def edit(rec_id: int, change: dict = Body()):
    """favorite: true/false; played: false (out of 最近播放); or piece, artist and performer (new names)."""
    _recording(rec_id)
    if 'favorite' in change:
        core.lib.favorite(rec_id, bool(change['favorite']))
    if change.get('played') is False:
        core.lib.played(rec_id, False)
    if 'piece' in change:
        core.lib.edit(rec_id, str(change['piece']), str(change.get('artist', '')), str(change.get('performer', '')))
    core.emit({'type': 'library'})
    return next(r for r in core.recordings() if r['id'] == rec_id)


@app.delete('/api/recordings')
def delete(ids: list[int] = Body(embed=True)):
    """One piece or several, in one go, their files to the Mac's Trash."""
    core.delete_recordings(ids)
    for rec_id in ids:
        _notes.pop(rec_id, None)  # its number may be given to the next new piece
    return {'ok': True}


@app.post('/api/uploads')
async def upload(request: Request, name: str):
    """One file for the library folder, its content the request's body and name its file name: a MIDI file or a
    recording, as yours, as if put there by hand ("name (2)" when the name is taken). It is written to a hidden file
    as it comes, stopped as soon as it is too large, and named only once it is whole; nothing stays of one that
    fails."""
    from starlette.requests import ClientDisconnect
    from .audio import AUDIO_EXTENSIONS
    from .library import MIDI_TYPES, free_name, safe_name
    suffix = Path(name).suffix.lower()
    kind = 'midi' if suffix in MIDI_TYPES else 'audio' if suffix in AUDIO_EXTENSIONS else None
    if kind is None:
        return JSONResponse({'error': 'type'}, status_code=415)
    limit = UPLOAD_LIMITS[kind]
    if int(request.headers.get('content-length') or 0) > limit:
        return JSONResponse({'error': 'too_large'}, status_code=413)
    folder = core.lib.music_dir
    folder.mkdir(parents=True, exist_ok=True)
    part = folder / f'.upload-{secrets.token_hex(8)}.part'  # hidden: the folder's scan passes it over
    try:
        size = 0
        with part.open('wb') as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    return JSONResponse({'error': 'too_large'}, status_code=413)
                await asyncio.to_thread(f.write, chunk)
        with part.open('rb') as f:
            if not size or (kind == 'midi' and f.read(4) != b'MThd'):
                return JSONResponse({'error': 'type'}, status_code=415)
        with _naming:
            dest = free_name(folder, safe_name(Path(name).stem) + suffix)
            part.rename(dest)
    except ClientDisconnect:  # the phone went: nobody to answer
        return Response(status_code=400)
    finally:
        part.unlink(missing_ok=True)
    if not await asyncio.to_thread(core.folder.take_in, dest):  # a MIDI file that is not one after all
        dest.unlink(missing_ok=True)
        return JSONResponse({'error': 'type'}, status_code=415)
    return {'name': dest.name}


@app.delete('/api/audio/{audio_id}')
def delete_audio(audio_id: int):
    """A recording to transcribe, to the Mac's Trash."""
    if not core.delete_audio(audio_id):
        raise HTTPException(404)
    return {'ok': True}


@app.post('/api/audio/{audio_id}/transcribe')
def transcribe(audio_id: int):
    """A recording in the library folder, made into a piece now."""
    if not core.features()['transcribe']:
        return JSONResponse({'error': 'transcribe_off'}, status_code=409)
    if not core.transcribe(audio_id):
        raise HTTPException(404)
    return {'ok': True}


@app.post('/api/messages')
def message(text: str = Body(embed=True), conversation: int | None = Body(default=None, embed=True)):
    """A typed request, answered on screen; in an earlier conversation, that one is picked up again."""
    if not text.strip():
        raise HTTPException(400)
    core.type(text.strip(), conversation)
    return {'ok': True}


@app.post('/api/conversations/new')
def new_conversation():
    core.new_conversation()
    return {'ok': True}


@app.post('/api/conversations/{cid}/pick-up')
def pick_up(cid: int):
    core.pick_up(cid)
    return {'ok': True}


@app.patch('/api/conversations/{cid}')
def change_conversation(cid: int, change: dict = Body()):
    if core.conversations.get(cid) is None:
        raise HTTPException(404)
    if 'title' in change:
        core.conversations.rename(cid, str(change['title']))
    if 'pinned' in change:
        core.conversations.pin(cid, bool(change['pinned']))
    core.emit({'type': 'conversations'})
    return {'ok': True}


@app.delete('/api/conversations/{cid}')
def delete_conversation(cid: int):
    core.delete_conversation(cid)
    return {'ok': True}


@app.patch('/api/settings')
def settings(change: dict = Body()):
    """language, appearance, or output (a unique ID from /api/outputs): heard at once, without a restart."""
    return core.set_settings(**{k: str(v) for k, v in change.items()})


@app.get('/api/youtube')
def youtube_search(q: str):
    """Videos to add: piano versions found by a search, or the one video a link points to."""
    from . import youtube
    from .download import video_details, video_info
    from .downloads import clean_url
    q = q.strip()
    try:
        if q.startswith(('http://', 'https://')):
            url = clean_url(q)
            found = youtube.details([url]).get(url) if youtube.video_id(url) and youtube.api_key() else None
            results = [{'url': url, **(found or video_details(video_info(url)))}]
        else:
            results = youtube.piano_versions(q)
    except youtube.YouTubeError as exc:
        return JSONResponse({'error': str(exc)}, status_code=502)
    except Exception as exc:  # a link yt-dlp cannot read
        return JSONResponse({'error': f'{type(exc).__name__}: {exc}'}, status_code=502)
    for r in results:
        r.pop('description', None)
        r.pop('flags', None)
    return results


@app.post('/api/downloads')
def download(url: str = Body(embed=True), query: str = Body(default='', embed=True),
             video: dict = Body(default={}, embed=True)):
    job = core.downloads.add(url, query=query, video=video)
    return job.public()


@app.delete('/api/downloads/{job_id}')
def stop_download(job_id: int):
    core.downloads.stop(job_id)
    return {'ok': True}


@app.post('/api/dictation')
async def dictation(request: Request):
    """What was said, as text for the message field: 16 kHz mono PCM16."""
    global _stt
    audio = np.frombuffer(await request.body(), dtype='<i2')
    if not len(audio):
        return {'text': ''}
    if _stt is None:
        from .voice import Transcriber
        _stt = _parts.stt if _parts else Transcriber()
    text = await asyncio.to_thread(_stt, audio.astype(np.float32) / 32768, core.lib)
    return {'text': text}


# ---- what changed --------------------------------------------------------------------------------------
def why_gone(message: dict) -> str:
    """Why a page's connection closed, for the details: the reason the page gave, or what its code means."""
    code = message.get('code')
    return message.get('reason') or {1001: 'the page went away', 1006: 'the connection broke'}.get(code, f'code {code}')


@app.websocket('/events')
async def events(socket: WebSocket):
    if not same_origin(socket.headers) or not signed_in(socket.cookies):
        await socket.close(code=1008)
        return
    await socket.accept()
    loop = asyncio.get_running_loop()
    waiting: asyncio.Queue = asyncio.Queue(maxsize=256)

    def listener(event):
        def put():
            if not waiting.full():  # a page that fell this far behind reloads its state when it reconnects
                waiting.put_nowait(event)
        loop.call_soon_threadsafe(put)

    core.listeners.append(listener)
    log.debug('a page is following the changes')

    async def send():
        while True:
            await socket.send_json(await waiting.get())

    async def receive():
        while (message := await socket.receive())['type'] != 'websocket.disconnect':
            pass
        log.debug('a page stopped following the changes: %s', why_gone(message))

    tasks = [asyncio.create_task(send()), asyncio.create_task(receive())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        core.listeners.remove(listener)
        for task in tasks:
            task.cancel()
        await asyncio.wait(tasks)  # until they have stopped; their cancelling is theirs, not this connection's


# ---- her ears and voice ------------------------------------------------------------------------------
class Phone:
    """The phone that is her ears and voice for the moment: connect(emit) when one connects, disconnect() when it goes;
    meanwhile nothing goes anywhere. Her voice goes to it in pieces as it is made, and it plays them back to back;
    speak returns once it has played them (or at once when she is hushed). It then asks to listen on (resume) or to
    wait for her name (sleep)."""

    def __init__(self):
        self.emit = None
        self.until = 0.0  # time.monotonic() when the phone will have played everything sent

    def connect(self, emit):
        self.emit, self.until = emit, 0.0

    def disconnect(self):
        self.emit = None

    def send(self, event):
        emit = self.emit
        if emit:
            try:
                emit(event)
            except TimeoutError:  # a phone that stopped taking them is going: she goes on without it
                log.debug('the phone stopped taking her messages')

    def status(self, state):
        self.send({'type': 'state', 'state': state})

    def speak(self, pieces, hushed):
        for pcm in pieces:
            if hushed.is_set():
                return
            pcm = np.asarray(pcm, dtype='<i2')
            self.until = max(self.until, time.monotonic()) + len(pcm) / 24000
            self.send({'type': 'audio', 'data': base64.b64encode(pcm.tobytes()).decode()})
        hushed.wait(max(0.0, self.until + 0.1 - time.monotonic()))  # a little room for the network

    def turn_end(self, conversation, sleep):
        self.send({'type': 'turn_end', 'sleep': sleep})


def her() -> tuple[Conversation, Phone, Worker]:
    """Her ears and voice for the phone, in the shared conversation: one for the server's life, as she is one, made
    when the first phone connects. A phone lends her its microphone and speaker while it is with her, so a request
    she is answering when it goes is answered aloud if one is back by then. Her own thread hears and answers in
    order; she is quiet (hushed) until a phone is with her."""
    global _her
    if _her is None:
        phone = Phone()
        conversation = Conversation(core.assistant, core.chat, voice_parts(), phone)
        conversation.hushed.set()
        _her = conversation, phone, Worker()
    return _her


SAID = {'continue_session': 'with her', 'pause_session': 'away from her', 'resume': 'her reply heard, listen on',
        'sleep': 'her reply heard, wait for her name'}  # what the phone's messages mean, for the details


@app.websocket('/voice')
async def voice(socket: WebSocket):
    if not same_origin(socket.headers) or not signed_in(socket.cookies):
        await socket.close(code=1008)
        return
    if core.assistant is None:
        await socket.close(code=1008, reason='assistant_off')
        return
    await socket.accept()
    if _voice.locked():
        await socket.close(code=1013, reason='Another phone is connected')
        return
    async with _voice:
        print('  · voice on the iPhone', flush=True)
        loop = asyncio.get_running_loop()
        outgoing: asyncio.Queue = asyncio.Queue(maxsize=64)

        def emit(event):  # from her thread
            asyncio.run_coroutine_threadsafe(outgoing.put(event), loop).result(timeout=10)

        async def send():
            while True:
                await socket.send_json(await outgoing.get())

        incoming: asyncio.Queue = asyncio.Queue(maxsize=8)
        conversation = phone = worker = None
        tasks = [asyncio.create_task(send())]

        def act(command, data):
            if command == 'frame':
                conversation.frame(np.frombuffer(data, dtype='<i2'))
            elif command in {'resume', 'sleep'}:
                if conversation.state == 'speaking':  # her turn is over and has been heard
                    getattr(conversation, command)()
            else:
                getattr(conversation, command)()  # hush, unhush

        async def process():
            while True:
                command, data = await incoming.get()
                await asyncio.wrap_future(worker.submit(act, command, data))

        async def receive():
            while True:
                message = await socket.receive()
                if message['type'] == 'websocket.disconnect':
                    log.debug('the phone went: %s', why_gone(message))
                    return
                if message.get('bytes') is not None:
                    if conversation.hushed.is_set():
                        continue
                    item = ('frame', message['bytes'])
                else:
                    try:
                        payload = json.loads(message.get('text') or '')
                    except json.JSONDecodeError:
                        payload = None
                    commands = {'resume': 'resume', 'sleep': 'sleep', 'pause_session': 'hush',
                                'continue_session': 'unhush'}
                    if not isinstance(payload, dict) or payload.get('type') not in commands or set(payload) != {'type'}:
                        await socket.close(code=1008, reason='Unsupported voice message')
                        return
                    item = (commands[payload['type']], None)
                    log.debug('the phone: %s (she is %s)', SAID[payload['type']], conversation.state)
                    # Quiet, or heard again, at once: even while she is still busy with a request; the rest follows
                    # in order.
                    if item[0] == 'hush':
                        conversation.hushed.set()
                    elif item[0] == 'unhush':
                        conversation.hushed.clear()
                try:
                    incoming.put_nowait(item)
                except asyncio.QueueFull:
                    await socket.close(code=1013, reason='Voice input overloaded')
                    return

        try:
            conversation, phone, worker = await asyncio.to_thread(her)  # the first time, the models load
            phone.connect(emit)
            log.debug('the phone connected (she is %s)', conversation.state)
            await outgoing.put({'type': 'ready'})
            await outgoing.put({'type': 'state', 'state': conversation.state})  # still busy with a request, say
            tasks += [asyncio.create_task(process()), asyncio.create_task(receive())]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except WebSocketDisconnect:
            pass
        except Exception:  # the phone sees the connection close and turns gray
            import traceback
            traceback.print_exc()
        finally:
            if conversation is not None:  # quiet at once; a piece paused while she listened plays on
                conversation.hushed.set()
                phone.disconnect()
                worker.submit(conversation.hush)
            for task in tasks:
                task.cancel()
            await asyncio.wait(tasks)
            print('  · voice off', flush=True)


def main(argv=None):
    global TOKEN, core, version
    from . import show_details
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--debug', action='store_true', help='also show what happens in detail, with times')
    show_details(parser.parse_args(argv).debug)
    import uvicorn
    from .chat import startup
    from .core import Core, installed, version as running
    from .library import DATA_DIR
    from .web_tls import CA_FILE, local_name, prepare_https
    TOKEN = saved_token()
    her, why = True, ''
    if not installed('transkun', 'yt_dlp'):
        her, why = False, 'the advanced extra is not installed'
    elif not os.environ.get('OPENAI_API_KEY'):
        her, why = False, 'OPENAI_API_KEY is not set'
    else:
        print('Loading speech recognition and the wake word…', flush=True)
        try:
            voice_parts()
        except Exception as exc:  # no network for the first download, say
            her, why = False, f'her voice did not load: {exc}'
    try:
        core = Core(VOICE_NOTE + TYPED_NOTE, assistant=her)
    except Exception as exc:  # her chat model did not start
        her, why = False, f'her chat model did not start: {exc}'
        core = Core(assistant=False)
    version = running()
    features = core.features()
    print('Features: ' + '; '.join([
        f'transcription {"on" if features["transcribe"] else "off (the advanced extra is not installed)"}',
        f'search by name {"on" if features["search"] else "off (needs transcription and YOUTUBE_API_KEY)"}',
        f'her {"on" if her else f"off ({why})"}']), flush=True)
    tls = DATA_DIR / 'phone-tls'
    try:
        host, new = prepare_https(tls)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        parser.exit(2, f'Cannot set up HTTPS: {exc}\n')
    address = f'https://{host}:{PORT}/#token={TOKEN}'
    print(voice_parts().describe(core.label, 'app', address=address) if her else
          startup('app', library=f'{core.lib.count()} piece(s) in {core.lib.music_dir}', address=address))
    if new:  # the one step by hand, shown only now: the iPhone learns to trust this Mac
        print(f'\nFirst time on this Mac: let the iPhone trust it, once.\n\n'
              f'  1. Send     Finder shows {CA_FILE}: right-click > Share > AirDrop\n'
              f'  2. Install  iPhone Settings > General > VPN & Device Management > Install\n'
              f'  3. Trust    Settings > General > About > Certificate Trust Settings\n'
              f'              > turn on "PianaSoul on {local_name().removesuffix(".local")}"\n'
              f'  4. Open     the address above in Safari > Share > Add to Home Screen\n\n'
              f'  The file: {tls / CA_FILE}\n', flush=True)
        if sys.platform == 'darwin':
            subprocess.run(['open', '-R', str(tls / CA_FILE)], check=False)
    said = ' or '.join(f'"{w}"' for w in voice_parts().wake.words) if her else ''
    print('Keep the address private.\n' + (f'Tap the orb and say {said}, then your request. ' if her else '') +
          'Control+C quits.\n', flush=True)
    # Only what matters shows, as with ./run.sh voice: no request log, no "running on 0.0.0.0" to mistake for the
    # address, and no error for quitting while a phone in the background never says goodbye (it stops in 2 s).
    logging.getLogger('uvicorn.error').addFilter(
        lambda record: 'timeout graceful shutdown exceeded' not in record.getMessage())
    try:
        uvicorn.run(app, host='0.0.0.0', port=PORT, ssl_certfile=str(tls / 'cert.pem'),
                    ssl_keyfile=str(tls / 'key.pem'), ws_max_size=1 << 20, ws_max_queue=4,
                    timeout_graceful_shutdown=2, log_level='warning')
    finally:
        core.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
