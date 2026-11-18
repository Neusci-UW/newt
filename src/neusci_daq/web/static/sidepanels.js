// The Analysis side panels, beside the board, as the Qt app's left rail has them: Groups
// (the Colors, and Group alike stimuli: a map of the stimuli by what's ticked, joining those
// close on it), Channels (amplifier gains, the stimulus fit, calculated channels) and Spikes
// (find action potentials and sort them into units). Groups and Channels change the Trials
// pane's state (/trials/cmd, through analysis.js's cmd); Spikes has its own (/spikes/*).
// Uses $, H, esc and cmd (analysis.js).
'use strict';

(() => {
const P = {st: null, sp: null, poll: 0, seen: -1, form: null, formSeq: 0};
const post = (url, d) => fetch(url, {method: 'POST', headers: H, body: JSON.stringify(d)});
const opt = (v, t, cur, dis) => `<option value="${esc(v)}"${String(v) === String(cur) ? ' selected' : ''}${dis ? ' disabled' : ''}>${esc(t)}</option>`;

// ---------------------------------------------------------------- the tabs
function openTab(p) {
  for (const b of $('sideTabs').children) b.setAttribute('aria-pressed', String(b.dataset.p === p));
  for (const el of document.querySelectorAll('.side .pane[data-p]')) el.hidden = el.dataset.p !== p;
  try { localStorage.setItem('newt.analysis.side', p); } catch (e) { /* */ }
  if (p === 'spikes') loadSpikes();
}
$('sideTabs').onclick = (e) => { if (e.target.dataset.p) openTab(e.target.dataset.p); };
let first = 'groups';
try { first = localStorage.getItem('newt.analysis.side') || 'groups'; } catch (e) { /* */ }

window.panelsTrials = (st) => {
  P.st = st;
  drawAlike();
  drawChannels();
  clearTimeout(P.poll);
  P.poll = setTimeout(loadSpikes, 60);       // a tick or a new channel can start a search
};

// ---------------------------------------------------------------- Group alike stimuli (alikepanel.py)
function drawAlike() {
  const S = P.st, a = S && S.alike, box = $('alike');
  if (!a || !S.folder) { box.innerHTML = ''; return; }
  const n = a.stimuli, m = a.map;
  const close = a.by === 'close';
  const v = close ? a.close : (a.n > 0 && a.n < n ? a.n : n);
  const ticks = a.ticks.map(([k, name], j) => `<label${j === 0 ? ' class="wide"' : ''} title="${j === 0 ? 'Each stimulus slid along the other until they line up best: the same shape is alike whatever its size' : ''}"><input type="checkbox" data-feat="${k}"${a.feats.includes(k) ? ' checked' : ''}> ${esc(name)}</label>`).join('');
  const measures = a.ticks.slice(1);
  let html = `<p class="caption">Compare stimuli by:</p><div class="ticks">${ticks}</div>
    <label class="field">Join <select id="alby" title="Pick how many groups, or how close on the map two stimuli must be to share a group">
      ${opt('n', 'Number of groups', a.by)}${opt('close', 'How close counts as alike', a.by)}</select></label>
    <div class="row"><span></span><input type="range" id="alslide" min="${close ? 0 : 1}" max="${close ? 100 : Math.max(1, n)}" value="${v}"${m ? '' : ' disabled'}><output id="alval"></output></div>
    <p class="caption">${close ? 'Stimuli closer than this on the map share a group (100%: the two farthest apart).' : 'How many groups: the stimuli closest on the map join first.'}</p>
    <div class="btnrow"><button id="alkeep"${a.can_keep ? '' : ' disabled'} title="Make the groups the map joined ordinary groups: rename, split or drag them, and they stay when you change the measures. Until then the list follows the map">Keep these groups</button></div>`;
  html += m ? mapSvg(m) + `<p class="caption">Near each other = alike. ${m.axes.map((ax, k) =>
    `<b>${k ? 'Next' : 'Main'} difference (${ax.share}%)</b>` + (ax.mostly ? `: mostly ${esc(ax.mostly)}${ax.measure ? `, bigger ${k ? 'up' : 'to the right'}` : ''}` : '')).join('. ')}.</p>`
    : `<p class="caption">${esc(a.why)}</p>`;
  html += `<label class="field">Color groups by <select id="colorby" title="Color the groups along a measure (or the map), lowest to highest, in the Groups color map (Viridis for Palette). A color you give a group stays">
      ${opt('', 'The Groups setting', a.color_by)}${opt('pc1', 'Along the map', a.color_by, !m)}${measures.map(([k, name]) => opt(k, name, a.color_by)).join('')}</select></label>
    <label class="field">Combined average <select id="avgeach" title="A group holding several stimuli (combined, or joined by the map): one average of all its trials, or an average for each stimulus in it, drawn together in its color">
      ${opt(0, 'All its trials', a.avg_each ? 1 : 0)}${opt(1, 'Each stimulus', a.avg_each ? 1 : 0)}</select></label>`;
  if (box.dataset.html === html) return;
  box.dataset.html = html;
  box.innerHTML = html;
  const slide = $('alslide'), val = $('alval');
  const text = (x) => !m ? 'each its own' : close ? (x ? x + '%' : '0%: each its own') : x >= n ? `${x}: each its own` : `${x} groups`;
  val.textContent = text(+slide.value);
  slide.oninput = () => { val.textContent = text(+slide.value); };
  slide.onchange = () => cmd(close ? {cmd: 'alike', close: +slide.value} : {cmd: 'alike', n: +slide.value >= n ? 0 : +slide.value}, 'Grouping…');
  box.querySelector('.ticks').onchange = () => cmd({cmd: 'alike', feats: [...box.querySelectorAll('[data-feat]:checked')].map((x) => x.dataset.feat)}, 'Comparing the stimuli…');
  $('alby').onchange = (e) => cmd({cmd: 'alike', by: e.target.value});
  $('alkeep').onclick = () => cmd({cmd: 'keep_alike'});
  $('colorby').onchange = (e) => cmd({cmd: 'looks', color_by: e.target.value});
  $('avgeach').onchange = (e) => cmd({cmd: 'looks', avg_each: e.target.value === '1'});
}

function mapSvg(m) {
  const S = 230, pts = m.points;
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
  const span = Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys), 1e-9) * 1.35;
  const cx = (Math.max(...xs) + Math.min(...xs)) / 2 + span * 0.08, cy = (Math.max(...ys) + Math.min(...ys)) / 2;
  const L = 18, B = 18;                                     // room for the axis names
  const X = (v) => (L + ((v - cx) / span + 0.5) * S).toFixed(1), Y = (v) => ((0.5 - (v - cy) / span) * S).toFixed(1);
  let s = `<svg class="amap" viewBox="0 0 ${S + L} ${S + B}" role="img" aria-label="The stimuli on a map: near each other is alike">`;
  s += `<rect class="frame" x="${L}" y="0" width="${S}" height="${S}"/>`;
  for (const members of m.joined) {                         // a group's stimuli, each joined to its middle
    if (members.length < 2) continue;
    const mx = members.reduce((a, k) => a + xs[k], 0) / members.length, my = members.reduce((a, k) => a + ys[k], 0) / members.length;
    s += members.map((k) => `<line x1="${X(mx)}" y1="${Y(my)}" x2="${X(xs[k])}" y2="${Y(ys[k])}" stroke="${pts[members[0]][2]}" stroke-opacity=".55" stroke-width="2"/>`).join('');
  }
  s += pts.map(([x, y, c, name]) => `<circle cx="${X(x)}" cy="${Y(y)}" r="4.5" fill="${c}"><title>${esc(name)}</title></circle>`).join('');
  s += pts.map(([x, y, , name]) => `<text class="lab" x="${+X(x) + 6}" y="${+Y(y) + 3}">${esc(name.length <= 22 ? name : name.slice(0, 21) + '…')}</text>`).join('');
  s += `<text class="alab" x="${L + S / 2}" y="${S + 13}" text-anchor="middle">Main difference (${m.axes[0].share}%)</text>`;
  s += `<text class="alab" transform="translate(12 ${S / 2}) rotate(-90)" text-anchor="middle">Next difference (${m.axes[1].share}%)</text>`;
  return s + '</svg>';
}

