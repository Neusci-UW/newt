// The Record page's half: reads frames from /live/stream and draws one uPlot strip per
// channel (then the calculated channels) as each arrives; the setup (device, rate, wiring,
// channels, calculated channels); comments, marked on the traces; and review, one recorded
// trial at a time, its frames fetched from /live/review.
'use strict';

const STRIP_H = 150;
const $ = (id) => document.getElementById(id);
let S = null;                 // the last /live/state
let R = null;                 // S.review while reviewing
let mode = 'live';            // or 'review'
const strips = [];            // {u, ch, yr: [lo, hi], auto, valEl, rateEl, loEl, hiEl}
let stripSig = '';
let lastFrame = null;
let marks = [];               // [time on the x axis (s), text]: comments, drawn as lines on the traces
let muted = '#888';
const ages = [], arrivals = [], canvasAges = [], draws = [], drawnAt = [];
let lastBuild = 0;

// the key this launch made (in the address after #): the server answers nothing without it
const H = {'X-Newt-Key': new URLSearchParams(location.hash.slice(1)).get('k') || ''};
const post = (d) => fetch('/live/cmd', {method: 'POST', headers: H, body: JSON.stringify(d)}).then((r) => r.json());
const minus = (s) => String(s).replace('-', '−');
const unminus = (s) => String(s).replace(/[−–]/g, '-').trim();
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const dark = () => matchMedia('(prefers-color-scheme: dark)').matches;
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'})[c]);
const num = (v) => minus(String(+(+v).toPrecision(6)));
const secs = (s) => (s >= 1 ? +s.toPrecision(3) : +s.toPrecision(2)) + ' s';
const typing = () => /^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement.tagName) || document.activeElement.isContentEditable;

function fmtVal(v, units) {
  if (!isFinite(v)) return '–';
  const a = Math.abs(v);
  return minus(v.toFixed(a >= 100 ? 0 : a >= 10 ? 1 : 2)) + ' ' + units;
}

// A command; its answer is the new state, maybe asking first (confirm) or offering more (recolor).
function act(d) {
  return post(d).then((st) => {
    if (st.confirm) ask(st.confirm, 'Go ahead', () => act({...d, confirmed: true}));
    else if (st.recolor) {
      const r = st.recolor;
      ask(`${r.n} trial${r.n === 1 ? '' : 's'} already recorded in this session show${r.n === 1 ? 's' : ''} ${r.name} in another color. ` +
          'Use the new color for them too? (Only the color changes, not the data.)', 'Recolor them',
          () => act({cmd: 'recolor', name: r.name, color: r.color}));
    }
    setupKey = '';                      // (put back what the server has, if it refused)
    if (!st.problem) problem = null;    // (an older problem gives way to what just happened)
    showState(st);
    return st;
  });
}

function ask(text, yes, onYes) {
  if ($('setup').hidden) toggleSetup(true);
  const box = $('ask');
  box.querySelector('span').textContent = text;
  $('askyes').textContent = yes;
  box.hidden = false;
  $('askyes').onclick = () => { box.hidden = true; onYes(); };
  $('askno').onclick = () => { box.hidden = true; };
}

// ---------------------------------------------------------------- strips
function chanList() { return mode === 'review' && R ? R.channels : S ? S.channels : []; }

function maybeRebuild() {
  const chs = chanList();
  const sig = mode + JSON.stringify(chs.map((c) => [c.name, c.units, c.light, c.dark, c.width || 0, !!c.calc,
                                                    c.problem || '', c.formula || '', !!c.stim]));
  if (sig !== stripSig) { stripSig = sig; buildStrips(chs); }
}

