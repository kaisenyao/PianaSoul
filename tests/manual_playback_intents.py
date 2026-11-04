"""Run by the user: real model, simulated playback, temporary library, no microphone or TTS. Online search gives
made-up results shaped as the sites give them, and a stand-in for the transcriber makes the pieces she chooses.
Each request is said as to her voice; what she says, the tools she calls, when her first words come and when she
is done, and the tokens read and written are kept for each, with a summary for each model.

    tests/manual_playback_intents.py --model bailian --model openai --rounds 3 --save runs.jsonl
    tests/manual_playback_intents.py --summary runs.jsonl
"""
import argparse
from collections import defaultdict
import contextlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from statistics import median
import time
from unittest.mock import Mock, patch

import mido
from openai import APIStatusError

import places  # noqa: F401  (first: the library, data and cache folders are throwaway ones)
from pianasoul import online
from pianasoul.chat import TOOLS, instructions
from pianasoul.downloads import clean_url
from pianasoul.llm import make_chat, model_spec
from pianasoul.voice import finish_interaction
from test_playback_interactions import PlaybackTests

# Each case starts with 贝加尔湖畔 paused at 22 seconds; for 'pause', paused for her to listen, as her voice holds a piece
# that plays: it plays on after her reply unless she pauses it.
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
    # Titled in English, without names in other languages: found by her own search (online too, as she searches the
    # library, in case it is not there), and played from the library, nothing prepared.
    ('弹一下肖邦的夜曲', 'library:Chopin - Nocturne Op. 9 No. 2 (Lang Lang)'),
    ('我想听贝多芬的月光奏鸣曲', 'library:Beethoven - Moonlight Sonata, I. Adagio sostenuto (Valentina Lisitsa)'),
    ('来一首致爱丽丝', 'library:Beethoven - Für Elise (Lang Lang)'),
    ('弹一下卡农吧', 'library:Pachelbel - Canon in D (Kassia)'),
    # Not in the library: online only after the library was searched, on the site chosen; one version prepared, the
    # next good ones handed over with it, and the piece plays once it is made, her words two at most (two_lines).
    # YouTube out of reach: she says why and where else to look, and does not search again. The version she chose
    # cannot be had: the next one plays.
    ('弹一首陈奕迅的十年', 'online:youtube'),
    ('弹一首陈奕迅的十年', 'online:bilibili'),
    ('弹一首陈奕迅的十年', 'unreachable'),
    ('弹一首陈奕迅的十年', 'fallback'),
    # Several pieces in one request: in the order asked, what is in the library first, the others after it once made.
    ('先弹肖邦的夜曲，再弹卡农', 'several:Chopin - Nocturne Op. 9 No. 2 (Lang Lang),Pachelbel - Canon in D (Kassia)'),
    ('我想听致爱丽丝和陈奕迅的十年', 'several:Beethoven - Für Elise (Lang Lang),十年'),
    ('我想听陈奕迅的十年和德彪西的月光', 'several:月光,十年'),
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
ONLINE = {expected for text, expected in CASES if '十年' in text}  # with online search, which has 十年 (VIDEOS)
SAID = {'Chopin - Nocturne Op. 9 No. 2 (Lang Lang)': ('夜曲', 'Nocturne'), 'Pachelbel - Canon in D (Kassia)': ('卡农', 'Canon'),
        'Beethoven - Für Elise (Lang Lang)': ('致爱丽丝', 'Elise'), '月光': ('月光', 'Clair de Lune'),
        '十年': ('十年', 'Ten Years')}  # the names her answer may give a piece of several
# 十年 on each site, as its search lists it: (id, title, channel, seconds, views); some not performances, flagged so.
VIDEOS = {
    'youtube': [('kL9vT2mQx0A', '陈奕迅 Eason Chan - 十年 Ten Years | Piano Cover by Jason Piano', 'Jason Piano', 254, 1830000),
                ('Hc3pWq7Rz1B', '十年 陳奕迅 鋼琴版 Piano Cover', 'Pianella Piano', 236, 412000),
                ('Yt5nB8sKd2C', 'Eason Chan - Ten Years (Easy Piano Tutorial)', 'Piano Tutorial Easy', 180, 210000),
                ('Mz7xR4fGh3D', '陈奕迅《十年》钢琴独奏 Piano Solo', 'MappleZS', 248, 96000),
                ('Qa2cV6jLp4E', '十年 - 陈奕迅 Synthesia', 'MidiPiano', 260, 54000)],
    'bilibili': [('BV1Kx411c7aA', '【钢琴】陈奕迅《十年》钢琴版 完整演奏', '琴童小鹿', 251, 682000),
                 ('BV1Wb4y1z7bB', '十年 钢琴独奏 | 陈奕迅经典', '钢琴师阿杰', 239, 151000),
                 ('BV1Ns4y1q7cC', '十年 钢琴简易教学 零基础也能学', '钢琴入门课', 300, 98000),
                 ('BV1Pd4y1h7dD', '【钢琴】十年（陈奕迅）高还原 附琴谱', '指尖的旋律', 246, 73000),
                 ('BV1Ra411k7eE', '陈奕迅 十年 钢琴伴奏 KTV', '伴奏大全', 245, 41000)],
}


