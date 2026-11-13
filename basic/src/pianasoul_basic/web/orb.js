// The orb is her: a bright sky over a slow tide of light, with a fine grain, drawn on the device. It goes faster
// while she thinks or speaks and with your voice; gray and color share the same motion.
const vertex = `attribute vec2 position; varying vec2 p;
void main() { p = position; gl_Position = vec4(position, 0., 1.); }`;
const fragment = `
        precision highp float;
        varying vec2 p;
        uniform float time,level,quiet;
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
          vec3 color=moonlight(p,time*1.6,level)*(1.-.07*pow(smoothstep(.55,1.,length(p)),2.));
          color+=(hash(floor(gl_FragCoord.xy))-.5)*.024;
          color=mix(color,vec3(dot(color,vec3(.22,.65,.13))*.82+.08),quiet);
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
    const uniforms = Object.fromEntries(['time', 'level', 'quiet'].map(name => [name, gl.getUniformLocation(program, name)]));
    return (time, level, quiet) => {
      gl.viewport(0, 0, gl.canvas.width, gl.canvas.height);
      gl.uniform1f(uniforms.time, time);
      gl.uniform1f(uniforms.level, level);
      gl.uniform1f(uniforms.quiet, quiet);
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

export function animateOrb(element) {
  const canvas = element.querySelector('canvas');
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  let gl, render, input = 0, volume = 0, last = 0, clock = 0, quiet = 1, frame = null;
  const fallback = () => {
    canvas.hidden = true;
    element.classList.add('orb-fallback');
    render = () => {};
  };
  try { gl = canvas.getContext('webgl', {alpha: false, antialias: false, powerPreference: 'low-power'}); } catch {}
  render = gl ? renderer(gl) : null;
  if (!render) fallback();
  const resize = () => {
    const size = Math.round(element.clientWidth * Math.min(devicePixelRatio || 1, 2));
    if (canvas.width !== size) canvas.width = canvas.height = size;
  };
  new ResizeObserver(resize).observe(element);
  resize();

  function draw(now) {
    frame = null;
    if (document.hidden) return;
    frame = requestAnimationFrame(draw);
    if (last && now - last < 32) return;
    const dt = last ? Math.min((now - last) / 1000, .1) : 0;
    last = now;
    const blend = 1 - Math.exp(-dt * 9);
    volume += (input - volume) * blend;
    // Gray when off, color when active, and half way while it is starting up.
    const target = element.dataset.active === 'true' ? 0 : element.dataset.waking === 'true' ? .5 : 1;
    quiet += (target - quiet) * blend;
    const energy = element.dataset.state === 'speaking' ? .7 : element.dataset.state === 'thinking' ? .4 : .16;
    if (!reduced.matches) clock += dt * (1 + energy + volume * 1.5);
    render(clock, reduced.matches ? 0 : volume, quiet);
    element.style.setProperty('--scale', reduced.matches ? '1' : String(1 + Math.sin(clock * 1.4) * .012 + volume * .11));
  }
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { cancelAnimationFrame(frame); frame = null; last = 0; }
    else if (frame === null) frame = requestAnimationFrame(draw);
  });
  canvas.addEventListener('webglcontextlost', event => { event.preventDefault(); fallback(); });
  canvas.addEventListener('webglcontextrestored', () => {
    const restored = gl && renderer(gl);
    if (restored) { render = restored; canvas.hidden = false; element.classList.remove('orb-fallback'); }
  });
  if (!document.hidden) frame = requestAnimationFrame(draw);
  return {level(value) { input = Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : 0; }};
}
