// The Analysis page's Trials pane: the session's groups from /trials/state, and every change
// sent to /trials/cmd, which saves it (pages.json) and answers with the new state.
'use strict';

const $ = (id) => document.getElementById(id);
const H = {'X-Newt-Key': new URLSearchParams(location.hash.slice(1)).get('k') || ''};
let S = null;                       // the last state
let lit = new Set();                // highlighted: 'g:' + group name, or 't:' + trial index
let open = new Set();               // groups showing their trials
let lastClick = null;               // for Shift-click ranges of trials

async function cmd(d, busy) {
  if (busy) showBusy(busy);
  try {
    const r = await fetch('/trials/cmd', {method: 'POST', headers: H, body: JSON.stringify(d)});
    show(await r.json());
  } catch (e) { showProblem('Newt is not answering.'); }
}
async function load() {
  try { show(await (await fetch('/trials/state', {headers: H})).json()); }
  catch (e) { showProblem('Newt is not answering.'); }
}
function showBusy(text) { const b = $('busy'); b.textContent = text; b.hidden = !text; }
function showProblem(text) { const p = $('problem'); p.textContent = text || ''; p.hidden = !text; }
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
const fixed = () => S.sorts.length > 0 || S.organize === 'time';

// ---------------------------------------------------------------- the stimulus pictures
let floor = 0;                      // the lowest point of any stimulus (0: they all sit on the bottom)
function spark(t, color) {
  if (!t.spark) return '<svg viewBox="0 0 84 18"></svg>';
  const [lo, hi] = t.spark, n = lo.length;
  const y = (v) => (16 - (v - floor) / (1 - floor) * 14).toFixed(1);
  let d = '';
  for (let k = 0; k < n; k++) {
    const x = (1 + k * 82 / (n - 1)).toFixed(1);
    const [a, b] = k % 2 ? [hi[k], lo[k]] : [lo[k], hi[k]];
    d += `${k ? 'L' : 'M'}${x} ${y(a)}L${x} ${y(b)}`;
  }
  return `<svg viewBox="0 0 84 18"><path d="${d}" stroke="${color}" fill="none" stroke-width="1.4" stroke-linejoin="round"/></svg>`;
}

// ---------------------------------------------------------------- showing the state
function show(st) {
  S = st;
  showBusy(st.busy);
  showProblem(st.problem);
  if (!st.folder && st.sessions.length && !st.busy) {      // nothing open yet: the newest session
    return cmd({cmd: 'open', folder: st.sessions[0].path}, 'Reading ' + st.sessions[0].name + '…');
  }
  const names = new Set(st.groups.map((g) => g.name));
  const trials = new Set(st.groups.flatMap((g) => g.trials.map((t) => t.i)));
  lit = new Set([...lit].filter((k) => k[0] === 'g' ? names.has(k.slice(2)) : trials.has(+k.slice(2))));
  open = new Set([...open].filter((n) => names.has(n)));
  floor = Math.min(0, ...st.groups.flatMap((g) => g.trials.map((t) => t.spark ? Math.min(...t.spark[0]) : 0)));
  fillControls();
  renderGroups();
}