# Her words for a piece found online: two at most, what she will do and her news of it, which is said right before
# its first note, so that neither asks to wait or says when it plays.
WAITING = re.compile(r'稍等|等我|等一下|稍候|准备好|好了就|\bmoment\b|\bhang on\b|\bbear with me\b|\bready\b|\bas soon as\b',
                     re.I)


def two_lines(said):
    return len(said) <= 2 and not any(WAITING.search(words) for words in said)


def by_title(fixture, title):
    """A piece by its title, or one made since by its name."""
    return next(r['id'] for r in fixture.lib.all() if r['title'] == title or title in r['title'].split(' - ')[-1])


@contextlib.contextmanager
def online_as(fixture, case, asked):
    """Search gives VIDEOS for 十年 (by its name in either language) on the site the case names; YouTube out of reach
    for 'unreachable'. Pieces are made by a stand-in for the transcriber, of the video asked for (asked), except the
    first for 'fallback': that one cannot be had."""
    site = 'bilibili' if case == 'online:bilibili' else 'youtube'
    fixture.lib.set_setting('search_sources', site)

    def search(query, n, source):
        if case == 'unreachable':
            raise online.OnlineError('Unable to download API page: timed out', 'unreachable', 'youtube')
        if '十年' not in query and 'ten years' not in query.casefold():
            return []
        url = 'https://www.bilibili.com/video/{}' if source == 'bilibili' else 'https://www.youtube.com/watch?v={}'
        return [{'url': url.format(vid), 'video_title': title, 'channel': channel, 'views': views, 'published': '',
                 'thumbnail': '', 'duration_s': seconds, 'description': '',
                 'flags': online.flags_for(title, channel, '', seconds, query), 'site': source}
                for vid, title, channel, seconds, views in VIDEOS[source][:n]]

    audio = fixture.root / 'audio.m4a'
    audio.write_bytes(b'audio')

    def download(url, folder, progress):
        asked.append(url)
        if case == 'fallback' and len(asked) == 1:
            raise ValueError('ERROR: [youtube] Video unavailable. This video is no longer available')
        return audio, {'video_title': url, 'duration_s': 250}

    class Transcription:  # the transcriber's process (pipeline.Transcription): it makes the piece whole, no parts
        def __init__(self, out, options, *, output_name=None, stopped=None):
            self.midi = Path(out) / f'{output_name}.mid'

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

        def convert(self, audio, part=None):
            midi = mido.MidiFile(); track = mido.MidiTrack(); midi.tracks.append(track)
            track.append(mido.Message('note_on', note=60, velocity=64, time=0))
            track.append(mido.Message('note_off', note=60, time=480 * 240))
            midi.save(self.midi)
            return {'files': {'midi': str(self.midi)}, 'audio_duration_s': 250}
    with patch('pianasoul.online.search', side_effect=search), \
            patch('pianasoul.download.download_audio', side_effect=download), \
            patch('pianasoul.pipeline.Transcription', Transcription), \
            patch('pianasoul.downloads._video_page', return_value={}):
        yield


def settled(fixture, seconds=20):
    """What she sent to be prepared is made, and done with: played, lined up, or said."""
    deadline = time.monotonic() + seconds
    while fixture.a.preparing and time.monotonic() < deadline:
        time.sleep(0.05)
    with fixture.a._turn:
        pass
    if fixture.player._thread: fixture.player._thread.join()


def prepared_well(trace, results, site):
    """One transcribe_url, for one of the site's results, with other results as its alternatives."""
    sent = [a for name, a in trace if name == 'transcribe_url']
    urls = {r['url'] for r in results}
    return len(sent) == 1 and clean_url(sent[0]['url']) in urls and online.site_of(sent[0]['url']) == site \
        and sent[0].get('alternatives') and all(clean_url(v['url']) in urls - {clean_url(sent[0]['url'])}
                                                for v in sent[0]['alternatives'])


