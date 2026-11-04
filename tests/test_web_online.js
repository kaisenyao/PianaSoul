// Searching online from the phone (web/online.js): on the site chosen, the latest search shown, and a failure said as
// why it failed, never as nothing found; and the words for it (web/strings.js), in both languages.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('src/pianasoul/web/online.js', 'utf8').replace(/^export /gm, '');
const tick = async () => { for (let i = 0; i < 10; i++) await Promise.resolve(); };

// The Mac, answering each request when told: with what it found, or with an error as api() in app.js throws it.
function harness() {
  const h = {asked: []};
  const get = path => new Promise((resolve, reject) => {
    const url = new URL(path, 'https://mac.local');
    h.asked.push({q: url.searchParams.get('q'), source: url.searchParams.get('source'), resolve, reject});
  });
  const sandbox = {encodeURIComponent};
  vm.runInNewContext(source + '\nthis.onlineSearch = onlineSearch;', sandbox);
  h.search = sandbox.onlineSearch(get, () => {});
  h.state = () => JSON.parse(JSON.stringify(h.search.state));  // made in the page's own context: compared as plain values
  h.answer = async found => { h.asked.at(-1).resolve(found); await tick(); };
  h.fail = async json => { h.asked.at(-1).reject(Object.assign(Error(json.error), {json})); await tick(); };
  return h;
}

async function searchesTheSiteChosen() {
  const h = harness();
  h.search.find('晴天', 'bilibili');
  assert.deepEqual([h.asked[0].q, h.asked[0].source], ['晴天', 'bilibili']);
  assert.equal(h.state().searching, true, 'The spinner, not "nothing found"');
  await h.answer([{url: 'https://www.bilibili.com/video/BV1Aa411A7aA'}]);
  assert.deepEqual(h.state(), {query: '晴天', source: 'bilibili', searching: false,
    found: [{url: 'https://www.bilibili.com/video/BV1Aa411A7aA'}], failed: null, offer: ''});
  h.search.find('不存在的曲子', 'bilibili');
  await h.answer([]);
  assert.deepEqual([h.state().found, h.state().failed], [[], null], 'Nothing found is nothing found');
}

async function anotherSiteSearchesAgainAndOnlyTheLatestShows() {
  const h = harness();
  h.search.find('晴天', 'youtube');
  h.search.follow('bilibili');
  assert.deepEqual(h.asked.map(a => [a.q, a.source]), [['晴天', 'youtube'], ['晴天', 'bilibili']]);
  h.search.follow('bilibili');
  assert.equal(h.asked.length, 2, 'The same site: not again');
  await h.answer([{url: 'b'}]);
  h.asked[0].resolve([{url: 'y'}]);  // YouTube's answer, late
  await tick();
  assert.deepEqual(h.state().found, [{url: 'b'}]);
  h.search.find('https://youtu.be/abcdefghijk', 'bilibili');
  h.search.follow('youtube');
  assert.equal(h.asked.length, 3, 'A link is the same video on any site');
  h.search.forget();
  await h.answer([{url: 'https://www.youtube.com/watch?v=abcdefghijk'}]);
  assert.deepEqual([h.state().query, h.state().found], ['', []], 'Let go of: it shows nothing when it comes back');
  h.search.follow('bilibili');
  assert.equal(h.asked.length, 3, 'No search shown: none made');
}

async function aFailureSaysWhyAndOffersTheOtherSite() {
  const h = harness();
  h.search.find('晴天', 'youtube');
  await h.fail({error: 'unreachable', source: 'youtube'});
  assert.deepEqual([h.state().failed, h.state().offer], [{reason: 'unreachable', source: 'youtube'}, 'bilibili']);
  h.search.find('晴天', 'bilibili');
  await h.fail({error: 'refused', source: 'bilibili'});
  assert.deepEqual([h.state().failed, h.state().offer], [{reason: 'refused', source: 'bilibili'}, ''],
                   'Refused: the same site again, later and without a proxy');
  h.search.find('https://www.youtube.com/watch?v=abcdefghijk', 'bilibili');
  await h.fail({error: 'unreachable', source: 'youtube'});
  assert.deepEqual([h.state().failed.source, h.state().offer], ['youtube', ''], "A link's video is on its own site only");
  h.search.find('晴天', 'youtube');
  h.asked.at(-1).reject(TypeError('Load failed'));  // the Mac not answering
  await tick();
  assert.deepEqual(h.state().failed, {reason: 'failed', source: 'youtube'});
  h.search.find('晴天', 'youtube');
  await h.fail({error: 'transcribe_off'});
  assert.deepEqual([h.state().failed, h.state().found], [{reason: 'failed', source: 'youtube'}, []]);
}

function theWordsForItInBothLanguages() {
  const sandbox = {};
  const strings = fs.readFileSync('src/pianasoul/web/strings.js', 'utf8').replace(/^export const LANGUAGES/m, 'this.LANGUAGES');
  vm.runInNewContext(strings, sandbox);
  const {zh, en} = sandbox.LANGUAGES;
  assert.equal(zh.refused.bilibili, '哔哩哔哩暂时拒绝了搜索，请关闭代理后再试。');
  assert.equal(zh.unreachable(zh.sites.youtube), '无法连接到YouTube。');
  assert.equal(zh.searchInstead(zh.sites.bilibili), '改用哔哩哔哩搜索');
  assert.equal(en.unreachable(en.sites.youtube), 'Can’t connect to YouTube.');
  assert.equal(en.searchInstead(en.sites.bilibili), 'Search Bilibili Instead');
  for (const T of [zh, en]) {
    assert.deepEqual(Object.keys(T.sites), ['youtube', 'bilibili']);
    assert.deepEqual(Object.keys(T.refused), ['youtube', 'bilibili']);
    assert.notEqual(T.searchFailed, T.nothing, 'A failure is not "No Results"');
  }
  const english = [...Object.values(en.sites), ...Object.values(en.refused), en.searchFailed];
  assert.ok(english.every(s => !/\p{Script=Han}/u.test(s)), 'The English words are all English');
}

(async () => {
  await searchesTheSiteChosen();
  await anotherSiteSearchesAgainAndOnlyTheLatestShows();
  await aFailureSaysWhyAndOffersTheOtherSite();
  theWordsForItInBothLanguages();
  console.log('Online search passed: the site chosen, only the latest search shown, a failure said as why, and its words.');
})().catch(error => { console.error(error); process.exitCode = 1; });