// ---------------------------------------------------------------- Channels: gains, the stimulus fit, calculated channels
function drawChannels() {
  const S = P.st, box = $('chanPane');
  if (!S || !S.folder) { box.innerHTML = '<p class="caption">Open a session first.</p>'; box.dataset.html = ''; return; }
  const html = `<p class="sect-title">Amplifier gain</p>
    <p class="caption">Every analysis divides each channel by its amplifier's gain, giving the voltage before the amplifier (a ×100 amplifier: 100). 1 means no amplifier. The recordings never change.</p>
    <div class="gains">${S.recorded.map((c) => `<label class="gainrow"><span>${esc(c.name)}${c.name === S.how ? ' <small>(stimulus)</small>' : ''}</span><span>÷</span>
      <input type="number" min="0.0001" step="any" data-gain="${esc(c.name)}" value="${c.gain}"></label>`).join('')}</div>
    <p class="sect-title">Stimulus fit</p>
    <label class="field">${S.channels.includes(S.how) ? esc(S.how) : 'The stimulus channel'} <select id="fit"${S.channels.includes(S.how) ? '' : ' disabled'}>${S.fits.map((f) => opt(f, f, S.stim_fit)).join('')}</select></label>
    <p class="caption">${S.channels.includes(S.how) ? 'Takes sampling noise and coarse voltage steps out of the stimulus channel. Piecewise linear: pulses, steps, ramps, custom shapes. Smooth: other shapes, without shifting timing. Sine: a sine. Repeat average, Harmonics: anything that repeats. The recordings never change.' : 'Pick the channel that recorded the stimulus in the Trials pane to fit it.'}</p>
    <p class="sect-title">Calculated channels</p>
    <p class="caption">Channels worked out from the others: a filter, a rate meter, one channel minus another. Charts list them beside the recorded ones.</p>
    <div class="calcs">${S.calcs.map((c) => `<div class="calc"><span>${esc(c.name)}${c.unit ? ` <span class="muted">(${esc(c.unit)})</span>` : ''}</span>
      <button class="ib" data-edit="${esc(c.name)}">Edit</button><code>${esc(c.formula)}</code>${c.problem ? `<span class="bad">${esc(c.problem)}</span>` : ''}</div>`).join('')}</div>
    <div class="btnrow"><button id="calcAdd">Add a channel…</button></div>`;
  if (box.dataset.html !== html) {
    const form = P.form ? $('calcForm') : null;
    box.dataset.html = html;
    box.innerHTML = html + '<div class="calcform" id="calcForm" hidden></div>';
    if (form) box.replaceChild(form, $('calcForm'));        // a form being filled in stays as it is
    box.querySelector('.gains').onchange = (e) => {
      const name = e.target.dataset.gain;
      if (name !== undefined) cmd({cmd: 'gain', name, gain: +e.target.value || 1}, 'Reading again…');
    };
    if ($('fit')) $('fit').onchange = (e) => cmd({cmd: 'fit', kind: e.target.value}, 'Fitting the stimulus…');
    $('calcAdd').onclick = () => openForm(null);
    box.querySelector('.calcs').onclick = (e) => { const n = e.target.dataset.edit; if (n !== undefined) openForm(n); };
  }
}

