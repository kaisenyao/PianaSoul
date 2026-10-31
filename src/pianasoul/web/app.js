// The app: the orb at home, the sidebar beside it, and the pages over it (library, artists, conversations,
// settings) with the player always one swipe away. Without her (the Mac has no key for her, or not the advanced
// features), the library is home instead, and nothing of hers shows. Everything shown comes from the Mac (/api,
// /events); what is done here goes back to it, so voice, chat and the phone always agree.
import {LANGUAGES} from './strings.js';
import {addOrb, animateOrb, homeColor, sameLook} from './orb.js';
import {createCues} from './cues.js';
import {uploader} from './upload.js';
import {voice as makeVoice} from './voice.js';

// ---------- Small things ----------
const $ = sel => document.querySelector(sel);
const app = $('#app'), main = $('#main'), pages = $('#pages');
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
// Enter, but not the Enter that picks a word on a Chinese or Japanese keyboard (Safari reports that one as 229).
const isEnter = e => e.key === 'Enter' && !e.isComposing && e.keyCode !== 229;
const mmss = s => {
  s = Math.max(0, Math.floor(s || 0));
  const m = Math.floor(s / 60), ss = String(s % 60).padStart(2, '0');
  return m >= 60 ? `${Math.floor(m / 60)}:${String(m % 60).padStart(2, '0')}:${ss}` : `${m}:${ss}`;
};
const DEL = 88, HOLD_MS = 480;
let guardUntil = 0;  // a drag or long-press never also counts as a tap
app.addEventListener('click', e => { if (performance.now() < guardUntil) { e.stopPropagation(); e.preventDefault(); } }, true);
const shake = el => el?.animate?.([{transform: 'translateX(0)'}, {transform: 'translateX(-6px)'}, {transform: 'translateX(6px)'},
  {transform: 'translateX(-3px)'}, {transform: 'translateX(0)'}], {duration: 320});

const svg = (d, extra = '') => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"${extra}>${d}</svg>`;
const ICON = {
  heart: svg('<path d="M12 20s-7.5-4.6-7.5-10.2A4.3 4.3 0 0 1 12 7.3a4.3 4.3 0 0 1 7.5 2.5C19.5 15.4 12 20 12 20z"/>'),
  edit: svg('<path d="M16.5 4.5a2.1 2.1 0 0 1 3 3L8 19l-4 1 1-4z"/>'),
  select: svg('<circle cx="12" cy="12" r="8.5"/><path d="M8.3 12.2l2.6 2.6 4.9-5.3"/>'),
  trash: svg('<path d="M4.5 7h15M10 7V4.8h4V7M6.5 7l1 12.5h9L17.5 7"/>'),
  order: svg('<path d="M4 6h12M4 11h12M4 16h7"/><path d="M17.5 13v7m0 0-2.6-2.6m2.6 2.6 2.6-2.6"/>'),
  one: svg('<path d="M17 3l3 3-3 3"/><path d="M4 11V9a3 3 0 0 1 3-3h13"/><path d="M7 21l-3-3 3-3"/><path d="M20 13v2a3 3 0 0 1-3 3H4"/><path d="M11 10.5l1.5-1V15"/>'),
  shuffle: svg('<path d="M16 3h5v5"/><path d="M4 20 21 3"/><path d="M21 16v5h-5"/><path d="M15 15l6 6"/><path d="M4 4l5 5"/>'),
  playlist: svg('<path d="M21 15V6"/><circle cx="18.5" cy="17.5" r="2.5"/><path d="M12 12H3M16 6H3M12 18H3"/>'),
  queue: svg('<path d="M9 6h11M9 12h11M9 18h11"/><circle cx="4.5" cy="6" r="1.2" fill="currentColor"/><circle cx="4.5" cy="12" r="1.2" fill="currentColor"/><circle cx="4.5" cy="18" r="1.2" fill="currentColor"/>'),
  add: svg('<path d="M12 5v14M5 12h14"/>', ' stroke-width="2.2"'),
  more: svg('<circle cx="5.5" cy="12" r="1.3" fill="currentColor"/><circle cx="12" cy="12" r="1.3" fill="currentColor"/><circle cx="18.5" cy="12" r="1.3" fill="currentColor"/>'),
  grip: svg('<path d="M5 8h14M5 12h14M5 16h14"/>'),
  mic: svg('<rect x="9" y="3" width="6" height="11.5" rx="3"/><path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3"/>'),
  up: svg('<path d="M12 19V5M5.5 11.5 12 5l6.5 6.5"/>', ' stroke-width="2.6"'),
  check: svg('<path d="M5 12.5l4.5 4.5L19 7.5"/>', ' stroke-width="2.6"'),
  close: svg('<path d="M6 6l12 12M18 6 6 18"/>'),
  gear: svg('<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>'),
  globe: svg('<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.5 2.5 2.5 14.5 0 17M12 3.5c-2.5 2.5-2.5 14.5 0 17"/>'),
  moon: svg('<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/>'),
  piano: svg('<rect x="3" y="5" width="18" height="14" rx="2.5"/><path d="M9 13v6M15 13v6"/><rect x="7.6" y="5" width="2.8" height="8" rx=".6" fill="currentColor" stroke="none"/><rect x="13.6" y="5" width="2.8" height="8" rx=".6" fill="currentColor" stroke="none"/>'),
  tablet: svg('<rect x="4.5" y="3" width="15" height="18" rx="2.5"/><path d="M10.5 18h3"/>'),
  book: svg('<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5z"/><path d="M4 20.5A2.5 2.5 0 0 0 6.5 23H20v-5"/>'),
  sparkle: svg('<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/><path d="M19 16l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7z"/>'),
  hand: svg('<path d="M8 13V5.5a1.5 1.5 0 0 1 3 0V12"/><path d="M11 11.5v-2a1.5 1.5 0 0 1 3 0V12"/><path d="M14 10.5a1.5 1.5 0 0 1 3 0V12"/><path d="M17 11.5a1.5 1.5 0 0 1 3 0V15a7 7 0 0 1-7 7h-1.5a6 6 0 0 1-4.6-2.2L4.3 16.5a1.6 1.6 0 0 1 2.4-2.1L8 15.6"/>'),
  play: svg('<path d="M7 4.9v14.2c0 .8.9 1.3 1.6.9l11-7.1c.6-.4.6-1.4 0-1.8l-11-7.1C7.9 3.6 7 4.1 7 4.9z"/>'),
  bubble: svg('<path d="M20 11.5c0 4.1-3.6 7.5-8 7.5-1.1 0-2.2-.2-3.1-.6L4 20l1.4-3.8A7.2 7.2 0 0 1 4 11.5C4 7.4 7.6 4 12 4s8 3.4 8 7.5z"/>'),
  question: svg('<circle cx="12" cy="12" r="8.5"/><path d="M9.6 9.4a2.5 2.5 0 0 1 4.8.9c0 1.7-2.4 2.2-2.4 3.7"/><circle cx="12" cy="17" r=".6" fill="currentColor"/>'),
  updown: svg('<path d="M8 9.5 12 5.5l4 4M8 14.5l4 4 4-4"/>', ' stroke-width="2.2"'),
  chevron: '<svg viewBox="0 0 8 14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 1l6 6-6 6"/></svg>',
  share: svg('<path d="M12 15V3.5M7.5 8 12 3.5 16.5 8"/><path d="M8 11H6.5A1.5 1.5 0 0 0 5 12.5v7A1.5 1.5 0 0 0 6.5 21h11a1.5 1.5 0 0 0 1.5-1.5v-7a1.5 1.5 0 0 0-1.5-1.5H16"/>'),
  pin: svg('<path d="M12 17v5"/><path d="M9 10.8a2 2 0 0 1-1.1 1.8l-1.8.9A2 2 0 0 0 5 15.2v.8a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.8a2 2 0 0 0-1.1-1.8l-1.8-.9A2 2 0 0 1 15 10.8V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z"/>'),
};
const CHECK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M6 12.5l4 4 8-9"/></svg>';
const ICON_PLAY = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M7 4.9v14.2c0 .8.9 1.3 1.6.9l11-7.1c.6-.4.6-1.4 0-1.8l-11-7.1C7.9 3.6 7 4.1 7 4.9z"/></svg>';
const ICON_PAUSE = '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="5.5" y="4" width="4.6" height="16" rx="1.3"/><rect x="13.9" y="4" width="4.6" height="16" rx="1.3"/></svg>';
const HEART_LINE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="M12 20s-7.5-4.6-7.5-10.2A4.3 4.3 0 0 1 12 7.3a4.3 4.3 0 0 1 7.5 2.5C19.5 15.4 12 20 12 20z"/></svg>';
const HEART_FILL = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 20s-7.5-4.6-7.5-10.2A4.3 4.3 0 0 1 12 7.3a4.3 4.3 0 0 1 7.5 2.5C19.5 15.4 12 20 12 20z"/></svg>';
const ICON_ADD = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>';
const ICON_UPLOAD = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 15V4M7.5 8.5 12 4l4.5 4.5"/><path d="M5 14v4.5A1.5 1.5 0 0 0 6.5 20h11a1.5 1.5 0 0 0 1.5-1.5V14"/></svg>';
const ICON_FIND = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="6.5"/><path d="M16 16l4 4"/></svg>';
const ICON_DOWNLOAD = '<svg viewBox="0 0 28 28" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="14" cy="14" r="11.5"/><path d="M14 8.6v10.2M9.7 14.6l4.3 4.3 4.3-4.3"/></svg>';
const ICON_PLAY_CIRCLE = '<svg viewBox="0 0 28 28" fill="none" stroke="currentColor" stroke-width="2"><circle cx="14" cy="14" r="11.5"/><path d="M11.6 9.6v8.8c0 .5.5.8.9.5l6.9-4.4c.4-.2.4-.8 0-1l-6.9-4.4c-.4-.3-.9 0-.9.5z" fill="currentColor" stroke="none"/></svg>';
const SPINNER = '<svg viewBox="0 0 28 28" class="ring waiting"><g class="dial"><circle cx="14" cy="14" r="11.5" class="trk"/><circle cx="14" cy="14" r="11.5" class="arc" stroke-dasharray="18 100"/></g></svg>';
const CHEV = `<span class="chev">${ICON.chevron}</span>`;

let voice = null;  // the orb's ears and voice, once the page is wired

// ---------- Language and look ----------
// The app's words are Chinese or English, the phone's until one is chosen; what people said stays as it was said.
let T = LANGUAGES.zh, lang = 'zh';
const dark = matchMedia('(prefers-color-scheme: dark)');
function applySettings() {
  try { localStorage.setItem('settings', JSON.stringify(data.settings)); } catch {}
  lang = LANGUAGES[data.settings.language] ? data.settings.language : /^zh/i.test(navigator.language) ? 'zh' : 'en';
  T = LANGUAGES[lang];
  document.documentElement.lang = lang === 'zh' ? 'zh-CN' : 'en';
  const theme = data.settings.appearance === 'system' ? (dark.matches ? 'dark' : 'light') : data.settings.appearance;
  document.documentElement.dataset.theme = theme;
  $('meta[name=theme-color]').content = theme === 'dark' ? '#000000' : '#ffffff';
  // The icon on a white or a black ground, as the look: Add to Home Screen keeps the one there is at that moment.
  for (const link of document.querySelectorAll('link[rel=icon], link[rel=apple-touch-icon]')) {
    link.href = theme === 'dark' ? '/icon.png' : '/icon-light.png';
  }
  for (const el of document.querySelectorAll('[data-t]')) el.textContent = T[el.dataset.t];
  for (const el of document.querySelectorAll('[data-t-placeholder]')) el.placeholder = T[el.dataset.tPlaceholder];
  for (const el of document.querySelectorAll('[data-t-label]')) el.setAttribute('aria-label', T[el.dataset.tLabel]);
  $('#search-input').placeholder = herOn() ? T.searchAll : T.searchPieces;
  $('#add-input').placeholder = data.features.search ? T.addHint : T.linkHint;
  voice?.render();
  refresh();
}
dark.addEventListener('change', () => { if (data.settings.appearance === 'system') applySettings(); });

// ---------- What the Mac keeps ----------
const data = {recordings: [], jobs: [], audio: [], playback: {id: null, state: 'stopped', ids: [], scope: {}, mode: 'order'},
  conversations: [], current: null, busy: null, settings: {language: '', appearance: 'system'}, devices: [],
  piano: {name: '', kind: 'other', connected: true},
  version: {commit: '', repo: ''}, features: {transcribe: true, search: true, assistant: true}, playlists: []};
let heardAt = performance.now();  // when the playback state arrived, to run its clock from
// What the Mac can do and the look chosen: kept on this phone too, so that the page opens on the right home (her orb,
// or the library) and in the right colors before the Mac has said.
try { Object.assign(data.features, JSON.parse(localStorage.getItem('features')) || {}); } catch {}
try { Object.assign(data.settings, JSON.parse(localStorage.getItem('settings')) || {}); } catch {}
const herOn = () => !!data.features.assistant;

// The address carries #token=…, and keeps it: Add to Home Screen saves this address, and the Home Screen app does
// not share Safari's storage. The token buys a cookie that the page, its media and its connections carry.
let token = new URLSearchParams(location.hash.slice(1)).get('token') || '';
try {
  token ||= localStorage.getItem('token') || '';
  if (token) localStorage.setItem('token', token);
} catch {}
if (token && !location.hash) history.replaceState(null, '', `#token=${encodeURIComponent(token)}`);

async function api(path, method = 'GET', body) {
  const response = await fetch(path, {method, headers: body === undefined ? {} : {'Content-Type': 'application/json'},
    body: body === undefined ? undefined : JSON.stringify(body)});
  if (response.status === 401) throw Error('signed out');
  const json = await response.json().catch(() => ({}));
  if (!response.ok) throw Object.assign(Error(json.error || response.status), {json});
  return json;
}
const recordings = async () => { data.recordings = await api('/api/recordings'); refresh(); };
async function conversations() {
  const c = await api('/api/conversations');
  data.conversations = c.conversations;
  data.current = c.current;
  if (state.session === 'now' && data.current) {  // the empty current conversation just began
    const top = stack.findLast(e => e.view === 'session' && e.id === 'now');
    if (top) top.id = state.session = data.current;
  }
  renderRecent();
  refresh();
}

