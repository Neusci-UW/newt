// The Trials pane, on Analysis and the DataPad: the session's groups from /trials/state, and
// every change sent to /trials/cmd, which saves it (pages.json) and answers with the new state.
// It fills <section id="trials">; a page with a Colors panel (#page_map…) gets that too, and
// window.boardTrials(state) and window.panelsTrials(state), if the page has them, hear of
// every change. With data-preview on the section (Analysis), ↑ and ↓ step through the list
// (Shift adds to what's highlighted) and the trial looked at shows below it, every channel.
'use strict';

const $ = (id) => document.getElementById(id);
$('trials').innerHTML = `
    <div class="thead"><h2>Trials</h2><span class="count" id="count"></span><span class="spacer"></span>
      <div class="seg" id="organize"><button data-k="" title="A group for each different stimulus">By stimulus</button><button data-k="time" title="One list, in the order they were recorded">By time</button></div>
    </div>
    <label class="field">Session <select id="session"></select></label>
    <label class="field">Stimulus channel <select id="how"></select></label>
    <div class="field">Sort groups by <span class="chips" id="sorts"></span></div>
    <span class="busy" id="busy" hidden></span>
    <span class="problem" id="problem" hidden></span>

    <div class="actions" id="actions" hidden>
      <span class="what" id="what"></span>
      <button data-a="only" title="Tick these and untick everything else">Only these</button>
      <button data-a="tick" title="Tick these, leaving the rest as they are">Tick</button>
      <button data-a="combine" title="Make these groups one group">Combine</button>
      <button data-a="split" title="Back into the stimuli it was made of">Split</button>
      <button data-a="move" title="Put these trials in another group">Move to…</button>
      <button data-a="uncolor" title="Back to the color from the Colors setting">Default color</button>
      <button data-a="delete" class="danger" title="Move these trials' files to the Trash (you can put them back from there)">Delete…</button>
    </div>
    <div class="mover" id="mover" hidden>
      <span class="caption">Move to:</span>
      <div class="to" id="moveto"></div>
      <div class="to"><input id="newgroup" placeholder="A new group's name"><button id="newgo">Move</button></div>
    </div>

    <div class="groups" id="groups"></div>
    <div class="tpreview" id="tpreview" hidden><div class="pvtitle" id="pvtitle"></div><div id="pvplots"></div></div>

    <div class="foot">
      <button id="tickall">Tick all</button><button id="untickall">Untick all</button><span class="spacer"></span>
      <button id="regroup" title="Sort every trial into groups again from scratch: each stimulus is measured again. Names and moves stay">Regroup</button>
      <button id="reset" title="Forget the group names you gave and the trials you moved">Reset…</button>
    </div>
    <p class="caption" id="summary"></p>
    <p class="caption">Click to highlight; Shift or ⌘ adds more<span class="stepnote" hidden>; ↑ and ↓ step through them (Shift adds)</span>. Double-click a name to rename it. Drag ⠿ to reorder, or a trial onto a group to move it. Unticked trials are left out of every chart and measurement.</p>
    <dialog class="confirm" id="delask"><p class="dtitle" id="deltitle"></p><p class="caption" id="delnames"></p>
      <p class="caption">You can put them back from the Trash.</p>
      <div class="dbtns"><button id="delno" autofocus>Cancel</button><button id="delyes" class="danger">Move to Trash</button></div></dialog>
`;
const PREVIEW = $('trials').hasAttribute('data-preview');
if (PREVIEW) document.querySelector('#trials .stepnote').hidden = false;
const H = {'X-Newt-Key': new URLSearchParams(location.hash.slice(1)).get('k') || ''};
let S = null;                       // the last state
let lit = new Set();                // highlighted: 'g:' + group name, or 't:' + trial index
let open = new Set();               // groups showing their trials
let lastClick = null;               // for Shift-click ranges of trials
let cur = null;                     // the entry looked at: 't:' + trial, or 'a:' + group (its average)

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
  const before = S && S.folder;
  S = st;
  showBusy(st.busy);
  showProblem(st.problem);
  if (st.note) showNote(st.note);
  if (before !== st.folder) cur = null;
  if (!st.folder && st.sessions.length && !st.busy) {      // nothing open yet: the newest session
    return cmd({cmd: 'open', folder: st.sessions[0].path}, 'Reading ' + st.sessions[0].name + '…');
  }
  const names = new Set(st.groups.map((g) => g.name));
  const trials = new Set(st.groups.flatMap((g) => g.trials.map((t) => t.i)));
  lit = new Set([...lit].filter((k) => k[0] === 'g' ? names.has(k.slice(2)) : trials.has(+k.slice(2))));
  open = new Set([...open].filter((n) => names.has(n)));
  floor = Math.min(0, ...st.groups.flatMap((g) => g.trials.map((t) => t.spark ? Math.min(...t.spark[0]) : 0)));
  if (cur && !(cur[0] === 'a' ? names.has(cur.slice(2)) : trials.has(+cur.slice(2)))) cur = null;
  fillControls();
  renderGroups();
  if (window.boardTrials) window.boardTrials(st);         // the charts follow (charts.js)
  if (window.panelsTrials) window.panelsTrials(st);       // the side panels too (sidepanels.js)
  preview();
}
let noteTimer = 0;
function showNote(text) {
  const b = $('busy');
  b.textContent = text; b.hidden = false; b.classList.add('ok');
  clearTimeout(noteTimer);
  noteTimer = setTimeout(() => { b.classList.remove('ok'); showBusy(S && S.busy); }, 8000);
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
  if ($('page_map')) fillColors();
}

