// Stimulus Design, drawn and wired (the Stimulator tab's second sub-tab). Everything is
// kept and checked in Python (/design/state, /design/cmd: web/design.py); this draws it.
// Left: the whole stimulus as one picture (click a segment to edit it underneath), the
// picked segment's settings with their dashed per-trial boxes, and Trials. Right: Run,
// Pause and Stop with progress, Send trial N once, what was sent, and folded Output and Save.
'use strict';
const DesignUI = (() => {
  const H = {'X-Newt-Key': new URLSearchParams(location.hash.slice(1)).get('k') || ''};
  const ORDER_HINT = {blocked: 'each trial all its repeats, then the next', interleaved: 'every trial once, then again',
                      random: 'every trial once in a shuffled order, then again (new shuffle each round)'};
  let S = null, pic = null, picV = null, kinds = null, el = null, timer = 0, drawn = '', edKey = '', busyPost = 0;

  const fmt = (v) => String(+(+v).toPrecision(4)).replace('-', '−');
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  const q = (s) => el.querySelector(s), qa = (s) => [...el.querySelectorAll(s)];

  async function post(d) {
    busyPost++;
    try {
      const r = await fetch('/design/cmd', {method: 'POST', headers: H, body: JSON.stringify({...d, pic: picV})});
      const st = await r.json();
      update(st);
      if (!r.ok) say(st.problem, true);
    } catch (e) { say('Newt is not answering.', true); }
    busyPost--;
  }
  async function poll() {
    if (busyPost || !el || (el.offsetParent === null && !(S && S.running))) return;   // hidden and idle: no need
    try { update(await (await fetch('/design/state?pic=' + (picV ?? ''), {headers: H})).json()); } catch (e) { /* again soon */ }
  }
  function say(text, bad) { const m = q('.say'); m.textContent = text || ''; m.classList.toggle('bad', !!bad); }

  // ------------------------------------------------------------ the picture
  function drawPic() {
    const box = q('.dspic'), W = Math.max(360, box.clientWidth || 800), many = pic.n > 1, sel = S.seg.i;
    const key = JSON.stringify([picV, sel, S.seg.dims, W, S.k, S.units]);
    if (key === drawn) return;
    drawn = key;
    const HH = many ? 300 : 280, L = 64, R = W - 16, T = 36, B = HH - (many ? 66 : 48);
    const total = Math.max(pic.total_ms, 1e-6);
    let lo = pic.lo, hi = pic.hi;
    if (hi - lo < 1e-9) { hi = lo + 1; lo -= 0.1; }
    const pad = (hi - lo) * 0.08;
    lo -= pad; hi += pad;
    const X = (ms) => L + ms / total * (R - L), Y = (v) => B - (v - lo) / (hi - lo) * (B - T);
    const o = [];
    // the segments: a band each (click to pick), its letter and kind under it
    pic.spans.forEach(([a, b], i) => {
      const x0 = X(a), x1 = Math.max(X(b), x0 + 4), on = i === sel;
      o.push(`<rect class="band${on ? ' on' : ''}" data-i="${i}" x="${x0.toFixed(1)}" y="${T - 26}" width="${(x1 - x0).toFixed(1)}" height="${B - T + 26}"><title>Edit ${pic.letters[i]} · ${esc(pic.names[i])}</title></rect>`);
      if (x1 - x0 >= 12 || on) o.push(`<text class="let${on ? ' on' : ''}" x="${(x0 + x1) / 2}" y="${B + 17}" text-anchor="middle">${pic.letters[i]}</text>`);
      if (x1 - x0 > 46) o.push(`<text class="nm" x="${(x0 + x1) / 2}" y="${B + 33}" text-anchor="middle">${esc(pic.names[i].length * 7 > x1 - x0 ? pic.names[i].slice(0, Math.max(1, Math.floor((x1 - x0) / 7) - 1)) + '…' : pic.names[i])}</text>`);
    });
    // 0, the axis labels, the length
    o.push(`<line class="zero" x1="${L}" y1="${Y(0)}" x2="${R}" y2="${Y(0)}"/>`);
    for (const v of [hi, 0, lo]) {
      if (v === 0 || Math.abs(Y(v) - Y(0)) > 14) o.push(`<text x="${L - 8}" y="${Y(v) + 4}" text-anchor="end">${fmt(v * S.k)} ${esc(S.units)}</text>`);
    }
    o.push(`<text x="${R}" y="${B + 17}" text-anchor="end">${fmt(pic.total_ms)} ms</text>`);
    // every trial in its color, the shown one last and thickest
    pic.traces.forEach((t, j) => {
      const last = j === pic.traces.length - 1;
      let d = '';
      for (let i = 0; i < t.x.length; i++) d += (i ? 'L' : 'M') + X(t.x[i]).toFixed(1) + ' ' + Y(t.y[i]).toFixed(1);
      const col = t.rgb ? `rgb(${t.rgb.join(',')})` : 'var(--accent)';
      o.push(`<path class="tr${last ? ' cur' : ''}" d="${d}" style="stroke:${col}"/>`);
    });
    // the map's key: trial 1 → n
    if (many) {
      const kw = 110, x0 = R - kw - 34, y0 = B + 46;
      o.push(`<defs><linearGradient id="dsgrad">${pic.stops.map(([f, c]) => `<stop offset="${f}" stop-color="rgb(${c.join(',')})"/>`).join('')}</linearGradient></defs>`,
        `<rect x="${x0}" y="${y0}" width="${kw}" height="8" rx="3" fill="url(#dsgrad)"/>`,
        `<text x="${x0 - 6}" y="${y0 + 8}" text-anchor="end">Trial 1</text><text x="${x0 + kw + 6}" y="${y0 + 8}">${pic.n}</text>`);
    }
    // the picked segment's numbers, in orange, above it
    if (pic.spans[sel] && S.seg.dims) {
      const [a, b] = pic.spans[sel], tw = S.seg.dims.length * 7.9 + 8;
      const cx = (X(a) + Math.max(X(b), X(a) + 4)) / 2, x = Math.min(Math.max(L, cx - tw / 2), R - tw);
      o.push(`<text class="dim" x="${x + tw / 2}" y="${T - 12}" text-anchor="middle">${esc(S.seg.dims)}</text>`);
    }
    box.innerHTML = `<svg width="${W}" height="${HH}" viewBox="0 0 ${W} ${HH}" role="img" aria-label="The whole stimulus, ${pic.letters.length} segments">${o.join('')}</svg>`;
  }

  // ------------------------------------------------------------ the picked segment
  function fieldHtml(f) {
    return `<div class="dsf" data-k="${f.key}"><span class="lab">${f.main ? '<i></i>' : ''}${esc(f.label)}</span>
      <div class="stepper"><button data-d="-1" tabindex="-1" aria-label="Less">−</button><input type="number" data-k="${f.key}" aria-label="${esc(f.label)}"><button data-d="1" tabindex="-1" aria-label="More">+</button><span class="u">${esc(f.unit)}</span></div>
      ${f.dkey ? `<input type="text" class="pt" data-dk="${f.dkey}" placeholder="same every trial" title="How it changes from trial to trial. Blank: the same each trial. +0.5: 0.5 more each trial. 0.5, 1, 2: one value per trial." aria-label="${esc(f.label)} per trial">` : ''}</div>`;
  }
  function extraHtml(seg) {
    const x = seg.extra;
    const toggle = (label, key, names, on) => `<div class="dsf"><span class="lab">${label}</span><div class="seg dsx" data-x="${key}">${names.map((n, i) => `<button data-v="${i}" aria-pressed="${i === on}">${n}</button>`).join('')}</div></div>`;
    if ('biphasic' in x) return `<div class="dsf"><span class="lab">&nbsp;</span><label class="check"><input type="checkbox" data-x="biphasic"> Biphasic (up, then down by as much)</label></div>`;
    if ('log' in x) return toggle('Sweep', 'log', ['Linear', 'Log'], x.log ? 1 : 0);
    if ('same' in x) return toggle('Each trial', 'same', ['New noise', 'Same noise'], x.same ? 1 : 0);
    if ('points' in x) {
      return `<div class="dsf dspts"><span class="lab">Points (time in ms, level in ${esc(S.units)})</span>
        <table><thead><tr><th>time (ms)</th><th>level (${esc(S.units)})</th></tr></thead><tbody></tbody></table>
        <div class="btns"><button data-pt="add">+ Point</button><button data-pt="del">− Last</button>
          <div class="seg dsx" data-x="join"><button data-v="line">Lines</button><button data-v="step">Steps</button></div></div></div>`;
    }
    if ('file' in x) return `<div class="dsf"><span class="lab">File</span><span class="mono">${esc(x.file)}</span><p class="caption">Choosing a file isn't in the HTML version yet.</p></div>`;
    return '';
  }
  function showEditor(seg) {
    const key = JSON.stringify([seg.i, seg.kind, seg.n_parts, seg.fields.map((f) => f.key), Object.keys(seg.extra),
                                (seg.extra.points || []).length, S.running]);
    const ed = q('.dsed');
    if (key !== edKey) {
      edKey = key;
      ed.innerHTML = `<div class="dsedhead"><b class="dstitle"></b><span class="caption">${esc(seg.hint)}</span><span class="spacer"></span>
          <button class="link" data-mv="-1" title="Move this segment earlier"${seg.i > 0 ? '' : ' disabled'}>◀</button>
          <button class="link" data-mv="1" title="Move this segment later"${seg.i < seg.n_parts - 1 ? '' : ' disabled'}>▶</button>
          <button class="link danger" data-rm title="Take this segment out"${seg.n_parts > 1 ? '' : ' disabled'}>Remove</button></div>
        <div class="dsfields">${seg.fields.map(fieldHtml).join('')}${extraHtml(seg)}</div>`;
      ed.toggleAttribute('inert', S.running);
    }
    q('.dstitle').textContent = `${seg.letter} · ${seg.name}`;
    for (const f of seg.fields) {
      const row = ed.querySelector(`.dsf[data-k="${f.key}"]`), inp = row.querySelector('input[type=number]');
      inp.step = f.step;
      inp.min = f.min ?? '';
      if (document.activeElement !== inp) inp.value = f.value;
      const pt = row.querySelector('.pt');
      if (pt) {
        if (document.activeElement !== pt) pt.value = f.dtext;
        pt.classList.toggle('set', !!f.dtext);
      }
    }
    const x = seg.extra;
    const cb = ed.querySelector('[data-x=biphasic]');
    if (cb) cb.checked = !!x.biphasic;
    for (const g of ed.querySelectorAll('.dsx')) {
      const k = g.dataset.x, v = k === 'join' ? x.join : String(x[k] ? 1 : 0);
      for (const b of g.children) b.setAttribute('aria-pressed', String(b.dataset.v === v));
    }
    const tb = ed.querySelector('.dspts tbody');
    if (tb && !tb.contains(document.activeElement)) {
      tb.innerHTML = x.points.map(([a, b]) => `<tr><td><input type="number" step="1" value="${+a}"></td><td><input type="number" step="0.1" value="${+b}"></td></tr>`).join('');
    }
  }

  // ------------------------------------------------------------ everything else
  function orderExample(n, reps, order) {
    const seq = [];
    if (order === 'blocked') for (let t = 1; t <= n; t++) for (let r = 0; r < reps; r++) seq.push(t);
    else for (let r = 0; r < reps; r++) for (let t = 1; t <= n; t++) seq.push(t);
    if (order === 'random') return ORDER_HINT.random;
    return 'Sends trials ' + seq.slice(0, 10).join(', ') + (seq.length > 10 ? ', …' : '') + ` (${ORDER_HINT[order]})`;
  }

  function update(st) {
    S = st;
    if (st.picture) { pic = st.picture; picV = st.pic_v; }
    if (st.kinds && !kinds) {
      kinds = st.kinds;
      q('.dskinds').innerHTML = kinds.map((k, i) => `<button class="kind" data-kind="${i}"><svg viewBox="0 0 64 30" aria-hidden="true"><polyline points="${k.pts.map((p) => p.join(',')).join(' ')}"/></svg><b>${esc(k.label)}</b><span>${esc(k.hint)}</span></button>`).join('');
    }
    if (!pic) return;
    const seg = st.seg;
    q('.dssum').textContent = `${st.summary} · ${fmt(pic.total_ms)} ms`;
    drawPic();
    showEditor(seg);
    q('.dspick').textContent = `after ${seg.letter} · click one`;
    const tsel = q('.dstrial');
    tsel.hidden = st.n < 2;
    q('.dstrial b').textContent = st.cur + 1;
    q('.dstrial .of').textContent = st.n;
    q('.dsprob').textContent = st.pic_problem || (st.warnings.length ? st.warnings[0] : '');
    q('.dsprob').classList.toggle('bad', !!st.pic_problem);
    for (const inp of qa('.dstrials input[data-t]')) if (document.activeElement !== inp) inp.value = st[inp.dataset.t];
    const ord = q('.dsorder');
    if (!ord.children.length) ord.innerHTML = st.orders.map(([k, n]) => `<button data-o="${k}">${n}</button>`).join('');
    for (const b of ord.children) b.setAttribute('aria-pressed', String(b.dataset.o === st.order));
    q('.dsordhint').textContent = st.n * st.repeats > 1 ? orderExample(st.n, st.repeats, st.order) : '';
    qa('.dstrials input, .dsorder button, .dsadd').forEach((w) => { w.disabled = st.running; });
    // right: Run and its progress
    const run = q('.dsrun');
    run.textContent = st.running ? 'Running…' : st.total > 1 ? `Run ${st.total} stimuli` : 'Send';
    run.disabled = st.running || !st.ok;
    const lamp = q('.dslamp');
    lamp.querySelector('span').textContent = st.lamp;
    lamp.classList.toggle('bad', !st.ok);
    q('.dsprog').hidden = !st.running;
    q('.dsprog .bar i').style.width = `${st.total ? 100 * st.sent / st.total : 0}%`;
    const pause = q('[data-a=pause]');
    pause.textContent = st.paused ? 'Resume' : 'Pause';
    pause.setAttribute('aria-pressed', String(st.paused));
    q('.dsstop').hidden = st.running || !st.busy;
    const once = q('.dsonce');
    once.hidden = st.n < 2;
    once.textContent = `Send trial ${st.cur + 1} once`;
    once.disabled = st.running || st.busy;
    if (st.message || !st.error) say(st.message, st.error);
    q('.sslog').innerHTML = st.log.length ? st.log.map((s) => `<li>${esc(s)}</li>`).join('') : '<li>Nothing yet.</li>';
    // folded: Output and Save
    q('.dsoutsum').textContent = `${st.output} → ${st.monitor ? 'recorded on ' + st.monitor : 'not recorded'}`;
    q('.dsoutkv').innerHTML = `<span>Sends on</span><b>${esc(st.output)}</b>
      <span>Recorded on</span><b>${st.monitor ? esc(st.monitor) + (st.monitor_name ? ' · ' + esc(st.monitor_name) : '') : 'nothing'}</b>
      <span>Output rate</span><b>${st.rate.toLocaleString()} samples/s</b><span>Limit</span><b>±${fmt(st.limit)} V</b>`;
    const off = q('[data-a=offset]');
    if (document.activeElement !== off) off.value = st.offset;
    q('.dsoffu').textContent = st.units;
    q('.dssavesum').textContent = st.name ? `“${st.name}”` : '';
    const nm = q('.dsname');
    if (document.activeElement !== nm && !nm.value) nm.placeholder = st.name || 'Name for this design';
    q('.dssaved').innerHTML = st.designs.length ? st.designs.map((n) => `<div class="p"><button data-load="${esc(n)}" title="Open this design">${esc(n)}</button><button class="x" data-del="${esc(n)}" title="Delete this design">×</button></div>`).join('')
      : '<p class="caption">None yet.</p>';
  }

  // ------------------------------------------------------------ building and wiring
  function mount(root) {
    el = root;
    el.innerHTML = `
      <div class="dsgrid">
        <section class="card stage">
          <div class="dstop"><span class="sect-title">Stimulus</span><span class="caption dssum"></span><span class="spacer"></span>
            <button class="dsadd" aria-expanded="false">+ Add segment</button></div>
          <div class="dspic"></div>
          <div class="dshint caption"><span>Click a segment on the picture to change it</span><span class="spacer"></span>
            <span class="dstrial" hidden>Picture shows trial <button data-tr="-1" aria-label="Trial before">‹</button><b></b><button data-tr="1" aria-label="Next trial">›</button> of <span class="of"></span></span></div>
          <div class="dspicker" hidden><div class="dsedhead"><b>Add a segment</b><span class="caption dspick"></span><span class="spacer"></span>
            <button class="link" data-a="close">Close</button></div><div class="dskinds"></div></div>
          <div class="dsed"></div>
          <p class="dsprob caption"></p>
          <div class="dstrials"><span class="sect-title">Trials</span>
            <div class="dsf"><span class="lab">How many</span><input type="number" data-t="n" min="1" max="200" step="1"></div>
            <div class="dsf"><span class="lab">Repeat each</span><input type="number" data-t="repeats" min="1" max="100" step="1"></div>
            <div class="dsf"><span class="lab">Order</span><div class="seg dsorder"></div></div>
            <div class="dsf"><span class="lab">Every</span><div class="stepper"><input type="number" data-t="gap" min="0.1" step="0.5"><span class="u">s</span></div></div>
          </div>
          <p class="caption dsordhint"></p>
          <p class="caption">How each trial differs: the dashed box under a number. Blank: the same every trial. <b>+0.5</b>: 0.5 more each trial. <b>0.5, 1, 2</b>: one value per trial.</p>
        </section>
        <aside class="panel">
          <div class="card">
            <button class="primary dsrun">Run</button>
            <p class="dslamp"><i></i><span></span></p>
            <div class="dsprog" hidden><div class="bar"><i></i></div>
              <div class="btns"><button class="rep" data-a="pause">Pause</button><button data-a="stop">Stop</button></div></div>
            <button class="dsstop" data-a="stop" hidden>Stop</button>
            <button class="dsonce" hidden>Send trial 1 once</button>
            <p class="ssmsg say"></p>
          </div>
          <div class="card"><p class="sect-title">Sent</p><ol class="sslog"></ol></div>
          <details class="card fold"><summary><b>Output</b> <span class="caption dsoutsum"></span></summary>
            <div class="kv dsoutkv"></div>
            <div class="kv"><span>Offset</span><span class="stepper"><input type="number" data-a="offset" step="0.1"><span class="u dsoffu"></span></span></div>
            <p class="caption">Offset: a steady level the whole stimulus sits on, baselines too. Each segment can have its own as well.</p></details>
          <details class="card fold"><summary><b>Save</b> <span class="caption dssavesum"></span></summary>
            <div class="prow"><input type="text" class="dsname" placeholder="Name for this design"><button data-a="save">Save</button></div>
            <div class="sspresets dssaved"></div>
            <p class="caption">Saved designs also show under Simple Stimulus's Lab presets.</p></details>
          <p class="caption">Responses: watch the Record page, then look in Analysis.</p>
        </aside>
      </div>`;
    el.addEventListener('click', (e) => {
      const t = e.target.closest('button, rect.band');
      if (!t || t.disabled) return;
      const d = t.dataset, picker = q('.dspicker');
      if (t.matches('rect.band')) { post({cmd: 'pick', i: +d.i}); picker.hidden = true; q('.dsadd').setAttribute('aria-expanded', 'false'); }
      else if (t.classList.contains('dsadd') || d.a === 'close') {
        picker.hidden = !picker.hidden;
        q('.dsadd').setAttribute('aria-expanded', String(!picker.hidden));
        q('.dsed').hidden = !picker.hidden;
      } else if (d.kind) { post({cmd: 'add', i: +d.kind}); picker.hidden = true; q('.dsed').hidden = false; q('.dsadd').setAttribute('aria-expanded', 'false'); }
      else if (d.d) {                                         // − / +
        const inp = t.parentElement.querySelector('input'), step = +inp.step || 1;
        let v = +(+inp.value + +d.d * step).toPrecision(9);
        if (inp.min !== '') v = Math.max(+inp.min, v);
        inp.value = v;
        post({cmd: 'set', key: inp.dataset.k, value: v});
      } else if (d.mv) post({cmd: 'move', step: +d.mv});
      else if ('rm' in d) post({cmd: 'remove'});
      else if (d.v !== undefined && t.parentElement.dataset.x) {
        const k = t.parentElement.dataset.x;
        post({cmd: 'set', key: k === 'same' ? 'seed' : k, value: k === 'join' ? d.v : d.v === '1'});
      } else if (d.pt) {
        const pts = S.seg.extra.points.slice();
        if (d.pt === 'add') { const last = pts[pts.length - 1] || [0, 0]; pts.push([last[0] + 10, 0]); } else pts.pop();
        post({cmd: 'points', points: pts});
      } else if (d.tr) post({cmd: 'show', trial: S.cur + 1 + +d.tr});
      else if (d.o) post({cmd: 'trials', order: d.o});
      else if (t.classList.contains('dsrun')) post({cmd: 'run'});
      else if (d.a === 'pause') post({cmd: 'pause', on: !S.paused});
      else if (d.a === 'stop') post({cmd: 'stop'});
      else if (t.classList.contains('dsonce')) post({cmd: 'once'});
      else if (d.a === 'save') { const n = q('.dsname'); post({cmd: 'save', name: n.value || S.name}); n.value = ''; }
      else if (d.load) post({cmd: 'load', name: d.load});
      else if (d.del) post({cmd: 'delete', name: d.del});
    });
    el.addEventListener('change', (e) => {
      const t = e.target, d = t.dataset;
      if (d.k && t.value !== '') post({cmd: 'set', key: d.k, value: +t.value});
      else if (d.dk) post({cmd: 'per_trial', key: d.dk, text: t.value});
      else if (d.x === 'biphasic') post({cmd: 'set', key: 'biphasic', value: t.checked});
      else if (d.t && t.value !== '') post({cmd: 'trials', [d.t]: +t.value});
      else if (d.a === 'offset' && t.value !== '') post({cmd: 'offset', value: +t.value});
      else if (t.closest('.dspts tbody')) {
        const pts = [...t.closest('tbody').rows].map((r) => [...r.querySelectorAll('input')].map((i) => +i.value));
        post({cmd: 'points', points: pts});
      }
    });
    el.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter' || !e.target.matches('input')) return;
      if (e.target.classList.contains('dsname')) q('[data-a=save]').click();
      else e.target.blur();                                   // blurring commits it (change)
    });
    new ResizeObserver(() => { if (pic && S) drawPic(); }).observe(q('.dspic'));
    poll();
    timer = setInterval(poll, 500);
  }
  return {mount, load: (name) => post({cmd: 'load', name}), poll};
})();
