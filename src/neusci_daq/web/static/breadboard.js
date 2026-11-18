// The Breadboard simulator's page half (Simulators tab): the board drawn in SVG as on the Qt
// page (breadpage.py), its parts placed, moved and set here, and checked, solved and kept
// in Python (/breadboard/state, /breadboard/cmd; see web/breadboard.py). A probe given an
// input replaces that input's trace on Record while Record runs.
//
// Drag a part from the strip onto the board, or pick it and click two holes (one for a
// probe or a ground). Click a part to select it; drag it, or one of its ends, to move it;
// double-click a hand switch to flip it; hold a push button down. Delete removes the
// selected part, Esc cancels a placement.
'use strict';
const BreadboardUI = (() => {
  const H = {'X-Newt-Key': new URLSearchParams(location.hash.slice(1)).get('k') || ''};
  // the board's geometry, as breadlayout.py
  const PITCH = 20, X0 = 40, BOARD_H = 345;
  const ROW_Y = {'t+': 20, 't-': 40, a: 80, b: 100, c: 120, d: 140, e: 160, f: 200, g: 220, h: 240, i: 260, j: 280, 'b+': 300, 'b-': 320};
  const ROWS = ['t+', 't-', ...'abcde', ...'fghij', 'b+', 'b-'];
  const ONE_HOLE = ['probe', 'ground'];
  const LEGS = ['a', 'b', 'gate', 'wiper'];
  const TOOLS = [['vin', 'Input voltage', "The stimulator's output, or a fixed voltage: drag it onto the board, or click its + end, then its − end"],
                 ['resistor', 'Resistor', 'Drag it onto the board, or click its two ends'],
                 ['capacitor', 'Capacitor', 'Drag it onto the board, or click its two ends'],
                 ['switch', 'Switch', 'Flipped by hand, or by a gate voltage like a MOSFET (set it under Selected part): drag it onto the board, or click its two ends'],
                 ['wire', 'Wire', 'Joins two holes: drag it onto the board, or click its two ends'],
                 ['probe', 'Probe', 'A recording site: drag it onto a hole, or click one; then pick the input it records on'],
                 ['ground', 'Ground', 'Ties a hole to 0 V, like a wire to a − rail: drag it onto a hole, or click one']];
  const MORE = [['led', 'LED', 'lights when current flows forward'], ['diode', 'Diode', 'current flows one way only'], null,
                ['button', 'Push button', 'closed only while you hold it'], ['pot', 'Potentiometer', 'three legs: an adjustable divider'],
                ['inductor', 'Inductor', 'henries'], null, ['battery', 'Battery', 'a fixed voltage, + and − ends']];
  const PART_COLOR = {vin: '#7c4dff', resistor: '#b87a1e', capacitor: '#1e88a8', switch: '#c2185b', wire: '#2e7d32',
                      ground: '#5f6670', diode: '#455a64', button: '#c2185b', pot: '#b87a1e', inductor: '#6d4c41', battery: '#7c4dff'};
  const LED_RGB = {red: '#e53935', yellow: '#fdd835', green: '#43a047', blue: '#1e88e5', white: '#e0e0e0'};
  const PROBE_FREE = '#8a8f98';
  // the smallest and largest each number can be (as the Qt page's boxes)
  const RANGE = {V: [-10, 10], 'Ω': [1, 1e9], F: [1e-12, 1], H: [1e-9, 100], noise: [0, 1], vth: [0.01, 10], ron: [1e-3, 1e6],
                 vf: [0.05, 10], A: [1e-4, 1]};

  let S = null, root = null, svg = null, cols = 30, W = 660;
  let tool = 'select', sel = -1, pending = null, hover = null, mouse = [0, 0];
  let drag = null, held = -1, shown = null, dragKind = null, adding = false, on = true, timer = 0, chart = null;
  const $ = (id) => root.querySelector('#bb_' + id);
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  const minus = (s) => String(s).replace(/-/g, '−');
  const isDark = () => document.documentElement.dataset.theme === 'dark' ||
    (document.documentElement.dataset.theme !== 'light' && matchMedia('(prefers-color-scheme: dark)').matches);
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  // ------------------------------------------------------------ holes
  function parseHole(h) {
    for (const r of ['t+', 't-', 'b+', 'b-']) if (h.startsWith(r)) return [r, +h.slice(2)];
    return [h[0], +h.slice(1)];
  }
  const xy = (h) => { const [r, c] = parseHole(h); return [X0 + (c - 1) * PITCH, ROW_Y[r]]; };
  function nearestHole(x, y) {
    const col = Math.round((x - X0) / PITCH) + 1;
    if (col < 1 || col > cols || Math.abs(x - (X0 + (col - 1) * PITCH)) > 8) return null;
    let row = null, best = 1e9;
    for (const r in ROW_Y) { const d = Math.abs(ROW_Y[r] - y); if (d < best) { best = d; row = r; } }
    return best > 8 ? null : row + col;
  }
  function shifted(h, dr, dc) {
    const [row, col] = parseHole(h), r = ROWS.indexOf(row) + dr, c = col + dc;
    return r >= 0 && r < ROWS.length && c >= 1 && c <= cols ? ROWS[r] + c : null;
  }
  function holes(p) {                          // every hole the part has a leg in (Part.holes)
    const out = [p.a, p.b].filter(Boolean);
    if (p.kind === 'switch' && p.mode === 'mosfet' && p.gate) out.push(p.gate);
    if (p.kind === 'pot' && p.wiper) out.push(p.wiper);
    return out;
  }
  const legHoles = (p) => LEGS.filter((k) => p[k] && holes(p).includes(p[k]));

  // ------------------------------------------------------------ numbers with units
  const PREFIX = {p: 1e-12, n: 1e-9, u: 1e-6, 'µ': 1e-6, m: 1e-3, '': 1, k: 1e3, K: 1e3, M: 1e6, G: 1e9};
  function fmtSI(v, unit) {
    if (v === 0) return '0 ' + unit;
    for (const [f, p] of [[1e9, 'G'], [1e6, 'M'], [1e3, 'k'], [1, ''], [1e-3, 'm'], [1e-6, 'µ'], [1e-9, 'n'], [1e-12, 'p']]) {
      if (Math.abs(v) >= f * 0.999) return minus(+(v / f).toPrecision(4)) + ' ' + p + unit;
    }
    return minus(+v.toPrecision(3)) + ' ' + unit;
  }
  function parseSI(text) {                     // "10k", "4.7 µF", "1 mA", "−2 V" -> a number, or NaN
    const m = String(text).trim().replace('−', '-').replace(/ohms?$/i, '').match(/^([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*([pnuµmkKMG]?)\s*([A-Za-zΩ]*)$/);
    return m ? parseFloat(m[1]) * PREFIX[m[2]] : NaN;
  }

  // ------------------------------------------------------------ talking to Python
  async function post(d) {
    adding = d.cmd === 'add' || d.cmd === 'drop';
    try {
      const r = await fetch('/breadboard/cmd', {method: 'POST', headers: H, body: JSON.stringify(d)});
      return show(await r.json());
    } catch (e) { say('Newt is not answering.', true); return null; }
  }
  async function poll(full) {
    clearTimeout(timer);
    if (!on || document.hidden) return;
    try {
      const st = await (await fetch('/breadboard/state' + (full || !S ? '' : '?v=' + S.version), {headers: H})).json();
      if (st.parts) show(st);
      else liveState(st);
    } catch (e) { /* try again */ }
    timer = setTimeout(poll, S && S.running ? 200 : 1000);
  }
  function liveState(st) {                     // only what changes while recording
    const changed = JSON.stringify([st.on, st.lit, st.running]) !== JSON.stringify([S.on, S.lit, S.running]);
    Object.assign(S, st);
    if (changed) { drawParts(); showRun(); if (sel >= 0) showPart(); }
  }
  function show(st) {
    const grew = S && st.parts.length > S.parts.length;
    const first = !S;
    S = st;
    cols = st.cols;
    W = X0 + cols * PITCH + 20;
    if (grew && adding) sel = st.parts.length - 1;   // a new part: picked, to set it
    adding = false;
    if (sel >= st.parts.length) sel = -1;
    if (first) buildBoard();
    drawParts();
    showPart();
    showSites();
    showTiming();
    showRun();
    drawPreview();
    say(st.problem || st.message, !!st.problem);
    return st;
  }
  function say(text, bad) {
    const m = $('msg');
    m.textContent = text || '';
    m.className = 'bbmsg' + (bad ? ' bad' : '');
  }

  // ------------------------------------------------------------ drawing
  function probeColor(p) {
    const inp = S && S.inputs.find((c) => c.physical === p.input);
    return inp ? (isDark() ? inp.dark : inp.light) : PROBE_FREE;
  }
  const L = (x1, y1, x2, y2, extra = '') => `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}"${extra}/>`;
  const glowLine = (x1, y1, x2, y2) => L(x1, y1, x2, y2, ' stroke="#2e7d32" stroke-width="7" stroke-opacity=".3" stroke-linecap="round"');
  function zigzag(s0, s1, body) {
    let d = `M${s0} 0`;
    for (let k = 0; k < 6; k++) d += `L${s0 + body * (k + 0.5) / 6} ${k % 2 ? 5 : -5}`;
    return `<path d="${d}L${s1} 0"/>`;
  }

  function drawPart(p, i, selected, isOn, amps) {
    const out = [];
    if (p.kind === 'ground') {                 // the ground symbol hanging below its hole
      const [x, y] = xy(p.a), c = PART_COLOR.ground;
      if (selected) out.push(`<circle cx="${x}" cy="${y + 5}" r="11" fill="none" stroke="var(--accent)" stroke-width="2"/>`);
      out.push(`<g stroke="${c}" stroke-width="2" fill="${c}"><circle cx="${x}" cy="${y}" r="2.5"/>${L(x, y, x, y + 7)}` +
               [8, 5, 2].map((w, k) => L(x - w, y + 7 + 3 * k, x + w, y + 7 + 3 * k)).join('') + '</g>');
      return out.join('');
    }
    if (p.kind === 'probe') {
      const [x, y] = xy(p.a), c = probeColor(p);
      if (selected) out.push(`<circle cx="${x}" cy="${y}" r="11" fill="none" stroke="var(--accent)" stroke-width="2"/>`);
      out.push(`<circle cx="${x}" cy="${y}" r="7" fill="var(--surface)" stroke="${c}" stroke-width="2.2"/>`,
               `<circle cx="${x}" cy="${y}" r="2.5" fill="${c}"/>`,
               `<text x="${x + 9}" y="${y - 8}" fill="${c}" font-size="8">${esc(p.input || '—')}</text>`);
      return out.join('');
    }
    const [ax, ay] = xy(p.a), [bx, by] = xy(p.b);
    const color = p.kind === 'led' ? (LED_RGB[p.color] || '#e53935') : PART_COLOR[p.kind];
    if (selected) out.push(L(ax, ay, bx, by, ' stroke="var(--accent)" stroke-width="8" stroke-opacity=".25" stroke-linecap="round"'));
    out.push(`<circle cx="${ax}" cy="${ay}" r="3" fill="${color}"/><circle cx="${bx}" cy="${by}" r="3" fill="${color}"/>`);
    const len = Math.hypot(bx - ax, by - ay), body = p.kind !== 'wire' ? Math.min(34, len * 0.7) : 0;
    const ang = Math.atan2(by - ay, bx - ax);
    let gate = null;
    const leg = p.kind === 'switch' && p.mode === 'mosfet' && p.gate ? p.gate : p.kind === 'pot' && p.wiper ? p.wiper : '';
    if (leg) {                                 // the gate or wiper leg, in the part's own frame
      const [gx0, gy0] = xy(leg), gx = gx0 - ax, gy = gy0 - ay;
      out.push(`<circle cx="${gx0}" cy="${gy0}" r="3" fill="${color}"/>`);
      gate = [Math.cos(ang) * gx + Math.sin(ang) * gy, -Math.sin(ang) * gx + Math.cos(ang) * gy];
    }
    const g = [];
    const s0 = (len - body) / 2, s1 = (len + body) / 2, mid = (s0 + s1) / 2;
    if (p.kind === 'wire') g.push(L(0, 0, len, 0));
    else g.push(L(0, 0, s0, 0), L(s1, 0, len, 0));
    const plus = `<text x="${s0 - 10}" y="-6" fill="var(--muted)" stroke="none" font-size="8">+</text>`;
    switch (p.kind) {
      case 'resistor': g.push(zigzag(s0, s1, body)); break;
      case 'capacitor':
        g.push(L(s0, 0, mid - 3, 0), L(mid + 3, 0, s1, 0), `<g stroke-width="3">${L(mid - 3, -8, mid - 3, 8)}${L(mid + 3, -8, mid + 3, 8)}</g>`);
        break;
      case 'pot': {                            // a resistor with an arrow from the wiper
        g.push(zigzag(s0, s1, body));
        const tip = s0 + body * p.pos, side = gate && gate[1] < 0 ? -1 : 1;
        if (gate) g.push(L(gate[0], gate[1], tip, side * 7));
        g.push(`<polygon fill="${color}" points="${tip},${side * 5} ${tip - 3},${side * 10} ${tip + 3},${side * 10}"/>`);
        break;
      }
      case 'inductor': {                       // four loops
        const w = body / 4;
        let d = `M${s0} 0`;
        for (let k = 0; k < 4; k++) d += `A${w / 2} ${w / 2 + 1} 0 0 1 ${s0 + (k + 1) * w} 0`;
        g.push(`<path d="${d}"/>`);
        break;
      }
      case 'diode': case 'led': {              // a triangle pointing to the bar: anode at a
        const h = 6;
        if (p.kind === 'led' && amps > 0) {    // glow, brighter with more current
          const a = Math.min(0.85, 0.25 + 0.6 * amps / Math.max(p.value, 1e-9));
          g.push(`<defs><radialGradient id="bbglow${i}"><stop offset="0" stop-color="${color}" stop-opacity="${a}"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></radialGradient></defs>`,
                 `<circle cx="${mid}" cy="0" r="18" fill="url(#bbglow${i})" stroke="none"/>`);
        }
        g.push(L(s0, 0, mid - h, 0), L(mid + h, 0, s1, 0),
               `<polygon fill="${p.kind === 'diode' || amps > 0 ? color : 'var(--surface)'}" points="${mid - h},${-h} ${mid - h},${h} ${mid + h},0"/>`,
               L(mid + h, -h, mid + h, h, ' stroke-width="2.6"'));
        if (p.kind === 'led') {                // two little arrows: light out
          g.push(...[-2, 3].map((dx) => L(mid + dx, -h - 1, mid + dx + 4, -h - 6, ' stroke-width="1.2"')));
          if (amps > p.value) g.push(`<text x="${mid - 3}" y="${h + 12}" fill="var(--warn)" stroke="none" font-size="9" font-weight="700">!</text>`);
        }
        break;
      }
      case 'button': {                         // a bar lifted off two contacts, down while held
        const lift = isOn ? 0 : -6;
        g.push(`<circle cx="${s0}" cy="0" r="2.5" fill="${color}"/><circle cx="${s1}" cy="0" r="2.5" fill="${color}"/>`);
        if (isOn) g.push(glowLine(s0, 0, s1, 0));
        g.push(L(s0, lift, s1, lift), L(mid, lift, mid, lift - 6), L(mid - 4, lift - 6, mid + 4, lift - 6));
        break;
      }
      case 'switch':
        if (!gate) {                           // a hand switch: a lever, down when closed
          g.push(`<circle cx="${s0}" cy="0" r="2.5" fill="${color}"/><circle cx="${s1}" cy="0" r="2.5" fill="${color}"/>`);
          if (isOn) g.push(glowLine(s0, 0, s1, 0));
          g.push(isOn ? L(s0, 0, s1, 0) : L(s0, 0, s1 - 2, -body * 0.4));
        } else {                               // a MOSFET: drain at a, source at b, gate to one side
          const side = gate[1] >= 0 ? 1 : -1, ch = side * 6, pl = side * 11;
          g.push(L(s0, 0, s0, ch), L(s1, 0, s1, ch), L(mid, 0, mid, ch), L(mid, 0, s1, 0));
          if (isOn) g.push(glowLine(s0, ch, s1, ch), L(s0, ch, s1, ch, ' stroke-width="3"'));
          else {                               // broken channel: off
            const w = (s1 - s0) / 3;
            for (let k = 0; k < 3; k++) g.push(L(s0 + k * w + 1.5, ch, s0 + (k + 1) * w - 1.5, ch, ' stroke-width="3"'));
          }
          g.push(L(s0 + 2, pl, s1 - 2, pl), L(mid, pl, gate[0], gate[1]));
          const [tip, base] = p.channel === 'n' ? [side * 5, side] : [side, side * 5];   // n points into the channel
          g.push(`<polygon fill="${color}" points="${mid},${tip} ${mid - 3},${base} ${mid + 3},${base}"/>`);
        }
        break;
      case 'battery':                          // long plate (+) at a, short plate (−) toward b
        g.push(L(s0, 0, mid - 2, 0), L(mid + 3, 0, s1, 0), L(mid - 2, -8, mid - 2, 8), L(mid + 3, -4, mid + 3, 4, ' stroke-width="3.5"'), plus);
        break;
      case 'vin': {
        const r = Math.min(10, body / 2);
        g.push(L(s0, 0, mid - r, 0), L(mid + r, 0, s1, 0), `<circle cx="${mid}" cy="0" r="${r}" fill="var(--surface)"/>`);
        if (p.source === 'stim') {             // a square wave: the stimulator
          const w = r * 0.55;
          g.push(`<polyline points="${mid - w},3 ${mid - w / 3},3 ${mid - w / 3},-3 ${mid + w / 3},-3 ${mid + w / 3},3 ${mid + w},3"/>`);
        } else g.push(L(mid - 2, -5, mid - 2, 5), L(mid + 2, -3, mid + 2, 3));
        g.push(plus);
        break;
      }
    }
    out.push(`<g transform="translate(${ax} ${ay}) rotate(${ang * 180 / Math.PI})" stroke="${color}" stroke-width="${p.kind === 'wire' ? 2.6 : 2.2}" fill="none" stroke-linejoin="round">${g.join('')}</g>`);
    return out.join('');
  }

  function buildBoard() {
    const box = $('board');
    const st = [`<rect x="4" y="4" width="${W - 8}" height="${BOARD_H - 8}" rx="10" fill="var(--bb)" stroke="var(--line)"/>`,
                `<rect x="12" y="172" width="${W - 24}" height="16" fill="var(--chan)"/>`];
    for (const [y, c] of [[14, '#d64545'], [46, '#2f6fd6'], [294, '#d64545'], [326, '#2f6fd6']]) {
      st.push(L(X0 - 12, y, X0 + (cols - 1) * PITCH + 12, y, ` stroke="${c}" stroke-width="1.2"`));
    }
    const t = (x, y, s) => `<text x="${x}" y="${y}">${s}</text>`;
    st.push('<g fill="var(--muted)" font-size="8">', t(14, 24, '+'), t(14, 44, '−'), t(14, 304, '+'), t(14, 324, '−'),
            ...[...'abcdefghij'].map((r) => t(18, ROW_Y[r] + 3, r)));
    for (let c = 1; c <= cols; c += 5) st.push(t(X0 + (c - 1) * PITCH - 3, 62, c));
    st.push('</g><g fill="var(--hole)">');
    for (const r in ROW_Y) for (let c = 0; c < cols; c++) st.push(`<rect x="${X0 + c * PITCH - 2.2}" y="${ROW_Y[r] - 2.2}" width="4.4" height="4.4"/>`);
    st.push('</g>');
    box.innerHTML = `<svg viewBox="0 0 ${W} ${BOARD_H}" tabindex="0" aria-label="The breadboard">${st.join('')}<g id="bb_parts"></g><g id="bb_over"></g></svg>`;
    svg = box.querySelector('svg');
    wireBoard();
  }

  function partOn(p, i) {
    if (p.kind === 'button' || (p.kind === 'switch' && p.mode !== 'mosfet')) return !!p.closed;
    return !!(S.on || {})[i];
  }
  function drawParts() {
    const parts = shown || S.parts;
    $('parts').innerHTML = parts.map((p, i) => drawPart(p, i, i === sel, partOn(p, i), (S.lit || {})[i] || 0)).join('');
    drawOver();
  }
  function drawOver() {
    const o = [];
    if (pending) {                             // a part being placed: from its first hole to the mouse
      const [x, y] = xy(pending), [x2, y2] = hover ? xy(hover) : mouse;
      o.push(L(x, y, x2, y2, ' stroke="var(--accent)" stroke-width="2" stroke-dasharray="5 4"'),
             `<circle cx="${x}" cy="${y}" r="4" fill="var(--accent)"/>`);
    }
    if (hover && (tool !== 'select' || dragKind)) {
      const [x, y] = xy(hover);
      o.push(`<circle cx="${x}" cy="${y}" r="6" fill="none" stroke="var(--accent)" stroke-width="1.5"/>`);
    }
    $('over').innerHTML = o.join('');
  }

  // ------------------------------------------------------------ the board's mouse
  function boardXY(e) {
    const pt = svg.createSVGPoint();
    pt.x = e.clientX; pt.y = e.clientY;
    const q = pt.matrixTransform(svg.getScreenCTM().inverse());
    return [q.x, q.y];
  }
  function segDist([px, py], [ax, ay], [bx, by]) {
    const dx = bx - ax, dy = by - ay;
    const t = dx === 0 && dy === 0 ? 0 : Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)));
    return Math.hypot(px - ax - t * dx, py - ay - t * dy);
  }
  function dist(p, pt) {
    const a = xy(p.a);
    if (ONE_HOLE.includes(p.kind)) return Math.hypot(pt[0] - a[0], pt[1] - a[1]);
    const b = xy(p.b);
    let d = segDist(pt, a, b);
    for (const leg of [p.gate, p.wiper]) {
      if (leg && holes(p).includes(leg)) d = Math.min(d, segDist(pt, [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2], xy(leg)));
    }
    return d;
  }
  function partAt(pt) {
    let best = 9, idx = -1;
    S.parts.forEach((p, i) => { const d = dist(p, pt); if (d < best) { best = d; idx = i; } });
    return idx;
  }
  const nearLeg = (p, pt) => LEGS.find((k) => p[k] && holes(p).includes(p[k]) && !ONE_HOLE.includes(p.kind) &&
                                            Math.hypot(pt[0] - xy(p[k])[0], pt[1] - xy(p[k])[1]) < 6);
  const onBody = (p, pt) => holes(p).every((h) => Math.hypot(pt[0] - xy(h)[0], pt[1] - xy(h)[1]) >= 6);

  function wireBoard() {
    svg.addEventListener('pointerdown', (e) => {
      if (e.button !== 0) return;
      svg.focus({preventScroll: true});
      const pt = boardXY(e);
      if (tool === 'select') {
        const i = partAt(pt);
        select(i);
        if (i < 0) return;
        const p = S.parts[i];
        if (p.kind === 'button' && onBody(p, pt)) {        // press it: closed while held
          held = i;
          try { svg.setPointerCapture(e.pointerId); } catch (err) { /* a synthetic press */ }
          post({cmd: 'edit', i, set: {closed: true}});
          return;
        }
        const end = nearLeg(p, pt);                        // an end moves that end, the body the whole part
        drag = {i, end, from: end ? p[end] : (nearestHole(...pt) || p.a), moved: false};
        try { svg.setPointerCapture(e.pointerId); } catch (err) { /* a synthetic press */ }
        svg.style.cursor = 'grabbing';
        return;
      }
      const hole = nearestHole(...pt);
      if (!hole) return;
      if (ONE_HOLE.includes(tool)) post({cmd: 'add', kind: tool, a: hole});
      else if (!pending) pending = hole;
      else if (hole !== pending) { post({cmd: 'add', kind: tool, a: pending, b: hole}); pending = null; }
      drawOver();
    });
    svg.addEventListener('pointermove', (e) => {
      mouse = boardXY(e);
      hover = nearestHole(...mouse);
      if (drag && hover) {
        const [r0, c0] = parseHole(drag.from), [r1, c1] = parseHole(hover);
        const dr = ROWS.indexOf(r1) - ROWS.indexOf(r0), dc = c1 - c0;
        const p = S.parts[drag.i], legs = drag.end ? [drag.end] : legHoles(p);
        const moved = Object.fromEntries(legs.map((k) => [k, shifted(p[k], dr, dc)]));
        if (Object.values(moved).every(Boolean)) {
          const q = {...p, ...moved}, hs = holes(q);
          if (new Set(hs).size === hs.length) {            // shown while dragging; sent on release
            shown = S.parts.map((x, k) => (k === drag.i ? q : x));
            drag.legs = moved;
            drag.moved = dr !== 0 || dc !== 0;
            drawParts();
            return;
          }
        }
      }
      if (tool !== 'select' || pending) drawOver();
    });
    const up = () => {
      if (held >= 0) {                                     // a push button springs open
        const i = held;
        held = -1;
        post({cmd: 'edit', i, set: {closed: false}});
      }
      const d = drag;
      drag = null;
      svg.style.cursor = '';
      if (d && d.moved) post({cmd: 'edit', i: d.i, set: d.legs}).then(() => { shown = null; drawParts(); });
      else if (d) { shown = null; drawParts(); }
    };
    svg.addEventListener('pointerup', up);
    svg.addEventListener('pointercancel', up);
    svg.addEventListener('pointerleave', () => { if (!drag) { hover = null; drawOver(); } });
    svg.addEventListener('dblclick', (e) => {
      if (tool !== 'select') return;
      const i = partAt(boardXY(e)), p = S.parts[i];
      if (p && p.kind === 'switch' && p.mode !== 'mosfet') post({cmd: 'edit', i, set: {closed: !p.closed}});
    });
    // dragged in from the parts strip
    svg.addEventListener('dragover', (e) => {
      if (!dragKind) return;
      hover = nearestHole(...boardXY(e));
      if (hover) e.preventDefault();
      drawOver();
    });
    svg.addEventListener('dragleave', () => { hover = null; drawOver(); });
    svg.addEventListener('drop', (e) => {
      e.preventDefault();
      const hole = nearestHole(...boardXY(e)), kind = dragKind;
      dragKind = null;
      hover = null;
      if (!hole || !kind) return drawOver();
      setTool('select');                                   // back to Select, so the new part can be moved
      post({cmd: 'drop', kind, hole});
    });
  }

  function select(i) {
    sel = i;
    drawParts();
    showPart();
  }
  function setTool(t) {
    tool = t;
    pending = null;
    for (const b of root.querySelectorAll('[data-tool]')) b.setAttribute('aria-pressed', String(b.dataset.tool === t));
    const moreKind = MORE.find((m) => m && m[0] === t);
    const mb = $('more');
    mb.setAttribute('aria-pressed', String(!!moreKind));
    mb.firstChild.textContent = moreKind ? `More parts: ${moreKind[1]} ` : 'More parts ';
    svg.style.cursor = t === 'select' ? '' : 'crosshair';
    showKey();
    drawOver();
  }

  // ------------------------------------------------------------ the side panel
  function keyName(p) {
    if (p.kind === 'vin' && p.source === 'dc') return 'vin:dc';
    if (p.kind === 'switch' && p.mode === 'mosfet') return p.channel === 'p' ? 'switch:mosfet:p' : 'switch:mosfet';
    return p.kind;
  }
  function showKey() {
    const part = S.parts[sel], placing = !part && tool !== 'select';
    const box = $('key');
    box.hidden = !part && !placing;
    if (box.hidden) return;
    const kind = part ? part.kind : tool, k = S.keys[part ? keyName(part) : kind];
    $('keytitle').textContent = `Key: ${S.names[kind]}` + (placing ? ' (placing)' : '');
    // the part drawn large, the way it looks on the board, with each leg tagged and named
    const demo = {...(part || {kind, value: 1, pos: 0.5, color: 'red', mode: 'toggle', channel: 'n', source: 'stim'})};
    Object.assign(demo, ONE_HOLE.includes(kind) ? {a: 'c5', b: ''} : {a: 'c3', b: 'c8'});
    if (kind === 'pot') demo.wiper = 'e6';
    if (demo.gate || (kind === 'switch' && demo.mode === 'mosfet')) demo.gate = 'e6';
    if (kind === 'probe') demo.input = demo.input || '';
    const legs = k.legs.filter(([leg]) => demo[leg]);
    const pts = legs.map(([leg]) => xy(demo[leg]));
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    const x0 = Math.min(...xs) - 34, x1 = Math.max(...xs) + 34, y0 = Math.min(...ys) - 24, y1 = Math.max(...ys) + 26;
    const tags = legs.map(([leg, tag, name], n) => {
      const [x, y] = pts[n], below = leg === 'a' || leg === 'b';
      let tx = x, ty = y + 13, nm = `<text x="${x}" y="${y - 12}" text-anchor="middle">${esc(name)}</text>`;
      if (ONE_HOLE.includes(kind)) { tx = x - 17; ty = y; nm = `<text x="${x + 13}" y="${y + 3}">${esc(name)}</text>`; }
      else if (!below) { tx = x + 12; ty = y + 3; nm = `<text x="${tx + 8}" y="${ty + 3}">${esc(name)}</text>`; }
      return `<circle cx="${tx}" cy="${ty}" r="5.5" fill="var(--ink)"/><text x="${tx}" y="${ty + 2.6}" text-anchor="middle" fill="var(--surface)" font-weight="700" font-size="7">${esc(tag)}</text>` +
             `<g fill="var(--muted)" font-size="7">${nm}</g>`;
    }).join('');
    const amps = kind === 'led' ? demo.value * 0.6 : 0;
    $('keydraw').innerHTML = `<svg viewBox="${x0} ${y0} ${x1 - x0} ${y1 - y0}">${drawPart(demo, 'k', false, kind === 'switch' || kind === 'button' ? !!demo.closed : false, amps)}${tags}</svg>`;
    $('keylegs').innerHTML = '<table>' + k.legs.map(([, tag, name, what]) =>
      `<tr><td>${esc(tag)}</td><td><b>${esc(name[0].toUpperCase() + name.slice(1))}</b>: ${esc(what)}</td></tr>`).join('') + '</table>';
    $('keywhat').innerHTML = k.text;                       // from Python: may hold <sub>
  }

  const ends = (p) => {
    if (ONE_HOLE.includes(p.kind)) return p.a;
    if (p.kind === 'switch' && p.mode === 'mosfet' && p.gate) return `drain ${p.a}, source ${p.b}, gate ${p.gate}`;
    if (p.kind === 'pot') return `ends ${p.a} and ${p.b}, wiper ${p.wiper}`;
    if (p.kind === 'diode' || p.kind === 'led') return `+ ${p.a}, − ${p.b}`;
    return `${p.a} to ${p.b}`;
  };
  function showPart() {
    const p = S.parts[sel], form = $('form'), info = $('info');
    $('del').disabled = !p;
    showKey();
    if (!p) {
      $('pname').textContent = 'Click a part to select it, or pick a part above to place one.';
      form.innerHTML = '';
      info.innerHTML = '';
      return;
    }
    if (form.contains(document.activeElement) && form.dataset.for === `${sel}:${S.version}`) return;   // being typed in
    form.dataset.for = `${sel}:${S.version}`;
    $('pname').innerHTML = `<b>${esc(S.names[p.kind])}</b> (${esc(ends(p))})`;
    const rows = [];
    const num = (label, field, unit, range, value = p[field]) =>
      rows.push(`<label>${label}</label><input type="text" class="num" data-f="${field}" data-u="${unit}" data-r="${range}" value="${esc(fmtSI(value, unit))}">`);
    const pick = (label, field, opts) =>
      rows.push(`<label>${label}</label><select data-f="${field}">` +
                opts.map(([v, t]) => `<option value="${esc(v)}"${String(p[field]) === String(v) ? ' selected' : ''}>${esc(t)}</option>`).join('') + '</select>');
    let text = '';
    switch (p.kind) {
      case 'vin':
        pick('Source', 'source', [['stim', 'Stimulator output'], ['dc', 'Fixed voltage']]);
        if (p.source === 'dc') num('Voltage', 'value', 'V', 'V');
        text = '+ end at ' + p.a;
        break;
      case 'resistor': num('Resistance', 'value', 'Ω', 'Ω'); break;
      case 'capacitor': num('Capacitance', 'value', 'F', 'F'); break;
      case 'probe': {
        const opts = [['', 'None (not recorded)'], ...S.inputs.map((c) =>
          [c.physical, `${c.physical} · ${c.name}` + (c.scale !== 1 ? ` (×${+c.scale.toPrecision(3)} ${c.units})` : ` (${c.units})`)])];
        if (p.input && !S.inputs.some((c) => c.physical === p.input)) opts.push([p.input, `${p.input} (not an input now)`]);
        pick('Records on', 'input', opts);
        num('Noise', 'noise_v', 'V', 'noise');
        const inp = S.inputs.find((c) => c.physical === p.input);
        if (inp && inp.scale !== 1) {
          text = `Record shows ${esc(inp.physical)} as ${esc(inp.name)}, scaled: 1 V here reads ${+inp.scale.toPrecision(3)} ${esc(inp.units)} there.`;
        } else if (inp) text = `Record shows this point on ${esc(inp.physical)} (${esc(inp.name)}), in ${esc(inp.units)}.`;
        else text = 'Pick an input, and Record shows the voltage here while it runs.';
        break;
      }
      case 'switch':
        pick('Flipped by', 'mode', [['toggle', 'Hand (toggle)'], ['mosfet', 'Gate voltage (MOSFET)']]);
        if (p.mode !== 'mosfet') {
          rows.push(`<label>State</label><div><button type="button" data-flip>${p.closed ? 'Open it' : 'Close it'}</button></div>`);
          text = `<b>${p.closed ? 'Closed' : 'Open'}</b>. Double-click it on the board to flip it.`;
        } else {
          pick('Type', 'channel', [['n', 'N-channel'], ['p', 'P-channel']]);
          num('Threshold V<sub>th</sub>', 'vth', 'V', 'vth');
          text = `Closes while the gate is more than V<sub>th</sub> ${p.channel === 'n' ? 'above' : 'below'} the source; open, it connects nothing. ` +
                 'No current flows into the gate. The preview\'s 1 V pulse may not reach V<sub>th</sub>.' +
                 (S.live ? ` Now <b>${partOn(p, sel) ? 'on' : 'off'}</b>.` : ` At rest it's <b>${partOn(p, sel) ? 'on' : 'off'}</b>.`);
        }
        num('R<sub>on</sub>', 'value', 'Ω', 'ron');
        break;
      case 'wire': text = 'Joins the two strips it connects.'; break;
      case 'ground': text = "Ties this hole's strip to 0 V, like a wire to a − rail."; break;
      case 'battery': num('Voltage', 'value', 'V', 'V'); text = '+ end at ' + p.a; break;
      case 'inductor': num('Inductance', 'value', 'H', 'H'); break;
      case 'pot':
        num('Resistance', 'value', 'Ω', 'Ω');
        rows.push(`<label>Wiper</label><div class="wiper"><input type="range" min="0" max="100" step="1" data-f="pos" value="${Math.round(p.pos * 100)}"><output>${Math.round(p.pos * 100)}%</output></div>`);
        text = `${Math.round(p.pos * 100)}% of the way from ${p.a}: ${fmtSI(p.value * p.pos, 'Ω')} to the wiper, ${fmtSI(p.value * (1 - p.pos), 'Ω')} on to ${p.b}.`;
        break;
      case 'button':
        num('R<sub>on</sub>', 'value', 'Ω', 'ron');
        text = 'Closed only while you hold the mouse down on it; it opens when you let go.';
        break;
      case 'led': case 'diode':
        if (p.kind === 'led') pick('Colour', 'color', Object.keys(S.led_vf).map((c) => [c, c[0].toUpperCase() + c.slice(1)]));
        num('Forward V', 'vf', 'V', 'vf');
        if (p.kind === 'led') {
          num('Max current', 'value', 'A', 'A');
          const amps = (S.lit || {})[sel] || 0;
          const state = amps <= 0 ? '<b>Off</b>: no current flows forward.'
            : amps > p.value ? `<span class="warn">⚠ <b>${fmtSI(amps, 'A')}</b>, over its maximum: add resistance in series.</span>`
            : `<b>Lit</b>, ${fmtSI(amps, 'A')}.`;
          text = state + (S.live ? ' Live, while Record runs.' : ' At rest, with the stimulator at 0 V; live while Record runs.');
        } else text = 'Current flows from + to − only, once + is more than the forward voltage above −.';
        break;
    }
    form.innerHTML = rows.join('');
    info.innerHTML = text;
  }

  function wirePanel() {
    const form = $('form');
    form.addEventListener('change', (e) => {
      const el = e.target, f = el.dataset.f, i = sel;
      if (!f || !S.parts[i]) return;
      if (el.classList.contains('num')) {
        let v = parseSI(el.value);
        if (!isFinite(v)) { el.classList.add('bad'); say(`Type a number, like 10k or 4.7 µ.`, true); return; }
        const [lo, hi] = RANGE[el.dataset.r];
        v = Math.min(hi, Math.max(lo, v));
        el.classList.remove('bad');
        post({cmd: 'edit', i, set: {[f]: v}});
      } else if (f === 'pos') post({cmd: 'edit', i, set: {pos: +el.value / 100}});
      else post({cmd: 'edit', i, set: {[f]: el.value}});
    });
    form.addEventListener('input', (e) => {                // the wiper's number follows the slider
      if (e.target.dataset.f === 'pos') e.target.nextElementSibling.textContent = e.target.value + '%';
    });
    form.addEventListener('keydown', (e) => { if (e.key === 'Enter' && e.target.classList.contains('num')) e.target.blur(); });
    form.addEventListener('click', (e) => {
      if (e.target.dataset.flip !== undefined && S.parts[sel]) post({cmd: 'edit', i: sel, set: {closed: !S.parts[sel].closed}});
    });
  }

  function showSites() {
    const probes = S.parts.map((p, i) => [p, i]).filter(([p]) => p.kind === 'probe');
    $('sites').innerHTML = probes.length ? probes.map(([p, i]) => {
      const inp = S.inputs.find((c) => c.physical === p.input);
      const where = p.input ? `records on <b>${esc(p.input)}</b>` + (inp ? ` (${esc(inp.name)} on Record)` : ' (not an input now)')
        : 'not recorded (pick an input)';
      return `<li><span class="dot" style="background:${probeColor(p)}"></span><span><a href="#" data-sel="${i}">${esc(p.a)}</a>: ${where}</span></li>`;
    }).join('') : '<li>No probes yet: pick Probe and click a hole.</li>';
  }

  function showTiming() {
    const t = S.timing || {lines: [], warnings: []};
    $('timing').innerHTML = t.lines.map((l) => `<p>${l}</p>`).join('') +
      t.warnings.map((w) => `<p class="warn">⚠ ${esc(w)}</p>`).join('');
    $('timingbox').hidden = !t.lines.length;
  }

  function showRun() {
    const b = $('run');
    b.textContent = S.running ? 'Stop' : 'Run';
    b.className = S.running ? '' : 'primary';
    $('stim').disabled = !S.running;
    const recorded = S.parts.some((p) => p.kind === 'probe' && p.input);
    $('runnote').textContent = S.running
      ? (recorded ? 'Running: the probes are on Record, and LEDs and switches here show what the circuit is doing now.'
                  : 'Running, but no probe has an input yet: give one an input to see it on Record.')
      : 'Stopped. LEDs and switches show the circuit at rest, with the stimulator at 0 V.';
  }

  // ------------------------------------------------------------ the preview
  function drawPreview() {
    const box = $('chart'), pv = S.preview;
    const probes = S.parts.filter((p) => p.kind === 'probe');
    if (!pv || !window.uPlot) { box.innerHTML = ''; chart = null; $('legend').innerHTML = ''; return; }
    const series = [{}], data = [pv.t], legend = [];
    if (pv.stim) {
      series.push({stroke: css('--muted'), width: 1, dash: [5, 4], points: {show: false}});
      data.push(pv.stim);
      legend.push(`<span><i class="dash" style="border-color:${css('--muted')}"></i>stimulator: a 1 V, 100 ms pulse</span>`);
    }
    probes.forEach((p, k) => {
      const c = probeColor(p);
      series.push({stroke: c, width: 2, points: {show: false}});
      data.push(pv.traces[k]);
      legend.push(`<span><i style="border-color:${c}"></i>probe ${esc(p.a)}${p.input ? ' → ' + esc(p.input) : ''}</span>`);
    });
    $('legend').innerHTML = legend.join('') || '<span>Add a probe to see what it would record.</span>';
    const muted = css('--muted'), line = css('--line'), font = '11.5px "IBM Plex Mono", ui-monospace, Menlo, monospace';
    const grid = {stroke: line, width: 1}, ticks = {stroke: line, width: 1, size: 4};
    box.innerHTML = '';
    chart = new uPlot({
      width: Math.max(300, box.clientWidth), height: 210, padding: [8, 12, 0, 0],
      legend: {show: false}, cursor: {show: false},
      scales: {x: {time: false}},
      axes: [{stroke: muted, grid, ticks, font, size: 32, values: (u, v) => v.map((x) => minus(+(x * 1000).toPrecision(3)) + ' ms')},
             {stroke: muted, grid, ticks, font, size: 58, values: (u, v) => v.map((y) => minus(+y.toPrecision(3)) + ' V')}],
      series,
    }, data, box);
  }

  // ------------------------------------------------------------ files
  async function openPanel() {
    const panel = $('openp');
    if (!panel.hidden) { panel.hidden = true; return; }
    $('savep').hidden = true;
    panel.hidden = false;
    const st = await post({cmd: 'files'});
    const files = (st && st.files) || [];
    $('files').innerHTML = files.length ? files.map((f) => `<button type="button" data-open="${esc(f.name)}">${esc(f.name)}</button>`).join('')
      : `<span class="caption">No boards saved in the data folder yet.</span>`;
  }
  async function save(replace) {
    const name = $('savename').value.trim();
    const st = await post({cmd: 'save', name, replace: !!replace});
    if (st && st.exists) {
      if (confirm(`${st.exists} is already in the data folder. Replace it?`)) return save(true);
      return;
    }
    if (st && !st.problem) $('savep').hidden = true;
  }
  function wireFiles() {
    $('save').onclick = () => {
      if (S.dialog) { post({cmd: 'save', dialog: true}); return; }
      $('openp').hidden = true;
      $('savep').hidden = !$('savep').hidden;
      if (!$('savep').hidden) { $('savename').focus(); $('savename').select(); }
    };
    $('savego').onclick = () => save(false);
    $('savename').onkeydown = (e) => { if (e.key === 'Enter') save(false); if (e.key === 'Escape') $('savep').hidden = true; };
    $('saveno').onclick = () => { $('savep').hidden = true; };
    $('open').onclick = openPanel;
    $('openno').onclick = () => { $('openp').hidden = true; };
    $('files').onclick = async (e) => {
      const name = e.target.dataset.open;
      if (!name) return;
      const st = await post({cmd: 'open', name});
      if (st && !st.problem) { sel = -1; $('openp').hidden = true; show(st); }
    };
    $('pickfile').onchange = async (e) => {
      const f = e.target.files[0];
      e.target.value = '';
      if (!f) return;
      const st = await post({cmd: 'open', name: f.name, text: await f.text()});
      if (st && !st.problem) { sel = -1; $('openp').hidden = true; show(st); }
    };
  }

  // ------------------------------------------------------------ the page
  function partIcon(kind) {                    // the part as it looks on the board, small
    const one = ONE_HOLE.includes(kind);
    const p = {kind, a: one ? 'c2' : 'c1', b: one ? '' : 'c4', value: 1, pos: 0.5, color: 'red', mode: 'toggle', channel: 'n',
               source: 'stim', closed: false, input: '', gate: '', wiper: ''};
    const box = kind === 'ground' ? '50 112 40 24' : kind === 'probe' ? '48 110 44 20' : '36 108 68 24';
    const saved = S;
    S = S || {inputs: []};
    const art = drawPart(p, 'i' + kind, false, false, 0).replace(/<text[^>]*>[^<]*<\/text>/g, '');
    S = saved;
    return `<svg viewBox="${box}" aria-hidden="true">${art}</svg>`;
  }
  function build(el) {
    root = el;
    el.innerHTML = `
<div class="bbgrid">
  <div class="stage">
    <p class="caption">Drag a part onto the board, or pick it and click two holes (one for a probe or a ground); drag a placed part, or one of its ends, to move it. In each column, holes a–e are joined, and so are f–j; the − rails are ground. Give a probe an input and Record shows that point of the circuit. An input voltage from the stimulator plays whatever the Stimulator sends.</p>
    <div class="bbtools" id="bb_tools">
      <button type="button" data-tool="select" aria-pressed="true" title="Click a part to select it and change its value; drag it, or one of its ends, to move it">Select</button>
      ${TOOLS.map(([k, label, tip]) => `<button type="button" data-tool="${k}" draggable="true" title="${esc(tip)}">${partIcon(k)}${label}</button>`).join('')}
      <span class="bbmore"><button type="button" id="bb_more" aria-pressed="false" aria-haspopup="true" title="Less-used parts: pick one, then click its holes on the board">More parts <span aria-hidden="true">▾</span></button>
        <div class="bbmenu" id="bb_menu" hidden>${MORE.map((m) => m ? `<button type="button" data-tool="${m[0]}" draggable="true">${partIcon(m[0])}${m[1]} <span>— ${m[2]}</span></button>` : '<hr>').join('')}</div></span>
    </div>
    <div class="bbacts">
      <button type="button" id="bb_save" title="Save this circuit to a file, to open again later or share">Save board…</button>
      <button type="button" id="bb_open" title="Replace the board with a circuit saved before">Open board…</button>
      <span class="spacer"></span>
      <button type="button" id="bb_del" title="Remove the selected part (Delete key)" disabled>Delete part</button>
      <button type="button" id="bb_reset" title="Back to the example: the stimulator through 10 kΩ into 1 µF">Reset board</button>
      <button type="button" id="bb_clear">Clear board</button>
    </div>
    <div class="bbfile" id="bb_savep" hidden>
      <label for="bb_savename">Save as</label><input type="text" id="bb_savename" value="circuit"><span class="caption">.board.toml, in the data folder</span>
      <button type="button" class="primary" id="bb_savego">Save</button><button type="button" class="link" id="bb_saveno">Cancel</button>
    </div>
    <div class="bbfile" id="bb_openp" hidden>
      <span>Boards in the data folder:</span><span class="spacer"></span>
      <label class="caption"><button type="button" onclick="this.nextElementSibling.click()">From a file…</button><input type="file" id="bb_pickfile" accept=".toml" hidden></label>
      <button type="button" class="link" id="bb_openno">Cancel</button>
      <div class="bbfiles" id="bb_files"></div>
    </div>
    <div class="card bbboard" id="bb_board"></div>
    <p class="bbmsg" id="bb_msg" role="status"></p>
    <div class="card">
      <p class="sect-title">Preview: what the probes would record</p>
      <div class="bblegend" id="bb_legend"></div>
      <div id="bb_chart"></div>
    </div>
  </div>
  <div class="panel">
    <div class="card">
      <p class="sect-title">Selected part</p>
      <p class="bbname" id="bb_pname"></p>
      <div class="bbform" id="bb_form"></div>
      <p class="bbinfo" id="bb_info"></p>
    </div>
    <div class="card bbkey" id="bb_key" hidden>
      <p class="sect-title" id="bb_keytitle">Key</p>
      <div id="bb_keydraw"></div>
      <div id="bb_keylegs"></div>
      <p class="caption" id="bb_keywhat"></p>
    </div>
    <div class="card">
      <p class="sect-title">Run the circuit</p>
      <div class="bbrun"><button type="button" class="primary" id="bb_run">Run</button>
        <button type="button" id="bb_stim" title="Send the Stimulator's pulse (as set on the Stimulator tab)">Stimulate</button>
        <a id="bb_rec">Watch it on Record →</a></div>
      <p class="caption" id="bb_runnote"></p>
    </div>
    <div class="card">
      <p class="sect-title">Recording sites</p>
      <ul class="bbsites" id="bb_sites"></ul>
    </div>
    <div class="card" id="bb_timingbox">
      <p class="sect-title">Timing</p>
      <div class="bbtiming" id="bb_timing"></div>
    </div>
  </div>
</div>`;
    $('rec').href = 'live.html#k=' + encodeURIComponent(H['X-Newt-Key']);
    const tools = $('tools'), menu = $('menu');
    tools.addEventListener('click', (e) => {
      const b = e.target.closest('button');
      if (!b) return;
      if (b.id === 'bb_more') { menu.hidden = !menu.hidden; return; }
      if (b.dataset.tool) { setTool(b.dataset.tool); menu.hidden = true; }
    });
    tools.addEventListener('dragstart', (e) => {
      const b = e.target.closest('[data-tool]');
      if (!b || b.dataset.tool === 'select') return;
      dragKind = b.dataset.tool;
      e.dataTransfer.setData('text/plain', dragKind);
      e.dataTransfer.effectAllowed = 'copy';
      menu.hidden = true;
    });
    tools.addEventListener('dragend', () => { dragKind = null; hover = null; if (svg) drawOver(); });
    document.addEventListener('click', (e) => { if (!e.target.closest('.bbmore')) menu.hidden = true; });
    $('del').onclick = () => { if (S.parts[sel]) { post({cmd: 'delete', i: sel}); sel = -1; } };
    $('reset').onclick = () => { sel = -1; post({cmd: 'reset'}); };
    $('clear').onclick = () => { sel = -1; post({cmd: 'clear'}); };
    $('run').onclick = async () => {
      await fetch('/live/cmd', {method: 'POST', headers: H, body: JSON.stringify({cmd: 'run', on: !S.running})});
      poll(true);
    };
    $('stim').onclick = async () => {
      const st = await (await fetch('/stim/cmd', {method: 'POST', headers: H, body: JSON.stringify({cmd: 'fire'})})).json();
      if (st.problem) say(st.problem, true);
    };
    $('sites').onclick = (e) => { const i = e.target.dataset.sel; if (i !== undefined) { e.preventDefault(); setTool('select'); select(+i); } };
    wirePanel();
    wireFiles();
    document.addEventListener('keydown', (e) => {
      if (!on || el.hidden || e.target.closest('input,select,textarea')) return;
      if (e.key === 'Escape') { pending = null; menu.hidden = true; if (svg) drawOver(); }
      else if ((e.key === 'Delete' || e.key === 'Backspace') && S && S.parts[sel]) {
        e.preventDefault();
        post({cmd: 'delete', i: sel});
        sel = -1;
      }
    });
    document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(true); });
    matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => S && show(S));
    let w = 0;
    new ResizeObserver(() => {
      const nw = $('chart').clientWidth;
      if (chart && Math.abs(nw - w) > 4) { w = nw; chart.setSize({width: Math.max(300, nw), height: 210}); }
    }).observe($('chart'));
  }

  return {
    mount(el) { build(el); poll(true); },
    active(yes) {                              // shown or hidden (another sub-tab)
      on = yes;
      if (yes && root) poll(true);
      else clearTimeout(timer);
    },
  };
})();