// the form (AnalysisChannelDialog): source, calculation and its settings; the formula,
// name and unit are worked out in Python (/trials/calc) as you go
function openForm(old) {
  const S = P.st;
  const c = old ? S.calcs.find((x) => x.name === old) : null;
  const above = S.calcs.map((x) => x.name);
  const sources = [...S.channels, ...(old ? above.slice(0, above.indexOf(old)) : above)];
  let kind = 'bandpass', src = S.channels.find((n) => n !== S.how) || sources[0] || '', values = {};
  if (c) {
    const spec = c.spec;
    if (spec && sources.includes(spec[1])) [kind, src, values] = spec;
    else [kind, values] = ['custom', {formula: c.formula}];
  }
  P.form = {old: old || '', kind, source: src, values, name: c ? c.name : null, unit: c ? c.unit : null, sources};
  const f = $('calcForm');
  f.hidden = false;
  const groups = [];
  for (const [k, label, grp] of S.calc_kinds) {
    let g = groups.find((x) => x[0] === grp);
    if (!g) groups.push(g = [grp, []]);
    g[1].push([k, label]);
  }
  f.innerHTML = `<b>${old ? 'Change ' + esc(old) : 'A new calculated channel'}</b>
    <label class="field">Source channel <select id="cfsrc">${sources.map((n) => opt(n, n, src)).join('')}</select></label>
    <label class="field">Calculation <select id="cfkind">${groups.map(([g, ks]) => `<optgroup label="${esc(g)}">${ks.map(([k, l]) => opt(k, l, kind)).join('')}</optgroup>`).join('')}</select></label>
    <div id="cfsets"></div>
    <label class="field">Name <input id="cfname" placeholder="(made up for you)"></label>
    <label class="field">Unit <input id="cfunit"></label>
    <div class="caption">Formula: <code id="cfformula">—</code></div>
    <div class="why" id="cfwhy"></div>
    <div class="btnrow">${old ? '<button class="danger" id="cfdel">Delete channel</button>' : ''}<span class="spacer"></span><button id="cfno">Cancel</button><button class="primary" id="cfok">${old ? 'Save' : 'Add'}</button></div>`;
  $('cfsrc').onchange = (e) => { P.form.source = e.target.value; check(); };
  $('cfkind').onchange = (e) => { P.form.kind = e.target.value; P.form.values = {}; drawSets(); check(); };
  $('cfname').oninput = (e) => { P.form.name = e.target.value.trim() || null; check(); };
  $('cfunit').oninput = (e) => { P.form.unit = e.target.value; check(); };
  $('cfno').onclick = closeForm;
  $('cfok').onclick = async () => {
    const F = P.form;
    await cmd({cmd: 'calc', old: F.old, kind: F.kind, source: F.source, values: F.values, name: F.name, unit: F.unit});
    if (!P.st.problem) closeForm();
  };
  if ($('cfdel')) $('cfdel').onclick = async () => { await cmd({cmd: 'calc', old: P.form.old, delete: true}); closeForm(); };
  if (c) { $('cfname').value = c.name; $('cfunit').value = c.unit; }
  drawSets();
  check();
  f.scrollIntoView({block: 'nearest'});
}
function closeForm() { P.form = null; const f = $('calcForm'); if (f) { f.hidden = true; f.innerHTML = ''; } }

