// The live view's page half: reads frames from /stream, draws one uPlot strip per input,
// and sends button presses to /cmd. Frames are drawn on the next animation frame; if two
// arrive before it, only the newer is drawn, so the page never falls behind.
'use strict';

const STRIP_H = 150;
const $ = (id) => document.getElementById(id);
let S = null;                 // the last /state
const strips = [];            // {u, yr: [lo, hi], auto, valEl, rateEl}
let lastFrame = null;
const ages = [], arrivals = [], canvasAges = [], draws = [], drawnAt = [];
let lastBuild = 0;

// the key this launch made (in the address after #): the server answers nothing without it
const H = {'X-Newt-Key': new URLSearchParams(location.hash.slice(1)).get('k') || ''};
const post = (d) => fetch('/cmd', {method: 'POST', headers: H, body: JSON.stringify(d)}).then((r) => r.json());
const minus = (s) => s.replace('-', '−');
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const dark = () => matchMedia('(prefers-color-scheme: dark)').matches;

function fmtVal(v, units) {
  if (!isFinite(v)) return '–';
  return minus(v.toFixed(units === 'mV' ? 1 : 2)) + ' ' + units;
}

// ---------------------------------------------------------------- strips
function buildStrips() {
  const box = $('strips');
  box.replaceChildren();
  strips.length = 0;
  const font = '11.5px "IBM Plex Mono", ui-monospace, Menlo, monospace';
  const muted = css('--muted'), line = css('--line');
  const width = box.clientWidth;
  S.channels.forEach((ch, i) => {
    const last = i === S.channels.length - 1;
    const color = dark() ? ch.dark : ch.light;
    const el = document.createElement('div');
    el.className = 'strip';
    el.innerHTML = `<div class="shead"><span class="dot" style="background:${color}"></span>
      <span class="sname">${ch.name}</span><span class="sval"></span><span class="srate"></span>
      <span class="spacer"></span>
      <div class="seg"><button data-z="out" title="Zoom out">−</button><button data-z="in" title="Zoom in">+</button><button data-z="auto" aria-pressed="true" title="Fit the trace">Auto</button></div></div>
      <div class="chart"></div>`;
    box.append(el);
    const grid = {stroke: line, width: 1}, ticks = {stroke: line, width: 1, size: 4};
    const u = new uPlot({
      width, height: STRIP_H + (last ? 30 : 0),
      padding: [6, 10, 0, 0],
      legend: {show: false},
      cursor: {sync: {key: 'live'}, y: false, points: {show: false}, drag: {x: false, y: false, setScale: false}},
      scales: {x: {time: false, auto: false}, y: {auto: false}},
      axes: [
        {stroke: muted, grid, ticks, font, size: last ? 30 : 4,
         values: last ? (u, v) => v.map((x) => minus(x.toFixed(x % 1 ? 1 : 0)) + ' s') : (u, v) => v.map(() => '')},
        {stroke: muted, grid, ticks, font, size: 68,
         values: (u, v) => v.map((y) => minus(String(+y.toPrecision(3))) + ' ' + ch.units)},
      ],
      series: [{}, {stroke: color, width: 1.25, points: {show: false}}],
    }, [new Float64Array(0), new Float32Array(0)], el.querySelector('.chart'));
    const s = {u, yr: null, auto: true, valEl: el.querySelector('.sval'), rateEl: el.querySelector('.srate')};
    el.querySelector('.seg').addEventListener('click', (e) => zoom(s, e.target.dataset.z, el));
    strips.push(s);
  });
  sendCols();
  if (lastFrame) draw(lastFrame);
}

function zoom(s, how, el) {
  if (!how) return;
  if (how === 'auto') s.auto = true;
  else if (s.yr) {
    const mid = (s.yr[0] + s.yr[1]) / 2, half = (s.yr[1] - s.yr[0]) / 2 * (how === 'in' ? 0.5 : 2);
    s.yr = [mid - half, mid + half];
    s.auto = false;
  }
  el.querySelector('[data-z=auto]').setAttribute('aria-pressed', String(s.auto));
  if (lastFrame) draw(lastFrame);
}