function fillColors() {
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
  act('delete').hidden = !nt.length;
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
    (open.has(g.name) ? `<div class="tlist">${PREVIEW && g.trials.length >= 2 ? `<div class="trial avg${cur === 'a:' + g.name ? ' cur' : ''}" data-avg="1" title="Every channel averaged over this group's ticked trials: click to look at it">
        <span></span><span class="tname">Average <span class="tnote">· of ${on || g.trials.length}</span></span><span></span><span></span></div>` : ''}${g.trials.map((t) => `<div class="trial${t.off ? ' off' : ''}${lit.has('t:' + t.i) ? ' lit' : ''}${cur === 't:' + t.i ? ' cur' : ''}" data-i="${t.i}" draggable="true">
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
  const avg = el.querySelector('.trial.avg');
  if (avg) avg.onclick = () => look('a:' + g.name);
  for (const row of el.querySelectorAll('.trial[data-i]')) {
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
  if (key[0] === 't' && lit.has(key)) cur = key;          // the trial clicked is the one looked at
  renderGroups();
  if (key[0] === 't' && cur === key) preview();
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
  else if (a === 'delete') askDelete(blocks);
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
if ($('page_map')) {
  $('page_map').onchange = (e) => cmd({cmd: 'looks', page_map: e.target.value === 'Palette' ? '' : e.target.value});
  $('trial_map').onchange = (e) => cmd({cmd: 'looks', trial_map: e.target.value === 'Solid' ? '' : e.target.value});
  $('picks').onchange = (e) => { if (e.target.dataset.k) cmd({cmd: 'looks', [e.target.dataset.k]: e.target.value}); };
  $('op').oninput = () => { $('opout').textContent = +$('op').value ? $('op').value + '%' : 'Auto'; };
  $('op').onchange = () => cmd({cmd: 'looks', trial_opacity: +$('op').value});
}
document.addEventListener('keydown', (e) => {
  const typing = e.target.isContentEditable || ['INPUT', 'SELECT', 'TEXTAREA'].includes(e.target.tagName) || $('delask').open;
  if (e.key === 'Escape' && !typing) { lit.clear(); $('mover').hidden = true; renderGroups(); }
  if (typing || !S || !S.folder) return;
  if (PREVIEW && (e.key === 'ArrowDown' || e.key === 'ArrowUp') && !e.metaKey && !e.ctrlKey && !e.altKey) {
    e.preventDefault();
    step(e.key === 'ArrowDown' ? 1 : -1, e.shiftKey);
  }
  if ((e.key === 'Delete' || e.key === 'Backspace') && [...lit].some((k) => k[0] === 't')) {
    e.preventDefault();
    askDelete([...lit].filter((k) => k[0] === 't').map((k) => +k.slice(2)));
  }
});

// ---------------------------------------------------------------- ↑ and ↓, and the trial looked at
// (AnalysisPage.step_list): on to the entry before or after (each group's Average, then its
// trials), highlighting just it, opening its group and closing the one left; with Shift, on
// to the next trial, keeping the ones highlighted.
function groupOf(key) {
  return key[0] === 'a' ? key.slice(2) : (S.groups.find((g) => g.trials.some((t) => 't:' + t.i === key)) || {}).name;
}
function step(d, extend) {
  const stops = [];
  for (const g of S.groups) {
    if (g.trials.length >= 2 && !extend) stops.push('a:' + g.name);
    for (const t of g.trials) stops.push('t:' + t.i);
  }
  if (!stops.length) return;
  const here = cur;
  let pos = stops.indexOf(here);
  if (pos < 0 && here && here[0] === 'a') {                // Shift from an Average: on from its group's first trial
    const g = S.groups.find((x) => x.name === here.slice(2));
    const k = g && g.trials.length ? stops.indexOf('t:' + g.trials[0].i) : -1;
    if (k >= 0) pos = d > 0 ? k - 1 : k;
  }
  if (pos < 0) pos = (d > 0 ? 0 : stops.length - 1) - d;
  const next = stops[Math.max(0, Math.min(stops.length - 1, pos + d))];
  if (extend) {
    if (here && here[0] === 't') lit.add(here);
    lit.add(next);
  } else {
    lit = new Set(next[0] === 't' ? [next] : []);
    const left = here && groupOf(here), to = groupOf(next);
    if (left && left !== to) open.delete(left);
  }
  open.add(groupOf(next));
  lastClick = next;
  look(next, true);
}
function look(key, scroll) {
  cur = key;
  renderGroups();
  if (scroll) {
    const el = key[0] === 'a' ? [...$('groups').querySelectorAll('.group')].find((x) => x.querySelector('.gname').textContent === key.slice(2))?.querySelector('.trial.avg')
      : $('groups').querySelector(`.trial[data-i="${key.slice(2)}"]`);
    if (el) el.scrollIntoView({block: 'nearest'});
  }
  preview();
}
window.trialsCurrent = () => cur && cur[0] === 't' ? +cur.slice(2) : null;
window.showTrial = (i) => {                               // a raster's row clicked: look at that trial
  const key = 't:' + i;
  const left = cur && groupOf(cur), to = groupOf(key);
  if (left && left !== to) open.delete(left);
  if (to) open.add(to);
  lit = new Set([key]);
  lastClick = key;
  look(key, true);
};

let pv = {seq: 0, plots: [], shape: '', key: null};
async function preview() {
  if (window.boardCurrent) window.boardCurrent();
  if (!PREVIEW) return;
  const box = $('tpreview');
  if (!cur || !S || !S.folder) { box.hidden = true; pv.key = null; return; }
  box.hidden = false;
  const seq = ++pv.seq, key = cur;
  const cols = Math.max(100, Math.round(box.clientWidth - 70));
  let buf;
  try {
    const r = await fetch('/trials/preview', {method: 'POST', headers: H,
      body: JSON.stringify(key[0] === 'a' ? {avg: key.slice(2), cols} : {i: +key.slice(2), cols})});
    buf = await r.arrayBuffer();
  } catch (e) { return; }
  if (seq !== pv.seq) return;                             // a newer one is on its way
  const dv = new DataView(buf), hl = dv.getUint32(0, true);
  const head = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 4, hl)));
  const title = $('pvtitle');
  if (head.note) { title.textContent = head.note; drawPreview(null); return; }
  title.innerHTML = `<span class="dot" style="background:${head.color || 'var(--muted)'}"></span> ${esc(head.title)}`;
  let off = 4 + hl;
  const n = head.n, x = new Float64Array(buf, off, n);
  off += 8 * n;
  const ys = head.plots.map(() => {
    const a = new Float32Array(buf, off, n), out = new Array(n);
    off += 4 * n;
    for (let k = 0; k < n; k++) { const v = a[k]; out[k] = v === v ? v : null; }
    return out;
  });
  drawPreview({head, x, ys});
}
function drawPreview(d) {
  const host = $('pvplots');
  if (!d || !window.uPlot) { pv.plots.forEach((u) => u.destroy()); pv.plots = []; pv.shape = ''; host.replaceChildren(); return; }
  const width = Math.max(160, host.clientWidth), h = 62;
  const shape = JSON.stringify([width, d.head.plots.map((p) => [p.label, p.unit])]);
  const color = d.head.color || getComputedStyle(document.documentElement).getPropertyValue('--ink').trim();
  if (shape !== pv.shape) {
    pv.plots.forEach((u) => u.destroy());
    pv.plots = [];
    host.replaceChildren();
    const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
    const muted = css('--muted'), line = css('--line'), font = '11px "IBM Plex Mono", ui-monospace, Menlo, monospace';
    d.head.plots.forEach((p, k) => {
      const last = k === d.head.plots.length - 1;
      const wrap = document.createElement('div');
      wrap.className = 'pvplot';
      wrap.innerHTML = `<div class="pvlab">${esc(p.label)}</div>`;
      host.append(wrap);
      pv.plots.push(new uPlot({
        width, height: h + (last ? 22 : 0), padding: [4, 8, 0, 0], legend: {show: false},
        cursor: {show: false}, scales: {x: {time: false, auto: false}, y: {auto: false}},
        axes: [{stroke: muted, grid: {show: false}, ticks: {stroke: line, size: 3}, font, size: last ? 22 : 2, space: 60,
                values: last ? (u, v) => v.map((t) => (+t.toFixed(3)) + ' s') : (u, v) => v.map(() => '')},
               {stroke: muted, grid: {stroke: line, width: 1}, ticks: {show: false}, font, size: 62, space: 24,
                values: (u, v) => previewTicks(v, p.unit)}],
        series: [{}, {stroke: color, width: 1.2, points: {show: false}}],
      }, [[], []], wrap));
    });
    pv.shape = shape;
  }
  d.head.plots.forEach((p, k) => {
    const u = pv.plots[k];
    let [lo, hi] = p.yr || [-1, 1];
    if (hi - lo < 1e-12) { const m = Math.abs(lo) * 0.1 || 1; lo -= m; hi += m; }
    const pad = (hi - lo) * 0.08;
    u.series[1].stroke = () => color;
    u.batch(() => {
      u.setData([d.x, d.ys[k]], false);
      u.setScale('x', {min: 0, max: d.head.end});
      u.setScale('y', {min: lo - pad, max: hi + pad});
    });
  });
}
function previewTicks(vals, unit) {                       // 0.02 V reads as 20 mV
  const big = Math.max(0, ...vals.filter((v) => v != null).map(Math.abs));
  const [k, pre] = !unit || unit === 's' || !(big > 0) ? [1, ''] : big < 1e-3 ? [1e6, 'µ'] : big < 1 ? [1e3, 'm'] : [1, ''];
  const st = vals.length > 1 ? Math.abs(vals[1] - vals[0]) * k : 1;
  const dec = Math.max(0, Math.min(5, -Math.floor(Math.log10(st || 1) + 1e-9)));
  return vals.map((v) => String((v * k).toFixed(dec)).replace('-', '−') + ' ' + pre + unit);
}

// ---------------------------------------------------------------- deleting trials (ReviewView.delete_trials)
function askDelete(blocks) {
  if (!blocks.length) return;
  const stems = S.groups.flatMap((g) => g.trials).filter((t) => blocks.includes(t.i)).map((t) => t.stem);
  const n = blocks.length;
  $('deltitle').textContent = `Move ${n} trial${n === 1 ? '' : 's'} to the Trash?`;
  $('delnames').textContent = stems.slice(0, 6).join(', ') + (stems.length > 6 ? ` and ${stems.length - 6} more` : '');
  const dlg = $('delask');
  $('delyes').onclick = () => {
    dlg.close();
    for (const b of blocks) lit.delete('t:' + b);
    cur = null;                                           // (the trials after it move up one)
    cmd({cmd: 'delete', blocks}, `Moving ${n} trial${n === 1 ? '' : 's'} to the Trash…`);
  };
  $('delno').onclick = () => dlg.close();
  dlg.showModal();
}
window.addEventListener('focus', load);                   // the Qt app may have changed the groups meanwhile

load();
