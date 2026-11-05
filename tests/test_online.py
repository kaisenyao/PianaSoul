"""Online search on YouTube and on Bilibili: what each site is asked, what of its answer is offered, both searched at
once and merged, why a search fails, and one address for each video; no network (yt-dlp, which every request goes
through, is replaced)."""
import io
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, call, patch
from urllib.parse import parse_qsl, urlsplit

import places  # noqa: F401  (first: the library, data and cache folders are throwaway ones)
from yt_dlp.networking import Response
from yt_dlp.networking.exceptions import HTTPError, TransportError
from yt_dlp.utils import DownloadError, ExtractorError

from pianasoul import download, downloads, online
from pianasoul.downloads import clean_url

# Bilibili's answer to a search, cut down to what is read, with made-up videos and names.
PAGE = {'code': 0, 'message': '0', 'data': {'pagesize': 20, 'result': [
    {'type': 'video', 'bvid': 'BV1Aa411A7aA', 'title': '<em class="keyword">晴天</em> 钢琴 &amp; 雨声',
     'author': '钢琴小站', 'upic': 'http://i1.hdslb.com/bfs/face/one.jpg', 'duration': '4:28', 'play': 1089173,
     'pubdate': 1669352519, 'pic': '//i0.hdslb.com/bfs/archive/one.jpg', 'description': '演奏：钢琴小站',
     'typename': '演奏', 'is_pay': 0, 'is_charge_video': 0},
    {'type': 'ketang', 'bvid': '', 'title': '<em class="keyword">晴天</em>钢琴课', 'author': '某课堂', 'duration': '',
     'is_pay': 0, 'is_charge_video': 0},
    {'type': 'video', 'bvid': 'BV1Bb411B7bB', 'title': '晴天 钢琴（付费）', 'author': 'Pianist B', 'duration': '4:30',
     'is_pay': 1, 'is_charge_video': 0},
    {'type': 'video', 'bvid': 'BV1Cc411C7cC', 'title': '晴天 钢琴（充电专属）', 'author': 'Pianist C', 'duration': '4:31',
     'is_pay': 0, 'is_charge_video': 1},
    {'type': 'video', 'bvid': 'BV1Dd411D7dD', 'title': '周杰伦 钢琴 两小时', 'author': 'Pianist D', 'duration': '120:5',
     'is_pay': 0, 'is_charge_video': 0},
    {'type': 'video', 'bvid': 'BV1Ee411E7eE', 'title': '晴天 前奏', 'author': 'Pianist E', 'duration': '39:3',
     'play': 3500, 'pubdate': 1700000000, 'pic': 'https://i2.hdslb.com/bfs/archive/five.jpg', 'typename': '音乐教学',
     'is_pay': 0, 'is_charge_video': 0},
]}}
VOUCHER = {'code': 0, 'message': '0', 'data': {'v_voucher': 'voucher_x'}}  # "nothing", until a human is shown asking


def answered(status: int, url: str = online.BILIBILI_SEARCH) -> Response:
    return Response(io.BytesIO(b''), url, {}, status=status)


class Site:
    """yt-dlp as online search uses it: the options each request is made with, and each request, answered in turn
    (an error is raised) from what is given; extract_info answers YouTube's search."""

    def __init__(self, *answers, entries=()):
        self.answers, self.entries, self.options, self.requests = list(answers), list(entries), [], []

    def __call__(self, options):  # YoutubeDL(options)
        self.options.append(options)
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def urlopen(self, request):
        self.requests.append(request)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, Response) else io.BytesIO(json.dumps(answer).encode())

    def extract_info(self, url, download, process):
        self.requests.append(url)
        return {'entries': iter(self.entries)}


def asked(request) -> dict:
    """A request's query, by name."""
    return dict(parse_qsl(urlsplit(request.url).query))