function fit(s, ys) {          // Auto: grow at once, shrink only when the trace takes under half
  let lo = Infinity, hi = -Infinity;
  for (let k = 0; k < ys.length; k++) { const v = ys[k]; if (v < lo) lo = v; if (v > hi) hi = v; }
  if (!isFinite(lo)) return;
  if (hi - lo < 1e-6) { lo -= 0.5; hi += 0.5; }
  const pad = (hi - lo) * 0.08;
  lo -= pad; hi += pad;
  const r = s.yr;
  if (!r || lo < r[0] || hi > r[1] || (hi - lo) < 0.5 * (r[1] - r[0])) s.yr = [lo, hi];
}

let colsTimer = 0;
function sendCols() {
  clearTimeout(colsTimer);
  colsTimer = setTimeout(() => {
    if (strips.length) post({cmd: 'cols', n: Math.round(strips[0].u.bbox.width / devicePixelRatio)});
  }, 150);
}

// ---------------------------------------------------------------- frames
function parse(buf) {
  const dv = new DataView(buf);
  const n = dv.getUint32(24, true), m = dv.getUint32(28, true);
  const f = {wall: dv.getFloat64(0, true), tEnd: dv.getFloat64(8, true), win: dv.getFloat32(16, true),
             build: dv.getFloat32(20, true), x: new Float64Array(buf, 32, m),
             newest: new Float32Array(buf, 32 + 8 * m, n), ys: []};
  let off = 32 + 8 * m + 4 * n;
  for (let i = 0; i < n; i++, off += 4 * m) f.ys.push(new Float32Array(buf, off, m));
  return f;
}

// Drawn as soon as it arrives (the newest of a read, if several came at once): waiting
// for an animation frame added 30-50 ms in WebKit. The canvas shows at the next refresh.
function onFrame(buf) {
  const f = parse(buf);
  if (f.wall) arrivals.push([performance.now(), Date.now() - f.wall]);
  draw(f);
  if (f.wall) {
    canvasAges.push([performance.now(), Date.now() - f.wall]);
    requestAnimationFrame(() => ages.push([performance.now(), Date.now() - f.wall]));
  }
}
const ticks = [];
(function tick() { ticks.push(performance.now()); requestAnimationFrame(tick); })();

function draw(f) {
  lastFrame = f;
  const t0 = performance.now();
  const xmin = Math.max(0, f.tEnd - f.win);
  strips.forEach((s, i) => {
    if (!f.ys[i]) return;
    if (s.auto) fit(s, f.ys[i]);
    const yr = s.yr || [-1, 1];
    s.u.batch(() => {
      s.u.setData([f.x, f.ys[i]], false);
      s.u.setScale('x', {min: xmin, max: xmin + f.win});
      s.u.setScale('y', {min: yr[0], max: yr[1]});
    });
  });
  const now = performance.now();
  draws.push([now, now - t0]);
  drawnAt.push(now);
  lastBuild = f.build;
}

async function stream() {
  for (;;) {
    try {
      const r = await fetch('/stream', {cache: 'no-store', headers: H});
      const rd = r.body.getReader();
      let buf = new Uint8Array(0);
      for (;;) {
        const {value, done} = await rd.read();
        if (done) break;
        if (buf.length) { const b = new Uint8Array(buf.length + value.length); b.set(buf); b.set(value, buf.length); buf = b; }
        else buf = value;
        let newest = null;
        while (buf.length >= 4) {
          const n = new DataView(buf.buffer, buf.byteOffset, 4).getUint32(0, true);
          if (buf.length < 4 + n) break;
          newest = buf.slice(4, 4 + n).buffer;
          buf = buf.subarray(4 + n);
        }
        if (newest) onFrame(newest);
      }
    } catch (e) { /* server gone: try again */ }
    await new Promise((ok) => setTimeout(ok, 1000));
  }
}

