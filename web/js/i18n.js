// UI strings. Every user-visible string lives in web/lang/<code>.json; the code only refers to keys.
//
//   t('nav.search')                       → "Seek"
//   t('manage.queueCount', { count: 3 })  → "Queue (3)"           ({name} placeholders)
//   t('common.videos', { count: 1 })      → "1 video"             (plural objects, picked by `count`)
//   t('library.emptyHint', { search: html`<a …>…</a>` })
//                                         → an array of parts, for markup inside a sentence
//
// The default language ('en') is always loaded and fills any key a translation lacks. To add a
// language: copy web/lang/en.json to web/lang/<code>.json, translate it, add it to LANGUAGES below
// and to SHELL in sw.js. The choice is per device: prefs().lang, or the browser's language.
import { prefs } from './store.js';

export const DEFAULT_LANG = 'en';
// code → name shown to the user (in that language).
export const LANGUAGES = { en: 'English (Early Modern)' };

let base = null; // the default language
let cur = null; // the chosen language (=== base when it is the default)
let plural = null;
const warned = new Set();

async function fetchLang(code) {
  const res = await fetch(`lang/${code}.json`);
  if (!res.ok) throw new Error(`lang/${code}.json: HTTP ${res.status}`);
  return res.json();
}

function chooseLang() {
  const want = prefs().lang;
  if (want && LANGUAGES[want]) return want;
  for (const l of navigator.languages || [navigator.language]) {
    const code = (l || '').toLowerCase().split('-')[0];
    if (LANGUAGES[code]) return code;
  }
  return DEFAULT_LANG;
}

/** Load the strings; main.js awaits this before the first render. */
export async function initI18n() {
  const code = chooseLang();
  base = await fetchLang(DEFAULT_LANG);
  cur = base;
  if (code !== DEFAULT_LANG) {
    try { cur = await fetchLang(code); } catch (e) { console.warn('language not loaded, using the default', e); }
  }
  plural = new Intl.PluralRules(locale());
  document.documentElement.lang = locale();
}

/** BCP 47 locale for Intl (dates, numbers, plural rules). */
export function locale() {
  return cur?._meta?.locale || base?._meta?.locale || 'en-GB';
}

function lookup(table, key) {
  let v = table;
  for (const k of key.split('.')) {
    if (v === null || typeof v !== 'object') return undefined;
    v = v[k];
  }
  return v;
}

export function has(key) {
  return lookup(cur, key) !== undefined || lookup(base, key) !== undefined;
}

export function t(key, params = {}) {
  let s = lookup(cur, key);
  if (s === undefined) s = lookup(base, key);
  if (s && typeof s === 'object') {
    // Plural forms, chosen by `count`.
    s = s[plural.select(Number(params.count ?? 0))] ?? s.other;
  }
  if (typeof s !== 'string') {
    if (!warned.has(key)) { warned.add(key); console.warn('missing string', key); }
    return key;
  }
  if (!s.includes('{')) return s;
  // Split around {name}: even indices are text, odd ones placeholder names.
  const parts = s.split(/\{(\w+)\}/);
  let rich = false;
  const out = parts.map((p, i) => {
    if (i % 2 === 0) return p;
    const v = params[p];
    if (v === undefined || v === null) return '';
    if (typeof v === 'object') { rich = true; return v; }
    return String(v);
  });
  return rich ? out.filter((x) => x !== '') : out.join('');
}
