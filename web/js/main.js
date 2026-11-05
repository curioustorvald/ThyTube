import { html, render, useEffect } from '../vendor/preact-htm.js';
import { get } from './api.js';
import { Avatar, ProfileScreen } from './components/Profiles.js';
import { StatusPill } from './components/StatusPill.js';
import { Icon } from './icons.js';
import { href, useRoute } from './router.js';
import { store, useStore } from './store.js';
import { Channel } from './views/Channel.js';
import { Library } from './views/Library.js';
import { Manage } from './views/Manage.js';
import { Search } from './views/Search.js';
import { Status } from './views/Status.js';
import { Watch } from './views/Watch.js';

const TABS = [
  ['/', 'Library', 'library', (p) => p[0] === undefined || p[0] === 'channel' || p[0] === 'watch'],
  ['/search', 'Search', 'search', (p) => p[0] === 'search'],
  ['/manage', 'Manage', 'manage', (p) => p[0] === 'manage'],
  ['/status', 'Status', 'status', (p) => p[0] === 'status'],
];

// Status is polled every 10 s for the pill on every page.
function useStatusPolling() {
  useEffect(() => {
    let alive = true;
    const tick = () => get('/status')
      .then((s) => alive && store.set({ status: s, statusError: null }))
      .catch((e) => alive && store.set({ statusError: e.message }));
    tick();
    const iv = setInterval(() => document.visibilityState === 'visible' && tick(), 10000);
    const onVis = () => document.visibilityState === 'visible' && tick();
    document.addEventListener('visibilitychange', onVis);
    return () => { alive = false; clearInterval(iv); document.removeEventListener('visibilitychange', onVis); };
  }, []);
}

function Toasts() {
  const toasts = useStore((s) => s.toasts);
  return html`<div class="toasts" aria-live="polite">${toasts.map((t) => html`<div class="toast ${t.kind}" key=${t.id}>${t.message}</div>`)}</div>`;
}

function View({ route }) {
  const [a, b] = route.parts;
  switch (a) {
    case undefined: return html`<${Library} query=${route.query} />`;
    case 'watch': return html`<${Watch} id=${b} />`;
    case 'channel': return html`<${Channel} id=${b} />`;
    case 'search': return html`<${Search} query=${route.query} />`;
    case 'manage': return html`<${Manage} tab=${b} />`;
    case 'status': return html`<${Status} />`;
    default: return html`<div class="empty"><h2>Not found</h2><a href="#/">Back to the library</a></div>`;
  }
}

function App() {
  const route = useRoute();
  const { profile, pickingProfile } = useStore((s) => ({ profile: s.profile, pickingProfile: s.pickingProfile }));
  useStatusPolling();
  useEffect(() => { document.title = 'ThyTube'; }, [route.path]);
  if (!profile || pickingProfile) return html`<${ProfileScreen} /><${Toasts} />`;
  return html`<div class="shell">
    <header class="topbar">
      <a class="brand" href="#/"><img src="icon.svg" alt="" />ThyTube</a>
      <span class="spacer"></span>
      <${StatusPill} />
      <button class="ghost icon" aria-label=${'Profile: ' + profile.name} title=${profile.name}
        onClick=${() => store.set({ pickingProfile: true })}><${Avatar} p=${profile} /></button>
    </header>
    <nav class="tabbar" aria-label="Main">
      ${TABS.map(([path, label, icon, match]) => html`<a href=${href(path)} class=${match(route.parts) ? 'on' : ''}>
        <${Icon} name=${icon} /><span>${label}</span></a>`)}
    </nav>
    <main><${View} route=${route} /></main>
    <${Toasts} />
  </div>`;
}

const root = document.getElementById('app');
root.textContent = ''; // Preact doesn't remove the boot placeholder
render(html`<${App} />`, root);