function buildStrips(chs) {
  const box = $('strips');
  box.replaceChildren();
  strips.length = 0;
  const font = '11.5px "IBM Plex Mono", ui-monospace, Menlo, monospace';
  muted = css('--muted');
  const line = css('--line');
  const width = box.clientWidth;
  chs.forEach((ch, i) => {
    const last = i === chs.length - 1;
    const color = dark() ? ch.dark : ch.light;
    const el = document.createElement('div');
    el.className = 'strip';
    el.innerHTML = `<div class="shead"><span class="dot" style="background:${color}"></span>
      <span class="sname">${esc(ch.name)}</span>
      ${ch.calc ? `<span class="fx" title="Calculated: ${esc(ch.name)} = ${esc(ch.formula || '')}">ƒ</span>` : ''}
      ${ch.stim ? '<span class="fx" title="This input records the stimulus">stimulus</span>' : ''}
      <span class="sval"></span><span class="srate"></span>
      ${ch.problem ? `<span class="sprob">Can't calculate: ${esc(ch.problem)}</span>` : ''}
      <span class="spacer"></span>
      <span class="yr" title="The plot's bottom and top: type a number to set one">from <input class="ylo" inputmode="decimal"> to <input class="yhi" inputmode="decimal"> ${esc(ch.units)}</span>
      <div class="seg"><button data-z="out" title="Zoom out (twice the range)">−</button><button data-z="in" title="Zoom in (half the range)">+</button><button data-z="auto" title="Fit the trace">Auto</button></div></div>
      <div class="chart"></div>`;
    box.append(el);
    const grid = {stroke: line, width: 1}, ticks = {stroke: line, width: 1, size: 4};
    const u = new uPlot({
      width, height: STRIP_H + (last ? 30 : 0),
      padding: [6, 10, 0, 0],
      legend: {show: false},
      cursor: {sync: {key: 'live'}, y: false, points: {show: false}, drag: {x: false, y: false, setScale: false},
               bind: {dblclick: () => null}},
      scales: {x: {time: false, auto: false}, y: {auto: false}},
      axes: [
        {stroke: muted, grid, ticks, font, size: last ? 30 : 4,
         values: last ? (u, v) => v.map((x) => minus(+x.toPrecision(6)) + ' s') : (u, v) => v.map(() => '')},
        {stroke: muted, grid, ticks, font, size: 68,
         values: (u, v) => v.map((y) => minus(String(+y.toPrecision(3))) + ' ' + ch.units)},
      ],
      series: [{}, {stroke: color, width: Math.max(0.5, Math.min(20, ch.width || 1.25)), points: {show: false}}],
      hooks: {draw: [(u) => drawMarks(u, i === 0)]},
    }, [new Float64Array(0), new Float32Array(0)], el.querySelector('.chart'));
    u.batch(() => { u.setScale('x', {min: 0, max: 1}); u.setScale('y', {min: -1, max: 1}); });   // (no data yet)
    const s = {u, ch, yr: null, auto: true, valEl: el.querySelector('.sval'), rateEl: el.querySelector('.srate'),
               loEl: el.querySelector('.ylo'), hiEl: el.querySelector('.yhi')};
    if (mode === 'live' && ch.y) { s.yr = ch.y.slice(); s.auto = false; }
    el.querySelector('.seg').addEventListener('click', (e) => zoom(s, e.target.dataset.z));
    for (const inp of [s.loEl, s.hiEl]) {
      inp.addEventListener('change', () => typedY(s));
      inp.addEventListener('keydown', (e) => { if (e.key === 'Enter') inp.blur(); });
    }
    u.over.addEventListener('click', (e) => clicked(u.posToVal(e.offsetX, 'x')));
    u.over.addEventListener('dblclick', (e) => doubleClicked(u.posToVal(e.offsetX, 'x')));
    strips.push(s);
    showAuto(s);
  });
  sendCols();
  if (lastFrame) draw(lastFrame);
}

function drawMarks(u, label) {
  if (!marks.length) return;
  const ctx = u.ctx, {top, height} = u.bbox, dpr = devicePixelRatio;
  const x0 = u.scales.x.min, x1 = u.scales.x.max;
  ctx.save();
  ctx.strokeStyle = ctx.fillStyle = muted;
  ctx.lineWidth = dpr;
  ctx.setLineDash([4 * dpr, 3 * dpr]);
  ctx.font = `${11.5 * dpr}px "IBM Plex Sans", system-ui, sans-serif`;
  for (const [t, text] of marks) {
    if (!(t >= x0 && t <= x1)) continue;
    const x = Math.round(u.valToPos(t, 'x', true)) + 0.5;
    ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, top + height); ctx.stroke();
    if (label) ctx.fillText(text.length > 40 ? text.slice(0, 39) + '…' : text, x + 4 * dpr, top + 12 * dpr);
  }
  ctx.restore();
}

function showAuto(s) { s.u.root.parentNode.parentNode.querySelector('[data-z=auto]').setAttribute('aria-pressed', String(s.auto)); }

function keepY(s) {             // live: the range is kept (from launch to launch); review: only on screen
  showAuto(s);
  if (mode === 'live') act({cmd: 'yrange', name: s.ch.name, lo: s.auto ? null : s.yr[0], hi: s.auto ? null : s.yr[1]});
  if (lastFrame) draw(lastFrame);
}

function zoom(s, how) {
  if (!how) return;
  if (how === 'auto') { s.auto = true; s.yr = null; }
  else if (s.yr) {
    const mid = (s.yr[0] + s.yr[1]) / 2, half = (s.yr[1] - s.yr[0]) / 2 * (how === 'in' ? 0.5 : 2);
    s.yr = [mid - half, mid + half];
    s.auto = false;
  }
  keepY(s);
}

function typedY(s) {
  const lo = parseFloat(unminus(s.loEl.value)), hi = parseFloat(unminus(s.hiEl.value));
  if (!isFinite(lo) || !isFinite(hi) || lo === hi) { showY(s, true); return; }
  s.yr = [Math.min(lo, hi), Math.max(lo, hi)];
  s.auto = false;
  keepY(s);
}

function showY(s, force) {
  const r = s.yr;
  for (const [el, v] of [[s.loEl, r && r[0]], [s.hiEl, r && r[1]]]) {
    if (!force && document.activeElement === el) continue;
    const t = r ? minus(String(+v.toPrecision(3))) : '';
    if (el.value !== t) el.value = t;
  }
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
    if (!strips.length) return;
    if (mode === 'live') post({cmd: 'cols', n: Math.round(strips[0].u.bbox.width / devicePixelRatio)});
    else reviewDraw();
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
  if (mode !== 'live') return;            // (reviewing: the live traces wait)
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
      const r = await fetch('/live/stream', {cache: 'no-store', headers: H});
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
  $('fps').textContent = S && S.running && mode === 'live' ? out.fps.toFixed(0) : '–';
  $('build').textContent = ms(out.build_ms, 2);
  strips.forEach((s, i) => {
    s.valEl.textContent = mode === 'live' && lastFrame ? fmtVal(lastFrame.newest[i], s.ch.units) : '';
    showY(s);
  });
  return out;
}

let problem = null;            // [what went wrong, until when]

function showState(st) {
  S = st;
  if (st.problem) problem = [st.problem, performance.now() + 8000];     // shown for a while
  const prob = problem && performance.now() < problem[1] ? problem[0] : '';
  const was = mode;
  mode = st.review && st.review.active ? 'review' : 'live';
  R = mode === 'review' ? st.review : null;
  document.body.classList.toggle('reviewing', mode === 'review');
  if (mode !== was) {
    lastFrame = null;
    rvKey = '';
    if (mode === 'live') showVideo({video: null});
  }
  maybeRebuild();
  showWindows();
  if (mode === 'review') showReview(R, prob);
  else {
    $('run').textContent = st.running ? 'Stop' : 'Run';
    $('chunk').textContent = st.chunk_ms.toFixed(0) + ' ms';
    const status = $('status');
    const recording = st.record && st.record.recording;
    const what = recording ? 'Recording' : st.record && st.record.windows ? 'Saving around each stimulus' : 'Running';
    const khz = st.fs >= 1000 ? `${+(st.fs / 1000).toPrecision(4)} kHz` : `${+st.fs.toPrecision(4)} Hz`;
    status.textContent = st.error ? st.error : st.running ? `${what} · ${khz} · ${st.setup.name}` : `Stopped · ${st.setup.name}`;
    status.classList.toggle('bad', !!st.error);
    if (st.record) showRecord(st.record, prob);
    strips.forEach((s, i) => {
      const r = st.channels[i] && st.channels[i].rate;
      s.rateEl.textContent = r == null || !st.running ? '' : `${r.toFixed(1)} spikes/s`;
    });
  }
  if (!$('setup').hidden) showSetup(st.setup, prob);
  for (const s of strips) if (s.u.scales.x.min != null) s.u.redraw(false, false);      // (the comment marks)
}

function showWindows() {
  const win = $('win');
  const opts = mode === 'review' ? [0, ...S.windows] : S.windows;      // 0: the whole trial
  const key = mode + opts.join();
  if (win.dataset.key !== key) {
    win.dataset.key = key;
    win.replaceChildren(...opts.map((w) => {
      const b = document.createElement('button');
      b.textContent = w ? secs(w) : 'Whole';
      b.dataset.w = w;
      b.onclick = () => setWindow(w);
      return b;
    }));
  }
  const cur = mode === 'review' ? rv.win : S.window_s;
  [...win.children].forEach((b) => b.setAttribute('aria-pressed', String(+b.dataset.w === cur)));
  if (document.activeElement !== $('wins')) {
    const show = mode === 'review' ? (cur || (R ? R.duration : 0)) : cur;
    $('wins').value = show ? String(+show.toPrecision(3)) : '';
  }
}

function setWindow(w) {
  if (mode === 'review') { rv.win = w; reviewDraw(); showWindows(); }
  else act({cmd: 'window', s: w});
}
$('wins').addEventListener('change', (e) => {
  const v = parseFloat(unminus(e.target.value));
  if (v > 0) setWindow(v); else showWindows();
});
$('wins').addEventListener('keydown', (e) => { if (e.key === 'Enter') e.target.blur(); });

// ---------------------------------------------------------------- Record
const clock = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;

function showRecord(r, problem) {
  const btn = $('rec');
  btn.classList.toggle('live', r.recording);
  $('rectext').textContent = r.recording ? `Stop recording · ${clock(r.secs)}` : r.windows ? 'Record: On · each stimulus'
    : r.on ? 'Record: On' : 'Record: Off';
  btn.title = r.recording ? `Recording ${r.trial}: click to stop recording`
    : r.windows ? 'Saving just around each stimulus (set on the Stimulator tab). Click to turn recording off.'
    : r.on ? (S && S.running ? 'Click to turn recording off' : 'Run will record too. Click to turn recording off.')
    : 'Turn on to record: now while running, or as soon as you press Run';
  const box = $('comment');
  box.disabled = !r.recording;
  box.placeholder = r.recording ? 'Type a comment, press Enter'
    : r.windows ? 'Each stimulus is saved as its own trial, with no comments' : 'Press Record to add comments';
  if (document.activeElement !== $('label')) $('label').value = r.label;
  $('sesstitle').textContent = 'Session';
  $('sname').textContent = r.session || 'None yet';
  const n = r.n_trials;
  $('scount').textContent = r.session
    ? `${n} trial${n === 1 ? '' : 's'}${r.recording ? ', one being recorded' : ''}` +
      (r.unsaved ? ' · kept in data/unsaved, as the Qt app keeps a session it hasn\'t saved yet' : '')
    : 'Your first trial starts one, in data/unsaved, as the Qt app does.';
  $('newsess').disabled = r.recording || r.windows || !r.session;
  $('addto').disabled = r.recording || r.windows;
  $('revbtn').disabled = r.recording || !r.n_trials;
  $('revbtn').title = r.recording ? 'Stop recording to look back through the trials'
    : 'Look back through this session\'s trials, one at a time';
  const msg = $('recmsg');
  msg.textContent = problem || (r.splitting ? 'Making a trial of each stimulus…' : r.message);
  msg.classList.toggle('bad', !!problem || r.error);
  marks = r.recording && r.t0 != null ? r.comments.map(([t, text]) => [r.t0 + t, text]) : [];
  showNotes(r.recording ? r.comments : [], r.recording);
}

// the comments of the trial being recorded, or of the trial reviewed: click one to change it
let notesKey = '';
function showNotes(list, editable) {
  const notes = $('notes');
  $('ntitle').hidden = !list.length;
  notes.classList.toggle('jump', mode === 'review');
  const key = mode + JSON.stringify(list) + editable;
  if (key === notesKey || notes.querySelector('[contenteditable=true]')) return;
  notesKey = key;
  notes.innerHTML = list.map(([t, text], row) => `<li data-row="${row}" data-t="${t}"><b title="${mode === 'review' ? 'Show this moment' : ''}">${t.toFixed(t < 10 ? 2 : 1)} s</b>` +
    `<span class="ntext" title="${editable ? 'Click to change it' : ''}">${esc(text)}</span>` +
    (editable ? '<button data-del title="Delete this comment">✕</button>' : '') + '</li>').join('');
  notes.scrollTop = notes.scrollHeight;
}

$('notes').addEventListener('click', (e) => {
  const li = e.target.closest('li');
  if (!li) return;
  const row = +li.dataset.row, live = mode === 'live';
  if (e.target.matches('[data-del]')) return act({cmd: 'comment_delete', row, live});
  if (e.target.tagName === 'B' && mode === 'review') return jumpTo(+li.dataset.t);
  const span = e.target.closest('.ntext');
  if (!span || li.querySelector('[data-del]') === null || span.isContentEditable) return;
  const was = span.textContent;
  span.contentEditable = 'true';
  span.focus();
  document.getSelection().selectAllChildren(span);
  const done = (save) => {
    span.contentEditable = 'false';
    notesKey = '';
    if (save && span.textContent.trim() && span.textContent.trim() !== was) act({cmd: 'comment_set', row, live, text: span.textContent});
    else { span.textContent = was; if (S) showState(S); }
  };
  span.onkeydown = (k) => {
    if (k.key === 'Enter') { k.preventDefault(); span.onblur = null; done(true); }
    if (k.key === 'Escape') { span.onblur = null; done(false); }
  };
  span.onblur = () => done(true);
});

$('rec').onclick = () => act({cmd: 'record', on: !(S && S.record && (S.record.on || S.record.recording))});
$('comment').addEventListener('keydown', (e) => {
  if (e.key !== 'Enter' || !e.target.value.trim()) return;
  if (mode === 'review') {
    if (pendingT != null) act({cmd: 'comment_add', t: pendingT, text: e.target.value});
    pendingT = null;
  } else act({cmd: 'comment', text: e.target.value});
  e.target.value = '';
});
$('label').addEventListener('change', (e) => act({cmd: 'label', text: e.target.value}));
$('label').addEventListener('keydown', (e) => { if (e.key === 'Enter') e.target.blur(); });
$('newsess').onclick = () => act({cmd: 'new_session'});
const addto = $('addto');
addto.addEventListener('focus', () => post({cmd: 'sessions'}).then((st) => {   // listed when you open it
  addto.replaceChildren(new Option('Keep adding to…', ''));
  for (const s of st.sessions || []) if (s.path !== (S.record && S.record.folder)) addto.append(new Option(s.name, s.path));
}));
addto.addEventListener('change', () => {
  if (addto.value) act({cmd: 'add_to', folder: addto.value});
  addto.value = '';
  addto.blur();
});
document.addEventListener('keydown', (e) => {
  if (typing() || e.metaKey || e.ctrlKey) return;
  if (e.key.toLowerCase() === 'c' && !$('comment').disabled) {   // C: the comment box
    e.preventDefault();
    $('comment').focus();
  } else if (mode === 'review' && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) {
    e.preventDefault();
    stepTrial(e.key === 'ArrowLeft' ? -1 : 1);
  }
});

// ---------------------------------------------------------------- review
const rv = {x0: 0, win: 0};    // where the plot starts (s) and how much it shows (0: the whole trial)
let rvKey = '', rvBusy = false, rvAgain = false, pendingT = null, videoKey = '', videoUrl = '';

function showReview(r, problem) {
  const key = `${r.folder}|${r.i}|${r.duration}`;
  if (key !== rvKey) {                      // another trial: each fitted to its own data, whole at first
    rvKey = key;
    rv.x0 = 0;
    pendingT = null;
    strips.forEach((s) => { s.auto = true; s.yr = null; showAuto(s); });
    reviewDraw();
    showVideo(r);
  }
  const trial = r.trials[r.i] || {stem: ''};
  const fs = r.fs >= 1000 ? `${+(r.fs / 1000).toPrecision(4)} kHz` : `${+r.fs.toPrecision(4)} Hz`;
  $('status').textContent = `Reviewing ${trial.stem} (${r.i + 1}/${r.trials.length}) · ${secs(r.duration)} · ${fs}`;
  $('status').classList.remove('bad');
  $('sesstitle').textContent = 'Reviewing';
  $('sname').textContent = r.session;
  $('scount').textContent = [`Trial ${r.i + 1} of ${r.trials.length}: ${trial.stem}`, r.label,
    r.stimuli ? `${r.stimuli} stimul${r.stimuli === 1 ? 'us' : 'i'}` : '', trial.hidden ? 'hidden in Analysis' : '']
    .filter(Boolean).join(' · ');
  const msg = $('recmsg');
  msg.textContent = problem || 'Double-click a trace to add a comment there. ← and → step through the trials.';
  msg.classList.toggle('bad', !!problem);
  const sel = $('rtrial');
  const tkey = r.folder + r.trials.length;
  if (sel.dataset.key !== tkey) {
    sel.dataset.key = tkey;
    sel.replaceChildren(...r.trials.map((t, i) => new Option(t.stem + (t.hidden ? ' (hidden)' : ''), i)));
  }
  if (document.activeElement !== sel) sel.value = r.i;
  $('prev').disabled = r.i <= 0;
  $('next').disabled = r.i >= r.trials.length - 1;
  const rs = $('rsess');
  if (!rs.querySelector(`option[value="${CSS.escape(r.folder)}"]`)) rs.replaceChildren(new Option(r.session, r.folder));
  if (document.activeElement !== rs) rs.value = r.folder;
  const box = $('comment');
  box.disabled = pendingT == null;
  box.placeholder = pendingT == null ? 'Double-click a trace to add a comment there'
    : `Comment at ${pendingT.toFixed(3)} s: type it, press Enter`;
  marks = r.comments;
  showNotes(r.comments, true);
}

function reviewDraw() {
  if (mode !== 'review' || !R) return;
  if (rvBusy) { rvAgain = true; return; }
  const dur = Math.max(R.duration, 1e-3);
  const win = rv.win && rv.win < dur ? rv.win : dur;
  rv.x0 = Math.min(Math.max(0, rv.x0), dur - win);
  const pan = $('pan');
  pan.hidden = win >= dur;
  pan.value = String(win >= dur ? 0 : Math.round(rv.x0 / (dur - win) * 1000));
  const cols = strips.length ? Math.round(strips[0].u.bbox.width / devicePixelRatio) : 1200;
  rvBusy = true;
  fetch('/live/review', {method: 'POST', headers: H, body: JSON.stringify({x0: rv.x0, x1: rv.x0 + win, cols})})
    .then((r) => r.ok ? r.arrayBuffer() : null)
    .then((buf) => { if (buf && mode === 'review') draw(parse(buf)); })
    .catch(() => {})
    .finally(() => { rvBusy = false; if (rvAgain) { rvAgain = false; reviewDraw(); } });
}

function jumpTo(t) {               // a moment of the trial, in the middle of the plot
  if (!R) return;
  const dur = R.duration, win = rv.win && rv.win < dur ? rv.win : dur;
  if (win < dur) { rv.x0 = t - win / 2; reviewDraw(); }
  seekVideo(t);
}

function clicked(t) { if (mode === 'review') seekVideo(t); }
function doubleClicked(t) {
  if (mode !== 'review' || !isFinite(t)) return;
  pendingT = Math.max(0, t);
  showReview(R, '');
  $('comment').focus();
}

function stepTrial(d) {
  if (!R) return;
  const i = Math.max(0, Math.min(R.trials.length - 1, R.i + d));
  if (i !== R.i) act({cmd: 'review_show', i});
}

function showVideo(r) {
  const key = r.video ? `${r.folder}|${r.i}` : '';
  if (key === videoKey) return;
  videoKey = key;
  const vid = $('vid');
  if (videoUrl) { URL.revokeObjectURL(videoUrl); videoUrl = ''; }
  vid.removeAttribute('src');
  $('video').hidden = !r.video;
  if (!r.video) return;
  fetch('/live/video', {method: 'POST', headers: H, body: '{}'}).then((x) => x.ok ? x.blob() : null).then((b) => {
    if (!b || videoKey !== key) return;
    videoUrl = URL.createObjectURL(b);
    vid.src = videoUrl;
  });
}
function seekVideo(t) {
  const vid = $('vid');
  if (R && R.video && vid.src && isFinite(t)) vid.currentTime = Math.max(0, t - R.video.offset);
}

$('revbtn').onclick = () => act({cmd: 'review_open'}).then(listSessions);
$('back').onclick = () => act({cmd: 'review_close'});
$('prev').onclick = () => stepTrial(-1);
$('next').onclick = () => stepTrial(1);
$('rtrial').addEventListener('change', (e) => { act({cmd: 'review_show', i: +e.target.value}); e.target.blur(); });
$('rsess').addEventListener('change', (e) => { act({cmd: 'review_open', folder: e.target.value}); e.target.blur(); });
$('pan').addEventListener('input', (e) => {
  if (!R) return;
  const dur = R.duration, win = rv.win && rv.win < dur ? rv.win : dur;
  rv.x0 = +e.target.value / 1000 * (dur - win);
  reviewDraw();
});
function listSessions() {
  post({cmd: 'sessions'}).then((st) => {
    const rs = $('rsess'), cur = R ? R.folder : '';
    const all = st.sessions || [];
    rs.replaceChildren(...all.map((s) => new Option(s.name, s.path)));
    if (cur && !all.some((s) => s.path === cur)) rs.prepend(new Option(R.session, cur));
    rs.value = cur;
  });
}

// ---------------------------------------------------------------- setup
let setupKey = '';
function toggleSetup(open) {
  $('setup').hidden = !open;
  $('setupbtn').setAttribute('aria-pressed', String(open));
  try { localStorage.setItem('newt.setupOpen', open ? '1' : ''); } catch (e) { /* (private window) */ }
  if (open && S) { setupKey = ''; showSetup(S.setup, ''); }
}
$('setupbtn').onclick = () => toggleSetup($('setup').hidden);

function showSetup(su, problem) {
  // the device
  const dev = $('device');
  const devs = su.devices.slice();
  if (!devs.some((d) => d.backend === su.backend && d.device === su.device))
    devs.push({backend: su.backend, device: su.device, label: su.name});
  const dkey = JSON.stringify(devs);
  if (dev.dataset.key !== dkey) {
    dev.dataset.key = dkey;
    dev.replaceChildren(...devs.map((d) => new Option(d.label + (d.note ? ` (${d.note})` : ''), JSON.stringify([d.backend, d.device]))),
                        new Option('Look for devices again…', 'refresh'));
  }
  if (document.activeElement !== dev) dev.value = JSON.stringify([su.backend, su.device]);
  // the rate and wiring
  if (document.activeElement !== $('rate')) $('rate').value = String(+su.rate);
  const rl = $('rates');
  if (rl.dataset.key !== su.rates.join()) { rl.dataset.key = su.rates.join(); rl.replaceChildren(...su.rates.map((r) => new Option('', r))); }
  const n = su.on.length;
  $('ratecap').textContent = 'per channel' + (su.max_rate ? ` · up to ${su.max_rate.toLocaleString()} with ${n} channel${n === 1 ? '' : 's'} on` : '');
  $('wirebox').hidden = !su.wired;
  [...$('wiring').children].forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.w === su.wiring)));
  // the channels
  const key = JSON.stringify([su.rows, su.inputs, su.palette]);
  const tb = $('chtab').tBodies[0];
  if (key !== setupKey && !tb.contains(document.activeElement)) {
    setupKey = key;
    if (!$('palette')) { const dl = document.createElement('datalist'); dl.id = 'palette'; document.body.append(dl); }
    $('palette').replaceChildren(...su.palette.map((c) => new Option('', c)));
    tb.innerHTML = su.rows.map((c, i) => {
      const inputs = su.inputs.includes(c.physical) ? su.inputs : [c.physical, ...su.inputs];
      return `<tr class="${c.enabled ? '' : 'off'}" data-i="${i}">
        <td><input type="checkbox" data-f="enabled" ${c.enabled ? 'checked' : ''} title="Record and show ${esc(c.name)}"></td>
        <td><input type="color" data-f="color" value="${esc(c.color || '#888888')}" list="palette" title="Trace color"></td>
        <td><input type="text" class="nm" data-f="name" value="${esc(c.name)}"></td>
        <td><select data-f="physical" title="Which input it reads">${inputs.map((p) => `<option${p === c.physical ? ' selected' : ''}>${p}</option>`).join('')}</select></td>
        <td><input type="text" data-f="units" value="${esc(c.units)}" style="width:62px"></td>
        <td><input type="text" class="num" data-f="scale" value="${num(c.scale)}" inputmode="decimal"></td>
        <td><input type="text" class="num" data-f="offset" value="${num(c.offset)}" inputmode="decimal"></td>
        <td><input type="text" class="num" data-f="width" value="${num(c.width)}" inputmode="decimal" style="width:46px" title="Line thickness (0.5–20 px)"></td>
        <td><input type="checkbox" data-stim ${c.stim ? 'checked' : ''} title="This input records the stimulator's output (wired to it)"></td>
        <td><button class="x" data-rm title="Remove ${esc(c.name)}">✕</button></td></tr>`;
    }).join('');
  }
  // calculated channels
  const ckey = JSON.stringify(su.calcs);
  const ul = $('calcs');
  if (ul.dataset.key !== ckey) {
    ul.dataset.key = ckey;
    ul.innerHTML = su.calcs.length ? su.calcs.map((c, k) => `<li><b>${esc(c.name)}</b> = <code>${esc(c.formula)}</code>` +
      (c.unit ? ` <span class="caption">(${esc(c.unit)})</span>` : '') +
      (c.problem ? ` <span class="bad">Can't calculate: ${esc(c.problem)}</span>` : '') +
      ` <button class="link" data-edit="${k}">Change</button><button class="link danger" data-del="${k}">Remove</button></li>`).join('')
      : '<li class="caption">None yet.</li>';
  }
  const ps = $('cpreset');
  if (ps.options.length === 1) su.presets.forEach((p, i) => ps.append(new Option(p, i)));
  // what happened
  const msg = $('setmsg');
  msg.textContent = problem || su.message + (su.config && !su.message ? `Started from ${su.config}.` : '');
  msg.classList.toggle('bad', !!problem || su.error);
}

