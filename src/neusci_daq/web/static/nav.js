// The tabs across the top, the same on every page. The launch's key (after #) goes along.
'use strict';
(function () {
  const TABS = [['Record', 'live.html'], ['Stimulator', 'stimulator.html'], ['Analysis', 'analysis.html'],
                ['DataPad', 'datapad.html'], ['Plot Canvas'], ['Simulators'], ['Lessons']];
  const here = location.pathname.split('/').pop();
  const nav = document.createElement('nav');
  nav.className = 'apptabs';
  for (const [name, page] of TABS) {
    const a = document.createElement(page ? 'a' : 'span');
    a.textContent = name;
    if (page) a.href = page + location.hash;
    else a.title = 'Not in the HTML front end yet';
    if (page === here) a.setAttribute('aria-current', 'page');
    nav.append(a);
  }
  document.querySelector('.wrap').prepend(nav);
})();
