// Exercise rendering lifecycle and motion invariants, without a GPU or browser.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('src/pianasoul/web/orb.js', 'utf8').replace(/^export /gm, '');

function harness({denied = false, reduced = false} = {}) {
  const frames = new Map(), documentEvents = {};
  let sequence = 0;
  // One orb's element, canvas and GL context; its uniforms and draws are kept on it.
  function make(dataset = {}) {
    const o = {canvasEvents: {}, styles: {}, classes: new Set(), uniforms: {}, draws: 0, deleted: 0};
    const canvas = o.canvas = {width: 0, height: 0, hidden: false, addEventListener: (name, fn) => o.canvasEvents[name] = fn};
    const gl = {canvas, createProgram: () => ({}), createShader: () => ({}), createBuffer: () => ({}),
      getShaderParameter: () => true, getProgramParameter: () => true, getAttribLocation: () => 0,
      getUniformLocation: (_, name) => name, uniform1f: (name, value) => o.uniforms[name] = value,
      drawArrays: () => o.draws++, deleteShader: () => o.deleted++};
    for (const name of ['shaderSource', 'compileShader', 'attachShader', 'linkProgram', 'useProgram', 'bindBuffer', 'bufferData', 'enableVertexAttribArray', 'vertexAttribPointer', 'viewport', 'deleteBuffer', 'deleteProgram']) gl[name] = () => {};
    canvas.getContext = () => { if (denied) throw Error('WebGL blocked'); return gl; };
    o.element = {clientWidth: 200, querySelector: () => canvas, dataset: {active: 'false', state: 'waiting', ...dataset},
      classList: {add: name => o.classes.add(name), remove: name => o.classes.delete(name)}, style: {setProperty: (name, value) => o.styles[name] = value}};
    return o;
  }
  const sandbox = {matchMedia: () => ({matches: reduced}), document: {hidden: false, addEventListener: (name, fn) => documentEvents[name] = fn},
    devicePixelRatio: 2, ResizeObserver: class { constructor(fn) { this.fn = fn; } observe() { this.fn(); } },
    requestAnimationFrame(fn) { const id = ++sequence; frames.set(id, fn); return id; }, cancelAnimationFrame: id => frames.delete(id)};
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox);
  const h = {sandbox, frames, documentEvents, make};
  h.home = (dataset, shown = () => true) => { const o = make(dataset); o.orb = sandbox.animateOrb(o.element, shown); return o; };
  h.other = (lit, shown = () => true, voiced = false) => { const o = make(); sandbox.addOrb(o.element, lit, shown, voiced); return o; };
  h.step = now => { const batch = [...frames.values()]; frames.clear(); for (const fn of batch) fn(now); };
  return h;
}

// The orb at home: color does not change its motion; your voice and hers do.
const g = harness(), b = harness();
const gray = g.home(), blue = b.home({active: 'true'});
for (const now of [1000, 1040, 1080, 1120, 1160]) { g.step(now); b.step(now); }
assert.equal(gray.uniforms.time, blue.uniforms.time, 'Color must not change flow speed');
assert.equal(gray.styles['--scale'], blue.styles['--scale'], 'Color must not change breathing');
assert.notEqual(gray.uniforms.quiet, blue.uniforms.quiet);
assert.equal(gray.deleted, 2, 'Compiled shader handles must be released after linking');
const before = Number(blue.styles['--scale']); blue.orb.level(.8); b.step(1200);
assert.ok(Number(blue.styles['--scale']) > before, 'Real volume drives scale');
blue.orb.level(NaN); b.step(1240); assert.ok(Number.isFinite(Number(blue.styles['--scale'])));

// Hidden page: no frames; back, the clock goes on from where it was.
const time = gray.uniforms.time;
g.sandbox.document.hidden = true; g.documentEvents.visibilitychange(); assert.equal(g.frames.size, 0);
g.sandbox.document.hidden = false; g.documentEvents.visibilitychange(); g.step(100000);
assert.equal(gray.uniforms.time, time, 'Resuming must not jump the animation clock');

// GPU lost, refused and back.
gray.canvasEvents.webglcontextlost({preventDefault() {}}); const draws = gray.draws;
g.step(100040); assert.equal(gray.draws, draws); assert.ok(gray.classes.has('orb-fallback'));
assert.ok(gray.styles['--quiet'] > .9, 'The fallback is gray too');
gray.canvasEvents.webglcontextrestored(); g.step(100080);
assert.equal(gray.canvas.hidden, false); assert.ok(gray.draws > draws); assert.equal(gray.classes.size, 0);
const f = harness({denied: true}), refused = f.home(); f.step(1000); f.step(1040);
assert.equal(refused.canvas.hidden, true); assert.ok(refused.classes.has('orb-fallback'));

