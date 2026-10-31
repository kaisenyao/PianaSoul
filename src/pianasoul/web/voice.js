// Her ears and voice on the phone: the orb at home. Gray is off, color is on, and nothing in between is kept.
// While she is on, the microphone goes to the Mac, her replies come back as audio, and the screen stays awake. Tap
// her gray and she is off: she stops listening and speaking at once, the connection closes and the screen may sleep
// again; a tap starts her afresh. A request she is answering is still answered: in the conversation, and aloud too if
// you are back with her by then. The piano is not the orb's: it plays on. Away from the orb (hold) she keeps quiet,
// and stays on: you left her on. Elsewhere (her orb in 设置) she shows your choice: in color unless you turned her gray.
const STATES = ['waiting', 'listening', 'thinking', 'speaking', 'conversation'];
const LISTENING = ['waiting', 'listening', 'conversation'];
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const STEP_MS = 5000; // the phone's audio waking and its worklet loading: quick, or stuck

// element: the orb; toggle: the native switch over it; orb: its animation; cues: the start and end sounds;
// labels(): the switch's words for each phase, in the app's language.
export function voice(element, toggle, orb, cues, labels) {
  // idle (gray) → starting → active → idle. `session` holds one connection's resources, from the tap that starts it
  // until they are all let go (just after she turns gray: see stop); every async step checks it is still current.
  let phase = 'idle', session = null, held = false, off = false;

  function render() {
    element.dataset.active = phase === 'active';
    element.dataset.waking = phase === 'starting';
    element.dataset.state = phase === 'idle' ? 'idle' : session.state;
    toggle.setAttribute('aria-label', labels()[phase]);
  }

  function feedback(connected = false) {
    if (!matchMedia('(prefers-reduced-motion: reduce)').matches) {
      element.animate?.(connected
        ? [{scale: 1}, {scale: .96, offset: .25}, {scale: 1.07, offset: .6}, {scale: 1}]
        : [{scale: 1}, {scale: .95}, {scale: 1}],
      {duration: connected ? 460 : 180, easing: 'ease-in-out'});
    }
    // The native switch gives its own haptic on iOS 18+; elsewhere, a short vibration.
    if (!connected) navigator.vibrate?.(12);
  }

  function stopTracks(stream) {
    stream?.getTracks().forEach(track => track.stop());
  }

  async function openMicrophone(s) {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true},
    });
    const [track] = stream.getAudioTracks();
    if (s !== session) { stopTracks(stream); return false; } // ended meanwhile
    if (track?.readyState !== 'live') { stopTracks(stream); throw Error('Microphone unavailable'); }
    track.enabled = false; // silent until activate()
    track.onended = () => { if (s.mic === stream) end(s, 'the microphone was taken'); }; // revoked, or by a call
    s.mic = stream;
    return true;
  }

  // Nothing leaves the phone unless capture is on: the track is muted and the worklet discards input.
  function capture(s, on) {
    s.mic?.getAudioTracks().forEach(track => { track.enabled = on; });
    s.node?.port.postMessage(on);
  }

  function send(s, type) {
    if (s.socket.readyState === WebSocket.OPEN) s.socket.send(JSON.stringify({type}));
  }

  // Quiet at once: what she was saying stops, and that turn no longer asks for the next.
  function silence(s) {
    s.turn++;
    for (const clip of s.clips) { try { clip.stop(); } catch {} }
    s.clips.clear();
    s.until = 0;
    orb.level(0);
  }

  // Everything one connection holds: the Mac, the screen kept awake, the microphone and the audio of her voice. The
  // Mac is told why (its details show it).
  function release(s, why) {
    clearTimeout(s.timeout);
    cancelAnimationFrame(s.frame);
    s.socket?.close(1000, why);
    s.wakeLock?.release().catch(() => {});
    stopTracks(s.mic);
    s.ctx?.close().catch(() => {});
  }

  // Off, whatever the reason: gray, nothing kept, and the sounds' audio at rest.
  function end(s = session, why = 'turned off') {
    if (!s || s !== session) return;
    session = null;
    phase = 'idle';
    release(s, why);
    cues.suspend();
    orb.level(0);
    render();
  }

  // The Mac keeps her quiet until the phone is with her: on, and on the orb. The first time since she was turned on,
  // the phone says so, and she takes that request without her name; back on the orb later, she waits for it.
  function withHer(s) {
    send(s, s.fresh ? 'start_session' : 'continue_session');
    s.fresh = false;
  }
  function activate(s) {
    phase = 'active';
    capture(s, !held);
    if (!held) withHer(s);
    render();
    cues.play('active');
  }

  // Tapped gray: off at once (nothing more leaves the phone, she stops mid-word, the Mac stops listening and
  // speaking), then the end sound, then the rest is let go: letting go of the microphone reconfigures iOS audio,
  // which would swallow the sound.
  function stop(s) {
    phase = 'idle';
    capture(s, false);
    silence(s);
    send(s, 'pause_session');
    render();
    Promise.race([cues.play('end'), delay(3000)]).then(() => end(s));
  }

  // Her voice arrives in pieces of 24 kHz PCM16 while it is being made; they play back to back.
  function play(s, data) {
    const pcm = new Int16Array(Uint8Array.from(atob(data), c => c.charCodeAt(0)).buffer);
    const audio = s.ctx.createBuffer(1, pcm.length, 24000);
    audio.getChannelData(0).set(Float32Array.from(pcm, value => value / 32768));
    const clip = s.ctx.createBufferSource();
    clip.buffer = audio;
    clip.connect(s.analyser);
    const at = Math.max(s.ctx.currentTime, s.until);
    s.until = at + audio.duration;
    s.clips.add(clip);
    s.spoken = new Promise(resolve => {
      clip.onended = () => { clip.disconnect(); s.clips.delete(clip); resolve(); };
    });
    clip.start(at);
  }

  function onFrame(s, {pcm, level}) {
    if (s !== session || phase !== 'active' || held) return;
    const listening = LISTENING.includes(s.state);
    if (!s.clips.size) orb.level(listening ? (level - .008) * 8 : 0);
    // Drop rather than queue audio when the network falls about a second behind.
    if (listening && s.socket.readyState === WebSocket.OPEN && s.socket.bufferedAmount < 32000) s.socket.send(pcm);
  }

  function meter(s) {
    if (s !== session) return;
    if (phase === 'active' && s.clips.size) {
      s.analyser.getByteTimeDomainData(s.samples);
      let sum = 0;
      for (const value of s.samples) sum += ((value - 128) / 128) ** 2;
      orb.level(Math.sqrt(sum / s.samples.length) * 8);
    }
    s.frame = requestAnimationFrame(() => meter(s));
  }

  function connect(s) {
    return new Promise((resolve, reject) => {
      const socket = s.socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/voice`);
      s.timeout = setTimeout(() => end(s, 'the Mac did not get ready'), 120000); // the first start loads models
      socket.onclose = () => {
        reject(Error('Connection closed'));
        end(s, 'the connection closed');
      };
      // Anything unexpected is ignored: the Mac ends a conversation by closing the connection. While she is quiet,
      // what was already on its way from the Mac is not played.
      socket.onmessage = ({data}) => {
        if (s !== session) return;
        let msg = null;
        try { msg = JSON.parse(data); } catch {}
        const quiet = phase !== 'active' || held;
        if (msg?.type === 'ready') {
          clearTimeout(s.timeout);
          resolve();
        } else if (msg?.type === 'state' && STATES.includes(msg.state)) {
          s.state = msg.state;
          render();
        } else if (msg?.type === 'audio' && typeof msg.data === 'string' && !quiet) {
          try { play(s, msg.data); } catch {} // a bad piece is skipped
        } else if (msg?.type === 'turn_end' && !quiet) {
          // Ask for the next turn only once her reply has actually been heard.
          const turn = s.turn;
          s.spoken.then(() => { if (s === session && s.turn === turn) send(s, msg.sleep ? 'sleep' : 'resume'); });
        }
      };
    });
  }

  // A step of the phone's own that never ends (iOS can leave its audio asleep after a long lock) ends her start
  // instead: gray again, so that the next tap starts afresh rather than finding her still starting.
  function inTime(step) {
    let timer;
    return Promise.race([step, new Promise((_, reject) => { timer = setTimeout(() => reject(Error('stuck')), STEP_MS); })])
      .finally(() => clearTimeout(timer));
  }

  async function start() {
    if (!window.isSecureContext || !navigator.mediaDevices) return render();
    if (session) release(session, 'turned on again'); // still letting go after its end sound: no longer
    const s = session = {turn: 0, state: 'idle', clips: new Set(), until: 0, spoken: Promise.resolve(), fresh: true};
    phase = 'starting';
    render();
    // The screen stays awake while she is on (where the system allows it), and sleeps as usual once she is off.
    navigator.wakeLock?.request('screen').then(lock => {
      if (s === session) s.wakeLock = lock;
      else lock.release().catch(() => {});
    }, () => {});
    try {
      s.ctx = new AudioContext();
      const resumed = s.ctx.resume(); // inside the tap
      const ready = connect(s); // the Mac prepares while the phone asks for the microphone
      ready.catch(() => {});
      await inTime(resumed);
      if (!await openMicrophone(s)) return;
      await inTime(s.ctx.audioWorklet.addModule('/capture.js'));
      if (s !== session) return;
      const {ctx} = s;
      s.analyser = ctx.createAnalyser();
      s.analyser.fftSize = 256;
      s.samples = new Uint8Array(256);
      s.analyser.connect(ctx.destination);
      s.node = new AudioWorkletNode(ctx, 'phone-capture');
      s.node.port.onmessage = event => onFrame(s, event.data);
      const silent = ctx.createGain();
      silent.gain.value = 0;
      s.node.connect(silent).connect(ctx.destination); // keeps the worklet running, inaudibly
      ctx.createMediaStreamSource(s.mic).connect(s.node);
      meter(s);
      await ready;
      if (s !== session) return;
      activate(s);
      feedback(true);
    } catch { end(s, 'she could not start'); }
  }

  // The hidden switch flips on every tap, so every tap gets the system's haptic tick; what a tap
  // does is decided here. While she is starting, more taps do not cancel it.
  function tap() {
    feedback();
    if (phase === 'starting') return;
    cues.unlock(); // iOS lets audio start only inside the tap
    cues.stop();
    off = phase === 'active';
    if (off) stop(session);
    else start();
  }
  toggle.onchange = tap;
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) end(session, 'the screen went off, or another app came up');
  });
  addEventListener('pagehide', () => end(session, 'the page closed'));
  render();

  return {
    // Turn her on from elsewhere (新对话, 进入语音对话): inside that gesture, as a tap would.
    on() {
      if (phase !== 'idle') return;
      off = false;
      cues.unlock();
      cues.stop();
      start();
    },
    // Away from the orb (true) or back on it (false). She stays on, and keeps quiet meanwhile.
    hold(away) {
      if (held === away) return;
      held = away;
      const s = session;
      if (!s || phase !== 'active') return;
      capture(s, !away);
      if (away) {
        silence(s);
        send(s, 'pause_session');
      } else {
        withHer(s);
      }
    },
    off: () => off,
    render,
  };
}
