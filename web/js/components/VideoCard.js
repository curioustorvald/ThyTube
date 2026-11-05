import { html } from '../../vendor/preact-htm.js';
import { href } from '../router.js';
import { ago, fmtDuration, STATE_BADGE, STATE_LABEL } from '../util.js';

export function progressFraction(v) {
  const pos = v.me?.position_s || 0;
  if (!pos || !v.duration_s) return 0;
  return Math.min(1, pos / v.duration_s);
}

export function VideoBadges({ v, showState = false }) {
  const badges = [];
  if (showState || v.state !== 'cached') badges.push(html`<span class="badge ${STATE_BADGE[v.state] || ''}">${STATE_LABEL[v.state] || v.state}</span>`);
  if (v.pinned) badges.push(html`<span class="badge info">Pinned</span>`);
  if (v.codec_warning) badges.push(html`<span class="badge warn" title="${v.vcodec}/${v.acodec}">Not H.264</span>`);
  if (v.me && v.state === 'cached') {
    if (progressFraction(v) > 0) badges.push(html`<span class="badge accent">In progress</span>`);
    else if (!v.me.watch_count) badges.push(html`<span class="badge">Unwatched</span>`);
  }
  return badges.length ? html`<div class="vbadges">${badges}</div>` : null;
}

export function VideoCard({ v, sub, showChannel = true, onClick, extra }) {
  const frac = progressFraction(v);
  const unwatched = v.me && !v.me.watch_count && !frac && v.state === 'cached';
  const line = [showChannel && v.channel?.title, v.published_at && ago(v.published_at)].filter(Boolean).join(' · ');
  return html`
    <a class="vcard ${v.state === 'evicted' ? 'dim' : ''}" href=${href('/watch/' + v.id)} onClick=${onClick}>
      <div class="thumb">
        <img src=${v.thumb} alt="" loading="lazy" decoding="async" />
        ${unwatched && html`<span class="new-dot" title="Unwatched"></span>`}
        ${v.duration_s ? html`<span class="dur">${fmtDuration(v.duration_s)}</span>` : null}
        ${frac > 0 && html`<div class="prog"><span style=${{ width: frac * 100 + '%' }}></span></div>`}
      </div>
      <div class="vmeta">
        <div class="vtitle">${v.title || v.id}</div>
        <div class="vsub">${sub ?? line}</div>
        <${VideoBadges} v=${v} />
        ${extra}
      </div>
    </a>`;
}
