// The orb is her, wherever it shows: at home, in the pull at the end of a conversation, flying home, and in
// Settings. Every one draws the same picture on one clock; the orb at home sets the pace, faster while she thinks
// or speaks and with your voice. The picture is a bright sky over a slow tide of light, with a fine grain, glowing on
// a dark page and keeping its blue on a light one; its gray is lighter on a dark page and deeper on a light one, so
// that it stands out from either. Gray and color share the same motion, and one turns into the other gently.
const vertex = `attribute vec2 position; varying vec2 p;
void main() { p = position; gl_Position = vec4(position, 0., 1.); }`;
const fragment = `
        precision highp float;
        varying vec2 p;
        uniform float time,level,quiet,page;  // page: 0 dark, 1 light
        float hash(vec2 v){return fract(sin(dot(v,vec2(127.1,311.7)))*43758.5453);}
        // The tide: its face swells slowly; with a voice, hers or yours, it rises and ripples, and its edge glows. The sky
        // above it, the light below.
        vec3 moonlight(vec2 p,float t,float energy){
          float swell=.10*sin(2.12*p.x+.19*t+.15*sin(t*.13))+.033*sin(4.15*p.x-.14*t)+.045*cos(1.22*p.x+.10*t)+.016*sin(5.8*p.x+.24*t);
          float face=-p.y-.045+swell+energy*(.06+.055*sin(3.2*p.x+.9*t)+.025*sin(7.3*p.x-1.4*t));
          float near=smoothstep(-.105,.105,face);
          vec3 sky=mix(vec3(.20,.42,.80),vec3(.42,.66,.94),smoothstep(.94,-.35,p.y));
          sky+=vec3(.025,.035,.040)*sin(2.6*p.x+1.5*p.y+t*.10);
          vec3 tide=mix(vec3(.78,.89,.98),vec3(.95,.98,1.),smoothstep(.02,1.75,face)*.82);
          tide+=vec3(.022,.028,.034)*(sin(p.x*2.9-p.y*1.2-t*.14)*sin(p.y*2.15+p.x*.65+t*.11)*.55+cos(p.x*1.35+p.y*3.6+t*.095)*.32);
          vec3 color=mix(sky,tide,near);
          float under=face-.44+.075*sin(1.95*p.x-.09*t+.3);
          color=mix(color,vec3(.56,.74,.95),smoothstep(-.13,.14,under)*.47*near);
          color+=vec3(.07,.09,.10)*exp(-pow(face/(.1525+.06*energy),2.))*mix(near,1.,energy)*(.45+1.4*energy);
          return clamp(color*(1.-.035*smoothstep(.6,1.,length(p))),0.,1.);
        }
        void main(){
          float r=length(p);
          vec3 base=moonlight(p,time*1.6,level);
          vec3 dark=base*(1.-.07*pow(smoothstep(.55,1.,r),2.));
          vec3 light=mix(base,vec3(1.),pow(smoothstep(.5,1.,r),2.)*.22);
          vec3 color=mix(dark,light,page)+(hash(floor(gl_FragCoord.xy))-.5)*.024;
          float gray=dot(color,vec3(.22,.65,.13));
          color=mix(color,vec3(mix(gray*.82+.08,gray*.62+.26,page)),quiet);
          gl_FragColor=vec4(clamp(color,0.,1.),1.);
        }`;

function renderer(gl) {
  const program = gl.createProgram();
  const shaders = [];
  let buffer;
  try {
    for (const [type, source] of [[gl.VERTEX_SHADER, vertex], [gl.FRAGMENT_SHADER, fragment]]) {
      const shader = gl.createShader(type);
      shaders.push(shader);
      gl.shaderSource(shader, source);
      gl.compileShader(shader);
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw Error('Shader unavailable');
      gl.attachShader(program, shader);
    }
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw Error('Orb program unavailable');
    gl.useProgram(program);
    buffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    const position = gl.getAttribLocation(program, 'position');
    gl.enableVertexAttribArray(position);
    gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);
    const uniforms = Object.fromEntries(['time', 'level', 'quiet', 'page'].map(name => [name, gl.getUniformLocation(program, name)]));
    return (time, level, quiet, page) => {
      gl.viewport(0, 0, gl.canvas.width, gl.canvas.height);
      gl.uniform1f(uniforms.time, time);
      gl.uniform1f(uniforms.level, level);
      gl.uniform1f(uniforms.quiet, quiet);
      gl.uniform1f(uniforms.page, page);
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    };
  } catch {
    if (buffer) gl.deleteBuffer(buffer);
    gl.deleteProgram(program);
    return null;
  } finally {
    for (const shader of shaders) gl.deleteShader(shader);
  }
}

