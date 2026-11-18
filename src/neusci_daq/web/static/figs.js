// What Single Plot and the Plot Canvas share (window.Figs): talking to web/figures.py, names
// with subscripts in lists, and saving a file (the window's own Save dialog in the app, a
// download in a browser).
'use strict';
(() => {
  const KEY = {'X-Newt-Key': new URLSearchParams(location.hash.slice(1)).get('k') || ''};
  const post = (url, d) => fetch(url, {method: 'POST', headers: KEY, body: JSON.stringify(d)});
  const get = (url) => fetch(url, {headers: KEY});
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  // what follows the first _ is a true subscript (I_B is I with B below)
  const sub = (name) => {
    const s = String(name), at = s.indexOf('_');
    return at < 0 ? esc(s) : `${esc(s.slice(0, at))}<sub>${esc(s.slice(at + 1))}</sub>`;
  };
  const SUBS = {0: '₀', 1: '₁', 2: '₂', 3: '₃', 4: '₄', 5: '₅', 6: '₆', 7: '₇', 8: '₈', 9: '₉', '+': '₊', '-': '₋',
    a: 'ₐ', e: 'ₑ', h: 'ₕ', i: 'ᵢ', j: 'ⱼ', k: 'ₖ', l: 'ₗ', m: 'ₘ', n: 'ₙ', o: 'ₒ', p: 'ₚ', r: 'ᵣ', s: 'ₛ', t: 'ₜ', u: 'ᵤ', v: 'ᵥ', x: 'ₓ'};
  const plain = (name) => {          // for a list (no markup there): Iₓ where Unicode has it, else the two run together
    const s = String(name), at = s.indexOf('_');
    if (at < 0) return s;
    const rest = s.slice(at + 1), small = [...rest].map((c) => SUBS[c] || SUBS[c.toLowerCase()]);
    return s.slice(0, at) + (small.every(Boolean) ? small.join('') : rest);
  };

  // Save a figure or the page: {what: 'single' | 'page', fmt, dpi}. Says what happened.
  async function save(d, say) {
    try {
      if (window.pywebview) {                 // the app's window: its own Save dialog, Python writes the file
        const r = await (await post('/fig/export', {...d, dialog: true})).json();
        if (r.problem) return say(r.problem, true);
        return say(r.saved ? `Saved ${r.saved}` : '');
      }
      const r = await post('/fig/export', d);
      if (!r.ok) return say((await r.json()).problem || 'Could not save it', true);
      const name = (/filename="([^"]+)"/.exec(r.headers.get('Content-Disposition') || '') || [])[1] || 'figure';
      const a = document.createElement('a');
      a.href = URL.createObjectURL(await r.blob());
      a.download = name;
      document.body.append(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 5000);
      say(`Saved as ${name} (in Downloads)`);
    } catch (e) { say('Newt is not answering.', true); }
  }

  // close any open .menu when clicking elsewhere
  document.addEventListener('mousedown', (e) => {
    for (const m of document.querySelectorAll('.menu:not([hidden])')) {
      if (!m.contains(e.target) && !(m.parentElement && m.parentElement.contains(e.target))) m.hidden = true;
    }
  });

  // a picture ready to show without a blank flash; never waiting long (a page in the background may not decode)
  const decoded = (im) => Promise.race([im.decode().catch(() => {}), new Promise((ok) => setTimeout(ok, 250))]);

  window.Figs = {KEY, post, get, esc, sub, plain, save, decoded};
})();