// What changed on the Mac, as it happens. A lost connection comes back, with everything read again.
function listen() {
  const socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/events`);
  socket.onmessage = ({data: raw}) => {
    let e = null;
    try { e = JSON.parse(raw); } catch { return; }
    if (e.type === 'playback') { delete e.type; data.playback = e; heardAt = performance.now(); refresh(); }
    else if (e.type === 'jobs') onJobs(e.jobs);
    else if (e.type === 'library') recordings();
    else if (e.type === 'conversations') conversations();
    else if (e.type === 'message') onMessage(e.message);
    else if (e.type === 'busy') { data.busy = e.conversation; renderTyping(); }
    else if (e.type === 'settings') { delete e.type; data.settings = e; applySettings(); }
    else if (e.type === 'audio') { data.audio = e.audio; refresh(); }
    else if (e.type === 'playlists') { data.playlists = e.playlists; refresh(); }
    else if (e.type === 'piano') { data.piano = e.piano; renderPiano(); if (state.view === 'settings') loadDevices(); }
  };
  socket.onclose = () => setTimeout(() => load().then(listen, error => error.message === 'signed out' ? signedOut(true)
                                                                                          : listen()), 2000);
}
async function load() {
  Object.assign(data, await api('/api/state'));
  heardAt = performance.now();
  try { localStorage.setItem('features', JSON.stringify(data.features)); } catch {}
  applyFeatures();
  applySettings();
  renderRecent();
  for (const e of stack) if (e.view === 'session') renderSession(e.id);  // what was said while the Mac was away
}
// Until the Mac has said whether this page may come in (a moment, a second at most), the page shows only its background:
// one it no longer lets in goes straight to saying so, without the home page showing first.
async function signIn() {
  const show = () => $('#app').classList.remove('waiting');
  setTimeout(show, 1000);
  try {
    if (!token) return signedOut(false);
    await api('/api/session', 'POST', {token});
    show();
    await load();
    listen();
  } catch (error) {
    if (error.message === 'signed out') signedOut(true);
    else setTimeout(signIn, 3000);  // the Mac is asleep or away: again until it answers
  } finally {
    show();
  }
}

// The Mac does not let this page in: it paired anew (refused: this page's token is an old one), or this page never had
// a token. Said over the page instead of leaving it empty, until a new code is scanned.
function signedOut(refused) {
  closeOverlays();  // nothing open is any use now, and a sheet would darken the page behind the notice
  token = '';
  try { localStorage.removeItem('token'); } catch {}
  applySettings();  // the look and words chosen, or the phone's when none came: before anything is loaded, it follows the phone
  $('#signed-out-title').textContent = refused ? T.pairedAnewTitle : T.notPairedTitle;
  $('#signed-out-text').textContent = refused ? T.pairedAnew : T.notPaired;
  $('#signed-out').hidden = false;
}

// ---------- Pieces ----------
// A piece is a library recording, or one still being made (a job, kept apart by its key).
const pieceById = id => data.recordings.find(r => r.id === id);
const jobKey = j => 'j' + j.id;
function titleParts(title) {
  const dash = title.indexOf(' - ');
  const artist = dash < 0 ? '' : title.slice(0, dash), rest = dash < 0 ? title : title.slice(dash + 3);
  const found = rest.match(/\s*[(（]([^()（）]*)[)）]\s*$/);
  return {artist, piece: (found ? rest.slice(0, found.index) : rest).trim() || title, performer: found ? found[1] : ''};
}
const subtitle = p => [p.artist, p.performer].filter(Boolean).join(' · ');
// The site a piece came from, as people call it: YouTube, or its address.
const siteOf = url => {
  const host = new URL(url).hostname.replace(/^(www|m)\./, '');
  return /^(youtube\.com|music\.youtube\.com|youtu\.be)$/.test(host) ? 'YouTube' : host;
};
const hueOf = id => 200 + (id * 37) % 140;
const NAME_HUES = [212, 250, 285, 330, 12, 168];  // a few clear hues, so no avatar turns muddy
const hueOfName = s => NAME_HUES[[...s].reduce((h, ch) => (h * 31 + ch.codePointAt(0)) % 997, 7) % NAME_HUES.length];
const initial = s => [...s][0].toUpperCase();
// How long a list of pieces lasts, in hours and minutes.
const totalTime = list => {
  const m = Math.round(list.reduce((sum, p) => sum + (p.duration_s || 0), 0) / 60);
  return T.duration(Math.floor(m / 60), m % 60);
};
const byNewest = (a, b) => b.id - a.id;
const audioName = j => data.audio.find(a => a.id === j.audio)?.name || '';  // a recording's, until the piece is named
const jobPieces = () => data.jobs.map(j => ({...titleParts(j.title || audioName(j)), key: jobKey(j), job: j}));

// ---------- Playing ----------
const pb = () => data.playback;
const nowPiece = () => pieceById(pb().id);
// Something is playing while a piece plays or is paused; once the list is over (or stopped) nothing is, and the
// mini player goes. The player then shows the list back at its first piece, and ▶ plays it again.
const somethingPlaying = () => !!nowPiece() && (pb().state === 'playing' || pb().state === 'paused');
const isPlaying = id => somethingPlaying() && pb().id === id;
// Seconds into the piece as heard now, on the Mac's report (see Playback.snapshot): it moves on after starts_in,
// up to until, so a pause stops the picture when the sound stops.
const position = () => Math.min(pb().until ?? Infinity,
  (pb().position || 0) + Math.max(0, (performance.now() - heardAt) / 1000 - (pb().starts_in || 0)));
async function control(action, value, index, from) {
  try {
    data.playback = await api('/api/playback', 'POST', {action, value, index});
    heardAt = performance.now();
    refresh();
  } catch { shake(from); }
}
// Tapping a piece plays it (unless it is already the current one) and opens the player over the list.
// ids and scope: the list it was tapped in, which becomes the list that plays.
async function play(id, ids, scope, from, mode) {
  try {
    data.playback = await api('/api/play', 'POST', {id, ids, scope, mode});
    heardAt = performance.now();
    refresh();
    openPlayer();
  } catch { shake(from); }
}
const pausePiano = () => { if (pb().state === 'playing') control('toggle'); };
function scopeName(scope) {
  if (scope.kind === 'conversation') {
    const c = data.conversations.find(x => x.id === scope.id);
    return scope.id === data.current || !c ? T.currentChat : T.quoted(conversationTitle(c));
  }
  if (scope.kind === 'artist' || scope.kind === 'performer') return scope.name;
  if (scope.kind === 'playlist') return T.quoted(playlistById(scope.id)?.name || T.playlists);
  return T[scope.kind] || T.library;
}

// ---------- Views ----------
const state = {view: 'orb', session: null, open: false, selecting: false};
const selected = new Set();
// The pages over home, bottom first: {view, id}. id is a conversation id (or 'now', the empty current one) for
// 'session', {kind: 'favorites' | 'recent' | 'artist' | 'performer', name} or {kind: 'playlist', id} for 'group'. Empty means home: the orb,
// or the sidebar beside it. The player is a page too, always the top one.
const stack = [];
function show(view, id = null) {
  for (const e of stack.splice(0)) slideOut($('#view-' + e.view));
  if (view === 'orb' || (view === 'library' && !herOn())) setOpen(false);  // home
  else { setOpen(true); push(view, id); }
  settle();
}
// Home is her orb, or without her the library: then the library page itself sits where the orb would, and
// everything else (the sidebar beside it, the player a left swipe away, the pages over it) is as it is with her.
function applyFeatures() {
  const lib = $('#view-library'), basic = !herOn();
  app.classList.toggle('basic', basic);
  if (basic === (lib.parentNode === main)) return;
  for (const e of stack.splice(0)) Object.assign($('#view-' + e.view), {hidden: true}).classList.remove('off');
  if (basic) main.append(lib);
  else pages.prepend(lib);
  lib.hidden = !basic;
  lib.classList.remove('off');
  lib.style.transform = lib.style.zIndex = '';
  setOpen(false);
  settle();
}
// A page made ready just past the right edge: drawn, scrolled to where it opens, and above the pages under it.
function ready(view, id = null) {
  const el = $('#view-' + view);
  el.dataset.gen = +(el.dataset.gen || 0) + 1;
  el.hidden = false;
  el.style.zIndex = view === 'player' ? 30 : stack.length + 1;  // the player also covers the mini player
  moveAside(el, null);
  el.classList.add('off');
  if (view === 'session') toLatest();  // a conversation opens at its latest message, or at a line search found
  renderEntry({view, id});
  if (view === 'player') showScreen(0);
  else if (view !== 'session') el.scrollTop = 0;
  if (view === 'settings') loadDevices();
  return el;
}
function push(view, id = null, el = ready(view, id)) {
  stack.push({view, id});
  el.offsetWidth;
  el.classList.remove('off');
  moveAside(el, null);
  settle();
}
function pop() {
  const e = stack.pop();
  if (e) slideOut($('#view-' + e.view));
  settle();
  if (state.view === 'session') drawChat(state.session);  // what came while it was covered
}
// A page the finger holds x pixels from its place, rounding as it goes (--off); null gives it back to its place, or to
// its slide in or out.
function moveAside(el, x) {
  el.style.transform = x === null ? '' : `translateX(${x}px)`;
  if (x === null) el.style.removeProperty('--off');
  else el.style.setProperty('--off', Math.min(1, x / app.offsetWidth));
}
function slideOut(el) {
  const gen = el.dataset.gen;
  moveAside(el, null);
  el.classList.add('off');
  setTimeout(() => { if (el.dataset.gen === gen) { el.hidden = true; el.classList.remove('off'); } }, 400);
}
// After the stack changes: the top page decides what the rest of the screen shows.
function settle() {
  const t = stack.at(-1);
  state.view = t ? t.view : herOn() ? 'orb' : 'library';
  state.session = t ? t.id : null;
  voice?.hold(state.open || stack.length > 0);
  setSelecting(false);
  $('#mini').style.transform = '';
  $('#pull').hidden = state.view !== 'session';
  if (state.view !== 'session') { endDictation(); $('#compose-input').blur(); }
  renderNow();
}
function renderEntry({view, id}) {
  if (view === 'library') renderLibrary();
  if (view === 'artists') renderArtists();
  if (view === 'playlists') renderPlaylists();
  if (view === 'group') renderGroup(id);
  if (view === 'history') renderHistory();
  if (view === 'session') renderSession(id);
  if (view === 'settings') renderSettings();
  if (view === 'help') renderHelp();
  if (view === 'article') renderArticle(id);
  if (view === 'player') renderNow();
}
// Redraw every open page, the 播放列表 if it is open, and what is playing.
function refresh() {
  renderPiano();
  if (!herOn()) renderLibrary();  // home
  for (const e of stack) if (e.view !== 'session') renderEntry(e);
  if (openSheet === $('#sheet-queue')) renderQueue();
  if (openSheet === $('#sheet-add')) renderCands();
  if (openSheet === $('#sheet-pick')) renderPick();
  renderSessionCards();
  renderNow();
}

// ---------- Conversations ----------
// Named after the pieces she played in it, or else what you said first (without her name), unless renamed.
function conversationTitle(c) {
  if (!c) return T.currentChat;
  if (c.title) return c.title;
  const names = [...new Set((c.pieces || []).map(t => titleParts(t).piece))];
  if (names.length) return names.length > 2 ? T.andMore(names.slice(0, 2).join(' · ')) : names.join(' · ');
  if (!c.first) return T.currentChat;
  return c.first.replace(/^(小茗同学|小明同学|hey jasmine|jasmine)[，,。.!！\s]*/i, '') || c.first;
}
const local = stamp => new Date(stamp);
const dayStart = d => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
const daysAgo = stamp => Math.round((dayStart(new Date()) - dayStart(local(stamp))) / 864e5);
const clockTime = stamp => local(stamp).toLocaleTimeString(lang === 'zh' ? 'zh-CN' : 'en', {hour: 'numeric', minute: '2-digit'});
// When something was, as Messages writes it: the time today, 昨天, the weekday within a week, else the date.
function agoLabel(stamp) {
  const days = daysAgo(stamp), d = local(stamp);
  if (days <= 0) return clockTime(stamp);
  if (days === 1) return T.yesterday;
  if (days < 7) return T.weekday(d.getDay());
  return T.monthDay(d.getMonth() + 1, d.getDate());
}
function dayName(stamp) {
  const days = daysAgo(stamp), d = local(stamp);
  if (days <= 0) return T.today;
  if (days === 1) return T.yesterday;
  return T.date(d.getFullYear(), d.getMonth() + 1, d.getDate());
}
const groupOf = c => { const days = daysAgo(c.updated_at); return days <= 0 ? 'today' : days === 1 ? 'yesterday' : days < 7 ? 'week' : 'earlier'; };
const currentConversation = () => data.conversations.find(c => c.id === data.current) || null;
// Pinned first (the latest pinned on top), then the rest, latest first; the current one is shown on its own.
const others = () => {
  const rest = data.conversations.filter(c => c.id !== data.current);
  return [...rest.filter(c => c.pinned_at).sort((a, b) => b.pinned_at.localeCompare(a.pinned_at)), ...rest.filter(c => !c.pinned_at)];
};
const pinMark = c => c.pinned_at ? `<span class="pin" aria-label="${esc(T.pinnedMark)}">${ICON.pin}</span>` : '';
function renderRecent() {
  const now = currentConversation();
  $('#recent').innerHTML = `
    <button class="sb-row recent" data-target="${now ? now.id : 'now'}">
      <span class="ago">${now ? esc(agoLabel(now.updated_at)) : ''}</span><span class="label">${esc(conversationTitle(now))}</span><span class="live-dot" aria-label="${esc(T.current)}"></span>
    </button>` + others().slice(0, 7).map(c => `
    <button class="sb-row recent" data-target="${c.id}">
      <span class="ago">${esc(agoLabel(c.updated_at))}</span><span class="label">${esc(conversationTitle(c))}</span>${pinMark(c)}
    </button>`).join('');
  $('#sidebar [data-target=history]').hidden = others().length <= 7;
}

function renderHistory() {
  const row = c => `<div class="swipe" data-id="${c ? c.id : 'now'}">
      <button class="del" tabindex="-1">${esc(T.delete)}</button>
      <div class="content"><div class="text"><div class="t1">${esc(conversationTitle(c))}</div></div>
      ${!c || c.id === data.current ? `<span class="side now">${esc(T.current)}</span>`
        : `<span class="side">${esc(c.pinned_at ? dayName(c.updated_at) : groupOf(c) === 'today' || groupOf(c) === 'yesterday' ? clockTime(c.updated_at) : dayName(c.updated_at))}</span>`}</div>
    </div>`;
  const list = others(), pinned = list.filter(c => c.pinned_at);
  $('#history-list').innerHTML = [[T.pinned, pinned], ...['today', 'yesterday', 'week', 'earlier'].map(g =>
    [T[g], (g === 'today' ? [currentConversation()] : []).concat(list.filter(c => !c.pinned_at && groupOf(c) === g))])]
    .map(([g, rows]) => rows.length ? `<div class="group">${esc(g)}</div>${rows.map(row).join('')}` : '').join('');
}

// One conversation, as in iMessage: you in blue on the right, her in gray on the left, and the pieces she played
// as cards. A time shows at the start and wherever half an hour went by.
const sessionMessages = new Map();  // conversation id → its messages, as last read
function renderSession(id) {
  const c = id === 'now' ? null : data.conversations.find(x => x.id === id);
  $('#session-title').textContent = conversationTitle(c);
  drawChat(id);  // as last read, then as it is now
  if (id !== 'now') {
    api(`/api/conversations/${id}`).then(found => {  // with what came meanwhile, after what it had then
      const last = found.messages.at(-1)?.id ?? 0;
      sessionMessages.set(id, [...found.messages, ...(sessionMessages.get(id) || []).filter(m => m.id > last)]);
      if (state.session === id) drawChat(id);
    }).catch(() => {});
  }
}
let findMessage = null;  // a message found by the sidebar search, to open the conversation at
function drawChat(id) {
  const messages = id === 'now' ? [] : sessionMessages.get(id) || [];
  let last = null;
  $('#session-chat').innerHTML = messages.map(m => {
    const at = Date.parse(m.at), mark = last === null || at - last > 30 * 60e3;
    last = at;
    return (mark ? `<div class="when">${esc(dayName(m.at))} ${esc(clockTime(m.at))}</div>` : '') + lineHTML(m);
  }).join('');
  renderTyping();
  const shown = findMessage && document.querySelector(`#session-chat [data-m="${findMessage}"]`);
  if (shown) {
    findMessage = null;
    atLatest = false;
    shown.scrollIntoView({block: 'center'});
    shown.classList.add('found');
  }
}
function lineHTML(m) {
  if (m.role === 'piece') return pieceCard(m.recording_id, m.text, m.id);
  return `<div class="${m.role === 'user' ? 'u' : 'a'}" data-m="${m.id}">${esc(m.text)}</div>`;
}
// A played piece as her message, like an audio message: tap it to play it again.
function pieceCard(id, title, mid) {
  const p = pieceById(id), m = mid ? ` data-m="${mid}"` : '';
  if (!p) return `<div class="card gone"${m}>${esc(titleParts(title).piece)} · ${esc(T.gone)}</div>`;
  const on = isPlaying(id);
  return `<button class="card" data-piece="${id}" data-title="${esc(title)}"${m}>
      <span class="cplay">${on ? `<span class="eq${pb().state === 'paused' ? ' paused' : ''}"><i></i><i></i><i></i></span>` : ICON_PLAY}</span>
      <span class="cbody"><span class="ct">${esc(p.piece)}</span><span class="cs">${esc(subtitle(p))}</span>
        <span class="cbar"><span class="cfill" style="width:${on ? Math.min(100, position() / (p.duration_s || 1) * 100) : 0}%"></span></span></span>
      <span class="cdur">${mmss(p.duration_s)}</span>
    </button>`;
}
function renderSessionCards() {
  if (state.view !== 'session') return;
  for (const card of document.querySelectorAll('#session-chat .card[data-piece]')) {
    card.outerHTML = pieceCard(+card.dataset.piece, card.dataset.title, card.dataset.m);
  }
}
const piecesIn = id => [...new Set((sessionMessages.get(id) || []).filter(m => m.role === 'piece' && pieceById(m.recording_id)).map(m => m.recording_id))];
$('#session-chat').addEventListener('click', e => {
  const card = e.target.closest('[data-piece]');
  if (card) play(+card.dataset.piece, piecesIn(state.session), {kind: 'conversation', id: state.session}, card);
});
// A message kept on the Mac: it shows at once in its conversation, and the sidebar learns its new title.
let listsLater = null;
function onMessage(m) {
  const id = m.conversation_id;
  if (!sessionMessages.has(id) && state.session === 'now' && !data.current) sessionMessages.set(id, []);
  const kept = sessionMessages.get(id);
  if (kept && !kept.some(x => x.id === m.id)) kept.push(m);  // once, though a reading of it may have it already
  if (state.view === 'session' && (state.session === id || (state.session === 'now' && !data.current))) {
    if (state.session === 'now') { stack.at(-1).id = state.session = id; data.current = id; }
    drawChat(id);
  }
  clearTimeout(listsLater);
  listsLater = setTimeout(conversations, 300);
}
// Her answer on its way: three dots in the conversation she is answering.
function renderTyping() {
  document.querySelector('#session-chat .typing')?.remove();
  const id = state.session === 'now' ? null : state.session;
  if (state.view === 'session' && data.busy !== null && (data.busy === id || (id === null && !data.current))) {
    $('#session-chat').insertAdjacentHTML('beforeend', `<div class="typing" aria-label="${esc(T.typing)}"><i></i><i></i><i></i></div>`);
  }
}

