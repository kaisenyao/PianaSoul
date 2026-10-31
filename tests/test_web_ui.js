// Her ears and voice on the phone (web/voice.js), with browser, microphone, network and audio replaced.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('src/pianasoul/web/voice.js', 'utf8').replace(/^export /gm, '');
const tick = async () => { for (let i = 0; i < 40; i++) await Promise.resolve(); };
const PIECE = Buffer.alloc(4800).toString('base64'); // 0.1 s of 24 kHz PCM16

function harness(options = {}) {
  const h = {options, tracks: [], sockets: [], contexts: [], clips: [], sources: [], nodes: [],
    cues: [], cueEnds: [], levels: [], vibrations: [], locks: [], animations: 0, mics: 0, timers: new Map()};
  // Like DOMStringMap, the dataset stores strings.
  const dataset = new Proxy({}, {set: (target, key, value) => { target[key] = String(value); return true; }});
  const orb = h.orb = {dataset, animate() { h.animations++; }};
  const toggle = h.toggle = {checked: false, setAttribute(name, value) { this[name] = value; }};
  const node = () => ({connect: next => next, disconnect() { this.disconnected = true; }});
  class Context {
    constructor() {
      this.state = 'suspended';
      this.currentTime = 5;
      this.audioWorklet = {addModule: async () => {}};
      h.contexts.push(this);
    }
    resume() {
      if (options.audioAsleep && !h.awake) return new Promise(() => {}); // as iOS may leave it after a long lock
      this.state = 'running';
      return Promise.resolve();
    }
    close() { this.state = 'closed'; return Promise.resolve(); }
    createAnalyser() { return {...node(), getByteTimeDomainData: samples => samples.fill(160)}; }
    createGain() { return {...node(), gain: {}}; }
    createMediaStreamSource(stream) { const item = {...node(), stream}; h.sources.push(item); return item; }
    createBuffer(channels, length, rate) {
      const samples = new Float32Array(length);
      return {duration: length / rate, getChannelData: () => samples};
    }
    createBufferSource() {
      const clip = {...node(), start(at) { clip.at = at; }, stop() { clip.stopped = true; clip.onended?.(); }};
      h.clips.push(clip);
      return clip;
    }
  }
  class Socket {
    static OPEN = 1;
    constructor() {
      this.readyState = 1;
      this.bufferedAmount = 0;
      this.sent = [];
      this.frames = 0;
      h.sockets.push(this);
      // Signed in by the page's cookie: the Mac gets ready on its own.
      queueMicrotask(() => { if (!options.slowServer) { this.event({type: 'ready'}); this.event({type: 'state', state: 'waiting'}); } });
    }
    send(raw) {
      if (typeof raw !== 'string') { this.frames++; return; }
      this.sent.push(JSON.parse(raw).type);
    }
    event(msg) { this.onmessage({data: typeof msg === 'string' ? msg : JSON.stringify(msg)}); }
    close() { this.closed = true; }
  }
  const events = {};
  const cues = {
    unlock() { assert.ok(h.inTap, 'Cue audio is unlocked inside the tap'); },
    stop() { h.cues.push('stop'); },
    suspend() { h.cues.push('suspend'); },
    play(name) { h.cues.push(name); return new Promise(resolve => h.cueEnds.push({name, resolve})); },
  };
  const sandbox = h.sandbox = {
    matchMedia: () => ({matches: false}),
    document: {hidden: false, addEventListener: (name, fn) => { events[name] = fn; }},
    addEventListener: (name, fn) => { events[name] = fn; },
    location: {protocol: 'https:', host: 'mac.local'},
    WebSocket: Socket, AudioContext: Context, queueMicrotask,
    AudioWorkletNode: class {
      constructor() { this.port = {postMessage: on => { this.capturing = on; }}; h.nodes.push(this); }
      connect(next) { return next; }
    },
    atob: value => Buffer.from(value, 'base64').toString('binary'),
    navigator: {vibrate: ms => h.vibrations.push(ms), wakeLock: options.noWakeLock ? undefined : {request: async type => {
      assert.ok(h.inTap, 'The screen lock is asked for inside the tap');
      const lock = {type, released: false, release: async () => { lock.released = true; }};
      h.locks.push(lock);
      if (options.slowLock) await new Promise(resolve => { h.grantLock = async () => { resolve(); await tick(); }; });
      return lock;
    }}, mediaDevices: {getUserMedia: async () => {
      h.mics++;
      if (options.holdMic) await new Promise(resolve => { h.grantMic = resolve; });
      if (options.denyMic) throw Error('denied');
      const track = {enabled: true, readyState: 'live', stop() { track.readyState = 'ended'; }};
      h.tracks.push(track);
      return {getTracks: () => [track], getAudioTracks: () => [track]};
    }}},
    window: {isSecureContext: options.insecure !== true},
    setTimeout: (fn, ms) => { const id = Symbol(); h.timers.set(id, {fn, ms}); return id; },
    clearTimeout: id => h.timers.delete(id),
    requestAnimationFrame: () => 1, cancelAnimationFrame() {},
  };
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox);
  const labels = () => ({idle: 'off', starting: 'starting', active: 'on'});
  h.voice = sandbox.voice(orb, toggle, {level: value => h.levels.push(value)}, cues, labels);
  const gesture = async fn => {
    h.inTap = true;
    try { fn(); } finally { h.inTap = false; }
    await tick();
  };
  h.tap = () => gesture(() => { toggle.checked = !toggle.checked; toggle.onchange(); }); // the switch flips itself first
  h.on = () => gesture(() => h.voice.on());
  h.hide = () => { sandbox.document.hidden = true; events.visibilitychange(); };
  sandbox.dispatch = name => events[name]();
  h.socket = () => h.sockets.at(-1);
  h.ctx = () => h.contexts.at(-1);
  h.track = () => h.tracks.at(-1);
  h.active = () => orb.dataset.active;
  h.state = state => h.socket().event({type: 'state', state});
  h.say = () => h.socket().event({type: 'audio', data: PIECE});
  h.frame = (level = .2) => h.nodes[0].port.onmessage({data: {pcm: new ArrayBuffer(2560), level}});
  h.endCue = async () => { h.cueEnds.findLast(cue => cue.name === 'end').resolve(); await tick(); };
  h.fireTimer = async ms => {
    for (const [id, timer] of h.timers) if (timer.ms === ms) { h.timers.delete(id); timer.fn(); }
    await tick();
  };
  return h;
}

