// Formatting helpers.

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

const rtf = new Intl.RelativeTimeFormat('en-GB', { numeric: 'auto' });
export function ago(iso) {
  if (!iso) return '';
  const diff = (new Date(iso).getTime() - Date.now()) / 1000;
  const abs = Math.abs(diff);
  const units = [['year', 31536000], ['month', 2592000], ['week', 604800], ['day', 86400], ['hour', 3600], ['minute', 60]];
  for (const [unit, secs] of units) if (abs >= secs) return rtf.format(Math.round(diff / secs), unit);
  return diff < 0 ? 'just now' : 'in a moment';
}

export function fmtTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  const sameDay = d.toDateString() === new Date().toDateString();
  return sameDay
    ? d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
    : d.toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}

export function fmtDate(iso) {
  if (!iso) return '';
  return new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
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

export const STATE_LABEL = {
  cached: 'Cached', queued: 'Queued', downloading: 'Downloading', evicted: 'Removed', filtered: 'Skipped',
  unavailable: 'Unavailable', failed: 'Failed', deferred_space: 'Waiting for space', discovered: 'Discovered',
};
export const STATE_BADGE = {
  cached: 'ok', queued: 'info', downloading: 'info', evicted: '', filtered: '', unavailable: 'bad', failed: 'bad', deferred_space: 'warn',
};

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