// ---------- The library ----------
const favMark = p => p.favorited_at ? `<span class="fav" aria-label="${esc(T.favorite)}">${HEART_FILL}</span>` : '';
const JOB_TEXT = () => ({queued: T.queued, downloading: T.downloadingNow, converting: T.converting});
const pieceText = p => p.job
  ? `<div class="text"><div class="t1">${esc(p.piece)}</div><div class="t2">${esc(JOB_TEXT()[p.job.status] || '')}</div></div>`
  : `<div class="text"><div class="t1">${esc(p.piece)}${favMark(p)}</div><div class="t2">${esc(subtitle(p))}</div></div>`;
const RING = 72.26;  // 2πr for r = 11.5
// Download ring, as on Apple Music: one lap for the whole job, the square in the middle stops it.
const ringButton = j => `<button class="addbtn" data-stop="${j.id}" aria-label="${esc(T.stopDownload)}">
  <svg viewBox="0 0 28 28" class="ring${j.status === 'queued' ? ' waiting' : ''}" data-ring="${j.id}"><g class="dial">
    <circle cx="14" cy="14" r="11.5" class="trk"/>
    <circle cx="14" cy="14" r="11.5" class="arc" stroke-dasharray="${j.status === 'queued' ? '18 100' : RING}" stroke-dashoffset="${j.status === 'queued' ? 0 : RING * (1 - j.progress)}"/></g>
    <rect x="10.25" y="10.25" width="7.5" height="7.5" rx="1.5" class="stop"/></svg></button>`;
// label: what the right side shows instead of the length (when it was played, on 最近播放).
function pieceSide(p, label) {
  if (p.job) return ringButton(p.job);
  if (isPlaying(p.id)) return `<span class="eq${pb().state === 'paused' ? ' paused' : ''}"><i></i><i></i><i></i></span>`;
  return label ? `<span class="side when">${esc(label)}</span>` : `<span class="side">${mmss(p.duration_s)}</span>`;
}
// remove: what a left swipe does here. It moves the piece to the Mac's Trash in 曲库 and on an artist's page; in 收藏
// and 最近播放 it only takes the piece out of that list.
// grip: a handle to drag it by (in a playlist).
const pieceRow = (p, label, remove = T.trash, grip = false) => `<div class="swipe${p.job ? ' busy' : ''}${selected.has(p.id) ? ' selected' : ''}" data-id="${p.job ? p.key : p.id}">
    <button class="del" tabindex="-1">${esc(remove)}</button>
    <div class="content"><span class="check">${CHECK}</span>${pieceText(p)}${pieceSide(p, label)}${grip ? `<span class="grip" aria-label="${esc(T.drag)}">${ICON.grip}</span>` : ''}</div>
  </div>`;
const listIds = list => [...list.querySelectorAll('.swipe:not(.busy):not(.audio)')].map(r => +r.dataset.id);

// A recording in the library folder that is not a piece (yet): one that a job is making shows as that job; the rest
// show what they need. Without transcription it is gray, and a tap says how to have it made; with it, a button
// makes it now (again, if its piece was deleted, or once more after it failed).
const audioKey = a => 'a' + a.id;
const making = a => data.jobs.some(j => j.audio === a.id);
const audioItems = () => data.audio.filter(a => !making(a));
function audioRow(a) {
  const can = data.features.transcribe, failedOnce = a.status === 'failed';
  const said = !can ? T.needsTranscription : failedOnce ? T.transcribeFailed : a.status === 'done' ? T.canTranscribe
    : T.waitingTranscription;
  const side = !can ? '' : a.status === 'waiting' ? SPINNER
    : `<button class="hbutton" data-transcribe="${a.id}">${esc(failedOnce ? T.retry : T.transcribe)}</button>`;
  return `<div class="swipe audio${can ? '' : ' off'}" data-id="${audioKey(a)}">
      <button class="del" tabindex="-1">${esc(T.trash)}</button>
      <div class="content"><span class="check">${CHECK}</span><div class="text"><div class="t1">${esc(a.name)}</div>
        <div class="t2">${esc(said)}</div></div>${side}</div>
    </div>`;
}
const transcribeAudio = id => api(`/api/audio/${id}/transcribe`, 'POST').catch(() => {});
const trashAudio = id => {
  data.audio = data.audio.filter(a => a.id !== id);
  refresh();
  api(`/api/audio/${id}`, 'DELETE').catch(() => {});
};
const audioInfo = () => info(T.recording, T.needsAdvanced);
// A job's progress moves its ring only; the list is redrawn when the jobs themselves change.
function onJobs(jobs) {
  const same = jobs.length === data.jobs.length && jobs.every((j, i) => j.id === data.jobs[i].id && j.status === data.jobs[i].status);
  data.jobs = jobs;
  if (!same) return refresh();
  for (const j of jobs) document.querySelectorAll(`[data-ring="${j.id}"] .arc`).forEach(a => a.setAttribute('stroke-dashoffset', RING * (1 - j.progress)));
}
const stopJob = id => api(`/api/downloads/${id}`, 'DELETE').catch(() => {});

// One search box: it filters the library as you type, and the last row takes the same words online.
let libQ = '';
const matches = (p, q) => [p.title, p.piece, p.artist, p.performer, p.composer].some(x => (x || '').toLowerCase().includes(q));
function renderLibrary() {
  const q = libQ.trim().toLowerCase();
  const ready = [...data.recordings].sort(byNewest), recordings = audioItems();
  const shown = q ? ready.filter(p => matches(p, q)) : ready;
  const audio = recordings.filter(a => !q || a.name.toLowerCase().includes(q));
  $('#library-list').classList.toggle('selecting', state.selecting);
  $('#library-list').innerHTML = (q ? [] : jobPieces()).map(p => pieceRow(p)).join('')
    + audio.map(audioRow).join('') + shown.map(p => pieceRow(p)).join('');
  // When the library has nothing for the words: the same two ways as its +, the search with these words. (Another
  // version of a piece it has is found with the + itself.)
  $('#lib-online').innerHTML = !q || shown.length || audio.length ? ''
    : `<button class="online" data-how="upload">${ICON_UPLOAD}<span>${esc(T.upload)}</span></button>`
    + (data.features.transcribe ? `<button class="online" data-how="search">${ICON_FIND}<span>${esc(T.searchOnlineFor(libQ.trim()))}</span></button>` : '');
  $('#library-count').textContent = q ? (shown.length ? T.found(shown.length) : T.notFound)
    : [T.pieces(ready.length), totalTime(ready), data.jobs.length ? T.preparing(data.jobs.length) : '',
       recordings.length ? T.toTranscribe(recordings.length) : ''].filter(Boolean).join(' · ');
  renderSelectionBar();
}
$('#lib-q').addEventListener('input', e => { libQ = e.target.value; renderLibrary(); });
$('#lib-online').addEventListener('click', e => {
  const how = e.target.closest('[data-how]')?.dataset.how;
  if (!how) return;
  $('#lib-q').blur();
  openAdd(how, libQ.trim());
});

// Choosing several pieces, in 曲库: a check circle slides in on the left of each.
function setSelecting(on, firstId) {
  state.selecting = on;
  selected.clear();
  if (on && firstId != null) selected.add(firstId);
  app.classList.toggle('selecting', on);
  document.querySelectorAll('#library-list .swipe.open').forEach(r => r.classList.remove('open'));
  if (stack.some(e => e.view === 'library') || !herOn()) renderLibrary();
}
function renderSelectionBar() {
  const n = selected.size;
  $('#library-title').textContent = state.selecting ? (n ? T.selected(n) : T.choose) : T.library;
  const shown = listIds($('#library-list'));
  $('#sel-all').textContent = n && shown.every(id => selected.has(id)) ? T.none : T.all;
  $('#sel-delete').disabled = $('#sel-playlist').disabled = !n;
  $('#sel-delete').textContent = n ? T.deleteN(n) : T.delete;
}
function toggleSelect(id) {
  selected.has(id) ? selected.delete(id) : selected.add(id);
  document.querySelector(`#library-list [data-id="${id}"]`)?.classList.toggle('selected', selected.has(id));
  renderSelectionBar();
}
$('#sel-cancel').addEventListener('click', () => setSelecting(false));
$('#sel-playlist').addEventListener('click', () => {
  const ids = [...selected];
  setSelecting(false);
  pickPlaylist(ids);
});
$('#sel-all').addEventListener('click', () => {  // the pieces shown: with words in the search, only those
  const shown = listIds($('#library-list')), all = shown.length && shown.every(id => selected.has(id));
  selected.clear();
  if (!all) shown.forEach(id => selected.add(id));
  renderLibrary();
});
$('#sel-delete').addEventListener('click', () => openAction(T.trashN(selected.size), () => {
  const ids = [...selected];
  collapse(ids.map(id => document.querySelector(`#library-list [data-id="${id}"]`)).filter(Boolean), () => {
    deletePieces(ids);
    setSelecting(false);
  });
}));
function deletePieces(ids) {
  data.recordings = data.recordings.filter(r => !ids.includes(r.id));
  for (const id of ids) rolls.delete(id);  // its number may be given to the next new piece
  refresh();
  api('/api/recordings', 'DELETE', {ids}).catch(() => {});
}
function collapse(rows, after) {
  for (const row of rows) { row.style.height = row.offsetHeight + 'px'; row.offsetHeight; row.classList.add('removing'); }
  setTimeout(after, 270);
}

// ---------- Artists, and the list page for 收藏 / 最近播放 / one artist ----------
// A piano version has two people: whose piece it is (原作者: the singer of a song, the composer of a classical
// piece) and who plays it here (演奏者), whose picture is that of the channel the video came from.
let role = 'artist';
function people(by) {
  const map = new Map();
  for (const p of data.recordings) {
    const name = p[by];
    if (name) map.set(name, [...(map.get(name) || []), p]);
  }
  return map;
}
const pictureOf = (by, pieces) => by === 'performer' ? pieces.find(p => p.channel_avatar)?.channel_avatar : '';
const avatar = (name, picture, cls = '') => picture
  ? `<img class="mono${cls}" src="${esc(picture)}" alt="" referrerpolicy="no-referrer">`
  : `<span class="mono${cls}" style="--h:${hueOfName(name)}">${esc(initial(name))}</span>`;
function renderArtists() {
  for (const b of document.querySelectorAll('#role-seg button')) b.classList.toggle('on', b.dataset.role === role);
  const list = [...people(role)].sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0], 'zh'));
  $('#artist-list').innerHTML = list.map(([name, ps]) => `<button class="arow" data-group="${esc(name)}">
      ${avatar(name, pictureOf(role, ps))}
      <div class="text"><div class="t1">${esc(name)}</div><div class="t2">${esc(T.pieces(ps.length))}</div></div>${CHEV}
    </button>`).join('');
}
$('#artist-list').addEventListener('click', e => {
  const b = e.target.closest('[data-group]');
  if (b) push('group', {kind: role, name: b.dataset.group});
});
$('#role-seg').addEventListener('click', e => {
  const b = e.target.closest('[data-role]');
  if (!b || b.dataset.role === role) return;
  role = b.dataset.role;
  renderArtists();
  $('#view-artists').scrollTop = 0;
});
function groupPieces(g) {
  if (g.kind === 'favorites') return data.recordings.filter(p => p.favorited_at).sort((a, b) => b.favorited_at.localeCompare(a.favorited_at));
  if (g.kind === 'recent') return data.recordings.filter(p => p.last_played_at).sort((a, b) => b.last_played_at.localeCompare(a.last_played_at));
  return people(g.kind).get(g.name)?.sort(byNewest) || [];
}
function renderGroup(g) {
  if (g.kind === 'playlist') return renderPlaylist(g.id);
  $('#group-more').hidden = true;
  $('#group-foot').innerHTML = '';
  const ps = groupPieces(g);
  const name = g.kind === 'favorites' ? T.favorites : g.kind === 'recent' ? T.recent : g.name;
  $('#group-title').textContent = name;
  $('#group-hero').hidden = g.kind === 'recent';
  $('#group-hero').innerHTML = (g.kind === 'favorites' ? `<span class="mono square" style="--h:340">${HEART_FILL}</span>` : avatar(name, pictureOf(g.kind, ps)))
    + `<b>${esc(name)}</b><span>${esc([g.kind === 'artist' || g.kind === 'performer' ? T[g.kind] : '', T.pieces(ps.length), totalTime(ps)].filter(Boolean).join(' · '))}</span>`;
  const remove = {favorites: T.unfavorite, recent: T.forgetPlay}[g.kind];
  $('#group-list').innerHTML = ps.map(p => pieceRow(p, g.kind === 'recent' ? agoLabel(p.last_played_at) : '', remove)).join('')
    || `<p class="empty">${esc(g.kind === 'favorites' ? T.noFavorites : T.noPlays)}</p>`;
}
async function favorite(id, on) {
  const p = pieceById(id);
  if (p) p.favorited_at = on ? new Date().toISOString() : null;
  refresh();
  await api(`/api/recordings/${id}`, 'PATCH', {favorite: on}).catch(() => {});
}

