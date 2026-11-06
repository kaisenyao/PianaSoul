// Searching online from the phone (web/online.js): every site chosen asked at once, each one's videos shown as they
// come and merged as the Mac merges hers, the latest search shown, a site that fails said as why it failed, never as
// nothing found, and what each video found is in the library, which its title acts on; and the words for it
// (web/strings.js), in both languages.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('src/pianasoul/web/online.js', 'utf8').replace(/^export /gm, '');
const CASES = JSON.parse(fs.readFileSync('tests/online_merge_cases.json', 'utf8'));  // the Mac's merge has the same
const tick = async () => { for (let i = 0; i < 10; i++) await Promise.resolve(); };
const plain = value => JSON.parse(JSON.stringify(value));  // made in the page's own context: compared as plain values

// The Mac, answering each request when told: with what it found, or with an error as api() in app.js throws it.
function harness() {
  const h = {asked: [], shown: 0};
  const get = path => new Promise((resolve, reject) => {
    const url = new URL(path, 'https://mac.local');
    h.asked.push({q: url.searchParams.get('q'), source: url.searchParams.get('source'), resolve, reject});
  });
  const sandbox = {encodeURIComponent};
  vm.runInNewContext(source + '\nthis.onlineSearch = onlineSearch; this.merged = merged;', sandbox);
  h.merged = sandbox.merged;
  h.search = sandbox.onlineSearch(get, () => h.shown++);
  h.state = () => plain(h.search.state);
  const request = site => h.asked.findLast(a => a.source === site);
  h.answer = async (site, found) => { request(site).resolve(found); await tick(); };
  h.fail = async (site, json) => { request(site).reject(Object.assign(Error(json.error), {json})); await tick(); };
  return h;
}
const videos = (site, n) => Array.from({length: n}, (_, i) => ({url: `https://${site}/${i}`, site}));
const urls = state => state.found.map(v => v.url);

function severalSitesVideosAreMergedInTurnEachVideoOnceWithTheAddressesOfItsCopies() {
  const {merged} = harness();
  for (const c of CASES) {
    const found = plain(merged(c.lists));
    assert.deepEqual(found.map(v => v.url), c.merged, c.case);
    assert.deepEqual(found.map(v => v.urls), c.urls || c.merged.map(url => [url]), c.case);
  }
}

async function everySiteChosenIsAskedAtOnceAndEachShowsAsItComes() {
  const h = harness();
  h.search.find('晴天', ['youtube', 'bilibili']);
  assert.deepEqual(h.asked.map(a => [a.q, a.source]), [['晴天', 'youtube'], ['晴天', 'bilibili']], 'Both at once');
  assert.deepEqual(h.state(), {query: '晴天', found: [], failed: [], answered: [], waiting: ['youtube', 'bilibili'],
    offer: []}, 'The spinner, not "nothing found"');
  await h.answer('bilibili', videos('bilibili', 2));
  assert.deepEqual([urls(h.state()), h.state().waiting], [['https://bilibili/0', 'https://bilibili/1'], ['youtube']],
                   "Bilibili's at once: a YouTube out of reach does not hold them");
  await h.answer('youtube', videos('youtube', 3));
  assert.deepEqual(urls(h.state()), ['https://youtube/0', 'https://bilibili/0', 'https://youtube/1',
                                     'https://bilibili/1', 'https://youtube/2'], 'Merged as the Mac merges hers');
  assert.deepEqual([h.state().answered, h.state().waiting, h.state().failed], [['youtube', 'bilibili'], [], []]);
  h.search.find('不存在的曲子', ['youtube', 'bilibili']);
  await h.answer('youtube', []);
  assert.deepEqual([h.state().found, h.state().waiting], [[], ['bilibili']], 'Nothing yet: still the spinner');
  await h.answer('bilibili', []);
  assert.deepEqual([h.state().found, h.state().answered, h.state().failed], [[], ['youtube', 'bilibili'], []],
                   'Nothing found is nothing found');
}

