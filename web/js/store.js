// A tiny observable store plus a Preact hook.
import { useEffect, useState } from '../vendor/preact-htm.js';

const PROFILE_KEY = 'thytube.profile';

function loadProfile() {
  try { return JSON.parse(localStorage.getItem(PROFILE_KEY)) || null; } catch { return null; }
}

let state = {
  profile: loadProfile(),
  status: null,
  statusError: null,
  toasts: [],
  dialog: null, // { kind, props }
  pickingProfile: false,
};
const listeners = new Set();

export const store = {
  get: () => state,
  set(patch) {
    state = { ...state, ...(typeof patch === 'function' ? patch(state) : patch) };
    for (const l of listeners) l(state);
  },
  subscribe(fn) {
    listeners.add(fn);
    return () => listeners.delete(fn);
  },
};

export function useStore(select = (s) => s) {
  const [v, setV] = useState(() => select(state));
  useEffect(() => store.subscribe((s) => setV(() => select(s))), []);
  return v;
}

export function setProfile(p) {
  try {
    if (p) localStorage.setItem(PROFILE_KEY, JSON.stringify(p));
    else localStorage.removeItem(PROFILE_KEY);
  } catch { /* private mode */ }
  store.set({ profile: p, pickingProfile: false });
}

let toastId = 0;
export function toast(message, kind = '') {
  const id = ++toastId;
  store.set((s) => ({ toasts: [...s.toasts, { id, message, kind }] }));
  setTimeout(() => store.set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })), kind === 'bad' ? 6000 : 3500);
}

export function toastError(e) {
  toast(e && e.message ? e.message : String(e), 'bad');
}

export function openDialog(kind, props = {}) { store.set({ dialog: { kind, props } }); }
export function closeDialog() { store.set({ dialog: null }); }

// Per-device preferences.
const PREFS_KEY = 'thytube.prefs';
const defaults = { subSize: 'm', subBg: 0.6, subLang: 'auto', speed: 1, nextMode: 'channel', volume: 1 };
export function prefs() {
  try { return { ...defaults, ...(JSON.parse(localStorage.getItem(PREFS_KEY)) || {}) }; } catch { return { ...defaults }; }
}
export function setPref(key, value) {
  const p = { ...prefs(), [key]: value };
  try { localStorage.setItem(PREFS_KEY, JSON.stringify(p)); } catch { /* ignore */ }
  return p;
}