function fillControls() {
  $('count').textContent = S.folder ? `${S.n_trials} in ${S.groups.length} group${S.groups.length === 1 ? '' : 's'}` : '';
  const sess = $('session');
  sess.innerHTML = (S.folder ? '' : '<option value="">Pick a session…</option>') +
    S.sessions.map((s) => `<option value="${esc(s.path)}"${s.path === S.folder ? ' selected' : ''}>${esc(s.name)}</option>`).join('');
  const how = $('how');
  const opts = [...S.channels.map((c) => [c, c]), ...(S.can_sent ? [['sent', 'What each trial saved it sent']] : []),
                ...(S.can_notes ? [['notes', 'The notes']] : []), ['', 'None']];
  how.innerHTML = opts.map(([v, t]) => `<option value="${esc(v)}"${v === S.how ? ' selected' : ''}>${esc(t)}</option>`).join('');
  for (const b of $('organize').children) b.setAttribute('aria-pressed', String(b.dataset.k === S.organize));
  $('sorts').innerHTML = S.measures.map(([k, name]) => {
    const at = S.sorts.findIndex(([x]) => x === k);
    const n = at >= 0 && S.sorts.length > 1 ? `<span class="n">${at + 1}</span>` : '';
    return `<button class="chip" data-key="${k}"${at >= 0 ? ` data-way="${S.sorts[at][1]}"` : ''}>${n}${esc(name)}</button>`;
  }).join('');
  const n = S.n_stimuli, none = S.groups.filter((g) => g.name === 'No stimulus found').reduce((s, g) => s + g.trials.length, 0);
  $('summary').textContent = !S.folder ? '' : !S.how ? 'No stimulus channel: pick the channel that recorded the stimulus.'
    : n ? `${n} different stimul${n === 1 ? 'us' : 'i'}` + (none ? `, and ${none} trial${none === 1 ? '' : 's'} with none.` : '.')
    : `No stimulus pulses found on ${S.how}: is it the right channel?`;
  // colors
  const L = S.looks;
  $('page_map').innerHTML = S.page_styles.map((s) => `<option${s === (L.page_map || 'Palette') ? ' selected' : ''}>${s}</option>`).join('');
  $('trial_map').innerHTML = S.trial_styles.map((s) => `<option${s === (L.trial_map || 'Solid') ? ' selected' : ''}>${s}</option>`).join('');
  const style = L.page_map || 'Palette';
  const picks = style === 'From one color to another' ? [['first_color', 'First', '#0072B2'], ['last_color', 'Last', '#D55E00']]
    : style === 'One color' || style === 'Shades of one color' ? [['base_color', 'Color', '#0072B2']] : [];
  $('picks').innerHTML = picks.map(([k, t, dflt]) => `<label>${t} <input type="color" data-k="${k}" value="${L[k] || dflt}"></label>`).join('');
  $('picks').hidden = !picks.length;
  const shown = S.groups.filter((g) => !g.hidden).map((g) => g.color);
  $('ramp').style.background = shown.length > 1 ? `linear-gradient(90deg, ${shown.slice(0, 40).join(', ')})` : (shown[0] || 'none');
  $('op').value = L.trial_opacity || 0;
  $('opout').textContent = +L.trial_opacity ? L.trial_opacity + '%' : 'Auto';
}

function renderGroups() {
  const box = $('groups');
  const keep = box.scrollTop;
  box.classList.toggle('fixed', fixed());
  box.replaceChildren(...S.groups.map(groupEl));
  box.scrollTop = keep;
  const ng = [...lit].filter((k) => k[0] === 'g'), nt = [...lit].filter((k) => k[0] === 't');
  $('actions').hidden = !lit.size;
  $('what').textContent = [ng.length && `${ng.length} group${ng.length > 1 ? 's' : ''}`,
                           nt.length && `${nt.length} trial${nt.length > 1 ? 's' : ''}`].filter(Boolean).join(', ');
  const litGroups = S.groups.filter((g) => lit.has('g:' + g.name));
  const act = (a) => $('actions').querySelector(`[data-a=${a}]`);
  act('combine').hidden = ng.length < 2 || S.organize === 'time';
  act('split').hidden = !(litGroups.length === 1 && litGroups[0].kind === 'combined');
  act('move').hidden = !nt.length || S.organize === 'time';
  act('uncolor').hidden = !litGroups.some((g) => g.own);
  if (!nt.length) $('mover').hidden = true;
}