class SearchTests(unittest.TestCase):
    def test_youtube_is_searched_with_a_short_timeout_and_no_retry(self):
        site = Site(entries=[{'id': 'aaaaaaaaaaa', 'title': '晴天 钢琴', 'channel': 'Pianist', 'duration': 240}])
        with patch('yt_dlp.YoutubeDL', site):
            found = online.search('晴天 钢琴')
        self.assertEqual(site.requests, ['ytsearch8:晴天 钢琴'])
        self.assertEqual((site.options[0]['socket_timeout'], site.options[0]['extractor_retries']), (8, 0),
                         'Out of reach (mainland China), it says so within seconds')
        self.assertEqual((found[0]['url'], found[0]['site']), ('https://www.youtube.com/watch?v=aaaaaaaaaaa', 'youtube'))
        site = Site(entries=[])
        with patch('yt_dlp.YoutubeDL', site):
            online.details('https://www.youtube.com/watch?v=aaaaaaaaaaa')
        self.assertEqual((site.options[0]['socket_timeout'], site.options[0]['extractor_retries']), (20, 1),
                         "A video's page keeps its time")

    def test_bilibili_is_asked_once_as_its_web_page_asks(self):
        site = Site(PAGE)
        with patch('yt_dlp.YoutubeDL', site):
            online.search('晴天 钢琴', 8, 'bilibili')
        [request] = site.requests
        self.assertTrue(request.url.startswith('https://api.bilibili.com/x/web-interface/search/type?'))
        self.assertEqual(asked(request), {'search_type': 'video', 'keyword': '晴天 钢琴', 'page': '1'})
        self.assertEqual(request.headers['Referer'], 'https://www.bilibili.com/')
        self.assertRegex(request.headers['Cookie'], r'^buvid3=[0-9a-f-]{36}infoc$', "A visitor's cookie, as yt-dlp's")
        self.assertEqual(site.options[0]['socket_timeout'], 8)

    def test_a_refusal_is_asked_again_once_a_moment_later(self):
        for refusal in (HTTPError(answered(412)), VOUCHER, {'code': -352, 'message': '风控校验失败'}):
            site = Site(refusal, PAGE)
            with patch('yt_dlp.YoutubeDL', site), patch.object(online.time, 'sleep') as slept:
                found = online.search('晴天', 8, 'bilibili')
            self.assertEqual(len(site.requests), 2)
            self.assertEqual(slept.call_args_list, [call(online.RETRY_S)])
            self.assertTrue(2 <= online.RETRY_S <= 3)
            self.assertEqual(len(found), 2)
        site = Site(VOUCHER, VOUCHER, PAGE)
        with patch('yt_dlp.YoutubeDL', site), patch.object(online.time, 'sleep'), \
                self.assertRaises(online.OnlineError) as failed:
            online.search('晴天', 8, 'bilibili')
        self.assertEqual((failed.exception.reason, failed.exception.source), ('refused', 'bilibili'),
                         'A refusal that looks like nothing found is a refusal')
        self.assertEqual(len(site.requests), 2, 'Asked again once, and no more')
        site = Site({'code': 0, 'message': '0', 'data': {'result': []}})
        with patch('yt_dlp.YoutubeDL', site):
            self.assertEqual(online.search('不存在的曲子', 8, 'bilibili'), [])
        self.assertEqual(len(site.requests), 1, 'Nothing found is an answer')

    def test_bilibili_offers_only_whole_videos_short_enough_titled_as_it_shows_them(self):
        with patch('yt_dlp.YoutubeDL', Site(PAGE)):
            first, second = online.search('晴天', 15, 'bilibili')
        self.assertEqual(first, {
            'url': 'https://www.bilibili.com/video/BV1Aa411A7aA', 'video_title': '晴天 钢琴 & 雨声', 'channel': '钢琴小站',
            'channel_avatar': 'https://i1.hdslb.com/bfs/face/one.jpg', 'views': 1089173, 'published': '2022-11-25',
            'thumbnail': 'https://i0.hdslb.com/bfs/archive/one.jpg', 'duration_s': 268, 'description': '演奏：钢琴小站',
            'flags': [], 'site': 'bilibili'})
        self.assertEqual((second['duration_s'], second['flags']), (2343, ['long', 'tutorial']),
                         'Filed under music teaching, it is a tutorial whatever its title says')
        with patch('yt_dlp.YoutubeDL', Site(PAGE)):
            self.assertEqual(len(online.search('晴天', 1, 'bilibili')), 1)

    def test_bilibili_titles_have_their_own_warning_words(self):
        self.assertEqual(online.flags_for('晴天 钢琴教程 简谱', 'UP', '', 240), ['score', 'tutorial'])
        self.assertEqual(online.flags_for('晴天 钢琴弹唱', 'UP', '', 240), ['sung'])
        self.assertEqual(online.flags_for('晴天 弹唱', 'UP', '', 240, '晴天 弹唱'), [], 'Unless that is what was asked for')

    def test_sheet_music_a_game_s_instrument_a_backing_track_and_a_lesson_say_so_as_bilibili_titles_do(self):
        for title, flags in (('天空之城钢琴谱', ['score']), ('【钢琴】钟-李斯特-乐谱版', ['score']),
                             ('《孤勇者》动态乐谱', ['score']), ('【光遇琴谱】江南[钢琴3指]', ['game', 'score']),
                             ('sky光遇 千与千寻片尾曲', ['game']), ('《稻香》（原神演奏）附谱', ['game']),
                             ('【原琴】告白气球', ['game']), ('『钢琴』十年 纯钢琴伴奏版', ['backing']),
                             ('只用四个和弦教你弹会这首《光年之外》', ['tutorial']), ('四个和弦就可以教会你《后来》', ['tutorial'])):
            self.assertEqual(online.flags_for(title, 'UP', '', 240), flags, title)
        self.assertEqual(online.describe(['backing']), ['accompaniment only (a backing track)'])
        self.assertEqual(online.flags_for('十年 伴奏', 'UP', '', 240, '十年 伴奏'), [], 'Unless that is what was asked for')
        for title, channel in (('【钢琴】起风了(简介附谱)', 'UP'), ('【Piano Cover】稻香｜高還原純鋼琴版｜附譜', 'UP'),
                               ('起风了 无损音质三角钢琴版', 'UP'), ('你的名字 钢琴组曲【Hi-Res无损音质】', 'UP'),
                               ('钟-郎朗-（李斯特）高音质', '新手的问号')):
            self.assertEqual(online.flags_for(title, channel, '', 240), [],
                             'Sheet music offered with a performance, its sound, a channel new to it: no warning')

    def test_a_bilibili_search_asks_for_piano_as_bilibili_says_it(self):
        site = Site(PAGE)
        with patch('yt_dlp.YoutubeDL', site):
            found = online.piano_versions('晴天', 'bilibili')
        self.assertEqual(asked(site.requests[0])['keyword'], '晴天 钢琴')
        self.assertEqual([r['url'] for r in found], ['https://www.bilibili.com/video/BV1Aa411A7aA'])

    def test_bilibili_s_whole_page_is_offered_and_youtube_s_first_fifteen(self):
        page = {'code': 0, 'data': {'result': [{'type': 'video', 'bvid': f'BV1Aa411A7{n:02d}', 'title': f'晴天 钢琴 {n}',
                                                'author': 'UP', 'duration': '4:28'} for n in range(20)]}}
        with patch('yt_dlp.YoutubeDL', Site(page)):
            self.assertEqual(len(online.piano_versions('晴天', 'bilibili')), 20, 'One request, and all of its page')
        with patch('yt_dlp.YoutubeDL', Site(page)):
            self.assertEqual(len(online.search('晴天', 30, 'bilibili')), 20)
        site = Site(entries=[{'id': f'{n:011d}', 'title': f'晴天 piano {n}', 'channel': 'Pianist', 'duration': 240}
                             for n in range(15)])
        with patch('yt_dlp.YoutubeDL', site):
            online.piano_versions('晴天')
            online.search('晴天', 30)
        self.assertEqual(site.requests, ['ytsearch15:晴天 piano', 'ytsearch15:晴天'])

    def test_on_bilibili_only_enough_others_follow_a_few_piano_versions(self):
        titles = ['晴天 钢琴', '晴天【钢琴】'] + [f'晴天 {other}' for other in ('竹笛', '古筝', '口琴', '二胡', '吉他', '琵琶')]
        page = {'code': 0, 'data': {'result': [{'type': 'video', 'bvid': f'BV1Aa411A7{n:02d}', 'title': title,
                                                'author': 'UP', 'duration': '4:28'} for n, title in enumerate(titles)]}}
        with patch('yt_dlp.YoutubeDL', Site(page)):
            found = online.piano_versions('晴天', 'bilibili')
        self.assertEqual([r['video_title'] for r in found], titles[:online.ENOUGH], 'Its first others, up to ENOUGH')
        site = Site(entries=[{'id': f'{n:011d}', 'title': title, 'channel': 'Channel', 'duration': 240}
                             for n, title in enumerate(titles)])
        with patch('yt_dlp.YoutubeDL', site):
            found = online.piano_versions('晴天')
        self.assertEqual([r['video_title'] for r in found], titles, "YouTube's all follow, as a piano piece's do")

    def test_a_piece_from_a_video_found_on_bilibili_has_its_up_s_picture(self):
        with patch('yt_dlp.YoutubeDL', Site(PAGE)):
            online.search('晴天', 8, 'bilibili')
        self.assertEqual(downloads._video_page('https://www.bilibili.com/video/BV1Aa411A7aA'),
                         {'channel_avatar': 'https://i1.hdslb.com/bfs/face/one.jpg'})
        self.assertEqual(downloads._video_page('https://www.bilibili.com/video/BV1Zz411Z7zZ'), {}, 'Not found: none')


