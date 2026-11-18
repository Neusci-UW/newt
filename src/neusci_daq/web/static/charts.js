// The Analysis board: the charts you add beside the Trials pane (web/charts.py works out
// what each draws). Time charts are uPlot strips like Record's, fed traces already shrunk
// to two points per screen column; zooming asks Python again for just what's on screen.
// A measure against the stimulus is a square SVG scatter; the spikes found (the Spikes
// panel) show as a raster on a canvas and each unit's waveforms in SVG. Uses $, H and esc
// (analysis.js).
'use strict';

(() => {
const B = {state: null, folder: null, sig: '', cards: new Map(), x: null};   // x: the time shown [x0, x1], or null: all
const isMac = /Mac/.test(navigator.platform);
const FONT = '12px "IBM Plex Mono", ui-monospace, Menlo, monospace';   // whole px: uPlot scales only the digits before "px"
const cssv = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const minus = (s) => String(s).replace(/-/g, '−');
const postJSON = (url, d) => fetch(url, {method: 'POST', headers: H, body: JSON.stringify(d)});
const BINS = [[0, 'Off'], [1, '1 ms'], [2, '2 ms'], [5, '5 ms'], [10, '10 ms'], [20, '20 ms'], [50, '50 ms']];
const ABOUT = {
  'Response': 'Each channel through the trial: each ticked group’s average, with its spread shaded, or every trial.',
  'Stimulus': 'What the stimulus was in each trial, in its group’s color.',
  'Measure against stimulus': 'One number from each trial (its peak, its area…) against the stimulus: an input–output curve.',
  'Raster': 'Each spike found, as a dot at its time, a row for each ticked trial: when did the cell fire?',
  'Waveforms': 'What each unit’s spike looks like: its spikes, faint, behind their average.',
};
const SPIKY = new Set(['Raster', 'Waveforms']);
const SMOOTH = [[0, 'None'], [0.05, '0.05 ms'], [0.1, '0.1 ms'], [0.2, '0.2 ms'], [0.5, '0.5 ms'], [1, '1 ms']];

// ---------------------------------------------------------------- numbers on axes
function si(big, unit) {            // a prefix for small numbers: 0.02 V reads as 20 mV
  if (!unit || /[²³]/.test(unit) || unit === 's' || !(big > 0)) return [1, unit];
  if (big < 1e-3) return [1e6, 'µ' + unit];
  if (big < 1) return [1e3, 'm' + unit];
  return [1, unit];
}
function tickText(vals, unit) {
  const fin = vals.filter((v) => v != null && isFinite(v));
  const big = Math.max(0, ...fin.map(Math.abs));
  const [k, u] = si(big, unit);
  const step = fin.length > 1 ? Math.abs(fin[1] - fin[0]) * k : big * k || 1;
  const dec = Math.max(0, Math.min(6, -Math.floor(Math.log10(step) + 1e-9)));
  return vals.map((v) => v == null ? '' : minus((v * k).toFixed(dec)) + (u ? ' ' + u : ''));
}
function niceTicks(lo, hi, n = 5) {
  const step0 = (hi - lo) / n, mag = 10 ** Math.floor(Math.log10(step0));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= step0) || 10 * mag;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(Math.abs(v) < step * 1e-9 ? 0 : v);
  return out;
}

// ---------------------------------------------------------------- the board
async function loadBoard() {
  try {
    const r = await fetch('/charts/state', {headers: H});
    B.state = await r.json();
  } catch (e) { return; }
  if (B.state.folder !== B.folder) { B.folder = B.state.folder; B.x = null; }
  render();
}
async function cmd(d) {
  try {
    const r = await postJSON('/charts/cmd', d);
    B.state = await r.json();
    if (B.state.problem) showProblem(B.state.problem);
  } catch (e) { showProblem('Newt is not answering.'); }
  render();
}

function render() {
  const S = B.state;
  if (!S) return;
  const box = $('charts');
  const ids = new Set(S.charts.map((c) => c.id));
  for (const [id, card] of B.cards) if (!ids.has(id)) { card.plots.forEach((p) => p.u.destroy()); card.el.remove(); B.cards.delete(id); }
  for (const c of S.charts) {
    let card = B.cards.get(c.id);
    if (!card) { card = makeCard(c); B.cards.set(c.id, card); }
    card.cfg = c;
    fillSets(card);
    box.append(card.el);                                    // (in the board's order)
  }
  $('noCharts').hidden = S.charts.length > 0;
  for (const b of $('spread').children) b.setAttribute('aria-pressed', String(b.dataset.s === S.spread));
  $('addChart').disabled = !S.folder;
  S.charts.forEach((c, k) => {
    const card = B.cards.get(c.id);
    card.el.querySelector('[data-a=up]').disabled = k === 0;
    card.el.querySelector('[data-a=down]').disabled = k === S.charts.length - 1;
  });
  refreshAll();
}
function refreshAll() { for (const card of B.cards.values()) fetchData(card); }

// the Trials pane changed (analysis.js show()): draw again if what charts show changed
window.boardTrials = (st) => {
  const sig = JSON.stringify([st.folder, st.how, st.looks, st.groups.map((g) => [g.name, g.color, g.hidden,
    g.trials.map((t) => t.off ? -1 - t.i : t.i)])]);
  if (st.folder !== B.folder || !B.state) { B.sig = sig; return loadBoard(); }
  if (sig === B.sig) return;
  B.sig = sig;
  loadBoard();                                              // (the channels listed follow the stimulus channel)
};

// ---------------------------------------------------------------- a chart's card
function makeCard(c) {
  const el = document.createElement('div');
  el.className = 'chart';
  const time = c.kind === 'Response' || c.kind === 'Stimulus';
  el.innerHTML = `<div class="chead"><b class="ctitle"></b><span class="cnote"></span><span class="spacer"></span>
      ${time ? '<button class="ib" data-a="whole" title="Show the whole trial (or double-click the chart)">Whole trial</button>' : ''}
      <span class="cbtns"><button class="ib" data-a="up" title="Move this chart up">↑</button><button class="ib" data-a="down" title="Move this chart down">↓</button><button class="ib" data-a="close" title="Remove this chart">×</button></span></div>
    <div class="crow">${c.kind === 'Stimulus' || SPIKY.has(c.kind) ? '' : `<div class="seg show" title="Each ticked group's average, every trial in its trial color, or faint trials behind bold averages"><button data-v="avg">Averages</button><button data-v="trials">All trials</button><button data-v="both">Both</button></div>`}<div class="csets"></div></div>
    <div class="cbody"></div>
    ${time ? `<p class="caption hint">Scroll to zoom in time · ${isMac ? '⌘' : 'Ctrl'}-scroll zooms up and down · drag to move along · double-click for the whole trial</p>` : ''}`;
  const card = {el, cfg: c, plots: [], shape: '', seq: 0, head: null, yr: {}, timer: 0, time};
  el.querySelector('.chead').onclick = el.querySelector('.crow').onclick = (e) => {
    const a = e.target.dataset.a, v = e.target.dataset.v;
    if (a === 'close') cmd({cmd: 'remove', id: card.cfg.id});
    else if (a === 'up' || a === 'down') cmd({cmd: 'move', id: card.cfg.id, by: a === 'up' ? -1 : 1});
    else if (a === 'whole') whole(card);
    else if (v) cmd({cmd: 'set', id: card.cfg.id, fields: {show_trials: v !== 'avg', show_average: v !== 'trials'}});
  };
  el.querySelector('.csets').addEventListener('change', (e) => setField(card, e.target));
  el.querySelector('.csets').addEventListener('click', (e) => {
    const chip = e.target.closest('[data-t]');
    if (!chip) return;
    const [name, s] = chip.dataset.t.split('|');
    const ticked = (card.cfg.ticked || []).filter(([n, k]) => !(n === name && k === +s));
    if (chip.getAttribute('aria-pressed') !== 'true') ticked.push([name, +s]);
    cmd({cmd: 'set', id: card.cfg.id, ticked});
  });
  return card;
}

function fillSets(card) {
  const S = B.state, c = card.cfg, f = c.fields;
  card.el.querySelector('.ctitle').textContent = S.kinds[c.kind] + (sameKind(c) > 1 ? ' ' + sameKind(c) : '');
  card.el.querySelector('.ctitle').title = ABOUT[c.kind] || '';
  const view = f.show_trials && f.show_average ? 'both' : f.show_trials ? 'trials' : 'avg';
  for (const b of card.el.querySelectorAll('.show button')) b.setAttribute('aria-pressed', String(b.dataset.v === view));
  const opt = (v, t, cur) => `<option value="${esc(v)}"${String(v) === String(cur) ? ' selected' : ''}>${esc(t)}</option>`;
  const bins = `<label class="cfield">Average in bins <select data-f="bin">${BINS.map(([v, t]) => opt(v, t, f.bin || 0)).join('')}</select></label>`;
  let html = '';
  if (c.kind === 'Response') {
    const on = new Set((c.ticked || []).map(([n, s]) => n + '|' + s));
    const chans = S.channels.filter((ch) => ch.name !== S.how);
    html = `<span class="cfield">Plot ${chans.map((ch) => `<span class="chgroup">${S.series.map((s, k) =>
      `<button class="chip${k ? ' small' : ''}" data-t="${esc(ch.name)}|${k}" aria-pressed="${on.has(ch.name + '|' + k)}"
        title="${k === 1 ? `How fast ${esc(ch.name)} changes (its slope), per second` : k === 2 ? 'How fast that slope changes, per second' : esc(ch.name) + ' as recorded'}">${k ? s : esc(ch.name)}</button>`).join('')}</span>`).join('')}</span>` + bins;
  } else if (c.kind === 'Stimulus') {
    html = `<span class="cfield muted">${S.how && S.channels.some((ch) => ch.name === S.how) ? esc(S.how) + ', the stimulus channel picked in the Trials pane' : 'The stimulus channel is picked in the Trials pane'}</span>` + bins;
  } else if (c.kind === 'Raster') {
    html = `<label class="cfield">Dot size <select data-f="dots">${[1, 2, 3, 4, 5, 6, 8, 10].map((v) => opt(v, v + ' px', f.dots)).join('')}</select></label>
      <span class="cfield muted">Click a row to look at that trial</span>`;
  } else if (c.kind === 'Waveforms') {
    html = `<label class="cfield">Show <select data-f="show">${[[0, 'V'], [1, 'dV/dt'], [2, 'd²V/dt²']].map(([v, t]) => opt(v, t, f.show)).join('')}</select></label>
      <label class="cfield" title="Each unit's spikes divided by the average size of their peaks (the unit's average peak = ±1), so units compare by shape"><input type="checkbox" data-f="norm"${f.norm ? ' checked' : ''}> Normalize</label>
      ${+f.show ? `<label class="cfield" title="Differentiating amplifies noise: average over this long first">Smooth first <select data-f="smooth">${SMOOTH.map(([v, t]) => opt(v, t, f.smooth)).join('')}</select></label>` : ''}`;
  } else {
    html = `<label class="cfield">Y <select data-f="channel">${opt('', 'Choose a channel…', f.channel)}${S.channels.filter((ch) => ch.name !== S.how).map((ch) => opt(ch.name, ch.name, f.channel)).join('')}</select>
        <select data-f="measure">${opt('', 'Choose a measure…', f.measure)}${S.y_measures.map(([k, name]) => opt(k, name, f.measure)).join('')}</select></label>
      <label class="cfield">measured <select data-f="window">${Object.entries(S.windows).map(([k, t]) => opt(k, t.toLowerCase(), f.window)).join('')}</select></label>
      <label class="cfield">X <select data-f="x">${opt('', 'Choose a stimulus measure…', f.x)}${S.x_measures.map(([k, name]) => opt(k, 'Stimulus ' + name.toLowerCase(), f.x)).join('')}</select></label>`;
  }
  const sets = card.el.querySelector('.csets');
  if (sets.dataset.html !== html) { sets.innerHTML = html; sets.dataset.html = html; }
  if (c.kind === 'Measure against stimulus') {
    const m = S.y_measures.find(([k]) => k === f.measure);
    const sel = sets.querySelector('[data-f=measure]');
    if (sel) sel.title = m ? m[2] : 'What to measure in each trial';
  }
}
const sameKind = (c) => {
  const same = B.state.charts.filter((x) => x.kind === c.kind);
  return same.length > 1 ? same.indexOf(c) + 1 : 0;
};
function setField(card, el) {
  const k = el.dataset.f;
  if (!k) return;
  const v = el.type === 'checkbox' ? el.checked : ['bin', 'dots', 'show', 'smooth'].includes(k) ? +el.value : el.value;
  cmd({cmd: 'set', id: card.cfg.id, fields: {[k]: v}});
}

// ---------------------------------------------------------------- data
function plotWidth(card) {
  const body = card.el.querySelector('.cbody');
  return Math.max(200, body.clientWidth);
}
async function fetchData(card) {
  const seq = ++card.seq;
  const cols = Math.round(Math.max(100, plotWidth(card) - 76));
  const x = card.time && B.x ? {x0: B.x[0], x1: B.x[1]} : {};
  let r;
  try { r = await postJSON('/charts/data', {id: card.cfg.id, cols, ...x}); } catch (e) { return; }
  if (seq !== card.seq) return;                             // a newer one is on its way
  if (!r.ok) { try { note(card, (await r.json()).problem || 'Couldn’t draw this'); } catch (e) { /* */ } return; }
  if ((r.headers.get('Content-Type') || '').includes('json')) {
    const d = await r.json();
    if (seq !== card.seq) return;
    if (card.cfg.kind === 'Raster') { card.data = d; return drawRaster(card); }
    if (card.cfg.kind === 'Waveforms') return drawWaves(card, d);
    return card.time ? note(card, d.note) : drawScatter(card, d);
  }
  const buf = await r.arrayBuffer();
  if (seq !== card.seq) return;
  drawTime(card, parse(buf));
}
function parse(buf) {
  const dv = new DataView(buf), hl = dv.getUint32(0, true);
  const head = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 4, hl)));
  if (head.note) return {head};
  let off = 4 + hl;
  const n = head.n, x = new Float64Array(buf, off, n);
  off += 8 * n;
  const plots = head.plots.map((p) => p.series.map(() => {
    const a = new Float32Array(buf, off, n), out = new Array(n);
    off += 4 * n;
    for (let k = 0; k < n; k++) { const v = a[k]; out[k] = v === v ? v : null; }
    return out;
  }));
  return {head, x, plots};
}
function note(card, text) {
  card.plots.forEach((p) => p.u.destroy());
  card.plots = [];
  card.shape = '';
  card.head = null;
  card.el.querySelector('.cnote').textContent = '';
  card.el.querySelector('.cbody').innerHTML = `<div class="prompt">${esc(text || '')}</div>`;
}

