// Hash router: #/path/segments?query
//
// Every history entry the app visits is stamped with its position (history.state.idx), so the
// in-app back button knows whether an earlier ThyTube entry really exists. history.length can't
// tell: it counts pages from before the app was opened, and in an iOS Home Screen app going
// "back" past the first entry reloads the page instead.
import { useEffect, useState } from '../vendor/preact-htm.js';

export function parseHash() {
  const raw = location.hash.replace(/^#/, '') || '/';
  const [path, qs = ''] = raw.split('?');
  const parts = path.split('/').filter(Boolean).map(decodeURIComponent);
  return { raw, path, parts, query: Object.fromEntries(new URLSearchParams(qs)) };
}

let idx = 0;

function stamp() {
  const s = history.state;
  if (s && typeof s.idx === 'number') {
    idx = s.idx; // revisiting a known entry (back/forward)
  } else {
    // A new entry (first load, or a plain link to #/…): one past the entry we came from.
    if (stamp.seen) idx += 1;
    history.replaceState({ ...(s || {}), idx }, '');
  }
  stamp.seen = true;
}
stamp.seen = false;
stamp();

/** True when the previous history entry is a ThyTube page we navigated from. */
export function canGoBack() {
  return idx > 0;
}

export function back() {
  if (canGoBack()) history.back();
  else go('/', null, { replace: true });
}

export function useRoute() {
  const [route, setRoute] = useState(parseHash);
  useEffect(() => {
    // WebKit doesn't always fire hashchange when going back to a pushState entry, so listen to
    // popstate as well; when both fire, the second is a no-op.
    const on = () => {
      stamp();
      const next = parseHash();
      setRoute((r) => (r.raw === next.raw ? r : next));
    };
    window.addEventListener('hashchange', on);
    window.addEventListener('popstate', on);
    return () => {
      window.removeEventListener('hashchange', on);
      window.removeEventListener('popstate', on);
    };
  }, []);
  return route;
}

export function href(path, query) {
  let h = '#' + path;
  if (query) {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(query)) if (v !== undefined && v !== null && v !== '') qs.set(k, v);
    const s = qs.toString();
    if (s) h += '?' + s;
  }
  return h;
}

export function go(path, query, { replace = false } = {}) {
  const h = href(path, query);
  if (replace) history.replaceState({ idx }, '', h);
  else history.pushState({ idx: idx + 1 }, '', h);
  window.dispatchEvent(new HashChangeEvent('hashchange'));
}
