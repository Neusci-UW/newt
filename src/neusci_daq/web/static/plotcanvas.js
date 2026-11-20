// The Plot Canvas: a page to lay figures out on, for a lab report or a poster. Figures come
// from Single Plot (Send to Canvas), drawn by Python (web/figures.py) as pictures at the
// page's zoom, sharp again once a zoom settles. Drag a figure to move it (whole mm), its
// corner to resize it; each gets a panel letter in the order they sit. Text, lines, arrows,
// boxes and ellipses are drawn on the page; pictures can be added. Export saves the whole
// page as PDF, SVG or PNG, drawn by matplotlib as the Qt app saves it.
'use strict';

(() => {
const {post, get, esc, save} = Figs;
const E = (id) => document.getElementById(id);
const view = E('view'), sheet = E('sheet');
const PT_MM = 25.4 / 72;                     // a point in mm
const C = {
  st: null, s: 2, fitS: 2, zoom: 1, sel: new Set(), tool: 'select',
  pics: new Map(),                           // panel id -> {img, key, want}
  drag: null, picTimer: 0, editing: null,
};

// ---------------------------------------------------------------- talking to Python
async function load() {
  try { show(await (await get('/canvas/state')).json()); }
  catch (e) { problem('Newt is not answering.'); }
}
async function ccmd(d) {
  let st;
  try { st = await (await post('/canvas/cmd', d)).json(); }
  catch (e) { problem('Newt is not answering.'); return null; }
  show(st);
  problem(st.problem || '');
  return st;
}
function problem(text, good) {
  const p = E('cproblem');
  p.textContent = text || '';
  p.hidden = !text;
  p.className = good ? 'note' : 'problem';
}

// ---------------------------------------------------------------- the page
function show(st) {
  if (!st || !st.page) return;
  const first = !C.st, paperChanged = C.st && String(C.st.page.size) !== String(st.page.size);
  C.st = st;
  const paper = E('paper');
  if (!paper.options.length) paper.innerHTML = st.papers.map((n) => `<option>${esc(n)}</option>`).join('');
  paper.value = st.page.paper;
  for (const [id, v] of [['pagew', st.page.size[0]], ['pageh', st.page.size[1]]]) {
    [E(id).min, E(id).max] = st.side_mm;
    if (document.activeElement !== E(id)) E(id).value = v;
  }
  E('letters').checked = !!st.page.letters;
  if (document.activeElement !== E('textn')) E('textn').value = st.page.text_pt;
  if (document.activeElement !== E('textr')) E('textr').value = st.page.text_pt;
  const ids = new Set(st.panels.map((p) => 'p:' + p.id).concat(st.anns.map((a) => 'a:' + a.id)));
  for (const k of [...C.sel]) if (!ids.has(k)) C.sel.delete(k);
  if (!st.folder) problem('Open a session first (on Analysis): the page is saved with it.');
  if (first || paperChanged) fit();
  else draw();
}
function fit() {
  const [W, H] = C.st.page.size;
  C.fitS = Math.max(0.2, Math.min((view.clientWidth - 40) / W, (view.clientHeight - 40) / H));
  C.zoom = 1;
  scaled();
  view.scrollLeft = 0;
  view.scrollTop = 0;
}
function scaled() {                          // the zoom changed: place everything again, sharp pictures soon
  C.s = C.fitS * C.zoom;
  E('zoomlab').textContent = Math.round(C.zoom * 100) + '%';
  draw();
}
const mm = (v) => v * C.s;
// a label's words with true subscripts, as the saved page has them (figspec.mathify): V_m, I_B
const subs = (text) => esc(text).replace(/(^|[^\w$\\])([A-Za-zÀ-ɏͰ-Ͽ]+)_(\w+)/g, '$1$2<sub>$3</sub>');

function draw() {
  const st = C.st, [W, H] = st.page.size, s = C.s;
  const left = Math.max(20, (view.clientWidth - W * s) / 2);
  Object.assign(sheet.style, {left: left + 'px', top: '20px', width: W * s + 'px', height: H * s + 'px'});
  let pad = view.querySelector('.padder');
  if (!pad) { pad = document.createElement('div'); pad.className = 'padder'; pad.style.cssText = 'position:absolute;width:1px;height:1px'; view.append(pad); }
  pad.style.left = (left + W * s + 19) + 'px';
  pad.style.top = (20 + H * s + 19) + 'px';
  sheet.innerHTML = '';
  if (!st.panels.length && !st.anns.length) {
    const h = document.createElement('div');
    h.className = 'hint';
    h.style.left = mm(12) + 'px';
    h.style.top = mm(12) + 'px';
    h.textContent = 'Send figures here from Single Plot (Send to Canvas).';
    sheet.append(h);
  }
  for (const p of st.panels) {
    const d = document.createElement('div');
    d.className = 'pan' + (C.sel.has('p:' + p.id) ? ' lit' : '');
    d.dataset.p = p.id;
    place(d, p);
    const pic = C.pics.get(p.id) || {img: new Image(), key: ''};
    C.pics.set(p.id, pic);
    pic.img.alt = '';
    d.append(pic.img);
    if (st.page.letters && p.letter) {
      const L = document.createElement('span');
      L.className = 'letter';
      L.style.fontSize = mm(p.font * 1.4 * PT_MM) + 'px';
      L.style.left = '2px';
      L.textContent = p.letter;
      d.append(L);
    }
    if (!p.linked && p.kind !== 'image' && p.kind !== 'curves') {
      const f = document.createElement('span');
      f.className = 'frozen';
      f.textContent = 'frozen';
      d.append(f);
    }
    const c = document.createElement('i');
    c.className = 'corner';
    d.append(c);
    sheet.append(d);
  }
  for (const id of [...C.pics.keys()]) if (!st.panels.some((p) => p.id === id)) C.pics.delete(id);
  drawAnns();
  buttons();
  clearTimeout(C.picTimer);
  C.picTimer = setTimeout(pictures, 120);
}
function place(d, p) {
  Object.assign(d.style, {left: mm(p.x) + 'px', top: mm(p.y) + 'px', width: mm(p.w) + 'px', height: mm(p.h) + 'px'});
}

// the drawings: shapes in an SVG in mm, labels as text
function shapeSvg(a, cls) {
  const sw = a.width * PT_MM, col = esc(a.color);
  if (a.kind === 'line' || a.kind === 'arrow') {
    let out = `<line x1="${a.x0}" y1="${a.y0}" x2="${a.x1}" y2="${a.y1}" stroke="${col}" stroke-width="${sw}" stroke-linecap="round"/>`;
    if (a.kind === 'arrow') {
      const dx = a.x1 - a.x0, dy = a.y1 - a.y0, L = Math.hypot(dx, dy) || 1, ux = dx / L, uy = dy / L;
      const head = (4 + 2 * a.width) * PT_MM;
      const ax = a.x1 - head * (ux - 0.5 * uy), ay = a.y1 - head * (uy + 0.5 * ux);
      const bx = a.x1 - head * (ux + 0.5 * uy), by = a.y1 - head * (uy - 0.5 * ux);
      out += `<polygon points="${a.x1},${a.y1} ${ax},${ay} ${bx},${by}" fill="${col}" stroke="${col}" stroke-width="${sw}" stroke-linejoin="round"/>`;
    }
    return out;
  }
  const x = Math.min(a.x0, a.x1), y = Math.min(a.y0, a.y1), w = Math.abs(a.x1 - a.x0), h = Math.abs(a.y1 - a.y0);
  return a.kind === 'rect' ? `<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="none" stroke="${col}" stroke-width="${sw}"/>`
    : `<ellipse cx="${x + w / 2}" cy="${y + h / 2}" rx="${w / 2}" ry="${h / 2}" fill="none" stroke="${col}" stroke-width="${sw}"/>`;
}
function drawAnns() {
  const st = C.st, [W, H] = st.page.size;
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('class', 'anns');
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('width', mm(W));
  svg.setAttribute('height', mm(H));
  let inner = '';
  for (const a of st.anns) {
    if (a.kind === 'text') continue;
    const lit = C.sel.has('a:' + a.id);
    const hitW = Math.max(a.width * PT_MM, 8 / C.s);
    const x = Math.min(a.x0, a.x1), y = Math.min(a.y0, a.y1), w = Math.abs(a.x1 - a.x0), h = Math.abs(a.y1 - a.y0);
    const hit = a.kind === 'line' || a.kind === 'arrow'
      ? `<line class="hit" data-a="${a.id}" x1="${a.x0}" y1="${a.y0}" x2="${a.x1}" y2="${a.y1}" stroke-width="${hitW}"/>`
      : `<rect class="hit area" data-a="${a.id}" x="${x}" y="${y}" width="${w}" height="${h}" fill="transparent"/>`;
    const pad = 2 / C.s;
    inner += `<g data-g="${a.id}">${shapeSvg(a)}${hit}${lit ? `<rect class="sel" x="${x - pad}" y="${y - pad}" width="${w + 2 * pad}" height="${h + 2 * pad}"/>` : ''}</g>`;
  }
  svg.innerHTML = inner;
  sheet.append(svg);
  for (const a of st.anns) {
    if (a.kind !== 'text') continue;
    const t = document.createElement('div');
    t.className = 'atext' + (C.sel.has('a:' + a.id) ? ' lit' : '');
    t.dataset.a = a.id;
    Object.assign(t.style, {left: mm(a.x_mm) + 'px', top: mm(a.y_mm) + 'px', fontSize: mm(a.size * PT_MM) + 'px',
      color: a.color, fontWeight: a.bold ? '700' : '400'});
    t.innerHTML = subs(a.text);
    sheet.append(t);
  }
}

// each figure's picture, drawn by Python at this zoom (a picture put on the page: as it is)
async function pictures() {
  const dpr = Math.max(1, window.devicePixelRatio || 1);
  const dpi = Math.round(C.s * 25.4 * dpr / 10) * 10;
  for (const p of C.st.panels) {
    const pic = C.pics.get(p.id);
    if (!pic) continue;
    const key = p.kind === 'image' ? `${p.v}` : `${p.v}@${dpi}`;
    if (pic.key === key || pic.want === key) continue;
    pic.want = key;
    try {
      const r = await (await post('/fig/png', {which: 'panel', id: p.id, dpi})).json();
      if (pic.want !== key) continue;
      if (r.png) {
        const im = new Image();
        im.src = 'data:image/png;base64,' + r.png;
        await Figs.decoded(im);
        pic.img.src = im.src;
        pic.key = key;
      }
    } catch (e) { /* tried again next time */ }
    pic.want = null;
  }
}

// ---------------------------------------------------------------- picking
const picked = (k) => [...C.sel].filter((x) => x[0] === k).map((x) => +x.slice(2));
function buttons() {
  const ps = picked('p'), panels = C.st.panels.filter((p) => ps.includes(p.id));
  E('edit').disabled = !(panels.length === 1 && panels[0].kind !== 'image');
  E('freeze').disabled = E('dup').disabled = !panels.length;
  E('freeze').textContent = panels.length && panels.every((p) => !p.linked) ? 'Unfreeze' : 'Freeze';
  E('del').disabled = !C.sel.size;
}
function pick(key, add) {
  if (add) C.sel.has(key) ? C.sel.delete(key) : C.sel.add(key);
  else if (!C.sel.has(key)) C.sel = new Set([key]);
  else return;
  draw();
}
const at = (e) => { const r = sheet.getBoundingClientRect(); return {x: (e.clientX - r.left) / C.s, y: (e.clientY - r.top) / C.s}; };

view.addEventListener('mousedown', (e) => {
  if (e.button !== 0 || C.editing) return;
  if (e.target === view && (e.offsetX >= view.clientWidth || e.offsetY >= view.clientHeight)) return;   // its scroll bars
  const p0 = at(e);
  if (C.tool !== 'select') { e.preventDefault(); return startDraw(p0, e); }
  const pan = e.target.closest('.pan'), txt = e.target.closest('.atext'), hit = e.target.closest('[data-a]');
  const key = pan ? 'p:' + pan.dataset.p : (txt || hit) ? 'a:' + (txt || hit).dataset.a : null;
  e.preventDefault();
  const now = Date.now();                    // a double-click: a figure to change, a label to type in
  if (key && C.last && C.last.key === key && now - C.last.t < 450 && !e.shiftKey) { C.last = null; return twice(key); }
  C.last = {key, t: now};
  if (!key) {                                // a box dragged on the empty page picks what it touches
    if (!e.shiftKey) { C.sel.clear(); draw(); }
    const band = document.createElement('div');
    band.className = 'band';
    sheet.append(band);
    C.drag = {kind: 'band', p0, band, add: e.shiftKey};
    return;
  }
  if (pan && e.target.classList.contains('corner')) {
    const p = C.st.panels.find((q) => q.id === +pan.dataset.p);
    C.sel = new Set([key]);
    draw();
    C.drag = {kind: 'size', p0, p, w: p.w, h: p.h, el: sheet.querySelector(`.pan[data-p="${p.id}"]`)};
    return;
  }
  if (e.shiftKey) { pick(key, true); return; }
  pick(key, false);
  C.drag = {kind: 'move', p0, moved: false,
    panels: picked('p').map((id) => C.st.panels.find((q) => q.id === id)).filter(Boolean).map((p) => ({p, x: p.x, y: p.y})),
    anns: picked('a').map((id) => C.st.anns.find((q) => q.id === id)).filter(Boolean)};
});
window.addEventListener('mousemove', (e) => {
  const d = C.drag;
  if (!d) return;
  const p = at(e), dx = p.x - d.p0.x, dy = p.y - d.p0.y;
  if (d.kind === 'move') {
    d.moved = d.moved || Math.abs(dx) * C.s > 2 || Math.abs(dy) * C.s > 2;
    if (!d.moved) return;
    d.dx = dx;
    d.dy = dy;
    for (const q of d.panels) {
      q.nx = Math.round(q.x + dx);
      q.ny = Math.round(q.y + dy);
      const el = sheet.querySelector(`.pan[data-p="${q.p.id}"]`);
      if (el) { el.style.left = mm(q.nx) + 'px'; el.style.top = mm(q.ny) + 'px'; }
    }
    for (const a of d.anns) {
      const el = a.kind === 'text' ? sheet.querySelector(`.atext[data-a="${a.id}"]`) : sheet.querySelector(`g[data-g="${a.id}"]`);
      if (!el) continue;
      if (a.kind === 'text') { el.style.left = mm(a.x_mm + dx) + 'px'; el.style.top = mm(a.y_mm + dy) + 'px'; }
      else el.setAttribute('transform', `translate(${dx} ${dy})`);
    }
  } else if (d.kind === 'size') {
    d.nw = Math.max(15, Math.round(d.w + dx));
    d.nh = d.p.kind === 'image' ? Math.round(d.nw * d.h / d.w * 10) / 10 : Math.max(15, Math.round(d.h + dy));
    if (d.el) { d.el.style.width = mm(d.nw) + 'px'; d.el.style.height = mm(d.nh) + 'px'; }
  } else if (d.kind === 'band') {
    const x = Math.min(p.x, d.p0.x), y = Math.min(p.y, d.p0.y);
    d.box = [x, y, Math.abs(dx), Math.abs(dy)];
    Object.assign(d.band.style, {left: mm(x) + 'px', top: mm(y) + 'px', width: mm(d.box[2]) + 'px', height: mm(d.box[3]) + 'px'});
  } else if (d.kind === 'draw') {
    d.a.x1 = p.x;
    d.a.y1 = p.y;
    d.g.innerHTML = shapeSvg(d.a);
  }
});
window.addEventListener('mouseup', async () => {
  const d = C.drag;
  if (!d) return;
  C.drag = null;
  if (d.kind === 'move' && d.moved) {
    for (const q of d.panels) { q.p.x = q.nx; q.p.y = q.ny; }
    await ccmd({cmd: 'move', panels: d.panels.map((q) => [q.p.id, q.nx, q.ny]), anns: d.anns.map((a) => [a.id, d.dx, d.dy])});
  } else if (d.kind === 'size' && d.nw) {
    await ccmd({cmd: 'resize', id: d.p.id, w_mm: d.nw, h_mm: d.nh});
  } else if (d.kind === 'band') {
    d.band.remove();
    if (d.box) {
      const [x, y, w, h] = d.box, hits = (a, b, c, e) => a < x + w && x < a + c && b < y + h && y < b + e;
      for (const p of C.st.panels) if (hits(p.x, p.y, p.w, p.h)) C.sel.add('p:' + p.id);
      for (const a of C.st.anns) {
        const el = a.kind === 'text' ? sheet.querySelector(`.atext[data-a="${a.id}"]`) : null;
        const box = a.kind === 'text' ? [a.x_mm, a.y_mm, el ? el.offsetWidth / C.s : 10, el ? el.offsetHeight / C.s : 4]
          : [Math.min(a.x0, a.x1), Math.min(a.y0, a.y1), Math.abs(a.x1 - a.x0) || 0.1, Math.abs(a.y1 - a.y0) || 0.1];
        if (hits(...box)) C.sel.add('a:' + a.id);
      }
    }
    draw();
  } else if (d.kind === 'draw') {
    d.g.remove();
    const a = d.a;
    if ((Math.abs(a.x1 - a.x0) + Math.abs(a.y1 - a.y0)) * 4 < 3) return;     // just a click: nothing drawn
    const st = await ccmd({cmd: 'ann_add', ann: a});
    if (st && st.ann) { C.sel = new Set(['a:' + st.ann]); draw(); }
  }
});

// a double-click: a figure to change in Single Plot, a label to type in
function twice(key) {
  const id = +key.slice(2);
  if (key[0] === 'a') {
    const el = sheet.querySelector(`.atext[data-a="${id}"]`);
    if (el) editText(el);
    return;
  }
  const p = C.st.panels.find((q) => q.id === id);
  if (p && p.kind !== 'image') edit(p.id);
}
async function edit(id) {
  try {
    const st = await (await post('/fig/cmd', {cmd: 'edit', id})).json();
    if (st.problem) return problem(st.problem);
    location.href = 'singleplot.html' + location.hash;
  } catch (e) { problem('Newt is not answering.'); }
}
function editText(el, all) {
  C.editing = el;
  const was = C.st.anns.find((q) => q.id === +el.dataset.a);
  if (was) el.textContent = was.text;          // typed as written: V_m
  el.contentEditable = 'true';
  el.focus();
  const r = document.createRange();
  r.selectNodeContents(el);
  if (!all) r.collapse(false);
  const s = getSelection();
  s.removeAllRanges();
  s.addRange(r);
  el.onblur = () => {
    el.contentEditable = 'false';
    C.editing = null;
    const a = C.st.anns.find((q) => q.id === +el.dataset.a);
    const text = el.innerText.replace(/\n$/, '');
    if (a && text !== a.text) ccmd({cmd: 'ann_set', ids: [a.id], text});
    else draw();
  };
  el.onkeydown = (e) => { if (e.key === 'Escape') el.blur(); };
}

// ---------------------------------------------------------------- drawing
const style = () => ({color: E('acolor').value, width: Math.max(0.25, +E('awidth').value || 1),
  size: Math.max(4, +E('asize').value || 10), bold: E('abold').getAttribute('aria-pressed') === 'true'});
function setTool(t) {
  C.tool = t;
  for (const b of E('tools').querySelectorAll('button')) b.setAttribute('aria-pressed', b.dataset.t === t);
  view.classList.toggle('drawing', t !== 'select');
}
async function startDraw(p0) {
  const s = style();
  if (C.tool === 'text') {
    setTool('select');
    const st = await ccmd({cmd: 'ann_add', ann: {kind: 'text', x_mm: p0.x, y_mm: p0.y, text: 'Text', size: s.size, color: s.color, bold: s.bold}});
    if (st && st.ann) {
      C.sel = new Set(['a:' + st.ann]);
      draw();
      const el = sheet.querySelector(`.atext[data-a="${st.ann}"]`);
      if (el) editText(el, true);
    }
    return;
  }
  const svg = sheet.querySelector('svg.anns');
  const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
  svg.append(g);
  C.drag = {kind: 'draw', p0, g, a: {kind: C.tool, x0: p0.x, y0: p0.y, x1: p0.x, y1: p0.y, color: s.color, width: s.width}};
}
E('tools').onclick = (e) => { const b = e.target.closest('button'); if (b) setTool(b.dataset.t); };
function restyle(d) {                        // the drawings picked take the new color, width, size or bold
  const ids = picked('a').filter((id) => C.st.anns.some((a) => a.id === id));
  if (ids.length) ccmd({cmd: 'ann_set', ids, ...d});
}
E('acolor').onchange = () => restyle({color: E('acolor').value});
E('awidth').onchange = () => restyle({width: style().width});
E('asize').onchange = () => restyle({size: style().size});
E('abold').onclick = () => {
  const b = E('abold'), on = b.getAttribute('aria-pressed') !== 'true';
  b.setAttribute('aria-pressed', on);
  restyle({bold: on});
};

document.addEventListener('keydown', (e) => {
  if (C.editing || /INPUT|SELECT|TEXTAREA/.test(e.target.tagName)) return;
  if (e.key === 'Delete' || e.key === 'Backspace') { e.preventDefault(); remove(); }
  else if (e.key === 'Escape') { setTool('select'); C.sel.clear(); draw(); }
});
function remove() {
  if (!C.sel.size) return;
  ccmd({cmd: 'remove', ids: picked('p'), anns: picked('a')});
}

// ---------------------------------------------------------------- the bars' buttons
E('paper').onchange = (e) => {              // Custom: the size stays, to type your own
  if (C.st && !C.st.papers.slice(0, -1).includes(e.target.value)) { E('pagew').focus(); E('pagew').select(); return; }
  ccmd({cmd: 'paper', name: e.target.value});
};
for (const id of ['pagew', 'pageh']) E(id).onchange = () => {
  const [w, h] = [+E('pagew').value, +E('pageh').value];
  if (C.st && w > 0 && h > 0) ccmd({cmd: 'paper', size: [w, h]}).then(() => { E('pagew').value = C.st.page.size[0]; E('pageh').value = C.st.page.size[1]; });
  else if (C.st) [E('pagew').value, E('pageh').value] = C.st.page.size;
};
E('letters').onchange = (e) => ccmd({cmd: 'letters', on: e.target.checked});
let textT = 0;
const text = (v) => { clearTimeout(textT); textT = setTimeout(() => ccmd({cmd: 'text', pt: +v}), 200); };
E('textr').oninput = () => { E('textn').value = E('textr').value; text(E('textr').value); };
E('textn').onchange = () => { const v = Math.max(4, Math.min(36, +E('textn').value || 9)); E('textr').value = v; text(v); };
E('edit').onclick = () => { const id = picked('p')[0]; if (id !== undefined) edit(id); };
E('freeze').onclick = () => ccmd({cmd: 'freeze', ids: picked('p')});
E('dup').onclick = () => ccmd({cmd: 'duplicate', ids: picked('p')});
E('del').onclick = remove;
E('zfit').onclick = fit;
function zoomTo(z, cx, cy) {                 // keep the page under (cx, cy) where it is
  const r = view.getBoundingClientRect();
  cx = cx === undefined ? r.left + view.clientWidth / 2 : cx;
  cy = cy === undefined ? r.top + view.clientHeight / 2 : cy;
  const before = at({clientX: cx, clientY: cy});
  C.zoom = Math.max(0.25, Math.min(8, z));
  scaled();
  const after = at({clientX: cx, clientY: cy});
  view.scrollLeft += (before.x - after.x) * C.s;
  view.scrollTop += (before.y - after.y) * C.s;
}
E('zin').onclick = () => zoomTo(C.zoom * 1.25);
E('zout').onclick = () => zoomTo(C.zoom * 0.8);
view.addEventListener('wheel', (e) => {     // ⌘/Ctrl and scroll (or a pinch) zooms; plain scrolling moves round the page
  if (!(e.ctrlKey || e.metaKey)) return;
  e.preventDefault();
  zoomTo(C.zoom * 1.0015 ** -(e.deltaMode === 1 ? e.deltaY * 33 : e.deltaY), e.clientX, e.clientY);
}, {passive: false});
let resizeT = 0;
window.addEventListener('resize', () => { clearTimeout(resizeT); resizeT = setTimeout(() => { if (C.st) { const z = C.zoom; fit(); if (z !== 1) zoomTo(z); } }, 150); });

E('exportbtn').onclick = () => { E('exportmenu').hidden = !E('exportmenu').hidden; };
E('exportmenu').onclick = (e) => {
  const b = e.target.closest('button[data-fmt]');
  if (!b) return;
  E('exportmenu').hidden = true;
  problem('Saving…', true);
  save({what: 'page', fmt: b.dataset.fmt, dpi: 300}, (t, bad) => problem(t, !bad));
};

// ---------------------------------------------------------------- pictures added to the page
E('addimg').onclick = () => E('imgfile').click();
E('imgfile').onchange = async (e) => { await addFiles([...e.target.files]); e.target.value = ''; };
view.addEventListener('dragover', (e) => { if (e.dataTransfer.types.includes('Files')) { e.preventDefault(); view.classList.add('drop'); } });
view.addEventListener('dragleave', () => view.classList.remove('drop'));
view.addEventListener('drop', (e) => { e.preventDefault(); view.classList.remove('drop'); addFiles([...e.dataTransfer.files]); });
const b64 = (blob) => new Promise((ok, no) => {
  const r = new FileReader();
  r.onload = () => ok(String(r.result).split(',')[1] || '');
  r.onerror = no;
  r.readAsDataURL(blob);
});
async function addFiles(files) {
  for (const f of files) {
    problem(`Adding ${f.name}…`, true);
    try {
      if (/\.svg$/i.test(f.name) || f.type === 'image/svg+xml') {    // drawn here as a picture at 300 dpi
        const url = URL.createObjectURL(f), im = new Image();
        im.src = url;
        await im.decode();
        const k = 300 / 96, w = im.naturalWidth || 400, h = im.naturalHeight || 300;
        const cv = document.createElement('canvas');
        cv.width = Math.round(w * k);
        cv.height = Math.round(h * k);
        const g = cv.getContext('2d');
        g.fillStyle = 'white';
        g.fillRect(0, 0, cv.width, cv.height);
        g.drawImage(im, 0, 0, cv.width, cv.height);
        URL.revokeObjectURL(url);
        const blob = await new Promise((ok) => cv.toBlob(ok, 'image/png'));
        await ccmd({cmd: 'image', name: f.name, b64: await b64(blob), drawn: true});
      } else {
        await ccmd({cmd: 'image', name: f.name, b64: await b64(f)});
      }
    } catch (err) { problem(`Couldn’t read ${f.name}`); }
  }
}

load();
})();
