// The app's own parts, on every page (nav.js loads this; app.py is the Python half): the
// session's name and the File menu (New, Open, Save, Save as, Import, Export), the light/dark
// switch, Esc to stop any stimulus, and a connection held open so that once no page is open,
// any stimulus stops and the outputs go to 0 V.
'use strict';
(function () {
  const H = {'X-Newt-Key': new URLSearchParams(location.hash.slice(1)).get('k') || ''};
  const root = document.documentElement;
  const mac = /Mac|iPhone|iPad/.test(navigator.platform);
  const KEY = mac ? '⌘' : 'Ctrl+';
  let S = null;                      // /app/state
  let working = false;               // a file command is going

  const css = document.createElement('link');
  css.rel = 'stylesheet';
  css.href = '/static/app.css';
  document.head.append(css);

  const get = (url) => fetch(url, {headers: H, cache: 'no-store'}).then((r) => r.json());
  const post = (d) => fetch('/app/cmd', {method: 'POST', headers: H, body: JSON.stringify(d)}).then((r) => r.json());
  const el = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  };

  // ---------------------------------------------------------------- the outputs
  // Held open while this page is; the server counts them (App.watch).
  (async function watch() {
    for (;;) {
      try {
        const r = await fetch('/app/watch', {headers: H, cache: 'no-store'});
        const rd = r.body.getReader();
        while (!(await rd.read()).done) { /* a byte every half second */ }
      } catch (e) { /* the server's gone: try again */ }
      await new Promise((ok) => setTimeout(ok, 1000));
    }
  })();

  // ---------------------------------------------------------------- a message at the bottom
  const toast = el('div', 'apptoast');
  toast.setAttribute('role', 'status');
  document.body.append(toast);
  let toastT = 0;
  function say(text, bad = false, ms = 6000) {
    clearTimeout(toastT);
    toast.textContent = text;
    toast.classList.toggle('bad', bad);
    toast.classList.toggle('on', !!text);
    if (text && ms) toastT = setTimeout(() => toast.classList.remove('on'), bad ? Math.max(ms, 10000) : ms);
  }

  // ---------------------------------------------------------------- the bar, right of the tabs
  const nav = document.querySelector('nav.apptabs');
  const bar = el('div', 'appbar');
  const sess = el('span', 'appsess');
  const fileBtn = el('button', 'appbtn', 'File ▾');
  fileBtn.setAttribute('aria-haspopup', 'menu');
  const themeBtn = el('button', 'appbtn appicon');
  themeBtn.setAttribute('aria-haspopup', 'menu');
  const help = el('a', 'appbtn appicon apphelp', '?');
  help.href = 'help.html' + location.hash;
  help.title = 'Help: keys, your files, and what the words on Analysis mean';
  if (location.pathname.endsWith('/help.html')) help.setAttribute('aria-current', 'page');
  bar.append(sess, fileBtn, themeBtn, help);
  if (nav) nav.append(bar);

  function showState(st) {
    if (!st || st.problem) return;
    S = st;
    sess.replaceChildren(el('span', 'appname', st.name));
    if (st.unsaved) {
      const m = el('span', 'appdot', '•');
      m.title = st.kind === 'newt' ? 'Changed since it was saved' : 'Not saved in a file yet';
      sess.append(m);
    }
    sess.title = st.file || st.folder || 'Nothing recorded yet';
    paintTheme();
  }
  const refresh = () => get('/app/state').then(showState).catch(() => {});
  refresh();
  setInterval(() => { if (!document.hidden) refresh(); }, 3000);

  // ---------------------------------------------------------------- menus
  let menu = null;
  function closeMenu() {
    if (!menu) return;
    menu.remove();
    menu = null;
    fileBtn.setAttribute('aria-expanded', 'false');
    themeBtn.setAttribute('aria-expanded', 'false');
  }
  function openMenu(btn, items) {
    closeMenu();
    menu = el('div', 'appmenu');
    menu.setAttribute('role', 'menu');
    for (const it of items) {
      if (it === '-') { menu.append(el('hr')); continue; }
      if (typeof it === 'string') { menu.append(el('div', 'apphead', it)); continue; }
      const b = el('button', it.on ? 'on' : '');
      b.setAttribute('role', 'menuitem');
      b.append(el('span', '', it.label));
      if (it.key) b.append(el('kbd', '', it.key));
      if (it.title) b.title = it.title;
      b.disabled = !!it.off;
      b.onclick = () => { closeMenu(); it.run(); };
      menu.append(b);
    }
    document.body.append(menu);
    const r = btn.getBoundingClientRect();
    menu.style.top = (r.bottom + 4) + 'px';
    menu.style.left = Math.max(8, Math.min(r.right - menu.offsetWidth, innerWidth - menu.offsetWidth - 8)) + 'px';
    btn.setAttribute('aria-expanded', 'true');
    menu.querySelector('button:not(:disabled)')?.focus();
  }
  document.addEventListener('pointerdown', (e) => {
    if (menu && !menu.contains(e.target) && e.target !== fileBtn && e.target !== themeBtn) closeMenu();
  });

  fileBtn.onclick = () => {
    if (menu && fileBtn.getAttribute('aria-expanded') === 'true') return closeMenu();
    const rec = !!(S && S.recording);
    const stop = rec ? 'Stop recording first' : '';
    const items = [
      {label: 'New session', run: () => file('new'), off: rec, title: stop},
      {label: 'Open…', key: KEY + 'O', run: () => file('open', 'newt'), off: rec, title: stop || 'A .newt file (or an .argus one)'},
      {label: 'Open a session folder…', run: () => file('open', 'folder'), off: rec, title: stop},
      {label: 'Save', key: KEY + 'S', run: () => file('save', 'newt'), off: rec, title: stop || (S && S.file) || ''},
      {label: 'Save as…', run: () => file('save_as', 'newt'), off: rec, title: stop},
      '-',
      {label: 'Import a LabChart or MATLAB .mat…', run: () => file('open', 'mat'), off: rec, title: stop},
      {label: 'Import an Argus file…', run: () => file('open', 'argus'), off: rec, title: stop || 'Newt was called Argus before'},
      {label: 'Export the trials as a .mat…', run: () => file('export', 'mat'), off: rec, title: stop || 'For LabChart or MATLAB'},
    ];
    const recent = (S && S.recent) || [];
    if (recent.length) {
      items.push('-', 'Open recent');
      for (const f of recent) items.push({label: f.name, title: f.path, off: rec, run: () => file('open', '', f.path)});
      items.push({label: 'Clear the list', run: () => post({cmd: 'forget'}).then(showState)});
    }
    openMenu(fileBtn, items);
  };

  // ---------------------------------------------------------------- light and dark
  const sysDark = () => matchMedia('(prefers-color-scheme: dark)').matches;
  const isDark = () => (root.dataset.theme || (sysDark() ? 'dark' : 'light')) === 'dark';
  function paintTheme() {
    themeBtn.textContent = isDark() ? '☾' : '☀';
    themeBtn.title = 'Light or dark' + (root.dataset.theme ? '' : ' (as the computer is set)');
  }
  paintTheme();
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', paintTheme);
  async function setTheme(t) {
    const was = isDark();
    if (t) root.dataset.theme = t;
    else delete root.dataset.theme;
    paintTheme();
    await post({cmd: 'theme', theme: t}).catch(() => {});
    if (isDark() === was) return;
    // Pages drawn with CSS follow by themselves; charts are drawn on canvases in the old colors,
    // so a page with any is drawn again, unless it does that itself (event.preventDefault()).
    const ev = new CustomEvent('newt-theme', {cancelable: true, detail: {dark: isDark()}});
    if (window.dispatchEvent(ev) && document.querySelector('canvas')) location.reload();
  }
  themeBtn.onclick = () => {
    if (menu && themeBtn.getAttribute('aria-expanded') === 'true') return closeMenu();
    const t = root.dataset.theme || '';
    openMenu(themeBtn, [
      {label: 'Light', on: t === 'light', run: () => setTheme('light')},
      {label: 'Dark', on: t === 'dark', run: () => setTheme('dark')},
      {label: 'As the computer is set', on: !t, run: () => setTheme('')},
    ]);
  };

  // ---------------------------------------------------------------- files
  const WORD = {open: 'Opening…', save: 'Saving…', save_as: 'Saving…', export: 'Exporting…', new: ''};
  async function file(cmd, kind = '', path = '') {
    if (working) return;
    working = true;
    try {
      const d = {cmd, kind};
      if (path) d.path = path;
      let st = await send(d);
      if (st.pick) {
        const got = await pick(st.pick);
        if (!got) return;
        st = await send({...d, path: got});
      }
      if (st.cancelled) return say('');
      showState(st);
      if (st.problem) return say(st.problem, true);
      say(st.message || '', st.error);
      if (st.opened) {               // see it: Analysis, or this page if it shows the session's analysis
        const page = location.pathname.split('/').pop();
        const here = ['analysis.html', 'datapad.html', 'singleplot.html', 'plotcanvas.html'].includes(page);
        if (cmd === 'new' || here) setTimeout(() => location.reload(), 400);
        else setTimeout(() => { location.href = 'analysis.html' + location.hash; }, 400);
      }
    } catch (e) {
      say("Couldn't reach Newt: " + e.message, true);
    } finally {
      working = false;
    }
  }
  function send(d) {
    if (WORD[d.cmd] && d.path) say(WORD[d.cmd], false, 0);
    return post(d);
  }

  // The page's own file dialog, when there's no window's (--browser, --serve): a folder's
  // folders and files, from the server. Resolves to a path, or null.
  const TITLES = {open: {newt: 'Open a Newt file', argus: 'Import an Argus file', mat: 'Import a LabChart or MATLAB .mat'},
                  save: {newt: 'Save as a Newt file', mat: 'Export the trials as a .mat'},
                  folder: {folder: 'Open a session folder'}};
  function pick(ask) {
    return new Promise((done) => {
      const dlg = el('dialog', 'apppick');
      const h = el('h2', '', TITLES[ask.mode]?.[ask.kind] || 'Choose a file');
      const places = el('div', 'appplaces');
      const where = el('div', 'appwhere');
      const up = el('button', 'appbtn', '↑ Up');
      const dirLine = el('span', 'appdir');
      where.append(up, dirLine);
      const list = el('div', 'applist');
      list.setAttribute('role', 'listbox');
      const foot = el('div', 'appfoot');
      const name = el('input');
      name.type = 'text';
      name.placeholder = 'Name';
      name.value = ask.name || '';
      const note = el('span', 'caption');
      const cancel = el('button', '', 'Cancel');
      const ok = el('button', 'primary', ask.mode === 'save' ? (ask.kind === 'mat' ? 'Export' : 'Save') : 'Open');
      if (ask.mode === 'save') {
        const lab = el('label', 'appname', 'Name ');
        lab.append(name);
        foot.append(lab);
      } else foot.append(note, el('span', 'spacer'));
      foot.append(cancel, ok);
      dlg.append(h, places, where, list, foot);
      document.body.append(dlg);
      let here = null, chosen = null;
      const sep = () => (here.dir.includes('\\') && !here.dir.includes('/') ? '\\' : '/');
      const join = (n) => here.dir.replace(/[\\/]$/, '') + sep() + n;
      const finish = (path) => { dlg.close(); dlg.remove(); done(path); };

      function canOpen() {
        if (ask.mode === 'save') ok.disabled = !name.value.trim();
        else if (ask.mode === 'folder') ok.disabled = !(chosen ? chosen.session : here && here.session);
        else ok.disabled = !(chosen && !chosen.dir);
        if (ask.mode === 'folder') {
          note.textContent = chosen && !chosen.session ? `${chosen.name} has no trials: double-click to look inside.`
            : here && here.session && !chosen ? 'This folder is a session.' : '';
        }
      }
      async function go(dir) {
        const st = await get('/app/files?' + new URLSearchParams({dir, kind: ask.kind}));
        if (st.problem) return say(st.problem, true);
        here = st;
        chosen = null;
        const home = (st.places.find((p) => p.name === 'Home') || {}).path;
        let shown = home && st.dir.startsWith(home) ? '~' + st.dir.slice(home.length) : st.dir;
        if (shown.length > 72) shown = '…' + shown.slice(-71);
        dirLine.textContent = shown;
        dirLine.title = st.dir;
        up.disabled = !st.parent;
        places.replaceChildren(...st.places.map((p) => {
          const b = el('button', 'appbtn', p.name);
          b.onclick = () => go(p.path);
          return b;
        }));
        list.replaceChildren();
        if (!st.entries.length) list.append(el('div', 'appempty', ask.mode === 'folder' ? 'No folders here.' : 'Nothing here to open.'));
        for (const it of st.entries) {
          const row = el('div', 'approw' + (it.dir ? ' dir' : ''));
          row.setAttribute('role', 'option');
          row.tabIndex = -1;
          row.append(el('span', 'appico', it.dir ? (it.session ? '▤' : '▸') : '·'), el('span', 'appfname', it.name),
                     el('span', 'appmeta', it.dir ? (it.session ? 'Session' : 'Folder') : size(it.size) + ' · ' + when(it.mtime)));
          row.onclick = () => {
            for (const r of list.children) r.removeAttribute('aria-selected');
            row.setAttribute('aria-selected', 'true');
            chosen = it;
            if (!it.dir && ask.mode === 'save') name.value = it.name;
            canOpen();
          };
          row.ondblclick = () => {
            if (it.dir && !(ask.mode === 'folder' && it.session)) go(it.path);
            else choose();
          };
          list.append(row);
        }
        canOpen();
      }
      function choose() {
        if (ok.disabled) return;
        if (ask.mode === 'save') {
          let n = name.value.trim();
          const end = ask.kind === 'mat' ? '.mat' : '.newt';
          if (!n.toLowerCase().endsWith(end) && !(ask.kind === 'newt' && n.toLowerCase().endsWith('.argus'))) n += end;
          if (here.entries.some((e) => !e.dir && e.name === n) && !confirm(`${n} is already there. Replace it?`)) return;
          return finish(join(n));
        }
        if (ask.mode === 'folder') return finish(chosen ? chosen.path : here.dir);
        finish(chosen.path);
      }
      up.onclick = () => here && here.parent && go(here.parent);
      cancel.onclick = () => finish(null);
      ok.onclick = choose;
      name.oninput = canOpen;
      name.onkeydown = (e) => { if (e.key === 'Enter') choose(); };
      dlg.addEventListener('cancel', (e) => { e.preventDefault(); finish(null); });
      dlg.showModal();
      go(ask.dir);
      if (ask.mode === 'save') { name.focus(); name.setSelectionRange(0, name.value.replace(/\.[^.]*$/, '').length); }
    });
  }
  const size = (n) => n < 1e3 ? n + ' B' : n < 1e6 ? (n / 1e3).toFixed(0) + ' kB' : n < 1e9 ? (n / 1e6).toFixed(1) + ' MB'
    : (n / 1e9).toFixed(2) + ' GB';
  const when = (t) => new Date(t * 1000).toLocaleString([], {dateStyle: 'medium', timeStyle: 'short'});

  // ---------------------------------------------------------------- keys
  // Capturing, so a page's own Esc (closing a box, clearing a pick) can't keep it from stopping.
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      closeMenu();
      post({cmd: 'stop'}).then((st) => { if (st.stopped) say('Stimulus stopped: the outputs are at 0 V.'); }).catch(() => {});
      return;
    }
    const k = e.key.toLowerCase();
    if ((mac ? e.metaKey : e.ctrlKey) && !e.altKey && (k === 's' || k === 'o')) {
      e.preventDefault();
      if (S && S.recording) return say('Stop recording first.', true);
      if (k === 'o') file('open', 'newt');
      else file(e.shiftKey ? 'save_as' : 'save', 'newt');
    }
  }, true);
})();
