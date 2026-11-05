// Light/dark theme override. A classic (non-module) script loaded synchronously in <head>, so the
// stored choice applies before the first paint (no flash of the wrong theme). The choice is per
// device and lives in the same localStorage object as the other prefs (store.js `prefs()`).
// Exposes window.ThyTheme for the header toggle in main.js.
(function () {
  'use strict';
  var KEY = 'thytube.prefs';
  var MODES = ['auto', 'light', 'dark'];
  var BAR = { light: '#f4f2ee', dark: '#131516' }; // matches --bg in app.css
  var listeners = [];

  function readPrefs() {
    try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch (e) { return {}; }
  }
  function get() {
    var t = readPrefs().theme;
    return MODES.indexOf(t) >= 0 ? t : 'auto';
  }
  function apply(mode) {
    var root = document.documentElement;
    if (mode === 'auto') root.removeAttribute('data-theme');
    else root.setAttribute('data-theme', mode);
    // Browser chrome (address bar / PWA title bar) colour.
    var metas = document.querySelectorAll('meta[name="theme-color"]');
    for (var i = 0; i < metas.length; i++) {
      var m = metas[i];
      if (!m.dataset.media) m.dataset.media = m.getAttribute('media') || '';
      var own = /dark/.test(m.dataset.media) ? 'dark' : 'light';
      m.setAttribute('content', BAR[mode === 'auto' ? own : mode]);
    }
    var cs = document.querySelector('meta[name="color-scheme"]');
    if (cs) cs.setAttribute('content', mode === 'auto' ? 'light dark' : mode);
  }
  function set(mode) {
    var p = readPrefs();
    p.theme = mode;
    try { localStorage.setItem(KEY, JSON.stringify(p)); } catch (e) { /* private mode */ }
    apply(mode);
    for (var i = 0; i < listeners.length; i++) listeners[i](mode);
    return mode;
  }
  function cycle() { return set(MODES[(MODES.indexOf(get()) + 1) % MODES.length]); }
  function subscribe(fn) {
    listeners.push(fn);
    return function () { listeners = listeners.filter(function (l) { return l !== fn; }); };
  }

  apply(get());
  window.ThyTheme = { get: get, set: set, cycle: cycle, subscribe: subscribe };
})();
