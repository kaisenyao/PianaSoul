"""The phone app. The Mac serves the page and keeps everything (core.Core): the library, the piano, her
conversations and new pieces. The phone shows them and changes them, and is her ears and voice.

    ./run.sh app
    python -m pianasoul.server --app      # as the Mac app runs it

/api/... answers the page, /events tells it what changed, and /voice carries the microphone one way and her voice
the other while the orb is on. All three need the cookie the page gets for the address's token; the page and its
files do not. What needs her, or transcription, answers 409 when this Mac has not got it (Core.features).

Beside it, plain HTTP on CERT_PORT serves a page on how to trust this Mac and the certificate itself, nothing else.
pairing.json in the data folder says how to reach it all (the Mac app shows it as codes to scan); like the token in
it, it is for this Mac's user only.
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
import signal
import socket
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
CERT_PORT = 8764  # the certificate's page, over plain HTTP: it holds no secret
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
_open: set[WebSocket] = set()  # the connections pages have open (/events, /voice), signed in when they opened
_parts = _stt = _her = None
_naming = threading.Lock()  # one upload at a time takes a name in the library folder
_where: tuple[str, Path] | None = None  # the address phones open, and the certificates' folder (main)
_notes: dict[int, list] = {}


def saved_token(fresh: bool = False) -> str:
    """The phone's access token, kept so the address and a Home Screen app survive restarts; fresh makes a new one,
    and every phone has to be paired again."""
    from .library import DATA_DIR
    path = DATA_DIR / 'phone-token'
    if fresh or not path.is_file() or not path.read_text().strip():  # an empty token would let anyone in
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(mode=0o600)
        path.write_text(secrets.token_urlsafe(32))
    return path.read_text().strip()


def write_pairing() -> None:
    """pairing.json: the address phones open (with the token), the certificate's page, this Mac's .local name (the
    name its certificate is checked by), the token, and the certificate authority's fingerprint. Readable by this
    Mac's user only, from the moment it is made."""
    from .library import DATA_DIR
    from .web_tls import fingerprint, local_name
    host, tls = _where
    info = {'address': f'https://{host}:{PORT}/#token={TOKEN}', 'certificate': f'http://{host}:{CERT_PORT}/',
            'local_name': local_name(), 'port': PORT, 'token': TOKEN, 'ca_sha256': fingerprint(tls)}
    part = DATA_DIR / '.pairing.json.part'
    part.unlink(missing_ok=True)
    with os.fdopen(os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as f:
        json.dump(info, f, indent=1)
    os.replace(part, DATA_DIR / 'pairing.json')


def certificate_page(tls: Path, sock: socket.socket) -> None:
    """Serve, on sock, the page on how to trust this Mac (with the certificate's fingerprint) and the certificate:
    nothing else, and no token. In a thread of its own, for the server's life."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from .web_tls import CA_FILE, fingerprint, local_name
    page = (WEB / 'certificate.html').read_text(encoding='utf-8')
    page = page.replace('{{fingerprint}}', fingerprint(tls)).replace(
        '{{name}}', f'PianaSoul on {local_name().removesuffix(".local")}').encode()

    class Certificate(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/':
                body, kind = page, 'text/html; charset=utf-8'
            elif self.path == f'/{CA_FILE}':
                body, kind = (tls / CA_FILE).read_bytes(), 'application/x-x509-ca-cert'
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # not a line per request
            pass

    class Server(ThreadingHTTPServer):
        def handle_error(self, request, client_address):
            if not isinstance(sys.exc_info()[1], ConnectionError):  # a phone that went mid-request: nothing to say
                super().handle_error(request, client_address)

    httpd = Server(sock.getsockname(), Certificate, bind_and_activate=False)
    httpd.socket.close()
    httpd.socket = sock
    httpd.server_activate()
    threading.Thread(target=httpd.serve_forever, daemon=True).start()


def listen(port: int) -> socket.socket:
    """A socket listening on port on every network the Mac is on; SystemExit, saying why, when another program
    (PianaSoul already running, say) has it."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(('0.0.0.0', port))
    except OSError as exc:
        sock.close()
        raise SystemExit(f'Port {port} is in use ({exc.strerror}): is PianaSoul already running, in a terminal '
                         f'(./run.sh app) or as the Mac app? Quit it first.') from None
    sock.listen(128)
    return sock


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
    her, recordings = core.assistant, core.recordings()
    return {'recordings': recordings, 'jobs': core.downloads.snapshot(),
            'playback': core.playback.snapshot(), 'conversations': core.conversations.all() if her else [],
            'current': her.conversation_id if her else None, 'busy': core.busy, 'settings': core.settings(),
            'devices': core.devices(), 'piano': core.piano, 'version': version, 'features': core.features(),
            'audio': core.audio(), 'playlists': core.lib.playlists(), 'pictures': core.pictures(recordings)}


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
def play(id: int = Body(), ids: list[int] = Body(default=[]), scope: dict = Body(default={}),
         mode: str | None = Body(default=None)):
    """A piece tapped in a list: it plays (unless it is the one already playing), and that list plays on. With a mode,
    the list is played as a whole (a playlist's 播放: order, its 随机播放: shuffle), from its start."""
    playback = core.playback
    try:
        if mode is None and id == playback.current_id and not playback.ended and playback.state != 'stopped':
            playback.show(id, ids, scope)
        else:
            playback.select(id, ids, scope, mode=mode)
    except ValueError:
        raise HTTPException(400) from None
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
    """The piano is not there: why shows here, and the page shows it is not connected; she tells you in the
    conversation too."""
    core.look_at_piano()
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
    core.library_changed()
    return next(r for r in core.recordings() if r['id'] == rec_id)


# ---- pictures: a piece's artwork, a playlist's, an artist's (library.py says where they are kept) --------------
PICTURE_LIMIT = 4 * 1024 * 1024  # what a phone makes, a square JPEG of about 1000 px, is far smaller


async def _picture(request: Request) -> bytes | None:
    """The JPEG of a PUT, as the phone makes it; None for a DELETE (taken away). Read as it comes, and stopped as
    soon as it is too large (413); 400 for what is not a JPEG."""
    if request.method == 'DELETE':
        return None
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > PICTURE_LIMIT:
            raise HTTPException(413)
    if not body.startswith(b'\xff\xd8\xff'):
        raise HTTPException(400)
    return bytes(body)


def _picture_file(path: Path) -> FileResponse:
    """A picture, asked for by its version (?v=): the same address is always the same picture."""
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type='image/jpeg', headers={'Cache-Control': 'private, max-age=31536000, immutable'})