async function aSiteTurnedOnIsAskedAndOneTurnedOffLetGoOf() {
  const h = harness();
  h.search.find('晴天', ['youtube']);
  await h.answer('youtube', videos('youtube', 2));
  h.search.follow(['youtube', 'bilibili']);  // turned on, here or on another phone
  assert.deepEqual(h.asked.map(a => a.source), ['youtube', 'bilibili'], 'Only the site turned on: YouTube kept');
  assert.deepEqual(h.state().waiting, ['bilibili']);
  h.search.follow(['bilibili']);  // YouTube turned off: its videos gone at once, nothing asked again
  assert.deepEqual([h.state().found, h.asked.length], [[], 2]);
  h.search.follow(['youtube', 'bilibili']);
  h.search.follow(['bilibili']);
  await h.answer('youtube', videos('youtube', 1));  // the answer of a site turned off since
  assert.deepEqual(urls(h.state()), [], 'Turned off: it shows nothing when it comes back');
  await h.answer('bilibili', videos('bilibili', 1));
  assert.deepEqual(urls(h.state()), ['https://bilibili/0']);
}

async function onlyTheLatestSearchShows() {
  const h = harness();
  h.search.find('晴天', ['youtube', 'bilibili']);
  const first = h.asked.slice();
  h.search.find('稻香', ['youtube', 'bilibili']);
  await h.answer('bilibili', videos('bilibili', 1));
  first[0].resolve(videos('youtube', 3));  // 晴天's, late
  await tick();
  assert.deepEqual([h.state().query, urls(h.state()), h.state().waiting], ['稻香', ['https://bilibili/0'], ['youtube']]);
  h.search.forget();
  await h.answer('youtube', videos('youtube', 1));
  assert.deepEqual([h.state().query, h.state().found], ['', []], 'Let go of: it shows nothing when it comes back');
  const asked = h.asked.length;
  h.search.follow(['youtube']);
  assert.equal(h.asked.length, asked, 'No search shown: none made');
}

async function aSiteThatFailsSaysWhyAboveTheOthersVideos() {
  const h = harness();
  h.search.find('晴天', ['youtube', 'bilibili']);
  await h.answer('bilibili', videos('bilibili', 2));
  await h.fail('youtube', {error: 'unreachable', source: 'youtube'});
  assert.deepEqual([h.state().failed, h.state().answered, urls(h.state()).length, h.state().offer],
                   [[{reason: 'unreachable', source: 'youtube'}], ['bilibili'], 2, []],
                   "Bilibili's videos, why YouTube's are not there, and nothing to turn on");
  h.search.find('晴天', ['youtube', 'bilibili']);
  await h.fail('bilibili', {error: 'refused', source: 'bilibili'});
  assert.deepEqual([h.state().found, h.state().waiting], [[], ['youtube']], 'Still the spinner while YouTube searches');
  await h.answer('youtube', []);
  assert.deepEqual([h.state().failed, h.state().answered], [[{reason: 'refused', source: 'bilibili'}], ['youtube']],
                   'Nothing found on one, the other refused: both said');
}

async function noSiteSearchedSaysWhyAndOffersOneTurnedOff() {
  const h = harness();
  h.search.find('晴天', ['youtube']);
  await h.fail('youtube', {error: 'unreachable', source: 'youtube'});
  assert.deepEqual([h.state().failed, h.state().answered, h.state().offer],
                   [[{reason: 'unreachable', source: 'youtube'}], [], ['bilibili']], 'Out of reach: the other, to turn on');
  h.search.follow(['youtube', 'bilibili']);  // turned on from the offer (or on another phone)
  assert.deepEqual([h.asked.at(-1).source, h.state().offer, h.state().waiting], ['bilibili', [], ['bilibili']],
                   'Asked; YouTube not asked again, its failure kept');
  await h.answer('bilibili', videos('bilibili', 1));
  assert.deepEqual([urls(h.state()), h.state().failed.map(f => f.source)], [['https://bilibili/0'], ['youtube']]);
  h.search.find('晴天', ['youtube', 'bilibili']);
  await h.fail('youtube', {error: 'unreachable', source: 'youtube'});
  await h.fail('bilibili', {error: 'refused', source: 'bilibili'});
  assert.deepEqual([h.state().failed.map(f => f.reason), h.state().offer], [['unreachable', 'refused'], []],
                   'Both on: why each, and nothing to turn on');
  h.search.find('晴天', ['bilibili']);
  await h.fail('bilibili', {error: 'refused', source: 'bilibili'});
  assert.deepEqual(h.state().offer, [], 'Refused: the same site again, later and without a proxy');
  h.search.find('晴天', ['youtube']);
  h.asked.at(-1).reject(TypeError('Load failed'));  // the Mac not answering
  await tick();
  assert.deepEqual([h.state().failed, h.state().offer], [[{reason: 'failed', source: 'youtube'}], []]);
  h.search.find('晴天', ['youtube']);
  await h.fail('youtube', {error: 'transcribe_off'});
  assert.deepEqual([h.state().failed, h.state().found], [[{reason: 'failed', source: 'youtube'}], []]);
}

