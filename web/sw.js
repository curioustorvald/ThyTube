// ThyTube service worker: makes the app installable and lets the shell open offline.
// Network-first for the app shell (the LAN server is fast and always newest); the cache is only a
// fallback. The API, media and avatars are never touched: video Range requests and live data must
// go straight to the server.
const BUILD = 'dev'; // stamped by deploy/remote-install.sh so each deploy installs a fresh worker
const CACHE = 'thytube-shell-' + BUILD;
const NETWORK_TIMEOUT_MS = 3000;

// Every file the shell needs. tests/test_pwa.py checks this list against web/.
const SHELL = [
  './',
  'index.html',
  'manifest.webmanifest',
  'icon.svg',
  'img/icon-192.png',
  'img/icon-512.png',
  'img/maskable-192.png',
  'img/maskable-512.png',
  'img/apple-touch-icon.png',
  'css/app.css',
  'vendor/preact-htm.js',
  'lang/en.json',
  'js/api.js',
  'js/i18n.js',
  'js/icons.js',
  'js/main.js',
  'js/pwa.js',
  'js/router.js',
  'js/store.js',
  'js/subs.js',
  'js/theme.js',
  'js/util.js',
  'js/components/Dialog.js',
  'js/components/FilterDialog.js',
  'js/components/Player.js',
  'js/components/Profiles.js',
  'js/components/StatusPill.js',
  'js/components/VideoCard.js',
  'js/views/Channel.js',
  'js/views/Library.js',
  'js/views/Manage.js',
  'js/views/Search.js',
  'js/views/Status.js',
  'js/views/Watch.js',
];

const PASSTHROUGH = /^\/(api|media|avatars)\//;

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL.map((u) => new Request(u, { cache: 'reload' })))).then(() => self.skipWaiting()),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k.startsWith('thytube-shell-') && k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

function timeout(ms) {
  return new Promise((_, reject) => setTimeout(() => reject(new Error('timeout')), ms));
}

async function networkFirst(request, fallbackUrl) {
  const cache = await caches.open(CACHE);
  try {
    const response = await Promise.race([fetch(request), timeout(NETWORK_TIMEOUT_MS)]);
    if (response.ok) cache.put(fallbackUrl || request, response.clone());
    return response;
  } catch (err) {
    const hit = await cache.match(fallbackUrl || request, { ignoreSearch: true });
    if (hit) return hit;
    throw err;
  }
}

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin || PASSTHROUGH.test(url.pathname)) return;
  if (req.mode === 'navigate') {
    event.respondWith(networkFirst(req, 'index.html'));
  } else {
    event.respondWith(networkFirst(req));
  }
});