// ---------------------------------------------------------------- time charts (uPlot)
function drawTime(card, d) {
  const head = d.head;
  if (head.note) return note(card, head.note);
  card.head = head;
  card.el.querySelector('.cnote').textContent = `${head.trials} trial${head.trials === 1 ? '' : 's'}`;
  const n = head.plots.length;
  const h = card.cfg.kind === 'Stimulus' ? 200 : n === 1 ? 300 : n === 2 ? 210 : 170;
  const width = plotWidth(card);
  const shape = JSON.stringify([width, h, head.plots.map((p) => [p.label, p.unit, p.series.map((s) => [s.c, s.w]), p.bands])]);
  if (shape !== card.shape) build(card, head, width, h);
  card.shape = shape;
  const xr = B.x || [0, head.end];
  card.plots.forEach((pv, k) => {
    const p = head.plots[k];
    pv.auto = p.yr;
    const yr = card.yr[p.label] || pad(p.yr);
    pv.u.batch(() => {
      pv.u.setData([d.x, ...d.plots[k]], false);
      pv.u.setScale('x', {min: xr[0], max: xr[1]});
      pv.u.setScale('y', {min: yr[0], max: yr[1]});
    });
  });
}
const pad = (r) => {
  if (!r) return [-1, 1];
  let [lo, hi] = r;
  if (hi - lo < 1e-12) { const m = Math.abs(lo) * 0.1 || 1; lo -= m; hi += m; }
  const p = (hi - lo) * 0.06;
  return [lo - p, hi + p];
};

