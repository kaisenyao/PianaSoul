// The app: the orb at home, the sidebar beside it, and the pages over it (library, playlists, artists, conversations,
// settings) with the player always one swipe away. Without her (the Mac has no key for her, or not the advanced
// features), the library is home instead, and nothing of hers shows. Everything shown comes from the Mac (/api,
// /events); what is done here goes back to it, so voice, chat and the phone always agree.
import {LANGUAGES} from './strings.js';
import {addOrb, animateOrb, homeColor, sameLook} from './orb.js';
import {createCues} from './cues.js';
import {SOURCES, onlineSearch, pieceOf} from './online.js';
import {tapPreparing} from './preparing.js';
import {pieceNotes} from './rolls.js';
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
  addTo: svg('<path d="M4 7h11M4 12h11M4 17h7"/><path d="M18 14v6M15 17h6"/>'),
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
  save: svg('<path d="M12 3.5V15M7.5 10.5 12 15l4.5-4.5"/><path d="M8 11H6.5A1.5 1.5 0 0 0 5 12.5v7A1.5 1.5 0 0 0 6.5 21h11a1.5 1.5 0 0 0 1.5-1.5v-7a1.5 1.5 0 0 0-1.5-1.5H16"/>'),
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
  // The icon is the white one in either look; the manifest's colors (an Android install's splash) follow the look.
  $('link[rel=manifest]').href = theme === 'dark' ? '/manifest.webmanifest' : '/manifest-light.webmanifest';
  for (const el of document.querySelectorAll('[data-t]')) el.textContent = T[el.dataset.t];
  for (const el of document.querySelectorAll('[data-t-placeholder]')) el.placeholder = T[el.dataset.tPlaceholder];
  for (const el of document.querySelectorAll('[data-t-label]')) el.setAttribute('aria-label', T[el.dataset.tLabel]);
  $('#search-input').placeholder = herOn() ? T.searchAll : T.searchPieces;
  voice?.render();
  renderRecent();  // the sidebar's chats are drawn as they change, and their days are words: drawn again here
  if (openSheet === $('#sheet-add')) online.follow(data.settings.search_sources);  // the sites chosen, here or elsewhere
  refresh();
}
dark.addEventListener('change', () => { if (data.settings.appearance === 'system') applySettings(); });

// ---------- What the Mac keeps ----------
const data = {recordings: [], making: [], jobs: [], audio: [], playback: {id: null, state: 'stopped', ids: [], scope: {}, mode: 'order'},
  conversations: [], current: null, busy: null, settings: {language: '', appearance: 'system', search_sources: [...SOURCES]},
  devices: [], piano: {name: '', kind: 'other', connected: true},
  version: {commit: '', repo: ''}, features: {transcribe: true, assistant: true}, playlists: [], pictures: {}, descriptions: {}};
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
// The library's pieces, and apart from them those being made that play as they are made: playing one, the player has
// it, as a chat card does; it is in the lists once it is made.
function setRecordings(all) {
  data.recordings = all.filter(r => !r.making);
  data.making = all.filter(r => r.making);
}
const recordings = async () => { setRecordings(await api('/api/recordings')); refresh(); };
async function conversations() {
  const c = await api('/api/conversations');
  data.conversations = c.conversations;
  data.current = c.current;
  followCurrent();
  renderRecent();
  refresh();
}

// The empty current conversation ('now'), once it has begun (on the Mac, or on another phone): that conversation.
function followCurrent() {
  if (state.session !== 'now' || !data.current) return;
  const top = stack.findLast(e => e.view === 'session' && e.id === 'now');
  if (top) top.id = state.session = data.current;
}