// Half colored while it comes on.
const w = harness(), waking = w.home({waking: 'true'});
for (const now of [1000, 1040, 1080, 1120, 1160, 1200, 1240, 1280]) w.step(now);
assert.ok(waking.uniforms.quiet > .4 && waking.uniforms.quiet < .7, 'Half colored while starting');

// Reduced motion: still.
const r = harness({reduced: true}), still = r.home({active: 'true'}); still.orb.level(1); r.step(1000); r.step(1100);
assert.equal(still.styles['--scale'], '1'); assert.equal(still.uniforms.time, 0);

// Her other orbs: one clock with the orb at home, in color as much as they say, drawn only while shown, without
// the voice; off screen they still settle, so each shows as it should.
const m = harness();
let lit = true, shown = true;
const home = m.home({active: 'true', state: 'speaking'}), her = m.other(() => lit, () => shown), off = m.other(() => false);
home.orb.level(.8);
for (const now of [1000, 1040, 1080, 1120, 1160, 1200, 1240, 1280]) m.step(now);
assert.equal(her.uniforms.time, home.uniforms.time, 'The same clock');
assert.ok(her.uniforms.quiet < .1, 'Lit');
assert.equal(off.uniforms.quiet, 1, 'Gray');
assert.ok(Number(home.styles['--scale']) > Number(her.styles['--scale']), 'Only the orb at home moves with the voice');
shown = false;
lit = false;
const herDraws = her.draws;
for (let now = 1320; now <= 2600; now += 40) m.step(now);
assert.equal(her.draws, herDraws, 'Not drawn while off screen');
shown = true;
m.step(2640);
assert.ok(her.uniforms.quiet > .9, 'Already gray when it shows');

// The orbs that lead home look as it does; the one that flies sets off as the one pulled.
assert.equal(m.sandbox.homeColor(), 1);
home.element.dataset.active = 'false';
home.element.dataset.waking = 'true';
assert.equal(m.sandbox.homeColor(), .5, 'Half while she comes on');
const fly = m.other(m.sandbox.homeColor);
m.sandbox.sameLook(fly.element, off.element);
m.step(2680);
assert.ok(fly.uniforms.quiet > .8, 'Sets off gray');
for (let now = 2720; now <= 4000; now += 40) m.step(now);
assert.ok(Math.abs(fly.uniforms.quiet - .5) < .05, 'And arrives as home is');

// Gray turns to color gently, over a moment, not at once; and back.
const t = harness(), turning = t.home();
for (let now = 1000; now <= 1400; now += 40) t.step(now);
turning.element.dataset.active = 'true';
t.step(1440);
assert.ok(turning.uniforms.quiet > .8, 'Not at once');
for (let now = 1480; now <= 1840; now += 40) t.step(now);
assert.ok(turning.uniforms.quiet > .05 && turning.uniforms.quiet < .5, 'Over a moment');
for (let now = 1880; now <= 3000; now += 40) t.step(now);
assert.ok(turning.uniforms.quiet < .02, 'Then in color');

// The orb that flies home is home's: it moves with the voice as home's does, and bounces with it; other orbs do not.
{
const v = harness();
let flying = true;
const at = v.home({active: 'true'}), fly = v.other(() => true, () => flying, true), pull = v.other(() => true);
at.orb.level(.8);
for (const now of [1000, 1040, 1080, 1120]) v.step(now);
assert.equal(fly.styles['--scale'], at.styles['--scale'], 'Landing, the same size as home');
assert.ok(Number(at.styles['--scale']) > Number(pull.styles['--scale']));
const bounced = [];
for (const o of [at, fly, pull]) o.element.animate = () => bounced.push(o);
at.orb.bounce([{scale: 1}, {scale: 1.07}], {duration: 460});
assert.deepEqual(bounced, [at, fly], 'The flying orb bounces with home, the others do not');
flying = false; bounced.length = 0;
at.orb.bounce([{scale: 1}, {scale: 1.07}], {duration: 460});
assert.deepEqual(bounced, [at], 'Landed and hidden, it no longer does');
}
console.log('Orb passed: gray/color motion parity, volume response, reduced motion, background suspension, GPU loss/fallback/recovery, her other orbs on one clock, a gentle change between gray and color, and the flying orb sized and bounced as the orb at home is.');