function build(card, head, width, h) {
  card.plots.forEach((p) => p.u.destroy());
  card.plots = [];
  const body = card.el.querySelector('.cbody');
  body.replaceChildren();
  const muted = cssv('--muted'), line = cssv('--line');
  const grid = {stroke: line, width: 1}, ticks = {stroke: line, width: 1, size: 4};
  head.plots.forEach((p, k) => {
    const last = k === head.plots.length - 1;
    const wrap = document.createElement('div');
    wrap.className = 'plot';
    const deriv = p.label.match(/ (d²?V\/dt²?)$/);
    wrap.innerHTML = `<div class="ptitle">${esc(deriv ? p.label.slice(0, -deriv[0].length) : p.label)}${deriv ? ` <span class="muted">${deriv[1]}</span>` : ''}</div>`;
    body.append(wrap);
    const pv = {auto: null};
    const u = new uPlot({
      width, height: h + (last ? 30 : 0),
      padding: [6, 12, 0, 0],
      legend: {show: false},
      cursor: {sync: {key: 'board'}, y: false, points: {show: false}, drag: {x: false, y: false, setScale: false}},
      scales: {x: {time: false, auto: false}, y: {auto: false}},
      axes: [
        {stroke: muted, grid, ticks, font: FONT, size: last ? 30 : 6, space: 70,
         values: last ? (u, v) => tickText(v, '').map((t) => t + ' s') : (u, v) => v.map(() => '')},
        {stroke: muted, grid, ticks, font: FONT, size: 76, values: (u, v) => tickText(v, p.unit)},
      ],
      series: [{}, ...p.series.map((s) => ({stroke: s.w ? s.c : 'transparent', width: s.w || 0, points: {show: false},
                                            spanGaps: false}))],
      bands: p.bands.map(([a, b, fill]) => ({series: [a, b], fill})),
    }, [[], ...p.series.map(() => [])], wrap);
    pv.u = u;
    pv.label = p.label;
    zoomable(card, pv);
    card.plots.push(pv);
  });
}

