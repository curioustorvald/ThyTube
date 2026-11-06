import { html, useEffect, useState } from '../../vendor/preact-htm.js';
import { del, get, patch, post } from '../api.js';
import { Confirm } from '../components/Dialog.js';
import { FilterDialog } from '../components/FilterDialog.js';
import { VideoCard } from '../components/VideoCard.js';
import { Icon } from '../icons.js';
import { t } from '../i18n.js';
import { href } from '../router.js';
import { toast, toastError, useStore } from '../store.js';
import { ago, fmtBytes, fmtTime, reasonText } from '../util.js';
import { rulesSummary } from './Channel.js';

function usePolling(fn, ms, deps = []) {
  useEffect(() => {
    let alive = true;
    const run = () => alive && fn();
    run();
    const iv = setInterval(() => document.visibilityState === 'visible' && run(), ms);
    return () => { alive = false; clearInterval(iv); };
  }, deps);
}

// ---- subscriptions -----------------------------------------------------------------------

function Subscriptions() {
  const [items, setItems] = useState(null);
  const [edit, setEdit] = useState(null);
  const [remove, setRemove] = useState(null);
  const [alsoFiles, setAlsoFiles] = useState(false);
  const load = () => get('/subscriptions').then((r) => setItems(r.items)).catch(toastError);
  usePolling(load, 15000);

  async function toggle(s) {
    try { await patch('/subscriptions/' + s.id, { enabled: !s.enabled }); load(); } catch (e) { toastError(e); }
  }
  async function doRemove() {
    try {
      const r = await del('/subscriptions/' + remove.id, { delete_files: alsoFiles ? 'true' : 'false' });
      toast(r.deleting ? t('manage.unsubscribedDeleting', { count: r.deleting }) : t('manage.unsubscribed'));
      load();
    } catch (e) { toastError(e); }
  }
  if (items === null) return html`<p class="muted">${t('common.loading')}</p>`;
  if (!items.length) return html`<div class="empty"><h2>${t('manage.noSubs')}</h2>
    <p>${t('manage.noSubsHint', { search: html`<a href="#/search?type=channel">${t('library.searchLink')}</a>`, action: t('common.cacheThisCreator') })}</p></div>`;
  return html`<ul class="list">
    ${items.map((s) => html`<li>
      <div class="row" style="align-items:flex-start">
        ${s.channel?.avatar ? html`<img class="ch-avatar" src=${s.channel.avatar} alt="" />` : html`<span class="ch-avatar"></span>`}
        <div class="grow">
          <div class="row wrap"><a href=${href('/channel/' + s.channel_id)}><strong>${s.channel?.title || s.channel_id}</strong></a>
            ${!s.enabled && html`<span class="badge">${t('manage.paused')}</span>`}
            ${s.last_poll_status && s.last_poll_status !== 'ok' && html`<span class="badge bad" title=${s.last_poll_error}>${t('manage.lastCheckFailed')}</span>`}</div>
          <div class="small muted">${rulesSummary(s)}</div>
          <div class="tiny faint">${[
            t('manage.cachedSize', { count: s.cached_count, size: fmtBytes(s.cached_bytes) }),
            s.queued_count ? t('manage.queuedCount', { count: s.queued_count }) : null,
            s.last_polled_at ? t('manage.checked', { when: ago(s.last_polled_at) }) : t('manage.neverChecked'),
            s.next_poll_at && s.enabled ? t('manage.nextCheck', { time: fmtTime(s.next_poll_at) }) : null,
          ].filter(Boolean).join(' · ')}</div>
          ${s.last_poll_error && html`<div class="tiny" style="color:var(--bad)">${s.last_poll_error}</div>`}
          <div class="row wrap" style="margin-top:0.4rem">
            <button class="small" onClick=${() => setEdit(s)}><${Icon} name="edit" size="16" /> ${t('manage.rules')}</button>
            <button class="small" onClick=${() => post(`/subscriptions/${s.id}/poll`).then(() => toast(t('common.checkingUploads'))).catch(toastError)}><${Icon} name="refresh" size="16" /> ${t('common.checkNow')}</button>
            <button class="small" onClick=${() => toggle(s)}>${s.enabled ? t('manage.pause') : t('manage.resume')}</button>
            <button class="small danger" onClick=${() => { setAlsoFiles(false); setRemove(s); }}><${Icon} name="trash" size="16" /> ${t('manage.unsubscribe')}</button>
          </div>
        </div>
      </div></li>`)}
    ${edit && html`<${FilterDialog} channel=${{ id: edit.channel_id, ...(edit.channel || {}) }} subscription=${edit}
      onClose=${() => setEdit(null)} onSaved=${load} />`}
    ${remove && html`<${Confirm} title=${t('manage.unsubTitle')} danger confirmLabel=${t('manage.unsubscribe')} onConfirm=${doRemove} onClose=${() => setRemove(null)}
      message=${t('manage.unsubMessage', { channel: remove.channel?.title || t('common.thisChannel') })}>
      <label class="check"><input type="checkbox" checked=${alsoFiles} onChange=${(e) => setAlsoFiles(e.target.checked)} />
        ${t('manage.alsoDelete', { count: remove.cached_count })}</label>
      <p class="small muted">${t('manage.otherwise')}</p>
    <//>`}
  </ul>`;
}

// ---- queue -------------------------------------------------------------------------------

function ProgressLine({ p }) {
  if (!p) return html`<span class="small muted">${t('manage.starting')}</span>`;
  if (p.stage === 'metadata') return html`<span class="small muted">${t('common.fetchingDetails')}</span>`;
  if (p.stage === 'subtitles') return html`<span class="small muted">${t('common.fetchingSubtitles')}</span>`;
  const frac = p.total ? p.downloaded / p.total : 0;
  return html`<div><div class="bar"><span style=${{ width: frac * 100 + '%' }}></span></div>
    <div class="tiny muted" style="margin-top:0.2rem">${[
      p.total ? t('common.bytesOf', { done: fmtBytes(p.downloaded), total: fmtBytes(p.total) }) : fmtBytes(p.downloaded),
      p.speed ? t('manage.perSecond', { size: fmtBytes(p.speed) }) : null,
      p.eta ? t('manage.minutesLeft', { count: Math.ceil(p.eta / 60) }) : null,
    ].filter(Boolean).join(' · ')}</div></div>`;
}

function Queue() {
  const [q, setQ] = useState(null);
  const load = () => get('/queue').then(setQ).catch(toastError);
  usePolling(load, 2500);
  const act = (fn, msg) => async (v) => { try { await fn(v); if (msg) toast(msg); load(); } catch (e) { toastError(e); } };
  const retry = act((v) => post(`/queue/${v.id}/retry`), t('manage.retried'));
  const cancel = act((v) => del(`/queue/${v.id}`), t('manage.cancelled'));
  if (!q) return html`<p class="muted">${t('common.loading')}</p>`;
  const section = (title, items, render, empty) => html`<section>
    <div class="section-head"><h2>${title}</h2><span class="small muted">${items.length || ''}</span></div>
    ${items.length ? html`<div class="vlist">${items.map(render)}</div>` : html`<p class="small muted">${empty}</p>`}</section>`;
  const errorLine = (v) => v.last_error && html`<details class="tiny" style="margin-top:0.3rem" onClick=${(e) => e.stopPropagation()}>
    <summary>${v.last_error_class || t('manage.error')}: ${v.last_error.split('\n')[0].slice(0, 120)}</summary><pre class="stderr">${v.last_error}</pre></details>`;
  return html`<div>
    ${section(t('manage.downloading'), q.active, (v) => html`<${VideoCard} key=${v.id} v=${v} extra=${html`<${ProgressLine} p=${v.progress} />
      <button class="small" style="margin-top:0.35rem" onClick=${(e) => { e.preventDefault(); cancel(v); }}>${t('manage.abandon')}</button>`} />`, t('manage.nothingDownloading'))}
    ${section(t('manage.waiting'), q.queued, (v) => html`<${VideoCard} key=${v.id} v=${v}
      sub=${v.next_attempt_at ? [t('manage.retryAt', { time: fmtTime(v.next_attempt_at) }), v.attempts ? t('manage.attempt', { count: v.attempts + 1 }) : null].filter(Boolean).join(' ') : v.origin === 'manual' ? t('manage.byHand') : v.channel?.title}
      extra=${html`${errorLine(v)}<button class="small" style="margin-top:0.35rem" onClick=${(e) => { e.preventDefault(); cancel(v); }}>${t('manage.abandon')}</button>`} />`, t('manage.queueEmpty'))}
    ${q.deferred.length > 0 && section(t('manage.waitingSpace'), q.deferred, (v) => html`<${VideoCard} key=${v.id} v=${v}
      sub=${v.last_error} extra=${html`<button class="small" style="margin-top:0.35rem" onClick=${(e) => { e.preventDefault(); retry(v); }}>${t('manage.tryAgain')}</button>`} />`)}
    ${section(t('manage.failed'), q.failed, (v) => html`<${VideoCard} key=${v.id} v=${v} extra=${html`${errorLine(v)}
      <div class="row" style="margin-top:0.35rem"><button class="small" onClick=${(e) => { e.preventDefault(); retry(v); }}>${t('manage.retry')}</button>
      <button class="small" onClick=${(e) => { e.preventDefault(); cancel(v); }}>${t('manage.dismiss')}</button></div>`} />`, t('manage.noFailures'))}
    ${q.unavailable.length > 0 && section(t('manage.unavailable'), q.unavailable, (v) => html`<${VideoCard} key=${v.id} v=${v}
      sub=${reasonText((v.last_error || '').split(':')[0])} extra=${html`<button class="small" style="margin-top:0.35rem" onClick=${(e) => { e.preventDefault(); retry(v); }}>${t('manage.tryAgain')}</button>`} />`)}
  </div>`;
}

// ---- storage -----------------------------------------------------------------------------

function Storage() {
  const [s, setS] = useState(null);
  usePolling(() => get('/storage').then(setS).catch(toastError), 15000);
  if (!s) return html`<p class="muted">${t('common.loading')}</p>`;
  const u = s.usage;
  const pct = (b) => Math.min(100, (b / u.max_bytes) * 100) + '%';
  const unpinned = u.cached_bytes - u.pinned_bytes;
  return html`<div class="stack">
    <div class="card stack">
      <div class="row between"><h2>${t('manage.usage', { used: fmtBytes(u.cached_bytes), max: fmtBytes(u.max_bytes) })}</h2>
        <span class="small muted">${t('common.videos', { count: u.cached_count })}</span></div>
      <div class="bar" style="height:12px"><span style=${{ width: pct(unpinned) }}></span>
        <span class="pinned" style=${{ width: pct(u.pinned_bytes) }}></span><span class="reserved" style=${{ width: pct(u.reserved_bytes) }}></span></div>
      <div class="row wrap small muted">
        <span><span class="dot" style="background:var(--accent);display:inline-block"></span> ${t('manage.legendCached')}</span>
        <span><span class="dot" style="background:var(--info);display:inline-block"></span> ${t('manage.legendPinned', { size: fmtBytes(u.pinned_bytes), limit: fmtBytes(u.pinned_limit) })}</span>
        <span><span class="dot" style="background:var(--warn);display:inline-block"></span> ${t('manage.legendReserved', { size: fmtBytes(u.reserved_bytes) })}</span>
      </div>
      ${u.fs_free !== null && html`<p class="small muted">${t('manage.disk', { free: fmtBytes(u.fs_free), total: fmtBytes(u.fs_total), min: fmtBytes(u.min_free_bytes) })}</p>`}
    </div>
    <section><div class="section-head"><h2>${t('manage.byChannel')}</h2></div>
      <div class="card flush"><table class="plain"><thead><tr><th>${t('manage.colChannel')}</th><th class="num">${t('manage.colVideos')}</th><th class="num">${t('manage.colSize')}</th></tr></thead>
        <tbody>${s.per_channel.map((c) => html`<tr><td><a href=${href('/channel/' + c.channel_id)}>${c.title || c.channel_id || '—'}</a></td>
          <td class="num">${c.count}</td><td class="num">${fmtBytes(c.bytes)}</td></tr>`)}</tbody></table></div></section>
    <section><div class="section-head"><h2>${t('manage.nextRemoved')}</h2><span class="small muted">${t('manage.whenNeeded')}</span></div>
      <p class="small muted">${t('manage.evictionRule', { days: s.grace_days })}</p>
      ${s.eviction_preview.length ? html`<ol class="list">${s.eviction_preview.map((v, i) => html`<li class="row">
        <span class="tiny faint" style="width:1.6em">${i + 1}</span>
        <div class="grow"><${VideoCard} v=${v} sub=${t('manage.evictionLine', { channel: v.channel?.title || '', size: fmtBytes(v.size_bytes), count: v.total_watch_count })}
          extra=${v.protected ? html`<span class="badge info">${t('manage.protected')}</span>` : null} /></div></li>`)}</ol>`
        : html`<p class="small muted">${t('manage.nothingRemovable')}</p>`}
    </section>
  </div>`;
}

export function Manage({ tab }) {
  const counts = useStore((s) => s.status?.queue || {});
  const cur = tab || 'subscriptions';
  const qn = (counts.queued || 0) + (counts.downloading || 0);
  return html`<div>
    <div class="seg" style="margin-bottom:1rem">
      <button class=${cur === 'subscriptions' ? 'on' : ''} onClick=${() => (location.hash = '#/manage')}>${t('manage.subscriptions')}</button>
      <button class=${cur === 'queue' ? 'on' : ''} onClick=${() => (location.hash = '#/manage/queue')}>${qn ? t('manage.queueCount', { count: qn }) : t('manage.queue')}</button>
      <button class=${cur === 'storage' ? 'on' : ''} onClick=${() => (location.hash = '#/manage/storage')}>${t('manage.storage')}</button>
    </div>
    ${cur === 'subscriptions' && html`<${Subscriptions} />`}
    ${cur === 'queue' && html`<${Queue} />`}
    ${cur === 'storage' && html`<${Storage} />`}
  </div>`;
}