// What changed on the Mac, as it happens. A lost connection comes back, with everything read again.
function listen() {
  const socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/events`);
  socket.onmessage = ({data: raw}) => {
    let e = null;
    try { e = JSON.parse(raw); } catch { return; }
    if (e.type === 'playback') { delete e.type; data.playback = e; heardAt = performance.now(); refresh(); }
    else if (e.type === 'jobs') onJobs(e.jobs);
    else if (e.type === 'library') {  // the artists' pictures and descriptions with it (one being saved: as it is here)
      data.pictures = e.pictures;
      if (!describing) data.descriptions = e.descriptions;
      recordings();
    }
    else if (e.type === 'conversations') conversations();
    else if (e.type === 'message') onMessage(e.message);
    else if (e.type === 'busy') { data.busy = e.conversation; renderTyping(); }
    else if (e.type === 'settings') { delete e.type; data.settings = e; applySettings(); }
    else if (e.type === 'audio') { data.audio = e.audio; refresh(); }
    else if (e.type === 'playlists') { if (!changing) setPlaylists(e.playlists); }  // changing: as it is here, until answered
    else if (e.type === 'piano') { if (state.view === 'settings') loadDevices(); }
  };
  // Asked again at once, and half a second later (a page back from the background finds the Mac as it was, even if its
  // first request fails), then every 2 s; still not back after 1.5 s, the Mac is shown away.
  socket.onclose = () => {
    const shown = setTimeout(away, 1500);
    let tries = 0;
    const again = () => load().then(() => { clearTimeout(shown); listen(); }, error => {
      if (error.message === 'signed out') { clearTimeout(shown); return signedOut(true); }
      setTimeout(again, tries++ ? 2000 : 500);
    });
    again();
  };
}
// The service stopped (the Mac app quitting or restarting it, the Mac restarting): what played stops where it was
// heard, as paused, which is how the Mac has it when it is back (Playback keeps it); ▶ then goes on from there.
function away() {
  if (pb().state !== 'playing') return;
  const at = position();
  data.playback = {...pb(), state: 'paused', position: at, starts_in: 0, until: at};
  heardAt = performance.now();
  refresh();
}
async function load() {
  showState(await api('/api/state'));
}
function showState({recordings: all, ...rest}) {
  const transcribed = data.features.transcribe;
  Object.assign(data, rest);
  setRecordings(all);
  heardAt = performance.now();
  try { localStorage.setItem('features', JSON.stringify(data.features)); } catch {}
  applyFeatures(transcribed);
  applySettings();
  renderRecent();
  sent.length = 0;  // what waited for her while the Mac was away: kept by now, and read again below, or gone with it
  followCurrent();
  for (const e of stack) if (e.view === 'session') renderSession(e.id);  // what was said while the Mac was away
}
// Until the Mac has said whether this page may come in (a moment, a second at most), the page shows only its background:
// one it no longer lets in goes straight to saying so, without the home page showing first.
async function signIn() {
  const show = () => $('#app').classList.remove('waiting');
  // Reloaded while a video was open: back as it was at once, from what was kept, and brought up to date behind it.
  const left = leftForVideo();
  if (left) {
    showState(left.state);
    backTo(left);
    show();
  } else setTimeout(show, 1000);
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
// A piece is a library recording, one being made that plays as it is made (making), or one still being made (a job,
// kept apart by its key).
const pieceById = id => data.recordings.find(r => r.id === id) || data.making.find(r => r.id === id);
const jobKey = j => 'j' + j.id;
// As the Mac splits it (library.title_parts): the performer is what the parentheses that end it hold, theirs too.
function titleParts(title) {
  const dash = title.indexOf(' - ');
  const artist = dash < 0 ? '' : title.slice(0, dash), rest = (dash < 0 ? title : title.slice(dash + 3)).trimEnd();
  const opened = closingParentheses(rest);
  return {artist, piece: (opened === null ? rest : rest.slice(0, opened)).trim() || title,
          performer: opened === null ? '' : rest.slice(opened + 1, -1).trim()};
}
function closingParentheses(text) {
  let depth = 0;
  for (let at = text.length - 1; at >= 0; at--) {
    if (')）'.includes(text[at])) depth++;
    else if ('(（'.includes(text[at])) depth--;
    if (depth === 0) return at < text.length - 1 ? at : null;
  }
  return null;
}
const subtitle = p => [p.artist, p.performer].filter(Boolean).join(' · ');
// In the player, each of them a way to their page, as in Apple Music: whose it is (原作者), who plays it (演奏者).
const whoLinks = p => [['artist', p.artist], ['performer', p.performer]].filter(([, name]) => name)
  .map(([kind, name]) => `<button class="who" data-kind="${kind}">${esc(name)}</button>`).join('<span class="sep">·</span>');
// The site a piece came from, as people call it: YouTube, Bilibili (哔哩哔哩), or its address.
const siteOf = url => {
  const host = new URL(url).hostname.replace(/^(www|m)\./, '');
  return /^(youtube\.com|music\.youtube\.com|youtu\.be)$/.test(host) ? T.sites.youtube
    : /^(bilibili\.com|b23\.tv)$/.test(host) ? T.sites.bilibili : host;
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
const pausePiano = () => { if (pb().state === 'playing') control('pause'); };
// ▶ or ⏸ as the button shows it: what the page shows is what a tap does, even when the page is behind (away).
const playOrPause = from => control(pb().state === 'playing' ? 'pause' : 'play', null, null, from);
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
// or the sidebar beside it. The player is a page too, and the pages opened from it (an artist's) go over it. Each
// kind of page has one element: a page of a kind lower in the stack takes it back once the one above goes (slideOut).
const stack = [];
// Its layer: over the pages under it; the player over the mini player too, and the pages over the player over both.
const layer = (view, below) => view === 'player' ? 30 : below.length + 1 + (below.some(e => e.view === 'player') ? 30 : 0);
function show(view, id = null) {
  for (const e of stack.splice(0)) slideOut($('#view-' + e.view));
  if (view === 'orb' || (view === 'library' && !herOn())) setOpen(false);  // home
  else { setOpen(true); push(view, id); }
  settle();
}
// Home is her orb, or without her the library: then the library page itself sits where the orb would, and
// everything else (the sidebar beside it, the player a left swipe away, the pages over it) is as it is with her.
// transcribed: whether it could before. Gone now, what offered it goes: a menu, the Upload or Search Online chooser, the
// search; nothing else open (an edit, the uploads), which works as before.
function applyFeatures(transcribed = false) {
  if (transcribed && !data.features.transcribe) {
    closeCtx();
    if ($('#add-how').classList.contains('show') || (openSheet === $('#sheet-add') && !$('#add-field').hidden)) closeOverlays();
  }
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
  // A page of this kind lower down, showing in it (not sliding out): its place in it, for when it is back.
  const under = stack.findLast(e => e.view === view);
  if (under && !el.hidden && !el.classList.contains('off')) under.scroll = el.scrollTop;
  el.hidden = false;
  el.style.zIndex = layer(view, stack);
  moveAside(el, null);
  if (view === 'session') toLatest();  // a conversation opens at its latest message, or at a line search found
  renderEntry({view, id});
  if (view === 'player') showScreen(0);
  else if (view !== 'session') el.scrollTop = 0;
  if (view === 'settings') loadDevices();
  // Drawn as it opens, then to the side at once (it may be on screen, under the player), to come in from there.
  el.style.transition = 'none';
  el.classList.add('off');
  el.offsetWidth;
  el.style.transition = '';
  return el;
}
function push(view, id = null, el = ready(view, id)) {
  stack.push({view, id});
  motion(el, 'coming');
  el.offsetWidth;
  el.classList.remove('off');
  moveAside(el, null);
  atRest(el);
  settle();
}
// Which way a page moves, for what grays (style.css): where it goes stays bright, what it leaves grays. Coming in,
// it grays what it covers; going back, itself. At rest (in its place, or away), neither. dataset.motion: the latest
// way, so that a rest that comes after another move began does nothing.
function motion(el, how) {
  el.dataset.motion = +(el.dataset.motion || 0) + 1;
  el.classList.toggle('coming', how === 'coming');
  el.classList.toggle('going', how === 'going');
}
function atRest(el) {
  const latest = el.dataset.motion;
  setTimeout(() => { if (el.dataset.motion === latest) motion(el, null); }, 420);
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
  motion(el, el.classList.contains('coming') ? 'coming' : 'going');  // one coming in (let go half-way) goes back as it came
  moveAside(el, null);
  el.classList.add('off');
  setTimeout(() => {
    if (el.dataset.gen !== gen) return;
    motion(el, null);
    el.classList.remove('off');
    const i = stack.findLastIndex(e => 'view-' + e.view === el.id);  // a page of this kind under it: its again
    if (i < 0) return void (el.hidden = true);
    el.style.zIndex = layer(stack[i].view, stack.slice(0, i));
    renderEntry(stack[i]);
    el.scrollTop = stack[i].scroll ?? 0;
  }, 400);
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
// Redraw every open page, the sheets that show the library (播放列表, 添加到歌单, 添加曲目, an edit's picture), and
// what is playing, once the pages whose content is gone have closed. Not under a finger on a row (rowFinger): what
// comes meanwhile is drawn once it lets go. A row swiped open stays open.
function refresh() {
  if (rowFinger !== null) { missed = true; return; }
  closeGone();
  const open = [...document.querySelectorAll('.swipe.open')].map(row => [row.parentElement, row.dataset.id]);
  if (!herOn()) renderLibrary();  // home
  for (const e of stack) if (e.view !== 'session' && !$('#view-' + e.view).classList.contains('off')) renderEntry(e);  // one going: by slideOut
  if (openSheet === $('#sheet-queue')) renderQueue();
  if (openSheet === $('#sheet-add')) renderCands();
  if (openSheet === $('#sheet-pick')) renderPick();
  if (openSheet === $('#sheet-addto')) renderAddTo();
  for (const ed of pictureEditors) if (openSheet === ed.sheet) ed.render();
  renderSessionCards();
  renderNow();
  for (const [list, id] of open) list.querySelector(`:scope > .swipe:not(.fixed)[data-id="${CSS.escape(id)}"]`)?.classList.add('open');
}
// The finger on a row (a press, a swipe, a drag by its handle), whose list is not drawn again under it: the row would
// go, and with it the swipe, or the tap. A moment after it lets go (the tap that may follow done, a row folded away),
// the list is drawn.
let rowFinger = null, missed = false, flushTimer = 0;
function flushRows() {
  clearTimeout(flushTimer);
  if (rowFinger === null && missed) { missed = false; refresh(); }
}
const letRowsGo = () => { rowFinger = null; flushTimer = setTimeout(flushRows, 350); };  // after a tap, and a row's fold
for (const type of ['pointerup', 'pointercancel']) addEventListener(type, e => { if (e.pointerId === rowFinger) letRowsGo(); });
// A new touch, or the page put away (the app switcher, a call): the touch before has ended, whether or not its end came.
addEventListener('pointerdown', e => { if (e.isPrimary && rowFinger !== null) letRowsGo(); }, true);
addEventListener('pagehide', () => { if (rowFinger !== null) letRowsGo(); });
document.addEventListener('visibilitychange', () => { if (document.hidden && rowFinger !== null) letRowsGo(); });

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
// What you typed and sent, shown at once as Messages does, while it waits for her (she may be answering another
// request): the Mac keeps it once she takes it up, and that message takes its place (onMessage).
const sent = [];
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
  }).join('') + sent.filter(w => w.session === id).map(w => `<div class="u" data-sent>${esc(w.text)}</div>`).join('');
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
// A played piece as her message, like an audio message: tap it to play it again. One not in the library as the phone
// last read it is drawn as deleted, and drawn again as the library comes (a piece just made, its card here first).
function pieceCard(id, title, mid) {
  const p = pieceById(id), m = mid ? ` data-m="${mid}"` : '';
  if (!p) return `<div class="card gone" data-was="${id}" data-title="${esc(title)}"${m}>${esc(titleParts(title).piece)} · ${esc(T.gone)}</div>`;
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
  for (const card of document.querySelectorAll('#session-chat .card:is([data-piece], [data-was])')) {
    card.outerHTML = pieceCard(+(card.dataset.piece || card.dataset.was), card.dataset.title, card.dataset.m);
  }
}
$('#session-chat').addEventListener('click', e => {
  const card = e.target.closest('[data-piece]');
  // That piece alone: the chat is not a play queue
  if (card) play(+card.dataset.piece, [+card.dataset.piece], {kind: 'conversation', id: state.session}, card);
});
// A message kept on the Mac: it shows at once in its conversation, and the sidebar learns its new title.
let listsLater = null;
function onMessage(m) {
  const id = m.conversation_id;
  if (m.role === 'user') {
    const waited = sent.findIndex(w => w.text === m.text && (w.session === id || w.session === 'now'));
    if (waited >= 0) sent.splice(waited, 1);
  }
  if (!sessionMessages.has(id) && state.session === 'now' && !data.current) sessionMessages.set(id, []);
  const kept = sessionMessages.get(id);
  if (kept && !kept.some(x => x.id === m.id)) kept.push(m);  // once, though a reading of it may have it already
  if (state.view === 'session' && (state.session === id || (state.session === 'now' && !data.current))) {
    if (state.session === 'now') {
      stack.at(-1).id = state.session = id;
      data.current = id;
      for (const w of sent) if (w.session === 'now') w.session = id;  // the conversation they go in, now made
    }
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
    const dots = `<div class="typing" aria-label="${esc(T.typing)}"><i></i><i></i><i></i></div>`;
    const waiting = document.querySelector('#session-chat [data-sent]');  // what you sent after, below her answer
    if (waiting) waiting.insertAdjacentHTML('beforebegin', dots);
    else $('#session-chat').insertAdjacentHTML('beforeend', dots);
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
// remove: what a left swipe does here. It moves the piece to the Mac's Trash in 曲库 and on an artist's page; in 收藏,
// 最近播放 and a playlist it only takes the piece out of that list.
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
  const same = jobs.length === data.jobs.length && jobs.every((j, i) => j.id === data.jobs[i].id && j.status === data.jobs[i].status
    && j.title === data.jobs[i].title);  // named while it is transcribed: its row shows the name
  data.jobs = jobs;
  if (!same) return refresh();
  for (const j of jobs) document.querySelectorAll(`[data-ring="${j.id}"] .arc`).forEach(a => a.setAttribute('stroke-dashoffset', RING * (1 - j.progress)));
}
const stopJob = id => api(`/api/downloads/${id}`, 'DELETE').catch(() => {});
// A piece being prepared, tapped in the library or by its video's title in Search Online: once it can play, as it is
// made, it plays in the library's list (tapPreparing), Search Online closing as for a piece made there; before, a word
// says it is still being prepared. Gone meanwhile (made, or stopped), its row is about to change: nothing.
function playPreparing(job, from) {
  if (!job) return;
  const tapped = tapPreparing(job, listIds($('#library-list')));
  if (!tapped) return hud(T.stillPreparing);
  if (openSheet === $('#sheet-add')) closeOverlays();
  play(tapped.id, tapped.ids, {kind: 'library'}, from);
}

// One search box: it filters the library as you type, and the last row takes the same words online.
let libQ = '';
const matches = (p, q) => [p.title, p.piece, p.artist, p.performer, p.composer].some(x => (x || '').toLowerCase().includes(q));
function renderLibrary() {
  const q = libQ.trim().toLowerCase();
  const ready = [...data.recordings].sort(byNewest), recordings = audioItems();
  const shown = q ? ready.filter(p => matches(p, q)) : ready;
  const audio = recordings.filter(a => !q || a.name.toLowerCase().includes(q));
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
  $('#library-title').textContent = T.library;
  renderSelectionBar();  // its title while choosing
}
$('#lib-q').addEventListener('input', e => { libQ = e.target.value; renderLibrary(); });
$('#lib-online').addEventListener('click', e => {
  const how = e.target.closest('[data-how]')?.dataset.how;
  if (!how) return;
  $('#lib-q').blur();
  openAdd(how, libQ.trim());
});

// Choosing several pieces, in any list of them (曲库, a playlist, an artist's, 收藏, 最近播放): a check circle slides
// in on the left of each; the bar below adds them to a playlist, or does to them all what a left swipe does there.
// selectionPage: the list chosen from, its title, how it is drawn, and which page it is (left: chosen no more).
let selectionPage = null;
const listPage = () => state.view === 'group'
  ? {view: 'group', session: state.session, list: $('#group-list'), title: $('#group-title'), draw: () => renderGroup(state.session)}
  : {view: state.view, session: state.session, list: $('#library-list'), title: $('#library-title'), draw: renderLibrary};
function setSelecting(on, firstId) {
  if (!on && !state.selecting) return;
  const page = on ? listPage() : selectionPage;
  state.selecting = on;
  selectionPage = on ? page : null;
  selected.clear();
  if (on && firstId != null) selected.add(firstId);
  app.classList.toggle('selecting', on);
  $('#selbar').style.zIndex = on ? Math.max(25, +page.list.closest('.view').style.zIndex + 1) : '';  // over its page
  page.list.querySelectorAll('.swipe.open, .swipe.selected').forEach(r => r.classList.remove('open', 'selected'));
  if (page.view === state.view && page.session === state.session) page.draw();
  else if (page.title === $('#library-title')) page.title.textContent = T.library;  // left for another page: as it was
}
// What the bar's red button does: a left swipe's work in this list, to all of them; only the Trash is asked first.
function selectionRemoval() {
  const g = selectionPage?.view === 'group' ? selectionPage.session : null;
  if (g?.kind === 'playlist') return {label: T.removeFromPlaylist, count: T.removeN, act: ids => removeFromPlaylist(playlistById(g.id), ids)};
  if (g?.kind === 'favorites') return {label: T.unfavorite, count: T.unfavoriteN, act: ids => favorite(ids, false)};
  if (g?.kind === 'recent') return {label: T.forgetPlay, count: T.forgetN, act: forgetPlay};
  return {label: T.delete, count: T.deleteN, ask: T.trashN, act: deletePieces};  // 曲库, an artist's
}
function renderSelectionBar() {
  if (!state.selecting) return;
  const shown = listIds(selectionPage.list), removal = selectionRemoval();
  for (const id of selected) if (!shown.includes(id)) selected.delete(id);  // gone from the list: chosen no more
  const n = selected.size;
  selectionPage.title.textContent = n ? T.selected(n) : T.choose;
  for (const b of document.querySelectorAll('.sel-all')) b.textContent = n && shown.every(id => selected.has(id)) ? T.none : T.all;
  $('#sel-delete').disabled = $('#sel-playlist').disabled = !n;
  $('#sel-delete').textContent = n ? removal.count(n) : removal.label;
}
function toggleSelect(id) {
  selected.has(id) ? selected.delete(id) : selected.add(id);
  selectionPage.list.querySelector(`:scope > .swipe[data-id="${id}"]`)?.classList.toggle('selected', selected.has(id));
  renderSelectionBar();
}
for (const b of document.querySelectorAll('.sel-cancel')) b.addEventListener('click', () => setSelecting(false));
for (const b of document.querySelectorAll('.sel-all')) {
  b.addEventListener('click', () => {  // the pieces shown: with words in the library's search, only those
    const shown = listIds(selectionPage.list), all = shown.length && shown.every(id => selected.has(id));
    selected.clear();
    if (!all) shown.forEach(id => selected.add(id));
    selectionPage.draw();
  });
}
$('#sel-playlist').addEventListener('click', () => {
  const ids = [...selected];
  setSelecting(false);
  pickPlaylist(ids);
});
$('#sel-delete').addEventListener('click', () => {
  const {list} = selectionPage, removal = selectionRemoval(), ids = [...selected];
  const go = () => collapse(ids.map(id => list.querySelector(`:scope > .swipe[data-id="${id}"]`)).filter(Boolean), () => {
    removal.act(ids);
    setSelecting(false);
  });
  if (removal.ask) openAction(removal.ask(ids.length), go);
  else go();
});
function deletePieces(ids) {
  data.recordings = data.recordings.filter(r => !ids.includes(r.id));
  refresh();
  api('/api/recordings', 'DELETE', {ids}).catch(() => {});
}
function collapse(rows, after) {
  for (const row of rows) { row.style.height = row.offsetHeight + 'px'; row.offsetHeight; row.classList.add('removing'); }
  setTimeout(after, 270);
}

// ---------- Artists, and the list page for 收藏 / 最近播放 / one artist (a playlist's is the same page) ----------
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
// An artist's own picture, or else a pianist's channel's.
const ownPicture = name => data.pictures[name] ? `/api/pictures?name=${encodeURIComponent(name)}&v=${data.pictures[name]}` : '';
const channelPicture = (by, pieces) => by === 'performer' && pieces.find(p => p.channel_avatar)?.channel_avatar || '';
const pictureOf = (by, name, pieces) => ownPicture(name) || channelPicture(by, pieces);
const artworkOf = p => p.artwork ? `/api/recordings/${p.id}/artwork?v=${p.artwork}` : '';
// What the user wrote of an artist or a playlist (简介), under its name, as written.
const description = text => text ? `<p class="desc">${esc(text)}</p>` : '';
const avatar = (name, picture, cls = '') => picture
  ? `<img class="mono${cls}" src="${esc(picture)}" alt="" referrerpolicy="no-referrer">`
  : `<span class="mono${cls}" style="--h:${hueOfName(name)}">${esc(initial(name))}</span>`;
function renderArtists() {
  for (const b of document.querySelectorAll('#role-seg button')) b.classList.toggle('on', b.dataset.role === role);
  const list = [...people(role)].sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0], 'zh'));
  $('#artist-list').innerHTML = list.map(([name, ps]) => `<button class="arow" data-group="${esc(name)}">
      ${avatar(name, pictureOf(role, name, ps))}
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
  $('#group-more').hidden = g.kind !== 'artist' && g.kind !== 'performer';  // their picture
  $('#group-foot').innerHTML = '';
  const ps = groupPieces(g);
  const name = g.kind === 'favorites' ? T.favorites : g.kind === 'recent' ? T.recent : g.name;
  $('#group-title').textContent = name;
  $('#group-hero').hidden = g.kind === 'recent';
  $('#group-hero').innerHTML = (g.kind === 'favorites' ? `<span class="mono square" style="--h:340">${HEART_FILL}</span>` : avatar(name, pictureOf(g.kind, name, ps)))
    + `<b>${esc(name)}</b><span>${esc([g.kind === 'artist' || g.kind === 'performer' ? T[g.kind] : '', T.pieces(ps.length), totalTime(ps)].filter(Boolean).join(' · '))}</span>`
    + description(g.kind === 'artist' || g.kind === 'performer' ? data.descriptions[name] : '');
  const remove = {favorites: T.unfavorite, recent: T.forgetPlay}[g.kind];
  $('#group-list').innerHTML = ps.map(p => pieceRow(p, g.kind === 'recent' ? agoLabel(p.last_played_at) : '', remove)).join('')
    || `<p class="empty">${esc({favorites: T.noFavorites, recent: T.noPlays}[g.kind] || T.noArtistPieces)}</p>`;
  renderSelectionBar();
}
// Out of 最近播放 (their plays still counted).
function forgetPlay(ids) {
  for (const id of ids) { const p = pieceById(id); if (p) p.last_played_at = null; }
  refresh();
  for (const id of ids) api(`/api/recordings/${id}`, 'PATCH', {played: false}).catch(() => {});
}
// Into 收藏, or out of it: shown at once, all of them, then told to the Mac.
function favorite(ids, on) {
  const stamp = on ? new Date().toISOString() : null;
  for (const id of ids) { const p = pieceById(id); if (p) p.favorited_at = stamp; }
  refresh();
  for (const id of ids) api(`/api/recordings/${id}`, 'PATCH', {favorite: on}).catch(() => {});
}