// ---------- Playlists (歌单) ----------
// The user's own lists of pieces, each once, in their order, kept on the Mac. Not the list that plays (播放列表):
// playing one fills that list, and changing either leaves the other alone. Only pieces whose files are there show.
const playlistById = id => data.playlists.find(pl => pl.id === +id);
const playlistPieces = pl => (pl?.ids || []).map(pieceById).filter(Boolean);
function cover(pl) {
  const ps = playlistPieces(pl).slice(0, 4);
  return ps.length === 4 ? `<span class="cover four">${ps.map(p => `<i style="--h:${hueOf(p.id)}"></i>`).join('')}</span>`
    : `<span class="cover" style="--h:${ps.length ? hueOf(ps[0].id) : 220}">${ICON.playlist}</span>`;
}
const playlistText = pl => `<div class="text"><div class="t1">${esc(pl.name)}</div><div class="t2">${esc(T.pieces(playlistPieces(pl).length))}</div></div>`;
function renderPlaylists() {
  $('#playlist-list').innerHTML = data.playlists.map(pl => `<div class="swipe" data-id="${pl.id}">
      <button class="del" tabindex="-1">${esc(T.delete)}</button>
      <div class="content">${cover(pl)}${playlistText(pl)}${CHEV}</div>
    </div>`).join('');
}
function renderPlaylist(id) {
  if (reordering) return;
  const pl = playlistById(id), ps = playlistPieces(pl);
  $('#group-title').textContent = pl?.name || '';
  $('#group-more').hidden = $('#group-hero').hidden = !pl;
  if (!pl) { $('#group-list').innerHTML = $('#group-foot').innerHTML = ''; return; }
  $('#group-hero').innerHTML = `${cover(pl)}<b>${esc(pl.name)}</b><span>${esc([T.pieces(ps.length), totalTime(ps)].filter(Boolean).join(' · '))}</span>`
    + (ps.length ? `<div class="hero-acts"><button data-act="order">${ICON_PLAY}<span>${esc(T.play)}</span></button><button data-act="shuffle">${ICON.shuffle}<span>${esc(T.shufflePlay)}</span></button></div>` : '');
  $('#group-list').innerHTML = ps.map(p => pieceRow(p, '', T.removeFromPlaylist, true)).join('') || `<p class="empty">${esc(T.noPlaylistPieces)}</p>`;
  $('#group-foot').innerHTML = `<button class="online" id="group-add">${ICON.add}<span>${esc(T.addMusic)}</span></button>`;
}
// The Mac's answer to a change: the playlist as it is now (it may differ from what was shown at once).
async function changePlaylist(id, change) {
  const changed = await api(`/api/playlists/${id}`, 'PATCH', change).catch(() => null);
  if (changed) { data.playlists = data.playlists.map(pl => pl.id === changed.id ? changed : pl); refresh(); }
  return changed;
}
// A new playlist, named first: with pieces (it stays where it was), or empty (it opens, to add to).
function newPlaylist(ids = []) {
  askName(T.newPlaylistTitle, '', async name => {
    if (!name) return;
    const made = await api('/api/playlists', 'POST', {name, ids}).catch(() => null);
    if (!made) return;
    data.playlists = [made, ...data.playlists.filter(pl => pl.id !== made.id)];
    refresh();
    if (ids.length) hud(T.addedTo(made.name));
    else push('group', {kind: 'playlist', id: made.id});
  });
}
function addToPlaylist(pl, ids) {
  const fresh = ids.filter(id => !pl.ids.includes(id));  // each piece once in a playlist
  if (!fresh.length) return hud(T.alreadyIn(pl.name));
  pl.ids = [...pl.ids, ...fresh];
  refresh();
  hud(T.addedTo(pl.name));
  changePlaylist(pl.id, {add: fresh});
}
function removeFromPlaylist(pl, id) {
  if (!pl) return;
  pl.ids = pl.ids.filter(i => i !== id);
  refresh();
  changePlaylist(pl.id, {remove: id});
}
function deletePlaylist(id) {
  if (state.view === 'group' && state.session?.kind === 'playlist' && state.session.id === id) pop();
  data.playlists = data.playlists.filter(pl => pl.id !== id);
  refresh();
  api(`/api/playlists/${id}`, 'DELETE').catch(() => {});
}
const askDeletePlaylist = id => openAction(T.deletePlaylist, () => deletePlaylist(id));  // its pieces stay
function playlistMenu(pl, row, html) {
  showCtx(row, html, [
    [T.addMusic, ICON.add, () => openPick(pl.id)],
    [T.rename, ICON.edit, () => askName(T.renamePlaylist, pl.name, name => {
      if (name && name !== pl.name) changePlaylist(pl.id, {name});
    })],
    [T.deletePlaylist, ICON.trash, () => askDeletePlaylist(pl.id), true],
  ]);
}
// Which playlist pieces go into, as an action sheet: a new one first, then each.
let picking = [];
function pickPlaylist(ids) {
  picking = ids;
  $('#pick-list').innerHTML = `<button data-new-playlist>${esc(T.newPlaylist)}</button>`
    + data.playlists.map(pl => `<button data-playlist="${pl.id}">${esc(pl.name)}</button>`).join('');
  $('#dim').classList.add('show');
  $('#pick-playlist').classList.add('show');
}
$('#pick-list').addEventListener('click', e => {
  const b = e.target.closest('button');
  if (!b) return;
  const ids = picking;
  closeOverlays();
  if ('newPlaylist' in b.dataset) newPlaylist(ids);
  else if (playlistById(b.dataset.playlist)) addToPlaylist(playlistById(b.dataset.playlist), ids);
});
$('#pick-cancel').addEventListener('click', closeOverlays);
// 添加曲目: the library, searchable, a + by each piece (a ✓ once it is in the playlist).
let pickFor = null;
function openPick(id) {
  pickFor = +id;
  $('#pick-q').value = '';
  renderPick();
  showSheet($('#sheet-pick'));
  $('#pick-pieces').scrollTop = 0;
}
function renderPick() {
  const pl = playlistById(pickFor);
  if (!pl) return closeOverlays();
  const q = $('#pick-q').value.trim().toLowerCase();
  const ps = [...data.recordings].sort(byNewest).filter(p => !q || matches(p, q));
  $('#pick-pieces').innerHTML = ps.map(p => `<div class="prow">${pieceText(p)}
      <button class="addbtn" data-add-piece="${p.id}" aria-label="${esc(T.addToPlaylistShort)}">${pl.ids.includes(p.id) ? CHECK : ICON.add}</button>
    </div>`).join('') || `<p class="empty">${esc(T.notFound)}</p>`;
}
$('#pick-q').addEventListener('input', renderPick);
$('#pick-pieces').addEventListener('click', e => {
  const b = e.target.closest('[data-add-piece]'), pl = playlistById(pickFor);
  if (b && pl && !pl.ids.includes(+b.dataset.addPiece)) {
    pl.ids = [...pl.ids, +b.dataset.addPiece];
    refresh();
    changePlaylist(pl.id, {add: [+b.dataset.addPiece]});
  }
});
$('#pick-done').addEventListener('click', closeOverlays);
$('#new-playlist').addEventListener('click', () => newPlaylist());
$('#group-more').innerHTML = ICON.more;
$('#group-more').addEventListener('click', e => {
  const pl = state.session?.kind === 'playlist' && playlistById(state.session.id);
  if (pl) playlistMenu(pl, e.currentTarget, null);
});
$('#group-foot').addEventListener('click', e => {
  if (e.target.closest('#group-add') && state.session?.kind === 'playlist') openPick(state.session.id);
});
// 播放 plays it in its order (shuffle off); 随机播放, shuffled from a piece picked at random.
$('#group-hero').addEventListener('click', e => {
  const b = e.target.closest('[data-act]'), g = state.session;
  const ids = g?.kind === 'playlist' ? playlistPieces(playlistById(g.id)).map(p => p.id) : [];
  if (!b || !ids.length) return;
  const shuffle = b.dataset.act === 'shuffle';
  play(shuffle ? ids[Math.floor(Math.random() * ids.length)] : ids[0], ids, {kind: 'playlist', id: g.id}, b,
       shuffle ? 'shuffle' : 'order');
});
// A word that something was done, for a moment (added to a playlist).
let hudTimer = 0;
function hud(text) {
  $('#hud').textContent = text;
  $('#hud').classList.add('show');
  clearTimeout(hudTimer);
  hudTimer = setTimeout(() => $('#hud').classList.remove('show'), 1600);
}

// Long-press menus for a piece: what it can do here.
function pieceMenu(key, row) {
  const job = data.jobs.find(j => jobKey(j) === key);
  if (job) return showCtx(row, pieceText({...titleParts(job.title || audioName(job)), job}), [[T.stopDownload, ICON.trash, () => foldAway(row, () => stopJob(job.id)), true]]);
  const a = data.audio.find(x => audioKey(x) === key);
  if (a) return showCtx(row, row.querySelector('.text').outerHTML, [
    data.features.transcribe && a.status !== 'waiting' && [T.transcribe, ICON.sparkle, () => transcribeAudio(a.id)],
    [T.moveToTrash, ICON.trash, () => foldAway(row, () => trashAudio(a.id)), true],
  ]);
  const id = +key, p = pieceById(id);
  if (!p) return;
  // In 收藏 and 最近播放 a swipe only takes a piece out of the list; the menu says plainly where the file goes.
  showCtx(row, pieceText(p) + pieceSide(p), [
    [p.favorited_at ? T.unfavorite : T.favorite, ICON.heart, () => favorite(id, !p.favorited_at)],
    [T.addToPlaylist, ICON.playlist, () => pickPlaylist([id])],
    [T.edit, ICON.edit, () => openEdit(id)],
    state.view === 'library' && [T.selectSeveral, ICON.select, () => setSelecting(true, id)],
    [T.moveToTrash, ICON.trash, () => foldAway(row, () => deletePieces([id])), true],
  ]);
}
// The list may have been redrawn while the menu was open, so the row to fold away is looked up again.
const foldAway = (row, then) => {
  const found = document.querySelector(`.view:not([hidden]) .swipe[data-id="${row.dataset.id}"]`);
  collapse(found ? [found] : [], then);
};

// Edit one piece: its name, whose it is, who plays it.
let editId = null;
function openEdit(id) {
  const p = pieceById(id);
  if (!p) return;
  editId = id;
  $('#f-piece').value = p.piece;
  $('#f-artist').value = p.artist;
  $('#f-performer').value = p.performer;
  $('#f-source').textContent = originOf(p);
  $('#f-added').textContent = addedOf(p);
  $('#f-technical').innerHTML = technicalOf(p).map(([k, v]) =>
    `<div class="frow"><label>${esc(k)}</label><span class="val">${esc(v)}</span></div>`).join('');
  showSheet($('#sheet-edit'));
}
$('#sheet-edit .fold').addEventListener('click', () => showTechnical(!technicalOpen));
$('#edit-cancel').addEventListener('click', closeOverlays);
$('#edit-save').addEventListener('click', async () => {
  const piece = $('#f-piece').value.trim(), id = editId;
  const change = {piece, artist: $('#f-artist').value.trim(), performer: $('#f-performer').value.trim()};
  closeOverlays();
  if (!piece) return;
  const p = pieceById(id);
  if (p) Object.assign(p, change);
  refresh();
  const saved = await api(`/api/recordings/${id}`, 'PATCH', change).catch(() => null);
  if (saved) { data.recordings = data.recordings.map(r => r.id === id ? saved : r); refresh(); }
});

// ---------- The player ----------
// Tapping a piece or the mini player opens it, like a page; a right swipe takes it away.
function openPlayer() {
  if (nowPiece() && state.view !== 'player') push('player');
}
const MODES = ['order', 'one', 'shuffle'];
function renderNow() {
  const p = nowPiece(), mini = $('#mini');
  // The mini player is on every page, under the player when it is open; her home (the orb, the sidebar) has none,
  // since the player is one left swipe from the orb. The library as home has it, but not with the sidebar out.
  mini.hidden = !somethingPlaying() || (!stack.length && (herOn() || state.open)) || stack[0]?.view === 'player';
  pages.classList.toggle('has-mini', !mini.hidden);
  const mode = pb().mode || 'order';
  $('#p-mode').innerHTML = ICON[mode];
  $('#p-mode').setAttribute('aria-label', T.modes[mode]);
  $('#view-player').classList.toggle('no-piece', !p);
  renderVolume();
  if (!p) return;
  document.documentElement.style.setProperty('--h', hueOf(p.id));  // its color: the mini player's, the player's, the status bar's
  $('#mini-title').textContent = $('#p-title').textContent = p.piece;
  $('#mini-sub').textContent = $('#p-sub').textContent = subtitle(p);
  const infoKey = JSON.stringify(p);
  if (infoId !== infoKey) { infoId = infoKey; renderInfo(p); }
  const paused = pb().state !== 'playing';
  for (const b of [$('#mini-toggle'), $('#p-toggle')]) {
    b.innerHTML = paused ? ICON_PLAY : ICON_PAUSE;
    b.setAttribute('aria-label', paused ? T.play : T.pause);
  }
  $('#view-player').classList.toggle('paused-art', paused);
  $('#p-fav').innerHTML = p.favorited_at ? HEART_FILL : HEART_LINE;
  $('#p-fav').classList.toggle('on', !!p.favorited_at);
  $('#p-fav').setAttribute('aria-label', p.favorited_at ? T.unfavorite : T.favorite);
  renderProgress();
}
let scrubbing = null;  // the position the bar is being dragged to
let volume = null;  // the volume the volume bar is being moved to
function renderVolume() {
  const v = volume ?? pb().volume ?? 0;
  $('#p-vol .fill').style.width = v + '%';
  $('#p-vol').setAttribute('aria-valuenow', v);
}
function renderProgress() {
  const p = nowPiece();
  if (!p) return;
  const at = scrubbing ?? position(), length = p.duration_s || 1, f = Math.min(1, at / length);
  $('#p-fill').style.width = $('#mini-line').style.width = f * 100 + '%';
  $('#p-pos').textContent = mmss(at);
  $('#p-left').textContent = '-' + mmss(length - at);
  $('#scrub').setAttribute('aria-valuenow', Math.floor(at));
  document.querySelectorAll(`.card[data-piece="${p.id}"] .cfill`).forEach(el => { el.style.width = f * 100 + '%'; });
}