@app.get('/api/recordings/{rec_id}/artwork')
def piece_artwork(rec_id: int):
    return _picture_file(core.lib.piece_artwork(_recording(rec_id)))


@app.api_route('/api/recordings/{rec_id}/artwork', methods=['PUT', 'DELETE'])
async def keep_piece_artwork(rec_id: int, request: Request):
    """A piece's artwork from the phone (PUT), or taken away (DELETE). 409 while its file is not wholly here (in
    iCloud only): the artwork is kept by the file's content."""
    _recording(rec_id)
    if not core.lib.set_piece_artwork(rec_id, await _picture(request)):
        raise HTTPException(409)
    core.library_changed()
    return {'ok': True}


@app.get('/api/playlists/{playlist_id}/artwork')
def playlist_artwork(playlist_id: int):
    return _picture_file(core.lib.playlist_cover(playlist_id))


@app.api_route('/api/playlists/{playlist_id}/artwork', methods=['PUT', 'DELETE'])
async def keep_playlist_artwork(playlist_id: int, request: Request):
    """A playlist's artwork (PUT), or its cover of its pieces again (DELETE)."""
    if not core.lib.set_playlist_cover(playlist_id, await _picture(request)):
        raise HTTPException(404)
    core.playlists_changed()
    return {'ok': True}


@app.get('/api/pictures')
def artist_picture(name: str):
    return _picture_file(core.lib.artist_picture(name))