function drawSets() {
  const F = P.form, S = P.st;
  const k = S.calc_kinds.find((x) => x[0] === F.kind);
  const sets = k ? k[3] : [];
  const sp = P.sp && P.sp.settings;                         // cyclic measurements start from the spike search's
  const pick = (key, dflt) => F.values[key] !== undefined ? F.values[key]
    : key === 'dir' && sp ? (sp.polarity === 'negative' ? 'below' : 'above') : key === 'dead' && sp ? sp.dead : dflt;
  let html = '';
  for (const [key, label, dflt, unit, tip] of sets) {
    const v = pick(key, dflt);
    F.values[key] = v;
    let w;
    if (key === 'dir') w = `<select data-k="${key}">${opt('above', 'Going above the level', v)}${opt('below', 'Going below the level', v)}</select>`;
    else if (key === 'per') w = `<select data-k="${key}">${opt('min', 'Minute (/min)', v)}${opt('s', 'Second (/s)', v)}</select>`;
    else if (key === 'op') w = `<select data-k="${key}">${['+', '−', '×', '÷'].map((o) => opt(o, o, v)).join('')}</select>`;
    else if (key === 'other') {
      const other = F.sources.includes(v) ? v : F.sources[1] || F.sources[0] || '';
      F.values[key] = other;
      w = `<select data-k="${key}">${F.sources.map((n) => opt(n, n, other)).join('')}</select>`;
    } else if (key === 'formula') w = `<input data-k="${key}" value="${esc(v)}" placeholder='e.g. rms(highpass("Channel 1", 100), 10)'>`;
    else w = `<input type="number" step="any" data-k="${key}" data-num="1" value="${esc(v)}"${key === 'order' ? ' min="1" max="10" step="1"' : ''}>`;
    const u = key === 'level' || key === 'b' ? (S.recorded.find((c) => c.name === F.source) || {}).unit || '' : unit;
    html += `<label class="setrow"${tip ? ` title="${esc(tip)}"` : ''}><span>${esc(label)}</span>${w}<span class="u">${esc(u)}</span></label>`;
    if (key === 'formula') html += `<div class="fhelp">${S.formula_help}</div>`;
  }
  const box = $('cfsets');
  box.innerHTML = html;
  box.oninput = (e) => {
    const key = e.target.dataset.k;
    if (!key) return;
    F.values[key] = e.target.dataset.num ? +e.target.value : e.target.value;
    check();
  };
  $('cfsrc').disabled = F.kind === 'custom';
}