async function aLinkIsReadOnceOnItsOwnSite() {
  const h = harness();
  h.search.find('【晴天 钢琴版-哔哩哔哩】 https://b23.tv/AbCdEfG', ['youtube', 'bilibili']);  // as Bilibili's app copies it
  assert.deepEqual(h.asked.map(a => [a.q, a.source]), [['https://b23.tv/AbCdEfG', null]], 'Once, on its own site');
  assert.equal(h.state().query, 'https://b23.tv/AbCdEfG');
  h.search.follow(['youtube']);
  assert.equal(h.asked.length, 1, 'Whatever sites are chosen');
  h.asked[0].reject(Object.assign(Error('unreachable'), {json: {error: 'unreachable', source: 'bilibili'}}));
  await tick();
  assert.deepEqual([h.state().failed, h.state().offer], [[{reason: 'unreachable', source: 'bilibili'}], []],
                   "A link's video is on its own site only: no other offered");
  h.search.find('https://www.youtube.com/watch?v=abcdefghijk', ['bilibili']);
  h.asked.at(-1).resolve([{url: 'https://www.youtube.com/watch?v=abcdefghijk', site: 'youtube'}]);
  await tick();
  assert.deepEqual([urls(h.state()), h.state().failed], [['https://www.youtube.com/watch?v=abcdefghijk'], []]);
  h.search.find('晴天 钢琴', ['youtube']);
  assert.equal(h.asked.at(-1).q, '晴天 钢琴', 'Words without a link, as they are');
}

// A row's title acts on its video's piece, found by any copy of the video: not a piece yet, it downloads it, as the
// download button does; being prepared, it plays it once it can (preparing.js), the ring stopping it; made, it plays
// it, as ▶ does. The cover opens the video on its site whatever the piece is (app.js renderCands).
function aVideoFoundIsAPieceMadeOneBeingPreparedOrNeitherByAnyCopyOfIt() {
  const {pieceOf} = vm.runInNewContext(source + '\n({pieceOf})', {});
  const video = {url: 'https://youtube/1', urls: ['https://youtube/1', 'https://bilibili/1']};
  const other = {recordings: [{id: 2, source_url: 'https://youtube/2'}], jobs: [{id: 3, source: 'https://bilibili/2'}]};
  assert.deepEqual(plain(pieceOf(video, other.recordings, other.jobs)), {}, 'Not a piece yet: download it');
  const job = {id: 4, source: 'https://bilibili/1', status: 'downloading', recording: null};
  assert.deepEqual(plain(pieceOf(video, other.recordings, [...other.jobs, job])), {job},
                   'Being prepared, from its copy on the other site: play it once it can');
  const made = {id: 5, source_url: 'https://youtube/1'};
  assert.deepEqual(plain(pieceOf(video, [...other.recordings, made], [job])), {made},
                   'Made, though its job has not gone yet: play it');
  const fromCopy = {id: 6, source_url: 'https://bilibili/1'};
  assert.deepEqual(plain(pieceOf(video, [fromCopy], [])), {made: fromCopy}, 'Made from its copy');
}