function groupEl(g) {
  const el = document.createElement('div');
  const on = g.trials.filter((t) => !t.off).length;
  el.className = 'group' + (open.has(g.name) ? ' open' : '') + (lit.has('g:' + g.name) ? ' lit' : '') + (g.hidden ? ' off' : '');
  el.draggable = !fixed();
  el.innerHTML = `<div class="ghead">
      <span class="grip" title="Drag to reorder">⠿</span>
      <input type="checkbox" ${g.hidden ? '' : 'checked'} title="In every chart">
      <button class="swatch" style="background:${g.color}" title="${g.own ? 'Its own color' : 'Its color'}: click to change"></button>
      <span class="gname" title="${esc(g.name)}">${esc(g.name)}</span>
      <span class="gmeta">${g.kind ? `<span class="badge" title="${g.kind === 'alike' ? 'Group alike stimuli joined these' : 'You combined these'}">${g.kind}</span>` : ''}${on < g.trials.length ? `${on}/` : ''}${g.trials.length}</span>
      <span class="chev">›</span></div>` +
    (open.has(g.name) ? `<div class="tlist">${g.trials.map((t) => `<div class="trial${t.off ? ' off' : ''}${lit.has('t:' + t.i) ? ' lit' : ''}" data-i="${t.i}" draggable="true">
        <input type="checkbox" ${t.off ? '' : 'checked'}>
        <span class="tname" title="${esc(t.stem + (t.note ? ' · ' + t.note : ''))}">${esc(t.stem)}${t.note ? ` <span class="tnote">· ${esc(t.note)}</span>` : ''}</span>
        ${spark(t, g.color)}<span class="dur">${t.dur == null ? '?' : t.dur.toFixed(1) + ' s'}</span></div>`).join('')}</div>` : '');
  const cb = el.querySelector('.ghead input');
  cb.indeterminate = !g.hidden && on > 0 && on < g.trials.length;
  cb.onclick = (e) => { e.stopPropagation(); cmd({cmd: 'tick', kind: 'page', name: g.name, on: cb.checked}); };
  el.querySelector('.chev').onclick = (e) => { e.stopPropagation(); toggleOpen(g.name); };
  el.querySelector('.swatch').onclick = (e) => { e.stopPropagation(); pickColor(g); };
  el.querySelector('.ghead').onclick = (e) => highlight('g:' + g.name, e);
  el.querySelector('.ghead').ondblclick = (e) => { if (!e.target.closest('.gname')) toggleOpen(g.name); };
  const nm = el.querySelector('.gname');
  nm.ondblclick = (e) => { e.stopPropagation(); rename(nm, g.name); };
  for (const row of el.querySelectorAll('.trial')) {
    const i = +row.dataset.i;
    row.querySelector('input').onclick = (e) => { e.stopPropagation(); cmd({cmd: 'tick', kind: 'block', i, on: e.target.checked}); };
    row.onclick = (e) => highlight('t:' + i, e, g);
    row.ondragstart = (e) => {
      e.stopPropagation();
      const keys = lit.has('t:' + i) ? [...lit].filter((k) => k[0] === 't').map((k) => +k.slice(2)) : [i];
      e.dataTransfer.setData('application/x-trials', JSON.stringify(keys));
      e.dataTransfer.effectAllowed = 'move';
    };
  }
  // dragging: a group onto another reorders; trials onto a group move there
  el.ondragstart = (e) => { el.classList.add('dragging'); e.dataTransfer.setData('application/x-group', g.name); };
  el.ondragend = () => el.classList.remove('dragging');
  el.ondragover = (e) => {
    const types = e.dataTransfer.types;
    if (types.includes('application/x-trials') && S.organize !== 'time') { e.preventDefault(); el.classList.add('drop'); }
    else if (types.includes('application/x-group') && !fixed()) { e.preventDefault(); el.classList.add('over'); }
  };
  el.ondragleave = () => el.classList.remove('over', 'drop');
  el.ondrop = (e) => {
    e.preventDefault();
    el.classList.remove('over', 'drop');
    const trialsMoved = e.dataTransfer.getData('application/x-trials');
    if (trialsMoved) return cmd({cmd: 'move', blocks: JSON.parse(trialsMoved), page: g.name});
    const name = e.dataTransfer.getData('application/x-group');
    if (name && name !== g.name) cmd({cmd: 'move_group', name, row: S.groups.findIndex((x) => x.name === g.name)});
  };
  return el;
}

function toggleOpen(name) { open.has(name) ? open.delete(name) : open.add(name); renderGroups(); }

function highlight(key, e, g) {
  if (e.shiftKey && g && lastClick && lastClick[0] === 't' && key[0] === 't') {     // a run of trials
    const order = S.groups.flatMap((x) => open.has(x.name) ? x.trials.map((t) => 't:' + t.i) : []);
    const [a, b] = [order.indexOf(lastClick), order.indexOf(key)].sort((p, q) => p - q);
    if (a >= 0) order.slice(a, b + 1).forEach((k) => lit.add(k));
  } else if (e.shiftKey || e.metaKey || e.ctrlKey) {
    lit.has(key) ? lit.delete(key) : lit.add(key);
  } else {
    lit = lit.has(key) && lit.size === 1 ? new Set() : new Set([key]);
  }
  lastClick = key;
  renderGroups();
}

function rename(nm, old) {
  nm.contentEditable = 'true';
  nm.focus();
  document.getSelection().selectAllChildren(nm);
  let done = false;
  const finish = (keep) => {
    if (done) return;
    done = true;
    nm.contentEditable = 'false';
    const name = nm.textContent.trim();
    if (keep && name && name !== old) {
      if (lit.delete('g:' + old)) lit.add('g:' + name);
      if (open.delete(old)) open.add(name);
      cmd({cmd: 'rename', old, new: name});
    } else nm.textContent = old;
  };
  nm.onkeydown = (e) => {
    if (e.key === 'Enter') { e.preventDefault(); finish(true); }
    if (e.key === 'Escape') { e.preventDefault(); finish(false); }
  };
  nm.onblur = () => finish(true);
  nm.onclick = (e) => e.stopPropagation();
}

function pickColor(g) {
  const p = $('colorpick');
  p.value = /^#[0-9a-f]{6}$/i.test(g.color) ? g.color : '#0072b2';
  p.onchange = () => cmd({cmd: 'color', name: g.name, color: p.value});
  p.click();
}