// zooming: the time is shared by every time chart (as Qt links them); y is each plot's own
function setX(card, x0, x1) {
  const end = card.head ? card.head.end : 1;
  let w = x1 - x0;
  const least = end / 20000;
  if (w < least) { const m = (x0 + x1) / 2; x0 = m - least / 2; x1 = m + least / 2; w = least; }
  if (w >= end) B.x = null;                                // all of it: zooming out reaches the whole trial
  else {
    if (x0 < 0) { x1 -= x0; x0 = 0; }
    if (x1 > end) { x0 -= x1 - end; x1 = end; }
    B.x = [x0, x1];
  }
  const xr = B.x || [0, end];
  for (const c of B.cards.values()) {
    if (c.cfg.kind === 'Raster' && c.data) drawRaster(c);          // the raster's time follows them
    if (!c.time) continue;
    const r = B.x || [0, c.head ? c.head.end : xr[1]];
    c.plots.forEach((pv) => pv.u.setScale('x', {min: r[0], max: r[1]}));
    clearTimeout(c.timer);
    c.timer = setTimeout(() => fetchData(c), 110);         // then the detail, from Python
  }
}
function whole(card) {
  card.yr = {};
  for (const c of B.cards.values()) if (c.time) c.yr = {};
  setX(card, 0, Infinity);
}
function zoomable(card, pv) {
  const u = pv.u, over = u.over;
  const xAt = (e) => u.posToVal(e.clientX - over.getBoundingClientRect().left, 'x');
  over.addEventListener('wheel', (e) => {
    e.preventDefault();
    const sx = u.scales.x, sy = u.scales.y;
    const yZoom = e.metaKey || (!isMac && e.ctrlKey);
    if (yZoom) {                                           // up and down, around the mouse
      const y = u.posToVal(e.clientY - over.getBoundingClientRect().top, 'y');
      const f = Math.exp(e.deltaY * 0.002);
      card.yr[pv.label] = [y - (y - sy.min) * f, y + (sy.max - y) * f];
      u.setScale('y', {min: card.yr[pv.label][0], max: card.yr[pv.label][1]});
      return;
    }
    const w = sx.max - sx.min;
    if (e.shiftKey || Math.abs(e.deltaX) > Math.abs(e.deltaY)) {        // along in time
      const d = (Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY) / over.clientWidth * w;
      return setX(card, sx.min + d, sx.max + d);
    }
    const m = xAt(e), f = Math.exp(e.deltaY * (e.ctrlKey ? 0.01 : 0.002));   // (a pinch comes with Ctrl)
    setX(card, m - (m - sx.min) * f, m + (sx.max - m) * f);
  }, {passive: false});
  let drag = null;
  over.addEventListener('pointerdown', (e) => {
    if (e.button !== 0) return;
    drag = {x: e.clientX, min: u.scales.x.min, max: u.scales.x.max};
    over.setPointerCapture(e.pointerId);
    over.classList.add('grabbing');
  });
  over.addEventListener('pointermove', (e) => {
    if (!drag) return;
    const d = (drag.x - e.clientX) / over.clientWidth * (drag.max - drag.min);
    setX(card, drag.min + d, drag.max + d);
  });
  const stop = () => { drag = null; over.classList.remove('grabbing'); };
  over.addEventListener('pointerup', stop);
  over.addEventListener('pointercancel', stop);
  over.addEventListener('dblclick', () => whole(card));
}

