// Client-side VTT parsing for the subtitle overlay (the server already cleaned the files).

function secs(t) {
  const p = t.split(':').map(parseFloat);
  return p.length === 3 ? p[0] * 3600 + p[1] * 60 + p[2] : p[0] * 60 + p[1];
}

const TIME = /^\s*((?:\d+:)?\d{2}:\d{2}[.,]\d{3})\s+-->\s+((?:\d+:)?\d{2}:\d{2}[.,]\d{3})/;

export function parseVtt(text) {
  const lines = text.replace(/\r\n?/g, '\n').split('\n');
  const cues = [];
  for (let i = 0; i < lines.length; i++) {
    const m = lines[i].match(TIME);
    if (!m) continue;
    const body = [];
    i++;
    while (i < lines.length && lines[i] !== '' && !TIME.test(lines[i])) body.push(lines[i++]);
    i--;
    const txt = body.join('\n').replace(/<[^>]*>/g, '')
      .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&nbsp;/g, ' ').replace(/&amp;/g, '&');
    if (txt.trim()) cues.push({ start: secs(m[1].replace(',', '.')), end: secs(m[2].replace(',', '.')), text: txt });
  }
  return cues;
}

// Active cues at time t (binary search on start; cues are sorted).
export function activeCues(cues, t) {
  let lo = 0, hi = cues.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (cues[mid].start <= t) lo = mid + 1; else hi = mid;
  }
  const out = [];
  for (let i = lo - 1; i >= 0 && i >= lo - 8; i--) if (cues[i].end > t) out.unshift(cues[i]);
  return out;
}

const cache = new Map();
export async function loadCues(url) {
  if (cache.has(url)) return cache.get(url);
  const res = await fetch(url);
  if (!res.ok) throw new Error('subtitle fetch failed');
  const cues = parseVtt(await res.text());
  cache.set(url, cues);
  return cues;
}

export const LANG_NAMES = { en: 'English', ko: '한국어', ja: '日本語', de: 'Deutsch', fr: 'Français', es: 'Español' };
export const langName = (l) => LANG_NAMES[l] || l;
