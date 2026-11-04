"""Run by the user: real model, simulated playback, temporary library, no microphone or TTS. Each request is said as
to her voice; what she says, the tools she calls, when her first words come and when she is done, and the tokens
read and written are kept for each, with a summary for each model.

    tests/manual_playback_intents.py --model qwen:qwen3.8-flash --model openai --rounds 3 --save runs.jsonl
    tests/manual_playback_intents.py --summary runs.jsonl
"""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
import os
from statistics import median
import time

import mido
from openai import APIStatusError

import places  # noqa: F401  (first: the library, data and cache folders are throwaway ones)
from pianasoul.chat import TOOLS, instructions
from pianasoul.llm import make_chat, model_spec
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
    # This conversation's pieces (she played 贝加尔湖畔, then 月光): again from the first, or kept as a playlist,
    # which leaves what plays as it is; and one of the playlists renamed.
    ('把这段聊天里的歌从头放一遍', 'replay_chat'),
    ('Play the songs from this chat again, from the first one.', 'replay_chat'),
    ('把刚才聊天里弹过的曲子整理成一个歌单吧', 'chat_playlist'),
    ('把歌单「睡前」改名叫「晚安曲」', 'rename'),
]
CHAT_CASES = {'replay_chat', 'chat_playlist', 'rename'}  # in a conversation where she played both pieces
ENGLISH_TITLES = [expected.split(':', 1)[1] for _, expected in CASES if expected.startswith('library:')]


def add_english_titles(fixture):
    for title in ENGLISH_TITLES:
        path = fixture.root / 'Music' / (title + '.mid')
        midi = mido.MidiFile(); track = mido.MidiTrack(); midi.tracks.append(track)
        track.append(mido.Message('note_on', note=60, velocity=64, time=0))
        track.append(mido.Message('note_off', note=60, time=480 * 240))
        midi.save(path)
        fixture.lib.add(path, title)


def check(expected, fixture, answer, trace):
    """Whether she did what the request asked, as the piano, the pending piece and the playlists are now."""
    player, pending = fixture.player, fixture.a.pending
    baikal, moon = (r['id'] for r in fixture.records)
    if expected.startswith('library:'):
        return player.state == 'playing' and player._from == 0 and player.current[1] == expected[8:] \
            and not any(name == 'search_online' for name, _ in trace)
    if expected == 'online':
        names = [name for name, _ in trace]
        return 'search_online' in names and 'search_library' in names[:names.index('search_online')] \
            and player.current[1] == '贝加尔湖畔'
    if expected in {'resume', 'pending', 'restart', 'new_song', 'replay'}:
        offset = 0 if expected in {'restart', 'new_song'} else 22
        title = '月光' if expected == 'new_song' else '贝加尔湖畔'
        offsets = {0, 22} if expected == 'replay' else {offset}
        return player.state == 'playing' and player._from in offsets and player.current[1] == title
    if expected.startswith('offline_'):
        offset = 0 if expected == 'offline_restart' else 22
        return pending is not None and pending.from_s == offset and '设备和钢琴' in answer
    if expected in {'info', 'chat'}:
        looked = any(name == 'library_info' for name, _ in trace)
        played = any(name in {'play_recording', 'control_playback'} for name, _ in trace)
        return player.state == 'paused' and player.position == 22 and not played and (looked or expected == 'chat')
    if expected == 'replay_chat':
        return 'replay_conversation' in [name for name, _ in trace] and player.state == 'playing' \
            and player._from == 0 and player.current[1] == '贝加尔湖畔' and player.ids == [baikal, moon]
    if expected == 'chat_playlist':
        made = [pl for pl in fixture.lib.playlists() if pl['name'] != '睡前']
        return len(made) == 1 and made[0]['ids'] == [baikal, moon] and made[0]['name'] in answer \
            and ('2' in answer or '两' in answer) and player.state == 'paused' and player.position == 22
    if expected == 'rename':
        return [pl['name'] for pl in fixture.lib.playlists()] == ['晚安曲'] and player.state == 'paused' \
            and player.position == 22
    if expected == 'cancel':
        return player.state == 'stopped' and pending is None
    okay = player.state == 'paused' and player.position == 22
    if expected == 'no_action':
        okay = okay and not any(name == 'play_recording' or name == 'control_playback'
                                and a.get('action') in {'resume', 'restart', 'retry'} for name, a in trace)
    return okay


def counted(chat, kept):
    """The chat model, each reply's tokens added to kept, and a reply held back for its words kept there too."""
    last = {}
    for name in ('send_user', 'send_tool_results', 'ask_again'):
        def send(*args, _send=getattr(chat, name), _name=name):
            if _name == 'ask_again':
                kept['held_back'] = last['text']
            reply = _send(*args)
            last['text'] = reply.text
            kept['calls'] += 1
            kept['tokens'] = [a + b for a, b in zip(kept['tokens'], reply.tokens)]
            return reply
        setattr(chat, name, send)


