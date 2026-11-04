"""Run by the user: real model, simulated playback, temporary library, no microphone or TTS. Online search gives
made-up results shaped as the sites give them, and a stand-in for the transcriber makes the pieces she chooses."""
import argparse
import contextlib
import json
from pathlib import Path
import time
from unittest.mock import patch

import mido

import places  # noqa: F401  (first: the library, data and cache folders are throwaway ones)
from pianasoul import online
from pianasoul.chat import TOOLS, instructions
from pianasoul.downloads import clean_url
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
    # Not in the library: online only after the library was searched, on the site chosen; one version prepared, the
    # next good ones handed over with it, and the piece plays once it is made. YouTube out of reach: she says why and
    # where else to look, and does not search again. The version she chose cannot be had: the next one plays.
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


def by_title(fixture, title):
    """A piece by its title, or one made since by its name."""
    return next(r['id'] for r in fixture.lib.all() if r['title'] == title or title in r['title'].split(' - ')[-1])


@contextlib.contextmanager
def online_as(fixture, case, asked):
    """Search gives VIDEOS for 十年 (by its name in either language) on the site the case names; YouTube out of reach
    for 'unreachable'. Pieces are made by a stand-in for the transcriber, of the video asked for (asked), except the
    first for 'fallback': that one cannot be had."""
    site = 'bilibili' if case == 'online:bilibili' else 'youtube'
    fixture.lib.set_setting('search_source', site)

    def search(query, n, source):
        if case == 'unreachable':
            raise online.OnlineError('Unable to download API page: timed out', 'unreachable', 'youtube')
        if '十年' not in query and 'ten years' not in query.casefold():
            return []
        url = 'https://www.bilibili.com/video/{}' if source == 'bilibili' else 'https://www.youtube.com/watch?v={}'
        return [{'url': url.format(vid), 'video_title': title, 'channel': channel, 'views': views, 'published': '',
                 'thumbnail': '', 'duration_s': seconds, 'description': '',
                 'flags': online.flags_for(title, channel, '', seconds, query)}
                for vid, title, channel, seconds, views in VIDEOS[source][:n]]

    audio = fixture.root / 'audio.m4a'
    audio.write_bytes(b'audio')

    def download(url, folder, progress):
        asked.append(url)
        if case == 'fallback' and len(asked) == 1:
            raise ValueError('ERROR: [youtube] Video unavailable. This video is no longer available')
        return audio, {'video_title': url, 'duration_s': 250}

    def transcribe(audio, out, options, output_name, stopped):
        midi = mido.MidiFile(); track = mido.MidiTrack(); midi.tracks.append(track)
        track.append(mido.Message('note_on', note=60, velocity=64, time=0))
        track.append(mido.Message('note_off', note=60, time=480 * 240))
        midi.save(Path(out) / f'{output_name}.mid')
        return {'files': {'midi': str(Path(out) / f'{output_name}.mid')}, 'audio_duration_s': 250}
    with patch('pianasoul.online.search', side_effect=search), \
            patch('pianasoul.download.download_audio', side_effect=download), \
            patch('pianasoul.pipeline.convert_apart', side_effect=transcribe), \
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
            original, results = fixture.a.run_tool, []
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
            asked = []  # the videos downloaded, in order
            chat, label = make_chat(instructions(), TOOLS)
            baikal, moon = (r['id'] for r in fixture.records)
            if expected in CHAT_CASES:
                for asked, said, rec in (('弹一下李健的贝加尔湖畔', '好啦，这就给你弹《贝加尔湖畔》。', baikal),
                                         ('再来一首德彪西的月光', '来啦，《月光》送给你～', moon)):  # as a conversation keeps them
                    fixture.a.keep('user', asked)
                    fixture.a.keep('assistant', said)
                    fixture.a.keep('piece', fixture.lib.get(rec)['title'], rec)
                chat.load(fixture.a.conversations.history(fixture.a.conversation_id))
                fixture.lib.new_playlist('睡前', [moon])
            with online_as(fixture, expected, asked) if expected in ONLINE else contextlib.nullcontext():
                answer = fixture.a.ask(chat, text)
                player = fixture.player
                if player._thread: player._thread.join()
                playing = player.current_id, player.state, player._from  # as she answered, before anything is made
                settled(fixture)
            pending, names = fixture.a.pending, [name for name, _ in trace]
            if expected.startswith('library:'):
                okay = player.state == 'playing' and player._from == 0 and player.current[1] == expected[8:] \
                    and not any(name == 'search_online' for name, _ in trace)
            elif expected.startswith('online:'):
                site = expected[7:]
                words = [a['query'] for name, a in trace if name == 'search_online']
                okay = 'search_online' in names and 'search_library' in names[:names.index('search_online')] \
                    and all(online.PIANO_WORD[site] in w for w in words) and prepared_well(trace, results, site) \
                    and '十年' in player.current[1] and player.state == 'playing' and player._from == 0
            elif expected == 'unreachable':
                okay = names.count('search_online') == 1 and 'transcribe_url' not in names \
                    and ('哔哩哔哩' in answer or 'Bilibili' in answer) and player.state == 'paused' and player.position == 22
            elif expected == 'fallback':
                sent = next((a for name, a in trace if name == 'transcribe_url'), {})
                versions = [clean_url(v['url']) for v in sent.get('alternatives') or []]
                okay = prepared_well(trace, results, 'youtube') and asked[1:2] == versions[:1] \
                    and player.state == 'playing' and fixture.lib.get(player.current_id)['source_url'] == versions[0]
            elif expected.startswith('several:'):  # one sentence of hers says the plan: it names both
                titles = expected[8:].split(',')
                first, then = (by_title(fixture, t) for t in titles)
                okay = playing == (first, 'playing', 0) and (player.current_id, player.ids) == (first, [first, then]) \
                    and names.count('transcribe_url') <= 1 \
                    and all(any(name in answer for name in SAID[t]) for t in titles)
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
            elif expected == 'replay_chat':
                okay = 'replay_conversation' in [name for name, _ in trace] and player.state == 'playing' \
                    and player._from == 0 and player.current[1] == '贝加尔湖畔' and player.ids == [baikal, moon]
            elif expected == 'chat_playlist':
                made = [pl for pl in fixture.lib.playlists() if pl['name'] != '睡前']
                okay = len(made) == 1 and made[0]['ids'] == [baikal, moon] and made[0]['name'] in answer \
                    and ('2' in answer or '两' in answer) and player.state == 'paused' and player.position == 22
            elif expected == 'rename':
                okay = [pl['name'] for pl in fixture.lib.playlists()] == ['晚安曲'] and player.state == 'paused' \
                    and player.position == 22
            elif expected == 'cancel':
                okay = player.state == 'stopped' and pending is None
            else:
                okay = player.state == 'paused' and player.position == 22
                if expected == 'no_action':
                    okay = okay and not any(name == 'play_recording' or name == 'control_playback' and a.get('action') in {'resume','restart','retry'} for name,a in trace)
            if not okay: result = 'FAIL'
        except Exception as exc:
            result = f'ERROR ({type(exc).__name__}: {exc})'
        finally:
            settled(fixture)  # not while the library closes
            fixture.doCleanups()
        if result != 'PASS': failures += 1
        print(f'{index:02d} {result}: {text}')
        if result != 'PASS': print('  tool calls:', json.dumps(trace, ensure_ascii=False))
    print(f'Result: {len(CASES)-failures}/{len(CASES)} passed')
    return int(failures != 0)


if __name__ == '__main__':
    raise SystemExit(main())