// ---------------------------------------------------------------- the controls
$('session').onchange = (e) => { if (e.target.value) { lit.clear(); open.clear(); cmd({cmd: 'open', folder: e.target.value}, 'Reading…'); } };
$('how').onchange = (e) => cmd({cmd: 'channel', how: e.target.value}, 'Grouping…');
$('organize').onclick = (e) => { const k = e.target.dataset.k; if (k !== undefined && k !== S.organize) cmd({cmd: 'organize', key: k}); };
$('sorts').onclick = (e) => {
  const chip = e.target.closest('.chip');
  if (!chip) return;
  const k = chip.dataset.key, first = k === 'trials' ? -1 : 1;
  const sorts = S.sorts.map((x) => [...x]), at = sorts.findIndex(([x]) => x === k);
  if (at < 0) sorts.push([k, first]);                      // off → first way → the other way → off
  else if (sorts[at][1] === first) sorts[at][1] = -first;
  else sorts.splice(at, 1);
  cmd({cmd: 'sorts', sorts}, 'Sorting…');
};
$('actions').onclick = (e) => {
  const a = e.target.dataset.a;
  if (!a) return;
  const pages = [...lit].filter((k) => k[0] === 'g').map((k) => k.slice(2));
  const blocks = [...lit].filter((k) => k[0] === 't').map((k) => +k.slice(2));
  if (a === 'only' || a === 'tick') cmd({cmd: 'tick_lit', pages, blocks, only: a === 'only'});
  else if (a === 'combine') cmd({cmd: 'combine', names: pages});
  else if (a === 'split') cmd({cmd: 'split', name: pages[0]});
  else if (a === 'uncolor') pages.forEach((name) => cmd({cmd: 'color', name, color: null}));
  else if (a === 'move') {
    const from = new Set(S.groups.filter((g) => g.trials.some((t) => blocks.includes(t.i))).map((g) => g.name));
    $('moveto').innerHTML = S.groups.filter((g) => !(from.size === 1 && from.has(g.name)))
      .map((g) => `<button data-to="${esc(g.name)}"><span class="dot" style="background:${g.color}"></span> ${esc(g.name)}</button>`).join('');
    $('mover').hidden = false;
  }
};
const moveTo = (page) => {
  const blocks = [...lit].filter((k) => k[0] === 't').map((k) => +k.slice(2));
  if (page && blocks.length) { $('mover').hidden = true; $('newgroup').value = ''; cmd({cmd: 'move', blocks, page}); }
};
$('moveto').onclick = (e) => { const b = e.target.closest('[data-to]'); if (b) moveTo(b.dataset.to); };
$('newgo').onclick = () => moveTo($('newgroup').value.trim());
$('newgroup').onkeydown = (e) => { if (e.key === 'Enter') moveTo($('newgroup').value.trim()); };
$('tickall').onclick = () => cmd({cmd: 'tick_all', on: true});
$('untickall').onclick = () => cmd({cmd: 'tick_all', on: false});
$('regroup').onclick = () => cmd({cmd: 'regroup'}, 'Measuring every stimulus again…');
let resetTimer = 0;
$('reset').onclick = (e) => {                              // asks once, on the button itself
  const b = e.currentTarget;
  if (!b.classList.contains('warn')) {
    b.classList.add('warn');
    b.textContent = 'Forget names and moves?';
    resetTimer = setTimeout(() => { b.classList.remove('warn'); b.textContent = 'Reset…'; }, 4000);
    return;
  }
  clearTimeout(resetTimer);
  b.classList.remove('warn');
  b.textContent = 'Reset…';
  cmd({cmd: 'reset'});
};
$('page_map').onchange = (e) => cmd({cmd: 'looks', page_map: e.target.value === 'Palette' ? '' : e.target.value});
$('trial_map').onchange = (e) => cmd({cmd: 'looks', trial_map: e.target.value === 'Solid' ? '' : e.target.value});
$('picks').onchange = (e) => { if (e.target.dataset.k) cmd({cmd: 'looks', [e.target.dataset.k]: e.target.value}); };
$('op').oninput = () => { $('opout').textContent = +$('op').value ? $('op').value + '%' : 'Auto'; };
$('op').onchange = () => cmd({cmd: 'looks', trial_opacity: +$('op').value});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && !e.target.isContentEditable && e.target.tagName !== 'INPUT') { lit.clear(); $('mover').hidden = true; renderGroups(); }
});
window.addEventListener('focus', load);                   // the Qt app may have changed the groups meanwhile

load();
