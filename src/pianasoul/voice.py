"""Voice: say "Hey Jasmine" (or the Chinese name), then ask for a piece. The same assistant as ./run.sh chat,
with ears and a voice.

    ./run.sh voice

Her names, 小茗同学 and Jasmine, are heard on this computer by sherpa-onnx keyword spotting, which takes a name
without training. Speech recognition uses OpenAI gpt-transcribe
(OPENAI_API_KEY). Replies are spoken by ElevenLabs (ELEVENLABS_API_KEY and PIANASOUL_VOICE_ID), or by the
macOS voice without them. Pause after the name and she says she is listening; or say the name and the request
in one breath. After her reply you may go on without the name, unless the piano plays. Add --debug to see what
happens in detail, with times (her name heard, how long recognition and her answer took), or --lines to hear
her ready-made lines (lines.py) once each.
Install the extras once: uv pip install --python .venv/bin/python -e ".[voice]"
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
import logging
import os
import queue
import re
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request
import warnings
import wave
from collections import deque
from collections.abc import Iterator
from pathlib import Path

import numpy as np

from .lines import LINES, line, recorded
from .play import clock
from .playback_commands import WAKE_WORDS, is_chinese, match_command, name_said, spell_names, strip_fillers

log = logging.getLogger(__spec__.name)

warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=FutureWarning)

SAMPLE_RATE = 16000
FRAME = 1280  # 80 ms of audio per read
FRAME_S = FRAME / SAMPLE_RATE
WAKE_THRESHOLD = float(os.environ.get("PIANASOUL_WAKE_THRESHOLD", "0.2"))  # lower hears more, wakes more by mistake
WAKE_MODEL = "sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20"  # Chinese and English, about 33 MB to download
WAKE_MODEL_URL = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/{WAKE_MODEL}.tar.bz2"
WAKE_PARTS = {"encoder": "encoder-epoch-13-avg-2-chunk-16-left-64.onnx",
              "decoder": "decoder-epoch-13-avg-2-chunk-16-left-64.onnx",
              "joiner": "joiner-epoch-13-avg-2-chunk-16-left-64.onnx",
              "tokens": "tokens.txt", "lexicon": "en.phone"}
WAKE_SCORE = 2.0  # how strongly the spotter favors the wake words; 2.0 did best on the owner's voice with piano
# Other ways to say English words in wake words, besides the dictionary's. Chinese speakers often say
# "jas-MEEN"; with these a test recording of the owner went from 0 to 9 wakes out of 10.
EXTRA_PRONUNCIATIONS = {"JASMINE": ["JH AE1 Z M IY1 N", "JH AE1 S M IY1 N"]}
STT_MODEL = os.environ.get("PIANASOUL_STT_MODEL", "gpt-transcribe")
TTS_MODEL = os.environ.get("PIANASOUL_TTS_MODEL", "eleven_v4_turbo")
VOICE_ID = os.environ.get("PIANASOUL_VOICE_ID", "")
ELEVENLABS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice}/stream?output_format=pcm_24000"
# The ElevenLabs defaults, as on its text-to-speech page: stability 0.5, similarity 0.75, no style exaggeration.
VOICE_SETTINGS = {"stability": 0.5, "similarity_boost": 0.75, "style": 0.0, "use_speaker_boost": True, "speed": 1.0}
# How her greetings are said, picked by ear by the owner: freer than replies, with some character, quick.
GREETING_SETTINGS = {**VOICE_SETTINGS, "stability": 0.25, "style": 0.4, "speed": 1.15}
# How her lines about a setting are said: plainly, as the state it is now. Said with feeling, "volume up" came out
# loud and excited, and "volume down" whispered.
STEADY = ("louder", "softer", "loudest", "silent")
STEADY_SETTINGS = {**VOICE_SETTINGS, "stability": 0.85}
# Her ready-made lines (lines.py) and how each is said: made once with her ElevenLabs voice and kept in the data
# folder (voice/). The greetings and the lines about a setting as above; the rest as her replies are, so that a
# ready-made line and the reply after it sound like one person. Without ElevenLabs the macOS voice says them.
READY = {**{text: GREETING_SETTINGS for lines in LINES["greet"].values() for text in lines},
         **{text: VOICE_SETTINGS for text in recorded()},
         **{text: STEADY_SETTINGS for key in STEADY for lines in LINES[key].values() for text in lines}}
# Silero voice activity detection tells speech from silence and the piano.
VAD_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx"
# A request is heard from 1.6 s before the wake word was heard (it is heard about half a second late), her name
# included: a short request said in the same breath ("小茗同学，停止") is often over by then.
PRE_ROLL_FRAMES = 20
GREET_AFTER_S = 1.5  # her name (or "嗯") alone, then this much quiet: she greets; going on, it is one request
STALL_S = 4.0  # heard as it is said, no words come back this long after you stopped: it is heard whole instead
START_TIMEOUT_S = 6.0  # after her greeting, wait this long for the request to begin
END_SILENCE_S = 0.7  # heard whole: this much silence (after the detector's own 0.3 s) ends the request
MAX_REQUEST_S = 15.0
FOLLOW_UP_S = 6.0  # after her reply you may go on without her name, unless the piano is playing

# A simplified-Chinese example sentence with an English title, so that a transcript in Chinese uses simplified
# characters and keeps English song names in English. No language is fixed: she understands any (SPEC M5.5).
STT_PROMPT = "以下是普通话和英语的句子。我想听 Adele 的 Someone Like You。暂停。继续。"

PROMPT_ECHOES = {STT_PROMPT, STT_PROMPT.split("。", 1)[-1], "暂停。继续。"}
# Spelling hints besides the library's names: the commands that are easiest to mishear.
STT_COMMAND_HINTS = ["暂停", "停止播放", "继续播放", "从头弹"]

VOICE_NOTE = """

