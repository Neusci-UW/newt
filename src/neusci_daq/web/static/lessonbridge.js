// What the Qt app's web channel gives a lesson page (membranepage.LessonBridge), for the
// HTML front end: web/lessons.py loads this first in every lesson page's <head>, so
// lesson.js (the lesson editor's Save) and breadboard.js ("Build on breadboard") find it
// as window.__newtBridge, with the same calls and callbacks as the Qt bridge.
'use strict';
(function () {
  // The window with the launch's key after # (the Lessons tab, the lesson's frame's parent)
  const outer = (() => { try { void window.top.location.hash; return window.top; } catch (e) { return window; } })();
  const key = () => {
    for (const w of [window, outer]) {
      try { const k = new URLSearchParams(w.location.hash.slice(1)).get('k'); if (k) return k; } catch (e) { /* elsewhere */ }
    }
    return '';
  };
  async function post(path, d) {
    const r = await fetch(path, {method: 'POST', headers: {'X-Newt-Key': key()}, body: JSON.stringify(d)});
    const text = await r.text();
    let j = {};
    try { j = JSON.parse(text); } catch (e) { j = {}; }
    if (!r.ok && !j.problem) j.problem = r.status === 404 ? 'not in this Newt' : text || 'error ' + r.status;
    return j;
  }
  const tab = () => { try { return window.parent !== window ? window.parent.NewtLessons || null : null; } catch (e) { return null; } };

  const bridge = {
    hasBreadboard(cb) { cb(!!key()); },
    toBreadboard(spec, cb) {
      let circuit;
      try { circuit = JSON.parse(spec); } catch (e) { return cb(String(e)); }
      post('/breadboard/cmd', {cmd: 'load', circuit}).then((j) => {
        if (j.problem) return cb(j.problem === 'not in this Newt' ? "the Breadboard simulator isn't in this Newt yet" : j.problem);
        cb('');
        outer.location.href = '/static/simulators.html?show=breadboard' + outer.location.hash;   // the Simulators tab
      }, (e) => cb(String(e)));
    },
    setEditing(on) { tab()?.editing?.(!!on); },        // the Lessons tab keeps its Edit lesson button in step
    save(name, text, cb) {
      post('/lessons/save', {name, text}).then((j) => cb(j.problem || ''), (e) => cb(String(e)));
    },
  };
  window.__newtBridge = Promise.resolve(bridge);
})();