// ---------------------------------------------------------------- a measure against the stimulus (SVG)
function drawScatter(card, d) {
  card.head = null;
  const body = card.el.querySelector('.cbody');
  card.el.querySelector('.cnote').textContent = '';
  if (d.note) return note(card, d.note);
  const pts = d.points, gs = d.groups;
  const xs = [...pts.map((p) => p[0]), ...gs.flatMap((g) => [g.x - g.ex, g.x + g.ex])];
  const ys = [...pts.map((p) => p[1]), ...gs.flatMap((g) => [g.y - g.ey, g.y + g.ey])];
  if (!xs.length) return note(card, 'Nothing to plot: no trial has both numbers');
  const range = (v) => {
    let lo = Math.min(...v), hi = Math.max(...v);
    if (hi - lo < 1e-12) { const m = Math.abs(lo) * 0.1 || 1; lo -= m; hi += m; }
    const p = (hi - lo) * 0.07;
    return [lo - p, hi + p];
  };
  const [x0, x1] = range(xs), [y0, y1] = range(ys);
  const L = 78, R = 16, T = 10, Bm = 50;
  const side = Math.max(220, Math.min(460, body.clientWidth - L - R));     // a square plot box
  const W = side + L + R, Hh = side + T + Bm;
  const X = (v) => L + (v - x0) / (x1 - x0) * side, Y = (v) => T + side - (v - y0) / (y1 - y0) * side;
  const xt = niceTicks(x0, x1), yt = niceTicks(y0, y1);
  const xl = tickText(xt, d.x.unit), yl = tickText(yt, d.y.unit);
  const unitOf = (ticks) => (ticks.find((t) => t.includes(' ')) || '').split(' ').slice(1).join(' ');
  let s = `<svg class="scatter" viewBox="0 0 ${W} ${Hh}" width="${W}" height="${Hh}">`;
  s += xt.map((v) => `<line class="grid" x1="${X(v)}" x2="${X(v)}" y1="${T}" y2="${T + side}"/>`).join('');
  s += yt.map((v) => `<line class="grid" x1="${L}" x2="${L + side}" y1="${Y(v)}" y2="${Y(v)}"/>`).join('');
  s += `<rect class="frame" x="${L}" y="${T}" width="${side}" height="${side}"/>`;
  s += xt.map((v, k) => `<text class="tick" x="${X(v)}" y="${T + side + 16}" text-anchor="middle">${esc(xl[k].split(' ')[0])}</text>`).join('');
  s += yt.map((v, k) => `<text class="tick" x="${L - 6}" y="${Y(v) + 4}" text-anchor="end">${esc(yl[k].split(' ')[0])}</text>`).join('');
  const xu = unitOf(xl), yu = unitOf(yl);
  s += `<text class="alab" x="${L + side / 2}" y="${Hh - 8}" text-anchor="middle">${esc(d.x.label)}${xu ? ` (${esc(xu)})` : ''}</text>`;
  s += `<text class="alab" transform="translate(14 ${T + side / 2}) rotate(-90)" text-anchor="middle">${esc(d.y.label)}${yu ? ` (${esc(yu)})` : ''}</text>`;
  const num = (v, u) => tickText([v], u)[0];
  s += pts.map(([x, y, c, t]) => `<circle cx="${X(x).toFixed(1)}" cy="${Y(y).toFixed(1)}" r="3.4" fill="${c}"><title>${esc(t)}: ${esc(num(x, d.x.unit))}, ${esc(num(y, d.y.unit))}</title></circle>`).join('');
  for (const g of gs) {
    const cx = X(g.x), cy = Y(g.y);
    s += '<g class="gpt">';
    if (g.ey) s += `<path d="M${cx} ${Y(g.y - g.ey)}V${Y(g.y + g.ey)}M${cx - 4} ${Y(g.y - g.ey)}h8M${cx - 4} ${Y(g.y + g.ey)}h8" stroke="${g.c}"/>`;
    if (g.ex) s += `<path d="M${X(g.x - g.ex)} ${cy}H${X(g.x + g.ex)}M${X(g.x - g.ex)} ${cy - 4}v8M${X(g.x + g.ex)} ${cy - 4}v8" stroke="${g.c}"/>`;
    s += `<circle cx="${cx}" cy="${cy}" r="6.5" fill="${g.c}"><title>${esc(g.name)}: average of ${g.n}, ${esc(num(g.x, d.x.unit))}, ${esc(num(g.y, d.y.unit))}${d.spread ? ` (± ${d.spread.toUpperCase()})` : ''}</title></circle></g>`;
  }
  s += '</svg>';
  const n = gs.length, t = pts.length;
  card.el.querySelector('.cnote').textContent = [n && `${n} average${n === 1 ? '' : 's'}`, t && `${t} trial${t === 1 ? '' : 's'}`].filter(Boolean).join(' · ');
  body.innerHTML = s + `<p class="caption">${esc(d.y.label)} ${esc(d.y.when)}, against the stimulus.` +
    (d.missing ? ` ${d.missing} trial${d.missing === 1 ? ' has' : 's have'} no number (no stimulus found in ${d.missing === 1 ? 'it' : 'them'}?).` : '') + '</p>';
}