CASES = json.loads(Path(__file__).with_name('online_merge_cases.json').read_text())  # the phone's merge has the same


class Sites:
    """Each site's search (online.search), made up: how long it takes (None: out of reach, until released) and how
    many videos it finds, or how it fails; and what each was asked."""

    def __init__(self, **answers):
        self.answers, self.asked, self.released = answers, [], threading.Event()

    def __call__(self, query, max_results, source):
        self.asked.append((query, max_results, source))
        seconds, answer = self.answers[source]
        if seconds is None:
            self.released.wait(5)
        else:
            time.sleep(seconds)
        if isinstance(answer, Exception):
            raise answer
        return [{'url': f'https://{source}/{n}', 'site': source} for n in range(answer)]


class SitesTests(unittest.TestCase):
    def test_several_sites_videos_are_merged_in_turn_each_video_once_with_the_addresses_of_its_copies(self):
        for case in CASES:
            found = online.merged(case['lists'])
            self.assertEqual([v['url'] for v in found], case['merged'], case['case'])
            self.assertEqual([v['urls'] for v in found], case.get('urls', [[url] for url in case['merged']]), case['case'])

    def test_each_site_is_asked_for_piano_in_its_own_word(self):
        for query, site, asked_as in (('周杰伦 晴天 piano', 'bilibili', '周杰伦 晴天 钢琴'),
                                      ('周杰伦 晴天 piano', 'youtube', '周杰伦 晴天 piano'),
                                      ('晴天 鋼琴', 'youtube', '晴天 piano'),
                                      ('Chopin Nocturne Op. 9 No. 2 Piano', 'bilibili', 'Chopin Nocturne Op. 9 No. 2 钢琴')):
            self.assertEqual(online.for_site(query, site), asked_as)
        for query in ('Billy Joel Piano Man', '海上钢琴师', '晴天', ''):
            self.assertEqual(online.for_site(query, 'bilibili'), query, 'Only the word for piano that ends the words')

    def test_the_sites_are_searched_at_once_and_merged(self):
        sites = Sites(youtube=(.3, 3), bilibili=(.3, 2))
        started = time.monotonic()
        with patch.object(online, 'search', sites):
            found, failed = online.search_sites('晴天 piano', 8, ['youtube', 'bilibili'])
        self.assertLess(time.monotonic() - started, .5, 'At once, not one after the other')
        self.assertEqual(sorted(sites.asked), [('晴天 piano', 8, 'youtube'), ('晴天 钢琴', 8, 'bilibili')])
        self.assertEqual([v['url'] for v in found], ['https://youtube/0', 'https://bilibili/0', 'https://youtube/1',
                                                     'https://bilibili/1', 'https://youtube/2'])
        self.assertEqual(failed, [])
        with patch.object(online, 'search', Sites(youtube=(0, 8), bilibili=(0, 8))):
            found, _ = online.search_sites('晴天', 8, ['youtube', 'bilibili'])
        self.assertEqual([v['site'] for v in found], ['youtube', 'bilibili'] * 4, 'As many as asked for, from both')
        sites = Sites(youtube=(0, 3), bilibili=(0, 3))
        with patch.object(online, 'search', sites):
            found, _ = online.search_sites('晴天 piano', 8, ['bilibili'])
        self.assertEqual((sites.asked, [v['site'] for v in found]), ([('晴天 钢琴', 8, 'bilibili')], ['bilibili'] * 3),
                         'Only the sites chosen')

    def test_as_many_videos_as_her_model_asks_for_in_any_form_at_least_one_and_at_most_15(self):
        for asked_for, many in (('8', 8), (8.0, 8), (3, 3), (0, 1), (-1, 1), (20, 15), ('30', 15)):
            sites = Sites(youtube=(0, 20), bilibili=(0, 20))
            with patch.object(online, 'search', sites):
                found, _ = online.search_sites('晴天', asked_for, ['youtube', 'bilibili'])
            self.assertEqual((len(found), {n for _, n, _ in sites.asked}), (many, {many}), repr(asked_for))

    def test_a_site_out_of_reach_does_not_hold_the_videos_of_another(self):
        sites = Sites(youtube=(None, 3), bilibili=(.05, 2))
        self.addCleanup(sites.released.set)
        started = time.monotonic()
        with patch.object(online, 'search', sites), patch.object(online, 'LATE_S', .2):
            found, failed = online.search_sites('晴天', 8, ['youtube', 'bilibili'])
        self.assertLess(time.monotonic() - started, .5, "Bilibili's, a moment after they came")
        self.assertEqual([v['site'] for v in found], ['bilibili', 'bilibili'])
        self.assertEqual([(e.source, e.reason) for e in failed], [('youtube', 'unreachable')])
        self.assertTrue(2 <= online.LATE_S <= 3, 'Longer than a search takes where both sites answer (about 1 s)')

    def test_a_site_that_found_nothing_does_not_cut_another_short(self):
        with patch.object(online, 'search', Sites(youtube=(.4, 2), bilibili=(0, 0))), patch.object(online, 'LATE_S', .1):
            found, failed = online.search_sites('晴天', 8, ['youtube', 'bilibili'])
        self.assertEqual(([v['site'] for v in found], failed), (['youtube', 'youtube'], []))

    def test_one_site_failing_leaves_the_others_videos_and_says_why(self):
        refused = online.OnlineError('HTTP Error 412: Precondition Failed', 'refused', 'bilibili')
        with patch.object(online, 'search', Sites(youtube=(.1, 2), bilibili=(0, refused))):
            found, failed = online.search_sites('晴天', 8, ['youtube', 'bilibili'])
        self.assertEqual([v['site'] for v in found], ['youtube', 'youtube'])
        self.assertEqual([(e.source, e.reason, str(e)) for e in failed],
                         [('bilibili', 'refused', 'HTTP Error 412: Precondition Failed')])
        with patch.object(online, 'search', Sites(youtube=(0, KeyError('entries')), bilibili=(0, 2))):
            found, failed = online.search_sites('晴天', 8, ['youtube', 'bilibili'])
        self.assertEqual(([v['site'] for v in found], [(e.source, e.reason) for e in failed]),
                         (['bilibili', 'bilibili'], [('youtube', 'failed')]), 'Anything else a site brings is its failure')

    def test_every_site_failing_says_why_each_failed_in_their_order(self):
        unreachable = online.OnlineError('timed out', 'unreachable', 'youtube')
        refused = online.OnlineError('Bilibili: 风控校验失败 (-352)', 'refused', 'bilibili')
        with patch.object(online, 'search', Sites(youtube=(.1, unreachable), bilibili=(0, refused))):
            found, failed = online.search_sites('晴天', 8, ['youtube', 'bilibili'])
        self.assertEqual((found, [(e.source, e.reason) for e in failed]),
                         ([], [('youtube', 'unreachable'), ('bilibili', 'refused')]))


