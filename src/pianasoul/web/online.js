// Searching online from the phone (GET /api/online), by name on the site chosen (YouTube or Bilibili: one setting,
// for her and every phone), or by a video's link. The latest search is the one shown: one still on its way when
// another starts, or when it is let go of, shows nothing when it comes back. One that fails says why, never that
// nothing was found: failed is {reason, source}, the reason unreachable, refused or failed, as the Mac says it; and
// a site that cannot be reached, searched by name, offers the other (offer).
export const SOURCES = ['youtube', 'bilibili'];
const isLink = q => /^https?:\/\//i.test(q);

export function onlineSearch(get, onChange) {
  const state = {query: '', source: '', searching: false, found: [], failed: null, offer: ''};
  let run = 0;

  async function find(query, source) {
    const mine = ++run;
    Object.assign(state, {query, source, searching: true, found: [], failed: null, offer: ''});  // the spinner
    onChange(state);
    let found = [], failed = null;
    try {
      found = await get(`/api/online?q=${encodeURIComponent(query)}&source=${source}`);
    } catch (error) {  // what the Mac said, or failed: the Mac itself not answering
      const said = error.json || {};
      failed = {reason: ['unreachable', 'refused'].includes(said.error) ? said.error : 'failed',
                source: 'source' in said ? said.source : source};
    }
    if (mine !== run) return;
    const offer = failed?.reason === 'unreachable' && !isLink(query) && SOURCES.find(s => s !== failed.source) || '';
    Object.assign(state, {searching: false, found, failed, offer});
    onChange(state);
  }

  // The site chosen changed (here or elsewhere): a search by name shown from another is made again on it.
  function follow(source) {
    if (state.query && !isLink(state.query) && source !== state.source) find(state.query, source);
  }

  // No search shown.
  function forget() {
    run++;
    Object.assign(state, {query: '', source: '', searching: false, found: [], failed: null, offer: ''});
  }

  return {find, follow, forget, state};
}