const orbs = [];
const reduced = matchMedia('(prefers-reduced-motion: reduce)');
let frame = null, last = 0, clock = 0, home = null, page = null;

// One loop draws them all, and only while the page shows.
function draw(now) {
  frame = null;
  if (document.hidden) return;
  frame = requestAnimationFrame(draw);
  if (last && now - last < 32) return;
  const dt = last ? Math.min((now - last) / 1000, .1) : 0;
  last = now;
  const blend = 1 - Math.exp(-dt * 9), gently = 1 - Math.exp(-dt * 4);  // the voice at once; gray and color, the page, over a moment
  if (home) home.volume += (home.input - home.volume) * blend;
  const light = document.documentElement?.dataset.theme === 'light' ? 1 : 0;
  page = page === null ? light : page + (light - page) * gently;
  const volume = home ? home.volume : 0;
  const state = home?.element.dataset.state;
  const energy = state === 'speaking' ? .7 : state === 'thinking' ? .4 : .16;
  if (!reduced.matches) clock += dt * (1 + energy + volume * 1.5);
  const breath = 1 + Math.sin(clock * 1.4) * .012;
  for (const o of orbs) {
    o.quiet += (o.target() - o.quiet) * gently;  // off screen too, so each is as it should be when it shows
    if (!o.shown()) continue;
    const level = o === home || o.voiced ? volume : 0;
    o.render(clock, reduced.matches ? 0 : level, o.quiet, page);
    if (o.render.fallback) o.element.style.setProperty('--quiet', o.quiet.toFixed(3));
    o.element.style.setProperty('--scale', reduced.matches ? '1' : String(breath + level * .11));
  }
}

function start() {
  if (frame === null && !document.hidden) frame = requestAnimationFrame(draw);
}

document.addEventListener('visibilitychange', () => {
  if (document.hidden) { cancelAnimationFrame(frame); frame = null; last = 0; }
  else start();
});

// element: a .orb with a canvas. target(): 0 in color, 1 gray (in between while it comes on). shown(): on screen now.
function register(element, target, shown = () => true) {
  const canvas = element.querySelector('canvas');
  const o = {element, target, shown, quiet: target(), input: 0, volume: 0};
  let gl;
  const fallback = () => {
    canvas.hidden = true;
    element.classList.add('orb-fallback');
    o.render = Object.assign(() => {}, {fallback: true});
  };
  try { gl = canvas.getContext('webgl', {alpha: false, antialias: false, powerPreference: 'low-power'}); } catch {}
  o.render = gl ? renderer(gl) : null;
  if (!o.render) fallback();
  // The canvas is drawn at the orb's laid-out size, so a scaled-down orb (the one that flies) stays sharp.
  const resize = () => {
    const size = Math.round(element.clientWidth * Math.min(devicePixelRatio || 1, 2));
    if (size && canvas.width !== size) canvas.width = canvas.height = size;
  };
  new ResizeObserver(resize).observe(element);
  resize();
  canvas.addEventListener('webglcontextlost', event => { event.preventDefault(); fallback(); });
  canvas.addEventListener('webglcontextrestored', () => {
    const restored = gl && renderer(gl);
    if (restored) { o.render = restored; canvas.hidden = false; element.classList.remove('orb-fallback'); }
  });
  orbs.push(o);
  start();
  return o;
}

// The orb at home: gray when off, color when on, half way while it comes on; it moves with your voice and hers.
export function animateOrb(element, shown) {
  home = register(element, () => element.dataset.active === 'true' ? 0 : element.dataset.waking === 'true' ? .5 : 1, shown);
  return {
    level(value) { home.input = Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : 0; },
    // A bounce (a tap, her coming on): the orb flying home bounces with it, being the same orb.
    bounce(keyframes, options) {
      for (const o of orbs) if (o === home || (o.voiced && o.shown())) o.element.animate?.(keyframes, options);
    },
  };
}

// How much the orb at home is in color (or is becoming): the orbs that lead home look as it does.
export function homeColor() {
  return home ? 1 - home.target() : 0;
}

// Another orb: color() says how much it is in color, from 0 (gray) to 1. voiced: it moves with the voices as the orb
// at home does (the one that flies home lands on it, the same size whatever is said meanwhile).
export function addOrb(element, color, shown, voiced = false) {
  register(element, () => 1 - color(), shown).voiced = voiced;
}

// At once as another orb looks now: the orb that flies home sets off as the one you pulled.
export function sameLook(element, like) {
  const find = el => orbs.find(o => o.element === el);
  find(element).quiet = find(like).quiet;
}
