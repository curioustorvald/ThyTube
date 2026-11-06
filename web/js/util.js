// Formatting helpers.
import { has, locale, t } from './i18n.js';

export function fmtDuration(s) {
  if (s === null || s === undefined || isNaN(s)) return '';
  s = Math.max(0, Math.floor(s));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  const mm = h ? String(m).padStart(2, '0') : String(m);
  return (h ? h + ':' : '') + mm + ':' + String(sec).padStart(2, '0');
}

export function fmtBytes(b) {
  if (b === null || b === undefined) return '–';
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0, v = b;
  while (v >= 1000 && i < u.length - 1) { v /= 1000; i++; }
  return (v >= 100 || i === 0 ? Math.round(v) : v.toFixed(1)) + ' ' + u[i];
}

export function ago(iso) {
  if (!iso) return '';
  const diff = (new Date(iso).getTime() - Date.now()) / 1000;
  const abs = Math.abs(diff);
  const units = [['year', 31536000], ['month', 2592000], ['week', 604800], ['day', 86400], ['hour', 3600], ['minute', 60]];
  for (const [unit, secs] of units) {
    if (abs >= secs) return t(`time.${diff < 0 ? 'past' : 'future'}.${unit}`, { count: Math.round(abs / secs) });
  }
  return t(diff < 0 ? 'time.justNow' : 'time.soon');
}

export function fmtTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  const sameDay = d.toDateString() === new Date().toDateString();
  return sameDay
    ? d.toLocaleTimeString(locale(), { hour: '2-digit', minute: '2-digit' })
    : d.toLocaleString(locale(), { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}

export function fmtDate(iso) {
  if (!iso) return '';
  return new Date(iso).toLocaleDateString(locale(), { day: 'numeric', month: 'short', year: 'numeric' });
}

// "1:02:03" / "4:05" → seconds
export function parseTimestamp(t) {
  const p = t.split(':').map(Number);
  if (p.some(isNaN)) return null;
  return p.reduce((acc, n) => acc * 60 + n, 0);
}

// Duration inputs accept "90", "1:30", "1h30m", "45m", "30s" → seconds (or null when empty).
export function parseDurationInput(v) {
  v = (v || '').trim().toLowerCase();
  if (!v) return null;
  if (/^\d+(:\d{1,2}){1,2}$/.test(v)) return parseTimestamp(v);
  const m = v.match(/^(?:(\d+)h)?\s*(?:(\d+)m(?:in)?)?\s*(?:(\d+)s)?$/);
  if (m && (m[1] || m[2] || m[3])) return (+m[1] || 0) * 3600 + (+m[2] || 0) * 60 + (+m[3] || 0);
  if (/^\d+$/.test(v)) return parseInt(v, 10) * 60; // bare number = minutes
  return NaN;
}

export function durationInputValue(s) {
  if (s === null || s === undefined) return '';
  return fmtDuration(s);
}

export const stateLabel = (state) => (has('state.' + state) ? t('state.' + state) : state);
export const STATE_BADGE = {
  cached: 'ok', queued: 'info', downloading: 'info', evicted: '', filtered: '', unavailable: 'bad', failed: 'bad', deferred_space: 'warn',
};

// The server records why a video was skipped in plain English (videos.filter_reason, preview items);
// show the known ones in the user's language and anything else as it came.
const REASONS = [
  [/^title: doesn't match include pattern$/, 'reason.noInclude'],
  [/^title: matches exclude pattern$/, 'reason.exclude'],
  [/^duration: shorter than (.+)$/, 'reason.tooShort'],
  [/^duration: longer than (.+)$/, 'reason.tooLong'],
  [/^shorts excluded$/, 'reason.shorts'],
  [/^streams excluded$/, 'reason.streams'],
  [/^before subscription \(beyond backfill\)$/, 'reason.beyondBackfill'],
  [/^before subscription$/, 'reason.beforeSub'],
  [/^cancelled$/, 'reason.cancelled'],
  [/^subscription (removed|deleted)$/, 'reason.subscriptionRemoved'],
  [/^deleted by user$/, 'reason.deletedByUser'],
];
export function reasonText(r) {
  if (!r) return r;
  for (const [rx, key] of REASONS) {
    const m = r.match(rx);
    if (m) return t(key, { duration: m[1] });
  }
  return r;
}

export function debounce(fn, ms) {
  let t;
  const d = (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
  d.cancel = () => clearTimeout(t);
  return d;
}

export function uuid() {
  if (crypto.randomUUID) return crypto.randomUUID();
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16);
  });
}

export function initials(name) {
  return (name || '?').trim().split(/\s+/).map((w) => w[0]).join('').slice(0, 2).toUpperCase();
}

export function looksLikeUrl(q) {
  q = q.trim();
  return /^(https?:\/\/)?(www\.|m\.|music\.)?(youtube\.com|youtu\.be|youtube-nocookie\.com)\//i.test(q)
    || /^@[\w.-]{2,}$/.test(q) || /^UC[\w-]{22}$/.test(q);
}