class FailureTests(unittest.TestCase):
    def test_a_failure_says_why(self):
        timed_out = TransportError(cause=TimeoutError('timed out'))
        for error, reason in (
                (ExtractorError('Unable to download API page: timed out', cause=timed_out), 'unreachable'),
                (ExtractorError('Unable to download API page', cause=TransportError(cause=socket.gaierror(8))),
                 'unreachable'),
                (DownloadError('ERROR: timed out', exc_info=(None, ExtractorError('x', cause=timed_out), None)),
                 'unreachable'),
                (ExtractorError('HTTP Error 429', cause=HTTPError(answered(429))), 'refused'),
                (HTTPError(answered(412)), 'refused'),
                (ExtractorError('Sign in to confirm you’re not a bot. Use --cookies'), 'refused'),
                (DownloadError('ERROR: [BiliBili] 1GJ411x7h7: You have exceeded the rate limit. Try again later'),
                 'refused'),  # yt-dlp's words for Bilibili's risk-control page instead of the video's
                (HTTPError(answered(404)), 'failed'),
                (ExtractorError('Video unavailable'), 'failed'),
                (ValueError('provide a single video URL, not a playlist or channel'), 'failed')):
            self.assertEqual(online.reason_of(error), reason, error)

    def test_youtube_out_of_reach_says_so(self):
        def pages():
            raise ExtractorError('Unable to download API page: timed out',
                                 cause=TransportError(cause=TimeoutError('timed out')))
            yield
        site = Site()
        site.extract_info = lambda url, download, process: {'entries': pages()}
        with patch('yt_dlp.YoutubeDL', site), self.assertRaises(online.OnlineError) as failed:
            online.search('晴天')
        self.assertEqual((failed.exception.reason, failed.exception.source), ('unreachable', 'youtube'))
        self.assertIn('timed out', str(failed.exception), 'As yt-dlp said it, for the terminal')

    def test_bilibili_out_of_reach_is_not_asked_again(self):
        site = Site(TransportError(cause=TimeoutError('timed out')), PAGE)
        with patch('yt_dlp.YoutubeDL', site), self.assertRaises(online.OnlineError) as failed:
            online.search('晴天', 8, 'bilibili')
        self.assertEqual((failed.exception.reason, failed.exception.source, len(site.requests)),
                         ('unreachable', 'bilibili', 1))


