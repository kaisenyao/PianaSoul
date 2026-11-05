// Searching online from the phone (GET /api/online): by name on each site chosen (YouTube, Bilibili: one setting,
// for her and every phone), every one asked at once and its videos shown as soon as they come, merged as the Mac
// merges hers; or by a video's link, on its own or in words around it (as Bilibili's app shares one), read once, on
// its own site. The latest search is the one shown: an answer to one before it, to one let go of, or from a site
// turned off meanwhile, shows nothing when it comes back. A site that fails says why, never that nothing was found:
// each failure is {reason, source}, the reason unreachable, refused or failed, as the Mac says it.
export const SOURCES = ['youtube', 'bilibili'];  // the sites online search can search, in the order they are merged
const isLink = q => /^https?:\/\//i.test(q);
const linkIn = q => q.match(/https?:\/\/[!-~]+/i)?.[0];  // up to a space, a CJK character or a 】

// Several sites' videos as one list: the first of each, then the second of each, and so on (round robin, the usual
// way to merge the ranked lists of several engines), a video already in it left out: the same address, or the same
// title and length (to 2 s), as one performance posted on both sites is. The one kept has the addresses of every copy
// (urls, its own first), for the piece made from any of them, or being made. The Mac's merge for her (online.merged)
// is the same, tested with the same cases.
const titleOf = video => (video.video_title || '').toLowerCase().split(/\s+/).filter(Boolean).join(' ');
const same = (a, b) => a.url === b.url
  || !!titleOf(a) && titleOf(a) === titleOf(b) && Math.abs((a.duration_s || 0) - (b.duration_s || 0)) <= 2;
export function merged(lists) {
  const together = [];
  for (let rank = 0; rank < Math.max(0, ...lists.map(videos => videos.length)); rank++) {
    for (const videos of lists) {
      const video = videos[rank];
      if (!video) continue;
      const kept = together.find(k => same(video, k));
      if (!kept) together.push({...video, urls: [video.url]});
      else if (!kept.urls.includes(video.url)) kept.urls.push(video.url);
    }
  }
  return together;
}

// What a video found is in the library, by any copy of it (urls): a piece made ({made}, its recording), one being
// prepared ({job}), or neither ({}). Its row acts on that piece, from its title and from the control at its end: both
// download it, and once it is made both play it; while it is prepared the title plays it as soon as it can, and the
// control, its ring, stops it. Its cover opens the video on its site, whatever the piece is.
export function pieceOf(video, recordings, jobs) {
  const made = recordings.find(r => video.urls.includes(r.source_url));
  const job = jobs.find(j => video.urls.includes(j.source));
  return made ? {made} : job ? {job} : {};
}

// What is shown (state): the query; found, the videos so far, merged; failed, why each site that could not be
// searched could not; answered, the sites that were; waiting, those still on their way; and offer, the sites turned
// off, to turn on when none could be searched because one is out of reach.
export function onlineSearch(get, onChange) {
  const state = {query: '', found: [], failed: [], answered: [], waiting: [], offer: []};
  let chosen = [], asked = {};  // asked: each site asked (a link: ''), by name, with its answer once it comes

  function show() {
    const sites = isLink(state.query) ? [''] : SOURCES.filter(site => site in asked);
    state.found = merged(sites.filter(site => asked[site].found).map(site => asked[site].found));
    state.failed = sites.filter(site => asked[site].failed).map(site => asked[site].failed);
    state.answered = sites.filter(site => asked[site].found);
    state.waiting = sites.filter(site => !asked[site].found && !asked[site].failed);
    const none = !state.answered.length && !state.waiting.length;
    state.offer = none && !isLink(state.query) && state.failed.some(f => f.reason === 'unreachable')
      ? SOURCES.filter(site => !chosen.includes(site)) : [];
    onChange(state);
  }

  async function ask(site) {
    const answer = asked[site] = {found: null, failed: null};
    try {
      answer.found = await get(`/api/online?q=${encodeURIComponent(state.query)}${site ? `&source=${site}` : ''}`);
    } catch (error) {  // what the Mac said, or failed: the Mac itself not answering
      const said = error.json || {};
      answer.failed = {reason: ['unreachable', 'refused'].includes(said.error) ? said.error : 'failed',
                       source: 'source' in said ? said.source : site};
    }
    if (asked[site] === answer) show();  // one no longer asked for has nothing new to show
  }

  // A search on the sites chosen (a link: on its own site).
  function find(query, sites) {
    query = linkIn(query) || query;
    state.query = query;
    chosen = [...sites];
    asked = {};
    for (const site of isLink(query) ? [''] : chosen) ask(site);
    show();  // the spinner
  }

  // The sites chosen changed (here or elsewhere): a search by name asks one turned on, and lets go of one turned off.
  function follow(sites) {
    chosen = [...sites];
    if (state.query && !isLink(state.query)) {
      for (const site of Object.keys(asked)) if (!chosen.includes(site)) delete asked[site];
      for (const site of chosen) if (!(site in asked)) ask(site);
    }
    show();
  }

  // No search shown.
  function forget() {
    asked = {};
    state.query = '';
    show();
  }

  return {find, follow, forget, state};
}