@app.api_route('/api/pictures', methods=['PUT', 'DELETE'])
async def keep_artist_picture(name: str, request: Request):
    """An artist's picture, by name (PUT), or taken away (DELETE)."""
    if not name.strip():
        raise HTTPException(400)
    core.lib.set_artist_picture(name, await _picture(request))
    core.library_changed()
    return {'ok': True}


@app.get('/api/playlists')
def playlists():
    return core.lib.playlists()


@app.post('/api/playlists')
def new_playlist(name: str = Body(), ids: list[int] = Body(default=[])):
    """A new playlist (歌单), with these pieces if any."""
    try:
        made = core.lib.new_playlist(name, ids)
    except ValueError:
        raise HTTPException(400) from None
    core.playlists_changed()
    return made


@app.patch('/api/playlists/{playlist_id}')
def change_playlist(playlist_id: int, change: dict = Body()):
    """One change: name (a new one); add (ids, at its end, each once); remove (an id); or move (an id) to index."""
    lib = core.lib
    if lib.playlist(playlist_id) is None:
        raise HTTPException(404)
    try:
        if set(change) == {'name'}:
            done = lib.rename_playlist(playlist_id, str(change['name'])) is not None
        elif set(change) == {'add'} and isinstance(change['add'], list):
            done = lib.add_to_playlist(playlist_id, [int(i) for i in change['add']]) is not None
        elif set(change) == {'remove'}:
            done = lib.remove_from_playlist(playlist_id, int(change['remove']))
        elif set(change) == {'move', 'index'}:
            done = lib.move_in_playlist(playlist_id, int(change['move']), int(change['index']))
        else:
            raise HTTPException(400)
    except (TypeError, ValueError):
        raise HTTPException(400) from None
    if not done:  # the piece is not in it
        raise HTTPException(404)
    core.playlists_changed()
    return lib.playlist(playlist_id)


@app.delete('/api/playlists/{playlist_id}')
def delete_playlist(playlist_id: int):
    """The playlist goes; its pieces stay in the library."""
    if not core.lib.delete_playlist(playlist_id):
        raise HTTPException(404)
    core.playlists_changed()
    return {'ok': True}


@app.delete('/api/recordings')
def delete(ids: list[int] = Body(embed=True)):
    """One piece or several, in one go, their files to the Mac's Trash."""
    core.delete_recordings(ids)
    for rec_id in ids:
        _notes.pop(rec_id, None)  # its number may be given to the next new piece
    return {'ok': True}


@app.post('/api/uploads')
async def upload(request: Request, name: str):
    """One file for the library folder's Uploaded/ (made with the first), its content the request's body and name its
    file name: a MIDI file or a recording, yours as if put there by hand ("name (2)" when the name is taken). It is
    written to a hidden file as it comes, stopped as soon as it is too large, and named only once it is whole;
    nothing stays of one that fails."""
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
    folder = core.lib.uploaded
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


@app.post('/api/pairing/reset')
async def reset_pairing(request: Request):
    """A new token: every phone, and every Home Screen app, has to be paired again, at once: the connections pages
    have open are closed too, and opened again only with the new token. Asked on this Mac only."""
    global TOKEN
    if request.client is None or request.client.host not in {'127.0.0.1', '::1'}:
        return JSONResponse({'error': 'mac_only'}, status_code=403)
    TOKEN = saved_token(fresh=True)
    write_pairing()
    for socket in list(_open):
        try:
            await socket.close(code=1008, reason='paired_anew')
        except RuntimeError:  # it was closing already
            pass
    log.debug('paired anew: a new token, %d connection(s) closed', len(_open))
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
    _open.add(socket)
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
        _open.discard(socket)
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