// The artwork is the piece itself: its notes, scrolling past as the piano plays them.
const rolls = new Map();
function notesFor(p) {
  if (!rolls.has(p.id)) {
    rolls.set(p.id, null);
    api(`/api/recordings/${p.id}/notes`).then(notes => {
      let lo = 60, hi = 72;
      for (const n of notes) { lo = Math.min(lo, n[2]); hi = Math.max(hi, n[2]); }
      rolls.set(p.id, {notes, lo: lo - 3, hi: hi + 3});
    }).catch(() => setTimeout(() => rolls.delete(p.id), 10000));  // asked again in a while, not every frame
  }
  return rolls.get(p.id);
}
function drawRoll(canvas, p, pos, small) {
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (!w || !h) return;
  const dpr = Math.min(devicePixelRatio || 1, 2);
  if (canvas.width !== Math.round(w * dpr)) { canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr); }
  const ctx = canvas.getContext('2d'), hue = hueOf(p.id), roll = notesFor(p);
  // In the piece's color: on its deepest shade, the notes sounding light up white; on its palest (the light look), the
  // notes waiting are a pale tint and those sounding light up in its full color, as a lit key does on a light screen.
  const light = document.documentElement.dataset.theme === 'light';
  const ink = light ? {ground: `hsl(${hue} 60% 96%)`, line: 'rgba(0, 0, 0, .05)', on: `hsl(${hue} 95% 60%)`,
                       glow: `hsl(${hue} 100% 62% / .75)`, past: `hsl(${hue} 50% 70% / .3)`, note: v => `hsl(${hue} 70% 74% / ${.55 + v / 127 * .4})`,
                       head: 'rgba(0, 0, 0, .3)'}
                    : {ground: `hsl(${hue} 35% 8%)`, line: 'rgba(255, 255, 255, .05)', on: '#fff', glow: `hsl(${hue} 90% 75%)`,
                       past: `hsl(${hue} 70% 70% / .3)`, note: v => `hsl(${hue} 85% 68% / ${.5 + v / 127 * .45})`,
                       head: 'rgba(255, 255, 255, .45)'};
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = ink.ground;
  ctx.fillRect(0, 0, w, h);
  if (!roll) return;
  const span = small ? 5 : 8, px = w / span, head = w * .32, {lo, hi} = roll, row = h / (hi - lo);
  ctx.fillStyle = ink.line;
  for (let m = Math.ceil(lo / 12) * 12; m <= hi; m += 12) ctx.fillRect(0, h - (m - lo) * row, w, 1);  // one line per C
  for (const [t, d, m, v] of roll.notes) {
    const x = head + (t - pos) * px, nw = Math.max(2, d * px - 1.5);
    if (x > w) break;
    if (x + nw < 0) continue;
    const on = t <= pos && pos < t + d;
    ctx.shadowBlur = on ? 14 : 0;
    ctx.shadowColor = ink.glow;
    ctx.fillStyle = on ? ink.on : t + d < pos ? ink.past : ink.note(v);
    ctx.beginPath();
    ctx.roundRect(x, h - (m - lo + 1) * row, nw, Math.max(2, row - 1), 2);
    ctx.fill();
  }
  ctx.shadowBlur = 0;
  if (!small) { ctx.fillStyle = ink.head; ctx.fillRect(head, 0, 1, h); }
}
// The clock between the Mac's own reports: the bar, the times and the notes move with it.
let lastDraw = 0;
function playClock(t) {
  requestAnimationFrame(playClock);
  if (t - lastDraw < 33 || document.hidden) return;
  lastDraw = t;
  const p = nowPiece();
  if (!p) return;
  renderProgress();
  if (!$('#view-player').hidden) drawRoll($('#roll'), p, scrubbing ?? position(), false);
  if (!$('#mini').hidden) drawRoll($('#mini .mart'), p, position(), true);
}

// The player and 详细信息 are two screens, one above the other. A swipe up or down anywhere, buttons included,
// moves between them: the screens follow the finger and snap into place. Sideways is left to going back.
let playerScreen = 0;  // the player (0) or 详细信息 (1)
function showScreen(n) {
  playerScreen = n;
  const el = $('#p-screens');
  el.classList.remove('dragging');
  el.style.setProperty('--screen', n);
  el.style.setProperty('--dy', '0px');
}
(function screenGestures() {
  const view = $('#view-player'), el = $('#p-screens');
  let g = null;
  // Not from the bottom edge, where a swipe up is the system's (the home indicator: going home, another app).
  const fromEdge = e => e.clientY > innerHeight - parseFloat(getComputedStyle(app).paddingBottom) - 16;
  view.addEventListener('pointerdown', e => {
    if (!e.button && !e.target.closest('.scrub') && !fromEdge(e)) {
      g = {id: e.pointerId, x0: e.clientX, y0: e.clientY, t: performance.now(), dy: 0, on: false};
    }
  });
  view.addEventListener('pointermove', e => {
    if (!g || e.pointerId !== g.id) return;
    const dx = e.clientX - g.x0, dy = e.clientY - g.y0;
    if (!g.on) {
      if (Math.abs(dx) > 10 && Math.abs(dx) > Math.abs(dy)) { g = null; return; }
      if (Math.abs(dy) < 10) return;
      g.on = true;
      view.setPointerCapture(e.pointerId);
      el.classList.add('dragging');
    }
    g.dy = (playerScreen === 0 ? dy > 0 : dy < 0) ? dy * .3 : dy;  // past either end it resists, like the edge of a scroll view
    el.style.setProperty('--dy', g.dy + 'px');
  });
  // Let go: to the screen it was swiped towards. Taken over by the system (pointercancel): back where it was.
  const end = e => {
    if (!g || e.pointerId !== g.id) return;
    if (g.on) {
      const far = performance.now() - g.t < 300 ? 30 : 110;
      showScreen(e.type === 'pointercancel' ? playerScreen : g.dy < -far ? 1 : g.dy > far ? 0 : playerScreen);
      guardUntil = performance.now() + 250;
    }
    g = null;
  };
  view.addEventListener('pointerup', end);
  view.addEventListener('pointercancel', end);
})();

// 详细信息: the video as YouTube shows it (when the piece came from one), then the library's facts about the piece,
// always the same four in the same places, then the technical details, folded at first. 编辑信息 has the same groups
// in the same order.
let infoId = null, technicalOpen = false;
// How a piece came: made by PianaSoul from a video or from a recording of yours, sent from a phone, or a file of yours
// in the library folder (where its file is: Transcribed/, Uploaded/ or anywhere else).
const originOf = p => p.origin === 'transcribed'
  ? (/^https?:/.test(p.source_url || '') ? T.origins.video(siteOf(p.source_url)) : T.origins.recording)
  : T.origins[p.origin] || T.origins.yours;