// ---------- Pictures: artwork, and artists' own ----------
// A picture in an edit sheet: a photo chosen (moved and scaled to its square, or circle) or the picture taken away
// shows at once, and is kept on the Mac only with 完成; 取消 leaves the one there was. Without one, it shows what
// shows instead (fallback: a playlist's cover of its pieces, an artist's initial). A tap on the picture itself moves
// and scales it again: the photo chosen here, from where it was left (all of it still there to take), or else the
// picture as the Mac keeps it; with none, it chooses a photo, as 选取封面 (头像) does.
const pictureEditors = [];
function pictureEditor(id, current, fallback) {
  const el = $(id), img = el.querySelector('img'), file = el.querySelector('input'), remove = el.querySelector('.danger');
  const tile = el.querySelector('.tile'), round = el.classList.contains('round');
  // chosen: undefined (as it is), a JPEG, or null (taken away); photo: what it was made from, and where in it (at)
  let chosen, shown = '', photo = null, at = null;
  let turn = 0, busy = false;  // turn: this sheet's opening (reset), so that nothing reaches another; busy: adjusting
  const showing = () => chosen === undefined ? current() : chosen ? shown : '';
  const ed = {
    sheet: el.closest('.sheet'),
    render() {
      const src = showing();
      if (src && img.getAttribute('src') !== src) img.src = src;
      img.hidden = remove.hidden = !src;
      el.querySelector('.fallback').innerHTML = src ? '' : fallback();
      tile.setAttribute('aria-label', src ? T.moveAndScale : el.querySelector('.link').textContent);
    },
    choose(jpeg) {
      URL.revokeObjectURL(shown);
      chosen = jpeg;
      shown = jpeg ? URL.createObjectURL(jpeg) : '';
      ed.render();
    },
    reset() { turn++; photo = at = null; ed.choose(undefined); },
    keep(path) { if (chosen !== undefined) keepPicture(path, chosen, chosen && {photo, at}); },  // with 完成: what changed
  };
  async function adjust(from, where = null) {
    if (busy || !from) return;
    busy = true;
    const mine = turn;
    try {
      const made = await cropPhoto(from, round, where);
      if (made && mine === turn) { photo = from; at = made.at; ed.choose(made.jpeg); }
    } finally {
      busy = false;
    }
  }
  file.addEventListener('change', () => {
    const chosenFile = file.files[0];
    file.value = '';
    adjust(chosenFile);
  });
  tile.addEventListener('click', async () => {
    const src = showing();
    if (!src) return file.click();  // still in the tap: the phone asks for a photo
    if (busy) return;
    if (photo) return adjust(photo, at);
    busy = true;
    const mine = turn;
    let kept = null, original = null;
    try {
      kept = await fetch(src).then(r => r.ok ? r.blob() : null);
      original = await originalOf(kept);
    } catch {} finally {  // the Mac or the network gone halfway: as far as it got
      busy = false;
    }
    if (mine !== turn || openSheet !== ed.sheet) return;  // the sheet closed meanwhile, or opened for another
    if (original) adjust(original.photo, original.at);
    else if (kept) adjust(kept);  // kept before photos were: what there is of it
    else hud(T.photoUnreadable);
  });
  remove.addEventListener('click', () => { photo = at = null; ed.choose(null); });
  pictureEditors.push(ed);
  return ed;
}
// A picture kept on the Mac (a JPEG), or taken away (null); the Mac then says what changed, and it shows. With it,
// the photo it was made from (from: {photo, at}), for moving and scaling it again later.
async function keepPicture(path, jpeg, from = null) {
  const sent = await fetch(path, jpeg ? {method: 'PUT', body: jpeg, headers: {'Content-Type': 'image/jpeg'}} : {method: 'DELETE'})
    .catch(() => null);
  if (!sent?.ok) return hud(T.photoFailed);
  if (jpeg && from?.photo) keepOriginal(jpeg, from).catch(() => {});  // only for adjusting later: no word if it fails
}
// The photo a picture was made from, as iOS keeps a contact's: the photo and the square of it the picture is, so that
// all of it is there when the picture is moved and scaled again. Kept on the Mac by the picture's own content (its
// SHA-256), at most ORIGINAL_PX on its longer side, or larger where its square needs it to make the same picture
// again (a small square of a large photo).
const ORIGINAL_PX = 1600, PHOTO_BYTES = 12e6;  // larger than PHOTO_BYTES, a whole photo is made a JPEG again (the Mac takes 16 MB)
const CANVAS_PX = 16e6;  // what iOS Safari draws on one canvas at most (4096 × 4096)
const digestOf = async blob => crypto.subtle && [...new Uint8Array(await crypto.subtle.digest('SHA-256', await blob.arrayBuffer()))]
  .map(b => b.toString(16).padStart(2, '0')).join('');
async function keepOriginal(jpeg, {photo, at}) {
  // At most ORIGINAL_PX on its longer side, but never so small that its square makes a smaller picture than now.
  const scaleFor = longer => Math.min(1, Math.max(ORIGINAL_PX / longer, Math.min(1000, at.side) / at.side));
  const [digest, small] = await Promise.all([digestOf(jpeg), shrunk(photo, scaleFor)]);
  if (!digest || !small) return;
  const square = new URLSearchParams({size: jpeg.size, x: at.x * small.scale, y: at.y * small.scale, side: at.side * small.scale});
  await fetch(`/api/picture-originals/${digest}?${square}`, {method: 'PUT', body: small.jpeg, headers: {'Content-Type': 'image/jpeg'}});
}
async function originalOf(picture) {
  if (!picture) return null;
  const digest = await digestOf(picture), found = digest && await fetch(`/api/picture-originals/${digest}`).catch(() => null);
  if (!found?.ok) return null;
  const [x, y, side] = (found.headers.get('X-Crop') || '').split(',').map(Number);
  return side > 0 ? {photo: await found.blob(), at: {x, y, side}} : null;
}
// A photo as a JPEG made smaller by scaleFor(its longer side) (on white, for a PNG's clear parts), and by how much;
// a JPEG not made smaller, and not too large to send, is as it is.
async function shrunk(photo, scaleFor) {
  const url = URL.createObjectURL(photo), img = new Image();
  try {
    img.src = url;
    await img.decode();
    let scale = scaleFor(Math.max(img.naturalWidth, img.naturalHeight));
    if (scale === 1 && photo.type === 'image/jpeg' && photo.size <= PHOTO_BYTES) return {jpeg: photo, scale};
    scale = Math.min(scale, Math.sqrt(CANVAS_PX / (img.naturalWidth * img.naturalHeight)));  // as large as iOS can draw
    const canvas = Object.assign(document.createElement('canvas'), {width: Math.round(img.naturalWidth * scale), height: Math.round(img.naturalHeight * scale)});
    const g = canvas.getContext('2d');
    g.fillStyle = '#fff';
    g.fillRect(0, 0, canvas.width, canvas.height);
    g.drawImage(img, 0, 0, canvas.width, canvas.height);
    const jpeg = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', .85));
    return jpeg && {jpeg, scale};
  } finally {
    URL.revokeObjectURL(url);
  }
}
// 移动和缩放, as iOS has it: the photo under a square (or a circle), always covering it, moved with one finger and
// scaled with two; from where, a square of the photo, it was left before (or the photo filling it, in the middle).
// 选取 gives {jpeg, at}: what is in it as the Mac keeps pictures (a JPEG, at most 1000 px, on white for a PNG's clear
// parts) and where that is in the photo. null with 取消, or when the photo cannot be opened (it says so).
let crop = null;
async function cropPhoto(file, round, at = null) {
  const img = $('#crop img'), url = URL.createObjectURL(file);
  img.src = url;
  try {
    await img.decode();
  } catch {
    URL.revokeObjectURL(url);
    hud(T.photoUnreadable);
    return null;
  }
  $('#crop').classList.toggle('round', round);
  $('#crop').hidden = false;
  const done = new Promise(resolve => { crop = {w: img.naturalWidth, h: img.naturalHeight, fingers: new Map(), resolve}; });
  layCrop(at);
  const kept = await done;
  $('#crop').hidden = true;
  URL.revokeObjectURL(url);
  if (kept === undefined) hud(T.photoUnreadable);
  return kept ?? null;
}
// The frame as big as the screen allows, over that square of the photo (at), or the photo filling it, in the middle.
function layCrop(at) {
  const stage = $('#crop .crop-stage'), W = stage.clientWidth, H = stage.clientHeight, F = Math.min(W, H);
  const least = F / Math.min(crop.w, crop.h), s = at ? Math.min(least * 5, Math.max(least, F / at.side)) : least;
  Object.assign(crop, {F, left: (W - F) / 2, top: (H - F) / 2, least, s,
                       x: at ? -at.x * s : (F - crop.w * s) / 2, y: at ? -at.y * s : (F - crop.h * s) / 2});
  Object.assign($('#crop .crop-frame').style, {left: crop.left + 'px', top: crop.top + 'px', width: F + 'px', height: F + 'px'});
  placeCrop();
}
// The photo kept over the whole frame: it never leaves part of it empty.
function placeCrop() {
  const c = crop;
  c.x = Math.min(0, Math.max(c.F - c.w * c.s, c.x));
  c.y = Math.min(0, Math.max(c.F - c.h * c.s, c.y));
  $('#crop img').style.transform = `translate(${c.left + c.x}px, ${c.top + c.y}px) scale(${c.s})`;
}
// The fingers, from where they were when the last one came or went: the point of the photo between them stays
// between them, and two scale it by how far apart they are.
function cropFingers() {
  const ps = [...crop.fingers.values()].slice(0, 2), r = $('#crop .crop-stage').getBoundingClientRect();
  return {cx: ps.reduce((a, p) => a + p.x, 0) / ps.length - r.left - crop.left, cy: ps.reduce((a, p) => a + p.y, 0) / ps.length - r.top - crop.top,
          d: ps.length > 1 ? Math.hypot(ps[0].x - ps[1].x, ps[0].y - ps[1].y) : 0};
}
function anchorCrop() { crop.from = crop.fingers.size ? {s: crop.s, x: crop.x, y: crop.y, ...cropFingers()} : null; }
const cropStage = $('#crop .crop-stage');
cropStage.addEventListener('pointerdown', e => {
  if (!crop) return;
  if (e.isPrimary) crop.fingers.clear();  // the first finger of a new touch: none from before is still down
  cropStage.setPointerCapture(e.pointerId);
  crop.fingers.set(e.pointerId, {x: e.clientX, y: e.clientY});
  anchorCrop();
});
cropStage.addEventListener('pointermove', e => {
  if (!crop?.fingers.has(e.pointerId)) return;
  crop.fingers.set(e.pointerId, {x: e.clientX, y: e.clientY});
  const {from} = crop, now = cropFingers(), scale = from.d && now.d ? from.s * now.d / from.d : from.s;
  crop.s = Math.min(crop.least * 5, Math.max(crop.least, scale));
  const x = now.cx - (from.cx - from.x) * crop.s / from.s, y = now.cy - (from.cy - from.y) * crop.s / from.s;
  Object.assign(crop, {x, y});
  placeCrop();
  if (crop.s !== scale || crop.x !== x || crop.y !== y) anchorCrop();  // held at a limit: from here on, not from where it began
});
for (const type of ['pointerup', 'pointercancel']) {
  cropStage.addEventListener(type, e => {
    if (!crop?.fingers.delete(e.pointerId)) return;
    anchorCrop();
  });
}
// A two-finger scroll or a pinch on a trackpad, as a pinch.
cropStage.addEventListener('wheel', e => {
  if (!crop) return;
  e.preventDefault();
  const r = cropStage.getBoundingClientRect(), cx = e.clientX - r.left - crop.left, cy = e.clientY - r.top - crop.top, was = crop.s;
  crop.s = Math.min(crop.least * 5, Math.max(crop.least, was * Math.exp(-e.deltaY / 300)));
  crop.x = cx - (cx - crop.x) * crop.s / was;
  crop.y = cy - (cy - crop.y) * crop.s / was;
  placeCrop();
}, {passive: false});
// The square of the photo in the frame, in the photo's own pixels.
const cropSquare = c => ({x: -c.x / c.s, y: -c.y / c.s, side: c.F / c.s});
addEventListener('resize', () => { if (crop) { layCrop(cropSquare(crop)); anchorCrop(); } });
function endCrop(choose) {
  const c = crop;
  crop = null;
  if (!choose) return c.resolve(null);
  const at = cropSquare(c), size = Math.round(Math.min(1000, at.side));
  const canvas = Object.assign(document.createElement('canvas'), {width: size, height: size}), g = canvas.getContext('2d');
  g.fillStyle = '#fff';
  g.fillRect(0, 0, size, size);
  g.drawImage($('#crop img'), at.x, at.y, at.side, at.side, 0, 0, size, size);
  canvas.toBlob(jpeg => c.resolve(jpeg ? {jpeg, at} : undefined), 'image/jpeg', .86);  // undefined: it could not be made
}
$('#crop-cancel').addEventListener('click', () => crop && endCrop(false));
$('#crop-done').addEventListener('click', () => crop && endCrop(true));

