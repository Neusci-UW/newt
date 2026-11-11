/* Student questions for the GHK lesson in membrane_potential.html.

Everything for the questions lives in this file: the questions, their styles and the
code that places them. To remove them, delete the <script src="ghk_questions.js"> line
near the end of membrane_potential.html (and this file). The page works the same without it.

Each question is placed after a heading of the derivation (matched by the start of its
text) or after the GHK calculator. Answers are kept in the browser (localStorage) so a
reload doesn't lose them; "Reset answers" clears them. */
(() => {
  const section = document.getElementById('ghk-equation');
  if (!section) return;

  // ---------- questions ----------
  // type 'mc': options, one marked ok. type 'num': answer ± tol. type 'predict': a number,
  // locked in before the calculator is set up and checked. type 'goal': reach a calculator state.
  const QUESTIONS = [
    { at: 'Definitions', id: 'rtf37', type: 'num', unit: 'mV', answer: 26.7, tol: 0.3, placeholder: '25.0',
      q: 'Work out RT/F at body temperature, 37 °C. Use R = 8.314 J/(mol·K) and F = 96 485 C/mol.',
      explain: 'RT/F = 8.314 × 310.15 / 96 485 ≈ 0.0267 V = 26.7 mV. Check it with the T slider on the calculator.' },
    { at: 'Definitions', id: 'scaleP', type: 'mc',
      q: 'If every permeability doubles, what happens to V<sub>m</sub>?',
      options: [
        { t: 'It moves toward 0 mV' },
        { t: 'Nothing: the factor of 2 cancels between the top and bottom of the fraction', ok: true },
        { t: 'It doubles' },
      ],
      explain: 'Every term on the top and the bottom of the GHK fraction has one P in it, so scaling them all cancels. That is why only the ratios between ions matter.' },

    { at: 'Assumptions', id: 'pump', type: 'mc',
      q: 'The Na⁺/K⁺ pump moves 3 Na⁺ out for every 2 K⁺ in. Which assumption does it break?',
      options: [
        { t: 'The constant field' },
        { t: 'That the only currents are passive ions crossing independently', ok: true },
        { t: 'The partition coefficient at each face' },
      ],
      explain: 'GHK counts only passive fluxes. The pump carries one net positive charge out per cycle, a current of its own, so a real resting potential sits a few mV more negative than GHK predicts.' },
    { at: 'Assumptions', id: 'field', type: 'mc',
      q: 'Which of these would make the field inside the membrane not constant?',
      options: [
        { t: 'Raising the temperature' },
        { t: 'Raising [K⁺]<sub>o</sub>' },
        { t: 'Charged amino acids lining the channel', ok: true },
      ],
      explain: 'Fixed charges inside the membrane bend the potential profile, so it no longer falls in a straight line. Temperature and concentrations change the voltage, not the shape of the field.' },

    { at: '1.', id: 'diffdir', type: 'mc',
      q: 'K⁺ is more concentrated inside. Which way does the diffusion term alone push K⁺?',
      options: [
        { t: 'Out of the cell', ok: true },
        { t: 'Into the cell' },
        { t: 'Neither, without a voltage' },
      ],
      explain: 'Concentration falls from x = 0 (inside) to x = d (outside), so dc/dx &lt; 0 and −D·dc/dx &gt; 0. Positive J means outward.' },
    { at: '1.', id: 'jzero', type: 'mc',
      q: 'Set J<sub>s</sub> = 0 in the Nernst–Planck equation and integrate across the membrane. What do you get?',
      options: [
        { t: 'Fick’s law' },
        { t: 'The Nernst equation', ok: true },
        { t: 'Ohm’s law' },
      ],
      explain: 'With no net flux, diffusion and drift balance exactly: dc/c = −(zF/RT)dφ. Integrating gives V<sub>m</sub> = (RT/zF) ln([S]<sub>o</sub>/[S]<sub>i</sub>), the Nernst potential. Step 5 gets there the long way.' },

    { at: '2.', id: 'fieldsize', type: 'num', unit: 'MV/m', answer: 14, tol: 0.7, placeholder: '1.0',
      q: 'With V<sub>m</sub> = −70 mV across a membrane 5 nm thick, how strong is the field? Answer in megavolts per metre.',
      explain: '70 mV / 5 nm = 0.070 V / 5 × 10⁻⁹ m = 1.4 × 10⁷ V/m = 14 MV/m. That is close to the field at which air breaks down into a spark.' },
    { at: '2.', id: 'ucl', type: 'num', unit: '', answer: 2.72, tol: 0.1, placeholder: '1.0',
      q: 'For Cl⁻ at V<sub>m</sub> = −70 mV and 25 °C (RT/F = 25.7 mV), what is u<sub>s</sub> = z<sub>s</sub>FV<sub>m</sub>/RT?',
      explain: 'u = (−1)(−70 mV)/(25.7 mV) ≈ +2.72. The two negatives cancel: z is −1 and V<sub>m</sub> is negative.' },

    { at: '3.', id: 'jconst', type: 'mc',
      q: 'Why is J<sub>s</sub> the same at every depth x?',
      options: [
        { t: 'The membrane is thin' },
        { t: 'At steady state ions can’t pile up anywhere inside the membrane', ok: true },
        { t: 'The field is constant' },
      ],
      explain: 'If more ions entered a slice than left it, the concentration there would change with time. Steady state rules that out, so the same flux passes every x.' },
    { at: '3.', id: 'fick', type: 'mc',
      q: 'With no voltage (u<sub>s</sub> → 0), what does the GHK flux equation become?',
      options: [
        { t: 'J<sub>s</sub> = P<sub>s</sub>([S]<sub>i</sub> − [S]<sub>o</sub>), Fick’s law', ok: true },
        { t: 'J<sub>s</sub> = 0' },
        { t: 'J<sub>s</sub> = P<sub>s</sub>[S]<sub>i</sub>' },
      ],
      explain: 'For small u, 1 − e<sup>−u</sup> ≈ u and e<sup>−u</sup> ≈ 1, so the u’s cancel and the flux is permeability times the concentration difference.' },

    { at: '4.', id: 'fluxes', type: 'mc',
      q: 'At the GHK resting potential, what is the flux of each individual ion?',
      options: [
        { t: 'Zero for every ion, so nothing moves' },
        { t: 'Not zero: K⁺ leaks out and Na⁺ leaks in, but the charges they carry cancel', ok: true },
        { t: 'Zero for the most permeant ion only' },
      ],
      explain: 'GHK only makes the total current zero. Each ion keeps leaking down its gradient, so without a pump the gradients slowly run down. The last questions on this page let you watch that happen.' },
    { at: '4.', id: 'anion', type: 'mc',
      q: 'Why does Cl⁻ appear with inside and outside swapped in the GHK equation?',
      options: [
        { t: 'Cl⁻ is more concentrated outside' },
        { t: 'Its charge is negative, so it carries current the opposite way for the same flux', ok: true },
        { t: 'Cl⁻ is pumped' },
      ],
      explain: 'A Cl⁻ ion moving in carries the same current as a K⁺ ion moving out. Flipping z from +1 to −1 flips u, which swaps which concentration sits with the e<sup>−v</sup> factor.' },

    { at: '5.', id: 'tenfold', type: 'predict', unit: 'mV', tol: 6, placeholder: '+30',
      q: 'Only K⁺ is permeable, with [K⁺]<sub>i</sub> = 140 mM, at 25 °C. If [K⁺]<sub>o</sub> rises from 5 to 50 mM, by how many mV does V<sub>m</sub> change?',
      setup: { gPK: 1, gPNa: 0, gPCl: 0, gKi: 140, gKo: 5, gT: 25 },
      task: 'The calculator is set to K⁺ only, with [K⁺]<sub>o</sub> = 5 mM. Drag [K⁺]<sub>o</sub> to 50 mM.',
      done: () => Math.abs(val('gKo') - 50) <= 2,
      measure: (start) => vm() - start,
      explain: 'A tenfold change in [K⁺]<sub>o</sub> shifts E<sub>K</sub> by (RT/F)·ln 10 ≈ 59 mV at 25 °C, whatever the starting values.' },

    { at: 'How the simulation', id: 'channels', type: 'mc',
      q: 'In the simulation, what sets the permeability of each ion?',
      options: [
        { t: 'A number in the GHK equation' },
        { t: 'How many channels there are for it', ok: true },
        { t: 'How fast its ions move' },
      ],
      explain: 'Every channel is always open, so more channels for an ion means more crossings for the same gradient. The GHK formula is only used to draw the reference line.' },

    { at: 'calc', id: 'naup', type: 'predict', unit: 'mV', tol: 8, placeholder: '−50',
      q: 'Start from the mammal values: P<sub>K</sub> : P<sub>Na</sub> : P<sub>Cl</sub> = 1 : 0.04 : 0.45, which gives V<sub>m</sub> ≈ −67 mV. Raise P<sub>Na</sub> tenfold to 0.4. What is the new V<sub>m</sub>?',
      setup: { gPK: 1, gPNa: 0.04, gPCl: 0.45, gKi: 140, gKo: 5, gNai: 14, gNao: 143.5, gCli: 10.5, gClo: 148.5, gT: 25 },
      task: 'The calculator is set to the mammal values. Drag P for Na⁺ to 0.40.',
      done: () => Math.abs(val('gPNa') - 0.4) <= 0.03,
      measure: () => vm(),
      explain: 'V<sub>m</sub> moves toward E<sub>Na</sub> (about +60 mV) but stops near −30 mV, because K⁺ and Cl⁻ still pull toward their own Nernst potentials. GHK is a compromise weighted by permeability.' },
    { at: 'calc', id: 'zero', type: 'goal',
      q: 'Starting from the mammal values, change only P<sub>Na</sub> until V<sub>m</sub> is 0 mV (within 1 mV). What ratio P<sub>Na</sub>/P<sub>K</sub> does it take?',
      setup: { gPK: 1, gPNa: 0.04, gPCl: 0.45, gKi: 140, gKo: 5, gNai: 14, gNao: 143.5, gCli: 10.5, gClo: 148.5, gT: 25 },
      task: 'Drag P for Na⁺ until V<sub>m</sub> reads 0.',
      done: () => Math.abs(vm()) < 1,
      result: () => `You reached 0 mV with P<sub>Na</sub>/P<sub>K</sub> = ${(val('gPNa') / Math.max(val('gPK'), 1e-9)).toFixed(2)}.`,
      explain: 'Na⁺ has to be about as permeant as K⁺ and Cl⁻ together before it cancels them. At the peak of an action potential P<sub>Na</sub> rises to about 20 times P<sub>K</sub>, which is how V<sub>m</sub> overshoots 0.' },
    { at: 'calc', id: 'simmatch', type: 'mc',
      q: 'Press “Send to simulation” and let it run for a while. Does the simulated voltage land exactly on the dotted GHK line?',
      options: [
        { t: 'Yes, exactly' },
        { t: 'Close, but a few mV off', ok: true },
        { t: 'No, it ends up near E<sub>K</sub>' },
      ],
      explain: 'The simulation is 2D: ions crowd a little at busy channel mouths and the charge layers carry a few mV, so it lands close to GHK but not on it.' },
    { at: 'calc', id: 'rundown', type: 'mc',
      q: 'In the simulation, uncheck both “Hold … concentrations fixed” boxes and leave the pump off. What happens to V<sub>m</sub> over time?',
      options: [
        { t: 'It stays where it is' },
        { t: 'It drifts toward 0 mV as the gradients run down', ok: true },
        { t: 'It becomes more negative' },
      ],
      explain: 'Each ion keeps leaking (step 4), so the concentrations slowly even out and every Nernst potential heads toward 0. Turn on the Na⁺/K⁺ pump to hold the gradients up.' },
  ];

  // ---------- calculator access (through the page's own sliders) ----------
  const el = id => document.getElementById(id);
  const val = id => {                       // the value a calculator slider shows, as numbers
    const e = el(id), v = parseFloat(e.value);
    if (!e.hasAttribute('data-log')) return v;
    if (id.startsWith('gP') && v <= parseFloat(e.min) + 1e-9) return 0;
    return Math.pow(10, v);
  };
  const vm = () => parseFloat((el('gV')?.textContent || '').replace('−', '-'));
  function setCalc(values) {
    const ghkBtn = el('gModeGHK');                // the questions set permeabilities, so use the GHK model
    if (ghkBtn && ghkBtn.getAttribute('aria-pressed') !== 'true') ghkBtn.click();
    Object.entries(values).forEach(([id, v]) => {
      const e = el(id); if (!e) return;
      e.value = e.hasAttribute('data-log') ? Math.log10(Math.max(v, Math.pow(10, parseFloat(e.min)))).toFixed(4) : v;
    });
    el('gT')?.dispatchEvent(new Event('input', { bubbles: true }));    // any slider redraws the whole calculator
  }

  // ---------- saved answers ----------
  const KEY = 'argus.ghkQuestions.v1';   // named before the rename to Newt; kept so saved answers stay
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(KEY) || '{}') || {}; } catch (e) { saved = {}; }
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(saved)); } catch (e) { /* private window */ } };

  // ---------- styles ----------
  const css = document.createElement('style');
  css.textContent = `
.gq-group{display:flex;flex-direction:column;gap:10px;margin:12px 0 4px}
.gq [hidden]{display:none!important}
.gq{border:1px solid var(--line);border-left:3px solid var(--accent);border-radius:0 8px 8px 0;background:color-mix(in srgb, var(--accent) 4%, var(--surface));padding:12px 14px;max-width:72ch}
.gq-h{font-size:12px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:var(--accent);margin:0 0 4px}
.gq-q{margin:0 0 10px;font-size:14.5px}
.gq-opts{display:flex;flex-direction:column;gap:6px}
.gq-opts button{text-align:left;font-size:13.5px}
.gq-opts button.ok{border-color:var(--cl);background:color-mix(in srgb, var(--cl) 14%, var(--surface))}
.gq-opts button.no{border-color:var(--pos);background:color-mix(in srgb, var(--pos) 12%, var(--surface))}
.gq-row{display:flex;flex-wrap:wrap;align-items:center;gap:8px;font-size:13px;color:var(--muted)}
.gq-row input{width:110px;font:inherit;font-family:var(--font-mono);font-size:13px;padding:6px 8px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink);text-align:right}
.gq-err{font-size:13px;color:var(--pos);margin:6px 0 0;min-height:0}
.gq-err:empty{display:none}
.gq-fb{font-size:13.5px;margin:10px 0 0;padding:8px 10px;border-radius:6px;background:var(--surface);border:1px solid var(--line)}
.gq-fb b.ok{color:var(--cl)} .gq-fb b.no{color:var(--pos)}
.gq-live{font-family:var(--font-mono);font-size:13px;color:var(--ink);margin:8px 0 0}
.gq-bar{display:flex;flex-wrap:wrap;align-items:center;gap:10px;font-size:13px;color:var(--muted);margin:12px 0 0}
.gq-bar b{color:var(--ink);font-weight:600}
.gq-bar button{font-size:12.5px;padding:4px 10px}
.calc ~ .gq-group{margin:0}
`;
  document.head.appendChild(css);

  // ---------- building the cards ----------
  const total = QUESTIONS.length;
  const bar = document.createElement('div');
  bar.className = 'gq-bar';
  bar.innerHTML = '<span>Questions: <b class="gq-count"></b> of ' + total + ' answered</span><button type="button">Reset answers</button>';
  const updateCount = () => { bar.querySelector('.gq-count').textContent = QUESTIONS.filter(q => saved[q.id]?.done).length; };
  bar.querySelector('button').addEventListener('click', () => {
    if (!confirm('Clear all your answers to these questions?')) return;
    saved = {}; save(); location.reload();
  });

  const watchers = [];                      // predict/goal cards waiting on the calculator
  const onCalc = () => watchers.slice().forEach(w => w());
  ['input', 'click'].forEach(t => section.addEventListener(t, () => requestAnimationFrame(onCalc)));

  const fmt = (v, unit) => (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v).toFixed(1) + (unit ? ' ' + unit : '');
  const parseNum = s => parseFloat(String(s).trim().replace(/−/g, '-').replace(/^\+/, '').replace(/×\s*10\^?/, 'e'));
  const verdict = ok => `<b class="${ok ? 'ok' : 'no'}">${ok ? 'Right.' : 'Not quite.'}</b> `;

  function card(q, n, of) {
    const c = document.createElement('div');
    c.className = 'gq';
    c.innerHTML = `<p class="gq-h">${q.type === 'predict' ? 'Predict, then check' : q.type === 'goal' ? 'Try it' : 'Check your understanding'}${of > 1 ? ` · ${n} of ${of}` : ''}</p><p class="gq-q">${q.q}</p>`;
    const fb = document.createElement('div');
    const show = html => { fb.innerHTML = `<p class="gq-fb">${html}</p>`; };
    const done = rec => { saved[q.id] = Object.assign({ done: true }, rec); save(); updateCount(); };

    if (q.type === 'mc') {
      const opts = document.createElement('div'); opts.className = 'gq-opts';
      const pick = (i, store) => {
        [...opts.children].forEach(b => b.classList.remove('ok', 'no'));
        const ok = !!q.options[i].ok;
        opts.children[i].classList.add(ok ? 'ok' : 'no');
        show(verdict(ok) + q.explain);
        if (store) done({ choice: i, ok });
      };
      q.options.forEach((o, i) => {
        const b = document.createElement('button'); b.type = 'button'; b.innerHTML = o.t;
        b.addEventListener('click', () => pick(i, true));
        opts.appendChild(b);
      });
      c.appendChild(opts);
      if (saved[q.id]?.choice != null) pick(saved[q.id].choice, false);
    } else if (q.type === 'num') {
      const row = document.createElement('div'); row.className = 'gq-row';
      row.innerHTML = `<input type="text" inputmode="decimal" placeholder="${q.placeholder || ''}" aria-label="Your answer">${q.unit ? `<span>${q.unit}</span>` : ''}<button type="button">Check</button>`;
      const inp = row.querySelector('input'), err = document.createElement('p'); err.className = 'gq-err';
      const check = store => {
        const v = parseNum(inp.value);
        if (inp.value.trim() === '' || !isFinite(v)) { err.textContent = 'Enter a number first'; return; }
        const ok = Math.abs(v - q.answer) <= q.tol;
        show(verdict(ok) + q.explain);
        if (store) done({ value: inp.value, ok });
      };
      inp.addEventListener('input', () => { err.textContent = ''; });
      inp.addEventListener('keydown', e => { if (e.key === 'Enter') check(true); });
      row.querySelector('button').addEventListener('click', () => check(true));
      c.append(row, err);
      if (saved[q.id]?.value != null) { inp.value = saved[q.id].value; check(false); }
    } else {                                // predict or goal: works through the calculator
      const isPredict = q.type === 'predict';
      const row = document.createElement('div'); row.className = 'gq-row';
      row.innerHTML = (isPredict ? `<input type="text" inputmode="decimal" placeholder="${q.placeholder || ''}" aria-label="Your prediction"><span>${q.unit}</span><button type="button">Lock in prediction</button>` : '<button type="button">Set up the calculator</button>')
        + '<button type="button" class="gq-go" hidden>Show calculator</button>';
      const inp = row.querySelector('input'), go = row.querySelector('.gq-go');
      const err = document.createElement('p'); err.className = 'gq-err';
      const live = document.createElement('p'); live.className = 'gq-live'; live.hidden = true;
      go.addEventListener('click', () => document.querySelector('#ghk-equation .calc')?.scrollIntoView({ behavior: 'smooth', block: 'start' }));
      let pred = null, start = 0;
      const finish = (got, store) => {
        if (isPredict) {
          const ok = Math.abs(got - pred) <= q.tol;
          show(`You predicted <b>${fmt(pred, q.unit)}</b>; the calculator gives <b>${fmt(got, q.unit)}</b>. ` + verdict(ok) + q.explain);
          if (store) done({ pred, got, ok });
        } else {
          show(`<b class="ok">Done.</b> ${saved[q.id]?.result || q.result()} ` + q.explain);
          if (store) done({ result: q.result() });
        }
      };
      const watch = () => {
        live.innerHTML = 'Calculator now: V<sub>m</sub> = ' + fmt(vm(), 'mV');
        if (!q.done()) return;
        watchers.splice(watchers.indexOf(watch), 1);
        live.hidden = true; go.hidden = true;
        finish(isPredict ? q.measure(start) : 0, true);
      };
      const begin = () => {
        if (isPredict) {
          const v = parseNum(inp.value);
          if (inp.value.trim() === '' || !isFinite(v)) { err.textContent = 'Enter a prediction first'; return; }
          pred = v; inp.disabled = true;
        }
        row.querySelector('button').disabled = true;
        row.querySelector('button').textContent = isPredict ? 'Locked in' : 'Set up';
        setCalc(q.setup); start = vm();
        show(q.task); live.hidden = false; go.hidden = false;
        if (!watchers.includes(watch)) watchers.push(watch);
        watch();
      };
      inp?.addEventListener('input', () => { err.textContent = ''; });
      inp?.addEventListener('keydown', e => { if (e.key === 'Enter') begin(); });
      row.querySelector('button').addEventListener('click', begin);
      c.append(row, err, live);
      const s = saved[q.id];
      if (s?.done) {                        // answered before: show the result, leave the calculator alone
        row.hidden = true;
        if (isPredict) { pred = s.pred; finish(s.got, false); } else finish(0, false);
      }
    }
    c.appendChild(fb);
    return c;
  }

  // ---------- placing them ----------
  const heads = [...section.querySelectorAll('.deriv h3')];
  const groups = new Map();
  QUESTIONS.forEach(q => { if (!groups.has(q.at)) groups.set(q.at, []); groups.get(q.at).push(q); });
  groups.forEach((qs, at) => {
    const g = document.createElement('div'); g.className = 'gq-group';
    qs.forEach((q, i) => g.appendChild(card(q, i + 1, qs.length)));
    if (at === 'calc') {
      const calc = section.querySelector('.calc');
      if (calc) calc.after(g);
      return;
    }
    const h = heads.find(h => h.textContent.trim().startsWith(at));
    if (!h) return;                         // heading renamed: skip its questions rather than misplace them
    let end = h.nextElementSibling;         // the step runs until the next heading
    while (end && end.tagName !== 'H3') end = end.nextElementSibling;
    if (end) end.before(g); else h.parentElement.appendChild(g);
  });
  const intro = section.querySelector('.deriv');
  if (intro) intro.appendChild(bar);
  updateCount();
})();
