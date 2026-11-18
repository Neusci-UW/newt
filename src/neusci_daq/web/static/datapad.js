// The DataPad: a table of measurements, a row per trial as the Trials pane lists them, a
// column per measurement the student adds (nothing is added for them); a preview of a row's
// trial with the picked column's stretch shaded; and charts of one column against another,
// in a square box with axes fitted to the dots. web/pad.py works it all out (padmodel.py
// measures, as in the Qt app). Uses $, H, esc and cmd from analysis.js (the Trials pane).
'use strict';

(() => {
const D = {
  st: null, how: null,
  picked: -1,               // the column picked (its header lit): clicks on the preview set where it measures
  row: null,                // the row shown: {trial, label}
  sum: -1,                  // or a summary row, its trials averaged
  stim: '', resp: '',       // the preview's channels
  x: null,                  // the time shown [x0, x1], or null: all of it
  sig: '', seq: 0, key: '', head: null, plots: [], drag: null, timer: 0,
  charts: new Map(), editing: -1,
};
const isMac = /Mac/.test(navigator.platform);
const FONT = '12px "IBM Plex Mono", ui-monospace, Menlo, monospace';   // whole px (uPlot scales the digits)
const cssv = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const minus = (s) => String(s).replace(/(\d|\b[A-Z])-(?=\d|[A-Z]\b)/g, '$1–').replace(/-(?=[\d.])/g, '−');   // ranges get –, numbers −
const post = (url, d) => fetch(url, {method: 'POST', headers: H, body: JSON.stringify(d)});
const store = (k, v) => { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) { /* */ } return null; };

// names: what follows the first _ is a true subscript (I_B is I with B below)
const sub = (name) => {
  const s = String(name), at = s.indexOf('_');
  return at < 0 ? esc(s) : `${esc(s.slice(0, at))}<sub>${esc(s.slice(at + 1))}</sub>`;
};
const subSvg = (name) => {
  const s = String(name), at = s.indexOf('_');
  return at < 0 ? esc(s) : `${esc(s.slice(0, at))}<tspan baseline-shift="sub" font-size="78%">${esc(s.slice(at + 1))}</tspan>`;
};
const SUBS = {0: '₀', 1: '₁', 2: '₂', 3: '₃', 4: '₄', 5: '₅', 6: '₆', 7: '₇', 8: '₈', 9: '₉', '+': '₊', '-': '₋',
  a: 'ₐ', e: 'ₑ', h: 'ₕ', i: 'ᵢ', j: 'ⱼ', k: 'ₖ', l: 'ₗ', m: 'ₘ', n: 'ₙ', o: 'ₒ', p: 'ₚ', r: 'ᵣ', s: 'ₛ', t: 'ₜ', u: 'ᵤ', v: 'ᵥ', x: 'ₓ'};
const plain = (name) => {           // for a list (no markup there): Iₓ where Unicode has it, else the two run together
  const s = String(name), at = s.indexOf('_');
  if (at < 0) return s;
  const rest = s.slice(at + 1), small = [...rest].map((c) => SUBS[c]);
  return s.slice(0, at) + (small.every(Boolean) ? small.join('') : rest);
};
function alpha(color, a) {
  const c = color.trim();
  if (/^#[0-9a-f]{6}$/i.test(c)) return `rgba(${parseInt(c.slice(1, 3), 16)},${parseInt(c.slice(3, 5), 16)},${parseInt(c.slice(5, 7), 16)},${a})`;
  return c;
}

// ---------------------------------------------------------------- numbers on axes (as charts.js)
function si(big, unit) {            // a prefix for small numbers: 0.02 V reads as 20 mV
  if (!unit || /[²³·/]/.test(unit) || unit === 's' || !(big > 0)) return [1, unit];
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

// ---------------------------------------------------------------- talking to Python
async function load() {
  try { show(await (await fetch('/pad/state', {headers: H})).json()); }
  catch (e) { problem('Newt is not answering.'); }
}
async function pcmd(d) {
  let st;
  try { st = await (await post('/pad/cmd', d)).json(); }
  catch (e) { problem('Newt is not answering.'); return null; }
  if (st.picked !== undefined) D.picked = st.picked;
  show(st);
  problem(st.problem || '');
  return st;
}
function problem(text) { const p = $('pproblem'); p.textContent = text || ''; p.hidden = !text; }

// the Trials pane changed (analysis.js show()): its groups, ticks or colors are the table's rows
window.boardTrials = (st) => {
  const sig = JSON.stringify([st.folder, st.how, st.looks, st.groups.map((g) => [g.name, g.color, g.hidden,
    g.trials.map((t) => t.off ? -1 - t.i : t.i)])]);
  if (sig === D.sig && D.st) return;
  D.sig = sig;
  load();
};

// ---------------------------------------------------------------- showing the state
function show(st) {
  const old = D.st;
  D.st = st;
  if (!old || old.folder !== st.folder) { D.picked = -1; D.row = null; D.sum = -1; D.x = null; D.drag = null; D.editing = -1; }
  if (D.picked >= st.columns.length) D.picked = st.columns.length - 1;
  if (D.sum >= st.summary.length) D.sum = -1;
  fillChannels();
  fillBars();
  renderTable();
  renderCharts();
  renderEditor();
  preview();
}

function fillChannels() {
  const st = D.st, names = st.channels.map((c) => c.name);
  if (st.how !== D.how) {                                   // the stimulus channel picked in the Trials pane: on top
    D.how = st.how;
    if (names.includes(st.how)) { D.stim = st.how; if (D.resp === D.stim) D.resp = ''; }
  }
  if (!names.includes(D.stim)) D.stim = names.includes(st.how) ? st.how : names[0] || '';
  if (!names.includes(D.resp)) D.resp = names.find((n) => n !== D.stim) || names[0] || '';
  const opts = (cur) => names.map((n) => `<option${n === cur ? ' selected' : ''}>${esc(n)}</option>`).join('');
  $('pstim').innerHTML = opts(D.stim);
  $('presp').innerHTML = opts(D.resp);
}

const picked = () => D.st && D.picked >= 0 ? D.st.columns[D.picked] : null;
const sets = (c) => c && ['measure', 'level', 'duration'].includes(c.kind);

function fillBars() {
  const st = D.st, c = picked();
  $('fxname').innerHTML = c ? sub(c.shown) : '';
  const fx = $('fx');
  if (document.activeElement !== fx) fx.value = c ? c.recipe : '';
  fx.readOnly = !c || c.kind === 'python';
  const err = c && c.error;
  $('fxerr').textContent = err || '';
  $('fxerr').hidden = !err;
  $('rowsmode').innerHTML = Object.entries(st.rows_modes).map(([k, t]) => `<option value="${k}"${k === st.rows_mode ? ' selected' : ''}>${esc(t)}</option>`).join('');
  const seg = $('rowsseg');
  seg.hidden = !['segment', 'pulse'].includes(st.rows_mode);
  seg.innerHTML = `<option value="">${st.rows_mode === 'pulse' ? 'every train' : 'every segment'}</option>` +
    st.letters.map((L) => `<option${L === st.rows_seg ? ' selected' : ''}>${esc(L)}</option>`).join('');
  const by = [['', 'nothing'], ['all', 'all rows'], ['group', 'each group'],
              ...st.columns.map((x) => [x.name, 'each ' + plain(x.shown)])];
  $('by').innerHTML = by.map(([k, t]) => `<option value="${esc(k)}"${k === (st.by || '') ? ' selected' : ''}>${esc(t)}</option>`).join('');
  $('plot').disabled = $('addcol').disabled = !st.folder;
  const pk = $('picking');
  pk.hidden = !sets(c);
  if (sets(c)) pk.innerHTML = `Picked: <b>${sub(c.shown)}</b> · the preview sets where it measures ×`;
  pk.title = 'Clicks and drags on the preview change where this column measures, in every trial. Esc, or click its name again, to let go';
  $('pbase').textContent = st.baseline.on ? `Baseline: ${minus(st.baseline.label)} ▾` : 'Baseline: off ▾';
  $('pbase').classList.toggle('primary', st.baseline.on);
}

// ---------------------------------------------------------------- the table
const showOff = () => $('showoff').checked;
function curIndex() {
  const rows = D.st.rows;
  if (D.row) {
    let k = rows.findIndex((r) => r.trial === D.row.trial && r.label === D.row.label);
    if (k < 0) k = rows.findIndex((r) => r.trial === D.row.trial);
    if (k >= 0 && (showOff() || !rows[k].off)) return k;
  }
  const k = rows.findIndex((r) => !r.off);
  return k >= 0 ? k : rows.length ? 0 : -1;
}
function renderTable() {
  const st = D.st, tab = $('tab');
  if (!st.folder) { tab.innerHTML = '<tbody><tr><td class="tempty"><b>No session open</b>Pick one in the Trials pane.</td></tr></tbody>'; return; }
  const cols = st.columns, withRow = st.rows_mode !== 'trial', cur = D.sum < 0 ? curIndex() : -1;
  let h = '<thead><tr><th></th><th>Trial</th>' + (withRow ? '<th>Row</th>' : '') + '<th>Group</th>';
  cols.forEach((c, k) => {
    h += `<th class="col${k === D.picked ? ' pk' : ''}" data-k="${k}" title="${esc(c.error ? '⚠ ' + c.error : c.recipe)}">${c.error ? '⚠ ' : ''}${sub(c.shown)}` +
         `${c.unit ? `<span class="cu">${esc(c.unit)}</span>` : ''}<button class="ed" data-ed="${k}" title="Change this column">▾</button></th>`;
  });
  h += '<th class="plus"><button data-plus title="Add a column">+</button></th></tr></thead><tbody>';
  if (!cols.length) {
    h += `<tr><td colspan="${4 + withRow}" class="tempty"><b>No columns yet</b>Add one with <b>+ Column</b>, or click the preview and pick one of the numbers under it.<br>Each column measures something in every trial: you choose what, and where.</td></tr>`;
  }
  const off = showOff();
  st.rows.forEach((r, k) => {
    if (!cols.length && k > 60) return;                    // (just the trials, until there are columns)
    if (r.off && !off) return;
    h += `<tr data-r="${k}" class="${r.off ? 'off' : ''}${k === cur ? ' cur' : ''}"><td class="tk">` +
         (r.first ? `<input type="checkbox" data-t="${r.trial}"${r.off ? '' : ' checked'} title="Untick to leave this trial out of everything">` : '') +
         `</td><td class="n" title="${esc(r.stem)}">${r.trial + 1}</td>` + (withRow ? `<td>${esc(r.label)}</td>` : '') +
         `<td class="g" title="${esc(r.group ? `${r.group}, trial ${r.n} of the group` : '')}">${r.color ? `<span class="dot" style="background:${r.color}"></span>` : ''}${esc(r.group)}</td>`;
    cols.forEach((c, j) => { h += `<td class="v${j === D.picked ? ' pk' : ''}${c.error ? ' err' : ''}" data-k="${j}">${esc(minus(st.cells[j][k]))}</td>`; });
    h += '<td></td></tr>';
  });
  st.summary.forEach((s, j) => {
    h += `<tr class="sum${j === 0 ? ' first' : ''}${j === D.sum ? ' cur' : ''}" data-s="${j}" title="${esc(s.title)}: click to see the average">` +
         `<td></td><td colspan="${2 + withRow}">${s.color ? `<span class="dot" style="background:${s.color}"></span> ` : ''}${esc(s.label)}</td>`;
    cols.forEach((c, k) => { h += `<td class="v${k === D.picked ? ' pk' : ''}" title="mean ± SEM, n = ${s.n[k]}">${esc(minus(s.cells[k]))}</td>`; });
    h += '<td></td></tr>';
  });
  tab.innerHTML = h + '</tbody>';
}
function markRow(scroll) {
  const cur = D.sum < 0 ? curIndex() : -1;
  for (const tr of $('tab').querySelectorAll('tr.cur')) tr.classList.remove('cur');
  const tr = $('tab').querySelector(D.sum >= 0 ? `tr[data-s="${D.sum}"]` : `tr[data-r="${cur}"]`);
  if (tr) { tr.classList.add('cur'); if (scroll) tr.scrollIntoView({block: 'nearest'}); }
  for (const card of D.charts.values()) markDots(card);
}
function setRow(k, scroll) {
  const r = D.st.rows[k];
  if (!r) return;
  D.row = {trial: r.trial, label: r.label};
  D.sum = -1;
  if (D.st.rows_mode === 'manual' || D.drag) D.drag = null;
  markRow(scroll);
  preview();
}
function pick(k, toggle) {
  D.picked = toggle && k === D.picked ? -1 : k;
  renderTable();
  fillBars();
  preview();
}
function curTrial() { const k = curIndex(); return k >= 0 ? D.st.rows[k].trial : -1; }

$('tab').addEventListener('click', (e) => {
  if (e.target.matches('input[data-t]')) return;
  const ed = e.target.closest('[data-ed]');
  if (ed) { e.stopPropagation(); return openEditor(+ed.dataset.ed); }
  if (e.target.closest('[data-plus]')) { e.stopPropagation(); return openMenu($('colmenu'), $('addcol')); }
  const th = e.target.closest('th.col');
  if (th) return pick(+th.dataset.k, true);
  const s = e.target.closest('tr[data-s]');
  if (s) { D.sum = +s.dataset.s; markRow(); return preview(); }
  const tr = e.target.closest('tr[data-r]');
  if (!tr) return;
  const td = e.target.closest('td[data-k]');
  if (td && +td.dataset.k !== D.picked) { D.picked = +td.dataset.k; setRow(+tr.dataset.r); renderTable(); fillBars(); return; }
  setRow(+tr.dataset.r);
});
$('tab').addEventListener('change', (e) => {     // a trial's tick is the Trials pane's: that saves it, and both follow
  const t = e.target.closest('input[data-t]');
  if (t) cmd({cmd: 'tick', kind: 'block', i: +t.dataset.t, on: t.checked});
});
$('showoff').checked = store('newt.pad.showoff') !== '0';
$('showoff').onchange = () => { store('newt.pad.showoff', $('showoff').checked ? '1' : '0'); renderTable(); preview(); };

// ---------------------------------------------------------------- the bar over the table
$('fx').addEventListener('keydown', (e) => {
  const c = picked();
  if (e.key === 'Escape') { e.target.value = c ? c.recipe : ''; e.target.blur(); return; }
  if (e.key !== 'Enter' || !c) return;
  if (c.kind === 'python') return openEditor(D.picked);
  e.target.blur();
  pcmd({cmd: 'fx', k: D.picked, text: e.target.value});
});
$('fx').addEventListener('blur', () => { const c = picked(); if (c && !D.st.problem) $('fx').value = c.recipe; });
$('rowsmode').onchange = () => pcmd({cmd: 'rows', mode: $('rowsmode').value, seg: ''});
$('rowsseg').onchange = () => pcmd({cmd: 'rows', mode: D.st.rows_mode, seg: $('rowsseg').value});
$('by').onchange = () => { D.sum = -1; pcmd({cmd: 'by', by: $('by').value || null}); };
$('pstim').onchange = () => { D.stim = $('pstim').value; preview(); };
$('presp').onchange = () => { D.resp = $('presp').value; preview(); };
$('pwhole').onclick = () => { D.x = null; preview(true); };
$('plot').onclick = async () => {
  await pcmd({cmd: 'chart_add'});
  const last = [...D.charts.values()].pop();
  if (last) last.el.scrollIntoView({behavior: 'smooth', block: 'nearest'});
};
$('picking').onclick = () => pick(-1);

// menus: + Column, Baseline, More
function openMenu(m, anchor) {
  const was = !m.hidden;
  closeMenus();
  if (was) return;
  if (m.id === 'colmenu') fillColMenu();
  if (m.id === 'basemenu') fillBaseMenu();
  m.hidden = false;
  const r = anchor.getBoundingClientRect(), box = m.offsetParent.getBoundingClientRect();
  const left = Math.min(r.left, window.innerWidth - m.offsetWidth - 12) - box.left;
  m.style.left = left + 'px';
  m.style.top = (r.bottom - box.top + 6) + 'px';
}
function closeMenus() { for (const id of ['colmenu', 'basemenu', 'moremenu']) $(id).hidden = true; }
document.addEventListener('click', (e) => {
  if (!e.target.closest('.menu') && !e.target.closest('#addcol,#pbase,#more,[data-plus]')) closeMenus();
  if (!e.target.closest('#editor') && !e.target.closest('[data-ed]') && !e.target.closest('.menu') && D.editing >= 0 &&
      !e.target.closest('#pplots,#pstrip')) { D.editing = -1; renderEditor(); }
});
$('addcol').onclick = () => openMenu($('colmenu'), $('addcol'));
$('pbase').onclick = () => openMenu($('basemenu'), $('pbase'));
$('more').onclick = () => openMenu($('moremenu'), $('more'));

function fillColMenu() {
  $('colmenu').innerHTML = D.st.menu.map(([g, items]) => `<div class="mh">${esc(g)}</div>` + items.map(([kind, key, label, help]) =>
    `<button data-kind="${kind}" data-key="${key}" title="${esc(help)}">${esc(label)}</button>`).join('')).join('');
}
$('colmenu').onclick = async (e) => {
  const b = e.target.closest('[data-kind]');
  if (!b) return;
  closeMenus();
  const st = await pcmd({cmd: 'add', kind: b.dataset.kind, key: b.dataset.key, channel: D.resp, trial: curTrial()});
  if (st && st.picked !== undefined) openEditor(st.picked);
};

function selKey(s) { return s ? JSON.stringify([s.first || '', s.last || '', s.mode || 'all', s.first ? 0 : +s.t0 || 0, s.first ? 0 : +s.t1 || 0]) : ''; }
function fillBaseMenu() {
  const st = D.st, head = D.head, items = [];
  for (const [L, t0, t1, kind] of (head && head.segs) || []) {
    if (/^\d/.test(L)) continue;                              // one baseline per trial: the first stimulus's segments
    items.push([`Segment ${L} · ${kind ? kind[0].toUpperCase() + kind.slice(1) + ' ' : ''}${+((t1 - t0) * 1000).toPrecision(4)} ms`,
                {first: L, last: L, mode: 'all'}]);
  }
  const q = st.quick;
  if (q && !q.first && q.mode !== 'point' && q.t1 > q.t0) items.push([`The stretch selected (${st.quick_label})`, q]);
  items.push(['The first 50 ms', {first: '', last: '', mode: 'all', t0: 0, t1: 0.05}]);
  const cur = st.baseline.on ? selKey(st.baseline.sel) : '';
  if (cur && !items.some(([, s]) => selKey(s) === cur)) items.push([`${st.baseline.label} (set before)`, st.baseline.sel]);
  D.baseItems = items;
  $('basemenu').innerHTML = '<div class="mh">Subtract each trial’s mean over</div>' +
    items.map(([t, s], k) => `<button data-b="${k}" class="${selKey(s) === cur ? 'on' : ''}">${esc(minus(t))}</button>`).join('') +
    `<hr><button data-b="off" class="${st.baseline.on ? '' : 'on'}">Off: no baseline</button>` +
    (head && head.segs.length ? '' : '<div class="mh" style="text-transform:none;letter-spacing:0;font-weight:400">Drag across the preview to pick a stretch of your own</div>');
}
$('basemenu').onclick = (e) => {
  const b = e.target.closest('[data-b]');
  if (!b) return;
  closeMenus();
  if (b.dataset.b === 'off') pcmd({cmd: 'baseline', on: false});
  else pcmd({cmd: 'baseline', on: true, sel: D.baseItems[+b.dataset.b][1]});
};
let clearTimer = 0;
$('moremenu').onclick = (e) => {
  const b = e.target.closest('[data-m]');
  if (!b) return;
  const m = b.dataset.m;
  if (m === 'clear' && !b.classList.contains('on')) {           // asks once, on the button itself
    b.classList.add('on');
    b.textContent = 'Click again: every column goes';
    clearTimer = setTimeout(() => { b.classList.remove('on'); b.textContent = 'Take every column out…'; }, 4000);
    return;
  }
  clearTimeout(clearTimer);
  closeMenus();
  if (m === 'clear') { b.classList.remove('on'); b.textContent = 'Take every column out…'; D.picked = -1; pcmd({cmd: 'clear'}); }
  else if (m === 'clear_rows') pcmd({cmd: 'clear_rows'});
  else if (m === 'copy') copyTable();
};
function copyTable() {
  const st = D.st, withRow = st.rows_mode !== 'trial';
  const lines = [['Trial', ...(withRow ? ['Row'] : []), 'Group', ...st.columns.map((c) => plain(c.shown) + (c.unit ? ` (${c.unit})` : ''))]];
  st.rows.forEach((r, k) => { if (!r.off) lines.push([r.trial + 1, ...(withRow ? [r.label] : []), r.group, ...st.columns.map((c, j) => st.cells[j][k])]); });
  const text = lines.map((l) => l.join('\t')).join('\n');
  const done = () => problem('');
  if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, () => fallback(text));
  else fallback(text);
}
function fallback(text) {
  const t = document.createElement('textarea');
  t.value = text;
  document.body.append(t);
  t.select();
  try { document.execCommand('copy'); } catch (e) { /* */ }
  t.remove();
}

// ---------------------------------------------------------------- the preview
function plotWidth() { return Math.max(300, $('pplots').clientWidth); }
function previewReq() {
  const st = D.st;
  if (!st || !st.folder || !D.stim || !D.resp) return null;
  const base = {stim: D.stim, resp: D.resp, cols: Math.round(Math.max(100, plotWidth() - 90)), ...(D.x ? {x0: D.x[0], x1: D.x[1]} : {})};
  if (D.sum >= 0 && st.summary[D.sum]) return {...base, avg: st.summary[D.sum].trials, title: st.summary[D.sum].title};
  const k = curIndex();
  if (k < 0) return null;
  return {...base, trial: st.rows[k].trial, row: k, col: D.picked >= 0 ? D.picked : null};
}
async function preview(force) {
  const req = previewReq();
  if (!req) { D.head = null; D.key = ''; return note(D.st && D.st.folder ? 'No trials' : 'Open a session in the Trials pane'); }
  const key = JSON.stringify([D.st.version, D.st.quick_label, plotWidth(), req]);
  if (!force && key === D.key) return drawExtras();
  D.key = key;
  const seq = ++D.seq;
  let r;
  try { r = await post('/pad/preview', req); } catch (e) { return; }
  if (seq !== D.seq) return;
  if (!r.ok) { try { problem((await r.json()).problem || 'Couldn’t show this trial'); } catch (e) { /* */ } return; }
  const buf = await r.arrayBuffer();
  if (seq !== D.seq) return;
  drawPreview(parse(buf));
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
function note(text) {
  D.plots.forEach((p) => p.u.destroy());
  D.plots = [];
  D.shape = '';
  $('pplots').innerHTML = `<div class="tempty">${esc(text)}</div>`;
  $('pstrip').innerHTML = '';
  $('pquick').innerHTML = '';
  $('pinfo').textContent = '';
}
const padR = (r) => {
  if (!r) return [-1, 1];
  let [lo, hi] = r;
  if (hi - lo < 1e-12) { const m = Math.abs(lo) * 0.1 || 1; lo -= m; hi += m; }
  const p = (hi - lo) * 0.08;
  return [lo - p, hi + p];
};
function drawPreview(d) {
  const head = d.head;
  if (head.note) { D.head = null; return note(head.note); }
  D.head = head;
  const width = plotWidth();
  const shape = JSON.stringify([width, head.plots.map((p) => [p.label, p.unit, p.series, p.stim])]);
  if (shape !== D.shape) build(head, width);
  D.shape = shape;
  const xr = D.x || [0, head.end];
  D.plots.forEach((pv, k) => {
    const p = head.plots[k], yr = padR(p.yr);
    pv.u.batch(() => {
      pv.u.setData([d.x, ...d.plots[k]], false);
      pv.u.setScale('x', {min: xr[0], max: xr[1]});
      pv.u.setScale('y', {min: yr[0], max: yr[1]});
    });
  });
  $('pinfo').textContent = head.label;
  drawExtras();
}
function build(head, width) {
  D.plots.forEach((p) => p.u.destroy());
  D.plots = [];
  const box = $('pplots');
  box.replaceChildren();
  const muted = cssv('--muted'), line = cssv('--line'), accent = cssv('--accent');
  const grid = {stroke: line, width: 1}, ticks = {stroke: line, width: 1, size: 4};
  head.plots.forEach((p, k) => {
    const last = k === head.plots.length - 1;
    const h = p.stim && head.plots.length > 1 ? 84 : 200;
    const wrap = document.createElement('div');
    wrap.innerHTML = `<div class="plab"><b>${esc(p.label)}</b>${p.stim ? '<span>the stimulus</span>' : ''}</div>`;
    box.append(wrap);
    const u = new uPlot({
      width, height: h + (last ? 30 : 0), padding: [4, 14, 0, 0], legend: {show: false},
      cursor: {sync: {key: 'pad'}, y: false, points: {show: false}, drag: {x: false, y: false, setScale: false}},
      scales: {x: {time: false, auto: false}, y: {auto: false}},
      axes: [
        {stroke: muted, grid, ticks, font: FONT, size: last ? 30 : 4, space: 70,
         values: last ? (u, v) => tickText(v, '').map((t) => t + ' s') : (u, v) => v.map(() => '')},
        {stroke: muted, grid, ticks, font: FONT, size: 78, space: 28, values: (u, v) => tickText(v, p.unit)},
      ],
      series: [{}, ...p.series.map((kind) => ({stroke: kind === 'bold' ? accent : alpha(accent, 0.18),
                                               width: kind === 'bold' ? 1.6 : 1, points: {show: false}, spanGaps: false}))],
      hooks: {draw: [shade]},
    }, [[], ...p.series.map(() => [])], wrap);
    pointer(u);
    D.plots.push({u, p, wrap});
  });
  const v = document.createElement('div');
  v.className = 'pvalue';
  v.id = 'pvalue';
  box.append(v);
}

// shading drawn on both plots: the baseline (grey), the column's stretch (accent), a drag (orange)
function shade(u) {
  const head = D.head;
  if (!head) return;
  const ctx = u.ctx, {left, top, width, height} = u.bbox, X = (t) => u.valToPos(t, 'x', true);
  const span = (a, b, fill, edge, dash) => {
    const x0 = X(a), x1 = X(b);
    if (fill && x1 - x0 > 0.5) { ctx.fillStyle = fill; ctx.fillRect(x0, top, x1 - x0, height); }
    if (edge) {
      ctx.strokeStyle = edge;
      ctx.lineWidth = devicePixelRatio * (a === b ? 2 : 1);
      ctx.setLineDash(dash || []);
      ctx.beginPath();
      for (const x of a === b ? [x0] : [x0, x1]) { ctx.moveTo(Math.round(x) + 0.5, top); ctx.lineTo(Math.round(x) + 0.5, top + height); }
      ctx.stroke();
    }
  };
  ctx.save();
  ctx.beginPath();
  ctx.rect(left, top, width, height);
  ctx.clip();
  const line = cssv('--line'), accent = cssv('--accent'), muted = cssv('--muted'), dim = cssv('--dim');
  ctx.strokeStyle = line;
  ctx.lineWidth = devicePixelRatio;
  ctx.setLineDash([4 * devicePixelRatio, 4 * devicePixelRatio]);
  ctx.beginPath();
  for (const s of head.segs.slice(1)) { const x = Math.round(X(s[1])) + 0.5; ctx.moveTo(x, top); ctx.lineTo(x, top + height); }
  ctx.stroke();
  if (head.base) span(head.base[0], head.base[1], alpha(muted, 0.14), alpha(muted, 0.6), [2 * devicePixelRatio, 3 * devicePixelRatio]);
  if (head.win) span(head.win[0], head.win[1], alpha(accent, 0.14), alpha(accent, 0.8));
  if (D.drag) span(D.drag[0], D.drag[1], alpha(dim, 0.22), alpha(dim, 0.9));
  ctx.restore();
}
function redraw() { D.plots.forEach((p) => p.u.redraw(false, false)); drawExtras(); }

// the letters under the plots, the value over the stretch, the quick values, the buttons
function drawExtras() {
  const head = D.head;
  if (!head || !D.plots.length) return;
  const last = D.plots[D.plots.length - 1], u = last.u;
  const strip = $('pstrip'), sr = strip.getBoundingClientRect(), or = u.over.getBoundingClientRect();
  const off = or.left - sr.left, lo = u.scales.x.min, hi = u.scales.x.max;
  if (!head.segs.length) {
    strip.innerHTML = head.avg ? '' : '<div class="none">No segments in this trial (none saved, none found on the stimulus channel): click the trace or drag across a stretch.</div>';
  } else {
    strip.innerHTML = head.segs.map(([L, t0, t1, kind]) => {
      const a = Math.max(t0, lo), b = Math.min(t1, hi);
      if (!(b > a)) return '';
      const x = off + u.valToPos((a + b) / 2, 'x');
      return `<button data-l="${esc(L)}" class="${head.lit.includes(L) ? 'lit' : ''}" style="left:${x.toFixed(1)}px"
        title="Segment ${esc(L)}: ${esc(kind)}, ${+((t1 - t0) * 1000).toPrecision(4)} ms. Click to measure all of it; Shift-click for a run">${esc(L)}</button>`;
    }).join('');
  }
  const v = $('pvalue');
  if (v) {
    v.hidden = !(head.value && head.win);
    if (!v.hidden) {
      const a = Math.max(head.win[0], lo), b = Math.min(head.win[1], hi), box = $('pplots').getBoundingClientRect();
      const mid = b >= a ? (a + b) / 2 : (lo + hi) / 2;
      v.innerHTML = `${sub(head.vname)} = ${esc(minus(head.value))}`;
      v.style.left = (or.left - box.left + u.valToPos(mid, 'x')) + 'px';
      v.style.top = (or.top - box.top + 2) + 'px';
    }
  }
  const q = head.quick, box = $('pquick');
  if (head.avg) box.innerHTML = '<span class="where">The average of the summary row’s trials, faint behind it. Click a row of the table to measure one trial.</span>';
  else if (!q) box.innerHTML = '<span class="where">What you click or drag on shows its numbers here, on every channel: click one to add it as a column.</span>';
  else if (q.missing) box.innerHTML = `<span class="where"><b>${esc(minus(q.where))}</b> isn’t in this trial.</span>`;
  else {
    box.innerHTML = `<span class="where">In <b>${esc(minus(q.where))}</b> of this trial: click a number to add it as a column, measured there in every trial</span>` +
      q.lines.map(([ch, vals]) => `<div class="qline"><span class="ch">${esc(ch)}</span>${vals.map(([key, short, text, long]) =>
        `<button class="qv" data-key="${key}" data-ch="${esc(ch)}" title="Add a column: ${esc(long.toLowerCase())} of ${esc(ch)} over ${esc(q.where)}, in every trial"><span>${esc(short)}</span>${esc(minus(text))}</button>`).join('')}</div>`).join('');
  }
  $('paddrow').hidden = !(D.st.rows_mode === 'manual' && D.drag && !head.avg);
}
$('pstrip').onclick = (e) => {
  const b = e.target.closest('[data-l]');
  if (!b || !D.head || D.head.avg) return;
  D.drag = null;
  pcmd({cmd: 'segment', k: D.picked >= 0 ? D.picked : null, letter: b.dataset.l, extend: e.shiftKey, trial: curTrial()});
};
$('pquick').onclick = async (e) => {
  const b = e.target.closest('[data-key]');
  if (!b) return;
  await pcmd({cmd: 'quick_add', key: b.dataset.key, channel: b.dataset.ch});
  renderTable();
};
$('paddrow').onclick = () => {
  if (!D.drag) return;
  const [t0, t1] = D.drag;
  D.drag = null;
  pcmd({cmd: 'add_row', trial: curTrial(), t0, t1});
};

// the mouse on the plots: a click is a moment, a drag a stretch; the wheel zooms in time
function pointer(u) {
  const over = u.over;
  const tAt = (e) => Math.max(0, u.posToVal(e.clientX - over.getBoundingClientRect().left, 'x'));
  let down = null;
  over.addEventListener('pointerdown', (e) => {
    if (e.button !== 0 || !D.head || D.head.avg) return;
    down = {x: e.clientX, t: tAt(e), moved: false};
    over.setPointerCapture(e.pointerId);
  });
  over.addEventListener('pointermove', (e) => {
    if (!down) return;
    if (Math.abs(e.clientX - down.x) > 4) down.moved = true;
    if (down.moved) { D.drag = [down.t, tAt(e)].sort((a, b) => a - b); redraw(); }
  });
  over.addEventListener('pointerup', () => {
    if (!down) return;
    const d = down;
    down = null;
    const k = D.picked >= 0 ? D.picked : null, trial = curTrial();
    if (!d.moved) { D.drag = null; return pcmd({cmd: 'point', k, t: d.t, trial}); }
    const [t0, t1] = D.drag;
    if (D.st.rows_mode === 'manual') { redraw(); return pcmd({cmd: 'stretch', k: null, t0, t1, trial}); }   // waits for Add as a row
    D.drag = null;
    pcmd({cmd: 'stretch', k, t0, t1, trial});
  });
  over.addEventListener('pointercancel', () => { down = null; });
  over.addEventListener('wheel', (e) => {
    if (!D.head) return;
    e.preventDefault();
    const sx = u.scales.x, w = sx.max - sx.min;
    if (e.shiftKey || Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
      const d = (Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY) / over.clientWidth * w;
      return setX(sx.min + d, sx.max + d);
    }
    const m = u.posToVal(e.clientX - over.getBoundingClientRect().left, 'x'), f = Math.exp(e.deltaY * (e.ctrlKey ? 0.01 : 0.002));
    setX(m - (m - sx.min) * f, m + (sx.max - m) * f);
  }, {passive: false});
}
function setX(x0, x1) {
  const end = D.head ? D.head.end : 1;
  let w = x1 - x0;
  const least = Math.max(end / 20000, 20 / ((D.head && D.head.fs) || 20000));
  if (w < least) { const m = (x0 + x1) / 2; x0 = m - least / 2; x1 = m + least / 2; w = least; }
  if (w >= end) D.x = null;
  else {
    if (x0 < 0) { x1 -= x0; x0 = 0; }
    if (x1 > end) { x0 -= x1 - end; x1 = end; }
    D.x = [x0, x1];
  }
  const r = D.x || [0, end];
  D.plots.forEach((p) => p.u.setScale('x', {min: r[0], max: r[1]}));
  drawExtras();
  clearTimeout(D.timer);
  D.timer = setTimeout(() => preview(true), 110);          // then the detail, from Python
}

// ---------------------------------------------------------------- the column editor
function openEditor(k) { D.editing = k; closeMenus(); renderEditor(true); }
function renderEditor(place) {
  const ed = $('editor'), st = D.st;
  if (!st || D.editing < 0 || D.editing >= st.columns.length) { ed.hidden = true; D.editing = -1; return; }
  const k = D.editing, c = st.columns[k];
  const focus = ed.contains(document.activeElement) ? document.activeElement.dataset.f : null;
  const opt = (v, t, cur) => `<option value="${esc(v)}"${String(v) === String(cur) ? ' selected' : ''}>${esc(t)}</option>`;
  const what = c.kind === 'measure' || c.kind === 'info' ? `${c.kind}:${c.key}` : `${c.kind}:`;
  let h = `<div class="ehead"><b>Column ${k + 1}</b><button data-a="left" title="Move left"${k ? '' : ' disabled'}>◀</button>` +
    `<button data-a="right" title="Move right"${k < st.columns.length - 1 ? '' : ' disabled'}>▶</button>` +
    `<button data-a="remove" title="Take this column out">Remove</button><button data-a="close" title="Close (Esc)">×</button></div>`;
  h += `<label class="ef"><span>Name</span><input type="text" data-f="title" value="${esc(c.title_auto ? '' : c.title)}" placeholder="${esc(c.shown)} (automatic)" title="The name shown on the table and charts; _ makes a subscript (V_rest)"></label>`;
  h += `<label class="ef"><span>In formulas</span><input type="text" class="mono" data-f="name" value="${esc(c.name)}" title="What formulas call it: letters, digits and _"></label>`;
  h += `<label class="ef"><span>What</span><select data-f="what">` + st.menu.map(([g, items]) => `<optgroup label="${esc(g)}">` +
    items.map(([kind, key, label]) => opt(`${kind}:${key}`, label, what)).join('') + '</optgroup>').join('') + '</select></label>';
  const segs = ((D.head && D.head.segs) || []).map((s) => s[0]).filter((L) => !/^\d/.test(L));
  if (c.kind === 'measure') {
    h += `<label class="ef"><span>Of</span><select data-f="channel">${st.channels.map((ch) => opt(ch.name, ch.name, c.channel)).join('')}</select></label>`;
    h += `<label class="ef"><span>Over</span><select data-f="over" title="Measure the stretch the row stands for, or a window of its own in the row's trial">${opt('window', 'Its own window', c.over)}${opt('row', 'The row (the whole trial, segment, pulse…)', c.over)}</select></label>`;
  }
  if (c.kind === 'level' || c.kind === 'duration' || (c.kind === 'measure' && c.over === 'window')) {
    const s = c.sel, on = new Set();
    if (s.first) { const a = segs.indexOf(s.first), b = segs.indexOf(s.last); if (a >= 0 && b >= 0) segs.slice(Math.min(a, b), Math.max(a, b) + 1).forEach((L) => on.add(L)); }
    h += `<div class="ef"><span>Where</span><div><b>${esc(minus(c.where))}</b>${s.first ? '' : s.mode === 'point' ? ' (a moment)' : ' (a stretch of time)'}</div></div>`;
    if (segs.length) {
      h += `<div class="ef"><span>Segments</span><div class="segs">${segs.map((L) =>
        `<button data-seg="${esc(L)}" aria-pressed="${on.has(L)}" title="Segment ${esc(L)}${c.kind === 'level' ? '' : '; Shift-click for a run'}">${esc(L)}</button>`).join('')}</div></div>`;
    }
    if (c.kind !== 'level' && s.first && s.mode !== 'phase') {
      h += `<div class="ef"><span>Part</span><div class="part"><select data-f="mode">${opt('all', 'Whole', s.mode)}${opt('first', 'First', s.mode)}${opt('last', 'Last', s.mode)}${opt('range', 'Start +', s.mode)}</select>` +
        (s.mode !== 'all' ? `<input type="number" data-f="a_ms" min="0" step="any" value="${s.a_ms}"> ms` : '') +
        (s.mode === 'range' ? ` to +<input type="number" data-f="b_ms" min="0" step="any" value="${s.b_ms}"> ms` : '') + '</div></div>';
    }
    h += `<p class="enote">${c.kind === 'level' ? 'Click a segment above, or its letter on the preview.' : 'Or, with this column picked, click a segment’s letter on the preview, click the trace (a moment) or drag across it (a stretch).'}</p>`;
  } else if (c.kind === 'formula') {
    h += `<label class="ef"><span>=</span><input type="text" class="mono" data-f="expr" value="${esc(c.expr)}" placeholder="dV / I_B * 1000"></label>`;
    h += `<div class="ef"><span></span><div class="ins">${st.columns.slice(0, k).map((x) => `<button data-ins="${esc(x.name)}" title="Put ${esc(x.name)} in">${sub(x.name)}</button>`).join('')}` +
      `<select data-fn><option value="">Functions…</option>${Object.entries(st.funcs).map(([f, t]) => opt(f, t, '')).join('')}</select></div></div>`;
  } else if (c.kind === 'python') {
    h += `<label class="ef"><span>Runs</span><select data-f="scope">${opt('row', 'For each row', c.scope)}${opt('column', 'Once, the whole column (col.<name>)', c.scope)}</select></label>`;
    h += `<label class="ef" style="grid-template-columns:1fr"><textarea data-f="expr" spellcheck="false" placeholder="# runs for each row: row.&lt;name&gt; is a column's value in it;&#10;# trace(channel) is the row's stretch, trace(channel, seg(&quot;B&quot;).last(50)) a window of its own;&#10;v = trace(&quot;Channel 1&quot;)&#10;return v.max() - v.min()">${esc(c.expr)}</textarea></label>`;
  }
  h += `<label class="ef"><span>Unit</span><input type="text" data-f="unit" value="${esc(c.own_unit)}" placeholder="${esc(c.unit ? c.unit + ' (automatic)' : 'automatic')}"></label>`;
  if (!(c.kind === 'info' && c.key === 'group')) {
    h += `<div class="ef"><span>Format</span><div class="part"><input type="number" data-f="digits" min="${c.places ? 0 : 1}" max="12" value="${c.digits}">` +
      `<select data-f="places" title="How its numbers show; they keep full precision">${opt('0', 'significant figures', c.places ? 1 : 0)}${opt('1', 'decimal places', c.places ? 1 : 0)}</select></div></div>`;
  }
  if (c.error) h += `<p class="eerr">${esc(c.error)}</p>`;
  ed.innerHTML = h;
  ed.hidden = false;
  if (place || !ed.dataset.placed) {
    const th = $('tab').querySelector(`th[data-k="${k}"]`);
    const r = (th || $('addcol')).getBoundingClientRect();
    ed.style.left = Math.max(8, Math.min(r.left + scrollX, scrollX + innerWidth - ed.offsetWidth - 12)) + 'px';
    ed.style.top = (r.bottom + scrollY + 6) + 'px';
    ed.dataset.placed = '1';
  }
  if (focus) { const f = ed.querySelector(`[data-f="${focus}"]`); if (f) f.focus(); }
}
function edit(set) { return pcmd({cmd: 'set', k: D.editing, set}); }
$('editor').addEventListener('change', (e) => {
  const f = e.target.dataset.f, c = D.st.columns[D.editing], v = e.target.value;
  if (!f || !c) return;
  if (f === 'what') { const [kind, key] = v.split(':'); return pcmd({cmd: 'what', k: D.editing, kind, key, channel: D.resp, trial: curTrial()}); }
  if (f === 'name') return pcmd({cmd: 'name', k: D.editing, name: v});
  if (f === 'mode' || f === 'a_ms' || f === 'b_ms') return edit({sel: {...c.sel, [f]: f === 'mode' ? v : Math.max(0, +v || 0)}});
  if (f === 'digits') return edit({digits: +v});
  if (f === 'places') return edit({places: v === '1'});
  edit({[f]: v});
});
$('editor').addEventListener('mousedown', (e) => {          // put a name in without leaving the formula
  if (e.target.closest('[data-ins]')) e.preventDefault();
});
$('editor').addEventListener('click', (e) => {
  const ins = e.target.closest('[data-ins]');
  if (ins) return insert(ins.dataset.ins);
  const sg = e.target.closest('[data-seg]');
  if (sg) return pcmd({cmd: 'segment', k: D.editing, letter: sg.dataset.seg, extend: e.shiftKey, trial: curTrial()});
  const a = e.target.closest('[data-a]');
  if (!a) return;
  const k = D.editing;
  if (a.dataset.a === 'close') { D.editing = -1; return renderEditor(); }
  if (a.dataset.a === 'remove') { D.editing = -1; D.picked = -1; return pcmd({cmd: 'remove', k}); }
  const by = a.dataset.a === 'left' ? -1 : 1;
  D.editing = D.picked = k + by;
  pcmd({cmd: 'move', k, by}).then(() => renderEditor(true));
});
$('editor').addEventListener('input', (e) => {
  if (e.target.dataset.fn === undefined || !e.target.value) return;
  const f = e.target.value;
  e.target.value = '';
  insert(f + '(');
});
function insert(text) {
  const box = $('editor').querySelector('[data-f="expr"]');
  if (!box) return;
  const a = box.selectionStart ?? box.value.length, b = box.selectionEnd ?? a;
  box.value = box.value.slice(0, a) + text + box.value.slice(b);
  box.focus();
  box.setSelectionRange(a + text.length, a + text.length);
}

// ---------------------------------------------------------------- charts: Y against X
function renderCharts() {
  const st = D.st, box = $('padcharts');
  $('tgrid').classList.toggle('with', st.charts.length > 0);
  const ids = new Set(st.charts.map((c) => c.id));
  for (const [id, card] of D.charts) if (!ids.has(id)) { card.el.remove(); D.charts.delete(id); }
  for (const c of st.charts) {
    let card = D.charts.get(c.id);
    if (!card) { card = makeChart(c); D.charts.set(c.id, card); }
    card.cfg = c;
    fillChart(card);
    box.append(card.el);
    const key = JSON.stringify([st.version, c, st.columns.map((x) => [x.name, x.shown])]);
    if (key !== card.key) { card.key = key; fetchChart(card); }
  }
}
function makeChart(c) {
  const el = document.createElement('div');
  el.className = 'pchart';
  el.innerHTML = `<div class="chead"><b>Y against X</b><span class="spacer"></span><button class="ib" data-a="swap" title="With one Y ticked: it runs across, X runs up">Swap X and Y</button><button class="ib" data-a="close" title="Remove this chart">×</button></div>
    <div class="csets"></div><div class="cbody"></div><div class="legend"></div>`;
  const card = {el, cfg: c, key: '', seq: 0, data: null};
  el.addEventListener('click', (e) => {
    const a = e.target.closest('[data-a]'), y = e.target.closest('[data-y]'), dot = e.target.closest('circle[data-r]');
    if (dot) return setRow(+dot.dataset.r, true);
    if (a && a.dataset.a === 'close') return pcmd({cmd: 'chart_remove', id: card.cfg.id});
    if (a && a.dataset.a === 'swap') {
      const f = card.cfg.fields, look = card.cfg.look || {};
      if (!f.x || f.x === '#row' || (f.y || []).length !== 1) return;
      return pcmd({cmd: 'chart_set', id: card.cfg.id, x: f.y[0], y: [f.x], xlog: !!look.ylog, ylog: !!look.xlog});
    }
    if (y) {
      const now = new Set(card.cfg.fields.y || []);
      now.has(y.dataset.y) ? now.delete(y.dataset.y) : now.add(y.dataset.y);
      pcmd({cmd: 'chart_set', id: card.cfg.id, y: D.st.columns.map((x) => x.name).filter((n) => now.has(n))});
    }
  });
  el.addEventListener('change', (e) => {
    const f = e.target.dataset.f;
    if (f === 'x') pcmd({cmd: 'chart_set', id: card.cfg.id, x: e.target.value || null});
    if (f === 'xlog' || f === 'ylog') pcmd({cmd: 'chart_set', id: card.cfg.id, [f]: e.target.checked});
  });
  return card;
}
function fillChart(card) {
  const st = D.st, f = card.cfg.fields || {}, look = card.cfg.look || {};
  const nums = st.columns.filter((c) => c.number), ys = new Set(f.y || []);
  const opt = (v, t, cur) => `<option value="${esc(v)}"${v === cur ? ' selected' : ''}>${esc(t)}</option>`;
  const html = !nums.length ? '<span>Add columns to the DataPad first: then choose what to plot here.</span>' :
    `<span>Y</span><span class="chips">${nums.map((c) => `<button class="chip" data-y="${esc(c.name)}" aria-pressed="${ys.has(c.name)}" title="Plot ${esc(plain(c.shown))} up the side">${sub(c.shown)}</button>`).join('')}</span>
     <label>X <select data-f="x">${opt('', 'Choose…', f.x || '')}${opt('#row', 'Row order', f.x || '')}${nums.map((c) => opt(c.name, plain(c.shown), f.x || '')).join('')}</select></label>
     <label><input type="checkbox" data-f="xlog"${look.xlog ? ' checked' : ''}> Log X</label><label><input type="checkbox" data-f="ylog"${look.ylog ? ' checked' : ''}> Log Y</label>`;
  const sets = card.el.querySelector('.csets');
  if (sets.dataset.html !== html) { sets.innerHTML = html; sets.dataset.html = html; }
  card.el.querySelector('[data-a=swap]').hidden = !(f.x && f.x !== '#row' && (f.y || []).length === 1);
}
async function fetchChart(card) {
  const seq = ++card.seq;
  let d;
  try { d = await (await post('/pad/chart', {id: card.cfg.id})).json(); } catch (e) { return; }
  if (seq !== card.seq) return;
  card.data = d;
  drawChart(card);
}
function drawChart(card) {
  const d = card.data, body = card.el.querySelector('.cbody'), legend = card.el.querySelector('.legend');
  legend.innerHTML = '';
  if (!d) return;
  if (d.note) { body.innerHTML = `<div class="prompt">${esc(d.note)}</div>`; return; }
  const pts = d.points;
  if (!pts.length) { body.innerHTML = '<div class="prompt">Nothing to plot: no ticked row has both numbers</div>'; return; }
  const tf = (log) => log ? (v) => Math.log10(v) : (v) => v;
  const fx = tf(d.xlog), fy = tf(d.ylog);
  const range = (v) => {
    let lo = Math.min(...v), hi = Math.max(...v);
    if (hi - lo < 1e-12) { const m = Math.abs(lo) * 0.1 || 1; lo -= m; hi += m; }
    const p = (hi - lo) * 0.07;
    return [lo - p, hi + p];
  };
  const [x0, x1] = range(pts.map((p) => fx(p[0]))), [y0, y1] = range(pts.map((p) => fy(p[1])));
  const L = 80, R = 14, T = 10, Bm = 52;
  const side = Math.max(200, Math.min(440, body.clientWidth - L - R));      // a square plot box
  const W = side + L + R, Hh = side + T + Bm;
  const X = (v) => L + (fx(v) - x0) / (x1 - x0) * side, Y = (v) => T + side - (fy(v) - y0) / (y1 - y0) * side;
  const ticks = (lo, hi, log) => {
    if (!log) return niceTicks(lo, hi);
    const dec = [];
    for (let e = Math.ceil(lo); e <= Math.floor(hi); e++) dec.push(e);
    return (dec.length >= 2 ? dec : niceTicks(lo, hi)).map((e) => 10 ** e);
  };
  const xt = ticks(x0, x1, d.xlog), yt = ticks(y0, y1, d.ylog);
  const yUnits = new Set(d.ys.map((s) => s.unit)), yUnit = yUnits.size === 1 ? [...yUnits][0] : '';
  const xl = tickText(xt, d.x.unit), yl = tickText(yt, yUnit);
  const unitOf = (t) => (t.find((s) => s.includes(' ')) || '').split(' ').slice(1).join(' ');
  const xu = unitOf(xl) || d.x.unit, yu = unitOf(yl) || yUnit;
  let s = `<svg class="scatter" viewBox="0 0 ${W} ${Hh}" width="${W}" height="${Hh}">`;
  s += xt.map((v) => `<line class="grid" x1="${X(v)}" x2="${X(v)}" y1="${T}" y2="${T + side}"/>`).join('');
  s += yt.map((v) => `<line class="grid" x1="${L}" x2="${L + side}" y1="${Y(v)}" y2="${Y(v)}"/>`).join('');
  s += `<rect class="frame" x="${L}" y="${T}" width="${side}" height="${side}"/>`;
  s += xt.map((v, k) => `<text class="tick" x="${X(v)}" y="${T + side + 16}" text-anchor="middle">${esc(xl[k].split(' ')[0])}</text>`).join('');
  s += yt.map((v, k) => `<text class="tick" x="${L - 6}" y="${Y(v) + 4}" text-anchor="end">${esc(yl[k].split(' ')[0])}</text>`).join('');
  const ylab = d.ys.map((y) => subSvg(y.name)).join(', ');
  s += `<text class="alab" x="${L + side / 2}" y="${Hh - 10}" text-anchor="middle">${d.x.sub ? subSvg(d.x.name) : esc(d.x.name)}${xu ? ` (${esc(xu)})` : ''}${d.xlog ? ', log' : ''}</text>`;
  s += `<text class="alab" transform="translate(16 ${T + side / 2}) rotate(-90)" text-anchor="middle">${ylab}${yu ? ` (${esc(yu)})` : ''}${d.ylog ? ', log' : ''}</text>`;
  const num = (v, u) => minus(tickText([v], u)[0]);
  s += pts.map(([x, y, c, r, , t], k) => `<circle data-r="${r}" data-k="${k}" cx="${X(x).toFixed(1)}" cy="${Y(y).toFixed(1)}" r="4" fill="${c}"><title>${esc(t)}: ${esc(num(x, d.x.unit))}, ${esc(num(y, yUnit))} · click to see it</title></circle>`).join('');
  s += '</svg>';
  body.innerHTML = s;
  if (d.ys.length > 1) legend.innerHTML = d.ys.map((y) => `<span><span class="dot" style="background:${y.color}"></span>${sub(y.name)}</span>`).join('');
  else legend.innerHTML = '<span>Each dot is a ticked row, in its group’s color. Click one to see its trial.</span>';
  markDots(card);
}
function markDots(card) {
  const k = D.sum < 0 && D.st ? curIndex() : -1;
  for (const c of card.el.querySelectorAll('circle.cur')) c.classList.remove('cur');
  for (const c of card.el.querySelectorAll(`circle[data-r="${k}"]`)) { c.classList.add('cur'); c.parentNode.append(c); }
}

// ---------------------------------------------------------------- keys and sizes
document.addEventListener('keydown', (e) => {
  const t = e.target;
  if (t.isContentEditable || ['INPUT', 'SELECT', 'TEXTAREA'].includes(t.tagName)) return;
  if (e.key === 'Escape') {
    closeMenus();
    if (D.editing >= 0) { D.editing = -1; return renderEditor(); }
    if (D.drag) { D.drag = null; return redraw(); }
    if (D.picked >= 0) pick(-1);
    return;
  }
  if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && D.st && D.st.rows.length) {
    e.preventDefault();
    const shown = D.st.rows.map((r, k) => [r, k]).filter(([r]) => showOff() || !r.off).map(([, k]) => k);
    const at = shown.indexOf(curIndex());
    const to = shown[Math.max(0, Math.min(shown.length - 1, at + (e.key === 'ArrowDown' ? 1 : -1)))];
    if (to !== undefined) setRow(to, true);
  }
});
let resizeTimer = 0;
window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => {
  D.shape = '';
  preview(true);
  for (const card of D.charts.values()) drawChart(card);
}, 150); });
})();