(async () => {
  // Opening the page: gray and silent, nothing requested; elsewhere she is shown in color, nobody turned her off.
  const h = harness();
  assert.equal(h.active(), 'false');
  assert.equal(h.toggle['aria-label'], 'off');
  assert.equal(h.mics + h.sockets.length + h.locks.length + h.cues.length + h.vibrations.length + h.animations, 0);
  assert.equal(h.voice.off(), false);

  // Tap: immediate feedback; color and the start sound only once the Mac is ready.
  const slow = harness({slowServer: true});
  await slow.tap();
  assert.equal(slow.animations, 1);
  assert.deepEqual(slow.vibrations, [12]);
  assert.equal(slow.active(), 'false');
  assert.equal(slow.orb.dataset.waking, 'true', 'Half colored while it starts');
  assert.equal(slow.cues.includes('active'), false);
  assert.equal(slow.mics, 1, 'The microphone is asked for while the Mac prepares');
  assert.equal(slow.locks.length, 1, 'The screen is kept awake from the tap');
  slow.socket().event({type: 'ready'});
  await tick();
  assert.equal(slow.active(), 'true');
  assert.equal(slow.orb.dataset.waking, 'false');
  assert.equal(slow.cues.at(-1), 'active');
  assert.equal(slow.animations, 2, 'A connected bounce');
  assert.deepEqual(slow.vibrations, [12], 'Connecting does not vibrate again');
  assert.equal(slow.timers.size, 0, 'No connection timeout is left behind');

  // Listening: audio goes to the Mac only while listening; the orb follows the voice. The Mac keeps her quiet until
  // the phone says it is with her: just turned on, so that the first request needs no name.
  await h.tap();
  assert.deepEqual(h.socket().sent, ['start_session']);
  assert.equal(h.nodes[0].capturing, true);
  assert.equal(h.track().enabled, true);
  h.frame(.2);
  assert.equal(h.socket().frames, 1);
  assert.ok(h.levels.at(-1) > 1);
  h.state('thinking');
  h.frame(.2);
  assert.equal(h.socket().frames, 1, 'Nothing is sent while she thinks or speaks');
  assert.equal(h.levels.at(-1), 0);
  h.socket().bufferedAmount = 40000;
  h.state('conversation');
  h.frame(.2);
  assert.equal(h.socket().frames, 1, 'A backed-up network drops audio instead of queueing it');
  assert.equal(h.active(), 'true');

  // Her reply arrives in pieces that play back to back; the next turn is asked for after the last.
  const talk = harness();
  await talk.tap();
  talk.state('speaking');
  talk.say();
  talk.socket().event({type: 'audio', data: '/w=='}); // not PCM16: skipped
  talk.say();
  talk.socket().event({type: 'turn_end', sleep: false});
  await tick();
  assert.equal(talk.clips.length, 2, 'A bad piece is skipped, the reply goes on');
  assert.equal(talk.clips[0].at, 5, 'The first piece plays at once');
  assert.ok(Math.abs(talk.clips[1].at - 5.1) < 1e-9, 'The next starts exactly where it ends');
  talk.clips[0].onended();
  await tick();
  assert.equal(talk.socket().sent.includes('resume'), false);
  talk.clips[1].onended();
  await tick();
  assert.equal(talk.socket().sent.at(-1), 'resume');
  talk.socket().event({type: 'turn_end', sleep: true});
  await tick();
  assert.equal(talk.socket().sent.at(-1), 'sleep');
  assert.equal(talk.active(), 'true');

  // Gray is off: quiet and private at once, then the end sound, then nothing is kept: the microphone, the
  // connection, her audio and the screen kept awake are all let go.
  const p = harness();
  await p.tap();
  const first = {track: p.track(), socket: p.socket(), ctx: p.ctx(), lock: p.locks[0]};
  await p.tap();
  assert.equal(p.active(), 'false');
  assert.equal(p.orb.dataset.state, 'idle');
  assert.equal(p.toggle['aria-label'], 'off');
  assert.equal(p.voice.off(), true, 'You turned her off: her other orbs are gray too');
  assert.equal(p.nodes[0].capturing, false);
  assert.equal(first.track.enabled, false, 'Muted at once');
  assert.equal(first.socket.sent.at(-1), 'pause_session', 'The Mac stops listening and speaking at once');
  assert.equal(p.cues.at(-1), 'end');
  p.frame(.5);
  assert.equal(first.socket.frames, 0, 'Nothing is sent while gray');
  assert.equal(first.track.readyState, 'live', 'The end sound plays before the microphone is let go');
  await p.endCue();
  assert.equal(first.track.readyState, 'ended', 'Then the microphone is let go');
  assert.equal(first.socket.closed, true, 'and the connection');
  assert.equal(first.ctx.state, 'closed');
  assert.equal(first.lock.released, true, 'and the screen may sleep again');
  assert.equal(p.cues.at(-1), 'suspend');
  // Back on: afresh.
  await p.tap();
  assert.equal(p.voice.off(), false);
  assert.equal(p.active(), 'true');
  assert.equal(p.sockets.length, 2);
  assert.equal(p.contexts.length, 2);
  assert.equal(p.mics, 2);
  assert.equal(p.locks.length, 2);
  assert.equal(p.track().enabled, true);
  assert.equal(p.cues.at(-1), 'active');

  // An end sound that never finishes still lets everything go after three seconds.
  const stuck = harness();
  await stuck.tap();
  await stuck.tap();
  assert.equal(stuck.track().readyState, 'live');
  await stuck.fireTimer(3000);
  assert.equal(stuck.track().readyState, 'ended');
  assert.equal(stuck.socket().closed, true);

  // Back on during the end sound: the sound fades, what was being let go goes at once, and she starts afresh; the
  // late end sound and its timer leave the new start alone.
  const quick = harness();
  await quick.tap();
  const old = {track: quick.track(), socket: quick.socket()};
  await quick.tap();
  await quick.tap();
  assert.equal(quick.cues.slice(-2).join(), 'stop,active');
  assert.equal(old.track.readyState, 'ended');
  assert.equal(old.socket.closed, true);
  assert.equal(quick.sockets.length, 2);
  await quick.endCue();
  await quick.fireTimer(3000);
  assert.equal(quick.active(), 'true');
  assert.equal(quick.track().readyState, 'live');
  assert.equal(quick.socket().closed, undefined);
  assert.equal(quick.cues.includes('suspend'), false, 'The sounds stay ready');

  // Gray while she speaks: she stops mid-word, what was already on its way is not played, and that turn does not
  // ask for the next. Back on, she starts afresh and a new reply plays at once.
  const hush = harness();
  await hush.tap();
  hush.state('speaking');
  hush.say();
  await hush.tap();
  assert.equal(hush.clips[0].stopped, true, 'Stopped, not frozen');
  hush.say(); // sent by the Mac before it heard
  hush.socket().event({type: 'turn_end', sleep: false});
  await tick();
  assert.equal(hush.clips.length, 1, 'Nothing more is played');
  assert.equal(hush.socket().sent.includes('resume'), false);
  await hush.tap();
  hush.say();
  assert.equal(hush.clips.length, 2, 'A new reply plays');
  assert.equal(hush.clips[1].at, 5, 'At once');

  // Away from the orb: the same quiet, but she stays on (and the screen awake), and coming back she listens again.
  const away = harness();
  await away.tap();
  away.state('speaking');
  away.say();
  away.voice.hold(true);
  assert.equal(away.active(), 'true', 'You left her on');
  assert.equal(away.voice.off(), false);
  assert.equal(away.clips[0].stopped, true);
  assert.equal(away.nodes[0].capturing, false);
  assert.equal(away.track().enabled, false);
  assert.equal(away.socket().sent.at(-1), 'pause_session');
  assert.equal(away.locks[0].released, false);
  away.say();
  away.frame(.5);
  assert.equal(away.clips.length, 1);
  assert.equal(away.socket().frames, 0);
  away.voice.hold(true);
  assert.equal(away.socket().sent.filter(type => type === 'pause_session').length, 1, 'Once');
  away.voice.hold(false);
  assert.equal(away.socket().sent.at(-1), 'continue_session', 'Back on the orb: not turned on again');
  assert.equal(away.nodes[0].capturing, true);
  assert.equal(away.track().enabled, true);
  // Turned on while away (新对话 in the sidebar, say): on, and quiet until back.
  const elsewhere = harness();
  elsewhere.voice.hold(true);
  await elsewhere.on();
  assert.equal(elsewhere.active(), 'true');
  assert.equal(elsewhere.nodes[0].capturing, false);
  assert.deepEqual(elsewhere.socket().sent, [], 'Quiet as the Mac keeps her until the phone is with her');
  elsewhere.voice.hold(false);
  assert.deepEqual(elsewhere.socket().sent, ['start_session'], 'With her for the first time since turned on');
  assert.equal(elsewhere.nodes[0].capturing, true);

  // on() from elsewhere: starts her, also after she was turned gray; while she is on it does nothing.
  const again = harness();
  await again.tap();
  await again.tap();
  await again.on();
  assert.equal(again.active(), 'true');
  assert.equal(again.voice.off(), false);
  await again.on();
  assert.equal(again.active(), 'true');
  assert.equal(again.sockets.length, 2);

  // The microphone refused: gray, nothing kept; a tap tries again.
  const denied = harness({denyMic: true});
  await denied.tap();
  assert.equal(denied.active(), 'false');
  assert.equal(denied.socket().closed, true);
  assert.equal(denied.locks[0].released, true);
  denied.options.denyMic = false;
  await denied.tap();
  assert.equal(denied.active(), 'true');

  // Ended while the microphone is still being asked for: the late stream is stopped at once.
  const late = harness({holdMic: true});
  await late.tap();
  late.hide();
  late.grantMic();
  await tick();
  assert.equal(late.track().readyState, 'ended');
  assert.equal(late.active(), 'false');

  // The screen is kept awake from the tap (asked for inside it) while she is on; a lock that arrives after she is
  // off is let go at once; a phone that cannot keep its screen awake still has her.
  const slowLock = harness({slowLock: true});
  await slowLock.tap();
  slowLock.hide();
  await slowLock.grantLock();
  assert.equal(slowLock.locks[0].released, true);
  const noLock = harness({noWakeLock: true});
  await noLock.tap();
  assert.equal(noLock.active(), 'true');
  await noLock.tap();
  await noLock.endCue();
  assert.equal(noLock.socket().closed, true);

  // A slow first start: more taps each give feedback, but do not cancel it.
  const eager = harness({holdMic: true});
  for (let i = 0; i < 4; i++) await eager.tap();
  assert.equal(eager.animations, 4, 'Every tap is felt');
  assert.deepEqual(eager.vibrations, [12, 12, 12, 12]);
  assert.equal(eager.sockets.length, 1);
  assert.equal(eager.socket().closed, undefined);
  eager.grantMic();
  await tick();
  assert.equal(eager.active(), 'true', 'However many taps, it comes on');
  assert.equal(eager.track().readyState, 'live');
  await eager.tap();
  assert.equal(eager.active(), 'false', 'Once on, one tap turns her gray');

  // Unexpected messages are ignored; the Mac ends a conversation by closing the connection.
  const odd = harness();
  await odd.tap();
  for (const msg of ['not json', 'null', {type: 'error'}, {type: 'session', paused: true}, {type: 'state', state: 'x'}]) odd.socket().event(msg);
  await tick();
  assert.equal(odd.active(), 'true');
  assert.equal(odd.socket().closed, undefined);

  // Ends: background, page closed, a revoked microphone, a lost connection (or a refused cookie).
  for (const ending of [
    x => x.hide(),
    x => x.sandbox.dispatch('pagehide'),
    x => x.track().onended(),
    x => x.socket().onclose({code: 1008, reason: ''}),
  ]) {
    const x = harness();
    await x.tap();
    ending(x);
    await tick();
    assert.equal(x.active(), 'false');
    assert.equal(x.voice.off(), false, 'Not your choice: elsewhere she is still in color');
    assert.equal(x.track().readyState, 'ended');
    assert.equal(x.socket().closed, true);
    assert.equal(x.ctx().state, 'closed');
    assert.equal(x.locks[0].released, true);
    assert.equal(x.cues.at(-1), 'suspend', 'Ending is silent');
    x.frame();
    assert.equal(x.socket().frames, 0);
  }
  const back = harness();
  await back.tap();
  back.hide();
  back.sandbox.document.hidden = false;
  await tick();
  assert.equal(back.sockets.length, 1, 'Coming back stays gray until tapped');
  // Back after a long lock, the phone's audio does not wake: she does not stay starting, and the next tap works.
  const asleep = harness({audioAsleep: true});
  await asleep.tap();
  assert.equal(asleep.orb.dataset.waking, 'true');
  await asleep.fireTimer(5000);
  assert.equal(asleep.orb.dataset.waking, 'false', 'Gray again');
  assert.equal(asleep.active(), 'false');
  assert.equal(asleep.socket().closed, true);
  asleep.awake = true;
  await asleep.tap();
  assert.equal(asleep.active(), 'true', 'The next tap starts her afresh');
  const insecure = harness({insecure: true});
  await insecure.tap();
  assert.equal(insecure.mics + insecure.sockets.length, 0, 'No microphone outside a secure page');
  assert.equal(insecure.active(), 'false');
  console.log('Voice passed: gray start, tap feedback, listening and replies, gray is off (quiet at once, all let go after the end sound), afresh when back on, away from the orb, on from elsewhere, refusals, the screen kept awake only while she is on, ends, a start whose audio never wakes.');
})().catch(error => { console.error(error); process.exitCode = 1; });