$('device').addEventListener('change', (e) => {
  const v = e.target.value;
  e.target.blur();
  if (v === 'refresh') { act({cmd: 'setup_devices'}).then(() => { $('device').dataset.key = ''; showSetup(S.setup, ''); }); return; }
  const [backend, device] = JSON.parse(v);
  act({cmd: 'setup_device', backend, device});
});
$('rate').addEventListener('change', (e) => act({cmd: 'setup_rate', text: e.target.value}));
$('rate').addEventListener('keydown', (e) => { if (e.key === 'Enter') e.target.blur(); });
$('wiring').addEventListener('click', (e) => { if (e.target.dataset.w) act({cmd: 'setup_wiring', wiring: e.target.dataset.w}); });
const tbody = $('chtab').tBodies[0];
tbody.addEventListener('change', (e) => {
  const el = e.target, i = +el.closest('tr').dataset.i;
  if (el.matches('[data-stim]')) return act({cmd: 'setup_stim', i, on: el.checked});
  const what = el.dataset.f;
  if (!what) return;
  const value = el.type === 'checkbox' ? el.checked : what === 'scale' || what === 'offset' || what === 'width' ? unminus(el.value) : el.value;
  el.blur();
  act({cmd: 'setup_set', i, what, value});
});
tbody.addEventListener('keydown', (e) => { if (e.key === 'Enter' && e.target.type === 'text') e.target.blur(); });
tbody.addEventListener('click', (e) => { if (e.target.matches('[data-rm]')) act({cmd: 'setup_remove', i: +e.target.closest('tr').dataset.i}); });
$('addch').onclick = () => act({cmd: 'setup_add'});
$('savesetup').onclick = () => act({cmd: 'setup_save'});