// ---------- Playlists (歌单) ----------
// The user's own lists of pieces, each once, in their order, kept on the Mac. Not the list that plays (播放列表):
// playing one fills that list, and changing either leaves the other alone. Only pieces whose files are there show.
const playlistById = id => data.playlists.find(pl => pl.id === +id);
const playlistPieces = pl => (pl?.ids || []).map(pieceById).filter(Boolean);
const coverOf = pl => pl?.artwork ? `/api/playlists/${pl.id}/artwork?v=${pl.artwork}` : '';
function cover(pl) {
  return pl.artwork ? `<span class="cover"><img src="${coverOf(pl)}" alt=""></span>` : madeCover(pl);
}
function madeCover(pl) {
  const ps = playlistPieces(pl).slice(0, 4);
  return ps.length === 4 ? `<span class="cover four">${ps.map(p => `<i style="--h:${hueOf(p.id)}"></i>`).join('')}</span>`
    : `<span class="cover" style="--h:${ps.length ? hueOf(ps[0].id) : 220}">${ICON.playlist}</span>`;
}
const playlistText = pl => `<div class="text"><div class="t1">${esc(pl.name)}</div><div class="t2">${esc(T.pieces(playlistPieces(pl).length))}</div></div>`;
function renderPlaylists() {
  $('#playlist-list').innerHTML = data.playlists.map(pl => `<div class="swipe" data-id="${pl.id}">
      <button class="del" tabindex="-1">${esc(T.delete)}</button>
      <div class="content">${cover(pl)}${playlistText(pl)}${CHEV}</div>
    </div>`).join('') || `<p class="empty">${esc(T.noPlaylists)}</p>`;
}
function renderPlaylist(id) {
  const pl = playlistById(id), ps = playlistPieces(pl);
  $('#group-title').textContent = pl?.name || '';
  $('#group-more').hidden = $('#group-hero').hidden = !pl;
  if (!pl) { $('#group-list').innerHTML = $('#group-foot').innerHTML = ''; return; }
  $('#group-hero').innerHTML = `${cover(pl)}<b>${esc(pl.name)}</b><span>${esc([T.pieces(ps.length), totalTime(ps)].filter(Boolean).join(' · '))}</span>`
    + description(pl.description)
    + (ps.length ? `<div class="hero-acts"><button data-act="order">${ICON_PLAY}<span>${esc(T.play)}</span></button><button data-act="shuffle">${ICON.shuffle}<span>${esc(T.shufflePlay)}</span></button></div>` : '');
  $('#group-list').innerHTML = ps.map(p => pieceRow(p, '', T.removeFromPlaylist, true)).join('') || `<p class="empty">${esc(T.noPlaylistPieces)}</p>`;
  // Empty, it asks for its pieces where they would be; with them, 添加曲目 is in its … menu, as in Apple Music.
  $('#group-foot').innerHTML = ps.length ? '' : `<button class="add-first" id="group-add">${ICON_ADD}<span>${esc(T.addMusic)}</span></button>`;
  renderSelectionBar();
}
// The playlists as the Mac has them, from its events or its answers: the pages of one gone close (refresh).
function setPlaylists(playlists) {
  data.playlists = playlists;
  refresh();
}
// What was shown at once of a change the Mac then refused (or did not get) is set right again from the Mac.
const resyncPlaylists = () => api('/api/playlists').then(playlists => { if (!changing) setPlaylists(playlists); }).catch(() => {});
// A change, shown at once (by the caller) and then as the Mac has it; null when it failed. Several made quickly (two
// pieces added, two rows taken out) each show at once: what the Mac says of the playlists meanwhile (its events, its
// answers) knows only those before it, so it waits for the last answer, and then the playlists are read again.
// They are sent one after another (playlistChanges), so that an add and its undo reach the Mac in their order.
let changing = 0, playlistChanges = Promise.resolve();
async function changePlaylist(id, change) {
  changing++;
  const sent = playlistChanges = playlistChanges.then(() => api(`/api/playlists/${id}`, 'PATCH', change).catch(() => null));
  const changed = await sent;
  if (--changing === 0) resyncPlaylists();
  return changed;
}
// A page whose content is gone closes, wherever it is in the stack: a playlist's (deleted here or on another phone),
// adding to it or editing it stopping too; the player's once there is no piece (the Mac started again, which plays
// nothing, or the piece was deleted), as the mini player goes.
function closeGone() {
  const gone = e => (e.view === 'group' && e.id?.kind === 'playlist' && !playlistById(e.id.id)) ||
                    (e.view === 'player' && !nowPiece());
  for (let i = stack.length - 1; i >= 0; i--) {
    if (!gone(stack[i])) continue;
    if (i === stack.length - 1) pop();
    else {
      const [e] = stack.splice(i, 1);
      if (!stack.some(x => x.view === e.view)) $('#view-' + e.view).hidden = true;  // not if a page above has it
    }
  }
  if ((openSheet === $('#sheet-pick') && !playlistById(pickFor)) || (openSheet === $('#sheet-plist') && !playlistById(plistFor))) closeOverlays();
}
// A new playlist, named first: with pieces (it stays where it was), or empty (it opens, to add to).
function newPlaylist(ids = []) {
  askName(T.newPlaylistTitle, '', async name => {
    const made = await api('/api/playlists', 'POST', {name, ids}).catch(() => null);
    if (!made) return;
    setPlaylists([made, ...data.playlists.filter(pl => pl.id !== made.id)]);
    if (ids.length) hud(T.addedTo(made.name));
    else if (state.view === 'playlists') push('group', {kind: 'playlist', id: made.id});
  }, true);
}
function addToPlaylist(pl, ids) {
  if (!pl) return;
  const fresh = ids.filter(id => !pl.ids.includes(id));  // each piece once in a playlist
  if (!fresh.length) return hud(T.alreadyIn(pl.name));
  pl.ids = [...pl.ids, ...fresh];
  refresh();
  changePlaylist(pl.id, {add: fresh}).then(changed => changed && hud(T.addedTo(pl.name)));
}
function removeFromPlaylist(pl, ids) {
  if (!pl) return;
  pl.ids = pl.ids.filter(i => !ids.includes(i));
  refresh();
  for (const id of ids) changePlaylist(pl.id, {remove: id});
}
function deletePlaylist(id) {
  setPlaylists(data.playlists.filter(pl => pl.id !== id));
  api(`/api/playlists/${id}`, 'DELETE').catch(resyncPlaylists);
}
const askDeletePlaylist = id => openAction(T.deletePlaylist, () => deletePlaylist(id));  // its pieces stay
function playlistMenu(pl, row, html) {
  showCtx(row, html, [
    [T.addMusic, ICON_ADD, () => openPick(pl.id)],
    [T.editPlaylist, ICON.edit, () => editPlaylist(pl.id)],
    [T.deletePlaylist, ICON.trash, () => askDeletePlaylist(pl.id), true],
  ]);
}
// 编辑歌单, as Apple Music edits one: its artwork, its name and its description (简介), kept with 完成.
let plistFor = null;
const plistArt = pictureEditor('#plist-art', () => coverOf(playlistById(plistFor)), () => madeCover(playlistById(plistFor) || {}));
function editPlaylist(id) {
  const pl = playlistById(id);
  if (!pl) return;
  plistFor = pl.id;
  $('#plist-name').value = pl.name;
  $('#plist-desc').value = pl.description;
  plistArt.reset();
  showSheet($('#sheet-plist'));
  fitField($('#plist-desc'));
}
$('#plist-name').addEventListener('keydown', e => { if (isEnter(e)) e.currentTarget.blur(); });
$('#plist-cancel').addEventListener('click', closeOverlays);
$('#plist-save').addEventListener('click', () => {
  const pl = playlistById(plistFor), name = $('#plist-name').value.trim() || pl?.name, description = $('#plist-desc').value.trim();
  closeOverlays();
  if (!pl) return;
  plistArt.keep(`/api/playlists/${pl.id}/artwork`);
  if (name === pl.name && description === pl.description) return;
  Object.assign(pl, {name, description});  // at once (with no name, its own)
  refresh();
  changePlaylist(pl.id, {name, description});
});
// 添加到歌单, as Apple Music asks: a sheet of the playlists, New Playlist… first, a ✓ by those the pieces are all in
// already; a tap adds them there.
let adding = [];
function pickPlaylist(ids) {
  adding = ids;
  renderAddTo();
  showSheet($('#sheet-addto'));
  $('#addto-list').scrollTop = 0;
}
function renderAddTo() {
  const holds = pl => adding.every(id => pl.ids.includes(id));
  $('#addto-list').innerHTML = `<button class="arow" data-new-playlist><span class="cover new">${ICON_ADD}</span>
      <div class="text"><div class="t1">${esc(T.newPlaylist)}</div></div></button>`
    + data.playlists.map(pl => `<button class="arow" data-playlist="${pl.id}">${cover(pl)}${playlistText(pl)}
      ${holds(pl) ? `<span class="in" aria-label="${esc(T.inPlaylist)}">${CHECK}</span>` : ''}</button>`).join('');
}
$('#addto-list').addEventListener('click', e => {
  const row = e.target.closest('.arow');
  if (!row) return;
  if ('newPlaylist' in row.dataset) return newPlaylist(adding);  // named over the sheet: 取消 goes back to it
  const ids = adding;
  closeOverlays();
  addToPlaylist(playlistById(row.dataset.playlist), ids);
});
$('#addto-cancel').addEventListener('click', closeOverlays);
// 添加曲目: the library, searchable, a + by each piece (a ✓ once it is in the playlist, which takes it out again).
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
  if (!pl) return;  // gone: closeGone closes the sheet
  const q = $('#pick-q').value.trim().toLowerCase();
  const ps = [...data.recordings].sort(byNewest).filter(p => !q || matches(p, q));
  $('#pick-pieces').innerHTML = ps.map(p => `<div class="prow">${pieceText(p)}
      <button class="addbtn" data-add-piece="${p.id}" aria-label="${esc(pl.ids.includes(p.id) ? T.removeFromPlaylistLong : T.addToPlaylistShort)}">${pl.ids.includes(p.id) ? CHECK : ICON_ADD}</button>
    </div>`).join('') || `<p class="empty">${esc(T.notFound)}</p>`;
}
$('#pick-q').addEventListener('input', renderPick);
// A tap on + adds the piece; on its ✓, takes it out again (one added by mistake, or one there from before).
$('#pick-pieces').addEventListener('click', e => {
  const b = e.target.closest('[data-add-piece]'), pl = playlistById(pickFor), id = +b?.dataset.addPiece;
  if (!b || !pl) return;
  if (pl.ids.includes(id)) return removeFromPlaylist(pl, [id]);
  pl.ids = [...pl.ids, id];  // added quietly (no word for each), as Apple Music does here
  refresh();
  changePlaylist(pl.id, {add: [id]});
});
$('#pick-done').addEventListener('click', closeOverlays);
$('#new-playlist').innerHTML = ICON_ADD;
$('#new-playlist').addEventListener('click', () => newPlaylist());
$('#group-more').innerHTML = ICON.more;
$('#group-more').addEventListener('click', e => {
  const g = state.session, pl = g?.kind === 'playlist' && playlistById(g.id);
  if (pl) playlistMenu(pl, e.currentTarget, null);
  else if (g?.kind === 'artist' || g?.kind === 'performer') showCtx(e.currentTarget, null, [[T.editArtist, ICON.edit, () => editArtist(g)]]);
});
// 编辑艺术家: an artist's (or a pianist's) own picture and description (简介), by name, kept with 完成.
let artistFor = {kind: 'artist', name: ''};
const artistArt = pictureEditor('#artist-art', () => ownPicture(artistFor.name),
                                () => avatar(artistFor.name, channelPicture(artistFor.kind, groupPieces(artistFor))));
