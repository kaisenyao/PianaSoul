"""Run by the user: real model, simulated playback, temporary library, no microphone or TTS."""
import argparse
import json

import mido

import places  # noqa: F401  (first: the library, data and cache folders are throwaway ones)
from pianasoul.chat import TOOLS, instructions
from pianasoul.llm import make_chat
from test_playback_interactions import PlaybackTests

# Each case starts with 贝加尔湖畔 paused at 22 seconds.
# 'replay' after a disconnection is ambiguous: allow resume or restart of the SAME piece.
# Explicit 'from the beginning' cases still require zero; continuation cases require 22.
CASES = [
    ('呃，好像断开了，你可以再帮我重新播放吗？', 'replay'),
    ('小明同学好像又断开了，你可以看看能帮我继续播放吗？', 'resume'),
    ('小茗同学，我想继续听这首歌。', 'resume'),
    ('我已经连接好了。', 'pending'),
    ('我尝试连接了，你可以再试试吗？', 'resume'),
    ('刚才中断了，能从断掉的地方接着弹吗？', 'resume'),
    ('请从开头再给我弹一遍刚才那首。', 'restart'),
    ('Could you carry on with the piece we were hearing?', 'resume'),
    ('The connection is fixed. Can you give it another try?', 'pending'),
    ('这首先停一下，我想说句话。', 'pause'),
    ('不用重试了，取消刚才的播放吧。', 'cancel'),
    ('先别继续，我还没连好。', 'no_action'),
    ('等我连好了再继续，现在先别播。', 'no_action'),
    ('换成德彪西的月光吧。', 'new_song'),
    ('好像又断开了，能帮我继续播放这首吗？', 'offline_resume'),
    ('好像又断开了，能从头重新播放吗？', 'offline_restart'),
    # Titled in English, without names in other languages: found by her own search, not looked for online.
    ('弹一下肖邦的夜曲', 'library:Chopin - Nocturne Op. 9 No. 2 (Lang Lang)'),
    ('我想听贝多芬的月光奏鸣曲', 'library:Beethoven - Moonlight Sonata, I. Adagio sostenuto (Valentina Lisitsa)'),
    ('来一首致爱丽丝', 'library:Beethoven - Für Elise (Lang Lang)'),
    ('弹一下卡农吧', 'library:Pachelbel - Canon in D (Kassia)'),
    # Not in the library: online only after the library was searched.
    ('弹一首陈奕迅的十年', 'online'),
    # Company: about the user's own music she looks in the library first; a chat is a chat. Nothing plays.
    ('我最近都听了些什么呀？', 'info'),
    ('我的曲库里有几首贝多芬的曲子？', 'info'),
    ('我最近什么歌听得最多？', 'info'),
    ('有些老歌好久没听了，你帮我翻一翻吧。', 'info'),
    ('今天有点累，陪我聊聊天吧。', 'chat'),
]
ENGLISH_TITLES = [expected.split(':', 1)[1] for _, expected in CASES if expected.startswith('library:')]


def add_english_titles(fixture):
    for title in ENGLISH_TITLES:
        path = fixture.root / 'Music' / (title + '.mid')
        midi = mido.MidiFile(); track = mido.MidiTrack(); midi.tracks.append(track)
        track.append(mido.Message('note_on', note=60, velocity=64, time=0))
        track.append(mido.Message('note_off', note=60, time=480 * 240))
        midi.save(path)
        fixture.lib.add(path, title)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--list', action='store_true', help='list cases without calling the model')
    args = parser.parse_args()
    if args.list:
        for index, (text, expected) in enumerate(CASES, 1): print(index, expected, text)
        return 0
    print('真实模型测试：只模拟播放和连接，不操作实际设备、曲库或语音输出。')
    failures = 0
    for index, (text, expected) in enumerate(CASES, 1):
        fixture = PlaybackTests()
        result = 'PASS'
        trace = []
        try:
            fixture.setUp()
            add_english_titles(fixture)
            fixture.prime()
            if expected == 'pending':
                fixture.connected = False
                fixture.a.control_playback('resume')
                fixture.connected = True
            if expected.startswith('offline_'): fixture.connected = False
            original = fixture.a.run_tool
            def safe_tool(name, arguments):
                trace.append((name, arguments))
                if name not in {'control_playback', 'search_library', 'play_recording', 'library_info'}:
                    return json.dumps({'error': 'Only playback and the library are enabled in this test.'})
                return original(name, arguments)
            fixture.a.run_tool = safe_tool
            chat, label = make_chat(instructions(), TOOLS)
            answer = fixture.a.ask(chat, text)
            if fixture.player._thread: fixture.player._thread.join()
            player, pending = fixture.player, fixture.a.pending
            if expected.startswith('library:'):
                okay = player.state == 'playing' and player._from == 0 and player.current[1] == expected[8:] \
                    and not any(name == 'search_online' for name, _ in trace)
            elif expected == 'online':
                names = [name for name, _ in trace]
                okay = 'search_online' in names and 'search_library' in names[:names.index('search_online')] \
                    and player.current[1] == '贝加尔湖畔'
            elif expected in {'resume', 'pending', 'restart', 'new_song', 'replay'}:
                offset = 0 if expected in {'restart', 'new_song'} else 22
                title = '月光' if expected == 'new_song' else '贝加尔湖畔'
                offsets = {0, 22} if expected == 'replay' else {offset}
                okay = player.state == 'playing' and player._from in offsets and player.current[1] == title
            elif expected.startswith('offline_'):
                offset = 0 if expected == 'offline_restart' else 22
                okay = pending is not None and pending.from_s == offset and '设备和钢琴' in answer
            elif expected in {'info', 'chat'}:
                looked = any(name == 'library_info' for name, _ in trace)
                played = any(name in {'play_recording', 'control_playback'} for name, _ in trace)
                okay = player.state == 'paused' and player.position == 22 and not played and (looked or expected == 'chat')
            elif expected == 'cancel':
                okay = player.state == 'stopped' and pending is None
            else:
                okay = player.state == 'paused' and player.position == 22
                if expected == 'no_action':
                    okay = okay and not any(name == 'play_recording' or name == 'control_playback' and a.get('action') in {'resume','restart','retry'} for name,a in trace)
            if not okay: result = 'FAIL'
        except Exception as exc:
            result = 'ERROR (' + type(exc).__name__ + ')'
        finally:
            fixture.doCleanups()
        if result != 'PASS': failures += 1
        print(f'{index:02d} {result}: {text}')
        if result != 'PASS': print('  tool calls:', json.dumps(trace, ensure_ascii=False))
    print(f'Result: {len(CASES)-failures}/{len(CASES)} passed')
    return int(failures != 0)


if __name__ == '__main__':
    raise SystemExit(main())