const dateOf = stamp => { const d = new Date(stamp); return T.date(d.getFullYear(), d.getMonth() + 1, d.getDate()); };
const addedOf = p => p.added_at ? dateOf(p.added_at) : '—';
const aboutOf = p => [
  [T.artist, p.artist || '—'], [T.performer, p.performer || '—'], [T.source, originOf(p)], [T.added, addedOf(p)],
];
const technicalOf = p => [
  [T.notes, p.notes != null ? T.noteCount(p.notes) : '—'], [T.pedal, p.pedal_presses != null ? T.times(p.pedal_presses) : '—'],
  [T.velocity, p.velocity_min != null ? `${p.velocity_min}–${p.velocity_max}` : '—'], [T.length, mmss(p.duration_s)],
];
// Opening or folding the technical details does it in both places.
function showTechnical(open) {
  technicalOpen = open;
  for (const fold of document.querySelectorAll('.fold')) {
    fold.setAttribute('aria-expanded', open);
    fold.nextElementSibling.hidden = !open;
  }
}
function renderInfo(p) {
  const facts = (list, hidden = false) => `<div class="facts"${hidden ? ' hidden' : ''}>${
    list.map(([k, v]) => `<div><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join('')}</div>`;
  const seen = [p.channel, p.views != null ? T.views(p.views) : '', p.published ? dateOf(p.published + 'T12:00') : ''].filter(Boolean);
  const video = p.video_title && /^https?:/.test(p.source_url || '') ? `
    <a class="video" id="p-video" href="${esc(p.source_url)}" target="_blank" rel="noopener" draggable="false">
      <span class="thumb" style="--h:${hueOf(p.id)}">${p.thumbnail ? `<img src="${esc(p.thumbnail)}" alt="" referrerpolicy="no-referrer">` : ''}
        <span class="vplay">${ICON_PLAY}</span><span class="dur">${mmss(p.duration_s)}</span></span>
      <span class="vt">${esc(p.video_title)}</span>
      <span class="vs">${esc([siteOf(p.source_url), ...seen].join(' · '))}</span>
    </a>` : '';
  $('#p-info').innerHTML = `<h3>${esc(T.details)}</h3>${video}
    <div class="facts-h${video ? '' : ' first'}">${esc(T.pieceInfo)}</div>${facts(aboutOf(p))}
    <button class="facts-h fold" aria-expanded="${technicalOpen}">${esc(T.technical)}${CHEV}</button>${facts(technicalOf(p), !technicalOpen)}`;
}
// Watching the original on YouTube: one sound at a time, so the piano pauses.
$('#p-info').addEventListener('click', e => {
  if (e.target.closest('#p-video')) pausePiano();
  if (e.target.closest('.fold')) showTechnical(!technicalOpen);
});
// Dragging the bar moves the playing position; the piano continues from there.
(function scrubber() {
  const el = $('#scrub');
  const at = e => {
    const r = el.getBoundingClientRect();
    return Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * (nowPiece()?.duration_s || 0);
  };
  el.addEventListener('pointerdown', e => {
    if (!nowPiece()) return;
    el.classList.add('active');
    el.setPointerCapture(e.pointerId);
    scrubbing = at(e);
    renderProgress();
  });
  el.addEventListener('pointermove', e => { if (scrubbing !== null) { scrubbing = at(e); renderProgress(); } });
  const end = () => {
    if (scrubbing === null) return;
    const to = scrubbing;
    el.classList.remove('active');
    control('seek', to).finally(() => { scrubbing = null; });
  };
  el.addEventListener('pointerup', end);
  el.addEventListener('pointercancel', end);
})();
// The piano's volume. It moves with the finger from wherever it is touched, so a tap never makes it jump, and the
// piano follows as it moves: one change on its way at a time, always the latest.
(function volumeSlider() {
  const el = $('#p-vol');
  let from = null, sent = null, sending = false;
  async function send() {
    if (sending) return;
    sending = true;
    while (volume !== null && volume !== sent) { sent = volume; await control('volume', sent); }
    sending = false;
    if (!from) { volume = null; renderVolume(); }  // let go: as the Mac has it from now on
  }
  el.addEventListener('pointerdown', e => {
    el.classList.add('active');
    el.setPointerCapture(e.pointerId);
    from = {x: e.clientX, v: volume ?? pb().volume ?? 0, width: el.getBoundingClientRect().width};
    sent = null;  // the Mac's may have changed since (she was asked): the first move always goes
  });
  el.addEventListener('pointermove', e => {
    if (!from) return;
    volume = Math.round(Math.max(0, Math.min(100, from.v + (e.clientX - from.x) / from.width * 100)));
    renderVolume();
    send();
  });
  const end = () => {
    if (!from) return;
    from = null;
    el.classList.remove('active');
    if (!sending) { volume = null; renderVolume(); }
  };
  el.addEventListener('pointerup', end);
  el.addEventListener('pointercancel', end);
})();
$('#p-toggle').addEventListener('click', e => control('toggle', null, null, e.currentTarget));
$('#p-fav').addEventListener('click', () => { const p = nowPiece(); if (p) favorite(p.id, !p.favorited_at); });
$('#p-prev').addEventListener('click', e => control('prev', null, null, e.currentTarget));
$('#p-next').addEventListener('click', e => control('next', null, null, e.currentTarget));
$('#p-mode').addEventListener('click', () => control('mode', MODES[(MODES.indexOf(pb().mode) + 1) % MODES.length]));
$('#p-queue').addEventListener('click', openQueue);
$('#p-queue').innerHTML = ICON.queue;
$('#p-share').innerHTML = ICON.share;
$('#p-close').innerHTML = ICON.close;
// ✕, once confirmed: the piece stops for good (played again, it starts over) and the player closes onto the page it
// covered; with nothing playing, that page has no mini player.
$('#p-close').addEventListener('click', () => openAction(T.stop, () => {
  data.playback = {...pb(), state: 'stopped', position: 0};
  pop();
  control('stop');
}));
// Sharing hands the piece's .mid file to the phone's share sheet (AirDrop, Messages, Files, GarageBand…).
$('#p-share').addEventListener('click', async e => {
  const p = nowPiece(), button = e.currentTarget;
  if (!p) return;
  const name = p.title.replace(/[<>:"/\\|?*\x00-\x1f\x7f]/g, '_') + '.mid';  // as the library names its files
  try {
    const blob = await (await fetch(`/api/recordings/${p.id}/midi`)).blob();
    await navigator.share({files: [new File([blob], name, {type: 'audio/midi'})], title: p.piece});
  } catch (error) { if (error?.name !== 'AbortError') shake(button); }
});
$('#mini').addEventListener('click', openPlayer);
$('#mini-toggle').addEventListener('click', e => { e.stopPropagation(); control('toggle', null, null, e.currentTarget); });

// ---------- 播放列表 ----------
let reordering = false;
function openQueue() {
  renderQueue();
  showSheet($('#sheet-queue'));
  $('#queue-list').scrollTop = 0;
}
function renderQueue() {
  if (reordering) return;
  const ids = pb().ids.filter(id => pieceById(id));
  $('#queue-sub').textContent = T.from(scopeName(pb().scope || {}), ids.length);
  $('#queue-list').innerHTML = ids.map(id => {
    const p = pieceById(id), now = pb().id === id;
    return `<div class="swipe${now ? ' fixed now' : ''}" data-id="${id}">
        ${now ? '' : `<button class="del" tabindex="-1">${esc(T.remove)}</button>`}
        <div class="content">${pieceText(p)}${pieceSide(p)}<span class="grip" aria-label="${esc(T.drag)}">${ICON.grip}</span></div>
      </div>`;
  }).join('');
}
// Hold a piece by its handle and drag it up or down; the pieces it passes make room. moved(id, index), let go.
function dragToReorder(list, moved) {
  let r = null;
  list.addEventListener('pointerdown', e => {
    const grip = e.target.closest('.grip');
    if (!grip || e.button > 0) return;
    const row = grip.closest('.swipe');
    const i = [...list.children].indexOf(row);
    r = {id: e.pointerId, row, y0: e.clientY, i0: i, i, h: row.offsetHeight};
    list.setPointerCapture(e.pointerId);
    row.classList.add('lifted');
    reordering = true;
  });
  list.addEventListener('pointermove', e => {
    if (!r || e.pointerId !== r.id) return;
    const dy = e.clientY - r.y0;
    const to = Math.max(0, Math.min(list.children.length - 1, r.i0 + Math.round(dy / r.h)));
    for (; r.i < to; r.i++) r.row.before(r.row.nextElementSibling);
    for (; r.i > to; r.i--) r.row.after(r.row.previousElementSibling);
    r.row.style.transform = `translateY(${dy - (r.i - r.i0) * r.h}px)`;
  });
  const end = e => {
    if (!r || e.pointerId !== r.id) return;
    r.row.style.transform = '';
    r.row.classList.remove('lifted');
    const id = +r.row.dataset.id, changed = r.i !== r.i0, index = r.i;
    reordering = false;
    guardUntil = performance.now() + 200;
    r = null;
    if (changed) moved(id, index);
  };
  list.addEventListener('pointerup', end);
  list.addEventListener('pointercancel', end);
}
dragToReorder($('#queue-list'), (id, index) => control('move', id, index));

// ---------- Adding pieces ----------
// Search YouTube for piano versions (or paste a link), watch one on YouTube, download it (the same queue she uses by
// voice), then play it: download, the ring while it is made, play.
let cands = [], query = '', searching = 0;
async function findCands(q) {
  query = q;
  cands = [];
  const n = ++searching;  // first: searching shows the spinner, not "nothing found"
  renderCands();
  try {
    const found = await api('/api/youtube?q=' + encodeURIComponent(q));
    if (n === searching) cands = found;
  } catch {}
  if (n === searching) { searching = 0; renderCands(); }
}
const jobFor = c => data.jobs.find(j => j.source === c.url);
function renderCands() {
  const body = $('#add-body');
  if (!query) { body.innerHTML = ''; return; }
  if (searching) { body.innerHTML = `<div class="loading">${SPINNER}</div>`; return; }
  if (!cands.length) { body.innerHTML = `<div class="empty">${esc(T.nothing)}</div>`; return; }
  body.innerHTML = cands.map((c, i) => {
    const rec = data.recordings.find(r => r.source_url === c.url), job = jobFor(c);
    const btn = rec ? `<button class="addbtn" data-play="${rec.id}" aria-label="${esc(T.playNow)}">${ICON_PLAY_CIRCLE}</button>`
              : job ? ringButton(job)
              : `<button class="addbtn" data-add="${i}" aria-label="${esc(T.download)}">${ICON_DOWNLOAD}</button>`;
    const seen = [c.channel, c.views != null ? T.views(c.views) : ''].filter(Boolean).join(' · ');
    return `<div class="cand">
        <a href="${esc(c.url)}" target="_blank" rel="noopener">
          <span class="thumb" style="--h:${hueOfName(c.url)}">${c.thumbnail ? `<img src="${esc(c.thumbnail)}" alt="" referrerpolicy="no-referrer">` : ''}<span class="dur">${mmss(c.duration_s)}</span></span>
          <span class="ctext"><span class="ct1">${esc(c.video_title)}</span><span class="ct2">${esc(seen)}</span></span>
        </a>
        ${btn}
      </div>`;
  }).join('');
}
// The library's +: upload files or search online, chosen first; without transcription only uploading is there, so
// straight to it.
function addPieces() {
  if (!data.features.transcribe) return openAdd('upload');
  $('#dim').classList.add('show');
  $('#add-how').classList.add('show');
}
$('#add-upload').addEventListener('click', () => { closeOverlays(); openAdd('upload'); });
$('#add-search').addEventListener('click', () => { closeOverlays(); openAdd('search'); });
$('#add-cancel').addEventListener('click', closeOverlays);
// One way of adding at a time: uploading (the files are chosen at once, in the same tap) or searching online, from
// the + or from the library's rows below a search, with its words already searched.
function openAdd(how, q = '') {
  const search = how === 'search';
  $('#add-title').textContent = search ? T.searchOnline : T.upload;
  $('#add-field').hidden = $('#add-body').hidden = !search;
  $('#sheet-add .uploads').hidden = search;
  $('#add-input').value = q;
  uploads.tidy();
  if (search && q) searchOnline(q);
  else { query = ''; renderCands(); }
  showSheet($('#sheet-add'));
  $('#add-scroll').scrollTop = 0;
  if (!search) $('#upload-files').click();
}
$('#add-done').addEventListener('click', closeOverlays);
$('#add-input').addEventListener('keydown', e => {
  if (!isEnter(e)) return;
  const q = e.target.value.trim();
  if (!q) return;
  e.target.blur();
  searchOnline(q);
});
// Searching online: by name with the Mac's YouTube key, a video link always; without the key, why not.
function searchOnline(q) {
  $('#add-scroll').scrollTop = 0;
  if (data.features.search || /^https?:/i.test(q)) return findCands(q);
  query = '';
  $('#add-body').innerHTML = `<div class="empty">${esc(T.searchOff)}</div>`;
}
$('#lib-add').innerHTML = ICON_ADD;
$('#lib-add').addEventListener('click', addPieces);

// Files from the phone go into the library folder on the Mac, one at a time, each with how far it has got; the Mac
// then tells the library (an event), and the piece is there.
const uploads = uploader(renderUploads);
$('#upload-pick').insertAdjacentHTML('afterbegin', ICON_UPLOAD);
$('#upload-pick').addEventListener('click', () => $('#upload-files').click());
$('#upload-files').addEventListener('change', e => {
  uploads.add([...e.target.files]);
  e.target.value = '';  // the same files may be chosen again
});
function renderUploads(items) {
  $('#upload-list').innerHTML = items.map(i => {
    const pct = Math.round(100 * i.sent / (i.size || 1));
    const said = i.status === 'waiting' ? T.uploadWaiting : i.status === 'sending' ? T.uploading(pct)
      : i.status === 'done' ? T.uploaded : T.uploadFailed[i.error] || T.uploadFailed.other;
    const side = i.status === 'done' ? `<span class="uok">${ICON.check}</span>`
      : i.status === 'failed' ? `<span class="ubad">${ICON.close}</span>`
      : `<svg viewBox="0 0 28 28" class="ring${i.status === 'waiting' ? ' waiting' : ''}"><g class="dial"><circle cx="14" cy="14" r="11.5" class="trk"/>
          <circle cx="14" cy="14" r="11.5" class="arc" stroke-dasharray="${i.status === 'waiting' ? '18 100' : RING}" stroke-dashoffset="${i.status === 'waiting' ? 0 : RING * (1 - pct / 100)}"/></g></svg>`;
    return `<div class="urow${i.status === 'failed' ? ' bad' : ''}"><div class="text"><div class="t1">${esc(i.name)}</div>
        <div class="t2">${esc(said)}</div></div>${side}</div>`;
  }).join('');
}
$('#add-body').addEventListener('click', e => {
  const add = e.target.closest('[data-add]');
  if (add) {
    const c = cands[+add.dataset.add];
    add.outerHTML = SPINNER;
    api('/api/downloads', 'POST', {url: c.url, query: /^https?:/i.test(query) ? '' : query, video: c}).catch(() => renderCands());
    return;
  }
  const stop = e.target.closest('[data-stop]');
  if (stop) { stopJob(+stop.dataset.stop); return; }
  // A piece made: it plays from the library, in the player; closing the player is back on the library.
  const made = e.target.closest('[data-play]');
  if (made) {
    closeOverlays();
    play(+made.dataset.play, listIds($('#library-list')), {kind: 'library'}, made);
    return;
  }
  if (e.target.closest('.cand > a')) pausePiano();  // the video plays on YouTube, the piano waits
});

// ---------- Talking in a conversation ----------
// As in iMessage: while you are at the latest message the conversation stays there, whatever grows or shrinks (a new
// message, her typing, the field getting taller, the mini player coming or going); scrolled up, it leaves you where you
// are. Opening a conversation, sending, and tapping the field take you to the latest message.
let atLatest = true, pinnedAt = -1;
const chatScroll = $('#session-scroll');
const pin = () => { chatScroll.scrollTop = chatScroll.scrollHeight; pinnedAt = chatScroll.scrollTop; };
const toLatest = () => { atLatest = true; pin(); };
// Only your own scrolling decides whether you are at the latest message (the page's own pinning arrives as scroll
// events too, a frame late, when the space may already have changed again).
chatScroll.addEventListener('scroll', () => {
  if (chatScroll.scrollTop === pinnedAt) return;
  atLatest = chatScroll.scrollTop + chatScroll.clientHeight >= chatScroll.scrollHeight - 4;
});
const keepLatest = new ResizeObserver(() => { if (atLatest) pin(); });
keepLatest.observe(chatScroll);
keepLatest.observe($('#session-chat'));
// The field rides up on the phone's keyboard, as in iMessage, and the mini player sinks behind it. The page itself
// never moves: Safari would scroll it up to show a field the keyboard is coming over, and again whenever the keyboard
// changes height, at the same moment as the field rises on its own. So the field takes you in itself, without that
// scroll (a tap on it goes into it with preventScroll), and while you are in it the page is kept where it is.
// The field is up while you are in it and the phone shows its keyboard (the visible area shrinks by it), and down
// otherwise. The phone tells of the keyboard only once it is up, so the tap starts the field up with it, on its curve
// and by the height the keyboard last had (kept for the next time the app opens); told of no keyboard within a second
// (a hardware keyboard), the field goes back down; without a touch screen (a computer) it waits for none. The field's
// buttons (and the dictation bar) leave the keyboard as it is; tapping the conversation puts it away.
const vv = window.visualViewport, input = $('#compose-input');
let keyboard = 336, coming = null;  // the keyboard's height as last told; after a tap on the field, until it is told
try { keyboard = Number(localStorage.getItem('keyboard')) || keyboard; } catch {}
function fitKeyboard(typing = document.activeElement === input) {
  const height = vv ? Math.round(innerHeight - vv.height) : 0;
  if (height > 80) {
    if (height !== keyboard) try { localStorage.setItem('keyboard', height); } catch {}
    keyboard = height;
    clearTimeout(coming);
    coming = null;
  }
  const up = typing && (height > 80 || coming !== null);
  app.style.setProperty('--kb', keyboard + 'px');
  if (up !== pages.classList.contains('kb-up')) {
    pages.classList.toggle('kb-up', up);
    if (up) toLatest();  // from the latest message, the conversation then rides up with the field
  }
  if (typing && (scrollY || vv?.offsetTop)) scrollTo(0, 0);
}
vv?.addEventListener('resize', () => fitKeyboard());
vv?.addEventListener('scroll', () => fitKeyboard());
document.addEventListener('visibilitychange', () => fitKeyboard());
// A press in the field never moves you in or out of it by itself: on its buttons you stay where you were, and on the
// words themselves, once you are in, the cursor moves as usual.
$('#compose .cfield').addEventListener('pointerdown', e => {
  if (e.target !== input || document.activeElement !== input) e.preventDefault();
});
input.addEventListener('click', () => {
  if (document.activeElement !== input) input.focus({preventScroll: true});
  clearTimeout(coming);
  if (navigator.maxTouchPoints) coming = setTimeout(() => { coming = null; fitKeyboard(); }, 1000);
  fitKeyboard();
});
input.addEventListener('blur', () => { clearTimeout(coming); coming = null; fitKeyboard(false); });
chatScroll.addEventListener('pointerdown', () => input.blur());

// Typing to her: she answers in text and does not speak, and the piano plays what she plays. In an earlier
// conversation the first message picks it back up, and you stay here.
function sendText() {
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  fitInput();
  const id = state.session === 'now' ? null : state.session;
  api('/api/messages', 'POST', {text, conversation: id}).catch(() => { input.value = text; fitInput(); });
  toLatest();
}
// The field grows with what you write, up to five lines; ↑ comes in beside the mic once there is something to send.
function fitInput() {
  input.style.height = '';
  input.style.height = Math.min(120, input.scrollHeight) + 'px';
  $('#compose-send').hidden = !input.value.trim();
}
input.addEventListener('input', fitInput);
input.addEventListener('keydown', e => {
  if (isEnter(e) && !e.shiftKey) { e.preventDefault(); sendText(); }
});
$('#compose-send').addEventListener('click', sendText);

// 听写, as in ChatGPT: while it listens, your voice comes in from the right along a dotted line; ✓ turns it into
// words at the cursor in the field (after what you wrote, usually), to look over and send; ✕ drops it. The keyboard
// stays as it was throughout. The phone records; the Mac writes.
const FRAME_MS = 80, PITCH = 5, BAR = 2.5;  // a bar for each frame the phone records (capture.js), in CSS pixels
let dictation = null;
async function startDictation() {
  const d = dictation = {chunks: [], levels: []};
  $('#compose').classList.add('dictating');
  drawWave(d);
  try {
    d.stream = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true}});
    d.ctx = new AudioContext();
    await d.ctx.audioWorklet.addModule('/capture.js');
    if (dictation !== d) return release(d);
    d.node = new AudioWorkletNode(d.ctx, 'phone-capture');
    d.node.port.onmessage = ({data: {pcm, level}}) => { d.chunks.push(new Int16Array(pcm)); d.levels.push(level); };
    d.ctx.createMediaStreamSource(d.stream).connect(d.node);
    const silent = d.ctx.createGain();
    silent.gain.value = 0;
    d.node.connect(silent).connect(d.ctx.destination);
    d.node.port.postMessage(true);
  } catch { if (dictation === d) endDictation(); }
}
// Every frame the line moves on by the time gone; the newest bar comes in at the right as a dot and grows to its
// height in the step it takes to move one place. The line never runs ahead of what has been heard.
function drawWave(d) {
  const canvas = $('#wave'), g = canvas.getContext('2d'), color = getComputedStyle(canvas).color;
  let scroll = -1, last = performance.now();
  const frame = now => {
    if (dictation !== d || d.abort) return;
    requestAnimationFrame(frame);
    const ratio = devicePixelRatio || 1, w = canvas.clientWidth, h = canvas.clientHeight, n = d.levels.length;
    if (canvas.width !== Math.round(w * ratio)) { canvas.width = Math.round(w * ratio); canvas.height = Math.round(h * ratio); }
    scroll = Math.min(n - 1, Math.max(n - 2, scroll + (now - last) / FRAME_MS));
    last = now;
    g.setTransform(ratio, 0, 0, ratio, 0, 0);
    g.clearRect(0, 0, w, h);
    g.fillStyle = color;
    g.beginPath();
    for (let i = n - 1, x; (x = w - BAR - (scroll + 1 - i) * PITCH) > -BAR; i--) {
      const height = BAR + (h * .68 - BAR) * loudness(d.levels[i] ?? 0) * Math.min(1, scroll + 1 - i);
      g.roundRect(x, (h - height) / 2, BAR, height, BAR / 2);
    }
    g.fill();
  };
  requestAnimationFrame(frame);
}
// A frame's level (RMS) on a decibel scale, 0 to 1: speech stands up, the quiet between words stays a dot.
const loudness = rms => Math.max(0, Math.min(1, (20 * Math.log10(rms + 1e-9) + 60) / 50)) ** 1.4;
function release(d) {
  d.stream?.getTracks().forEach(t => t.stop());
  d.ctx?.close().catch(() => {});
  d.stream = d.ctx = null;
}
function endDictation() {
  const d = dictation;
  if (!d) return;
  dictation = null;
  release(d);
  d.abort?.abort();
  $('#compose').classList.remove('dictating', 'transcribing');
}
async function transcribe() {
  const d = dictation;
  if (!d || d.abort) return;
  release(d);
  if (!d.chunks.length) return endDictation();
  d.abort = new AbortController();
  $('#compose').classList.add('transcribing');
  const audio = new Int16Array(d.chunks.reduce((n, c) => n + c.length, 0));
  let at = 0, text = '';
  for (const c of d.chunks) { audio.set(c, at); at += c.length; }
  try { ({text} = await (await fetch('/api/dictation', {method: 'POST', body: audio.buffer, signal: d.abort.signal})).json()); } catch {}
  if (dictation !== d) return;  // dropped with ✕, or you left the conversation
  endDictation();
  if (text) insertText(text);
}
// Between words of a language that spaces them, a space; none in Chinese or Japanese.
const CJK = /[\u2E80-\u9FFF\u3000-\u303F\uAC00-\uD7AF\uFF00-\uFFEF]/;
// The cursor is placed after the words only in a field you are typing in: on the iPhone, placing it in a field you
// are not in takes you into it, without its keyboard.
function insertText(text) {
  const start = input.selectionStart, before = input.value.slice(0, start);
  const space = before && !/\s$/.test(before) && !CJK.test(before.at(-1)) && !CJK.test(text[0]) ? ' ' : '';
  input.value = before + space + text + input.value.slice(input.selectionEnd);
  if (document.activeElement === input) input.selectionStart = input.selectionEnd = start + space.length + text.length;
  fitInput();
}
$('#compose-mic').addEventListener('click', startDictation);
$('#dictate-cancel').addEventListener('click', endDictation);
$('#dictate-done').addEventListener('click', transcribe);
$('#compose-mic').innerHTML = ICON.mic;
$('#compose-send').innerHTML = ICON.up;
$('#dictate-done').innerHTML = ICON.check + SPINNER;
$('#dictate-cancel').innerHTML = ICON.close;

// 进入语音对话: at the end of a conversation's text, keep pulling up and let go to talk in it: an earlier one is
// picked back up, the current one simply returns to the orb. The orb in the ring is the one at home as you left it,
// in color or gray, coming through as you pull. Let go, and it flies to the middle of the screen and grows into the
// orb while everything around it goes dark, coming on (if it was gray) as it flies; in the dark the screen
// underneath becomes home, and the dark lifts. It is the same orb all the way (one shader on one clock).
let flying = false;
const pickUp = id => { if (id !== 'now') api(`/api/conversations/${id}/pick-up`, 'POST').catch(() => {}); };
function talkIn(id) {
  pickUp(id);
  show('orb');
  voice.on();
}
function enterVoice(id) {
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) return talkIn(id);
  // It lands where the orb at home will be once home shows, in the middle of the screen (the orb at home may be off
  // to the side now, with the sidebar out).
  const r = $('#pull .porb').getBoundingClientRect(), size = $('#orb').offsetWidth;
  const home = {left: (app.clientWidth - size) / 2, top: (app.clientHeight - size) / 2, width: size, height: size};
  const dark = $('#to-voice'), fly = $('#fly-orb');
  Object.assign(fly.style, {left: home.left + 'px', top: home.top + 'px', width: home.width + 'px', height: home.height + 'px'});
  dark.hidden = fly.hidden = false;
  flying = true;
  sameLook(fly, $('#pull .porb'));
  const dx = r.left + r.width / 2 - (home.left + home.width / 2), dy = r.top + r.height / 2 - (home.top + home.height / 2);
  fly.animate([{transform: `translate(${dx}px, ${dy}px) scale(${r.width / home.width})`}, {transform: 'none'}],
    {duration: 560, easing: 'cubic-bezier(.32, .72, 0, 1)'});
  voice.on();  // inside the gesture, as a tap on the orb would be
  dark.animate([{opacity: 0}, {opacity: 1}], {duration: 200, fill: 'forwards'}).finished.then(() => {
    app.classList.add('instant');
    pickUp(id);
    show('orb');
    app.offsetWidth;
    app.classList.remove('instant');
  });
  // Landed, it breathes with the orb at home; the dark lifts around it, and then the orb at home, the same picture
  // beneath it, simply takes its place.
  setTimeout(() => {
    dark.animate([{opacity: 1}, {opacity: 0}], {duration: 280, fill: 'forwards'}).finished.then(() => {
      for (const el of [dark, fly]) { el.hidden = true; el.getAnimations().forEach(a => a.cancel()); }
      flying = false;
    });
  }, 560);
}
(function pullToTalk() {
  const view = $('#session-scroll'), chat = $('#session-chat'), hint = $('#pull');
  const ARM = 80;
  let y0 = null, pull = 0;
  const atEnd = () => view.scrollTop + view.clientHeight >= view.scrollHeight - 2;
  const set = (px, settle) => {
    pull = px;
    chat.style.transition = settle ? 'transform .3s var(--ease)' : 'none';
    chat.style.transform = px ? `translateY(${-px}px)` : '';
    hint.style.setProperty('--pull', Math.min(1, px / ARM));
    hint.classList.toggle('armed', px >= ARM);
  };
  const begin = (y, target) => { y0 = atEnd() && state.view === 'session' && !target.closest('.compose') ? y : null; };
  const move = (y, e) => {
    if (y0 === null) return;
    const up = y0 - y;
    if (up <= 0) { if (pull) set(0); return; }
    if (e && e.cancelable) e.preventDefault();
    set(Math.min(140, up * .55));
  };
  const end = () => {
    if (y0 === null) return;
    const go = pull >= ARM;
    y0 = null;
    if (go) enterVoice(state.session);
    set(0, true);
  };
  view.addEventListener('touchstart', e => begin(e.touches[0].clientY, e.target), {passive: true});
  view.addEventListener('touchmove', e => move(e.touches[0].clientY, e), {passive: false});
  view.addEventListener('touchend', end);
  view.addEventListener('touchcancel', end);
  view.addEventListener('mousedown', e => begin(e.clientY, e.target));
  addEventListener('mousemove', e => { if (e.buttons) move(e.clientY); });
  addEventListener('mouseup', end);
})();
// 新对话: she forgets, and you are back at the orb, talking.
function newChat() {
  api('/api/conversations/new', 'POST').catch(() => {});
  show('orb');
  voice.on();
}

// ---------- 设置 and 使用说明 ----------
// Her picture is the orb (in color unless you turned her gray), and a word from her; then the app's language and look, what plays
// the notes, the help, and the version the Mac runs (the project and that commit on GitHub).
const CHOICES = {language: ['zh', 'en'], appearance: ['system', 'dark', 'light']};
const CHOICE_WORDS = {language: () => T.languages, appearance: () => T.appearances};
const chosen = key => key === 'language' ? lang : data.settings[key];
function renderSettings() {
  for (const key in CHOICES) $(`#view-settings [data-pick=${key}] .gv`).textContent = CHOICE_WORDS[key]()[chosen(key)];
  $('#her-intro').textContent = T.intro;
  renderDevices();
  const {repo, commit} = data.version;
  $('#repo-link').href = repo || '#';
  $('#commit-link').href = repo && commit ? `${repo}/commit/${commit}` : '#';
  $('#commit-link').textContent = (commit || '').slice(0, 7);
}
// What plays the notes, as the Mac sees it: read when the settings open, and again whenever the piano changes while
// they are open. One not connected is a row to tap, for how to connect it (as the bar at the top says).
const loadDevices = () => api('/api/devices').then(found => { data.devices = found; renderDevices(); }).catch(() => {});
function renderDevices() {
  $('#devices').innerHTML = data.devices.map((d, i) => {
    const row = `${d.kind === 'network' ? ICON.tablet : ICON.piano}<span class="gl">${esc(d.name)}</span>`;
    return d.connected ? `<div class="grow">${row}<span class="gv ok">${esc(T.connected)}</span></div>`
                       : `<button class="grow" data-device="${i}">${row}<span class="gv">${esc(T.disconnected)}</span>${CHEV}</button>`;
  }).join('');
}
document.querySelectorAll('[data-icon]').forEach(el => { el.outerHTML = ICON[el.dataset.icon]; });
$('#view-settings').addEventListener('click', e => {
  const row = e.target.closest('[data-pick]');
  if (row) {
    const key = row.dataset.pick;
    showCtx(row, null, CHOICES[key].map(c => [CHOICE_WORDS[key]()[c], c === chosen(key) ? CHECK : '', () => {
      data.settings[key] = c;
      applySettings();
      api('/api/settings', 'PATCH', {[key]: c}).catch(() => {});
    }]));
  }
  const device = data.devices[e.target.closest('[data-device]')?.dataset.device];
  if (device) info(T.howToConnect, T.connectHow[device.kind] || T.connectHow.other);
  if (e.target.closest('#open-help')) push('help');
});
const topics = () => herOn() ? T.helpTopics : T.basicTopics;
function renderHelp() {
  $('#help-list').innerHTML = topics().map(h =>
    `<button class="grow" data-article="${h.id}">${ICON[h.icon]}<span class="gl">${esc(h.title)}</span>${CHEV}</button>`).join('');
}
function renderArticle(id) {
  const h = topics().find(x => x.id === id);
  $('#article-title').textContent = h.title;
  $('#article-body').innerHTML = h.items.map(([t, text]) => `<h4>${esc(t)}</h4><p>${esc(text)}</p>`).join('');
}
$('#help-list').addEventListener('click', e => {
  const row = e.target.closest('[data-article]');
  if (row) push('article', row.dataset.article);
});