class LinkTests(unittest.TestCase):
    def test_a_bilibili_video_has_one_address(self):
        canonical = 'https://www.bilibili.com/video/BV1We4y1g7sj'
        for link in ('https://www.bilibili.com/video/BV1We4y1g7sj', 'https://www.bilibili.com/video/BV1We4y1g7sj/',
                     'http://bilibili.com/video/BV1We4y1g7sj', 'https://m.bilibili.com/video/BV1We4y1g7sj?p=1',
                     'https://www.bilibili.com/video/bv1We4y1g7sj/?spm_id_from=333.1007&vd_source=abc&t=30',
                     'https://www.bilibili.com/video/av648111975', 'https://m.bilibili.com/video/AV648111975/'):
            self.assertEqual(clean_url(link), canonical, link)
        self.assertEqual(clean_url('https://www.bilibili.com/video/BV1We4y1g7sj/?p=2&share_source=copy_web'),
                         canonical + '?p=2', 'A part after the first is its own')
        self.assertEqual(clean_url(canonical + '?p=2'), canonical + '?p=2')
        self.assertEqual(clean_url('https://www.bilibili.com/video/av170001'), 'https://www.bilibili.com/video/BV17x411w7KC')
        self.assertEqual(clean_url('https://www.bilibili.com/bangumi/play/ep21495?spm_id_from=x'),
                         'https://www.bilibili.com/bangumi/play/ep21495', 'Another page: only what tracks is dropped')
        self.assertEqual(clean_url('https://youtu.be/abcdefghijk?si=x'), 'https://www.youtube.com/watch?v=abcdefghijk')

    def test_a_short_link_is_followed_to_the_video(self):
        site = Site(answered(200, 'https://m.bilibili.com/video/BV1We4y1g7sj?share_source=copy_web'))
        with patch('yt_dlp.YoutubeDL', site):
            found = online.resolved('https://b23.tv/AbCdEfG')
        self.assertEqual(site.requests[0].method, 'HEAD')
        self.assertEqual(clean_url(found), 'https://www.bilibili.com/video/BV1We4y1g7sj')
        page = 'https://www.bilibili.com/video/BV1We4y1g7sj/'
        with patch('yt_dlp.YoutubeDL', Site(HTTPError(answered(412, page)))):
            self.assertEqual(online.resolved('https://b23.tv/AbCdEfG'), page, 'Followed there, the page saying no')
        with patch('yt_dlp.YoutubeDL', Site(TransportError(cause=TimeoutError('timed out')))), \
                self.assertRaises(online.OnlineError) as failed:
            online.resolved('https://b23.tv/AbCdEfG')
        self.assertEqual((failed.exception.reason, failed.exception.source), ('unreachable', 'bilibili'))
        with patch('yt_dlp.YoutubeDL', Site()):
            self.assertEqual(online.resolved(' https://youtu.be/abcdefghijk '), 'https://youtu.be/abcdefghijk')


