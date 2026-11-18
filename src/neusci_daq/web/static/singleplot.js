// Single Plot: one figure, made to look right, drawn by Python (web/figures.py, the Qt app's
// figspec.py with matplotlib) and shown here as a picture at its size in mm, scaled to fit.
// A figure starts from DataPad columns (nothing is chosen for the student) or from a chart,
// and figures of columns follow the DataPad. The settings on the right change it at once;
// drag the plot to move what it shows, scroll to zoom. Uses the Trials pane (analysis.js).
'use strict';

(() => {
const {post, get, esc, sub, plain, save} = Figs;
const E = (id) => document.getElementById(id);
const side = E('side'), img = E('figimg'), area = E('area');
const P = {
  st: null, C: null, sig: '', seriesSig: '', v: -1,
  geo: null, busy: false, again: false, againLow: false,
  view: null, viewTimer: 0, pending: null, sending: false, drag: null,
  maker: {kind: null, x: null, ys: []}, makerOpen: false,
};

// ---------------------------------------------------------------- talking to Python
async function load() {
  try { show(await (await get('/fig/state')).json()); }
  catch (e) { problem('Newt is not answering.'); }
}
async function fcmd(d) {
  let st;
  try { st = await (await post('/fig/cmd', d)).json(); }
  catch (e) { problem('Newt is not answering.'); return null; }
  show(st);
  problem(st.problem || '');
  return st;
}
function problem(text, good) {
  const p = E('fproblem');
  p.textContent = text || '';
  p.hidden = !text;
  p.className = good ? 'note' : 'problem';
}

// the Trials pane changed (analysis.js show()): the figure's numbers follow
window.boardTrials = (st) => {
  const sig = JSON.stringify([st.folder, st.how, st.looks, st.groups.map((g) => [g.name, g.color, g.hidden,
    g.trials.map((t) => t.off ? -1 - t.i : t.i)])]);
  if (sig === P.sig && P.st) return;
  P.sig = sig;
  load();
};

// ---------------------------------------------------------------- the settings, built once
const NUMS = [...side.querySelectorAll('.num')];
for (const box of NUMS) {
  const d = box.dataset;
  box.innerHTML = `<input type="range" min="${d.lo}" max="${d.hi}" step="${d.step}"><input type="number" min="${d.lo}" max="${d.hi}" step="${d.step}"${d.auto ? ` placeholder="${d.auto}"` : ''} title="${esc(box.title)}${d.unit ? ' (' + d.unit + ')' : ''}">`;
  const [range, num] = box.querySelectorAll('input');
  let t = 0;
  const value = (v) => {
    if (v === '' || v == null) return d.auto ? 0 : null;
    v = Math.max(+d.lo, Math.min(+d.hi, +v));
    return d.int !== undefined ? Math.round(v) : v;
  };
  range.oninput = () => { num.value = d.auto && +range.value === 0 ? '' : range.value; clearTimeout(t); t = setTimeout(() => setv(d.num, value(range.value)), 140); };
  range.onchange = () => { clearTimeout(t); setv(d.num, value(range.value)); };
  num.onchange = () => { const v = value(num.value); if (v != null) setv(d.num, v); };
}
function opts(sel, items, keep) {           // items: [[value, label]]
  sel.innerHTML = items.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join('');
  if (keep !== undefined) sel.value = keep;
}
function built(C) {
  opts(side.querySelector('[data-k=family]'), C.families.map((f) => [f, f]));
  opts(side.querySelector('[data-k=legend]'), C.legends.map((f) => [f, f]));
  opts(side.querySelector('[data-k=fit]'), Object.entries(C.fits));
  opts(side.querySelector('[data-k=fit_style]'), Object.entries(C.fit_styles));
  opts(side.querySelector('[data-k=fit_span]'), Object.entries(C.fit_spans));
  opts(side.querySelector('[data-k=fit_where]'), Object.entries(C.fit_where));
  E('fitshow').innerHTML = Object.entries(C.fit_show).map(([k, l]) =>
    `<label title="${k === 'r' ? 'For a straight line, the correlation coefficient (signed like the slope); for other curves, √R²' : k === 'coef' ? 'Each coefficient ± its standard error' : ''}"><input type="checkbox" data-fs="${k}"> ${esc(l)}</label>`).join('');
  E('presets').innerHTML = Object.keys(C.presets).map((n) =>
    `<button data-preset="${esc(n)}" title="${esc(n)}: its text, lines, points, ticks and size, all at once">${esc(n)}</button>`).join('');
}

const setv = (k, v) => fcmd({cmd: 'set', values: {[k]: v}});
const g6 = (v) => v == null || v === '' ? '' : String(+(+v).toPrecision(6));

side.addEventListener('change', (e) => {
  const el = e.target;
  if (el.closest('.series') || el.closest('.num')) return;
  if (el.dataset.link) return fcmd({cmd: 'link', section: el.dataset.link, on: el.checked});
  if (el.dataset.fs) return setv('fit_show', [...E('fitshow').querySelectorAll('input:checked')].map((x) => x.dataset.fs));
  if (el.id === 'fitline') return setv('fit_line', Math.max(0, el.selectedIndex));
  const k = el.dataset.k;
  if (!k) return;
  if (el.dataset.i !== undefined) {
    return setv(k, [...side.querySelectorAll(`[data-k="${k}"][data-i]`)].map((x) => x.value === '' ? null : +x.value));
  }
  if (k === 'fit_degree') return setv(k, Math.max(0, Math.min(9, Math.round(+el.value || 0))));
  if (k === 'fit_width') return setv(k, Math.max(0.25, Math.min(6, +el.value || 1.5)));
  if (el.type === 'checkbox') return setv(k, el.checked);
  if (k === 'grid') return setv(k, {none: false, y: 'y', xy: true}[el.value]);
  setv(k, el.value);
});
side.addEventListener('click', (e) => {
  const b = e.target.closest('button');
  if (e.target.closest('summary label')) { e.stopPropagation(); return; }   // the 🔗 box doesn't fold the section
  if (!b) return;
  if (b.dataset.preset) return fcmd({cmd: 'look', preset: b.dataset.preset});
  if (b.dataset.tab) {
    for (const t of side.querySelectorAll('[data-tab]')) t.setAttribute('aria-selected', t === b);
    E('tab-fig').hidden = b.dataset.tab !== 'fig';
    E('tab-fit').hidden = b.dataset.tab !== 'fit';
  }
});
for (const l of side.querySelectorAll('summary label')) l.addEventListener('click', (e) => e.stopPropagation());
E('copylook').onclick = () => fcmd({cmd: 'copy_look'}).then((st) => st && !st.problem && problem('Look copied: open another figure and Paste look', true));
E('pastelook').onclick = () => fcmd({cmd: 'look'});
E('every').onclick = () => fcmd({cmd: 'every'}).then((st) => st && !st.problem &&
  problem(st.panels ? 'Every figure on the Plot Canvas has this look now (each keeps its size)' : 'No figures on the Plot Canvas yet', true));
E('fitview').onclick = () => {
  const s = P.st && P.st.spec;
  if (!s) return;
  const lim = s.xlim && s.xlim.some((v) => v != null) ? s.xlim : P.geo ? P.geo.xlim : [null, null];
  setv('fit_x', [lim[0] == null ? null : +g6(lim[0]), lim[1] == null ? null : +g6(lim[1])]);
};
E('fitcopy').onclick = () => { navigator.clipboard && navigator.clipboard.writeText(E('fitres').innerText).catch(() => {}); };

// ---------------------------------------------------------------- showing the state
function show(st) {
  if (!st || st.consts === undefined) return;
  if (!P.C) { P.C = st.consts; built(P.C); }
  P.st = st;
  const s = st.spec, C = P.C;
  side.setAttribute('aria-disabled', !s);
  for (const id of ['copy', 'savebtn', 'tocanvas', 'grab', 'zin', 'zout', 'zfit']) E(id).disabled = !s;
  E('tocanvas').textContent = st.editing ? 'Update on Canvas' : 'Send to Canvas';
  E('pastelook').disabled = !st.copied;
  for (const box of side.querySelectorAll('[data-link]')) box.checked = !!(st.links || {})[box.dataset.link];
  if (!st.folder) E('finfo').innerHTML = 'Open a session in the Trials pane';
  else if (!s) E('finfo').innerHTML = 'No figure yet';
  else if (s.kind === 'curves') E('finfo').innerHTML = esc(st.info);
  else E('finfo').innerHTML = `<b>${s.series.map((x) => sub(shown(x.y))).join(', ') || 'Nothing plotted'}</b> · ${esc(st.info)}`;
  maker();
  if (!s) { img.hidden = true; P.geo = null; return; }
  fill(s);
  series(s);
  fit(s, st.fit);
  if (st.v !== P.v) { P.v = st.v; render(!!P.drag); }
}
const shown = (name) => { const c = (P.st.names || []).find((n) => n.name === name); return c ? c.shown : name; };

function fill(s) {
  const C = P.C, look = {...C.look_defaults, ...C.fit_look, ...s};
  for (const el of side.querySelectorAll('[data-k]')) {
    if (el === document.activeElement) continue;
    const k = el.dataset.k;
    let v = look[k];
    if (k === 'box') v = s.box || (s.despine === false ? 'closed' : 'open');
    if (k === 'grid') v = v === 'y' ? 'y' : v ? 'xy' : 'none';
    if (k === 'fit_color') v = s.fit_color || C.fit_look.fit_color;
    if (el.dataset.i !== undefined) v = (v || [])[+el.dataset.i];
    if (el.type === 'checkbox') el.checked = k === 'points' ? v !== false : !!v;
    else if (el.type === 'number') el.value = g6(v);
    else el.value = v == null ? '' : v;
  }
  for (const box of NUMS) {
    const d = box.dataset, [range, num] = box.querySelectorAll('input');
    const v = look[d.num];
    if (num !== document.activeElement) num.value = d.auto && !v ? '' : g6(v);
    if (range !== document.activeElement) range.value = v || 0;
  }
  const curves = s.kind === 'curves' || s.kind === 'image';
  E('datapart').hidden = curves;
  E('datahead').hidden = curves;
  E('fromchart').hidden = !curves;
  E('fromchart').textContent = curves ? 'The lines of a chart, as they were when you made the figure. Make it again from the chart (New figure ▾) to update it.' : '';
  side.querySelector('[data-k=points]').parentElement.hidden = s.kind !== 'bar';
  side.querySelector('[data-k=fit_degree]').disabled = s.fit !== 'poly';
  for (const b of E('fitshow').querySelectorAll('input')) b.checked = (s.fit_show || []).includes(b.dataset.fs);
}

// ---------------------------------------------------------------- what's plotted: the series
function series(s) {
  const names = P.st.names || [];
  const sig = JSON.stringify([s.kind, s.series, names]);
  if (sig === P.seriesSig) return;
  P.seriesSig = sig;
  const C = P.C;
  const colOpts = (cur, blank) => (blank ? `<option value="">${blank}</option>` : '') +
    names.map((n) => `<option value="${esc(n.name)}">${esc(plain(n.shown))}${n.unit ? ' (' + esc(n.unit) + ')' : ''}</option>`).join('') +
    (cur && !names.some((n) => n.name === cur) ? `<option value="${esc(cur)}">${esc(plain(cur))} (not on the DataPad now)</option>` : '');
  const bar = s.kind === 'bar';
  E('serieslist').innerHTML = s.series.map((x, i) => `<div class="series" data-i="${i}">
    <div class="top"><select class="t" data-s="y" title="What runs up (Y)">${colOpts(x.y)}</select><button class="x" data-s="rm" title="Take this series off">✕</button></div>
    <div class="form">
      ${bar ? '' : `<span>Against</span><select class="t" data-s="x" title="What runs across (X)">${colOpts(x.x, 'Trial order')}</select>
      <span>Draw</span><select class="t" data-s="style">${Object.entries(C.styles).map(([k, l]) => `<option value="${k}">${esc(l)}</option>`).join('')}</select>`}
      <span>${bar ? 'Bars' : 'Trials'}</span><select class="t" data-s="average" title="${bar ? 'Each bar: its group’s mean, with ± SEM or ± SD' : 'Every trial its own point, or the trials with the same x averaged (an I–V curve from repeated steps), with error bars'}">${Object.entries(C.averages).filter(([k]) => !bar || k).map(([k, l]) => `<option value="${k}">${esc(bar ? l.replace(' at each x', '') : l)}</option>`).join('')}</select>
      ${bar ? '' : '<label class="full"><input type="checkbox" data-s="by_group"> Split into groups</label>'}
      <span>Color</span><span class="dd"><button class="swatch${x.color ? '' : ' auto'}" data-s="color" style="${x.color ? 'background:' + esc(x.color) : ''}" title="The series' color; Groups' colors uses each group's">${x.color ? '' : 'Groups’ colors'}</button></span>
      <span>Legend</span><input type="text" class="t" data-s="label" placeholder="legend: automatic" value="${esc(x.label || '')}">
    </div></div>`).join('') || '<p class="caption">No series: + Series adds one.</p>';
  for (const row of E('serieslist').querySelectorAll('.series')) {
    const x = s.series[+row.dataset.i];
    for (const el of row.querySelectorAll('select[data-s]')) el.value = x[el.dataset.s] || (el.dataset.s === 'average' && bar ? 'sem' : '');
    const g = row.querySelector('[data-s=by_group]');
    if (g) g.checked = x.by_group !== false;
  }
}
function collect() {
  const s = P.st.spec;
  return [...E('serieslist').querySelectorAll('.series')].map((row) => {
    const old = s.series[+row.dataset.i] || {}, out = {...old};
    for (const el of row.querySelectorAll('[data-s]')) {
      const k = el.dataset.s;
      if (k === 'rm' || k === 'color') continue;
      out[k] = el.type === 'checkbox' ? el.checked : el.value;
    }
    return out;
  });
}
E('serieslist').addEventListener('change', () => fcmd({cmd: 'series', series: collect()}));
E('serieslist').addEventListener('click', (e) => {
  const b = e.target.closest('button[data-s]');
  if (!b) return;
  const i = +b.closest('.series').dataset.i;
  if (b.dataset.s === 'rm') return fcmd({cmd: 'series', series: collect().filter((_, j) => j !== i)});
  colorMenu(b, i);
});
E('addseries').onclick = () => {
  const names = P.st.names || [];
  if (!names.length) return problem('Add a column on the DataPad first');
  const all = collect(), last = all[all.length - 1] || {x: '', y: names[0].name};
  fcmd({cmd: 'series', series: [...all, {x: last.x, y: last.y, by_group: true, style: 'markers', average: '', color: '', label: ''}]});
};
function colorMenu(btn, i) {
  let m = btn.parentElement.querySelector('.menu');
  if (m) { m.remove(); return; }
  m = document.createElement('div');
  m.className = 'menu right';
  m.innerHTML = `<button data-c="">Groups’ colors<span>Each group its own</span></button>
    <div class="swatches">${P.C.palette.map((c) => `<button data-c="${c}" style="background:${c}" title="${c}"></button>`).join('')}</div>
    <button data-c="other">Other…</button>`;
  btn.parentElement.append(m);
  const set = (c) => { const all = collect(); all[i].color = c; m.remove(); fcmd({cmd: 'series', series: all}); };
  m.onclick = (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    if (b.dataset.c !== 'other') return set(b.dataset.c);
    const pick = E('colorpick');
    pick.value = P.st.spec.series[i].color || '#1f5fbf';
    pick.onchange = () => set(pick.value);
    pick.click();
  };
}

// ---------------------------------------------------------------- the fit
function fit(s, f) {
  const sel = E('fitline'), lines = (f && f.lines) || [];
  sel.innerHTML = lines.map((n) => `<option>${esc(plain(n))}</option>`).join('');
  sel.selectedIndex = lines.length ? Math.min(s.fit_line || 0, lines.length - 1) : -1;
  const r = E('fitres');
  if (s.kind === 'bar' || s.kind === 'image') { r.innerHTML = '<span class="caption">Bars aren’t fitted: make a Y against X figure to fit a curve.</span>'; return; }
  if (!s.fit) { r.innerHTML = '<span class="caption">Pick a model to fit: the fitted curve shows on the figure as a red dashed line.</span>'; return; }
  if (f && f.error) { r.innerHTML = `<span class="bad">${esc(f.error)}</span>`; return; }
  if (!f || !f.name) { r.innerHTML = '<span class="caption">Nothing to fit through yet.</span>'; return; }
  const num = (v, d) => (+v).toPrecision(d).replace(/^(-?)/, (m, a) => a ? '−' : '');
  r.innerHTML = `<b>${sub(f.name)}</b><span class="eq">${esc(f.equation)}</span>
    <table>${f.table.map(([n, v, e]) => `<tr><td><b>${esc(n)}</b></td><td align="right">${num(v, 5)}</td><td>± ${num(e, 3)}</td></tr>`).join('')}</table>
    <span>R² = ${(+f.r2).toFixed(4)} &nbsp; R = ${(+f.R).toFixed(4)} &nbsp; n = ${f.n}</span>`;
}

// ---------------------------------------------------------------- a new figure
function maker() {
  const st = P.st, m = P.maker, names = st.names || [];
  const open = !st.spec || P.makerOpen;
  E('maker').hidden = !open;
  E('makerx').hidden = !st.spec;
  if (!open) return;
  for (const b of E('mkind').querySelectorAll('button')) b.setAttribute('aria-pressed', b.dataset.k === m.kind);
  m.ys = m.ys.filter((y) => names.some((n) => n.name === y));
  E('mys').innerHTML = names.length ? names.map((n) => `<button class="chip" data-y="${esc(n.name)}" aria-pressed="${m.ys.includes(n.name)}">${sub(n.shown)}</button>`).join('')
    : '<span class="caption">No columns yet: add some on the <a href="datapad.html' + location.hash + '">DataPad</a>.</span>';
  const xs = E('mx');
  xs.innerHTML = '<option value="" disabled hidden>Choose…</option><option value="#row">Trial order</option>' +
    names.map((n) => `<option value="${esc(n.name)}">${esc(plain(n.shown))}${n.unit ? ' (' + esc(n.unit) + ')' : ''}</option>`).join('');
  xs.value = m.x || '';
  const bar = m.kind === 'bar';
  xs.hidden = E('mxlab').hidden = bar;
  E('mgo').disabled = !(m.kind && m.ys.length && (bar || m.x));
  E('mnote').textContent = !m.kind ? 'Choose the kind, then Y' + (bar ? '' : ' and what it’s against') : '';
  E('msrcs').innerHTML = (st.sources || []).map((c) => `<button data-src="${c.src}" data-id="${esc(c.id)}" title="A figure of this chart: its lines as they are now">${esc(c.name)}<span>${esc(c.kind)}</span></button>`).join('') ||
    '<span class="caption">No charts yet: add some on Analysis or the DataPad.</span>';
}
E('mkind').onclick = (e) => { const b = e.target.closest('button'); if (b) { P.maker.kind = b.dataset.k; maker(); } };
E('mys').onclick = (e) => {
  const b = e.target.closest('button[data-y]');
  if (!b) return;
  const ys = P.maker.ys, y = b.dataset.y;
  P.maker.ys = ys.includes(y) ? ys.filter((z) => z !== y) : [...ys, y];
  maker();
};
E('mx').onchange = (e) => { P.maker.x = e.target.value; maker(); };
E('mgo').onclick = async () => {
  const m = P.maker;
  P.makerOpen = false;
  await fcmd({cmd: 'new', kind: m.kind, x: m.x, ys: m.ys});
};
E('msrcs').onclick = async (e) => {
  const b = e.target.closest('button[data-src]');
  if (!b) return;
  P.makerOpen = false;
  const st = await fcmd({cmd: 'chart', src: b.dataset.src, id: b.dataset.id});
  if (st && st.problem) { P.makerOpen = true; maker(); }
};
E('newbtn').onclick = () => { P.makerOpen = !P.makerOpen || !P.st.spec; maker(); };
E('makerx').onclick = () => { P.makerOpen = false; maker(); };

// ---------------------------------------------------------------- the picture
async function render(low) {
  const s = P.st && P.st.spec;
  if (!s) return;
  if (P.busy) { P.again = true; P.againLow = low; return; }
  P.busy = true;
  E('busydot').classList.add('on');
  const w_in = s.w_mm / 25.4, h_in = s.h_mm / 25.4, dpi = 96;
  const zoom = Math.min(1.6, Math.max(0.4, Math.min((area.clientWidth - 40) / (w_in * dpi), (area.clientHeight - 40) / (h_in * dpi))));
  const W = Math.round(w_in * dpi * zoom), H = Math.round(h_in * dpi * zoom);
  const ratio = low ? 1 : Math.max(1, window.devicePixelRatio || 1);
  try {
    const r = await (await post('/fig/png', {which: 'single', dpi: dpi * zoom * ratio, w: W, h: H})).json();
    if (r.png) {
      const pic = new Image();
      pic.src = 'data:image/png;base64,' + r.png;
      await Figs.decoded(pic);
      img.src = pic.src;
      img.style.width = W + 'px';
      img.style.height = H + 'px';
      img.hidden = false;
      P.geo = r.geo;
    } else if (r.problem) problem(r.problem);
  } catch (e) { problem('Newt is not answering.'); }
  P.busy = false;
  E('busydot').classList.remove('on');
  if (P.again) { P.again = false; render(P.againLow); }
}
let resizeT = 0;
window.addEventListener('resize', () => { clearTimeout(resizeT); resizeT = setTimeout(() => render(false), 150); });

// ---------------------------------------------------------------- moving the view: grab, zoom
const grabOn = () => E('grab').getAttribute('aria-pressed') === 'true' && P.st && P.st.spec && P.geo;
const at = (e) => { const r = img.getBoundingClientRect(); return {x: e.clientX - r.left, y: e.clientY - r.top}; };
const inPlot = (p) => { const g = P.geo; return g && p.x >= g.left && p.x <= g.left + g.w && p.y >= g.top && p.y <= g.top + g.h; };
function shifted([a, b], frac, log) {
  if (log && a > 0 && b > 0) { const la = Math.log10(a), lb = Math.log10(b), d = (lb - la) * frac; return [10 ** (la + d), 10 ** (lb + d)]; }
  const d = (b - a) * frac;
  return [a + d, b + d];
}
function scaledAbout([a, b], f, k, log) {
  if (log && a > 0 && b > 0) { const la = Math.log10(a), lb = Math.log10(b), c = la + (lb - la) * f; return [10 ** (c - (c - la) * k), 10 ** (c + (lb - c) * k)]; }
  const c = a + (b - a) * f;
  return [c - (c - a) * k, c + (b - c) * k];
}
function current() {                        // the view as last asked for (renders lag behind), else as drawn
  const g = P.geo;
  return P.view || {xlim: g.xlim, ylim: g.ylim};
}
function setView(xlim, ylim) {
  P.view = {xlim, ylim};
  clearTimeout(P.viewTimer);
  P.viewTimer = setTimeout(() => { if (!P.drag) P.view = null; }, 600);
  P.pending = {xlim: xlim.map((v) => v == null ? null : +(+v).toPrecision(6)), ylim: ylim.map((v) => v == null ? null : +(+v).toPrecision(6))};
  flush();
}
async function flush() {                    // one at a time, the newest view
  if (P.sending) return;
  P.sending = true;
  while (P.pending) {
    const v = P.pending;
    P.pending = null;
    await fcmd({cmd: 'set', values: v});
  }
  P.sending = false;
}
function zoomView(k, p) {
  const g = P.geo;
  if (!g) return;
  const cur = current(), s = P.st.spec;
  const fx = p ? Math.min(1, Math.max(0, (p.x - g.left) / g.w)) : 0.5;
  const fy = p ? Math.min(1, Math.max(0, 1 - (p.y - g.top) / g.h)) : 0.5;
  setView(g.x_moves ? scaledAbout(cur.xlim, fx, k, g.xlog) : (s.xlim || [null, null]), scaledAbout(cur.ylim, fy, k, g.ylog));
}
img.addEventListener('mousedown', (e) => {
  const p = at(e);
  if (e.button !== 0 || !grabOn() || !inPlot(p)) return;
  e.preventDefault();
  P.drag = {x: e.clientX, y: e.clientY, view: current(), geo: {...P.geo}};
  area.classList.add('grabbing');
});
window.addEventListener('mousemove', (e) => {
  const d = P.drag;
  if (d) {
    const g = d.geo, s = P.st.spec;
    const xlim = g.x_moves ? shifted(d.view.xlim, -(e.clientX - d.x) / g.w, g.xlog) : (s.xlim || [null, null]);
    setView(xlim, shifted(d.view.ylim, (e.clientY - d.y) / g.h, g.ylog));
    return;
  }
  if (e.target === img) area.classList.toggle('grab', !!grabOn() && inPlot(at(e)));
});
window.addEventListener('mouseup', () => {
  if (!P.drag) return;
  P.drag = null;
  area.classList.remove('grabbing');
  setTimeout(() => render(false), 0);       // sharp again once it stops
});
img.addEventListener('wheel', (e) => {
  const p = at(e);
  if (!grabOn() || !inPlot(p)) return;
  e.preventDefault();
  const dy = Math.max(-300, Math.min(300, e.deltaMode === 1 ? e.deltaY * 33 : e.deltaY));
  zoomView(0.85 ** (-dy / 100), p);
}, {passive: false});
E('grab').onclick = () => { const b = E('grab'); b.setAttribute('aria-pressed', b.getAttribute('aria-pressed') !== 'true'); };
E('zin').onclick = () => zoomView(0.8);
E('zout').onclick = () => zoomView(1.25);
E('zfit').onclick = () => { P.view = null; setView([null, null], [null, null]); };

// ---------------------------------------------------------------- out: the Canvas, a file, the clipboard
E('tocanvas').onclick = async () => {
  const st = await fcmd({cmd: 'to_canvas'});
  if (st && !st.problem) location.href = 'plotcanvas.html' + location.hash;
};
E('savebtn').onclick = () => { E('savemenu').hidden = !E('savemenu').hidden; };
E('savemenu').onclick = (e) => {
  const b = e.target.closest('button[data-fmt]');
  if (!b) return;
  E('savemenu').hidden = true;
  save({what: 'single', fmt: b.dataset.fmt, dpi: 300}, (t, bad) => problem(t, !bad));
};
E('copy').onclick = () => {
  const blob = post('/fig/export', {what: 'single', fmt: 'png', dpi: 200}).then((r) => r.blob());
  try {
    navigator.clipboard.write([new ClipboardItem({'image/png': blob})])
      .then(() => problem('Copied: paste it in your document', true))
      .catch(() => problem('Couldn’t copy it here: use Save figure ▾ instead'));
  } catch (e) { problem('Couldn’t copy it here: use Save figure ▾ instead'); }
};

load();
})();