function editArtist(g) {
  artistFor = g;
  $('#artist-name').textContent = g.name;
  $('#artist-desc').value = data.descriptions[g.name] || '';
  artistArt.reset();
  showSheet($('#sheet-artist'));
  fitField($('#artist-desc'));
}
let describing = 0;  // descriptions on their way to the Mac: what it says of them meanwhile waits
$('#artist-cancel').addEventListener('click', closeOverlays);
$('#artist-save').addEventListener('click', () => {
  const {name} = artistFor, description = $('#artist-desc').value.trim(), was = data.descriptions[name] || '';
  closeOverlays();
  artistArt.keep(`/api/pictures?name=${encodeURIComponent(name)}`);
  if (description === was) return;
  const set = text => { data.descriptions = {...data.descriptions, [name]: text}; if (!text) delete data.descriptions[name]; refresh(); };
  set(description);  // at once; the Mac then says so (the library event), or it is as it was
  describing++;
  api('/api/artists', 'PATCH', {name, description}).catch(() => set(was)).finally(() => describing--);
});
// A description's field grows with what is written.
function fitField(field) {
  field.style.height = '';
  field.style.height = field.scrollHeight + 'px';
}
for (const field of document.querySelectorAll('textarea.fline')) field.addEventListener('input', () => fitField(field));

// A single-line field's clear button (style.css), as Apple's guidelines have it for search fields and for iOS text
// fields: a tap empties it as typing would (its input event), the keyboard staying up. A search field shows it
// whenever it has text; a text field (a name) while it is edited and has text. Not for text of many lines (a message,
// a description), nor on the Mac, whose text fields have none.
function clearable(input) {
  const clear = Object.assign(document.createElement('button'), {type: 'button', className: 'clear'});
  clear.dataset.tLabel = 'clearText';  // named in the page's language, and again as it changes (applySettings)
  clear.setAttribute('aria-label', T.clearText);
  clear.addEventListener('pointerdown', e => e.preventDefault());  // the field keeps the focus
  clear.addEventListener('click', () => {
    input.value = '';
    input.dispatchEvent(new Event('input', {bubbles: true}));
    input.focus();
  });
  input.after(clear);
  if (input.type === 'search') return;
  const shown = () => { clear.hidden = !input.value || document.activeElement !== input; };
  for (const type of ['input', 'focus', 'blur']) input.addEventListener(type, shown);
  shown();
}
for (const input of document.querySelectorAll('.field input, .frow input, input.fline, .alert-field input')) clearable(input);
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

// A word, for a moment: something done (added to a playlist), or that could not be (a photo).
let hudTimer = 0;
function hud(text) {
  $('#hud').textContent = text;
  $('#hud').classList.add('show');
  clearTimeout(hudTimer);
  hudTimer = setTimeout(() => $('#hud').classList.remove('show'), 1600);
}

// Long-press menus for a piece: what it can do here.
function pieceMenu(key, row) {
  const list = row.parentElement;
  const job = data.jobs.find(j => jobKey(j) === key);
  if (job) return showCtx(row, pieceText({...titleParts(job.title || audioName(job)), job}), [[T.stopDownload, ICON.trash, () => foldAway(list, key, () => stopJob(job.id)), true]]);
  const a = data.audio.find(x => audioKey(x) === key);
  if (a) return showCtx(row, row.querySelector('.text').outerHTML, [
    data.features.transcribe && a.status !== 'waiting' && [T.transcribe, ICON.sparkle, () => transcribeAudio(a.id)],
    [T.moveToTrash, ICON.trash, () => foldAway(list, key, () => trashAudio(a.id)), true],
  ]);
  const id = +key, p = pieceById(id);
  if (!p) return;
  // Keeping it first (收藏, a playlist), then sending it out, changing it, choosing several; what cannot be undone apart,
  // last. In 收藏, 最近播放 and a playlist a swipe only takes a piece out of the list; the menu says plainly where the
  // file goes.
  const pl = state.session?.kind === 'playlist' && playlistById(state.session.id);
  showCtx(row, pieceText(p) + pieceSide(p), [
    [p.favorited_at ? T.unfavorite : T.favorite, ICON.heart, () => favorite([id], !p.favorited_at)],
    [T.addToPlaylist, ICON.addTo, () => pickPlaylist([id])],
    !p.making && [T[MIDI_OUT.text], MIDI_OUT.icon, () => sendMidi(p).then(sent => sent || hud(T[MIDI_OUT.failed]))],
    [T.edit, ICON.edit, () => openEdit(id)],
    [T.selectSeveral, ICON.select, () => setSelecting(true, id)],
    pl && [T.removeFromPlaylistLong, ICON.trash, () => foldAway(list, key, () => removeFromPlaylist(playlistById(pl.id), [id])), true],
    [T.moveToTrash, ICON.trash, () => foldAway(list, key, () => deletePieces([id])), true],
  ]);
}
// The list may have been redrawn while the menu was open, so the row to fold away is looked up again, in its list.
const foldAway = (list, id, then) => {
  const found = list.querySelector(`:scope > .swipe[data-id="${CSS.escape(id)}"]`);
  collapse(found ? [found] : [], then);
};

// Edit one piece: its artwork, its name, whose it is, who plays it.
let editId = null;
const pieceArt = pictureEditor('#edit-art', () => artworkOf(pieceById(editId) || {}),
                               () => `<span class="cover" style="--h:${hueOf(editId)}">${ICON.piano}</span>`);
function openEdit(id) {
  const p = pieceById(id);
  if (!p) return;
  editId = id;
  $('#f-piece').value = p.piece;
  $('#f-artist').value = p.artist;
  $('#f-performer').value = p.performer;
  $('#f-source').textContent = originOf(p);
  $('#f-added').textContent = addedOf(p);
  pieceArt.reset();
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
  pieceArt.keep(`/api/recordings/${id}/artwork`);
  if (!piece) return;
  const p = pieceById(id);
  if (p) Object.assign(p, change);
  refresh();
  const saved = await api(`/api/recordings/${id}`, 'PATCH', change).catch(() => null);
  if (saved) { data.recordings = data.recordings.map(r => r.id === id ? saved : r); refresh(); }
});

// ---------- The player ----------
// Tapping a piece or the mini player opens it, like a page; a right swipe takes it away. Open already under pages
// opened from it (an artist's), it is gone back to: those pages go.
function openPlayer() {
  if (!nowPiece() || state.view === 'player') return;
  const at = stack.findIndex(e => e.view === 'player');
  if (at < 0) return push('player');
  for (const e of stack.splice(at + 1)) slideOut($('#view-' + e.view));
  settle();
}
const MODES = ['order', 'one', 'shuffle'];
function renderNow() {
  const p = nowPiece(), mini = $('#mini');
  // The mini player is on every page, but not while the player is open (nor over pages opened from it); her home (the orb, the sidebar) has none,
  // since the player is one left swipe from the orb. The library as home has it, but not with the sidebar out.
  mini.hidden = !somethingPlaying() || (!stack.length && (herOn() || state.open)) || stack.some(e => e.view === 'player');
  pages.classList.toggle('has-mini', !mini.hidden);
  const mode = pb().mode || 'order';
  $('#p-mode').innerHTML = ICON[mode];
  $('#p-mode').setAttribute('aria-label', T.modes[mode]);
  renderVolume();
  if (!p) return;
  document.documentElement.style.setProperty('--h', hueOf(p.id));  // its color: the mini player's, the player's, the status bar's
  $('#mini-title').textContent = $('#p-title').textContent = p.piece;
  $('#mini-sub').textContent = subtitle(p);
  const who = JSON.stringify([p.artist, p.performer]);  // not drawn again under a finger while they stay the same
  if ($('#p-sub').dataset.who !== who) Object.assign($('#p-sub'), {innerHTML: whoLinks(p)}).dataset.who = who;
  const infoKey = lang + JSON.stringify(p);  // drawn again when the piece changes, or the page's language
  if (infoId !== infoKey) { infoId = infoKey; renderInfo(p); renderArtwork(p); }
  const paused = pb().state !== 'playing';
  for (const b of [$('#mini-toggle'), $('#p-toggle')]) {
    b.innerHTML = paused ? ICON_PLAY : ICON_PAUSE;
    b.setAttribute('aria-label', paused ? T.play : T.pause);
  }
  $('#view-player').classList.toggle('paused-art', paused);
  $('#p-midi').disabled = !!p.making;  // its file comes once it is made
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
  // Of a piece being made, how much is there, as a stream shows what it has loaded; once it is all there, it fades.
  const ready = pb().ready, bar = $('#p-ready');
  if (ready != null) {
    const width = Math.min(1, ready / length) * 100 + '%';
    if (bar.classList.contains('whole')) {  // another piece: from where it is, not grown from the last one's
      bar.style.transition = 'none';
      bar.style.width = width;
      bar.offsetWidth;
      bar.style.transition = '';
    }
    bar.style.width = width;
  }
  bar.classList.toggle('whole', ready == null);
  $('#p-pos').textContent = mmss(at);
  $('#p-left').textContent = '-' + mmss(length - at);
  $('#scrub').setAttribute('aria-valuenow', Math.floor(at));
  document.querySelectorAll(`.card[data-piece="${p.id}"] .cfill`).forEach(el => { el.style.width = f * 100 + '%'; });
}