def run(index, text, expected):
    """One request to the model PIANASOUL_MODEL names, in a fixture of its own: what was kept of it."""
    fixture = PlaybackTests()
    trace, said = [], []
    kept = {'model': '', 'at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'case': index,
            'request': text, 'expected': expected, 'result': 'PASS', 'said': said, 'tools': trace,
            'first_words_s': None, 'done_s': None, 'calls': 0, 'tokens': [0, 0, 0], 'held_back': None}
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
            if name not in {'control_playback', 'search_library', 'play_recording', 'library_info', 'play_playlist',
                            'replay_conversation', 'save_conversation_as_playlist', 'rename_playlist'}:
                return json.dumps({'error': 'Only playback and the library are enabled in this test.'})
            return original(name, arguments)
        fixture.a.run_tool = safe_tool
        chat, kept['model'] = make_chat(instructions(), TOOLS)
        counted(chat, kept)
        baikal, moon = (r['id'] for r in fixture.records)
        if expected in CHAT_CASES:
            for asked, line, rec in (('弹一下李健的贝加尔湖畔', '好啦，这就给你弹《贝加尔湖畔》。', baikal),
                                     ('再来一首德彪西的月光', '来啦，《月光》送给你～', moon)):  # as a conversation keeps them
                fixture.a.keep('user', asked)
                fixture.a.keep('assistant', line)
                fixture.a.keep('piece', fixture.lib.get(rec)['title'], rec)
            chat.load(fixture.a.conversations.history(fixture.a.conversation_id))
            fixture.lib.new_playlist('睡前', [moon])
        started = time.monotonic()
        def speak(words, meanwhile=False):  # as her voice says them
            if words:
                said.append(words)
                kept['first_words_s'] = kept['first_words_s'] or round(time.monotonic() - started, 2)
        answer = fixture.a.ask(chat, text, speak=speak)
        kept['done_s'] = round(time.monotonic() - started, 2)
        if answer:
            said.append(answer)
            kept['first_words_s'] = kept['first_words_s'] or kept['done_s']
        if fixture.player._thread: fixture.player._thread.join()
        if not check(expected, fixture, answer, trace): kept['result'] = 'FAIL'
    except APIStatusError as exc:
        if exc.status_code < 500:
            raise  # a key, quota, payment or permission error, or a model not offered: it would only come again
        kept['result'] = f'ERROR ({type(exc).__name__}: {str(exc)[:120]})'
    except Exception as exc:
        kept['result'] = f'ERROR ({type(exc).__name__}: {str(exc)[:120]})'
    finally:
        fixture.doCleanups()
    return kept


def summary(runs):
    """Each model's pass rate, its median and slowest times, and its tokens a request."""
    by_model = defaultdict(list)
    for kept in runs:
        by_model[kept['model']].append(kept)
    for model, kept in by_model.items():
        passed = sum(k['result'] == 'PASS' for k in kept)
        first = [k['first_words_s'] for k in kept if k['first_words_s'] is not None]
        done = [k['done_s'] for k in kept if k['done_s'] is not None]
        read, cached, written = (sum(k['tokens'][i] for k in kept) / len(kept) for i in range(3))
        held = sum(k['held_back'] is not None for k in kept)
        print(f'{model}: {passed}/{len(kept)} passed ({passed / len(kept):.0%})'
              + (f'; first words {median(first):.2f} s, slowest {max(first):.2f} s' if first else '')
              + (f'; done {median(done):.2f} s, slowest {max(done):.2f} s' if done else '')
              + f'; a request {read:,.0f} tokens read ({cached:,.0f} cached), {written:,.0f} written'
              + f'; held back {held}')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--list', action='store_true', help='list cases without calling the model')
    parser.add_argument('--model', action='append', metavar='PROVIDER[:MODEL]',
                        help='the model to ask, as PIANASOUL_MODEL names it (again for each to compare); '
                             'default: PIANASOUL_MODEL as set')
    parser.add_argument('--rounds', type=int, default=1, help='how many times each case is asked (default 1)')
    parser.add_argument('--cases', type=int, nargs='+', metavar='N', help='only these cases, by number')
    parser.add_argument('--save', metavar='FILE', help='add what was kept of each request to FILE (JSON lines)')
    parser.add_argument('--summary', metavar='FILE', nargs='+', help='summarize saved runs, without calling a model')
    args = parser.parse_args()
    if args.list:
        for index, (text, expected) in enumerate(CASES, 1): print(index, expected, text)
        return 0
    if args.summary:
        summary([json.loads(line) for name in args.summary for line in open(name, encoding='utf-8') if line.strip()])
        return 0
    specs = args.model or [':'.join(model_spec())]
    cases = [(index, *CASES[index - 1]) for index in args.cases or range(1, len(CASES) + 1)]
    print('真实模型测试：只模拟播放和连接，不操作实际设备、曲库或语音输出。')
    runs = []
    try:
        for spec in specs:
            os.environ['PIANASOUL_MODEL'] = spec
            make_chat('', [])  # its key is there, and its name right
            for round_ in range(1, args.rounds + 1):
                for index, text, expected in cases:
                    kept = {**run(index, text, expected), 'round': round_}
                    runs.append(kept)
                    if args.save:
                        with open(args.save, 'a', encoding='utf-8') as f:
                            f.write(json.dumps(kept, ensure_ascii=False) + '\n')
                    times = f"{kept['first_words_s'] or '-'} / {kept['done_s'] or '-'} s"
                    print(f"{index:02d} {kept['result']}: {text}  ({kept['model']}, {times}, {kept['calls']} calls, "
                          f"{kept['tokens'][0]:,} → {kept['tokens'][2]:,} tokens)")
                    if kept['held_back'] is not None:
                        print('  held back:', json.dumps(kept['held_back'], ensure_ascii=False))
                    if kept['result'] != 'PASS':
                        print('  she said:', json.dumps(kept['said'], ensure_ascii=False))
                        print('  tool calls:', json.dumps(kept['tools'], ensure_ascii=False))
    except (APIStatusError, ValueError) as exc:
        print(f'Stopped: {type(exc).__name__}: {str(exc)[:300]}')
    if runs:
        summary(runs)
    return int(not runs or any(kept['result'] != 'PASS' for kept in runs))


if __name__ == '__main__':
    raise SystemExit(main())