// ---------- Overlays ----------
let openSheet = null;
// A full sheet pushes the screen behind it back, as on iOS; a half sheet (播放列表) leaves it where it is.
function showSheet(sheet) {
  openSheet = sheet;
  app.classList.toggle('sheeted', !sheet.classList.contains('half'));
  $('#dim').classList.add('show');
  sheet.classList.add('show');
}
// An action sheet, as iOS asks before what cannot be undone: the red button does it, 取消 does not.
let confirmed = null;
function openAction(label, then) {
  $('#confirm-yes').textContent = label;
  confirmed = then;
  $('#dim').classList.add('show');
  $('#confirm').classList.add('show');
}
$('#confirm-yes').addEventListener('click', () => {
  const then = confirmed;
  closeOverlays();
  then();
});
$('#confirm-no').addEventListener('click', closeOverlays);
function closeOverlays() {
  openSheet?.classList.remove('show');
  openSheet = null;
  naming = confirmed = null;
  app.classList.remove('sheeted');
  for (const id of ['#dim', '#confirm', '#add-how', '#pick-playlist', '#rename', '#info']) $(id).classList.remove('show');
  document.activeElement?.blur?.();
}
const overlayOpen = () => openSheet || ['#confirm', '#add-how', '#pick-playlist', '#rename', '#info'].some(id => $(id).classList.contains('show'));
// Something to know, with one button, as iOS says it.
function info(title, text) {
  $('#info-title').textContent = title;
  $('#info-text').textContent = text;
  $('#dim').classList.add('show');
  $('#info').classList.add('show');
}
$('#info-ok').addEventListener('click', closeOverlays);

// The piano, as the Mac watches it: while the one chosen is not connected, a bar at the top of every page says so,
// and the pages make room for it; a tap says how to connect it. It goes once the piano is back. × hides it until
// then: it shows again the next time that piano goes, or for another one chosen meanwhile. With her, she says so
// too, as ever.
let pianoHidden = null;  // the piano whose bar was hidden, while it stays away
function renderPiano() {
  if (data.piano.connected) pianoHidden = null;
  const off = !data.piano.connected && pianoHidden !== data.piano.name;
  app.classList.toggle('piano-off', off);
  $('#piano-bar').hidden = !off;
  if (off) $('#piano-text').textContent = T.pianoOff(data.piano.name);
}
$('#piano-how').addEventListener('click', () => info(T.howToConnect, T.connectHow[data.piano.kind] || T.connectHow.other));
$('#piano-hide').innerHTML = ICON.close;
$('#piano-hide').addEventListener('click', () => { pianoHidden = data.piano.name; renderPiano(); });
$('#dim').addEventListener('click', closeOverlays);

// Long-press menu, for a piece and for a conversation: the row lifts and its actions show by it. With html null it is
// a pull-down menu instead (a setting's choices), opening from the right of its row.
// items: [label, icon, action, danger]; falsy items are left out.
let ctxActions = [];
function showCtx(row, html, items) {
  items = items.filter(Boolean);
  ctxActions = items.map(i => i[2]);
  const dr = app.getBoundingClientRect(), rr = row.getBoundingClientRect(), D = app.offsetWidth;
  const top = rr.top - dr.top, h = rr.height;
  const left = Math.max(10, rr.left - dr.left), right = Math.max(10, D - (rr.right - dr.left));
  const card = $('#ctx-card'), menu = $('#ctx-menu');
  card.hidden = html === null;
  card.innerHTML = html || '';
  Object.assign(card.style, {top: top + 'px', height: h + 'px', left: left + 'px', width: D - left - right + 'px'});
  menu.innerHTML = items.map(([label, icon, , danger], i) =>
    `<button data-i="${i}"${danger ? ' class="danger"' : ''}>${esc(label)}${icon}</button>`).join('');
  menu.style.left = (html === null ? D - right - 250 : left) + 'px';
  $('#ctx').classList.add('show');
  const mh = menu.offsetHeight, room = app.clientHeight - (top + h + 10);
  const below = room > mh + parseFloat(getComputedStyle(app).paddingBottom) + 10;
  menu.classList.toggle('up', !below);
  menu.style.top = (below ? top + h + 10 : top - mh - 10) + 'px';
}
function closeCtx() { $('#ctx').classList.remove('show'); document.querySelectorAll('.held').forEach(r => r.classList.remove('held')); }
$('#ctx .blur').addEventListener('click', closeCtx);
$('#ctx-menu').addEventListener('click', e => {
  const b = e.target.closest('[data-i]');
  if (!b) return;
  const act = ctxActions[b.dataset.i];
  closeCtx();
  act();
});

// As in ChatGPT, without the preview (a tap already opens the conversation) and without archive or lock.
// The current conversation is always on top, so it has nothing to pin.
function conversationMenu(target, row) {
  const c = target === 'now' ? null : data.conversations.find(x => String(x.id) === String(target));
  if (target !== 'now' && !c) return;
  showCtx(row, `<div class="text"><div class="t1">${esc(conversationTitle(c))}</div></div>`, [
    c && c.id !== data.current && [c.pinned_at ? T.unpin : T.pin, ICON.pin, () => changeConversation(c.id, {pinned: !c.pinned_at})],
    c && [T.rename, ICON.edit, () => askName(T.renameChat, conversationTitle(c), name => {
      changeConversation(c.id, {title: name && name !== conversationTitle({...c, title: ''}) ? name : ''});
    })],
    [T.delete, ICON.trash, () => collapse([row], () => deleteConversation(c ? c.id : 'now')), true],
  ]);
}
function changeConversation(id, change) {
  const c = data.conversations.find(x => x.id === id);
  if (c && 'title' in change) c.title = change.title;
  if (c && 'pinned' in change) c.pinned_at = change.pinned ? new Date().toISOString() : null;
  renderRecent();
  refresh();
  api(`/api/conversations/${id}`, 'PATCH', change).catch(() => {});
}
// Deleting the current conversation is a new conversation that keeps nothing: she forgets it and starts fresh.
function deleteConversation(id) {
  if (id === 'now') return api('/api/conversations/new', 'POST').catch(() => {});
  data.conversations = data.conversations.filter(c => c.id !== id);
  if (id === data.current) data.current = null;
  sessionMessages.delete(id);
  renderRecent();
  refresh();
  if (stack.some(e => e.view === 'session' && e.id === id)) show('orb');
  api(`/api/conversations/${id}`, 'DELETE').catch(() => {});
}

// A name in a text field, as iOS asks for one.
let naming = null;
function askName(title, value, save) {
  naming = save;
  $('#rename-title').textContent = title;
  const input = $('#rename-input');
  input.value = value;
  $('#dim').classList.add('show');
  $('#rename').classList.add('show');
  input.focus();
  input.select();
}
function saveName() {
  const save = naming, value = $('#rename-input').value.trim();
  closeOverlays();
  save?.(value);
}
$('#rename-cancel').addEventListener('click', closeOverlays);
$('#rename-save').addEventListener('click', saveName);
$('#rename-input').addEventListener('keydown', e => { if (isEnter(e)) saveName(); });

