// The Neural network simulator (web/network.py; simpage.py in the Qt app): the cells the
// simulated recordings come from, the synapses between them, which cell each input records,
// and the cells the stimulus drives. Changes go straight to the simulated backend, so Record
// shows them live. The Simulators page mounts it: NetworkUI.mount(el). Needs newt.css.
'use strict';
window.NetworkUI = (function () {
  const GREEN = '#2fae6a', RED = '#e0524f', ORANGE = '#E69F00';
  const STYLE = `
.nw{display:flex;flex-direction:column;gap:16px}
.nw .card{display:flex;flex-direction:column;gap:10px;min-width:0}
.nw-top{display:grid;grid-template-columns:minmax(0,1fr) minmax(260px,420px);gap:16px;align-items:start}
@media (max-width:900px){.nw-top{grid-template-columns:1fr}}
.nw-left{display:flex;flex-direction:column;gap:16px;min-width:0}
.nw-intro{margin:0;color:var(--muted);max-width:90ch}
.nw .nw-netset{flex-direction:row;flex-wrap:wrap;align-items:center;gap:10px 22px;font-size:14px}
.nw-netset label{display:inline-flex;align-items:center;gap:8px}
.nw input.nw-num{font-family:var(--font-mono);font-size:13px;width:66px;padding:3px 6px;border:1px solid var(--line);border-radius:6px;
  background:var(--surface);color:var(--ink);text-align:right;font-variant-numeric:tabular-nums}
.nw input.nw-num:focus{outline:2px solid color-mix(in srgb,var(--accent) 45%,transparent);outline-offset:0;border-color:var(--accent)}
.nw input.nw-num.bad{border-color:var(--bad);color:var(--bad)}
.nw input.nw-num:disabled{opacity:.45}
.nw input#nw-n{width:52px}
.nw select{font:inherit;font-size:13px;padding:3px 6px;border:1px solid var(--line);border-radius:6px;background:var(--surface);color:var(--ink)}
.nw .u{font-size:12px;color:var(--muted);font-weight:400}
.nw-scroll{overflow-x:auto}
.nw table{border-collapse:collapse;font-size:13.5px}
.nw thead th{font-weight:500;color:var(--muted);text-align:left;padding:2px 5px 6px;white-space:nowrap;font-size:12.5px;vertical-align:bottom;cursor:help}
.nw thead th .u{display:block}
.nw td{padding:3px 5px}
.nw tbody th{font-weight:600;text-align:left;padding:3px 10px 3px 0;white-space:nowrap}
.nw tbody th .u{display:block;font-size:11.5px}
.nw-rate{font-family:var(--font-mono);font-size:13px;white-space:nowrap;color:var(--muted);min-width:64px}
.nw-syn td.self{color:var(--muted);text-align:center}
.nw-syn input.nw-num{width:56px}
.nw-stimgrid{display:flex;flex-wrap:wrap;gap:8px 20px}
.nw-stimgrid label{display:inline-flex;align-items:center;gap:6px;font-size:14px;font-weight:500}
.nw-stimgrid span.cell{display:inline-flex;align-items:center;gap:6px}
.nw-wire{display:flex;flex-wrap:wrap;align-items:center;gap:8px 12px;font-size:14px}
.nw-wire .warn{color:var(--bad)}
.nw a.nw-btn{font-size:14px;border-radius:7px;border:1px solid var(--line);background:var(--surface);color:var(--ink);padding:5px 12px;text-decoration:none;white-space:nowrap}
.nw a.nw-btn:hover{border-color:var(--accent)}
.nw-pic svg{display:block;width:100%;height:auto}
.nw-pic text{font-family:var(--font-body);fill:var(--ink)}
.nw-pic .num{font-weight:600;font-size:15px}
.nw-pic .lab{font-family:var(--font-mono);font-size:11.5px;fill:var(--muted)}
.nw-pic .cell{fill:var(--surface);stroke:var(--accent);stroke-width:2.5}
.nw-pic .stimbox{fill:var(--surface);stroke:${ORANGE};stroke-width:2}
.nw-two{display:flex;flex-wrap:wrap;gap:16px;align-items:flex-start}
.nw-two>.card{flex:1 1 auto}
.nw-msg{position:sticky;bottom:8px;margin:0;padding:8px 12px;border:1px solid var(--bad);border-radius:8px;background:var(--surface);color:var(--bad);font-size:14px}
.nw-msg:empty{display:none}
`;
  const INTRO = 'A network of integrate-and-fire cells: each fires when its membrane potential reaches threshold, and ' +
    'its spikes give the cells it connects to a PSP after the synaptic delay. The stimulus is injected as a current ' +
    'into the cells it drives, following its waveform: pulse width, trains and sines all matter. Changes apply ' +
    'straight away, so Record shows them as you make them.';
  const FIRING_TIP = 'Spikes per second over the last 10 s, while Record is running';

  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  const fmt = (v) => String(Math.abs(v) < 1e-12 ? 0 : Number(Number(v).toPrecision(4)));

  function addStyle() {
    if (document.getElementById('nw-style')) return;
    const s = document.createElement('style');
    s.id = 'nw-style';
    s.textContent = STYLE;
    document.head.append(s);
  }

  function mount(el) {
    addStyle();
    const H = {'X-Newt-Key': new URLSearchParams(location.hash.slice(1)).get('k') || ''};
    let S = null, shape = '';
    const pending = new Map();        // edits the network hasn't taken yet (they don't check out together)
    const strength = {};              // a cell's strength while it's unticked (only this page keeps it, as in Qt)

    el.innerHTML = `<div class="nw">
      <div class="nw-top">
        <div class="nw-left">
          <p class="nw-intro">${esc(INTRO)}</p>
          <div class="card nw-netset">
            <label title="1 to 10 cells">Cells <input id="nw-n" class="nw-num" type="number" min="1" max="10" step="1"></label>
            <label title="From a spike (or stimulus pulse) to the start of the PSP it causes">Synaptic delay
              <input class="nw-num" data-e='{"net":"synapse_delay_ms"}'><span class="u">ms</span></label>
            <label title="How long the synaptic current lasts: longer gives slower, broader PSPs">Synaptic time const.
              <input class="nw-num" data-e='{"net":"synapse_tau_ms"}'><span class="u">ms</span></label>
            <span class="spacer"></span>
            <button type="button" id="nw-reset" title="Three cells in a chain: the stimulus drives 1, 1 excites 2, 2 excites 3, and 3 inhibits 1">Reset to the default network</button>
          </div>
          <div class="card">
            <h2 class="sect-title">⚡ Stimulator → network: which cells the stimulus drives</h2>
            <p class="caption">Whatever the Stimulator sends is injected as a current into the ticked cells.
              Strength: the depolarization from a 1 V, 1 ms pulse; negative hyperpolarizes.</p>
            <div class="nw-stimgrid"></div>
            <div class="nw-wire"><span class="nw-wiretext"></span><a class="nw-btn" id="nw-openstim">Open the Stimulator</a></div>
          </div>
        </div>
        <div class="card nw-pic">
          <h2 class="sect-title">Network</h2>
          <svg viewBox="0 0 340 320" role="img" aria-label="The network: cells, synapses and the stimulator"></svg>
          <p class="caption">Green arrows excite, red inhibit; thicker is stronger. ⚡ = the stimulator and the cells
            it drives. Labels show the inputs recording each cell (in = intracellular, ex = extracellular).</p>
        </div>
      </div>
      <div class="card">
        <h2 class="sect-title">Cells</h2>
        <p class="caption">Each box takes a number, or a number with a unit (e.g. 500 µs in a ms box).</p>
        <div class="nw-scroll"><table class="nw-cells"></table></div>
      </div>
      <div class="nw-two">
        <div class="card">
          <h2 class="sect-title">Synapses: PSP (mV) from each spike of the row's cell onto the column's cell</h2>
          <p class="caption">Positive excites (EPSP), negative inhibits (IPSP), 0 = no synapse.</p>
          <div class="nw-scroll"><table class="nw-syn"></table></div>
        </div>
        <div class="card">
          <h2 class="sect-title">Recordings: the cell each input records</h2>
          <div class="nw-scroll"><table class="nw-recs"></table></div>
        </div>
      </div>
      <p class="nw-msg" role="status"></p>
    </div>`;
    const $ = (sel) => el.querySelector(sel);
    $('#nw-openstim').href = '/static/stimulator.html' + location.hash;

    // -- talking to Python -------------------------------------------------------------
    async function call(body) {
      const r = await fetch('/network/cmd', {method: 'POST', headers: H, body: JSON.stringify(body)});
      return r.json();
    }
    async function load() {
      try { show(await fetch('/network/state', {headers: H}).then((r) => r.json())); } catch (e) { say(String(e)); }
    }
    async function edit(e, value) {
      pending.set(JSON.stringify(e), {...e, value});
      try { show(await call({cmd: 'set', edits: [...pending.values()]})); } catch (err) { say(String(err)); }
    }
    async function whole(body) {              // a change of shape: anything not taken is dropped
      pending.clear();
      try { show(await call(body)); } catch (err) { say(String(err)); }
    }
    function say(text) { $('.nw-msg').textContent = text || ''; }

    // -- drawing ----------------------------------------------------------------------
    function show(out) {
      if (!out.problem) pending.clear();
      say(out.problem);
      S = out;
      const sig = S.cells.length + '|' + S.inputs.map((i) => i.physical + (i.monitor ? '*' : '')).join(',');
      if (sig !== shape) { shape = sig; build(); }
      fill();
    }

    function build() {
      const n = S.cells.length;
      const cols = S.cell_columns;
      let h = '<thead><tr><th></th>' + cols.map(([head, , unit, tip]) =>
        `<th title="${esc(tip)}">${esc(head)}<span class="u">${esc(unit) || '&nbsp;'}</span></th>`).join('') +
        `<th title="${FIRING_TIP}">Firing now<span class="u">&nbsp;</span></th></tr></thead><tbody>`;
      for (let i = 0; i < n; i++) {
        h += `<tr><th>Cell ${i + 1}</th>` + cols.map(([head, key]) =>
          `<td><input class="nw-num" aria-label="Cell ${i + 1} ${esc(head)}" data-e='{"cell":${i},"key":"${key}"}'></td>`).join('') +
          `<td class="nw-rate" data-i="${i}" title="${FIRING_TIP}">—</td></tr>`;
      }
      $('.nw-cells').innerHTML = h + '</tbody>';

      h = '<thead><tr><th></th>';
      for (let j = 0; j < n; j++) h += `<th>To ${j + 1}</th>`;
      h += '</tr></thead><tbody>';
      for (let i = 0; i < n; i++) {
        h += `<tr><th>From ${i + 1}</th>`;
        for (let j = 0; j < n; j++) {
          h += i === j ? '<td class="self" title="A cell has no synapse onto itself">—</td>'
            : `<td><input class="nw-num" aria-label="Synapse ${i + 1} to ${j + 1}" data-e='{"syn":[${i},${j}]}'></td>`;
        }
        h += '</tr>';
      }
      $('.nw-syn').innerHTML = h + '</tbody>';

      const rc = S.rec_columns;
      h = '<thead><tr><th></th>' + rc.map(([head, , unit, tip]) =>
        `<th title="${esc(tip)}">${esc(head)}<span class="u">${esc(unit) || '&nbsp;'}</span></th>`).join('') + '</tr></thead><tbody>';
      const cellOpts = '<option value="-1">None</option>' +
        S.cells.map((_, i) => `<option value="${i}">Cell ${i + 1}</option>`).join('');
      const kindOpts = S.kinds.map(([label, key]) => `<option value="${key}">${esc(label)}</option>`).join('');
      for (const inp of S.inputs) {
        const p = esc(inp.physical);
        h += `<tr><th>${p}<span class="u">${esc(inp.name)}</span></th>`;
        if (inp.monitor) {
          h += `<td colspan="${rc.length}" class="caption" title="Wired to the stimulator: this input records what the Stimulator sends">records the stimulus</td>`;
        } else {
          h += `<td><select aria-label="${p}: the cell it records" title="${esc(rc[0][3])} (None: just noise until you pick one)" data-e='{"input":"${p}","key":"cell"}'>${cellOpts}</select></td>` +
            `<td><select aria-label="${p}: how it records" data-e='{"input":"${p}","key":"kind"}'>${kindOpts}</select></td>` +
            rc.slice(2).map(([head, key]) =>
              `<td><input class="nw-num" aria-label="${p} ${esc(head)}" data-e='{"input":"${p}","key":"${key}"}'></td>`).join('');
        }
        h += '</tr>';
      }
      $('.nw-recs').innerHTML = h + '</tbody>';

      $('.nw-stimgrid').innerHTML = S.cells.map((_, i) =>
        `<span class="cell"><label title="Whether the stimulus reaches this cell"><input type="checkbox" class="nw-tick" data-i="${i}">Cell ${i + 1}</label>` +
        `<input class="nw-num nw-strength" data-i="${i}" aria-label="Cell ${i + 1} stimulus strength" title="Depolarization from a 1 V, 1 ms pulse; negative hyperpolarizes"><span class="u">mV</span></span>`).join('');
    }

    function current(e) {
      if ('cell' in e) return S.cells[e.cell][e.key];
      if ('syn' in e) return S.weights[e.syn[0]][e.syn[1]];
      if ('net' in e) return S[e.net];
      const inp = S.inputs.find((x) => x.physical === e.input);
      return inp ? inp[e.key] : '';
    }

    function fill() {
      const focus = document.activeElement;
      $('#nw-n').max = S.max_cells;
      if (focus !== $('#nw-n')) $('#nw-n').value = S.cells.length;
      for (const w of el.querySelectorAll('[data-e]')) {
        const id = w.dataset.e, waiting = pending.get(id);
        w.classList.toggle('bad', !!waiting);
        if (waiting) { w.value = waiting.value; continue; }
        if (w === focus && w.tagName === 'INPUT') continue;      // being typed in
        const v = current(JSON.parse(id));
        w.value = typeof v === 'number' && w.tagName === 'INPUT' ? fmt(v) : String(v);
      }
      S.cells.forEach((c, i) => {
        const tick = el.querySelector(`.nw-tick[data-i="${i}"]`), box = el.querySelector(`.nw-strength[data-i="${i}"]`);
        const waiting = pending.get(JSON.stringify({cell: i, key: 'stimulus'}));
        tick.checked = !!(waiting ? waiting.value !== '0' : c.stimulus);
        box.disabled = !tick.checked;
        if (box !== focus) box.value = waiting ? waiting.value : fmt(c.stimulus || strength[i] || 25);
        box.classList.toggle('bad', !!waiting);
      });
      tint();
      wire();
      draw();
      showRates(S);
    }

    function tint() {
      for (const w of el.querySelectorAll('.nw-syn input')) {
        const v = parseFloat(w.value.replace('−', '-')) || 0;
        const a = Math.min(110, 25 + 6 * Math.abs(v)) / 255;
        w.style.background = v > 0 ? `rgba(0,160,80,${a})` : v < 0 ? `rgba(220,50,50,${a})` : '';
      }
    }

    function wire() {
      const driven = S.cells.map((c, i) => (c.stimulus ? i + 1 : 0)).filter(Boolean);
      const mon = S.inputs.find((i) => i.monitor);
      const t = $('.nw-wiretext');
      if (!driven.length) {
        t.innerHTML = '<span class="warn">No cell is ticked: stimuli won’t affect the network.</span>';
      } else {
        t.textContent = `The stimulus drives cell${driven.length > 1 ? 's' : ''} ${driven.join(', ')}.` +
          (mon ? ` It’s recorded on ${mon.physical} (${mon.name}).` : '');
      }
    }

    function showRates(r) {
      for (const td of el.querySelectorAll('.nw-rate')) {
        const i = +td.dataset.i, v = r.rates && i < r.rates.length ? r.rates[i] : null;
        td.textContent = v == null ? '—' : `${v.toFixed(1)} /s`;
      }
    }

    // the picture: cells on a circle, synapses as arrows, the stimulator in the corner
    function draw() {
      const n = S.cells.length, w = S.weights;
      const X = (x) => (x + 1.7) * 100, Y = (y) => (1.6 - y) * 100;
      const pos = [];
      for (let i = 0; i < n; i++) {
        const a = Math.PI / 2 - 2 * Math.PI * i / n, r = n === 1 ? 0 : 1;
        pos.push([Math.cos(a) * r, Math.sin(a) * r]);
      }
      const R = 0.18;
      let biggest = 1e-9;
      for (const row of w) for (const v of row) biggest = Math.max(biggest, Math.abs(v));
      const parts = [];
      const arrow = (a, b, color, width, dash) => {
        const [x0, y0, x1, y1] = [X(a[0]), Y(a[1]), X(b[0]), Y(b[1])];
        const d = Math.hypot(x1 - x0, y1 - y0) || 1, ux = (x1 - x0) / d, uy = (y1 - y0) / d;
        const head = 11 + width, half = 4.5 + width / 2;
        const bx = x1 - ux * head, by = y1 - uy * head;
        parts.push(`<line x1="${x0.toFixed(1)}" y1="${y0.toFixed(1)}" x2="${bx.toFixed(1)}" y2="${by.toFixed(1)}" stroke="${color}" stroke-width="${width.toFixed(1)}"${dash ? ' stroke-dasharray="6 4"' : ''}/>`);
        parts.push(`<polygon fill="${color}" points="${x1.toFixed(1)},${y1.toFixed(1)} ${(bx - uy * half).toFixed(1)},${(by + ux * half).toFixed(1)} ${(bx + uy * half).toFixed(1)},${(by - ux * half).toFixed(1)}"/>`);
      };
      for (let i = 0; i < n; i++) {
        for (let j = 0; j < n; j++) {
          if (i === j || !w[i][j]) continue;
          const a = pos[i], b = pos[j], d = Math.hypot(b[0] - a[0], b[1] - a[1]);
          const u = [(b[0] - a[0]) / d, (b[1] - a[1]) / d], side = [-u[1] * 0.06, u[0] * 0.06];   // two-way: side by side
          arrow([a[0] + u[0] * R + side[0], a[1] + u[1] * R + side[1]],
                [b[0] - u[0] * (R + 0.02) + side[0], b[1] - u[1] * (R + 0.02) + side[1]],
                w[i][j] > 0 ? GREEN : RED, 1.5 + 4 * Math.abs(w[i][j]) / biggest, false);
        }
      }
      const stim = [-1.45, 1.35];
      S.cells.forEach((c, i) => {
        if (!c.stimulus) return;
        const d = Math.hypot(pos[i][0] - stim[0], pos[i][1] - stim[1]) || 1e-9;
        const u = [(pos[i][0] - stim[0]) / d, (pos[i][1] - stim[1]) / d];
        arrow([stim[0] + u[0] * 0.16, stim[1] + u[1] * 0.16], [pos[i][0] - u[0] * (R + 0.02), pos[i][1] - u[1] * (R + 0.02)],
              ORANGE, 2, true);
      });
      parts.push(`<rect class="stimbox" x="${X(stim[0]) - 15}" y="${Y(stim[1]) - 15}" width="30" height="30" rx="4"/>`,
                 `<text x="${X(stim[0])}" y="${Y(stim[1]) + 6}" text-anchor="middle" style="fill:${ORANGE};font-size:17px">⚡</text>`,
                 `<text x="${X(stim[0]) - 16}" y="${Y(stim[1]) + 31}" style="font-size:12px">Stimulator</text>`);
      const recorded = {};
      for (const inp of S.inputs) {
        if (inp.monitor || inp.cell < 0) continue;
        (recorded[Math.min(inp.cell, n - 1)] ||= []).push(inp.physical + (inp.kind === 'intracellular' ? ' (in)' : ' (ex)'));
      }
      pos.forEach(([x, y], i) => {
        parts.push(`<circle class="cell" cx="${X(x)}" cy="${Y(y)}" r="${R * 100}"><title>Cell ${i + 1}</title></circle>`,
                   `<text class="num" x="${X(x)}" y="${Y(y) + 5}" text-anchor="middle">${i + 1}</text>`);
        if (recorded[i]) {
          const [lx, ly] = n > 1 ? [x * 1.38, y * 1.38] : [0, -0.35];
          parts.push(`<text class="lab" x="${X(lx)}" y="${Y(ly) + 4}" text-anchor="middle">${esc(recorded[i].join('  '))}</text>`);
        }
      });
      $('.nw-pic svg').innerHTML = parts.join('');
    }

    // -- the student's changes ----------------------------------------------------------
    el.addEventListener('change', (ev) => {
      const t = ev.target;
      if (!S) return;
      if (t.id === 'nw-n') return whole({cmd: 'cells', n: +t.value || 1});
      if (t.classList.contains('nw-tick')) {
        const i = +t.dataset.i, box = el.querySelector(`.nw-strength[data-i="${i}"]`);
        if (!t.checked) strength[i] = parseFloat(box.value.replace('−', '-')) || 25;
        return edit({cell: i, key: 'stimulus'}, t.checked ? box.value : '0');
      }
      if (t.classList.contains('nw-strength')) {
        const i = +t.dataset.i;
        if (el.querySelector(`.nw-tick[data-i="${i}"]`).checked) edit({cell: i, key: 'stimulus'}, t.value);
        return;
      }
      if (t.dataset.e) edit(JSON.parse(t.dataset.e), t.value);
    });
    el.addEventListener('input', (ev) => { if (ev.target.closest('.nw-syn')) tint(); });
    $('#nw-reset').addEventListener('click', () => whole({cmd: 'reset'}));

    // firing rates, once a second while this is on screen; everything again when it comes back
    let wasShown = true;
    const timer = setInterval(async () => {
      if (!el.isConnected) return clearInterval(timer);
      const shown = !document.hidden && el.offsetParent !== null;
      if (!shown) { wasShown = false; return; }
      if (!wasShown) {
        wasShown = true;
        if (!pending.size && !el.contains(document.activeElement)) return load();
      }
      try { showRates(await fetch('/network/rates', {headers: H}).then((r) => r.json())); } catch (e) { /* next time */ }
    }, 1000);
    load();
  }

  return {mount};
})();