async function check() {
  const F = P.form;
  if (!F) return;
  const seq = ++P.formSeq;
  let d;
  try { d = await (await post('/trials/calc', {old: F.old, kind: F.kind, source: F.source, values: F.values, name: F.name, unit: F.unit})).json(); }
  catch (e) { return; }
  if (seq !== P.formSeq || !P.form) return;
  $('cfformula').textContent = d.formula || '—';
  if (F.name === null) $('cfname').value = d.name || '';
  if (F.unit === null) $('cfunit').value = d.unit || '';
  $('cfwhy').textContent = d.why || '';
  $('cfok').disabled = !!d.why;
}

// ---------------------------------------------------------------- Spikes (spikefinder.py)
async function loadSpikes() {
  clearTimeout(P.poll);
  try { showSpikes(await (await fetch('/spikes/state', {headers: H})).json()); } catch (e) { /* */ }
}
async function spikeCmd(d) {
  try {
    const r = await post('/spikes/cmd', d);
    showSpikes(await r.json());
  } catch (e) { /* */ }
}
function showSpikes(sp) {
  P.sp = sp;
  if (sp.version !== P.seen) {                              // new spikes, or units changed: the charts follow
    if (P.seen !== -1 && window.boardSpikes) window.boardSpikes();
    P.seen = sp.version;
  }
  if (sp.searching) P.poll = setTimeout(loadSpikes, 350);
  drawSpikes();
}

const NUMS = [['dead', 'Dead time', 'ms', 'After an event, no other can start for this long', 0.1, 100, 0.5],
              ['pre', 'Waveform before', 'ms', "How much of each event's waveform to keep before its peak", 0.1, 50, 0.5],
              ['post', 'Waveform after', 'ms', "How much of each event's waveform to keep after its peak", 0.1, 50, 0.5]];