Your names are 小茗同学 in Chinese and Jasmine in English; people say one of them to wake you, and you call yourself
by the one that fits the language of the conversation. Spoken requests are answered aloud by a young, cheerful
voice, so sound natural and a little playful. Use one short sentence when you can and no lists. If the user asks
for a link or a file path, say it is on the screen and include it; it is shown but not read aloud.

Speaking first: a spoken request (typed is not true) is answered aloud while the listener waits in silence. When you
must look something up before you can answer (search_library with the piece's other names, search_online,
library_info), begin that response with one short sentence in the listener's language, written before those tool
calls in the same response: it is said at once while you look, and it comes instead of "saying nothing yet" above.
It answers what they just said, naming the piece or the thing as they said it, and says what you are about to do,
never that it is done, worked or failed. No question, no empty filler, and never about yourself in the third person.
Keep it short (about twelve Chinese characters, or eight English words); before a search online, it may also say that
this takes a moment. When library_matches already has the piece, call play_recording at once with no words before
it, and for pause, resume, retry, restart, next, previous or cancel call control_playback at once: the application
says a line once it is done. After the tools, say only what is new, and never a piece's name twice."""


def speakable(text: str) -> str:
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # [title](link) -> title
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[`*_#>\[\]]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def hints(lib, limit: int = 50) -> list[str]:
    """The names in the library, to help recognize them: the artists' and the performers' first (fewer, and the
    most often misheard), then the pieces', the newest first."""
    from .library import title_parts

    names = [title_parts(r["title"], r["composer"], r["performer"]) for r in reversed(lib.playable())]
    people = [n[k] for n in names for k in ("artist", "performer")]
    return [w for w in dict.fromkeys([*people, *(n["piece"] for n in names)]) if w][:limit]


def clean_transcript(text: str, prompt: str | None) -> str:
    text = text.strip()
    if prompt and text and (text[:6] == prompt[:6] or text in PROMPT_ECHOES):
        return ""  # the recognizer repeating its prompt on noise, not something the user said
    return text


def to_wav(audio: np.ndarray) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
    return buf.getvalue()


class Microphone:
    """The default input in 80 ms frames. macOS can break a running stream when audio devices change; then
    no audio arrives, and the microphone is opened again. Waiting for audio never blocks Control+C."""

    def __init__(self):
        self.frames: queue.Queue[np.ndarray] = queue.Queue(maxsize=200)  # 16 s while she is busy
        self.stream = None
        self.open()

    def open(self) -> None:
        import sounddevice as sd

        if self.stream is not None:
            self.stream.close()
        self.stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=FRAME,
                                     callback=self._received)
        self.stream.start()

    def _received(self, data, *_) -> None:
        with contextlib.suppress(queue.Full):
            self.frames.put_nowait(data[:, 0].copy())

    def read(self) -> np.ndarray:
        try:
            return self.frames.get(timeout=2)
        except queue.Empty:
            print("  (the microphone stopped; opening it again)", flush=True)
            self.open()
        try:
            return self.frames.get(timeout=5)
        except queue.Empty:
            raise RuntimeError("the microphone sends no audio; check the input in System Settings > Sound") from None

    def drain(self) -> None:
        """Drop audio that piled up while we were busy or speaking."""
        with contextlib.suppress(queue.Empty):
            while True:
                self.frames.get_nowait()


def wake_model() -> Path:
    """The keyword spotting model's folder, downloaded on first use (only the files needed are kept)."""
    from .library import DATA_DIR

    folder = DATA_DIR / "models" / WAKE_MODEL
    if all((folder / name).is_file() for name in WAKE_PARTS.values()):
        return folder
    print("Downloading the wake word model (about 33 MB, once)…", flush=True)
    folder.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(WAKE_MODEL_URL, timeout=60) as resp, tarfile.open(fileobj=resp, mode="r|bz2") as tar:
        for member in tar:
            name = member.name.rsplit("/", 1)[-1]
            if member.isfile() and name in WAKE_PARTS.values():
                part = folder / (name + ".part")
                part.write_bytes(tar.extractfile(member).read())
                part.replace(folder / name)
    missing = [name for name in WAKE_PARTS.values() if not (folder / name).is_file()]
    if missing:
        raise RuntimeError(f"the wake word download lacks {', '.join(missing)}")
    return folder


def download(url: str, path: Path) -> Path:
    """Fetch url to path once."""
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_suffix(".part")
        with urllib.request.urlopen(url, timeout=60) as resp:
            part.write_bytes(resp.read())
        part.replace(path)
    return path


def vad_model() -> Path:
    from .library import DATA_DIR

    return download(VAD_URL, DATA_DIR / "models" / "silero_vad.onnx")


class Speech:
    """Tells whether someone is speaking, frame by frame (Silero voice activity detection via sherpa-onnx). One
    for each conversation: it keeps what it has heard."""

    def __init__(self, model: Path):
        import sherpa_onnx

        silero = sherpa_onnx.SileroVadModelConfig(model=str(model), threshold=0.5, min_silence_duration=0.3,
                                                  min_speech_duration=0.2)
        self.vad = sherpa_onnx.VoiceActivityDetector(sherpa_onnx.VadModelConfig(silero_vad=silero,
                                                                                 sample_rate=SAMPLE_RATE), 30)

    def __call__(self, frame: np.ndarray) -> bool:
        """Feed one frame of 16-bit audio; True while speech is going on."""
        self.vad.accept_waveform(frame.astype(np.float32) / 32768.0)
        while not self.vad.empty():  # finished stretches of speech are not needed
            self.vad.pop()
        return self.vad.is_speech_detected()

    def reset(self) -> None:
        self.vad.reset()


def keyword_lines(phrase: str, lexicon: dict[str, list[str]], limit: int = 8) -> list[str]:
    """A wake word in the model's spelling, one line per way of saying it: Chinese as split pinyin, English as
    phonemes from the lexicon (word -> pronunciations).

    "Hey Jasmine" -> ["HH EY1 JH AE1 Z M AH0 N @Hey_Jasmine", "HH EY1 JH AE1 Z M IY1 N @Hey_Jasmine", ...]
    """
    from pypinyin import pinyin
    from pypinyin.contrib.tone_convert import to_finals_tone, to_initials

    spellings = [""]
    for word in re.findall(r"[\u4e00-\u9fff]+|[A-Za-z']+", phrase):
        if word.isascii():
            if word.upper() not in lexicon:
                raise ValueError(f'the wake word "{phrase}" uses "{word}", which the model does not know')
            ways = lexicon[word.upper()]
        else:
            parts = []
            for syllable, *_ in pinyin(word):
                parts += [p for p in (to_initials(syllable, strict=False), to_finals_tone(syllable, strict=False))
                          if p] or [syllable]
            ways = [" ".join(parts)]
        spellings = [f"{s} {w}".strip() for s in spellings for w in ways][:limit]
    if spellings == [""]:
        raise ValueError(f'the wake word "{phrase}" has no Chinese characters or English words')
    return [f"{s} @" + "_".join(phrase.split()) for s in spellings]


class WakeWord:
    """Her names (WAKE_WORDS), listened for on this computer with sherpa-onnx keyword spotting (no training
    needed). The model is loaded once; each conversation listens with its own listener()."""

    def __init__(self):
        import sherpa_onnx

        folder = wake_model()
        lexicon: dict[str, list[str]] = {}
        for entry in (folder / WAKE_PARTS["lexicon"]).read_text(encoding="utf-8").splitlines():
            word, *phones = entry.split()
            lexicon.setdefault(word, []).append(" ".join(phones))
        for word, ways in EXTRA_PRONUNCIATIONS.items():
            lexicon.setdefault(word, []).extend(w for w in ways if w not in lexicon[word])
        keywords = folder / "keywords.txt"
        keywords.write_text("\n".join(k for w in WAKE_WORDS for k in keyword_lines(w, lexicon)) + "\n",
                            encoding="utf-8")
        self.spotter = sherpa_onnx.KeywordSpotter(
            tokens=str(folder / WAKE_PARTS["tokens"]), encoder=str(folder / WAKE_PARTS["encoder"]),
            decoder=str(folder / WAKE_PARTS["decoder"]), joiner=str(folder / WAKE_PARTS["joiner"]),
            keywords_file=str(keywords), keywords_threshold=WAKE_THRESHOLD, keywords_score=WAKE_SCORE, num_threads=1,
            provider="cpu")
        self.words = WAKE_WORDS

    def listener(self) -> "WakeListener":
        return WakeListener(self.spotter)


class WakeListener:
    """One conversation's ear for her name: the model is shared, what it has heard is its own."""

    def __init__(self, spotter):
        self.spotter = spotter
        self.reset()

    def heard(self, frame: np.ndarray) -> str | None:
        """Feed one frame of 16-bit audio; the wake word if it was just said."""
        self.stream.accept_waveform(SAMPLE_RATE, frame.astype(np.float32) / 32768.0)
        while self.spotter.is_ready(self.stream):
            self.spotter.decode_stream(self.stream)
            found = self.spotter.get_result(self.stream)
            if found:
                self.reset()
                return found.replace("_", " ")
        return None

    def reset(self) -> None:
        self.stream = self.spotter.create_stream()


class Transcriber:
    """OpenAI speech recognition, as the request is said (listen) or uploaded whole once it has ended; the library's
    titles and names are passed as spelling hints."""

    def __init__(self, model: str = STT_MODEL):
        from openai import OpenAI

        if not os.environ.get("OPENAI_API_KEY"):  # needed for hearing even when another provider does the chat
            raise RuntimeError("speech recognition needs OPENAI_API_KEY")
        self.client, self.model = OpenAI(timeout=20), model  # bounded: a hung upload does not keep her listening
        self.label = f"OpenAI {model}, as you speak"

    def hints(self, lib) -> dict:
        """The prompt and, for gpt-transcribe, the spelling hints: the same for both ways of hearing."""
        fixed = [*WAKE_WORDS, *STT_COMMAND_HINTS]
        keywords = {"keywords": fixed + hints(lib, limit=50 - len(fixed))} if self.model == "gpt-transcribe" else {}
        return {"prompt": STT_PROMPT, **keywords}

    def listen(self, lib) -> Live:
        """One request heard as it is said."""
        return Live(self.client, {"model": self.model, **self.hints(lib)})

    def __call__(self, audio: np.ndarray, lib) -> str:
        """The whole request, uploaded once it has ended."""
        request = dict(model=self.model, file=("request.wav", to_wav(audio), "audio/wav"), response_format="text")
        try:
            text = self.client.audio.transcriptions.create(**request, **self.hints(lib))
        except Exception:
            text = self.client.audio.transcriptions.create(**request)  # without the optional hints
        return spell_names(clean_transcript(str(text), STT_PROMPT))


class Live:
    """One request heard as it is said: an OpenAI realtime transcription session, fed the request's 80 ms frames,
    in which OpenAI decides where what is said ends (semantic VAD). Its news, read on her thread (news): ("speech",
    "") when someone starts to speak; ("text", all said so far) once they have paused and every part is written
    out; ("broken", why) when the connection fails. Nothing here waits on the network."""

    def __init__(self, client, transcription: dict):
        self.outbox: queue.SimpleQueue = queue.SimpleQueue()  # audio to send; None ends the session
        self.inbox: queue.SimpleQueue = queue.SimpleQueue()  # what the session says, or why it failed
        self.order: list[str] = []  # the parts OpenAI has ended, in the order said
        self.texts: dict[str, str] = {}
        self.talking, self.told, self.last = False, 0, 0.0
        threading.Thread(target=self._run, args=(client, transcription), name="her ears", daemon=True).start()

    def _run(self, client, transcription: dict) -> None:
        try:  # no on_reconnecting: a session connected again would have lost what was said
            with client.realtime.connect(extra_query={"intent": "transcription"}) as conn:
                conn.session.update(session={"type": "transcription", "audio": {"input": {
                    "format": {"type": "audio/pcm", "rate": 24000}, "transcription": transcription,
                    "turn_detection": {"type": "semantic_vad", "eagerness": "high"}, "noise_reduction": None}}})
                threading.Thread(target=self._read, args=(conn,), name="her ears, heard", daemon=True).start()
                while (pcm := self.outbox.get()) is not None:
                    conn.input_audio_buffer.append(audio=base64.b64encode(pcm.tobytes()).decode())
        except Exception as exc:
            self.inbox.put(exc)

    def _read(self, conn) -> None:
        try:
            for event in conn:
                self.inbox.put(event)
            self.inbox.put(ConnectionError("the connection closed"))
        except Exception as exc:
            self.inbox.put(exc)

    def send(self, pcm: np.ndarray) -> None:
        """An 80 ms frame at 16 kHz, sent at 24 kHz (as the session takes it): 1920 samples, joined to the last."""
        frame = pcm.astype(np.float32)
        up = np.interp(np.arange(1920) * (FRAME / 1920), np.arange(FRAME + 1), np.r_[self.last, frame])
        self.last = frame[-1]
        self.outbox.put(up.astype("<i2"))

    def close(self) -> None:
        self.outbox.put(None)

    def news(self) -> list[tuple[str, str]]:
        news = []
        while True:
            try:
                event = self.inbox.get_nowait()
            except queue.Empty:
                break
            kind = getattr(event, "type", "")
            if isinstance(event, Exception) or kind == "error" or kind.endswith("transcription.failed"):
                why = getattr(getattr(event, "error", None), "message", None) or str(event)
                return [*news, ("broken", why)]
            if kind == "input_audio_buffer.speech_started":
                self.talking = True
                news.append(("speech", ""))
            elif kind == "input_audio_buffer.committed":
                self.talking = False
                self.order.append(event.item_id)
            elif kind.endswith("input_audio_transcription.completed"):
                self.texts[event.item_id] = spell_names(clean_transcript(event.transcript, STT_PROMPT))
        if not self.talking and len(self.order) > self.told and all(i in self.texts for i in self.order):
            self.told = len(self.order)
            news.append(("text", " ".join(t for i in self.order if (t := self.texts[i]))))
        return news


class Speaker:
    """Her voice: ElevenLabs, arriving while it is made; the macOS voice without it, or when it fails."""

    def __init__(self):
        self.key = os.environ.get("ELEVENLABS_API_KEY", "")
        self.cloud = bool(self.key and VOICE_ID)
        self.label = f"ElevenLabs {TTS_MODEL}" if self.cloud else "macOS (set ELEVENLABS_API_KEY and PIANASOUL_VOICE_ID)"
        self.making = threading.Lock()  # one recording made at a time (clip)

    def stream(self, text: str) -> Iterator[np.ndarray]:
        """text as pieces of 24 kHz PCM, the first as soon as it arrives."""
        text = speakable(text)
        if not text:
            return
        if self.cloud and text in READY:  # one of her ready-made lines: its recording
            try:
                yield self.clip(text, READY[text])
                return
            except Exception as exc:  # made as any other sentence instead
                print(f"  (her recording of \"{text}\" is not there: {exc})", flush=True)
        if self.cloud:
            sent, started = False, time.monotonic()
            try:
                with urllib.request.urlopen(self._request(text), timeout=30) as resp:
                    rest = b""
                    while chunk := resp.read(4800):  # 0.1 s of audio
                        chunk, rest = rest + chunk, b""
                        if len(chunk) % 2:
                            chunk, rest = chunk[:-1], chunk[-1:]
                        if chunk:
                            if not sent:
                                log.debug("her voice: first sound after %.2f s", time.monotonic() - started)
                            sent = True
                            yield np.frombuffer(chunk, dtype="<i2")
                return
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode(errors="replace")[:200]
                print(f"  (ElevenLabs error {exc.code}: {detail}; using the Mac voice)", flush=True)
                if exc.code in (401, 402, 403):  # key, payment or permission: stop trying
                    self.cloud = False
            except Exception as exc:  # network trouble: the Mac voice for this sentence only
                if sent:
                    print(f"  (ElevenLabs stopped: {exc})", flush=True)
                    return
                print(f"  (ElevenLabs failed: {exc}; using the Mac voice)", flush=True)
        yield mac_voice(text)

    def _request(self, text: str, settings: dict = VOICE_SETTINGS) -> urllib.request.Request:
        body = {"text": text, "model_id": TTS_MODEL, "voice_settings": settings}
        return urllib.request.Request(ELEVENLABS_URL.format(voice=VOICE_ID), data=json.dumps(body).encode(),
                                      headers={"xi-api-key": self.key, "Content-Type": "application/json"})

    def clip_path(self, text: str, settings: dict = VOICE_SETTINGS) -> Path:
        from .library import DATA_DIR

        key = hashlib.sha1(json.dumps([VOICE_ID, TTS_MODEL, settings, text]).encode()).hexdigest()[:16]
        return DATA_DIR / "voice" / f"{key}.wav"

    def clip(self, text: str, settings: dict = VOICE_SETTINGS) -> np.ndarray:
        """The voice saying text as 24 kHz audio, made once and then read from the data folder (voice/). One is made
        at a time: a line she needs while it is being made in the background (prepare_lines) is waited for and read,
        not made, and paid for, twice."""
        path = self.clip_path(text, settings)
        if not path.is_file():
            with self.making:
                if not path.is_file():
                    with urllib.request.urlopen(self._request(text, settings), timeout=30) as resp:
                        pcm = resp.read()
                    path.parent.mkdir(parents=True, exist_ok=True)
                    part = path.with_suffix(".part")
                    with wave.open(str(part), "wb") as w:
                        w.setnchannels(1)
                        w.setsampwidth(2)
                        w.setframerate(24000)
                        w.writeframes(pcm[: len(pcm) // 2 * 2])
                    part.replace(path)
        with wave.open(str(path)) as w:
            return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def mac_voice(text: str) -> np.ndarray:
    """The macOS voice saying text, as 24 kHz PCM; Chinese in a Chinese voice."""
    from .audio import ffmpeg_exe

    voice = ["-v", "Tingting"] if is_chinese(text) else []
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "voice.aiff"
        subprocess.run(["say", *voice, "-o", str(path), text], check=True, timeout=60)
        pcm = subprocess.run([ffmpeg_exe(), "-v", "error", "-i", str(path), "-ar", "24000", "-ac", "1",
                              "-f", "s16le", "-"], check=True, capture_output=True, timeout=60).stdout
    return np.frombuffer(pcm, dtype="<i2")


def prepare_lines(speaker: Speaker) -> None:
    """Her ready-made lines (READY) in her ElevenLabs voice, made once and kept, so that each is said at once. Run
    in the background (VoiceParts): a line not made yet is made when she first says it."""
    if not speaker.cloud:
        return
    missing = [(text, how) for text, how in READY.items() if not speaker.clip_path(text, how).is_file()]
    if missing:
        print(f"Preparing her lines ({len(missing)}, once)…", flush=True)
    for text, how in missing:
        try:
            speaker.clip(text, how)
        except Exception as exc:  # that one is made when it is first said
            print(f"  (could not prepare \"{text}\": {exc})")
            if isinstance(exc, urllib.error.HTTPError) and exc.code in (401, 402, 403):
                break  # key, payment or permission: the others would fail too


def preview_lines(speaker: Speaker) -> None:
    """Say each of her ready-made lines once, to hear how they sound."""
    import sounddevice as sd

    if not speaker.cloud:
        print("Her lines are made with ElevenLabs: set ELEVENLABS_API_KEY and PIANASOUL_VOICE_ID.")
        return
    for text, how in READY.items():
        print(f"  {text}", flush=True)
        sd.play(speaker.clip(text, how), 24000)
        sd.wait()
        time.sleep(0.4)


def finished_remark(player, chinese: bool, since: float = 0.0) -> str | None:
    """Consume a natural finish once, one second after the scheduled audio ends; a finish from before since (when
    she began to listen) is dropped."""
    if player is None or player.completed is None:
        return None
    if player.state != "stopped":
        player.completed = None
        return None
    _, ended = player.completed
    if time.perf_counter() < ended + 1.0:
        return None
    player.completed = None
    if ended < since:
        return None
    return line("finished", chinese)


def finish_interaction(assistant, speaker, answer: str, was_playing: bool, taps_held: int | None = None) -> None:
    """Check automatic resumption before speaking a final reply, so a fault is reported once. taps_held: the phone's
    player as it was when the piece was paused for her to listen; used since, what it did stands."""
    def say(text):
        if text:
            print(f"assistant> {text}\n", flush=True)
            speaker.say(text)

    player = assistant.player
    if was_playing and player is not None and player.state == "paused":
        if assistant.touched_playback or taps_held is not None and assistant.taps != taps_held:  # she or the phone
            say(answer)
            print(f"  ⏸ {player.current[1]}  {clock(player.position)}", flush=True)
            return
        piece, at = player.current, player.position
        try:
            player.look_for_piano()  # a piano that is gone is found before she speaks, and said once
            say(answer)
            if player.state == "paused" and player.current == piece:  # unless something else was chosen meanwhile
                player.go_on(at)
        except LookupError as exc:
            speaker.say(assistant.piano_news(assistant.piano_missing(exc)))
        except FileNotFoundError:  # its file was deleted while it waited
            speaker.say(assistant.piano_news({"error": "recording_unavailable"}))
    else:
        say(answer)


class VoiceParts:
    """The slow parts, loaded once and used by one conversation after another."""

    def __init__(self):
        self.stt, self.wake, self.vad = Transcriber(), WakeWord(), vad_model()
        self.speaker = Speaker()
        # In the background, so that the service starts at once: with a new voice they take about a minute. A
        # thread that ends with the program: a recording cut short stays a .part file, and is made again.
        threading.Thread(target=prepare_lines, args=(self.speaker,), name="her lines", daemon=True).start()

    def detectors(self) -> tuple[WakeListener, Speech]:
        """A conversation's own ears for her name and for speech: what one has heard is never another's."""
        return self.wake.listener(), Speech(self.vad)

    def describe(self, model: str, name: str = "voice", **more: str) -> str:
        """The start-up lines, as ./run.sh chat prints its own: what does each job."""
        from .chat import search_label, startup

        return startup(name, chat=model, hearing=self.stt.label, voice=self.speaker.label, search=search_label(),
                       **more)


class Conversation:
    """Her ears and voice for the conversation the Assistant keeps, fed 80 ms microphone frames: from the Mac's own
    microphone or the phone's.

    out decides where her voice goes. status(state) reports what she is doing; speak(pieces, hushed) plays 24 kHz PCM
    pieces and returns once they have been heard, or as soon as hushed is set; turn_end(conversation, sleep) follows
    each reply, and then calls conversation.sleep() (wait for her name again) or conversation.resume() (listen on
    without it).

    Tapped on (turn_on), she takes the first request without her name; back on the orb (unhush), she waits for it.
    While hushed is set (no phone is with her: it shows another page, or she is gray) she neither listens nor
    speaks. Whatever she was asked is still answered, on screen, and aloud from the moment hushed is cleared; the
    piano is not touched.
    """

    def __init__(self, assistant, chat, parts: VoiceParts, out):
        self.assistant, self.chat, self.out = assistant, chat, out
        self.stt, self.speaker = parts.stt, parts.speaker
        self.wake, self.speech = parts.detectors()
        self.hushed = threading.Event()
        self.saying: threading.Thread | None = None  # a line said meanwhile, while she is busy with what it says
        self.recent: deque[np.ndarray] = deque(maxlen=PRE_ROLL_FRAMES)  # for one-breath requests
        self.frames: list[np.ndarray] = []
        self.state = "waiting"
        self.started = False
        self.silent = self.elapsed = 0.0
        self.dialogue_until = 0.0
        self.deaf_until = 0.0  # tapped on while the piano played: the notes already sent sound on till then
        self.was_playing = False
        self.live: Live | None = None  # the request heard as it is said, while she listens to it
        self.name: str | None = None  # the wake word the request began with
        self.heard: str | None = None  # all said so far, while it asks for nothing yet (her name alone)
        self.unheard = False  # said and not yet heard: no text covers it
        self.taps_held = 0  # the phone's player as it was when the piece playing was paused for her (hold_piece)
        self.follow_up_s = FOLLOW_UP_S
        self.listening_since = 0.0
        self.news_is_old()

    def status(self, state: str) -> None:
        self.state = state
        # A piece she prepared starts only while she waits for her name: listening on, its sound would be taken for
        # speech (end_turn), and it would cut into what she is saying or hearing.
        if state == "waiting":
            self.assistant.may_play.set()
        else:
            self.assistant.may_play.clear()
        self.out.status(state)

    def speak(self, pieces) -> None:
        """Her voice out, after what she is still saying; a failure loses this sentence only."""
        self.said()
        self._speak(pieces)

    def _speak(self, pieces) -> None:
        if self.hushed.is_set():
            return
        try:
            self.out.speak(pieces, self.hushed)
        except Exception:
            traceback.print_exc()

    def say(self, text: str, meanwhile: bool = False) -> None:
        """meanwhile: return as she begins, so that what she says she will do goes on while she says it."""
        if text and self.hushed.is_set():
            log.debug("not said aloud, no one is with her: %s", text[:60])
        elif text and meanwhile:
            self.said()
            self.saying = threading.Thread(target=self._speak, args=(self.speaker.stream(text),), daemon=True)
            self.saying.start()
        elif text:
            self.speak(self.speaker.stream(text))

    def said(self) -> None:
        """Wait for a line said meanwhile to have been heard (or for hushed)."""
        if self.saying is not None:
            self.saying.join()
            self.saying = None

    def hush(self) -> None:
        """Quiet: set hushed (from any thread) to stop her voice at once, then call this on the conversation's
        thread. She waits for her name again, and a piece paused while she listened plays on. Back by then (hushed
        cleared again), there is nothing to do."""
        if self.hushed.is_set() and self.state != "waiting":
            log.debug("quiet while %s: she waits for her name", self.state)
            self.sleep()

    def unhush(self) -> None:
        """Back: clear hushed (from any thread) so that she is heard again at once, a reply she is still making
        included, then call this on the conversation's thread. She listens for her name from then on, unless a
        reply of hers is out: then the phone says when it has been heard."""
        if self.hushed.is_set():
            return
        if self.state == "speaking":
            log.debug("back while her reply is out: the phone says when it has been heard")
            return
        log.debug("back: she listens for her name")
        self.news_is_old()
        self.sleep()

    def turn_on(self) -> None:
        """Tapped on: clear hushed (from any thread), then call this on the conversation's thread. As if her name had
        been said, the piece playing pauses and she listens for a request without her name, for as long as after
        her greeting; not spoken to, the piece plays on. After it, as after any reply, her name again once the
        conversation pauses. Coming back to the orb (unhush) is not turning her on."""
        if self.hushed.is_set():
            return
        if self.state == "speaking":
            log.debug("on while her reply is out: the phone says when it has been heard")
            return
        self.news_is_old()
        self.hold_piece()
        # A piece paused on a network session still sounds for its lead (the notes already sent): not taken for you.
        lead = self.assistant.player.lead if self.was_playing else 0.0
        log.debug("on: she listens for a request without her name%s", " (the piece pauses meanwhile)"
                  if self.was_playing else "")
        self.follow_up_s = START_TIMEOUT_S + lead
        self.resume()
        self.deaf_until = time.monotonic() + lead

    def news_is_old(self) -> None:
        """She is listening from now on: what happened to the piece before (it ended, or the piano went away),
        when she could not say so, is not said later."""
        self.listening_since = time.perf_counter()
        if self.assistant.player is not None:
            self.assistant.player.lost = None

    def end_turn(self, sleep: bool | None = None) -> None:
        """Her reply is out. Listen on without her name, but not while the piano plays: its sound would be
        taken for speech."""
        self.said()
        if self.hushed.is_set():
            log.debug("her turn is over, no one is with her: she waits for her name")
            self.sleep()
            return
        if sleep is None:
            player = self.assistant.player
            sleep = player is not None and player.state == "playing"
        log.debug("her turn is over: %s", "she waits for her name" if sleep else "she listens on, without it")
        if self.state != "speaking":
            self.status("speaking")
        self.out.turn_end(self, sleep)

    def greet(self, word: str) -> None:
        """Her name and a pause: a ready-made greeting, then she listens for the request."""
        self.status("speaking")
        greeting = line("greet", is_chinese(word))  # in the language of the name used; not kept: it says nothing
        print(f"assistant> {greeting}", flush=True)
        self.say(greeting)
        self.follow_up_s = START_TIMEOUT_S  # the request should start soon
        self.end_turn(sleep=False)

    def piano_unavailable(self) -> None:
        """The piano went away while playing: the piece waits where it stopped, and she says so (her line is in the
        conversation already: Core)."""
        notice = self.assistant.playback_reply({"error": "playback_connection_lost"})
        self.status("speaking")
        self.say(notice)
        self.follow_up_s = FOLLOW_UP_S
        self.end_turn()

    def remark(self) -> None:
        """Something happened to the piece on its own: the piano went away, or the list played to its end. She
        says so once, in the language of the last request."""
        player = self.assistant.player
        if player is not None and player.lost:
            player.lost = None
            self.piano_unavailable()
            return
        remark = finished_remark(player, self.assistant.chinese(), self.listening_since)
        if remark:
            self.status("speaking")
            print(f"assistant> {remark}\n", flush=True)
            self.assistant.keep("assistant", remark)
            self.say(remark)
            self.follow_up_s = FOLLOW_UP_S
            self.end_turn()

    def hold_piece(self) -> None:
        """The piece playing pauses while she listens; it plays on once she has answered (finish_playback). A piece
        she prepared waits from now on, before she looks at what plays (status)."""
        self.assistant.may_play.clear()
        player = self.assistant.player
        playing = player is not None and player.state == "playing"
        if playing and not self.was_playing:
            self.taps_held = self.assistant.taps
        self.was_playing = self.was_playing or playing
        if player is not None:
            player.completed = None  # a new request takes priority over a closing remark
        if self.was_playing:
            player.pause()
        self.assistant.touched_playback = False

    def begin(self, name: str | None = None) -> None:
        """Start listening to a request (name: the wake word it began with); the piano pauses meanwhile. It is heard
        as it is said (Live), from the moments before it, or, when that cannot be, whole once it has ended."""
        self.hold_piece()
        log.debug("listening to a request%s", " (the piece pauses meanwhile)" if self.was_playing else "")
        self.stop_hearing()
        self.name, self.heard, self.unheard = name, None, True  # a request begins with something said
        self.frames = list(self.recent)
        self.started = False
        self.silent = self.elapsed = 0.0
        try:
            self.live = self.stt.listen(self.assistant.lib)
        except Exception as exc:  # heard whole instead
            print(f"  (hearing as you speak not possible: {exc}; heard whole)", flush=True)
        for pcm in self.frames if self.live else ():
            self.live.send(pcm)
        self.status("listening")

    def stop_hearing(self, why: str = "") -> None:
        """No more of this request is sent to be heard as it is said; why: it failed, and what is still unheard is
        heard whole (hear)."""
        if self.live:
            self.live.close()
            self.live = None
            if why:
                print(f"  (hearing as you speak: {why}; heard whole)", flush=True)

    def resume(self) -> None:
        """Listen on without her name, for a while."""
        self.frames.clear()
        self.recent.clear()
        self.speech.reset()
        self.wake.reset()
        self.dialogue_until = time.monotonic() + self.follow_up_s
        self.status("conversation")

    def finish_playback(self, answer: str = "") -> None:
        finish_interaction(self.assistant, self, answer, self.was_playing, self.taps_held)
        self.was_playing = False

    def sleep(self) -> None:
        """Wait for her name again; a piece paused for the request plays on."""
        self.stop_hearing()
        self.finish_playback()
        self.frames.clear()
        self.recent.clear()
        self.speech.reset()
        self.wake.reset()
        self.dialogue_until = 0.0
        self.status("waiting")

    def frame(self, pcm: np.ndarray) -> None:
        """Feed 80 ms of 16 kHz mono PCM16."""
        if len(pcm) != FRAME:
            raise ValueError("expected 1280 samples of 16 kHz mono PCM16")
        if self.hushed.is_set() or self.state in {"thinking", "speaking"}:
            return  # quiet, her own voice, or frames sent before the phone heard she was busy
        self.recent.append(pcm)
        speaking = self.speech(pcm)
        if self.state == "conversation":
            if time.monotonic() > self.dialogue_until:
                self.sleep()
            else:
                if speaking and time.monotonic() >= self.deaf_until:
                    self.begin()  # the request starts with the frames just before it, this one included
                return
        if self.state == "waiting":
            word = self.wake.heard(pcm)
            if not word:
                self.remark()
                return
            log.debug("heard %s", word)
            self.begin(word)
            return
        self.hear(pcm, speaking)

    def hear(self, pcm: np.ndarray, speaking: bool) -> None:
        """The request, heard: as it is said, OpenAI deciding where it ends; or, without that (it failed, or no
        words came back), whole once the fixed rule ends it. Each text is all said so far: her name or "嗯" alone,
        she listens on, and what follows is the same request; quiet after it, she greets (nothing_asked)."""
        self.frames.append(pcm)
        self.elapsed += FRAME_S
        self.started = self.started or speaking
        self.silent = 0.0 if speaking else self.silent + FRAME_S
        full = self.elapsed >= MAX_REQUEST_S
        if full:
            self.stop_hearing("the longest a request may be" if self.unheard else "")
        if self.live:
            self.live.send(pcm)
            for kind, text in self.live.news():
                if kind == "broken":
                    self.stop_hearing(text)
                    break
                if kind == "speech":
                    self.unheard = True
                elif self.answer_if_asked(text, "as you spoke"):
                    return
            if self.live and self.unheard and self.silent >= STALL_S:  # (silent counts from begin: her name too)
                self.stop_hearing("no words came back")
        elif speaking:
            self.unheard = True
        if not self.live and self.unheard and (full or self.silent >= END_SILENCE_S and (self.started or
                                                                                         self.elapsed >= 1.2)):
            started = time.monotonic()
            self.status("thinking")  # the phone sends nothing meanwhile: her thread is busy with the upload
            try:
                text = self.stt(np.concatenate(self.frames).astype(np.float32) / 32768, self.assistant.lib)
            except Exception as exc:  # this request is lost, not the conversation
                print(f"  (speech recognition failed: {exc})", flush=True)
                self.sleep()
                return
            log.debug("recognized whole in %.1f s", time.monotonic() - started)
            if self.answer_if_asked(text, "whole"):
                return
            self.status("listening")
        if not self.unheard and (full or self.silent >= GREET_AFTER_S):
            self.nothing_asked()

    def answer_if_asked(self, text: str, how: str) -> bool:
        """All said so far, heard: answered if it asks for something (True); her name or a filler alone, not yet."""
        self.unheard, self.heard = False, text
        log.debug("heard %s, %.1f s after the last words: %s", how, self.silent, text or "(nothing clear)")
        if not strip_fillers(text):
            return False
        self.respond(text)
        return True

    def nothing_asked(self) -> None:
        """Her name (or "嗯", or nothing clear) and quiet: with her name, she greets and listens for the request, as
        after a pause; without it (tapped on, or after her reply), she waits for her name again."""
        self.stop_hearing()
        name = self.name or name_said(self.heard or "")
        if name:
            self.greet(name)
        else:
            self.sleep()

    def respond(self, text: str) -> None:
        print(f"you> {text}", flush=True)
        self.stop_hearing()
        self.status("thinking")
        answer, started = "", time.monotonic()
        try:
            answer = self.assistant.ask(self.chat, text, speak=self.say)
            log.debug("answered in %.1f s", time.monotonic() - started)
        except Exception as exc:  # that request is dropped; the conversation goes on, and a paused piece plays on
            print(f"  (model error: {exc}; that request is dropped)", flush=True)
        self.status("speaking")
        self.finish_playback(answer)
        self.follow_up_s = FOLLOW_UP_S
        self.end_turn(sleep=True if match_command(text) == "goodbye" else None)  # after goodbye: her name again


class Speakers:
    """The Mac's own speakers. She speaks while her voice is being made; afterwards the microphone's backlog,
    her own voice, is dropped before listening goes on."""

    def __init__(self, mic: Microphone):
        self.mic = mic

    def status(self, state: str) -> None:
        pass

    def speak(self, pieces, hushed=None) -> None:
        import sounddevice as sd

        with sd.RawOutputStream(samplerate=24000, channels=1, dtype="int16") as out:
            for pcm in pieces:
                out.write(np.ascontiguousarray(pcm, dtype="<i2").tobytes())

    def turn_end(self, conversation: Conversation, sleep: bool) -> None:
        self.mic.drain()
        if sleep:
            conversation.sleep()
        else:
            conversation.resume()


def main(argv: list[str] | None = None) -> int:
    import argparse

    from . import show_details
    from .core import Core
    from .library import move_old_library

    parser = argparse.ArgumentParser(prog="pianasoul voice", description="Say her name, then ask for a piece.")
    parser.add_argument("--lines", action="store_true", help="just listen to her ready-made lines")
    parser.add_argument("--debug", action="store_true", help="also show what happens in detail, with times")
    args = parser.parse_args(argv)
    show_details(args.debug)
    move_old_library()  # before her lines are kept in the data folder
    if args.lines:
        preview_lines(Speaker())
        return 0
    try:
        core = Core(VOICE_NOTE)
    except Exception as exc:
        print(f"Cannot start the chat model: {exc}", file=sys.stderr)
        return 2
    try:
        print("Loading speech recognition and the wake word…", flush=True)
        parts, mic = VoiceParts(), Microphone()
    except ImportError as exc:
        print(f"Voice needs the extras: uv pip install --python .venv/bin/python -e \".[voice]\" ({exc})",
              file=sys.stderr)
        return 2
    except (OSError, RuntimeError, ValueError) as exc:  # no network for the first download, or no OpenAI key
        print(f"Cannot start her voice: {exc}", file=sys.stderr)
        return 2
    conversation = Conversation(core.assistant, core.chat, parts, Speakers(mic))
    print(parts.describe(core.label))
    said = " or ".join(f'"{w}"' for w in parts.wake.words)
    print(f"Say {said}, then your request. Control+C quits.\n", flush=True)
    try:
        while True:
            conversation.frame(mic.read())
    except KeyboardInterrupt:
        core.assistant.stop()
        print()
        return 0
    except RuntimeError as exc:  # the microphone could not be opened again
        print(f"Voice stopped: {exc}", file=sys.stderr)
        return 1
    finally:
        core.close()


if __name__ == "__main__":
    raise SystemExit(main())
