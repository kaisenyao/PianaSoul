<h1 align="center">PianaSoul</h1>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/tagline-dark.svg">
    <img src="docs/tagline-light.svg" alt="Give your piano a soul" width="420">
  </picture>
</p>

<p align="center">
  Choose a piece on your phone, and your own piano plays it.<br>
  Or ask her for one, by voice or in a chat: if it is not in your library yet, she finds a performance online and
  turns it into MIDI first.
</p>

<br>

<p align="center">
  <img src="docs/iphone-voice.gif" alt="The orb: gray while she is off, in color while she listens and speaks" width="250">&nbsp;&nbsp;&nbsp;
  <img src="docs/app-chat.gif" alt="A conversation: asked for the Fantaisie-Impromptu, she plays it, and turns it down when asked" width="250">&nbsp;&nbsp;&nbsp;
  <img src="docs/app-player.gif" alt="The library, then a piece in the player: its notes scroll by, the volume goes down, and its details slide up" width="250">
</p>

<br>

<p align="center">
  <a href="https://pianasoul.kaisenyao.com"><picture><source media="(prefers-color-scheme: dark)" srcset="docs/demo-button-dark.svg"><img src="docs/demo-button-light.svg" alt="Try the demo in your browser" width="172"></picture></a>&nbsp;&nbsp;
  <a href="https://github.com/kaisenyao/PianaSoul/releases/latest/download/PianaSoul.dmg"><picture><source media="(prefers-color-scheme: dark)" srcset="docs/download-button-dark.svg"><img src="docs/download-button-light.svg" alt="Download PianaSoul for Mac" width="208"></picture></a>
</p>

<br>

## What it does

PianaSoul runs on a Mac, quietly, in the menu bar. The Mac keeps your library and plays the piano; everything else
happens on your phone.

- **Your library is a folder on your Mac.** Add MIDI files in Finder or from your phone, and they are on the phone at
  once, filed under their pianist, with search, artists, favorites, playlists and what you played lately.
- **It plays your piano.** Notes and pedal go over MIDI, as from someone playing a MIDI keyboard: to a digital piano
  over USB or Bluetooth, to an iPad or another Mac that makes the sound, or to a sound app on the Mac itself. With no
  piano at all, Built-in Piano, a sampled grand, plays through the Mac's own speakers.
- **She finds what you don't have.** Ask for a piece, or a pianist's version of it, and she looks on YouTube and
  Bilibili for a real piano performance and turns it into MIDI: notes, timing, dynamics and pedal. It starts to play a
  few seconds after it is found, while the rest is still being transcribed. Your own recordings work too.
- **Talk to her.** Tap the orb on the phone and say what you would like to hear, or type it in a chat. She is
  Jasmine ("Xiǎomíng Tóngxué" in Chinese), and she answers in your language.

The first two need no key and no account. The last two are the advanced features, which you turn on in the Mac app
when you want them.

<br>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/how-it-works-dark.svg">
    <img src="docs/how-it-works-light.svg" alt="You ask for a piece; PianaSoul on the Mac finds it in the library (or finds a recording and transcribes it) and sends the notes over Wi-Fi to Pianoteq on an iPad, which plays them through a digital piano" width="900">
  </picture>
</p>

<br>

## Hear a transcription

