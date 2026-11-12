/* Lessons for Argus simulation pages.

A lesson places questions on the page and keeps chosen parts of the page hidden, covered,
locked or with their numbers blurred until the questions before them are answered, so a
student works through the simulation one part at a time.

A page takes part by setting window.LESSON_ADAPTER before loading this file. The adapter
says which lessons the page has and what a lesson may use:
  lessons   lesson file names, in ../lessons/<name>.js
  values    { key: { label, unit, get() } }            numbers a question can read
  controls  { key: { label, unit, get(), set(v) } }    settings a question can set up
  afterSet  called after a question has set controls, to update the page
  regions   { key: { label, sel } }                    named parts of the page
  actions   { pause() }                                optional
Lesson files call ArgusLesson.define({...}). They are written with the editor: open the
page with ?edit (or press Edit lesson in Argus), right-click the page and choose
"Insert question here" or "Make this a region". Argus saves into the lessons folder; a
browser downloads the file, to be put in the lessons folder by hand.

Progress is kept in the browser (localStorage). Locking parts of the page steers
attention; it isn't security: anyone can read a lesson file. */
(() => {
  const A = window.LESSON_ADAPTER;
  if (!A || window.ArgusLesson) return;
  const DIR = A.lessonDir || '../lessons/';
  const params = new URLSearchParams(location.search);
  const ID_RE = /^[a-z0-9_-]{1,64}$/;
  const TYPES = { mc: 'Multiple choice', num: 'Number', predict: 'Predict, then check', goal: 'Reach a goal' };
  const HEADS = { mc: 'Check your understanding', num: 'Check your understanding', predict: 'Predict, then check', goal: 'Try it' };
  const MODES = {
    covered: 'Covered, with a note saying what opens it',
    hidden: 'Hidden completely',
    locked: 'Visible but can’t be used',
    values: 'Usable, but its numbers and charts are blurred',
  };

  const lessons = {};
  let L = null;                      // the lesson on the page
  let progress = {};
  let editing = false, editAllowed = params.has('edit'), allOpen = false, ready = false;
  let bridge = null;                 // Argus: saves lesson files (Qt web channel)
  const cardEl = new Map();          // question id -> card element
  let order = [];                    // questions in page order
  const wasAvailable = new Set();

  // ---------- small helpers ----------
  const mk = (tag, cls, html) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    e.setAttribute('data-lesson', '');
    return e;
  };
  const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const parseNum = s => parseFloat(String(s ?? '').trim().replace(/−/g, '-').replace(/^\+/, '').replace(/×\s*10\^?/, 'e'));
  const mag = a => a === 0 ? '0' : a >= 100 ? a.toFixed(0) : a >= 10 ? a.toFixed(1) : a >= 1 ? a.toFixed(2) : a.toPrecision(2);
  const signed = (v, unit) => !isFinite(v) ? '—' : (v > 0 ? '+' : v < 0 ? '−' : '') + mag(Math.abs(v)) + (unit ? ' ' + unit : '');
  const plain = (v, unit) => !isFinite(v) ? '—' : (v < 0 ? '−' : '') + mag(Math.abs(v)) + (unit ? ' ' + unit : '');
  const item = key => A.controls?.[key] || A.values?.[key];
  const get = key => { const c = item(key); try { return c ? +c.get() : NaN; } catch (e) { return NaN; } };
  const labelOf = key => item(key)?.label || key;
  // a value's name as HTML, with "V_m" as V<sub>m</sub>
  const labelHtml = key => esc(labelOf(key)).replace(/_([A-Za-z0-9∞]+)/g, '<sub>$1</sub>');
  const unitOf = key => item(key)?.unit || '';
  const verdict = ok => `<b class="${ok ? 'ok' : 'no'}">${ok ? 'Right.' : 'Not quite.'}</b> `;
  // An element's text, leaving out anything a lesson added and any math: math reads as TeX until
  // MathJax has drawn it and as symbols after, so it would make the same element look different.
  const textOf = el => {
    let s = '';
    const w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, { acceptNode: n => n.parentElement?.closest('[data-lesson],mjx-container') ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT });
    while (w.nextNode()) s += w.currentNode.nodeValue;
    return s.replace(/\\\([\s\S]*?\\\)|\\\[[\s\S]*?\\\]/g, ' ').trim().replace(/\s+/g, ' ');
  };
  const store = {
    load: () => { try { return JSON.parse(localStorage.getItem('argus.lesson.' + L.id + '.v1') || '{}') || {}; } catch (e) { return {}; } },
    save: () => { try { localStorage.setItem('argus.lesson.' + L.id + '.v1', JSON.stringify(progress)); } catch (e) { /* private window */ } },
  };

  // ---------- places on the page ----------
  // A place is a named region of the adapter, or a path of (tag, index among same-tag siblings)
  // from the nearest element with an id, plus the start of its text to find it again if the page changes.
  const own = e => e.hasAttribute('data-lesson');
  function placeOf(el) {
    for (const [k, r] of Object.entries(A.regions || {})) if (document.querySelector(r.sel) === el) return { region: k };
    const path = [];
    let e = el;
    while (e && e !== document.body && !e.id) {
      let n = 0;
      for (let s = e.parentElement.firstElementChild; s && s !== e; s = s.nextElementSibling) if (!own(s) && s.tagName === e.tagName) n++;
      path.unshift([e.tagName.toLowerCase(), n]);
      e = e.parentElement;
    }
    return { root: e && e.id ? '#' + e.id : 'body', path, text: textOf(el).slice(0, 60) };
  }
  function resolve(p) {
    if (!p) return null;
    if (p.region) { const r = A.regions?.[p.region]; return r ? document.querySelector(r.sel) : null; }
    let e = p.root === 'body' ? document.body : document.querySelector(p.root);
    for (const [tag, n] of p.path || []) {
      if (!e) break;
      let k = -1, found = null;
      for (const c of e.children) { if (own(c) || c.tagName.toLowerCase() !== tag) continue; if (++k === n) { found = c; break; } }
      e = found;
    }
    const start = (p.text || '').slice(0, 30);
    if (!start || (e && textOf(e).startsWith(start))) return e;
    // the page has changed since: look for an element of the same kind that starts the same way
    const tag = p.path?.length ? p.path[p.path.length - 1][0] : null;
    if (tag) for (const c of document.querySelectorAll(tag)) if (!c.closest('[data-lesson]') && textOf(c).startsWith(start)) return c;
    return e;
  }
  const describe = p => p?.region ? (A.regions[p.region]?.label || p.region) : (p?.text ? '“' + p.text.slice(0, 40) + (p.text.length > 40 ? '…' : '') + '”' : 'a part of the page');

  // ---------- styles ----------
  const css = document.createElement('style');
  css.textContent = `
.ls-group{display:flex;flex-direction:column;gap:10px;margin:12px 0}
.ls-card{border:1px solid var(--line);border-left:3px solid var(--accent);border-radius:0 8px 8px 0;background:color-mix(in srgb, var(--accent) 4%, var(--surface));padding:12px 14px;max-width:72ch;scroll-margin:80px}
.ls-card [hidden],.ls-off{display:none!important}
.ls-h{font-size:12px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:var(--accent);margin:0 0 4px}
.ls-q{margin:0 0 10px;font-size:14.5px}
.ls-opts{display:flex;flex-direction:column;gap:6px}
.ls-opts button{text-align:left;font-size:13.5px}
.ls-opts button.ok{border-color:var(--cl);background:color-mix(in srgb, var(--cl) 14%, var(--surface))}
.ls-opts button.no{border-color:var(--pos);background:color-mix(in srgb, var(--pos) 12%, var(--surface))}
.ls-row{display:flex;flex-wrap:wrap;align-items:center;gap:8px;font-size:13px;color:var(--muted)}
.ls-row input{width:110px;font:inherit;font-family:var(--font-mono);font-size:13px;padding:6px 8px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink);text-align:right}
.ls-err{font-size:13px;color:var(--pos);margin:6px 0 0}
.ls-err:empty{display:none}
.ls-fb{font-size:13.5px;margin:10px 0 0;padding:8px 10px;border-radius:6px;background:var(--surface);border:1px solid var(--line)}
.ls-fb b.ok{color:var(--cl)} .ls-fb b.no{color:var(--pos)}
.ls-live{font-family:var(--font-mono);font-size:13px;color:var(--ink);margin:8px 0 0}
@keyframes ls-pulse{0%{box-shadow:0 0 0 0 color-mix(in srgb, var(--accent) 55%, transparent)}100%{box-shadow:0 0 0 14px transparent}}
.ls-pulse{animation:ls-pulse 1.2s ease-out 2}
.ls-hidden{display:none!important}
.ls-covered{position:relative}
.ls-cover{position:absolute;inset:0;z-index:5;display:flex;align-items:flex-start;justify-content:center;text-align:center;padding:16px;border-radius:inherit;background:color-mix(in srgb, var(--surface) 90%, transparent);backdrop-filter:blur(6px);-webkit-backdrop-filter:blur(6px);font-size:14px;color:var(--muted)}
.ls-cover > div{position:sticky;top:30vh;max-width:46ch;padding:12px 16px;border:1px solid var(--line);border-radius:8px;background:var(--surface)}
.ls-cover b{color:var(--ink)}
.ls-locked{opacity:.45;filter:grayscale(.6)}
.ls-values output,.ls-values canvas,.ls-values .big,.ls-values [data-lesson-value]{filter:blur(7px)}
.ls-bar{position:fixed;right:16px;bottom:16px;z-index:40;display:flex;flex-wrap:wrap;align-items:center;gap:8px;max-width:calc(100vw - 32px);padding:8px 10px;border:1px solid var(--line);border-radius:10px;background:var(--surface);box-shadow:0 4px 18px rgba(0,0,0,.12);font-size:13px;color:var(--muted)}
.ls-bar b{color:var(--ink);font-weight:600}
.ls-bar button,.ls-bar select{font-size:12.5px;padding:4px 10px}
.ls-bar select{font:inherit;font-size:12.5px;border:1px solid var(--line);border-radius:7px;background:var(--surface);color:var(--ink)}
html.ls-editing body{padding-right:420px}
.ls-edit-region{outline:2px dashed color-mix(in srgb, var(--accent) 70%, transparent);outline-offset:3px;position:relative}
.ls-edit-region::before{content:attr(data-ls-label);position:absolute;top:-11px;left:8px;z-index:6;font-size:11px;font-weight:600;padding:1px 6px;border-radius:4px;background:var(--accent);color:var(--surface)}
.ls-pick{outline:2px solid var(--na)!important;outline-offset:2px}
.ls-menu{position:fixed;z-index:60;min-width:220px;padding:4px;border:1px solid var(--line);border-radius:8px;background:var(--surface);box-shadow:0 8px 24px rgba(0,0,0,.18)}
.ls-menu button{display:block;width:100%;text-align:left;border:0;border-radius:5px;font-size:13.5px;padding:7px 10px;background:transparent}
.ls-menu button:hover{background:color-mix(in srgb, var(--accent) 12%, var(--surface))}
.ls-menu hr{border:0;border-top:1px solid var(--line);margin:4px 0}
.ls-panel{position:fixed;top:0;right:0;bottom:0;width:400px;z-index:50;overflow:auto;padding:14px 16px 40px;border-left:1px solid var(--line);background:var(--surface);color:var(--ink);font-size:13.5px;box-shadow:-6px 0 20px rgba(0,0,0,.08)}
.ls-panel h3{font-size:15px;margin:16px 0 6px}
.ls-panel h3:first-child{margin-top:0}
.ls-panel .hint{font-size:12.5px;color:var(--muted);margin:4px 0}
.ls-panel label.f{display:block;font-size:12px;font-weight:600;color:var(--muted);margin:10px 0 3px}
.ls-panel input[type=text],.ls-panel textarea,.ls-panel select{width:100%;font:inherit;font-size:13px;padding:6px 8px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink)}
.ls-panel textarea{min-height:64px;resize:vertical}
.ls-panel .rowf{display:flex;gap:6px;align-items:center}
.ls-panel .rowf > *{flex:1;min-width:0}
.ls-panel .rowf > button{flex:none}
.ls-panel .checks{max-height:160px;overflow:auto;border:1px solid var(--line);border-radius:6px;padding:6px 8px}
.ls-panel .checks label{display:flex;gap:6px;align-items:baseline;font-size:12.5px;margin:2px 0}
.ls-panel .list{display:flex;flex-direction:column;gap:4px}
.ls-panel .li{display:flex;gap:6px;align-items:center;padding:5px 6px;border:1px solid var(--line);border-radius:6px}
.ls-panel .li span{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ls-panel .li button{font-size:12px;padding:3px 8px}
.ls-panel .btns{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.ls-panel .btns button{font-size:12.5px;padding:5px 10px}
.ls-panel .ls-status{font-size:12.5px;color:var(--muted);min-height:1em;margin-top:6px}
.ls-panel .warn{color:var(--pos)}
`;
  document.head.appendChild(css);

  // ---------- cards ----------
  const watchers = new Set();
  setInterval(() => watchers.forEach(w => w()), 200);
  const markDone = (q, rec) => { progress[q.id] = Object.assign({ done: true }, rec); store.save(); refresh(); };
  function applySetup(setup) {
    for (const [k, v] of Object.entries(setup || {})) { const c = A.controls?.[k]; if (c) try { c.set(v); } catch (e) { /* control gone */ } }
    A.afterSet?.();
  }
  const met = d => { if (!d?.key) return false; const v = get(d.key); return isFinite(v) && (d.min == null || v >= d.min) && (d.max == null || v <= d.max); };

  function card(q) {
    const c = mk('div', 'ls-card');
    c.setAttribute('data-lesson-card', q.id);
    c.innerHTML = `<p class="ls-h"></p><p class="ls-q">${q.q || ''}</p>`;
    const fb = mk('div');
    const show = html => { fb.innerHTML = `<p class="ls-fb">${html}</p>`; };
    const saved = progress[q.id];
    const explain = q.explain || '';

    if (q.type === 'mc') {
      const opts = mk('div', 'ls-opts');
      const pick = (i, keep) => {
        [...opts.children].forEach(b => b.classList.remove('ok', 'no'));
        const ok = !!q.options[i]?.ok;
        opts.children[i]?.classList.add(ok ? 'ok' : 'no');
        show(verdict(ok) + explain);
        if (keep) markDone(q, { choice: i, ok });
      };
      (q.options || []).forEach((o, i) => {
        const b = mk('button', '', o.t); b.type = 'button';
        b.addEventListener('click', () => pick(i, true));
        opts.appendChild(b);
      });
      c.appendChild(opts);
      if (saved?.choice != null) pick(saved.choice, false);
    } else if (q.type === 'num') {
      const row = mk('div', 'ls-row');
      row.innerHTML = `<input type="text" inputmode="decimal" placeholder="${esc(q.placeholder)}" aria-label="Your answer">${q.unit ? `<span>${esc(q.unit)}</span>` : ''}<button type="button">Check</button>`;
      const inp = row.querySelector('input'), err = mk('p', 'ls-err');
      const check = keep => {
        const v = parseNum(inp.value);
        if (inp.value.trim() === '' || !isFinite(v)) { err.textContent = 'Enter a number first'; return; }
        const ok = Math.abs(v - (+q.answer)) <= (+q.tol || 0);
        show(verdict(ok) + explain);
        if (keep) markDone(q, { value: inp.value, ok });
      };
      inp.addEventListener('input', () => { err.textContent = ''; });
      inp.addEventListener('keydown', e => { if (e.key === 'Enter') check(true); });
      row.querySelector('button').addEventListener('click', () => check(true));
      c.append(row, err);
      if (saved?.value != null) { inp.value = saved.value; check(false); }
    } else {                                // predict or goal: works through the page's own controls
      const isPredict = q.type === 'predict';
      const unit = q.unit || unitOf(q.measure?.key);
      const row = mk('div', 'ls-row');
      row.innerHTML = (isPredict
        ? `<input type="text" inputmode="decimal" placeholder="${esc(q.placeholder)}" aria-label="Your prediction"><span>${esc(unit)}</span><button type="button">Lock in prediction</button>`
        : '<button type="button">Set it up</button>') + '<button type="button" class="ls-go" hidden></button>';
      const inp = row.querySelector('input'), go = row.querySelector('.ls-go'), begin = row.querySelector('button');
      const err = mk('p', 'ls-err'), live = mk('p', 'ls-live'); live.hidden = true;
      const goEl = () => q.goTo ? document.querySelector(A.regions?.[q.goTo]?.sel || '') : null;
      if (goEl()) go.textContent = 'Show the ' + (A.regions[q.goTo].label || 'part of the page');
      go.addEventListener('click', () => goEl()?.scrollIntoView({ behavior: 'smooth', block: 'start' }));
      let pred = null, start = 0;
      const finish = (got, keep) => {
        if (isPredict) {
          const ok = Math.abs(got - pred) <= (+q.tol || 0);
          show(`You predicted <b>${signed(pred, unit)}</b>; the page gives <b>${signed(got, unit)}</b>. ` + verdict(ok) + explain);
          if (keep) markDone(q, { pred, got, ok });
        } else {
          const result = keep ? (q.report || []).map(k => `${labelHtml(k)} = ${plain(get(k), unitOf(k))}`).join(', ') : saved?.result;
          show('<b class="ok">Done.</b> ' + (result ? 'You got there with ' + result + '. ' : '') + explain);
          if (keep) markDone(q, { result, ok: true });
        }
      };
      const watch = () => {
        if (!c.isConnected) { watchers.delete(watch); return; }
        const shown = [q.done?.key, q.measure?.key].filter((k, i, a) => k && a.indexOf(k) === i);
        live.innerHTML = 'Now: ' + shown.map(k => `${labelHtml(k)} = ${esc(plain(get(k), unitOf(k)))}`).join(' · ');
        if (!met(q.done)) return;
        watchers.delete(watch); live.hidden = true; go.hidden = true;
        finish(isPredict ? get(q.measure?.key) - (q.measure?.relative ? start : 0) : 0, true);
      };
      const begun = () => {
        if (isPredict) {
          const v = parseNum(inp.value);
          if (inp.value.trim() === '' || !isFinite(v)) { err.textContent = 'Enter a prediction first'; return; }
          pred = v; inp.disabled = true;
        }
        begin.disabled = true; begin.textContent = isPredict ? 'Locked in' : 'Set up';
        applySetup(q.setup);
        start = get(q.measure?.key);
        show(q.task || 'Now make the change on the page.');
        live.hidden = false; go.hidden = !goEl();
        watchers.add(watch); watch();
      };
      inp?.addEventListener('input', () => { err.textContent = ''; });
      inp?.addEventListener('keydown', e => { if (e.key === 'Enter') begun(); });
      begin.addEventListener('click', begun);
      c.append(row, err, live);
      if (saved?.done) { row.hidden = true; if (isPredict) { pred = saved.pred; finish(saved.got, false); } else finish(0, false); }
    }
    c.appendChild(fb);
    return c;
  }

  // ---------- putting the lesson on the page ----------
  function render(fromForm) {
    document.querySelectorAll('[data-lesson-group]').forEach(e => e.remove());
    cardEl.clear();
    if (!L) return;
    const groups = new Map();
    for (const q of L.questions) {
      const at = resolve(q.anchor);
      if (!at) continue;
      let g = groups.get(at);
      if (!g) { g = mk('div', 'ls-group'); g.setAttribute('data-lesson-group', ''); at.after(g); groups.set(at, g); }
      const c = card(q);
      g.appendChild(c); cardEl.set(q.id, c);
    }
    const inPage = [...document.querySelectorAll('[data-lesson-card]')].map(e => e.getAttribute('data-lesson-card'));
    order = inPage.map(id => L.questions.find(q => q.id === id)).filter(Boolean);
    order.forEach((q, i) => { const h = cardEl.get(q.id)?.querySelector('.ls-h'); if (h) h.textContent = `Question ${i + 1} · ${HEADS[q.type] || ''}`; });
    refresh(fromForm);
  }
  const numberOf = id => order.findIndex(q => q.id === id) + 1;
  const isOpen = r => {
    if (allOpen) return true;
    const ids = (r.until?.ids || []).filter(id => L.questions.some(q => q.id === id));
    return ids.every(id => r.until?.kind === 'correct' ? progress[id]?.ok : progress[id]?.done);
  };
  const listNums = ids => {
    const n = ids.map(numberOf).filter(n => n > 0).sort((a, b) => a - b);
    return n.length === 1 ? 'question ' + n[0] : 'questions ' + n.slice(0, -1).join(', ') + ' and ' + n[n.length - 1];
  };
  function clearRegions() {
    document.querySelectorAll('.ls-hidden,.ls-covered,.ls-locked,.ls-values,.ls-edit-region').forEach(e => {
      e.classList.remove('ls-hidden', 'ls-covered', 'ls-locked', 'ls-values', 'ls-edit-region');
      e.inert = false; e.removeAttribute('data-ls-label');
    });
    document.querySelectorAll('.ls-cover').forEach(e => e.remove());
  }
  // fromForm: the change came from the editor's form, which stays as it is while the teacher types
  function refresh(fromForm) {
    if (!L) return;
    const done = id => !!progress[id]?.done;
    for (const q of L.questions) {
      const c = cardEl.get(q.id); if (!c) continue;
      const avail = editing || (q.after || []).every(id => done(id) || !L.questions.some(o => o.id === id));
      c.classList.toggle('ls-off', !avail);
      if (!avail) wasAvailable.delete(q.id);   // so it gets attention again when it comes back
      if (!editing && avail && !done(q.id) && !wasAvailable.has(q.id)) {
        wasAvailable.add(q.id);
        if (ready) attend(q, c);
      }
    }
    clearRegions();
    for (const r of L.regions) {
      const el = resolve(r.target); if (!el) continue;
      if (editing) { el.classList.add('ls-edit-region'); el.setAttribute('data-ls-label', r.label || 'Region'); continue; }
      if (isOpen(r)) continue;
      if (r.mode === 'hidden') el.classList.add('ls-hidden');
      else if (r.mode === 'locked') { el.classList.add('ls-locked'); el.inert = true; }
      else if (r.mode === 'values') el.classList.add('ls-values');
      else {
        el.classList.add('ls-covered'); el.inert = true;
        const ids = (r.until?.ids || []).filter(id => numberOf(id) > 0);
        const cover = mk('div', 'ls-cover', `<div><b>${esc(r.label || 'This part')}</b> opens once you’ve ${r.until?.kind === 'correct' ? 'correctly ' : ''}answered ${ids.length ? listNums(ids) : 'the questions before it'}.</div>`);
        el.appendChild(cover);
      }
    }
    drawBar();
    if (editing && !fromForm) drawPanel();
  }
  function attend(q, c) {
    if (q.pause) A.actions?.pause?.();
    if (q.focus) { c.scrollIntoView({ behavior: 'smooth', block: 'center' }); pulse(c); }
  }
  const pulse = c => { c.classList.remove('ls-pulse'); void c.offsetWidth; c.classList.add('ls-pulse'); };

  // ---------- the lesson bar ----------
  let bar = null;
  function drawBar() {
    // a lesson with no questions shows no bar, except where it can be edited
    if (!L || editing || (!L.questions.length && !editAllowed)) { bar?.remove(); bar = null; return; }
    if (!bar) { bar = mk('div', 'ls-bar'); document.body.appendChild(bar); }
    const n = order.length, d = order.filter(q => progress[q.id]?.done).length;
    const many = Object.keys(lessons).length > 1;
    bar.innerHTML = (many ? `<select aria-label="Lesson">${Object.values(lessons).map(l => `<option value="${esc(l.id)}"${l.id === L.id ? ' selected' : ''}>${esc(l.title || l.id)}</option>`).join('')}</select>` : `<span><b>${esc(L.title || 'Lesson')}</b></span>`)
      + `<span>${d} of ${n} answered</span>`
      + (d < n ? '<button type="button" data-a="next">Next question</button>' : '')
      + '<button type="button" data-a="reset">Reset</button>'
      + (editAllowed ? `<button type="button" data-a="open">${allOpen ? 'Lock again' : 'Unlock all'}</button><button type="button" data-a="edit">Edit lesson</button>` : '');
    bar.querySelector('select')?.addEventListener('change', e => use(e.target.value));
    bar.querySelectorAll('button').forEach(b => b.addEventListener('click', () => {
      const a = b.dataset.a;
      if (a === 'next') {
        const q = order.find(q => !progress[q.id]?.done && !cardEl.get(q.id)?.classList.contains('ls-off'));
        const c = q && cardEl.get(q.id);
        if (c) { c.scrollIntoView({ behavior: 'smooth', block: 'center' }); pulse(c); }
      } else if (a === 'reset') {
        if (!confirm('Clear your answers to this lesson and start it again?')) return;
        progress = {}; store.save(); wasAvailable.clear(); render();
      } else if (a === 'open') { allOpen = !allOpen; refresh(); }
      else if (a === 'edit') setEdit(true);
    }));
  }

  // ---------- loading lessons ----------
  function use(id) {
    L = lessons[id]; if (!L) return;
    L.questions = L.questions || []; L.regions = L.regions || [];
    progress = store.load(); wasAvailable.clear(); ready = false;
    render();
    ready = true;
  }
  function blank(id) {
    return { id, title: 'New lesson', sim: A.sim || '', questions: [], regions: [] };
  }
  function start() {
    const want = params.get('lesson');
    const id = (want && lessons[want]) ? want : (A.lessons || []).find(n => lessons[n]);
    if (id) use(id);
    else if (editAllowed) { const n = want && ID_RE.test(want) ? want : (A.lessons?.[0] || A.sim || 'lesson'); lessons[n] = blank(n); use(n); }
    if (editAllowed && params.has('edit')) setEdit(true);
  }
  let pending = (A.lessons || []).length;
  if (!pending) start();
  (A.lessons || []).forEach(name => {
    const s = document.createElement('script');
    s.src = DIR + name + '.js?t=' + Date.now();   // always the saved version, not a cached one
    s.onload = s.onerror = () => { if (--pending === 0) start(); };
    document.head.appendChild(s);
  });
  // Argus: the web channel object that saves lesson files
  if (window.qt?.webChannelTransport && window.QWebChannel) {
    try { new window.QWebChannel(window.qt.webChannelTransport, ch => { bridge = ch.objects.argusLessons || null; }); } catch (e) { bridge = null; }
  }

  // ---------- editor ----------
  let panel = null, view = { kind: 'list' }, menu = null, picked = null, status = '';
  function setEdit(on) {
    editAllowed = editAllowed || on;
    if (on && !L) { const n = A.lessons?.[0] || A.sim || 'lesson'; lessons[n] = lessons[n] || blank(n); use(n); }
    editing = on;
    try { bridge?.setEditing?.(on); } catch (e) { /* Argus older than the lesson editor */ }
    document.documentElement.classList.toggle('ls-editing', on);
    if (!on) { panel?.remove(); panel = null; view = { kind: 'list' }; closeMenu(); }
    render();
  }
  const newId = p => { let n = 1; const used = new Set([...L.questions, ...L.regions].map(x => x.id)); while (used.has(p + n)) n++; return p + n; };
  function sortQuestions() {     // keep the list in page order; questions at the same place keep their order
    const at = new Map(L.questions.map((q, i) => [q, [resolve(q.anchor), i]]));
    L.questions.sort((a, b) => {
      const [ea, ia] = at.get(a), [eb, ib] = at.get(b);
      if (!ea || !eb || ea === eb) return (!ea) - (!eb) || ia - ib;
      return ea.compareDocumentPosition(eb) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1;
    });
  }
  let renderTimer = 0;
  const changed = () => { clearTimeout(renderTimer); renderTimer = setTimeout(() => render(true), 250); };

  // right-click: insert a question or make a region at the element under the pointer
  const INLINE = /^(SPAN|B|I|EM|STRONG|SUB|SUP|A|LABEL|OUTPUT|INPUT|SELECT|OPTION|BUTTON|SMALL|CODE|SVG|PATH|RECT|CIRCLE|LINE|G|TEXT|USE)$/;
  function blockOf(el) {
    while (el && el !== document.body && (INLINE.test(el.tagName.toUpperCase()) || el.tagName.toUpperCase().startsWith('MJX-') || el.closest?.('mjx-container') && el.tagName.toUpperCase() !== 'MJX-CONTAINER' || own(el) && !el.hasAttribute('data-lesson-card'))) el = el.parentElement;
    if (el?.tagName?.toUpperCase() === 'MJX-CONTAINER') el = el.parentElement;
    return el && el !== document.body ? el : null;
  }
  function closeMenu() { menu?.remove(); menu = null; picked?.classList.remove('ls-pick'); picked = null; }
  document.addEventListener('contextmenu', e => {
    if (!editing || e.target.closest('.ls-panel,.ls-menu,.ls-bar')) return;
    e.preventDefault(); closeMenu();
    const cardHit = e.target.closest('[data-lesson-card]');
    const el = cardHit ? null : blockOf(e.target);
    const items = [];
    if (cardHit) {
      const q = L.questions.find(q => q.id === cardHit.getAttribute('data-lesson-card'));
      if (q) {
        items.push([`Edit question ${numberOf(q.id)}`, () => openQuestion(q.id)]);
        items.push(['Insert question after this one', () => insertQuestion(q.anchor, q.id)]);
        items.push([`Delete question ${numberOf(q.id)}`, () => deleteQuestion(q.id)]);
      }
    }
    if (el) {
      picked = el; el.classList.add('ls-pick');
      items.push(['Insert question here', () => insertQuestion(placeOf(el))]);
      items.push(null);
      items.push(['Make this a region…', () => makeRegion(el)]);
      // bigger parts: the box around it, and the page's named parts that contain it
      const box = el.parentElement;
      const named = Object.values(A.regions || {}).map(r => [r, document.querySelector(r.sel)]).filter(([, n]) => n && n.contains(el));
      if (box && box !== document.body && !named.some(([, n]) => n === box)) items.push(['Make the box around it a region…', () => makeRegion(box)]);
      named.forEach(([r, n]) => items.push([`Make “${r.label}” a region…`, () => makeRegion(n, r.label)]));
    }
    const inRegion = L.regions.filter(r => { const t = resolve(r.target); return t && t.contains(e.target); });
    if (inRegion.length) items.push(null);
    inRegion.forEach(r => items.push([`Edit region “${r.label}”`, () => openRegion(r.id)]));
    if (!items.length) return;
    menu = mk('div', 'ls-menu');
    items.forEach(it => {
      if (!it) { menu.appendChild(mk('hr')); return; }
      const b = mk('button', '', esc(it[0])); b.type = 'button';
      b.addEventListener('click', () => { closeMenu(); it[1](); });
      menu.appendChild(b);
    });
    document.body.appendChild(menu);
    const w = menu.offsetWidth, h = menu.offsetHeight;
    menu.style.left = Math.min(e.clientX, innerWidth - w - 8) + 'px';
    menu.style.top = Math.min(e.clientY, innerHeight - h - 8) + 'px';
  });
  document.addEventListener('click', e => { if (menu && !e.target.closest('.ls-menu')) closeMenu(); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeMenu(); });

  function insertQuestion(anchor, afterId) {
    const q = { id: newId('q'), type: 'mc', anchor, q: 'New question', options: [{ t: 'First option', ok: true }, { t: 'Second option' }], explain: '' };
    const i = afterId ? L.questions.findIndex(o => o.id === afterId) + 1 : L.questions.length;
    L.questions.splice(i, 0, q);
    sortQuestions(); render(); openQuestion(q.id);
  }
  function deleteQuestion(id) {
    if (!confirm(`Delete question ${numberOf(id)}?`)) return;
    L.questions = L.questions.filter(q => q.id !== id);
    L.questions.forEach(q => { if (q.after) q.after = q.after.filter(a => a !== id); });
    L.regions.forEach(r => { if (r.until?.ids) r.until.ids = r.until.ids.filter(a => a !== id); });
    view = { kind: 'list' }; render();
  }
  // A new region opens after the questions above it on the page, until the teacher changes that.
  function makeRegion(el, name) {
    const before = order.filter(q => { const c = cardEl.get(q.id); return c && (c.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING) && !el.contains(c); });
    const label = name || (textOf(el).slice(0, 32) || el.tagName.toLowerCase()).replace(/\s+\S*$/, '') || 'Region';
    const r = { id: newId('r'), label, target: placeOf(el), mode: 'covered', until: { kind: 'answered', ids: before.map(q => q.id) } };
    L.regions.push(r); render(); openRegion(r.id);
  }
  const openQuestion = id => { view = { kind: 'q', id }; drawPanel(); const c = cardEl.get(id); c?.scrollIntoView({ behavior: 'smooth', block: 'center' }); };
  const openRegion = id => { view = { kind: 'r', id }; drawPanel(); };

  // ---------- the editor panel ----------
  const field = (label, control, hint) => {
    const w = mk('div');
    w.append(mk('label', 'f', esc(label)), control);
    if (hint) w.appendChild(mk('p', 'hint', hint));
    return w;
  };
  const input = (value, on, type = 'text') => { const i = mk('input'); i.type = type; i.value = value ?? ''; i.addEventListener('input', () => on(i.value)); return i; };
  const area = (value, on) => { const t = mk('textarea'); t.value = value ?? ''; t.addEventListener('input', () => on(t.value)); return t; };
  const select = (opts, value, on) => {
    const s = mk('select');
    s.innerHTML = opts.map(([v, t]) => `<option value="${esc(v)}"${String(v) === String(value) ? ' selected' : ''}>${esc(t)}</option>`).join('');
    s.addEventListener('change', () => on(s.value));
    return s;
  };
  const button = (text, on) => { const b = mk('button', '', esc(text)); b.type = 'button'; b.addEventListener('click', on); return b; };
  const numOrNull = v => { const n = parseNum(v); return isFinite(n) ? n : null; };
  const keyOpts = (which, blankText) => {
    const o = blankText ? [['', blankText]] : [];
    if (which !== 'values') for (const [k, c] of Object.entries(A.controls || {})) o.push([k, c.label || k]);
    if (which !== 'controls') for (const [k, c] of Object.entries(A.values || {})) o.push([k, c.label || k]);
    return o;
  };
  const qTitle = q => `${numberOf(q.id) || '?'}. ${(q.q || '').replace(/<[^>]+>/g, '').slice(0, 60)}`;
  const checks = (list, chosen, on) => {
    const box = mk('div', 'checks');
    if (!list.length) box.appendChild(mk('p', 'hint', 'No questions yet.'));
    list.forEach(q => {
      const l = mk('label'), c = mk('input'); c.type = 'checkbox'; c.checked = chosen.includes(q.id);
      c.addEventListener('change', () => on(q.id, c.checked));
      l.append(c, mk('span', '', esc(qTitle(q)))); box.appendChild(l);
    });
    return box;
  };

  function drawPanel() {
    if (!editing) return;
    if (!panel) { panel = mk('div', 'ls-panel'); document.body.appendChild(panel); }
    const keep = panel.scrollTop;
    panel.innerHTML = '';
    if (view.kind === 'q' && L.questions.some(q => q.id === view.id)) questionForm(L.questions.find(q => q.id === view.id));
    else if (view.kind === 'r' && L.regions.some(r => r.id === view.id)) regionForm(L.regions.find(r => r.id === view.id));
    else lessonView();
    panel.scrollTop = keep;
  }
  const tag = (el, name) => { el.dataset.f = name; return el; };   // names a form field (for tests and debugging)

  function lessonView() {
    panel.append(mk('h3', '', 'Lesson editor'),
      mk('p', 'hint', 'Right-click the page to insert a question or to make a part of the page a region that stays hidden or locked until chosen questions are answered.'));
    panel.append(field('Title', tag(input(L.title, v => { L.title = v; }), 'title')));
    const idIn = tag(input(L.id, v => { if (ID_RE.test(v)) { delete lessons[L.id]; L.id = v; lessons[v] = L; } }), 'id');
    panel.append(field('File name', idIn, `Saved as lessons/${esc(L.id)}.js. Lower-case letters, digits, - and _ only. Add it to the page’s lesson list to show it.`));
    const st = mk('p', 'ls-status', esc(status));
    panel.append(mk('div', 'btns'));
    panel.lastChild.append(
      button(bridge ? 'Save' : 'Download lesson file', save),
      button('Preview as a student', () => setEdit(false)),
      button('Reset my answers', () => { progress = {}; store.save(); render(); }));
    panel.append(st);

    panel.append(mk('h3', '', `Questions (${L.questions.length})`));
    const ql = mk('div', 'list');
    if (!L.questions.length) ql.appendChild(mk('p', 'hint', 'None yet. Right-click a paragraph or a part of the simulation and choose Insert question here.'));
    [...L.questions].sort((a, b) => (numberOf(a.id) || 1e9) - (numberOf(b.id) || 1e9)).forEach(q => {
      const li = mk('div', 'li');
      const placed = numberOf(q.id) > 0;
      li.append(mk('span', placed ? '' : 'warn', esc((placed ? '' : '(its place on the page is gone) ') + qTitle(q))), button('Edit', () => openQuestion(q.id)), button('×', () => deleteQuestion(q.id)));
      ql.appendChild(li);
    });
    panel.append(ql);

    panel.append(mk('h3', '', `Regions (${L.regions.length})`));
    const rl = mk('div', 'list');
    if (!L.regions.length) rl.appendChild(mk('p', 'hint', 'None yet. Right-click a part of the page and choose Make this a region.'));
    L.regions.forEach(r => {
      const li = mk('div', 'li');
      const ids = r.until?.ids || [];
      li.append(mk('span', resolve(r.target) ? '' : 'warn', esc(`${r.label} · ${r.mode} · ${ids.length ? 'opens after ' + listNums(ids) : 'always open'}`)),
        button('Edit', () => openRegion(r.id)),
        button('×', () => { if (confirm(`Remove the region “${r.label}”?`)) { L.regions = L.regions.filter(x => x !== r); render(); } }));
      rl.appendChild(li);
    });
    panel.append(rl);
    panel.append(mk('div', 'btns'));
    panel.lastChild.append(button('Done editing', () => setEdit(false)));
  }

  function questionForm(q) {
    const back = button('← All questions and regions', () => { view = { kind: 'list' }; drawPanel(); });
    panel.append(back, mk('h3', '', `Question ${numberOf(q.id) || ''}`), mk('p', 'hint', 'Placed after ' + esc(describe(q.anchor)) + '. Changes show on the page as you type.'));
    const set = (k, v) => { q[k] = v; changed(); };
    panel.append(field('Kind', select(Object.entries(TYPES), q.type, v => {
      q.type = v;
      if (v === 'mc' && !q.options) q.options = [{ t: 'First option', ok: true }, { t: 'Second option' }];
      if ((v === 'predict' || v === 'goal') && !q.done) q.done = { key: '', min: null, max: null };
      if (v === 'predict' && !q.measure) q.measure = { key: '', relative: false };
      render(); drawPanel();
    })));
    panel.append(field('Question', tag(area(q.q, v => set('q', v)), 'q'), 'HTML is allowed, e.g. V<sub>m</sub>.'));

    if (q.type === 'mc') {
      const lines = (q.options || []).map(o => (o.ok ? '* ' : '') + o.t).join('\n');
      panel.append(field('Options', tag(area(lines, v => set('options', v.split('\n').map(s => s.trim()).filter(Boolean).map(s => s.startsWith('*') ? { t: s.slice(1).trim(), ok: true } : { t: s }))), 'opts'),
        'One option per line. Start the right one with *.'));
    }
    if (q.type === 'num') {
      const r = mk('div', 'rowf');
      r.append(tag(input(q.answer, v => set('answer', numOrNull(v))), 'ans'), tag(input(q.tol, v => set('tol', numOrNull(v))), 'tol'), tag(input(q.unit, v => set('unit', v)), 'unit'));
      panel.append(field('Answer · tolerance (±) · unit', r));
      panel.append(field('Placeholder', tag(input(q.placeholder, v => set('placeholder', v)), 'ph'), 'An example shown in the empty answer box.'));
    }
    if (q.type === 'predict' || q.type === 'goal') {
      // the state the page is put in when the student starts
      panel.append(mk('label', 'f', 'Set up the page first'));
      const rows = mk('div', 'list');
      const entries = Object.entries(q.setup || {});
      const writeSetup = list => { q.setup = Object.fromEntries(list.filter(([k]) => k)); changed(); };
      entries.forEach(([k, v], i) => {
        const r = mk('div', 'rowf');
        r.append(select(keyOpts('controls'), k, nk => { entries[i][0] = nk; writeSetup(entries); }),
          tag(input(v, nv => { const n = numOrNull(nv); entries[i][1] = n ?? nv; writeSetup(entries); }), 'setup' + i),
          button('×', () => { entries.splice(i, 1); writeSetup(entries); drawPanel(); }));
        rows.appendChild(r);
      });
      panel.append(rows);
      panel.append(mk('div', 'btns'));
      panel.lastChild.append(
        button('+ Add a setting', () => { const k = Object.keys(A.controls || {}).find(k => !(k in (q.setup || {}))); if (k) { q.setup = Object.assign(q.setup || {}, { [k]: get(k) }); changed(); drawPanel(); } }),
        button('Fill in the current values', () => { for (const k of Object.keys(q.setup || {})) q.setup[k] = typeof A.controls?.[k]?.get() === 'string' ? A.controls[k].get() : +(+get(k)).toPrecision(4); changed(); drawPanel(); }));
      panel.append(mk('p', 'hint', 'Set the page the way the question should start, then press Fill in the current values.'));
      panel.append(field('What the student is asked to do', tag(area(q.task, v => set('task', v)), 'task')));
      const d = q.done || (q.done = { key: '', min: null, max: null });
      const dr = mk('div', 'rowf');
      dr.append(select(keyOpts('all', 'Choose…'), d.key, v => { d.key = v; changed(); }), tag(input(d.min, v => { d.min = numOrNull(v); changed(); }), 'dmin'), tag(input(d.max, v => { d.max = numOrNull(v); changed(); }), 'dmax'));
      panel.append(field('Done when · between · and', dr, 'Finished when this setting or value is in the range (leave an end blank for no limit).'));
      panel.append(field('Show this part of the page', select([['', 'No button'], ...Object.entries(A.regions || {}).map(([k, r]) => [k, r.label || k])], q.goTo || '', v => set('goTo', v || undefined))));
    }
    if (q.type === 'predict') {
      const m = q.measure || (q.measure = { key: '', relative: false });
      const mr = mk('div', 'rowf');
      const rel = mk('label'); const cb = mk('input'); cb.type = 'checkbox'; cb.checked = !!m.relative; cb.addEventListener('change', () => { m.relative = cb.checked; changed(); });
      rel.append(cb, document.createTextNode(' change from the start'));
      mr.append(select(keyOpts('all', 'Choose…'), m.key, v => { m.key = v; changed(); }), rel);
      panel.append(field('Compare the prediction with', mr));
      const r = mk('div', 'rowf');
      r.append(tag(input(q.tol, v => set('tol', numOrNull(v))), 'tol'), tag(input(q.unit, v => set('unit', v)), 'unit'), tag(input(q.placeholder, v => set('placeholder', v)), 'ph'));
      panel.append(field('Tolerance (±) · unit · placeholder', r));
    }
    if (q.type === 'goal') {
      const box = mk('div', 'checks');
      for (const [k, c] of Object.entries(A.controls || {})) {
        const l = mk('label'), cb = mk('input'); cb.type = 'checkbox'; cb.checked = (q.report || []).includes(k);
        cb.addEventListener('change', () => { q.report = Object.keys(A.controls).filter(x => x === k ? cb.checked : (q.report || []).includes(x)); changed(); });
        l.append(cb, mk('span', '', esc(c.label || k))); box.appendChild(l);
      }
      panel.append(field('When done, tell the student these settings', box));
    }
    panel.append(field('Explanation', tag(area(q.explain, v => set('explain', v)), 'explain'), 'Shown after the student answers, right or wrong.'));

    panel.append(mk('h3', '', 'Steering attention'));
    const others = order.filter(o => o.id !== q.id);
    panel.append(field('Show this question only after these are answered', checks(others, q.after || [], (id, on) => {
      q.after = on ? [...new Set([...(q.after || []), id])] : (q.after || []).filter(x => x !== id);
      if (!q.after.length) delete q.after;
      changed();
    })));
    const flag = (k, text) => {
      const l = mk('label'), cb = mk('input'); cb.type = 'checkbox'; cb.checked = !!q[k];
      cb.addEventListener('change', () => { if (cb.checked) q[k] = true; else delete q[k]; changed(); });
      l.append(cb, document.createTextNode(' ' + text)); l.style.display = 'block'; l.style.marginTop = '6px';
      return l;
    };
    panel.append(flag('focus', 'Scroll to it and highlight it when it appears'));
    if (A.actions?.pause) panel.append(flag('pause', 'Pause the simulation when it appears'));
    panel.append(mk('div', 'btns'));
    panel.lastChild.append(button('Delete question', () => deleteQuestion(q.id)));
  }

  function regionForm(r) {
    panel.append(button('← All questions and regions', () => { view = { kind: 'list' }; drawPanel(); }),
      mk('h3', '', 'Region'), mk('p', 'hint', 'The part of the page at ' + esc(describe(r.target)) + '.' + (resolve(r.target) ? '' : ' <span class="warn">It can’t be found on the page any more.</span>')));
    panel.append(field('Name', tag(input(r.label, v => { r.label = v; changed(); }), 'label'), 'Shown to students on the cover.'));
    panel.append(field('Until it opens', select(Object.entries(MODES), r.mode, v => { r.mode = v; changed(); })));
    const u = r.until || (r.until = { kind: 'answered', ids: [] });
    panel.append(field('It opens when these questions are', select([['answered', 'answered'], ['correct', 'answered correctly']], u.kind, v => { u.kind = v; changed(); })));
    panel.append(checks(order, u.ids || [], (id, on) => { u.ids = on ? [...new Set([...(u.ids || []), id])] : (u.ids || []).filter(x => x !== id); changed(); }));
    panel.append(mk('p', 'hint', 'With none ticked the region is always open. Use Preview as a student to see it locked.'));
    panel.append(mk('div', 'btns'));
    panel.lastChild.append(button('Remove region', () => { if (confirm(`Remove the region “${r.label}”?`)) { L.regions = L.regions.filter(x => x !== r); view = { kind: 'list' }; render(); } }));
  }

  function fileText() {
    const clean = JSON.parse(JSON.stringify(L));   // drops undefined fields
    return `// Argus lesson "${(L.title || L.id).replace(/[\r\n]/g, ' ')}". Edit it on its simulation page: open the page with ?edit,\n// or press Edit lesson in Argus. See web/lesson.js for the format.\nArgusLesson.define(${JSON.stringify(clean, null, 2)});\n`;
  }
  function save() {
    if (!ID_RE.test(L.id)) { status = 'The file name can only use lower-case letters, digits, - and _.'; drawPanel(); return; }
    const text = fileText();
    if (bridge) {
      status = 'Saving…'; drawPanel();
      bridge.save(L.id, text, err => { status = err ? 'Couldn’t save: ' + err : `Saved lessons/${L.id}.js`; drawPanel(); });
      return;
    }
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([text], { type: 'text/javascript' }));
    a.download = L.id + '.js';
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    status = `Downloaded ${L.id}.js. Put it in the lessons folder (src/neusci_daq/lessons/) to keep it.`;
    drawPanel();
  }

  window.ArgusLesson = {
    define(lesson) { if (lesson?.id) lessons[lesson.id] = lesson; },
    setEdit,
    current: () => L,
  };
})();
