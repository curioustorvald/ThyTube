// Service-worker registration and the install prompt.
import { store } from './store.js';

export const isStandalone = () =>
  window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
export const isIOS = () => /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);

let deferredPrompt = null;

export function initPwa() {
  // Service workers only run in a secure context (HTTPS, or localhost).
  if ('serviceWorker' in navigator && window.isSecureContext) {
    navigator.serviceWorker.register('sw.js').catch((e) => console.warn('service worker registration failed', e));
  }
  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    deferredPrompt = e;
    store.set({ canInstall: true });
  });
  window.addEventListener('appinstalled', () => {
    deferredPrompt = null;
    store.set({ canInstall: false });
  });
}

export async function promptInstall() {
  if (!deferredPrompt) return false;
  deferredPrompt.prompt();
  const { outcome } = await deferredPrompt.userChoice;
  deferredPrompt = null;
  store.set({ canInstall: false });
  return outcome === 'accepted';
}