// ---------------------------------------------------------------- numbers on the page
const median = (a) => { const b = [...a].sort((p, q) => p - q); return b.length ? b[Math.floor(b.length / 2)] : NaN; };
const pct = (a, p) => { const b = [...a].sort((p, q) => p - q); return b.length ? b[Math.min(b.length - 1, Math.floor(b.length * p))] : NaN; };
function recent(list, ms) {
  const cut = performance.now() - ms;
  while (list.length && (Array.isArray(list[0]) ? list[0][0] : list[0]) < cut) list.shift();
  return list.map((x) => Array.isArray(x) ? x[1] : x);
}
function lag() {
  const a = recent(ages, 2000), n = recent(arrivals, 2000), c = recent(canvasAges, 2000), d = recent(draws, 2000),
        fr = recent(drawnAt, 2000), tk = recent(ticks, 2000);
  const ms = (v, dp = 0) => isFinite(v) ? v.toFixed(dp) + ' ms' : '–';
  const out = {age_med: median(a), age_p95: pct(a, 0.95), net_med: median(n), canvas_med: median(c),
               draw_ms: median(d), fps: fr.length / 2, build_ms: lastBuild, refresh: tk.length / 2,
               vis: document.visibilityState + (document.hasFocus() ? ', focused' : ', not focused')};
  $('age').textContent = ms(out.age_med);
  $('p95').textContent = ms(out.age_p95);
  $('draw').textContent = ms(out.draw_ms, 2);
  $('fps').textContent = S && S.running ? out.fps.toFixed(0) : '–';
  $('build').textContent = ms(out.build_ms, 2);
  if (lastFrame) strips.forEach((s, i) => { s.valEl.textContent = fmtVal(lastFrame.newest[i], S.channels[i].units); });
  return out;
}

function showState(st) {
  S = st;
  $('run').textContent = st.running ? 'Stop' : 'Run';
  $('fire').disabled = !st.running || st.stim_busy;
  $('chunk').textContent = st.chunk_ms.toFixed(0) + ' ms';
  const status = $('status');
  status.textContent = st.error ? st.error : st.running ? `Recording ${st.fs / 1000} kHz` : 'Stopped';
  status.classList.toggle('bad', !!st.error);
  const win = $('win');
  if (!win.children.length) {
    for (const w of st.windows) {
      const b = document.createElement('button');
      b.textContent = w + ' s';
      b.onclick = () => post({cmd: 'window', s: w}).then(showState);
      win.append(b);
    }
  }
  [...win.children].forEach((b, k) => b.setAttribute('aria-pressed', String(st.windows[k] === st.window_s)));
  strips.forEach((s, i) => {
    const r = st.channels[i] && st.channels[i].rate;
    s.rateEl.textContent = r == null || !st.running ? '' : `${r.toFixed(1)} spikes/s`;
  });
}

// ---------------------------------------------------------------- controls
for (const out of document.querySelectorAll('output[data-for]')) {
  const inp = $(out.dataset.for);
  const show = () => { out.textContent = inp.value + (out.dataset.unit ? ' ' + out.dataset.unit : ''); };
  inp.addEventListener('input', show);
  show();
}
$('run').onclick = () => post({cmd: 'run', on: !(S && S.running)}).then(showState);
$('fire').onclick = () => post({cmd: 'stim', amp: +$('amp').value, width: +$('width').value,
                                count: +$('count').value, every: +$('every').value})
  .then((st) => { showState(st); $('sent').textContent = st.problem || 'Sent: ' + st.sent; });

new ResizeObserver(() => {
  const w = $('strips').clientWidth;
  strips.forEach((s, i) => s.u.setSize({width: w, height: STRIP_H + (i === strips.length - 1 ? 30 : 0)}));
  sendCols();
}).observe($('strips'));
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => S && buildStrips());

(async () => {
  showState(await (await fetch('/state', {headers: H})).json());
  buildStrips();
  stream();
  setInterval(lag, 250);
  setInterval(() => fetch('/state', {headers: H}).then((r) => r.json()).then(showState), 500);
  const engine = /Chrome\//.test(navigator.userAgent) ? 'Chromium' : 'WebKit';
  setInterval(() => { const l = lag(); if (S && S.running && isFinite(l.age_med)) post({cmd: 'lag', engine, ...l}); }, 5000);
})();