function theWordsForItInBothLanguages() {
  const sandbox = {};
  const strings = fs.readFileSync('src/pianasoul/web/strings.js', 'utf8').replace(/^export const LANGUAGES/m, 'this.LANGUAGES');
  vm.runInNewContext(strings, sandbox);
  const {zh, en} = sandbox.LANGUAGES;
  assert.equal(zh.refused.bilibili, '哔哩哔哩暂时拒绝了搜索，请关闭代理后再试。');
  assert.equal(zh.unreachable(zh.sites.youtube), '无法连接到YouTube。');
  assert.equal(zh.searchSite(zh.sites.bilibili), '搜索哔哩哔哩');
  assert.equal(zh.partly([zh.cause.unreachable(zh.sites.youtube)], [zh.sites.bilibili]), '无法连接到YouTube，仅显示哔哩哔哩的结果。');
  assert.equal(zh.partly([zh.cause.refused(zh.sites.bilibili)], [zh.sites.youtube]), '哔哩哔哩暂时拒绝了搜索，仅显示YouTube的结果。');
  assert.equal(zh.watchVideo, '观看视频', "A video's cover, to a screen reader");
  assert.equal(zh.actionOn(zh.download, '晴天 钢琴版'), '下载“晴天 钢琴版”', 'Its title: what it does, to which video');
  assert.equal(zh.actionOn(zh.playNow, '晴天 钢琴版'), '播放“晴天 钢琴版”');
  assert.equal(en.unreachable(en.sites.youtube), 'Can’t connect to YouTube.');
  assert.equal(en.searchSite(en.sites.bilibili), 'Search Bilibili');
  assert.equal(en.watchVideo, 'Watch Video');
  assert.equal(en.actionOn(en.download, 'Clair de Lune'), 'Download “Clair de Lune”');
  assert.equal(en.actionOn(en.playNow, 'Clair de Lune'), 'Play “Clair de Lune”');
  assert.equal(en.partly([en.cause.unreachable(en.sites.youtube)], [en.sites.bilibili]),
               'Can’t connect to YouTube. Showing results from Bilibili.');
  assert.equal(en.partly([en.cause.failed('YouTube'), en.cause.refused('Vimeo')], ['Bilibili', 'Dailymotion']),
               'Couldn’t search YouTube. Vimeo isn’t allowing searches right now. Showing results from Bilibili and ' +
               'Dailymotion.', 'Another site later, in the same words');
  const sites = vm.runInNewContext(source + '\nSOURCES', {});
  for (const T of [zh, en]) {
    assert.deepEqual(Object.keys(T.sites), [...sites], 'A name for each site online search knows');
    assert.deepEqual(Object.keys(T.refused), [...sites]);
    assert.deepEqual(Object.keys(T.cause), ['unreachable', 'refused', 'failed']);
    assert.notEqual(T.searchFailed, T.nothing, 'A failure is not "No Results"');
  }
  const english = [...Object.values(en.sites), ...Object.values(en.refused), en.searchFailed, en.sourcesLabel,
    en.partly(Object.values(en.cause).map(c => c(en.sites.youtube)), [en.sites.bilibili]), en.watchVideo,
    en.actionOn(en.download, 'Clair de Lune')];
  assert.ok(english.every(s => !/\p{Script=Han}/u.test(s)), 'The English words are all English');
}

async function aSearchKeptShowsAgainAsItWasAskingOnlyWhatWasNotAnswered() {
  const h = harness();
  h.search.find('晴天', ['youtube', 'bilibili']);
  await h.answer('youtube', videos('youtube', 2));
  const kept = JSON.parse(JSON.stringify(h.search.kept()));  // as localStorage keeps it
  const back = harness();
  back.search.resume(kept);
  assert.equal(back.state().query, '晴天');
  assert.equal(back.state().found.length, 2);
  assert.deepEqual(back.asked.map(a => a.source), ['bilibili']);
}

(async () => {
  severalSitesVideosAreMergedInTurnEachVideoOnceWithTheAddressesOfItsCopies();
  await everySiteChosenIsAskedAtOnceAndEachShowsAsItComes();
  await aSiteTurnedOnIsAskedAndOneTurnedOffLetGoOf();
  await onlyTheLatestSearchShows();
  await aSiteThatFailsSaysWhyAboveTheOthersVideos();
  await noSiteSearchedSaysWhyAndOffersOneTurnedOff();
  await aLinkIsReadOnceOnItsOwnSite();
  await aSearchKeptShowsAgainAsItWasAskingOnlyWhatWasNotAnswered();
  aVideoFoundIsAPieceMadeOneBeingPreparedOrNeitherByAnyCopyOfIt();
  theWordsForItInBothLanguages();
  console.log('Online search passed: the sites merged as on the Mac, each shown as it comes, one turned on or off, ' +
              'only the latest search shown, a failure said as why, a link read on its own site, what a video found ' +
              'is in the library, and its words.');
})().catch(error => { console.error(error); process.exitCode = 1; });