Glenn Gould's rehearsal of Bach's Partita No. 2 in C minor, BWV 826, from the
[Glenn Gould Official Channel on YouTube](https://www.youtube.com/watch?v=WqwZC-yLYI4), and PianaSoul's transcription of
it ([the MIDI file](docs/gould-partita-2-rehearsal.mid)) played by Pianoteq 9 on an iPad: default settings, no
manual edits. They play side by side, and the sound switches between them every ten seconds.

<video src="https://github.com/user-attachments/assets/037fc112-9d36-47ac-b44a-c47c4e3aa064" width="100%" controls></video>

<br>

## Get started

PianaSoul needs a Mac with Apple silicon and macOS 15 or later, a phone on the same Wi-Fi, and for the sound a piano
that takes MIDI, a sound app such as Pianoteq, or Built-in Piano on the Mac itself.

### Install it

1. [Download PianaSoul](https://github.com/kaisenyao/PianaSoul/releases/latest/download/PianaSoul.dmg), open the DMG
   and drag PianaSoul to the Applications folder.
2. The first time you open it, macOS stops it because it isn't notarized by Apple (the DMG's window shows these steps
   too): click Done, not Move to Trash. Then in System Settings › Privacy & Security, click Open Anyway near the
   bottom, click Open Anyway again in the dialog that appears, then enter your password.

### Set it up

PianaSoul then walks you through four steps. Each can be done later, or changed, in its settings.

1. **Your library.** The folder is `~/Music/PianaSoul` unless you choose another. With Open at login selected,
   PianaSoul runs whenever you are logged in to the Mac.
2. **Advanced features**, if you want them: Online Search & Transcription, and Assistant (her). Either one needs a
   one-time download of about 373 MB; she also needs a key of your own (see Her keys below).
3. **Your piano.** Choose how it connects (USB, Bluetooth, Network or Software), then click it, and Test plays a few
   notes on it. With no piano found, PianaSoul chooses Built-in Piano.
4. **Your phone.** Scan the first code to install a certificate that lets the phone trust this Mac, and only this
   Mac. Then scan the second code to open PianaSoul, and add it to the Home Screen. The second code is the key to
   your library: show it only to phones you trust.

The phone app is a web page that the Mac serves on your own network, made for iPhone, in Safari or on the Home
Screen; it also runs in Chrome on Android.

### Her keys

She needs an API key of your own, entered in the Mac app's Settings › Advanced: for conversation and speech, OpenAI,
or Alibaba Cloud Model Studio where OpenAI is out of reach, as in China mainland; for her voice, the Mac's own voice
needs nothing, while ElevenLabs and Model Studio sound more natural. Keys stay in the keychain on your Mac, and what
you use is billed to your own account. [How to get each key](docs/keys.md), step by step.

<br>

## Using it

- **On the phone.** Swipe right for the sidebar: Library, Artists, Playlists, Favorites, Recently Played and her
  chats. Tap a piece to play it; touch and hold one for what can be done with it. The + in the library adds pieces:
  files from the phone, or a search online by name or with a video link.
- **The player.** Swipe up for a piece's details. The … at the top right has Edit Info, Share MIDI… and Show Piano
  View, a piano in place of the artwork with its keys going down as the piece plays them. Settings › Player Style ›
  Classic turns the player into a classic one with a click wheel.
- **Start and Stop.** In Edit Info, a piece can play only part of itself, an intro or an outro left out: drag the
  yellow frame around its notes, or set the start and stop where the playhead is, and hear the part on the piano
  first. The file itself does not change.
- **Choosing the piano from the phone.** Settings › Devices › Piano lists every piano the Mac sees, and Test plays a
  few notes on the one chosen.
- **Her.** Tap the orb and say what you would like, no name needed the first time; after that, say her name before a
  new request. She plays pieces and playlists, several in a row, pauses, goes on, makes it louder or softer, and
  keeps what she played as a playlist when you ask. New Chat starts afresh.
- **Leaving the page changes nothing.** The Mac plays the piano and the phone is its remote control: lock the phone
  or close the page, and the music goes on.

Settings › Help › User Guide on the phone explains the rest.

<br>

## How it works

PianaSoul is a Mac app and a phone app that talk over your own Wi-Fi. The Mac does the work; nothing goes through a
server of ours.

- **The library** is a folder of MIDI files on the Mac, kept in order as Music keeps its media folder, by pianist.
- **The piano** is played over MIDI, from the Mac: USB, Bluetooth, a network session to an iPad or another Mac, a
  sound app, or Built-in Piano.
- **Transcription** turns a solo piano recording into performance MIDI, on the Mac, with
  [Transkun](https://github.com/Yujia-Yan/Transkun). A new piece plays as soon as its first seconds are transcribed.
- **Her.** What you say on the phone is heard on the Mac, her name and where you stop, then goes to the model you
  chose, with your key, which answers with what to do. She can only play pieces that were actually found, in your
  library or online, never one made up.

PianaSoul grew out of [piano2midi](https://github.com/kaisenyao/piano2midi), open source, which turns solo piano
recordings into performance MIDI and first put her voice, chat and playback together.

### Timing

Wi-Fi delivers notes unevenly, which breaks the rhythm. So to a network session each note leaves the Mac a moment
early, together with the time it should sound, and the iPad plays it on time:

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/timing-dark.svg">
    <img src="docs/timing-light.svg" alt="Sent when due, notes reach the piano unevenly; sent early with their time, the iPad waits and plays them evenly" width="900">
  </picture>
</p>

USB and Bluetooth deliver notes evenly, so a piano connected that way plays them at once.

### Privacy

Your library, your chats and your settings stay on your Mac. The phone talks only to your Mac, on your own network.
What you say to her goes to the services you chose, with your own keys; searches, and the pictures of the pianists
you choose, come from YouTube and Bilibili. Beyond that, PianaSoul goes online only to check for updates and to
download what you turn on: Built-in Piano's sounds and the advanced features.

<br>

## Updates

PianaSoul checks for a new version once a day and when you choose Check for Updates in the Mac app's Settings ›
About, and installs it when you say so. What you allowed it, the keychain and Bluetooth, stays allowed through
updates.

## When something goes wrong

- **Nothing sounds:** the Mac app's menu and Settings › Piano say what it sees, and Test plays a few notes. An iPad
  drops out of a network session when its sound app closes or the Mac sleeps: connect it again in Audio MIDI Setup.
- **The phone says Mac Not Connected:** the Mac is asleep or off, PianaSoul is not running, or the two are not on the
  same Wi-Fi.
- **A search can't reach YouTube:** Bilibili's results still come. In China mainland, turn YouTube off under Sources
  in the phone's Settings.
- **Anything else:** use Send Feedback in the phone's Settings, or write to
  [pianasoul@kaisenyao.com](mailto:pianasoul@kaisenyao.com).

## Limits

Transcription works best on solo piano: some notes may be missed or added, and the sustain pedal is either down or up.
Recordings are limited to 60 minutes. Download only videos you are permitted to download.

<br>

## License and credits

PianaSoul is not open source. © 2026 PianaSoul. All rights reserved.

It is built on open-source work, with thanks:

- Transcription uses [Transkun](https://github.com/Yujia-Yan/Transkun) (MIT), whose pretrained weights were trained on
  the MAESTRO dataset (CC BY-NC-SA 4.0): for non-commercial use.
- Hearing her name uses [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) (Apache 2.0), and telling speech from
  silence [Silero VAD](https://github.com/snakers4/silero-vad) (MIT).
- Built-in Piano plays the Salamander Grand Piano by Alexander Holm (CC BY 3.0), trimmed and converted for
  PianaSoul.
- The classic player's shell, screen and click wheel come from Mitch Ivin's
  [ipod-classic](https://github.com/mitchivin/ipod-classic) (MIT), and its taps on an iPhone from
  [ios-haptics](https://github.com/tijnjh/ios-haptics) (MIT), with Material Icons (Apache 2.0) and Source Sans 3 (SIL
  Open Font License).
- The tagline is set in [Great Vibes](https://fonts.google.com/specimen/Great+Vibes) (SIL Open Font License).
- Updates come with [Sparkle](https://sparkle-project.org) (MIT).
- The advanced features install third-party packages from PyPI under their own licenses, among them FFmpeg (GPL),
  run as a separate program, and mutagen (GPL).

Every license is included in the app, and listed in its Settings › About › Acknowledgments.