// ---------- Going around: the sidebar, the pages, the player ----------
// No buttons for going around. Home is the orb: a right swipe opens the sidebar beside it (a left swipe closes it
// again), a left swipe brings in the player. On any page, the player included, a right swipe takes it away.
// Whatever moves follows the finger, as on iOS. Away from the orb she keeps quiet (voice.hold).
const sidebarWidth = () => app.clientWidth * .78;
function setOpen(open) {
  state.open = open;
  voice?.hold(open || stack.length > 0);
  app.classList.remove('dragging');
  app.classList.toggle('open', open);
  app.style.setProperty('--p', open ? 1 : 0);
  if (open) document.querySelectorAll('.swipe.open').forEach(r => r.classList.remove('open'));
  else stopSearch();
  renderNow();
}
let drag = null;
app.addEventListener('pointerdown', e => {
  if (e.button > 0 || overlayOpen() || $('#ctx').classList.contains('show') || e.target.closest('.swipe.open, .scrub, textarea, #orb')) return;
  drag = {id: e.pointerId, x0: e.clientX, y0: e.clientY, p0: state.open ? 1 : 0, p: state.open ? 1 : 0, v: 0, t: performance.now(), lx: e.clientX,
          active: false, page: stack.length ? $('#view-' + stack.at(-1).view) : null, player: null, dx: 0,
          row: !!e.target.closest('#main .swipe')};  // at the library home, a left swipe on a row is the row's
});
app.addEventListener('pointermove', e => {
  if (!drag || e.pointerId !== drag.id) return;
  if ($('#ctx').classList.contains('show')) { drag = null; return; }  // a long-press took over
  const dx = e.clientX - drag.x0, dy = e.clientY - drag.y0;
  if (!drag.active) {
    if (Math.abs(dy) > 10 && Math.abs(dy) > Math.abs(dx)) { drag = null; return; }
    if (Math.abs(dx) < 10) return;
    const goes = drag.page ? dx > 0 : drag.p0 ? dx < 0 : dx > 0 || (!!nowPiece() && !drag.row);
    if (!goes) { drag = null; return; }
    drag.active = true;
    if (!drag.page && !drag.p0 && dx < 0) drag.player = ready('player');
    app.setPointerCapture(e.pointerId);
    app.classList.add('dragging');
  }
  const now = performance.now();
  drag.v = (e.clientX - drag.lx) / Math.max(1, now - drag.t);
  drag.lx = e.clientX; drag.t = now;
  if (drag.player) {
    drag.dx = Math.min(0, dx);
    moveAside(drag.player, app.offsetWidth + drag.dx);
  } else if (drag.page) {
    drag.dx = Math.max(0, dx);
    moveAside(drag.page, drag.dx);
    if (stack.length === 1 && herOn()) $('#mini').style.transform = drag.page.style.transform;  // going home, where it is not shown
  } else {
    drag.p = Math.max(0, Math.min(1, drag.p0 + dx / sidebarWidth()));
    app.style.setProperty('--p', drag.p);
  }
});
const endDrag = e => {
  if (!drag || e.pointerId !== drag.id) return;
  if (drag.active) {
    app.classList.remove('dragging');
    if (drag.player) {
      if (drag.dx < -90 || drag.v < -.3) push('player', null, drag.player);
      else slideOut(drag.player);
    } else if (drag.page) {
      if (drag.dx > 90 || drag.v > .3) pop();
      else { moveAside(drag.page, null); $('#mini').style.transform = ''; }
    } else setOpen(drag.v > .3 ? true : drag.v < -.3 ? false : drag.p > .5);
    guardUntil = performance.now() + 300;
  }
  drag = null;
};
app.addEventListener('pointerup', endDrag);
app.addEventListener('pointercancel', endDrag);

// Long-press: hold still on a row and its menu opens; the release that follows is not a tap.
function onHold(list, selector, open) {
  let h = null;
  const cancel = () => { if (h) clearTimeout(h.timer); h = null; };
  list.addEventListener('pointerdown', e => {
    const row = e.target.closest(selector);
    if (!row || e.button > 0 || state.selecting) return;
    const held = {x: e.clientX, y: e.clientY};
    held.timer = setTimeout(() => {
      // The list may have been redrawn during the press (a download moving on), so the row is the one under the finger now.
      const now = row.isConnected ? row : document.elementFromPoint(held.x, held.y)?.closest(selector);
      if (!now || !list.contains(now)) return cancel();
      held.on = true;
      try { now.setPointerCapture(e.pointerId); } catch {}
      now.classList.add('held');
      navigator.vibrate?.(10);
      open(now);
    }, HOLD_MS);
    h = held;
  });
  list.addEventListener('pointermove', e => {
    if (h && !h.on && Math.hypot(e.clientX - h.x, e.clientY - h.y) > 6) cancel();
  });
  const end = () => { if (h?.on) guardUntil = performance.now() + 100; cancel(); };
  list.addEventListener('pointerup', end);
  list.addEventListener('pointercancel', end);
  list.addEventListener('contextmenu', e => e.preventDefault());
}

// Rows: swipe left to reveal 删除, long-press for the menu.
function rowGestures(list, {tap, remove, hold}) {
  if (hold) onHold(list, '.swipe:not(.open)', row => hold(row.dataset.id, row));
  let s = null;
  list.addEventListener('pointerdown', e => {
    const row = e.target.closest('.swipe:not(.fixed)');
    if (!row || e.button > 0 || state.selecting || e.target.closest('.grip')) return;
    s = {id: e.pointerId, row, x0: e.clientX, y0: e.clientY, base: row.classList.contains('open') ? -DEL : 0, x: 0, active: false};
  });
  list.addEventListener('pointermove', e => {
    if (!s || e.pointerId !== s.id || s.row.classList.contains('held')) return;
    const dx = e.clientX - s.x0, dy = e.clientY - s.y0;
    if (!s.active) {
      if (Math.abs(dy) > 8 && Math.abs(dy) > Math.abs(dx)) { s = null; return; }
      if (Math.abs(dx) < 8) return;
      if (s.base === 0 && dx > 0) { s = null; return; }  // a right swipe on a closed row goes back
      s.active = true;
      s.row.setPointerCapture(e.pointerId);
      list.querySelectorAll('.swipe.open').forEach(r => { if (r !== s.row) r.classList.remove('open'); });
      s.content = s.row.querySelector('.content');
      s.content.style.transition = 'none';
    }
    let x = Math.min(0, s.base + dx);
    if (x < -DEL) x = -DEL - (-DEL - x) * .3;
    s.x = x;
    s.content.style.transform = `translateX(${x}px)`;
  });
  const end = e => {
    if (!s || e.pointerId !== s.id) return;
    if (s.active) {
      s.content.style.transition = '';
      s.content.style.transform = '';
      s.row.classList.toggle('open', s.x < -DEL / 2);
      guardUntil = performance.now() + 300;
    }
    s = null;
  };
  list.addEventListener('pointerup', end);
  list.addEventListener('pointercancel', end);
  list.addEventListener('click', e => {
    const row = e.target.closest('.swipe');
    if (!row) return;
    const stop = e.target.closest('[data-stop]');
    if (stop) { collapse([row], () => stopJob(+stop.dataset.stop)); return; }
    if (e.target.closest('.del')) { collapse([row], () => remove(row.dataset.id)); return; }
    const open = list.querySelector('.swipe.open');
    if (open) { open.classList.remove('open'); return; }  // like iOS: a tap first closes an open row
    tap(row.dataset.id, row);
  });
}
const isJob = key => String(key).startsWith('j'), isAudio = key => String(key).startsWith('a');
rowGestures($('#library-list'), {
  tap: (id, row) => isJob(id) || (isAudio(id) && (state.selecting || data.features.transcribe)) ? null
    : isAudio(id) ? audioInfo() : state.selecting ? toggleSelect(+id) : play(+id, listIds($('#library-list')), {kind: 'library'}, row),
  remove: id => isJob(id) ? stopJob(+id.slice(1)) : isAudio(id) ? trashAudio(+id.slice(1)) : deletePieces([+id]),
  hold: (id, row) => pieceMenu(id, row),
});
$('#library-list').addEventListener('click', e => {
  const b = e.target.closest('[data-transcribe]');
  if (b) { e.stopPropagation(); b.outerHTML = SPINNER; transcribeAudio(+b.dataset.transcribe); }
}, true);
rowGestures($('#group-list'), {
  tap: (id, row) => play(+id, listIds($('#group-list')), state.session, row),
  remove: id => {
    const kind = state.session.kind;
    if (kind === 'favorites') favorite(+id, false);
    else if (kind === 'playlist') removeFromPlaylist(playlistById(state.session.id), +id);
    else if (kind === 'recent') {
      const p = pieceById(+id);
      if (p) p.last_played_at = null;
      refresh();
      api(`/api/recordings/${id}`, 'PATCH', {played: false}).catch(() => {});
    } else deletePieces([+id]);
  },
  hold: (id, row) => pieceMenu(id, row),
});
rowGestures($('#playlist-list'), {
  tap: id => push('group', {kind: 'playlist', id: +id}),
  remove: id => { renderPlaylists(); askDeletePlaylist(+id); },  // the row comes back unless it is deleted
  hold: (id, row) => {
    const pl = playlistById(id);
    if (pl) playlistMenu(pl, row, cover(pl) + playlistText(pl));
  },
});
// In a playlist only its pieces whose files are there show: one dropped before the next one shown goes there.
dragToReorder($('#group-list'), (id, index) => {
  const pl = state.session?.kind === 'playlist' && playlistById(state.session.id);
  if (!pl) return;
  const next = listIds($('#group-list'))[index + 1], rest = pl.ids.filter(i => i !== id);
  const at = next === undefined ? rest.length : rest.indexOf(next);
  pl.ids = [...rest.slice(0, at), id, ...rest.slice(at)];
  changePlaylist(pl.id, {move: id, index: at});
});
rowGestures($('#queue-list'), {
  tap: (id, row) => { if (pb().id !== +id) play(+id, pb().ids, pb().scope, row); },
  remove: id => control('remove', +id),
});
rowGestures($('#history-list'), {
  tap: id => push('session', id === 'now' ? 'now' : +id),
  remove: id => deleteConversation(id === 'now' ? 'now' : +id),
  hold: (id, row) => conversationMenu(id, row),
});
onHold($('#recent'), '.sb-row', row => conversationMenu(row.dataset.target, row));
// Over the conversations a right swipe shows their times while the finger is down; letting go puts them away.
// (A left swipe there closes the sidebar, as anywhere else on it.)
(function peekTimes() {
  const list = $('#recent');
  let s = null;
  list.addEventListener('pointerdown', e => { if (!e.button) s = {id: e.pointerId, x0: e.clientX, y0: e.clientY, on: false}; });
  list.addEventListener('pointermove', e => {
    if (!s || e.pointerId !== s.id) return;
    if ($('#ctx').classList.contains('show')) { s = null; return; }
    const dx = e.clientX - s.x0, dy = e.clientY - s.y0;
    if (!s.on) {
      if (Math.abs(dy) > 8 && Math.abs(dy) > Math.abs(dx)) { s = null; return; }
      if (dx < 8) return;
      s.on = true;
      list.setPointerCapture(e.pointerId);
      list.classList.add('peeking');
    }
    const t = Math.max(0, dx) / 70;
    list.style.setProperty('--peek', t < 1 ? t : 1 + Math.min(.2, (t - 1) * .2));
  });
  const end = () => {
    if (s?.on) {
      list.classList.remove('peeking');
      list.style.setProperty('--peek', 0);
      guardUntil = performance.now() + 250;
    }
    s = null;
  };
  list.addEventListener('pointerup', end);
  list.addEventListener('pointercancel', end);
})();

// ---------- Sidebar search: conversations, then pieces ----------
// As in ChatGPT: a conversation shows the latest thing said in it that has the words, and opens there. The Mac has
// what was said; the pieces are here.
function startSearch() {
  $('#sidebar').classList.add('searching');
  $('#search-input').value = '';
  renderSearch('');
  $('#search-input').focus();
}
function stopSearch() {
  $('#sidebar').classList.remove('searching');
  $('#search-input').blur();
}
let found = {q: '', chats: []}, asking = null;
function renderSearch(text) {
  const q = text.trim().toLowerCase(), box = $('#search-results');
  if (!q) { box.innerHTML = ''; return; }
  const ps = data.recordings.filter(p => matches(p, q)).sort(byNewest);
  const cs = found.q === q ? found.chats.filter(f => data.conversations.some(c => c.id === f.id)) : [];
  box.innerHTML = (cs.length ? `<div class="group">${esc(T.chats)}</div>` + cs.map(f => {
      const c = data.conversations.find(x => x.id === f.id), m = f.message;
      return `<button class="res" data-conversation="${c.id}"${m ? ` data-message="${m.id}"` : ''}>
          <div class="t1"><span>${esc(conversationTitle(c))}</span><span class="when">${esc(agoLabel((m || c).at || c.updated_at))}</span></div>
          ${m ? `<div class="t2">${saidBy(m)}${excerpt(m.role === 'piece' ? titleParts(m.text).piece : m.text, q)}</div>` : ''}
        </button>`;
    }).join('') : '')
    + (ps.length ? `<div class="group">${esc(T.results)}</div>` + ps.map(p =>
      `<button class="res" data-piece="${p.id}"><div class="t1">${esc(p.piece)}</div><div class="t2">${esc(subtitle(p))}</div></button>`).join('') : '')
    || (found.q === q ? `<p class="empty">${esc(T.nothing)}</p>` : '');
}
// Who said it: you, she, or a piece she played.
const saidBy = m => m.role === 'piece' ? '♪ ' : esc(T.saidBy(m.role === 'user' ? T.you : T.name));
// The words in their sentence, shown from a little before them.
function excerpt(text, q) {
  const at = text.toLowerCase().indexOf(q);
  if (at < 0) return esc(text);
  const from = Math.max(0, at - 12);
  return (from ? '…' : '') + esc(text.slice(from, at)) + `<b>${esc(text.slice(at, at + q.length))}</b>` + esc(text.slice(at + q.length));
}
$('#search-input').addEventListener('input', e => {
  const q = e.target.value.trim().toLowerCase();
  renderSearch(q);
  clearTimeout(asking);
  if (q && herOn()) asking = setTimeout(() => api('/api/search?q=' + encodeURIComponent(q)).then(chats => {
    found = {q, chats};
    if ($('#search-input').value.trim().toLowerCase() === q) renderSearch(q);
  }).catch(() => {}), 150);
});
$('#search-results').addEventListener('click', e => {
  const res = e.target.closest('.res');
  if (!res) return;
  if (res.dataset.piece) {
    const id = +res.dataset.piece;
    show('library');
    play(id, listIds($('#library-list')), {kind: 'library'}, res);
    document.querySelector(`#library-list [data-id="${id}"]`)?.scrollIntoView({block: 'center'});
  } else {
    findMessage = +res.dataset.message || null;
    show('session', +res.dataset.conversation);
  }
});

// ---------- Wiring ----------
$('#scrim').addEventListener('click', () => setOpen(false));
$('#search-open').addEventListener('click', startSearch);
$('#search-cancel').addEventListener('click', stopSearch);
$('#new-chat').addEventListener('click', newChat);
$('#open-settings').innerHTML = ICON.gear;
$('#open-settings').addEventListener('click', () => show('settings'));
$('#sidebar').addEventListener('click', e => {
  const row = e.target.closest('[data-target]');
  if (!row) return;
  const t = row.dataset.target;
  if (['library', 'playlists', 'artists', 'history'].includes(t)) show(t);
  else if (t === 'favorites' || t === 'recent') show('group', {kind: t});
  else show('session', t === 'now' ? 'now' : +t);
});
addEventListener('keydown', e => {
  if (e.key !== 'Escape') return;
  if ($('#ctx').classList.contains('show')) closeCtx();
  else if (overlayOpen()) closeOverlays();
  else if (stack.length) pop();
  else if (state.open) setOpen(false);
});
document.addEventListener('gesturestart', event => event.preventDefault());  // Safari's own pinch zoom

// ---------- The orbs ----------
// The orb at home is her ears and voice; the others are the same picture on the same clock.
// It sits in the middle of the screen, not of the page: an iPhone Home Screen app's page starts below the status bar
// and reaches the bottom, so the orb goes up by half of what the page does not have (nothing when the page has the
// whole screen). Elsewhere (a browser's bars, Android's navigation bar below) it stays in the middle of the page.
function centerOrb() {
  const portrait = innerHeight > innerWidth, tall = Math.max(screen.width, screen.height);
  const missing = navigator.standalone === true && portrait ? Math.max(0, tall - innerHeight) : 0;
  $('#app').style.setProperty('--lift', `${missing / 2}px`);
}
addEventListener('resize', centerOrb);
centerOrb();
const orbElement = $('#orb');
voice = makeVoice(orbElement, $('#voice-toggle'), animateOrb(orbElement, () => herOn() && (state.view === 'orb' || state.open || flying)),
                  createCues(), () => T.orb);
const pulled = () => parseFloat($('#pull').style.getPropertyValue('--pull') || 0);
addOrb($('#pull .porb'), () => homeColor() * pulled(), () => state.view === 'session' && pulled() > 0);
addOrb($('#fly-orb'), homeColor, () => flying);
addOrb($('#her-orb'), () => !voice.off(), () => herOn() && state.view === 'settings');

applyFeatures();
applySettings();
requestAnimationFrame(playClock);
signIn();