// ---------------------------------------------------------------- the spikes found
// the Spikes panel found new spikes, or a unit was ticked or recolored (sidepanels.js)
window.boardSpikes = () => { for (const c of B.cards.values()) if (SPIKY.has(c.cfg.kind)) fetchData(c); };
// the trial looked at in the Trials pane (analysis.js): the raster marks its row
window.boardCurrent = () => { for (const c of B.cards.values()) if (c.cfg.kind === 'Raster' && c.data) drawRaster(c); };

function drawRaster(card) {
  const d = card.data, body = card.el.querySelector('.cbody');
  card.head = null;
  if (d.note) return note(card, d.note);
  const rows = d.rows.length, nd = d.t.length;
  card.el.querySelector('.cnote').textContent = `${nd} spike${nd === 1 ? '' : 's'} in ${rows} trial${rows === 1 ? '' : 's'}`;
  const L = 76, R = 12, T = 6, Bm = 30;
  const W = Math.max(200, plotWidth(card)), side = W - L - R;
  const rowH = Math.max(2, Math.min(14, Math.floor(380 / Math.max(1, rows))));
  const H = T + rows * rowH + Bm;
  let cv = body.querySelector('canvas.raster');
  if (!cv) { body.innerHTML = '<canvas class="raster"></canvas>'; cv = body.querySelector('canvas'); }
  const dpr = window.devicePixelRatio || 1;
  cv.width = W * dpr; cv.height = H * dpr; cv.style.width = W + 'px'; cv.style.height = H + 'px';
  const g = cv.getContext('2d');
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, W, H);
  const [x0, x1] = B.x || [0, d.end];
  const X = (t) => L + (t - x0) / (x1 - x0) * side;
  const muted = cssv('--muted'), line = cssv('--line');
  g.font = FONT; g.fillStyle = muted; g.strokeStyle = line; g.lineWidth = 1;
  const xt = niceTicks(x0, x1), xl = tickText(xt, '');
  g.textAlign = 'center';
  xt.forEach((v, k) => {
    const x = Math.round(X(v)) + 0.5;
    g.beginPath(); g.moveTo(x, T); g.lineTo(x, T + rows * rowH); g.stroke();
    g.fillText(xl[k] + ' s', x, T + rows * rowH + 18);
  });
  g.strokeRect(L + 0.5, T + 0.5, side, rows * rowH);
  g.save(); g.translate(16, T + rows * rowH / 2); g.rotate(-Math.PI / 2); g.textAlign = 'center';
  g.fillText('trial', 0, 0); g.restore();
  const cur = window.trialsCurrent ? window.trialsCurrent() : null;
  const at = d.rows.findIndex((r) => r.i === cur);
  if (at >= 0) { g.fillStyle = 'rgba(86,180,233,.22)'; g.fillRect(L, T + at * rowH, side, rowH); }
  d.rows.forEach((r, k) => { if (r.c) { g.fillStyle = r.c; g.fillRect(L - 8, T + k * rowH, 6, rowH); } });
  g.save(); g.beginPath(); g.rect(L, T, side, rows * rowH); g.clip();
  const sz = Math.max(1, Math.min(d.dots, rowH + 2)), half = sz / 2;
  let last = null;
  for (let k = 0; k < nd; k++) {
    const t = d.t[k];
    if (t < x0 || t > x1) continue;
    const c = d.colors[d.u[k]];
    if (c !== last) { g.fillStyle = c; last = c; }
    g.fillRect(X(t) - half, T + (d.r[k] + 0.5) * rowH - half, sz, sz);
  }
  g.restore();
  cv.onclick = (e) => {
    const k = Math.floor((e.clientY - cv.getBoundingClientRect().top - T) / rowH);
    if (k >= 0 && k < rows && window.showTrial) window.showTrial(d.rows[k].i);
  };
  cv.onmousemove = (e) => {
    const k = Math.floor((e.clientY - cv.getBoundingClientRect().top - T) / rowH);
    cv.title = k >= 0 && k < rows ? d.rows[k].stem : '';
  };
}