// the calculated channel being made or changed
let calcK = null, checkTimer = 0;
function openCalc(k) {
  calcK = k;
  const c = k == null ? {name: '', formula: '', unit: ''} : S.setup.calcs[k];
  $('cname').value = c.name;
  $('cformula').value = c.formula;
  $('cunit').value = c.unit;
  $('cpreset').value = '';
  $('cpreset').parentNode.querySelector('[for=cpreset]').hidden = $('cpreset').hidden = k != null;
  $('csave').textContent = k == null ? 'Add' : 'Save';
  $('ccheck').textContent = '';
  $('calcform').hidden = false;
  $('addcalc').hidden = true;
  (k == null ? $('cpreset') : $('cformula')).focus();
  if (k != null) checkCalc();
}
function closeCalc() { $('calcform').hidden = true; $('addcalc').hidden = false; }
function checkCalc() {
  clearTimeout(checkTimer);
  checkTimer = setTimeout(() => post({cmd: 'calc_check', k: calcK, formula: $('cformula').value}).then((st) => {
    const el = $('ccheck');
    el.textContent = st.calc_check ? `Can't calculate: ${st.calc_check}` : '✓ It can be worked out';
    el.classList.toggle('bad', !!st.calc_check);
  }), 250);
}
$('addcalc').onclick = () => openCalc(null);
$('ccancel').onclick = closeCalc;
$('cformula').addEventListener('input', checkCalc);
$('cpreset').addEventListener('change', (e) => {
  if (e.target.value === '') return;
  post({cmd: 'calc_preset', i: +e.target.value}).then((st) => {
    const p = st.calc_preset;
    $('cname').value = p.name; $('cformula').value = p.formula; $('cunit').value = p.unit;
    checkCalc();
  });
});
$('calcform').addEventListener('submit', (e) => {
  e.preventDefault();
  act({cmd: 'calc_set', k: calcK, name: $('cname').value, formula: $('cformula').value, unit: $('cunit').value})
    .then((st) => { if (!st.problem) closeCalc(); });
});
$('calcs').addEventListener('click', (e) => {
  if (e.target.dataset.edit != null) openCalc(+e.target.dataset.edit);
  else if (e.target.dataset.del != null) act({cmd: 'calc_remove', k: +e.target.dataset.del});
});

// ---------------------------------------------------------------- controls
$('run').onclick = () => act({cmd: 'run', on: !(S && S.running)});

new ResizeObserver(() => {
  const w = $('strips').clientWidth;
  strips.forEach((s, i) => s.u.setSize({width: w, height: STRIP_H + (i === strips.length - 1 ? 30 : 0)}));
  sendCols();
}).observe($('strips'));
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => { stripSig = ''; if (S) maybeRebuild(); });

(async () => {
  showState(await (await fetch('/live/state', {headers: H})).json());
  try { if (localStorage.getItem('newt.setupOpen')) toggleSetup(true); } catch (e) { /* (private window) */ }
  if (mode === 'review') listSessions();
  StimUI.mount($('dock'), false);
  stream();
  setInterval(lag, 250);
  setInterval(() => fetch('/live/state', {headers: H}).then((r) => r.json()).then(showState), 500);
  const engine = /Chrome\//.test(navigator.userAgent) ? 'Chromium' : 'WebKit';
  setInterval(() => { const l = lag(); if (S && S.running && mode === 'live' && isFinite(l.age_med)) post({cmd: 'lag', engine, ...l}); }, 5000);
})();
