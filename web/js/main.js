import { html, render, useEffect, useState } from '../vendor/preact-htm.js';
import { get } from './api.js';
import { initI18n, t } from './i18n.js';
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

// [path, label key, icon, is-active]
const TABS = [
  ['/', 'nav.library', 'library', (p) => p[0] === undefined || p[0] === 'channel' || p[0] === 'watch'],
  ['/search', 'nav.search', 'search', (p) => p[0] === 'search'],
  ['/manage', 'nav.manage', 'manage', (p) => p[0] === 'manage'],
  ['/status', 'nav.status', 'status', (p) => p[0] === 'status'],
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
const THEME_ICONS = { auto: 'contrast', light: 'sun', dark: 'moon' };
function ThemeToggle() {
  const [mode, setMode] = useState(() => window.ThyTheme?.get() || 'auto');
  useEffect(() => window.ThyTheme?.subscribe(setMode), []);
  if (!window.ThyTheme) return null;
  const label = t('theme.' + mode);
  return html`<button class="ghost icon" aria-label=${t('theme.change', { label })} title=${label}
    onClick=${() => toast(t(`theme.${window.ThyTheme.cycle()}Now`))}><${Icon} name=${THEME_ICONS[mode]} /></button>`;
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
    default: return html`<div class="empty"><h2>${t('nav.notFound')}</h2><a href="#/">${t('nav.backToLibrary')}</a></div>`;
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
      ${isStandalone() && ['watch', 'channel'].includes(route.parts[0]) && html`<button class="ghost icon" aria-label=${t('common.back')}
        onClick=${back}><${Icon} name="back" /></button>`}
      <a class="brand" href="#/"><img src="icon.svg" alt="" />ThyTube</a>
      <span class="spacer"></span>
      <${StatusPill} />
      <${ThemeToggle} />
      <button class="ghost icon" aria-label=${t('nav.profile', { name: profile.name })} title=${profile.name}
        onClick=${() => store.set({ pickingProfile: true })}><${Avatar} p=${profile} /></button>
    </header>
    <nav class="tabbar" aria-label=${t('nav.main')}>
      ${TABS.map(([path, label, icon, match]) => html`<a href=${href(path)} class=${match(route.parts) ? 'on' : ''}>
        <${Icon} name=${icon} /><span>${t(label)}</span></a>`)}
    </nav>
    <main><${View} route=${route} /></main>
    <${Toasts} />
  </div>`;
}

initPwa();
const root = document.getElementById('app');
try {
  await initI18n();
} catch (e) {
  // No strings to say it with, so this one stays in the code.
  root.textContent = 'ThyTube could not load its words: ' + e.message;
  throw e;
}
root.textContent = ''; // Preact doesn't remove the boot placeholder
render(html`<${App} />`, root);