function drawWaves(card, d) {
  card.head = null;
  const body = card.el.querySelector('.cbody');
  if (d.note) return note(card, d.note);
  const t = d.t, us = d.units;
  if (!us.length) return note(card, 'No units ticked: tick one in the Spikes panel');
  const ys = us.flatMap((u) => [...u.mean.map((v, k) => v - (u.band ? u.band[k] : 0)), ...u.mean.map((v, k) => v + (u.band ? u.band[k] : 0)),
                                ...u.each.flat()]).filter((v) => isFinite(v));
  let y0 = Math.min(...ys), y1 = Math.max(...ys);
  if (y1 - y0 < 1e-15) { y0 -= 1; y1 += 1; }
  const py = (y1 - y0) * 0.06; y0 -= py; y1 += py;
  const x0 = t[0], x1 = t[t.length - 1];
  const L = 78, R = 16, T = 10, Bm = 50;
  const W = Math.max(260, Math.min(620, body.clientWidth)), side = W - L - R, h = Math.round(side * 0.62), Hh = h + T + Bm;
  const X = (v) => (L + (v - x0) / (x1 - x0) * side).toFixed(1), Y = (v) => (T + h - (v - y0) / (y1 - y0) * h).toFixed(1);
  const xt = niceTicks(x0, x1), yt = niceTicks(y0, y1), yl = tickText(yt, d.y.unit);
  const yu = (yl.find((s) => s.includes(' ')) || '').split(' ').slice(1).join(' ');
  let s = `<svg class="scatter" viewBox="0 0 ${W} ${Hh}" width="${W}" height="${Hh}">`;
  s += xt.map((v) => `<line class="grid" x1="${X(v)}" x2="${X(v)}" y1="${T}" y2="${T + h}"/>`).join('');
  s += yt.map((v) => `<line class="grid" x1="${L}" x2="${L + side}" y1="${Y(v)}" y2="${Y(v)}"/>`).join('');
  s += `<rect class="frame" x="${L}" y="${T}" width="${side}" height="${h}"/>`;
  s += xt.map((v) => `<text class="tick" x="${X(v)}" y="${T + h + 16}" text-anchor="middle">${minus(+v.toFixed(3))}</text>`).join('');
  s += yt.map((v, k) => `<text class="tick" x="${L - 6}" y="${+Y(v) + 4}" text-anchor="end">${esc(yl[k].split(' ')[0])}</text>`).join('');
  s += `<text class="alab" x="${L + side / 2}" y="${Hh - 8}" text-anchor="middle">ms from the peak</text>`;
  s += `<text class="alab" transform="translate(14 ${T + h / 2}) rotate(-90)" text-anchor="middle">${esc(d.y.label)}${yu ? ` (${esc(yu)})` : ''}</text>`;
  const path = (y) => y.map((v, k) => `${k ? 'L' : 'M'}${X(t[k])} ${Y(v)}`).join('');
  for (const u of us) {
    if (u.each.length) s += `<path d="${u.each.map(path).join('')}" stroke="${u.c}" stroke-opacity="${(0.16 * u.a).toFixed(3)}" fill="none" stroke-width="1"/>`;
  }
  for (const u of us) {
    if (u.band) {
      const hi = u.mean.map((v, k) => v + u.band[k]), lo = u.mean.map((v, k) => v - u.band[k]).reverse();
      s += `<path d="${path(hi)}${lo.map((v, k) => `L${X(t[t.length - 1 - k])} ${Y(v)}`).join('')}Z" fill="${u.c}" fill-opacity="${(0.22 * u.a).toFixed(3)}" stroke="none"/>`;
    }
    s += `<path d="${path(u.mean)}" stroke="${u.c}" stroke-opacity="${u.a}" fill="none" stroke-width="3"><title>Unit ${u.u + 1}: average of ${u.n} spike${u.n === 1 ? '' : 's'}</title></path>`;
  }
  s += '</svg>';
  card.el.querySelector('.cnote').textContent = us.map((u) => `Unit ${u.u + 1}: ${u.n}`).join(' · ');
  body.innerHTML = s + `<p class="caption">Up to 150 of each unit’s spikes, faint, behind their average${d.spread ? ` (± ${d.spread.toUpperCase()} shaded)` : ''}.</p>`;
}

// ---------------------------------------------------------------- the board's own controls
$('addChart').onclick = (e) => {
  e.stopPropagation();
  const m = $('addMenu');
  m.hidden = !m.hidden;
  if (!m.hidden) m.innerHTML = Object.entries(B.state.kinds).map(([k, name]) =>
    `<button data-k="${esc(k)}"><b>${esc(name)}</b><span>${esc(ABOUT[k] || '')}</span></button>`).join('');
};
$('addMenu').onclick = async (e) => {
  const b = e.target.closest('[data-k]');
  if (!b) return;
  $('addMenu').hidden = true;
  await cmd({cmd: 'add', kind: b.dataset.k});
  const last = [...B.cards.values()].pop();
  if (last) last.el.scrollIntoView({behavior: 'smooth', block: 'nearest'});
};
document.addEventListener('click', (e) => { if (!e.target.closest('#addMenu')) $('addMenu').hidden = true; });
$('spread').onclick = (e) => { const s = e.target.dataset.s; if (s !== undefined && B.state) cmd({cmd: 'spread', spread: s}); };
let resizeTimer = 0;
window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => {
  for (const c of B.cards.values()) c.shape = '';
  refreshAll();
}, 150); });
})();