// The artwork is the piece itself: its notes (web/rolls.js), of one being made as much as is there. Drawn only for
// the piece playing (playClock), whose playback says how much that is.
const rollOf = pieceNotes(id => api(`/api/recordings/${id}/notes`));
const notesFor = p => rollOf(p, pb().ready ?? Infinity);
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
  const art = $('#view-player .art');  // the notes are behind the artwork until it is turned over
  if (!$('#view-player').hidden && (!art.classList.contains('has-artwork') || art.classList.contains('flipped'))) {
    drawRoll($('#roll'), p, scrubbing ?? position(), false);
  }
  if (!$('#mini').hidden && $('#mini-art').hidden) drawRoll($('#mini canvas.mart'), p, position(), true);
}

// The piece's artwork, when it has one: in the player a card, the picture in front and its notes behind, the front
// again for each piece; in the mini player the picture instead of the notes.
let artFor = null;
function renderArtwork(p) {
  const art = $('#view-player .art'), src = artworkOf(p);
  art.classList.toggle('has-artwork', !!src);
  // With artwork it is a button (it turns over), as a tap is: reached and pressed from a keyboard, and named.
  if (src) for (const [name, value] of [['role', 'button'], ['tabindex', '0'], ['aria-label', T.turnArtwork]]) art.setAttribute(name, value);
  else for (const name of ['role', 'tabindex', 'aria-label']) art.removeAttribute(name);
  if (artFor !== p.id) { artFor = p.id; art.classList.remove('flipped'); }
  if (src && $('#p-artwork').getAttribute('src') !== src) $('#p-artwork').src = src;
  $('#mini-art').hidden = !src;
  $('#mini .mart:not(img)').hidden = !!src;
  if (src && $('#mini-art').getAttribute('src') !== src) $('#mini-art').src = src;
}
$('#view-player .art').addEventListener('click', e => {
  if (e.currentTarget.classList.contains('has-artwork')) e.currentTarget.classList.toggle('flipped');
});
$('#p-sub').addEventListener('click', e => {
  const b = e.target.closest('.who'), p = nowPiece();
  if (b && state.view === 'player' && p?.[b.dataset.kind]) push('group', {kind: b.dataset.kind, name: p[b.dataset.kind]});
});
$('#view-player .art').addEventListener('keydown', e => {
  if (isEnter(e) || e.key === ' ') { e.preventDefault(); e.currentTarget.click(); }
});

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
  const info = $('#p-info');
  view.addEventListener('pointerdown', e => {
    if (!e.button && e.isPrimary && !e.target.closest('.scrub') && !fromEdge(e)) {
      g = {id: e.pointerId, x0: e.clientX, y0: e.clientY, t: performance.now(), dy: 0, on: false};
    }
  });
  // On 详细信息, which scrolls, the swipe is the screens' only from its top and down: anything else scrolls it.
  const scrolls = dy => playerScreen === 1 && (dy < 0 || info.scrollTop > 0);
  view.addEventListener('pointermove', e => {
    if (!g || e.pointerId !== g.id) return;
    const dx = e.clientX - g.x0, dy = e.clientY - g.y0;
    if (!g.on) {
      if (Math.abs(dx) > 10 && Math.abs(dx) > Math.abs(dy)) { g = null; return; }
      if (Math.abs(dy) < 10) return;
      if (scrolls(dy)) { g = null; return; }
      g.on = true;
      view.setPointerCapture(e.pointerId);
      el.classList.add('dragging');
    }
    g.dy = playerScreen === 0 ? Math.min(0, dy) : Math.max(0, dy);  // nothing above the player, nor below 详细信息
    el.style.setProperty('--dy', g.dy + 'px');
  });
  // Let go (or the touch ends in a pointercancel, which a browser may still send): to the screen it was swiped
  // towards.
  const end = e => {
    if (!g || e.pointerId !== g.id) return;
    if (g.on) {
      const far = performance.now() - g.t < 300 ? 30 : 110;
      showScreen(g.dy < -far ? 1 : g.dy > far ? 0 : playerScreen);
      guardUntil = performance.now() + 250;
    }
    g = null;
  };
  view.addEventListener('pointerup', end);
  view.addEventListener('pointercancel', end);
  // ...and kept from the scroll, which would otherwise take the touch (a pointercancel) a few pixels in. iOS decides
  // on the first move: only one that goes down, at the top, is kept.
  view.addEventListener('touchmove', e => {
    const dy = g ? e.touches[0].clientY - g.y0 : 0;
    if (playerScreen === 1 && dy > 0 && !scrolls(dy) && e.cancelable) e.preventDefault();
  }, {passive: false});
})();

