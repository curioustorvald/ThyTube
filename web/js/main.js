import { html, render, useEffect, useState } from '../vendor/preact-htm.js';
import { get } from './api.js';
import { Avatar, ProfileScreen } from './components/Profiles.js';
import { StatusPill } from './components/StatusPill.js';
import { Icon } from './icons.js';
import { back, href, useRoute } from './router.js';
import { initPwa, isStandalone } from './pwa.js';
import { store, toast, useStore } from './store.js';
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

// Light/dark override (js/theme.js applies it before first paint). Cycles system → light → dark.
const THEMES = {
  auto: ['contrast', 'Theme: follows thy system', 'Theme now follows thy system'],
  light: ['sun', 'Theme: light', 'Light theme'],
  dark: ['moon', 'Theme: dark', 'Dark theme'],
};
function ThemeToggle() {
  const [mode, setMode] = useState(() => window.ThyTheme?.get() || 'auto');
  useEffect(() => window.ThyTheme?.subscribe(setMode), []);
  if (!window.ThyTheme) return null;
  const [icon, label] = THEMES[mode];
  return html`<button class="ghost icon" aria-label=${label + ' (change)'} title=${label}
    onClick=${() => toast(THEMES[window.ThyTheme.cycle()][2])}><${Icon} name=${icon} /></button>`;
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
      ${isStandalone() && ['watch', 'channel'].includes(route.parts[0]) && html`<button class="ghost icon" aria-label="Back"
        onClick=${back}><${Icon} name="back" /></button>`}
      <a class="brand" href="#/"><img src="icon.svg" alt="" />ThyTube</a>
      <span class="spacer"></span>
      <${StatusPill} />
      <${ThemeToggle} />
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

initPwa();
const root = document.getElementById('app');
root.textContent = ''; // Preact doesn't remove the boot placeholder
render(html`<${App} />`, root);