class DownloadTests(unittest.TestCase):
    def download(self, url, formats, warning=''):
        """yt-dlp's answer for a video of these formats, saying warning (as it does, to its logger) while it reads it."""
        ydl = MagicMock()
        ydl.__enter__.return_value = ydl
        ydl.prepare_filename.return_value = '/nowhere/audio.m4a'
        with tempfile.TemporaryDirectory() as tmp, patch('yt_dlp.YoutubeDL', return_value=ydl) as made, \
                patch.object(download, 'valid_audio', return_value=False), patch('sys.stderr', io.StringIO()) as said:
            def extract_info(url, download):
                if warning:
                    made.call_args.args[0]['logger'].warning(warning)
                return {'_type': 'video', 'duration': 240, 'formats': formats}
            ydl.extract_info.side_effect = extract_info
            try:
                download.download_audio(url, Path(tmp))
            except RuntimeError:  # its audio, made up here, is not real: the download got that far
                pass
        ydl.options, ydl.said = made.call_args.args[0], said.getvalue()  # what yt-dlp was made with, and said
        return ydl

    def test_a_bilibili_preview_is_not_taken_for_the_piece(self):
        legacy = [{'format_id': '16', 'ext': 'mp4', 'vcodec': None, 'acodec': None}]  # one file, its audio in it
        warning = ('[BiliBili] BV1Cc411C7cC: This is a supporter-only video, only the preview will be extracted: '
                   '充电专属. Use --cookies-from-browser or --cookies for the authentication.')  # as yt-dlp says it
        with self.assertRaisesRegex(ValueError, 'only a preview'):
            self.download('https://www.bilibili.com/video/BV1Cc411C7cC', legacy, warning)
        self.download('https://www.bilibili.com/video/BV1nx411u79K', legacy).process_ie_result.assert_called_once()
        whole = [{'format_id': '30280', 'vcodec': 'none', 'acodec': 'mp4a.40.2'}, {'format_id': '100026', 'vcodec': 'hev1'}]
        self.download('https://www.bilibili.com/video/BV1Aa411A7aA', whole).process_ie_result.assert_called_once()

    def test_what_yt_dlp_says_is_on_stderr_as_before(self):
        self.assertEqual(self.download('https://www.youtube.com/watch?v=abcdefghijk', [], 'a warning').said,
                         'WARNING: a warning\n')

    def test_bilibili_s_audio_is_asked_for_a_part_at_a_time(self):
        whole = [{'format_id': '30280', 'vcodec': 'none', 'acodec': 'mp4a.40.2'}]
        self.assertEqual(self.download('https://www.bilibili.com/video/BV1Aa411A7aA', whole).options['http_chunk_size'],
                         10 << 20)
        self.assertNotIn('http_chunk_size', self.download('https://www.youtube.com/watch?v=abcdefghijk', whole).options,
                         "YouTube's formats set their own")

    def test_a_video_s_picture_is_kept_over_https(self):
        self.assertEqual(download.video_details({'thumbnail': 'http://i0.hdslb.com/bfs/archive/one.jpg'})['thumbnail'],
                         'https://i0.hdslb.com/bfs/archive/one.jpg')
        self.assertEqual(download.video_details({'thumbnail': 'https://i.ytimg.com/x.jpg'})['thumbnail'],
                         'https://i.ytimg.com/x.jpg')


if __name__ == '__main__':
    unittest.main()