// 详细信息: the video as its site shows it (when the piece came from one), then the library's facts about the piece,
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
// Watching the original on its site: one sound at a time, so the piano pauses. iOS may reload a Home Screen app while a
// video is open over it, and the app would start again on its first page: where you were is kept until the page is
// back, and a page that was reloaded goes back there.
const LEFT_FOR_VIDEO = 'leftForVideo';
function leaveForVideo() {
  pausePiano();
  const search = openSheet === $('#sheet-add') && online.state.query ? online.kept() : null;
  try {
    localStorage.setItem(LEFT_FOR_VIDEO, JSON.stringify({at: Date.now(), state: {...data, recordings: [...data.recordings, ...data.making]}, open: state.open, stack, search,
                                                     scroll: $('#add-scroll').scrollTop, screen: playerScreen}));
  } catch {}
}
// iOS may also show the page for a moment and only then reload it: seen again, it is kept for a few seconds more.
document.addEventListener('visibilitychange', () => {
  if (document.hidden) return;
  try {
    const left = JSON.parse(localStorage.getItem(LEFT_FOR_VIDEO));
    if (left) localStorage.setItem(LEFT_FOR_VIDEO, JSON.stringify({...left, seen: Date.now()}));
  } catch {}
});
function leftForVideo() {
  let left = null;
  try {
    left = JSON.parse(localStorage.getItem(LEFT_FOR_VIDEO));
    // Long after (an hour away, or seen and not reloaded), starting afresh is what you would expect.
    if (!left?.state || Date.now() - (left.seen ?? left.at) > (left.seen ? 10_000 : 3600_000)) {
      localStorage.removeItem(LEFT_FOR_VIDEO);
      return null;
    }
    // Not gone yet: iOS may reload the page again, behind the video and once more as it closes.
    if (!document.hidden) localStorage.setItem(LEFT_FOR_VIDEO, JSON.stringify({...left, seen: Date.now()}));
  } catch {}
  return left;
}
function backTo(left) {  // at once, as it was: nothing slides in
  app.classList.add('instant');
  if (left.open) setOpen(true);  // the sidebar under the pages: the last of them goes back to it, not to home
  for (const e of left.stack) {  // every page you came through, so that going back goes where it went
    push(e.view, e.id);
    Object.assign(stack.at(-1), e);
  }
  if (state.view === 'player') showScreen(left.screen);
  if (left.search) {
    openAdd('search');
    $('#add-input').value = left.search.query;
    online.resume(left.search);
    $('#add-scroll').scrollTop = left.scroll;
  }
  app.offsetWidth;
  app.classList.remove('instant');
}
$('#p-info').addEventListener('click', e => {
  if (e.target.closest('#p-video')) leaveForVideo();
  if (e.target.closest('.fold')) showTechnical(!technicalOpen);
});
// Dragging the bar moves the playing position; the piano continues from there.
(function scrubber() {
  const el = $('#scrub');
  const at = e => {
    const r = el.getBoundingClientRect();
    return Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * (nowPiece()?.duration_s || 0);
  };
  let held = false;  // the finger is on it; scrubbing stays until the Mac has gone there, unless it is held again
  el.addEventListener('pointerdown', e => {
    if (!nowPiece() || !e.isPrimary) return;
    held = true;
    el.classList.add('active');
    el.setPointerCapture(e.pointerId);
    scrubbing = at(e);
    renderProgress();
  });
  el.addEventListener('pointermove', e => { if (held) { scrubbing = at(e); renderProgress(); } });
  const end = () => {
    if (!held) return;
    held = false;
    el.classList.remove('active');
    control('seek', scrubbing).finally(() => { if (!held) scrubbing = null; });
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
$('#p-toggle').addEventListener('click', e => playOrPause(e.currentTarget));
$('#p-fav').addEventListener('click', () => { const p = nowPiece(); if (p) favorite([p.id], !p.favorited_at); });
$('#p-add').innerHTML = ICON.addTo;
$('#p-add').addEventListener('click', () => { const p = nowPiece(); if (p) pickPlaylist([p.id]); });
$('#p-prev').addEventListener('click', e => control('prev', null, null, e.currentTarget));
$('#p-next').addEventListener('click', e => control('next', null, null, e.currentTarget));
$('#p-mode').addEventListener('click', () => control('mode', MODES[(MODES.indexOf(pb().mode) + 1) % MODES.length]));
$('#p-queue').addEventListener('click', openQueue);
$('#p-queue').innerHTML = ICON.queue;
$('#p-close').innerHTML = ICON.close;
// ✕, once confirmed: the piece stops for good (played again, it starts over) and the player closes onto the page it
// covered; with nothing playing, that page has no mini player.
$('#p-close').addEventListener('click', () => openAction(T.stop, () => {
  data.playback = {...pb(), state: 'stopped', position: 0};
  pop();
  control('stop');
}));
// A piece's .mid file goes out from the player, or its menu: to the share sheet (AirDrop, Messages, Files,
// GarageBand…) in Apple's WebKit (every browser on an iPhone or iPad, and Safari), into the Downloads in any other.
// Chrome says it can share a .mid (canShare), then refuses it ("Permission denied"), on Android and on a computer
// alike. Told by the engine, not by "Android" in the user agent: a tablet's desktop mode leaves that out.
const SHARES_MIDI = navigator.vendor === 'Apple Computer, Inc.'
  && !!navigator.canShare?.({files: [new File([''], 'piece.mid', {type: 'audio/midi'})]});
const MIDI_OUT = SHARES_MIDI ? {icon: ICON.share, label: 'share', text: 'shareMidi', failed: 'shareFailed'}
  : {icon: ICON.save, label: 'downloadMidi', text: 'downloadMidi', failed: 'downloadFailed'};
// False when it could not go (not when the share sheet was closed).
async function sendMidi(p) {
  const name = p.title.replace(/[<>:"/\\|?*\x00-\x1f\x7f]/g, '_') + '.mid';  // as the library names its files
  try {
    const got = await fetch(`/api/recordings/${p.id}/midi`);
    if (!got.ok) return false;
    const blob = await got.blob();
    if (SHARES_MIDI) {
      await navigator.share({files: [new File([blob], name, {type: 'audio/midi'})], title: p.piece});
    } else {  // as audio/x-midi, MIDI's other name: typed audio/midi, Android's Chrome saves it as ….mid.rtx
      const url = URL.createObjectURL(new Blob([blob], {type: 'audio/x-midi'}));
      Object.assign(document.createElement('a'), {href: url, download: name}).click();
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    }
    return true;
  } catch (error) {
    return error?.name === 'AbortError';
  }
}
$('#p-midi').innerHTML = MIDI_OUT.icon;
$('#p-midi').dataset.tLabel = MIDI_OUT.label;
$('#p-midi').addEventListener('click', async e => {
  const p = nowPiece(), button = e.currentTarget;
  if (p && !await sendMidi(p)) shake(button);
});
$('#mini').addEventListener('click', openPlayer);
$('#mini-toggle').addEventListener('click', e => { e.stopPropagation(); playOrPause(e.currentTarget); });

// ---------- 播放列表 ----------
function openQueue() {
  renderQueue();
  showSheet($('#sheet-queue'));
  $('#queue-list').scrollTop = 0;
}
function renderQueue() {
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
// Hold a piece by its handle and drag it up or down; the pieces it passes make room. Let go, moved(id, at, ids): at,
// its place in the whole list (all(); null: nothing to move in), ids, that list with it there. Only pieces whose files
// are there show, so one dropped before the next one shown goes there.
function dragToReorder(list, all, moved) {
  let r = null;
  list.addEventListener('pointerdown', e => {
    const grip = e.target.closest('.grip');
    if (!grip || e.button > 0 || !e.isPrimary || state.selecting) return;
    rowFinger = e.pointerId;
    const row = grip.closest('.swipe');
    const i = [...list.children].indexOf(row);
    r = {id: e.pointerId, row, y0: e.clientY, i0: i, i, h: row.offsetHeight};
    list.setPointerCapture(e.pointerId);
    row.classList.add('lifted');
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
    guardUntil = performance.now() + 200;
    r = null;
    const ids = changed && all();
    if (ids) {
      const next = listIds(list)[index + 1], rest = ids.filter(i => i !== id);
      const at = next === undefined ? rest.length : rest.indexOf(next);
      moved(id, at, [...rest.slice(0, at), id, ...rest.slice(at)]);
    }
    refresh();  // as it is now (drawn once the finger is gone: flushRows)
  };
  list.addEventListener('pointerup', end);
  list.addEventListener('pointercancel', end);
}
dragToReorder($('#queue-list'), () => pb().ids, (id, at, ids) => {
  data.playback = {...pb(), ids};  // where it was dropped, until the Mac says so too
  control('move', id, at);
});

// ---------- Adding pieces ----------
// Search online for piano versions, on the sites chosen (YouTube, Bilibili: each a capsule below the field, one
// always on), or paste a link. A video's cover opens it on its site; its title and the control at its end act on its
// piece (pieceOf): download it (the same queue she uses by voice), then play it; while it is made, the title plays it
// as soon as it can, and the ring stops it. Each site's videos show as they come, merged as hers are.
const online = onlineSearch(api, () => renderCands());
// Why a site could not be searched, as the site's: it cannot be reached, or will not search for now; or else that it
// failed. In full when none could be, in short (cause) above the videos of those that could.
const failure = ({reason, source}) => reason === 'unreachable' && T.sites[source] ? T.unreachable(T.sites[source])
  : reason === 'refused' && T.refused[source] || T.searchFailed;
const cause = ({reason, source}) => T.cause[reason](T.sites[source]);
$('#sources').innerHTML = '<span></span>' + SOURCES.map(s => `<button data-source="${s}"></button>`).join('');
function renderCands() {
  $('#sources span').textContent = T.sourcesLabel;
  for (const b of document.querySelectorAll('#sources button')) {
    b.textContent = T.sites[b.dataset.source];
    b.setAttribute('aria-pressed', data.settings.search_sources.includes(b.dataset.source));
  }
  const body = $('#add-body'), {query, found, failed, answered, waiting, offer} = online.state;
  if (!query) { body.innerHTML = ''; return; }
  if (!found.length && waiting.length) { body.innerHTML = `<div class="loading">${SPINNER}</div>`; return; }
  if (!answered.length) {  // no site could be searched: why (each reason once), and a site turned off, to search
    const why = [...new Set(failed.map(failure))].map(w => `<p>${esc(w)}</p>`).join('');
    const turnOn = offer.map(s => `<button class="offer" data-turn-on="${s}">${esc(T.searchSite(T.sites[s]))}</button>`);
    body.innerHTML = `<div class="empty">${why}${turnOn.join('')}</div>`;
    return;
  }
  const note = waiting.length ? `<div class="note">${SPINNER}</div>`
    : failed.length ? `<p class="note">${esc(T.partly(failed.map(cause), answered.map(s => T.sites[s])))}</p>` : '';
  if (!found.length) { body.innerHTML = `${note}<div class="empty">${esc(T.nothing)}</div>`; return; }
  body.innerHTML = note + found.map((c, i) => {
    const {made, job} = pieceOf(c, data.recordings, data.jobs);
    const btn = made ? `<button class="addbtn" data-play="${made.id}" aria-label="${esc(T.playNow)}">${ICON_PLAY_CIRCLE}</button>`
              : job ? ringButton(job)
              : `<button class="addbtn" data-add="${i}" aria-label="${esc(T.download)}">${ICON_DOWNLOAD}</button>`;
    const act = made ? `data-play="${made.id}"` : job ? `data-preparing="${job.id}"` : `data-add="${i}"`;
    const seen = [c.channel, c.views != null ? T.views(c.views) : ''].filter(Boolean);  // a line each, read in full
    return `<div class="cand">
        <a href="${esc(c.url)}" target="_blank" rel="noopener" aria-label="${esc(T.watchVideo)}">
          <span class="thumb" style="--h:${hueOfName(c.url)}">${c.thumbnail ? `<img src="${esc(c.thumbnail)}" alt="" referrerpolicy="no-referrer">` : ''}${T.sites[c.site] ? `<span class="site">${esc(T.sites[c.site])}</span>` : ''}<span class="dur">${mmss(c.duration_s)}</span></span>
        </a>
        <button class="ctext" ${act} aria-label="${esc(T.actionOn(made || job ? T.playNow : T.download, c.video_title))}">
          <span class="ct1">${esc(c.video_title)}</span>${seen.map(x => `<span class="ct2">${esc(x)}</span>`).join('')}
        </button>
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
  $('#add-field').hidden = $('#sources').hidden = $('#add-body').hidden = !search;
  $('#sheet-add .uploads').hidden = search;
  $('#add-input').value = q;
  uploads.tidy();
  if (search && q) searchOnline(q);
  else online.forget();
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
// Searching online, by name or by a video's link (the sheet is there with transcription only).
function searchOnline(q) {
  $('#add-scroll').scrollTop = 0;
  online.find(q, data.settings.search_sources);
}
// The sites to search, for her and every phone: kept on the Mac. The search shown asks a site turned on, and lets go
// of one turned off.
function chooseSources(sources) {
  data.settings.search_sources = SOURCES.filter(s => sources.includes(s));
  applySettings();
  api('/api/settings', 'PATCH', {search_sources: data.settings.search_sources}).catch(() => {});
}
$('#sources').addEventListener('click', e => {
  const site = e.target.closest('[data-source]')?.dataset.source, on = data.settings.search_sources;
  if (!site) return;
  if (!on.includes(site)) chooseSources([...on, site]);
  else if (on.length > 1) chooseSources(on.filter(s => s !== site));  // the last one on stays on
});
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
  // Download, from the button or the title: the button turns to a spinner and the title rests, until the job shows.
  const add = e.target.closest('[data-add]');
  if (add) {
    const row = add.closest('.cand'), c = online.state.found[+add.dataset.add], {query} = online.state;
    row.querySelector('.ctext').removeAttribute('data-add');
    row.querySelector('.addbtn').outerHTML = SPINNER;
    api('/api/downloads', 'POST', {url: c.url, query: /^https?:/i.test(query) ? '' : query, video: c}).catch(() => renderCands());
    return;
  }
  const turnOn = e.target.closest('[data-turn-on]');  // a site turned off, those on out of reach
  if (turnOn) { chooseSources([...data.settings.search_sources, turnOn.dataset.turnOn]); return; }
  const stop = e.target.closest('[data-stop]');
  if (stop) { stopJob(+stop.dataset.stop); return; }
  const preparing = e.target.closest('[data-preparing]');  // its title, as its row in the library
  if (preparing) { playPreparing(data.jobs.find(j => j.id === +preparing.dataset.preparing), preparing); return; }
  // A piece made, from ▶ or its title: it plays from the library, in the player; closing the player is back on the
  // library.
  const made = e.target.closest('[data-play]');
  if (made) {
    closeOverlays();
    play(+made.dataset.play, listIds($('#library-list')), {kind: 'library'}, made);
    return;
  }
  if (e.target.closest('.cand > a')) leaveForVideo();
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
  const id = state.session === 'now' ? null : state.session, waiting = {text, session: state.session};
  sent.push(waiting);
  drawChat(state.session);
  api('/api/messages', 'POST', {text, conversation: id}).catch(() => {
    if (sent.includes(waiting)) sent.splice(sent.indexOf(waiting), 1);
    if (state.view === 'session') drawChat(state.session);
    input.value = text;
    fitInput();
  });
  toLatest();
}
// The field grows with what you write, up to five lines; ↑ comes in beside the mic once there is something to send.
function fitInput() {
  input.style.height = '';
  input.style.height = Math.min(120, input.scrollHeight) + 'px';
  $('#compose-send').hidden = !input.value.trim();
}
input.addEventListener('input', fitInput);
// While the phone's own keyboard is on the screen, its return writes a new line, as in Messages, and ↑ sends; with a
// keyboard of your own (a computer's, or one for an iPad or a phone: none on the screen), return sends, and
// shift-return writes a new line. The keyboard on the screen is the visible area it takes (fitKeyboard).
const onScreenKeyboard = () => !!vv && innerHeight - vv.height > 80;
input.addEventListener('keydown', e => {
  if (isEnter(e) && !e.shiftKey && !onScreenKeyboard()) { e.preventDefault(); sendText(); }
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
  let at = 0, text;  // stays undefined when not heard: the Mac out of reach, or its recognition failing
  for (const c of d.chunks) { audio.set(c, at); at += c.length; }
  try { ({text} = await (await fetch('/api/dictation', {method: 'POST', body: audio.buffer, signal: d.abort.signal})).json()); } catch {}
  if (dictation !== d) return;  // dropped with ✕, or you left the conversation
  endDictation();
  if (text) insertText(text);
  else if (text === undefined) hud(T.dictationFailed);
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
  // It lands where the orb at home will be once home shows, in the middle of the screen: the page's, lifted as
  // centerOrb lifts it (the orb at home may be off to the side now, with the sidebar out).
  const r = $('#pull .porb').getBoundingClientRect(), size = $('#orb').offsetWidth;
  const top = (app.clientHeight - size) / 2 - (parseFloat(app.style.getPropertyValue('--lift')) || 0);
  const home = {left: (app.clientWidth - size) / 2, top, width: size, height: size};
  const dark = $('#to-voice'), fly = $('#fly-orb');
  Object.assign(fly.style, {left: home.left + 'px', top: home.top + 'px', width: home.width + 'px', height: home.height + 'px'});
  dark.hidden = fly.hidden = false;
  flying = true;
  sameLook(fly, $('#pull .porb'));
  const dx = r.left + r.width / 2 - (home.left + home.width / 2), dy = r.top + r.height / 2 - (home.top + home.height / 2);
  fly.animate([{transform: `translate(${dx}px, ${dy}px) scale(${r.width / home.width})`, offset: 0}],  // from the pull,
    {duration: 560, easing: 'cubic-bezier(.32, .72, 0, 1)'});  // to its own size, which is home's
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
  let x0 = 0, sure = false;  // sure: it went up before it went sideways
  const begin = (x, y, target) => {  // not again while the orb is still flying home
    x0 = x; sure = false; y0 = !flying && atEnd() && state.view === 'session' && !target.closest('.compose') ? y : null;
  };
  const move = (x, y, e) => {
    if (y0 === null) return;
    const up = y0 - y;
    if (!sure) {
      if (Math.abs(x - x0) > 10 && Math.abs(x - x0) > Math.abs(up)) { y0 = null; return; }
      sure = up > 10;
    }
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
  view.addEventListener('touchstart', e => begin(e.touches[0].clientX, e.touches[0].clientY, e.target), {passive: true});
  view.addEventListener('touchmove', e => move(e.touches[0].clientX, e.touches[0].clientY, e), {passive: false});
  view.addEventListener('touchend', end);
  view.addEventListener('touchcancel', end);
  view.addEventListener('mousedown', e => begin(e.clientX, e.clientY, e.target));
  addEventListener('mousemove', e => { if (e.buttons) move(e.clientX, e.clientY); });
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
  if (device) info(...(T.connectHow[device.kind] || T.connectHow.other));
  if (e.target.closest('#open-help')) push('help');
});
// Her topics or the basic ones, as she is on or off; and of them, the library's as online search is (each set has the
// one for its usual case: hers with it, the basic without).
const topics = () => {
  const library = (data.features.transcribe ? T.helpTopics : T.basicTopics).find(t => t.id === 'library');
  return (herOn() ? T.helpTopics : T.basicTopics).map(t => t.id === 'library' ? library : t);
};
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
// A full sheet pushes the screen behind it back, as on iOS; a half sheet (播放列表, 添加到歌单) leaves it where it is.
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
  if (crop) endCrop(false);  // the sheet it was choosing for goes
  openSheet?.classList.remove('show');
  openSheet = null;
  naming = confirmed = null;
  app.classList.remove('sheeted');
  for (const id of ['#dim', '#confirm', '#add-how', '#rename', '#info']) $(id).classList.remove('show');
  $('#dim').classList.remove('over');
  document.activeElement?.blur?.();
}
const overlayOpen = () => openSheet || ['#confirm', '#add-how', '#rename', '#info'].some(id => $(id).classList.contains('show'));
// 取消, a tap beside or Escape: an alert over a sheet goes, the sheet stays; anything else, all of it.
const dismiss = () => $('#dim').classList.contains('over') ? closeAlert() : closeOverlays();
// Something to know, with one button, as iOS says it.
function info(title, text) {
  $('#info-title').textContent = title;
  $('#info-text').textContent = text;
  $('#dim').classList.add('show');
  $('#info').classList.add('show');
}
$('#info-ok').addEventListener('click', closeOverlays);

$('#dim').addEventListener('click', dismiss);

// Long-press menu, for a piece, a playlist or a conversation: the row lifts and its actions show by it. With html null
// it is a pull-down menu instead (a setting's choices, a page's …), opening from the right of what was tapped.
// items: [label, icon, action, danger]; falsy items are left out, and the danger ones go last.
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
  // What cannot be undone comes last, in a group of its own, as iOS keeps it.
  menu.innerHTML = items.map(([label, icon, , danger], i) =>
    `<button data-i="${i}"${danger ? ` class="danger${i && !items[i - 1][3] ? ' apart' : ''}"` : ''}>${esc(label)}${icon}</button>`).join('');
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

// A name in a text field, as iOS asks for one; over a sheet (新建歌单 from 添加到歌单), the sheet stays under it.
// needed: saved only with a name (a playlist's), else an empty one is a name too (a conversation's: its own again).
let naming = null, nameNeeded = false;
function askName(title, value, save, needed = false) {
  naming = save;
  nameNeeded = needed;
  $('#rename-title').textContent = title;
  const input = $('#rename-input');
  input.value = value;
  fitSave();
  $('#dim').classList.add('show');
  $('#dim').classList.toggle('over', !!openSheet);
  $('#rename').classList.add('show');
  input.focus();
  input.select();
}
const fitSave = () => { $('#rename-save').disabled = nameNeeded && !$('#rename-input').value.trim(); };
$('#rename-input').addEventListener('input', fitSave);
function saveName() {
  const save = naming, value = $('#rename-input').value.trim();
  if (nameNeeded && !value) return;
  closeOverlays();
  save?.(value);
}
function closeAlert() {
  naming = null;
  $('#rename').classList.remove('show');
  $('#dim').classList.remove('over');
  document.activeElement?.blur?.();
}
$('#rename-cancel').addEventListener('click', dismiss);
$('#rename-save').addEventListener('click', saveName);
$('#rename-input').addEventListener('keydown', e => { if (isEnter(e)) saveName(); });

// A sheet goes down with the finger from its top (the grabber and the header), and away past a third of its height or
// with a flick, as on iOS (away is as 取消: nothing kept); short of that it goes back up.
(function sheetDrag() {
  let g = null;
  for (const sheet of document.querySelectorAll('.sheet')) {
    sheet.addEventListener('pointerdown', e => {
      if (e.button > 0 || !e.isPrimary || !e.target.closest('.grabber, .sheet-head') || e.target.closest('button, input')) return;
      g = {id: e.pointerId, y0: e.clientY, dy: 0, v: 0, t: performance.now(), on: false};
    });
    sheet.addEventListener('pointermove', e => {
      if (!g || e.pointerId !== g.id) return;
      const dy = Math.max(0, e.clientY - g.y0), now = performance.now();
      if (!g.on) {
        if (dy < 6) return;
        g.on = true;
        sheet.setPointerCapture(e.pointerId);
        sheet.style.transition = 'none';
      }
      g.v = (dy - g.dy) / Math.max(1, now - g.t);
      Object.assign(g, {dy, t: now});
      sheet.style.transform = `translateY(${dy}px)`;
    });
    const end = e => {
      if (!g || e.pointerId !== g.id) return;
      const {on, dy} = g, v = performance.now() - g.t > 80 ? 0 : g.v;
      g = null;
      if (!on) return;
      sheet.style.transition = sheet.style.transform = '';  // from where it is, away or back
      if (dy > sheet.offsetHeight / 3 || (dy > FLICK_PX && v > .5)) closeOverlays();
      guardUntil = performance.now() + 300;
    };
    sheet.addEventListener('pointerup', end);
    sheet.addEventListener('pointercancel', end);
  }
})();

// ---------- Going around: the sidebar, the pages, the player ----------
// No buttons for going around. Home is the orb: a right swipe opens the sidebar beside it (a left swipe closes it
// again), a left swipe brings in the player. On any page, the player included, a right swipe takes it away.
// Whatever moves follows the finger, as on iOS. Away from the orb she keeps quiet (voice.hold).
const sidebarWidth = () => app.clientWidth * .78;
// A flick goes on its way only once the finger has really moved: a touch that wobbles is not one.
const FLICK_PX = 40, flicked = (v, dx) => Math.abs(dx) >= FLICK_PX && v * Math.sign(dx) > .3;
function setOpen(open) {
  state.open = open;
  voice?.hold(open || stack.length > 0);
  app.classList.remove('dragging');
  app.classList.toggle('open', open);
  app.style.setProperty('--p', open ? 1 : 0);
  if (open) {
    setSelecting(false);  // the sidebar is somewhere else
    document.querySelectorAll('.swipe.open').forEach(r => r.classList.remove('open'));
  } else stopSearch();
  renderNow();
}
let drag = null;
app.addEventListener('pointerdown', e => {
  if (e.button > 0 || !e.isPrimary || overlayOpen() || $('#ctx').classList.contains('show') || e.target.closest('.swipe.open, .scrub, textarea, #orb, .grip')) return;
  drag = {id: e.pointerId, x0: e.clientX, y0: e.clientY, p0: state.open ? 1 : 0, p: state.open ? 1 : 0, v: 0, t: performance.now(), lx: e.clientX,
          active: false, page: null, player: null, dx: 0,
          row: !!e.target.closest('#main .swipe')};  // at the library home, a left swipe on a row is the row's
});
app.addEventListener('pointermove', e => {
  if (!drag || e.pointerId !== drag.id) return;
  if ($('#ctx').classList.contains('show')) { drag = null; return; }  // a long-press took over
  const dx = e.clientX - drag.x0, dy = e.clientY - drag.y0;
  if (!drag.active) {
    if (Math.abs(dy) > 10 && Math.abs(dy) > Math.abs(dx)) { drag = null; return; }
    if (Math.abs(dx) < 10) return;
    drag.page = stack.length ? $('#view-' + stack.at(-1).view) : null;  // the top page as the drag starts (one may come in after the press)
    const goes = drag.page ? dx > 0 : drag.p0 ? dx < 0 : dx > 0 || (!!nowPiece() && !drag.row);
    if (!goes) { drag = null; return; }
    drag.active = true;
    if (!drag.page && !drag.p0 && dx < 0) motion(drag.player = ready('player'), 'coming');
    if (drag.page) motion(drag.page, 'going');
    app.setPointerCapture(e.pointerId);
    app.classList.add('dragging');
  }
  const now = performance.now();
  drag.v = (e.clientX - drag.lx) / Math.max(1, now - drag.t);
  drag.lx = e.clientX; drag.t = now;
  if (drag.player) {
    drag.dx = Math.max(-app.offsetWidth, Math.min(0, dx));  // no further than its place
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
    const v = performance.now() - drag.t > 80 ? 0 : drag.v;  // a finger that stopped before letting go did not flick
    const top = stack.length ? $('#view-' + stack.at(-1).view) : null;
    if (drag.player) {
      if (drag.dx < -90 || flicked(v, drag.dx)) push('player', null, drag.player);
      else slideOut(drag.player);
    } else if (drag.page) {
      if (drag.page === top && (drag.dx > 90 || flicked(v, drag.dx))) pop();
      else { moveAside(drag.page, null); atRest(drag.page); $('#mini').style.transform = ''; }  // back (another page came in meanwhile: it stays)
    } else {
      const moved = (drag.p - drag.p0) * sidebarWidth();
      setOpen(flicked(v, moved) ? v > 0 : drag.p > .5);
    }
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
    if (!row || e.button > 0 || !e.isPrimary || state.selecting || e.target.closest('.grip')) return;
    cancel();
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
// ask(id): what to ask before remove, which cannot be undone; the row folds away once it is confirmed.
function rowGestures(list, {tap, remove, hold, ask}) {
  if (hold) onHold(list, '.swipe:not(.open)', row => hold(row.dataset.id, row));
  let s = null;
  list.addEventListener('pointerdown', e => {
    const row = e.target.closest('.swipe:not(.fixed)');
    if (!row || e.button > 0 || !e.isPrimary || e.target.closest('.grip')) return;
    rowFinger = e.pointerId;  // choosing too: the row tapped stays the row tapped
    if (state.selecting) return;
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
    if (e.target.closest('.del')) {
      const go = () => collapse([row], () => remove(row.dataset.id));
      if (ask) openAction(ask(row.dataset.id), go);
      else go();
      return;
    }
    const open = list.querySelector('.swipe.open');
    if (open) { open.classList.remove('open'); return; }  // like iOS: a tap first closes an open row
    tap(row.dataset.id, row);
  });
}
const isJob = key => String(key).startsWith('j'), isAudio = key => String(key).startsWith('a');
rowGestures($('#library-list'), {
  tap: (id, row) => isJob(id) ? state.selecting || playPreparing(data.jobs.find(j => jobKey(j) === id), row)
    : isAudio(id) && (state.selecting || data.features.transcribe) ? null
    : isAudio(id) ? audioInfo() : state.selecting ? toggleSelect(+id) : play(+id, listIds($('#library-list')), {kind: 'library'}, row),
  remove: id => isJob(id) ? stopJob(+id.slice(1)) : isAudio(id) ? trashAudio(+id.slice(1)) : deletePieces([+id]),
  hold: (id, row) => pieceMenu(id, row),
});
$('#library-list').addEventListener('click', e => {
  const b = e.target.closest('[data-transcribe]');
  if (b) { e.stopPropagation(); b.outerHTML = SPINNER; transcribeAudio(+b.dataset.transcribe); }
}, true);
rowGestures($('#group-list'), {
  tap: (id, row) => state.selecting ? toggleSelect(+id) : play(+id, listIds($('#group-list')), state.session, row),
  remove: id => {
    const kind = state.session.kind;
    if (kind === 'favorites') favorite([+id], false);
    else if (kind === 'playlist') removeFromPlaylist(playlistById(state.session.id), [+id]);
    else if (kind === 'recent') forgetPlay([+id]);
    else deletePieces([+id]);
  },
  hold: (id, row) => pieceMenu(id, row),
});
rowGestures($('#playlist-list'), {
  tap: id => push('group', {kind: 'playlist', id: +id}),
  remove: id => deletePlaylist(+id),
  ask: () => T.deletePlaylist,  // it cannot be undone: asked first (its pieces stay)
  hold: (id, row) => {
    const pl = playlistById(id);
    if (pl) playlistMenu(pl, row, cover(pl) + playlistText(pl));
  },
});
const shownPlaylist = () => state.session?.kind === 'playlist' && playlistById(state.session.id);
dragToReorder($('#group-list'), () => shownPlaylist()?.ids, (id, at, ids) => {
  const pl = shownPlaylist();
  pl.ids = ids;
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
  list.addEventListener('pointerdown', e => { if (!e.button && e.isPrimary) s = {id: e.pointerId, x0: e.clientX, y0: e.clientY, on: false}; });
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
  if (crop) endCrop(false);
  else if ($('#ctx').classList.contains('show')) closeCtx();
  else if (overlayOpen()) dismiss();
  else if (state.selecting) setSelecting(false);
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
addOrb($('#fly-orb'), homeColor, () => flying, true);
addOrb($('#her-orb'), () => !voice.off(), () => herOn() && state.view === 'settings');

applyFeatures();
applySettings();
requestAnimationFrame(playClock);
signIn();