def add_english_titles(fixture):
    for title in ENGLISH_TITLES:
        path = fixture.root / 'Music' / (title + '.mid')
        midi = mido.MidiFile(); track = mido.MidiTrack(); midi.tracks.append(track)
        track.append(mido.Message('note_on', note=60, velocity=64, time=0))
        track.append(mido.Message('note_off', note=60, time=480 * 240))
        midi.save(path)
        fixture.lib.add(path, title)


def check(expected, fixture, answer, said, trace, results, asked, playing):
    """Whether she did what the request asked, as the piano, the pending piece and the playlists are now; answer is her
    last reply, said all she said for the request, and playing what played as she answered, before anything was
    made."""
    player, pending = fixture.player, fixture.a.pending
    baikal, moon = (r['id'] for r in fixture.records)
    names = [name for name, _ in trace]
    if expected.startswith('library:'):
        return player.state == 'playing' and player._from == 0 and player.current[1] == expected[8:] \
            and 'transcribe_url' not in names
    if expected.startswith('online:'):
        site = expected[7:]
        words = [a['query'] for name, a in trace if name == 'search_online']
        # A search online before the library's, not in the same response, is refused, and is not in the trace.
        return 'search_online' in names and 'search_library' in names \
            and all(online.PIANO_WORD[site] in online.for_site(w, site) for w in words) \
            and prepared_well(trace, results, site) \
            and '十年' in player.current[1] and player.state == 'playing' and player._from == 0 and two_lines(said)
    if expected == 'unreachable':
        return names.count('search_online') == 1 and 'transcribe_url' not in names \
            and ('哔哩哔哩' in answer or 'Bilibili' in answer) and player.state == 'paused' and player.position == 22
    if expected == 'fallback':
        sent = next((a for name, a in trace if name == 'transcribe_url'), {})
        versions = [clean_url(v['url']) for v in sent.get('alternatives') or []]
        return prepared_well(trace, results, 'youtube') and asked[1:2] == versions[:1] \
            and player.state == 'playing' and fixture.lib.get(player.current_id)['source_url'] == versions[0] \
            and two_lines(said)
    if expected.startswith('several:'):  # all she said names both: the plan may come before a search, the news after
        titles = expected[8:].split(',')
        first, then = (by_title(fixture, t) for t in titles)
        heard = ' '.join(said)
        return playing == (first, 'playing', 0) and (player.current_id, player.ids) == (first, [first, then]) \
            and names.count('transcribe_url') <= 1 and all(any(name in heard for name in SAID[t]) for t in titles)
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
        paused_for_her = expected == 'pause'  # as voice's hold_piece leaves it: not touched since
        if paused_for_her: fixture.a.touched_playback = False
        original, results, asked = fixture.a.run_tool, [], []  # what search_online found; the videos downloaded
        enabled = {'control_playback', 'search_library', 'play_recording', 'library_info', 'play_playlist',
                   'replay_conversation', 'save_conversation_as_playlist', 'rename_playlist',
                   *({'search_online', 'transcribe_url'} if expected in ONLINE else ())}
        def safe_tool(name, arguments):
            trace.append((name, arguments))
            if name not in enabled:
                return json.dumps({'error': 'Only playback and the library are enabled in this test.'})
            raw = original(name, arguments)
            if name == 'search_online':
                results.extend(json.loads(raw).get('results', []))
            return raw
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
        with online_as(fixture, expected, asked) if expected in ONLINE else contextlib.nullcontext():
            answer = fixture.a.ask(chat, text, speak=speak, paused_for_her=paused_for_her)
            kept['done_s'] = round(time.monotonic() - started, 2)
            if paused_for_her:
                finish_interaction(fixture.a, Mock(), answer, True)  # as her voice ends the request
            player = fixture.player
            if player._thread: player._thread.join()
            playing = player.current_id, player.state, player._from  # as she answered, before anything is made
            settled(fixture)
        if answer:
            said.append(answer)
            kept['first_words_s'] = kept['first_words_s'] or kept['done_s']
        if not check(expected, fixture, answer, said, trace, results, asked, playing): kept['result'] = 'FAIL'
    except APIStatusError as exc:
        if exc.status_code < 500:
            raise  # a key, quota, payment or permission error, or a model not offered: it would only come again
        kept['result'] = f'ERROR ({type(exc).__name__}: {str(exc)[:120]})'
    except Exception as exc:
        kept['result'] = f'ERROR ({type(exc).__name__}: {str(exc)[:120]})'
    finally:
        settled(fixture)  # not while the library closes
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