SAID = {'start_session': 'turned on, with her', 'continue_session': 'with her', 'pause_session': 'away from her',
        'resume': 'her reply heard, listen on',
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
        _open.add(socket)
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
                getattr(conversation, command)()  # hush, unhush, turn_on

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
                                'continue_session': 'unhush', 'start_session': 'turn_on'}
                    if not isinstance(payload, dict) or payload.get('type') not in commands or set(payload) != {'type'}:
                        await socket.close(code=1008, reason='Unsupported voice message')
                        return
                    item = (commands[payload['type']], None)
                    log.debug('the phone: %s (she is %s)', SAID[payload['type']], conversation.state)
                    # Quiet, or heard again, at once: even while she is still busy with a request; the rest follows
                    # in order.
                    if item[0] == 'hush':
                        conversation.hushed.set()
                    elif item[0] in {'unhush', 'turn_on'}:
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
            _open.discard(socket)
            if conversation is not None:  # quiet at once; a piece paused while she listened plays on
                conversation.hushed.set()
                phone.disconnect()
                worker.submit(conversation.hush)
            for task in tasks:
                task.cancel()
            await asyncio.wait(tasks)
            print('  · voice off', flush=True)


def main(argv=None):
    global TOKEN, core, version, _where
    from . import show_details
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--debug', action='store_true', help='also show what happens in detail, with times')
    parser.add_argument('--app', action='store_true', help='as the Mac app runs it: no guide here, and the address '
                        'with its token only in pairing.json')
    args = parser.parse_args(argv)
    show_details(args.debug)
    import uvicorn
    from .chat import startup
    from .core import Core, installed, version as running
    from .library import DATA_DIR, move_old_library
    from .web_tls import CA_FILE, local_name, prepare_https
    sockets = listen(PORT), listen(CERT_PORT)  # first: nothing starts while another PianaSoul runs
    move_old_library()  # then the library from before, ahead of the phone's token and her voice's files
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
    _where = host, tls
    write_pairing()
    certificate_page(tls, sockets[1])
    # The address with its token is a key to the library: shown in a terminal, but never in the Mac app's log.
    address = f'https://{host}:{PORT}/' + ('' if args.app else f'#token={TOKEN}')
    print(voice_parts().describe(core.label, 'app', address=address) if her else
          startup('app', library=f'{core.lib.count()} piece(s) in {core.lib.music_dir}', address=address), flush=True)
    if new and not args.app:  # the one step by hand, shown only now: the iPhone learns to trust this Mac
        print(f'\nFirst time on this Mac: let the iPhone trust it, once.\n\n'
              f'  1. Send     Finder shows {CA_FILE}: right-click > Share > AirDrop\n'
              f'  2. Install  iPhone Settings > General > VPN & Device Management > Install\n'
              f'  3. Trust    Settings > General > About > Certificate Trust Settings\n'
              f'              > turn on "PianaSoul on {local_name().removesuffix(".local")}"\n'
              f'  4. Open     the address above in Safari > Share > Add to Home Screen\n\n'
              f'  The file: {tls / CA_FILE}\n'
              f'  Or open http://{host}:{CERT_PORT}/ on the phone: it shows how.\n', flush=True)
        if sys.platform == 'darwin':
            subprocess.run(['open', '-R', str(tls / CA_FILE)], check=False)
    if not args.app:
        said = ' or '.join(f'"{w}"' for w in voice_parts().wake.words) if her else ''
        print('Keep the address private.\n' + (f'Tap the orb and say {said}, then your request. ' if her else '') +
              'Control+C quits.\n', flush=True)
    # Only what matters shows, as with ./run.sh voice: no request log, no "running on 0.0.0.0" to mistake for the
    # address, and no error for quitting while a phone in the background never says goodbye (it stops in 2 s).
    logging.getLogger('uvicorn.error').addFilter(
        lambda record: 'timeout graceful shutdown exceeded' not in record.getMessage())
    config = uvicorn.Config(app, ssl_certfile=str(tls / 'cert.pem'), ssl_keyfile=str(tls / 'key.pem'),
                            ws_max_size=1 << 20, ws_max_queue=4, timeout_graceful_shutdown=2, log_level='warning')
    # SIGTERM (the Mac app quitting) or Control+C: uvicorn stops serving, then hands the signal back. It ends the
    # program as an exit, never at once, so that the piano is closed first: the notes already sent finish.
    for stop in (signal.SIGTERM, signal.SIGINT):
        signal.signal(stop, lambda *_: sys.exit(0))
    try:
        uvicorn.Server(config).run(sockets=[sockets[0]])
    finally:
        core.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