function drawSpikes() {
  const sp = P.sp, box = $('spikePane');
  if (!sp || !sp.folder) { box.innerHTML = '<p class="caption">Open a session first.</p>'; box.dataset.html = ''; return; }
  const s = sp.settings;
  const html = `<label class="field">Channel <select id="spch" title="The channel to find action potentials on">${sp.channels.map((n) => opt(n, n, s.channel)).join('')}</select></label>
    <label class="check" title="Find action potentials in every ticked trial, and again as you change a setting. Unticked, none are found"><input type="checkbox" id="spid"${s.identify ? ' checked' : ''}> Identify spikes</label>
    <div class="btnrow"><button id="spfind" title="Search every ticked trial all over again">Find again</button><button id="spclear" title="Clear the action potentials found, and stop searching until you press Find again">Clear</button></div>
    <span class="busy" id="spbusy" hidden></span>
    <table class="units" id="spunits"></table>
    <p class="caption spmsg" id="spmsg"></p>
    <details class="more" id="spmore" open><summary>Search settings</summary>
      <label class="setrow" title="Bimodal: the threshold goes in the valley between the many small noise peaks and the few big spikes. If they don't form two groups, 5 SD is used"><span>Method</span><select data-s="method">${opt(0, 'Manual threshold', s.method)}${opt(1, 'Bimodal (automatic)', s.method)}</select><span></span></label>
      <label class="setrow" title="A level from the baseline, e.g. 50 mV or 0.05 V, or a number of noise SDs, e.g. 5 SD (a plain number means SDs)"><span>Threshold</span><input data-s="threshold" value="${esc(s.threshold)}"><span></span></label>
      <label class="setrow" title="Which way spikes go from the baseline: down, up, or either"><span>Direction</span><select data-s="polarity">${sp.polarities.map(([k, t]) => opt(k, t, s.polarity)).join('')}</select><span></span></label>
      <label class="setrow" title="Keep only events that also swing the other way, as a real extracellular spike does and many artifacts don't"><span>Biphasic</span><span class="check"><input type="checkbox" data-s="biphasic"${s.biphasic ? ' checked' : ''}>
        <input type="number" data-s="bip_frac" data-num="1" min="0.05" max="1" step="0.05" value="${s.bip_frac}" title="How far the other way, as a fraction of the threshold (0 to 1)"${s.biphasic ? '' : ' disabled'}></span><span></span></label>
      <label class="setrow" title="Within this long of the event's peak (before or after)"><span>Within</span><input type="number" data-s="bip_ms" data-num="1" min="0.1" max="20" step="0.5" value="${s.bip_ms}"${s.biphasic ? '' : ' disabled'}><span class="u">ms</span></label>
      ${NUMS.map(([k, label, u, tip, lo, hi, st]) => `<label class="setrow" title="${esc(tip)}"><span>${label}</span><input type="number" data-s="${k}" data-num="1" min="${lo}" max="${hi}" step="${st}" value="${s[k]}"><span class="u">${u}</span></label>`).join('')}
      <label class="setrow" title="How many units to sort the events into. Auto picks however many separate clearly by shape"><span>Units</span><select data-s="units" data-num="1">${opt(0, 'Auto', s.units)}${[1, 2, 3, 4, 5, 6, 7, 8].map((k) => opt(k, k, s.units)).join('')}</select><span></span></label>
    </details>
    <p class="caption">To see them, add a chart: <i>Spikes, trial by trial</i> or <i>Each unit's spike shape</i>. Saved with the session, for the Qt app too.</p>`;
  if (box.dataset.html !== html) {
    const more = $('spmore'), wasOpen = more ? more.open : true;
    box.dataset.html = html;
    box.innerHTML = html;
    $('spmore').open = wasOpen;
    $('spch').onchange = (e) => spikeCmd({cmd: 'set', values: {channel: e.target.value}});
    $('spid').onchange = (e) => spikeCmd({cmd: 'identify', on: e.target.checked});
    $('spfind').onclick = () => spikeCmd({cmd: 'find'});
    $('spclear').onclick = () => spikeCmd({cmd: 'clear'});
    $('spmore').onchange = (e) => {
      const k = e.target.dataset.s;
      if (!k) return;
      const v = e.target.type === 'checkbox' ? e.target.checked : e.target.dataset.num ? +e.target.value : e.target.value;
      spikeCmd({cmd: 'set', values: {[k]: v}});
    };
    $('spmore').onkeydown = (e) => { if (e.key === 'Enter' && e.target.dataset.s === 'threshold') e.target.blur(); };
  }
  const busy = $('spbusy');
  busy.textContent = sp.busy || '';
  busy.hidden = !sp.busy;
  const msg = $('spmsg');
  msg.textContent = sp.problem || sp.message || (s.identify ? '' : 'Tick Identify spikes to find the action potentials in every ticked trial.');
  msg.classList.toggle('bad', !!(sp.problem || sp.error));
  const t = $('spunits');
  t.hidden = !sp.units.length;
  t.innerHTML = sp.units.length ? `<tr><th title="Tick the units every chart shows">Unit</th><th title="How many action potentials it fired">n</th><th title="Its mean firing rate (spikes per second)">Hz</th><th title="Its mean amplitude">Size</th><th title="Mean waveform width, trough to peak">ms</th><th></th></tr>` +
    sp.units.map((u) => `<tr><td><label class="check" style="font-size:12.5px"><input type="checkbox" data-u="${u.unit - 1}"${u.on ? ' checked' : ''}> ${u.unit}</label></td>
      <td>${u.count}</td><td>${u.rate.toFixed(1)}</td><td>${esc(u.size)}</td><td>${u.width.toFixed(2)}</td>
      <td><input type="color" data-uc="${u.unit - 1}" value="${/^#[0-9a-f]{6}$/i.test(u.color) ? u.color : '#0072b2'}" title="Unit ${u.unit}'s color, on every chart"></td></tr>`).join('') : '';
  t.onchange = (e) => {
    if (e.target.dataset.u !== undefined) spikeCmd({cmd: 'unit', u: +e.target.dataset.u, on: e.target.checked});
    if (e.target.dataset.uc !== undefined) spikeCmd({cmd: 'unit_color', u: +e.target.dataset.uc, color: e.target.value});
  };
}

openTab(['groups', 'channels', 'spikes'].includes(first) ? first : 'groups');
})();
