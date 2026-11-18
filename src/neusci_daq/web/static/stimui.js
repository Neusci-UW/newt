// Simple Stimulus, drawn and wired: the whole panel (Stimulator tab) or the dock (Record).
// Both show the same settings, kept in Python (/stim/state, /stim/cmd): change one and the
// other follows. The picture is the pulse type with its numbers labeled, in the colors of
// the boxes they're set in (an orange dot), as in the Qt Simple Stimulus.
'use strict';
const StimUI = (() => {
  const H = {'X-Newt-Key': new URLSearchParams(location.hash.slice(1)).get('k') || ''};
  // each setting: step, smallest, units ('u': the stimulator's units)
  const META = {level: [0.1, -1e9, 'u'], width: [0.05, 0.01, 'ms'], interval: [1, 0.1, 'ms'], rate: [1, 0.1, 'Hz'],
                count: [1, 1, ''], freq: [1, 0.1, 'Hz'], dur: [10, 1, 'ms'], offset: [0.1, -1e9, 'u']};
  const SHORT = {single: 'Single', pair: 'Pair', train: 'Train', sine: 'Sine', triangle: 'Triangle'};
  const views = [];
  let S = null, timer = 0, pending = {}, setTimer = 0;

  const fmt = (v) => String(+(+v).toPrecision(4)).replace('-', '−');
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));

  async function post(d) {
    try {
      const r = await fetch('/stim/cmd', {method: 'POST', headers: H, body: JSON.stringify(d)});
      const st = await r.json();
      update(st);
      if (st.problem) views.forEach((v) => v.say(st.problem, true));
    } catch (e) { views.forEach((v) => v.say('Newt is not answering.', true)); }
  }
  async function poll() {
    try { update(await (await fetch('/stim/state', {headers: H})).json()); } catch (e) { /* try again */ }
  }
  function update(st) { S = st; views.forEach((v) => v.show(st)); }
  function set(k, v) {                       // typed values go together, a moment after the last
    pending[k] = v;
    clearTimeout(setTimer);
    setTimer = setTimeout(() => { const d = pending; pending = {}; post({cmd: 'set', values: d}); }, 120);
  }

  // ------------------------------------------------------------ the labeled picture
  function picture(v, units) {
    const base = 128, top = 44, L = [], u = units;
    const arrow = (x0, y0, x1, y1) => L.push(`<line class="d" x1="${x0}" y1="${y0}" x2="${x1}" y2="${y1}" marker-start="url(#ah)" marker-end="url(#ah)"/>`);
    const text = (x, y, s, anchor = 'middle', cls = '') => L.push(`<text x="${x}" y="${y}" text-anchor="${anchor}" class="${cls}">${esc(s)}</text>`);
    let d = '';
    if (v.mode === 'sine' || v.mode === 'triangle') {
      const mid = (top + base) / 2, A = (base - top) / 2, x0 = 70, x1 = 490, cyc = 4, per = (x1 - x0) / cyc;
      L.push(`<line class="zero" x1="20" y1="${mid}" x2="500" y2="${mid}"/>`);
      d = `M20 ${mid}L${x0} ${mid}`;
      for (let k = 0; k <= 200; k++) {
        const f = k / 200, ph = f * cyc;
        const y = v.mode === 'sine' ? Math.sin(2 * Math.PI * ph) : (() => { const p = ph % 1; return p < .25 ? 4 * p : p < .75 ? 2 - 4 * p : 4 * p - 4; })();
        d += `L${(x0 + f * (x1 - x0)).toFixed(1)} ${(mid - y * A).toFixed(1)}`;
      }
      d += `L500 ${mid}`;
      arrow(44, mid, 44, top); text(38, (mid + top) / 2 + 5, `${fmt(v.level)} ${u}`, 'end');
      arrow(x0, top - 16, x0 + per, top - 16); text(x0 + per / 2, top - 24, `${fmt(v.freq)} Hz`);
      arrow(x0, base + 18, x1, base + 18); text((x0 + x1) / 2, base + 38, `${fmt(v.dur)} ms`);
    } else {
      const xs = v.mode === 'single' ? [[240, 290]] : v.mode === 'pair' ? [[160, 190], [330, 360]]
        : [0, 1, 2, 3, 4].map((i) => [140 + i * 66, 156 + i * 66]);
      d = `M20 ${base}` + xs.map(([a, b]) => `L${a} ${base}L${a} ${top}L${b} ${top}L${b} ${base}`).join('') + `L500 ${base}`;
      const ax = xs[0][0] - 34;
      arrow(ax, base, ax, top); text(ax - 7, (top + base) / 2 + 5, `${fmt(v.level)} ${u}`, 'end');
      if (v.mode !== 'train') {
        arrow(xs[0][0], base + 18, xs[0][1], base + 18); text((xs[0][0] + xs[0][1]) / 2, base + 38, `${fmt(v.width)} ms`);
      }
      if (v.mode === 'pair') {
        arrow(xs[0][0], top - 16, xs[1][0], top - 16); text((xs[0][0] + xs[1][0]) / 2, top - 24, `${fmt(v.interval)} ms`);
      }
      if (v.mode === 'train') {
        arrow(xs[0][0], top - 16, xs[1][0], top - 16);
        text(xs[0][0], top - 24, `${fmt(1000 / v.rate)} ms → ${fmt(v.rate)} Hz`, 'start');
        arrow(xs[0][0], base + 18, xs[4][1], base + 18); text((xs[0][0] + xs[4][1]) / 2, base + 38, `${v.count} pulses`);
      }
    }
    if (+v.offset) text(500, base - 6, `on ${fmt(v.offset)} ${u}`, 'end', 'k');
    return `<svg viewBox="0 0 520 172" role="img" aria-label="${esc(S ? S.describe : '')}">
      <defs><marker id="ah" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
        <path class="hd" d="M0 1L10 5L0 9z"/></marker></defs>
      <line class="ax" x1="20" y1="${base}" x2="500" y2="${base}"/>
      <path class="w" d="${d}"/>${L.join('')}</svg>`;
  }

  // ------------------------------------------------------------ a panel
  function fieldRow(k) {
    return `<div class="ssf" data-k="${k}"><label><i></i><span></span></label>
      <button data-d="-1" tabindex="-1">−</button><input type="number" data-k="${k}"><button data-d="1" tabindex="-1">+</button>
      <span class="u"></span></div>`;
  }

  function mount(el, full) {
    const keys = ['level', 'width', 'interval', 'rate', 'count', 'freq', 'dur', 'offset'];
    el.innerHTML = full ? `
      <div class="ssgrid">
        <section class="card stage">
          <div class="seg ssmodes"></div>
          <div class="sspic"></div>
          <div class="ssfields">${keys.map(fieldRow).join('')}</div>
        </section>
        <aside class="panel">
          <div class="card">
            <p class="sect-title">Send</p>
            <div class="check">Every <input type="number" class="inl" data-k="every" step="0.5" min="0.1"> s ·
              Repeats <input type="number" class="inl" data-k="repeats" step="1" min="0"></div>
            <p class="caption">Repeats 0: until Stop.</p>
            <label class="check"><input type="checkbox" data-k="ramp"> <span>After each stimulus, raise the amplitude by
              <input type="number" class="inl" data-k="ramp_by" step="0.1"> <span class="uu"></span>, up to
              <input type="number" class="inl" data-k="ramp_to" step="0.5"> <span class="uu"></span></span></label>
            <p class="caption">For threshold and recruitment labs: start repeating, watch Record, stop when the response stops growing.</p>
            <div class="btns"><button class="primary" data-a="fire">Stimulate</button>
              <button class="rep" data-a="repeat">Start repeating</button><button data-a="stop">Stop</button></div>
            <p class="ssmsg st"></p><p class="ssmsg say"></p>
          </div>
          <div class="card"><p class="sect-title">Sent</p><ol class="sslog"></ol></div>
          <div class="card"><p class="sect-title">Lab presets</p><div class="sspresets"></div>
            <div class="prow"><input type="text" placeholder="Name for these settings"><button data-a="save">Save</button></div></div>
          <div class="card"><p class="caption out"></p></div>
        </aside>
      </div>` : `
      <p class="sect-title">Stimulator</p>
      <div class="seg ssmodes"></div>
      <div class="sspic"></div>
      <div class="ssfields">${keys.map(fieldRow).join('')}</div>
      <div class="btns"><button class="primary" data-a="fire">Stimulate</button>
        <button class="rep" data-a="repeat">Repeat</button><button data-a="stop">Stop</button></div>
      <p class="ssmsg st"></p><p class="ssmsg say"></p>
      <a class="caption" href="stimulator.html${location.hash}">All the settings: the Stimulator tab →</a>`;
    const q = (s) => el.querySelector(s), qa = (s) => [...el.querySelectorAll(s)];
    let drawn = '';
    const view = {
      say(text, bad) { const m = q('.say'); m.textContent = text || ''; m.classList.toggle('bad', !!bad); },
      show(st) {
        const v = st.values, u = st.units, shown = (full ? st.fields : st.marked)[v.mode];
        const modes = q('.ssmodes');
        if (!modes.children.length) modes.innerHTML = st.modes.map(([k, name]) => `<button data-m="${k}">${full ? name : SHORT[k]}</button>`).join('');
        for (const b of modes.children) b.setAttribute('aria-pressed', String(b.dataset.m === v.mode));
        const key = JSON.stringify([v, u]);
        if (key !== drawn) { q('.sspic').innerHTML = picture(v, u); drawn = key; }
        for (const row of qa('.ssf')) {
          const k = row.dataset.k, m = META[k];
          row.hidden = !shown.includes(k);
          row.classList.toggle('marked', st.marked[v.mode].includes(k));
          row.querySelector('span').textContent = k === 'interval' ? 'Interval (start to start)' : st.names[k];
          row.querySelector('.u').textContent = m[2] === 'u' ? u : m[2];
          const inp = row.querySelector('input');
          inp.step = m[0]; inp.min = m[1] > -1e8 ? m[1] : '';
        }
        for (const inp of qa('input[data-k]')) {
          if (document.activeElement === inp || inp.dataset.k in pending) continue;
          if (inp.type === 'checkbox') inp.checked = !!v[inp.dataset.k];
          else inp.value = +(+v[inp.dataset.k]).toPrecision(6);
        }
        qa('.uu').forEach((s) => { s.textContent = u; });
        const fire = q('[data-a=fire]'), rep = q('[data-a=repeat]'), stop = q('[data-a=stop]');
        fire.disabled = !st.ok || st.repeating;
        rep.disabled = !st.ok;
        rep.setAttribute('aria-pressed', String(st.repeating));
        stop.disabled = !st.repeating && !st.busy;
        q('.st').textContent = st.status;
        if (st.message || !st.error) view.say(st.message, st.error);
        if (full) {
          q('.sslog').innerHTML = st.log.length ? st.log.map((s) => `<li>${esc(s)}</li>`).join('') : '<li>Nothing yet.</li>';
          q('.sspresets').innerHTML = (st.presets.length ? st.presets.map((n) => `<div class="p"><button data-p="${esc(n)}" title="Load these settings">${esc(n)}</button><button class="x" data-x="${esc(n)}" title="Delete this preset">×</button></div>`).join('')
            : '<p class="caption">None yet: set things up, name them, Save.</p>')
            + st.designs.map((n) => `<div class="p"><button data-dz="${esc(n)}" title="Open this design on Stimulus Design, ready to run">${esc(n)}<span class="tag">design</span></button></div>`).join('');
          q('.out').textContent = `Output on ${st.output}` + (st.monitor ? `, recorded on ${st.monitor}${st.monitor_name ? ' (' + st.monitor_name + ')' : ''}` : '')
            + ` · limit ±${fmt(st.limit)} V`;
        }
      },
    };
    el.addEventListener('click', (e) => {
      const t = e.target.closest('button');
      if (!t) return;
      if (t.dataset.m) set('mode', t.dataset.m);
      else if (t.dataset.d) {                         // − / +
        const inp = t.parentElement.querySelector('input'), k = inp.dataset.k, m = META[k];
        const nv = Math.max(m[1], +(+inp.value + +t.dataset.d * m[0]).toPrecision(8));
        inp.value = nv;
        set(k, nv);
      } else if (t.dataset.a === 'fire') post({cmd: 'fire'});
      else if (t.dataset.a === 'repeat') post({cmd: S && S.repeating ? 'stop' : 'repeat'});
      else if (t.dataset.a === 'stop') post({cmd: 'stop'});
      else if (t.dataset.a === 'save') { const n = q('.prow input'); post({cmd: 'preset_save', name: n.value}); n.value = ''; }
      else if (t.dataset.p) post({cmd: 'preset_load', name: t.dataset.p});
      else if (t.dataset.x) post({cmd: 'preset_delete', name: t.dataset.x});
      else if (t.dataset.dz && api.onDesign) api.onDesign(t.dataset.dz);
    });
    el.addEventListener('change', (e) => {
      const k = e.target.dataset.k;
      if (!k) return;
      if (e.target.type === 'checkbox') set(k, e.target.checked);
      else if (e.target.value !== '') set(k, +e.target.value);
    });
    el.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && e.target.dataset.k && e.target.value !== '') { set(e.target.dataset.k, +e.target.value); e.target.blur(); }
      if (e.key === 'Enter' && e.target.closest('.prow')) q('[data-a=save]').click();
    });
    views.push(view);
    if (S) view.show(S);
    if (!timer) { poll(); timer = setInterval(poll, 500); }
  }
  const api = {mount, onDesign: null};       // onDesign(name): a saved design was clicked (stimulator.html)
  return api;
})();
