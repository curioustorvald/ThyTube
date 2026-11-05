// Small inline SVG icon set (24×24, stroke-based).
import { html } from '../vendor/preact-htm.js';

const P = {
  library: 'M4 5h16M4 12h16M4 19h10',
  search: 'M11 4a7 7 0 1 1 0 14 7 7 0 0 1 0-14zM20 20l-4-4',
  manage: 'M4 7h10M18 7h2M4 17h4M12 17h8M14 4v6M8 14v6',
  status: 'M3 12h4l3-7 4 14 3-7h4',
  play: 'M7 4.5v15l12-7.5z',
  pause: 'M7 5h3v14H7zM14 5h3v14h-3z',
  back10: 'M11 6 6 10l5 4M6 10h8a5 5 0 0 1 0 10h-3',
  fwd10: 'M13 6l5 4-5 4M18 10h-8a5 5 0 0 0 0 10h3',
  full: 'M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5',
  exitfull: 'M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5',
  cc: 'M3 6h18v12H3zM10 10.5a2 2 0 1 0 0 3M16 10.5a2 2 0 1 0 0 3',
  gear: 'M12 9a3 3 0 1 1 0 6 3 3 0 0 1 0-6zM12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1 7 17M17 7l2.1-2.1',
  pin: 'M9 4h6l-1 6 3 3H7l3-3zM12 13v7',
  trash: 'M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13',
  refresh: 'M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7',
  close: 'M6 6l12 12M18 6 6 18',
  check: 'M5 12l5 5 9-10',
  download: 'M12 4v11M7 10l5 5 5-5M5 20h14',
  volume: 'M4 9h4l5-4v14l-5-4H4zM16 9a4 4 0 0 1 0 6M19 6a8 8 0 0 1 0 12',
  mute: 'M4 9h4l5-4v14l-5-4H4zM17 9l5 6M22 9l-5 6',
  next: 'M6 5l9 7-9 7zM18 5v14',
  shuffle: 'M4 7h3l10 10h3M4 17h3l3-3M14 10l3-3h3M18 4l3 3-3 3M18 14l3 3-3 3',
  repeat: 'M4 12V9a3 3 0 0 1 3-3h12M16 3l3 3-3 3M20 12v3a3 3 0 0 1-3 3H5M8 21l-3-3 3-3',
  stop: 'M6 6h12v12H6z',
  list: 'M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01',
  plus: 'M12 5v14M5 12h14',
  external: 'M14 4h6v6M20 4l-9 9M18 14v6H4V6h6',
  eye: 'M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zM12 9a3 3 0 1 1 0 6 3 3 0 0 1 0-6z',
  edit: 'M4 20h4L19 9l-4-4L4 16zM14 6l4 4',
  user: 'M12 4a4 4 0 1 1 0 8 4 4 0 0 1 0-8zM4 21c1-4 4-6 8-6s7 2 8 6',
};

export function Icon({ name, size }) {
  const d = P[name] || P.close;
  const fill = name === 'play' || name === 'pause' || name === 'next' || name === 'stop';
  return html`<svg viewBox="0 0 24 24" width=${size || 22} height=${size || 22} aria-hidden="true"
    fill=${fill ? 'currentColor' : 'none'} stroke="currentColor" stroke-width=${fill ? 0 : 2}
    stroke-linecap="round" stroke-linejoin="round"><path d=${d} /></svg>`;
}
