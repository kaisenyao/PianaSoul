# How PianaSoul Basic works

The models behind [PianaSoul Basic](../README.md), the path of a request, and the details of transcription.

## Models

These are the project's defaults, as tested. Shell settings can override the chat, speech-recognition and
speech-output models; the chat/voice startup banner shows the active selections.

- **Understanding requests, choosing pieces and calling playback tools:** OpenAI
  [`gpt-6-luna`](https://developers.openai.com/api/docs/models/gpt-6-luna), through the OpenAI API. The tested
  default for this focused assistant.
- **Turning your spoken request into text:** OpenAI
  [`gpt-transcribe`](https://developers.openai.com/api/docs/models/gpt-transcribe), through the OpenAI API. Set
  `PIANASOUL_BASIC_LANGUAGES=zh,en` for Chinese and English hints.
- **Speaking replies and greetings:** ElevenLabs `eleven_v4_turbo`, through the ElevenLabs API; macOS speech is the
  fallback.
- **Converting a piano recording into MIDI:** Transkun `2.0.1`, pretrained `2.0`, on this computer, on the CPU by
  default; Apple MPS is opt-in.
- **Detecting the wake words:** a sherpa-onnx bilingual keyword-spotting model, on this computer. No cloud key
  needed.
- **Detecting speech and silence:** Silero VAD through sherpa-onnx, on this computer. No cloud key needed.

The recommendation is to keep this working combination rather than change models during normal use. A stronger chat
model is optional and does not replace reliable playback state or connection handling. No local language model or
extra GPU is required for this setup.

### Optional chat provider

The code also supports Anthropic for chat. `ANTHROPIC_API_KEY` is needed only if you choose that provider; it is not
used by the tested OpenAI setup. The project's Anthropic default is `claude-haiku-5-5`. To explicitly select it, set
`PIANASOUL_BASIC_MODEL=anthropic:claude-haiku-5-5` and supply your own Anthropic API key. Voice recognition still
needs `OPENAI_API_KEY`. This alternative has been tested less than the OpenAI setup.

## The path of a request

A spoken request follows this path:

1. The wake word and the recording, on this computer.
2. Speech recognition, at OpenAI.
3. The chat model and a library lookup.
4. If the piece is new: search, download and transcription on this computer.
5. The connection check.
6. Her spoken introduction.
7. MIDI playback.

Pieces already in the library skip step 4. Network calls and local transcription both contribute to the wait, so
response time varies. The Mac is not running the OpenAI or ElevenLabs models locally.

When the chat searches YouTube, it looks for a real performance where the piano carries the music: solo piano first,
then piano with voice, never a band mix or a remix, and no falling-notes videos or tutorials.

### Playback tools

Playback tools separate selection from current-piece control: `play_recording(id)` accepts only IDs offered by
library search or transcription in the current request; `control_playback(action)` accepts no recording ID. Chat and
voice share the same execution path and the application's current/pending playback state. Invalid selections are
returned to the model for correction without overwriting the existing piece. Exact short commands are handled
locally; natural requests use the model rather than an expanding set of phrase-matching rules.

## Transcription

### An example

Glenn Gould's rehearsal of Bach's Partita No. 2 in C minor, BWV 826: 1 min 47 s of audio became 1,229 notes with
velocities from 36 to 93 and one short press of the sustain pedal, with default settings and no manual edits, in
about a minute on 2 CPU cores. Gould plays this music almost without pedal, and the transcription reflects that.
[Hear it beside the original](../../README.md#hear-a-transcription), or download
[the MIDI file](../../docs/gould-partita-2-rehearsal.mid).

### The task list

CSV columns are `url,name,status,audio_path,midi_path,error`. Status is `pending`, `processing`, `done` or `failed`
(blank means pending). Work files (the original audio, raw MIDI and statistics) stay in the hidden `output/.cache/`
folder.

Exit codes are 0 for success, 1 for task failures, 2 for task-list or usage errors and 130 for interruption.

### Cleanup

By default, cleanup trims same-pitch overlaps, removes soft-pedal events and makes sure the sustain pedal is released
at the end. `--legato`, `--keep-soft-pedal` and `--pedal-ramps` change that; see `./run.sh --help`.
