# Her name, her voice and how she listens

More about the voice assistant in [PianaSoul Basic](../README.md#voice): `./run.sh voice` on the Mac, and
`./run.sh web-voice` with an iPhone.

## Her name

She answers to "Hey Jasmine" and to Xiǎomíng Tóngxué. Speech recognition hears only the sound of a name, so whatever
Chinese characters it writes for her name are shown and passed on as the ones she is set up with. To give her other
names, list one or more, separated by commas:

```bash
echo 'export PIANASOUL_BASIC_WAKE_WORDS="Hey Maestro,Hey Piano"' >> ~/.zshrc
```

- Write a Chinese name in Chinese characters; any name works, with no training. English names must use words from
  the pronouncing dictionary; if one is unknown, `./run.sh voice` says so when it starts.
- The names are hers too: she calls herself by them (without "Hey"), and she greets you in Chinese when you call her
  by a Chinese name, in English otherwise.
- Good names have three to five syllables and are not words you say often. A short English name works better with
  "Hey" in front.
- If she often misses you, run `./run.sh voice --debug` to see what she hears, and lower
  `PIANASOUL_BASIC_WAKE_THRESHOLD` (default `0.2`, for example to `0.15`); if she wakes by herself, raise it. If you
  say an English name differently from the dictionary, add your way to `EXTRA_PRONUNCIATIONS` in `voice.py`, as
  "Jasmine" also accepts "jas-MEEN".

## Her voice and greetings

- **Voice:** the ElevenLabs voice in `PIANASOUL_BASIC_VOICE_ID`. The default model is `eleven_v4_turbo`;
  `PIANASOUL_BASIC_TTS_MODEL=eleven_v4` is richer and slower. Without ElevenLabs the macOS voice speaks.
- **Greetings:** what she says when you pause after her name, a few lines each in Chinese and English, picked at
  random. Change the lines in `GREETINGS` in `voice.py`, and how they are said (a little livelier than her answers)
  in `GREETING_SETTINGS`. They are made once with her voice and kept in `output/.cache/voice`, and made again when
  you change them. Hear them all with `./run.sh voice --greetings`.

## What she says, and when

- **Replies** use the language of the latest request. She speaks as a musical companion playing for you, with small
  variations in the control replies; recording and performer credits stay accurate.
- **Pause, continue and start over** get a short acknowledgment when they succeed. Short commands use local replies
  without an extra model call.
- **Her name alone** gets a local greeting, without a model-wait acknowledgment or a second greeting in the same
  listening turn.
- **While the chat model takes longer** than about three seconds, a short acknowledgment can fill the silence once.
  Before an online search or preparation, she acknowledges the wait without promising a completion time.
- **Before the music,** a playback introduction is spoken after the connection check.
- **After a piece finishes naturally,** she leaves one second after the last scheduled MIDI events, then says a
  short, randomly chosen closing line. Pausing, stopping, changing pieces or failed playback does not trigger one.
- **When playback fails,** she gives a brief, nontechnical connection reminder instead of a success acknowledgment.
  Diagnostic messages stay in the terminal.

## How she listens

- **Wake words:** heard on the computer by [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) keyword spotting.
- **When you have finished:** [Silero](https://github.com/snakers4/silero-vad) voice activity detection tells your
  voice from silence and the piano, so the music or her own greeting is not taken for your request.
- **What you said:** OpenAI `gpt-transcribe`, with your library's titles and names as spelling hints and the
  languages in `PIANASOUL_BASIC_LANGUAGES`.

`./run.sh voice --debug` also shows each time she hears her name, and which one.
