import { html, useEffect, useState } from '../../vendor/preact-htm.js';
import { del, get, patch, post } from '../api.js';
import { Confirm } from '../components/Dialog.js';
import { FilterDialog } from '../components/FilterDialog.js';
import { VideoCard } from '../components/VideoCard.js';
import { Icon } from '../icons.js';
import { href } from '../router.js';
import { toast, toastError, useStore } from '../store.js';
import { ago, fmtBytes, fmtTime } from '../util.js';
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
      toast(r.deleting ? `Unsubscribed; removing ${r.deleting} cached video(s)` : 'Unsubscribed');
      load();
    } catch (e) { toastError(e); }
  }
  if (items === null) return html`<p class="muted">Loading…</p>`;
  if (!items.length) return html`<div class="empty"><h2>No subscriptions</h2>
    <p>Find a creator in <a href="#/search?type=channel">Search</a> and choose “Cache this creator…”.</p></div>`;
  return html`<ul class="list">
    ${items.map((s) => html`<li>
      <div class="row" style="align-items:flex-start">
        ${s.channel?.avatar ? html`<img class="ch-avatar" src=${s.channel.avatar} alt="" />` : html`<span class="ch-avatar"></span>`}
        <div class="grow">
          <div class="row wrap"><a href=${href('/channel/' + s.channel_id)}><strong>${s.channel?.title || s.channel_id}</strong></a>
            ${!s.enabled && html`<span class="badge">Paused</span>`}
            ${s.last_poll_status && s.last_poll_status !== 'ok' && html`<span class="badge bad" title=${s.last_poll_error}>Last check failed</span>`}</div>
          <div class="small muted">${rulesSummary(s)}</div>
          <div class="tiny faint">${s.cached_count} cached (${fmtBytes(s.cached_bytes)})${s.queued_count ? ` · ${s.queued_count} queued` : ''}${' · '}checked ${s.last_polled_at ? ago(s.last_polled_at) : 'not yet'}${s.next_poll_at && s.enabled ? ` · next ${fmtTime(s.next_poll_at)}` : ''}</div>
          ${s.last_poll_error && html`<div class="tiny" style="color:var(--bad)">${s.last_poll_error}</div>`}
          <div class="row wrap" style="margin-top:0.4rem">
            <button class="small" onClick=${() => setEdit(s)}><${Icon} name="edit" size="16" /> Rules</button>
            <button class="small" onClick=${() => post(`/subscriptions/${s.id}/poll`).then(() => toast('Checking for new uploads…')).catch(toastError)}><${Icon} name="refresh" size="16" /> Check now</button>
            <button class="small" onClick=${() => toggle(s)}>${s.enabled ? 'Pause' : 'Resume'}</button>
            <button class="small danger" onClick=${() => { setAlsoFiles(false); setRemove(s); }}><${Icon} name="trash" size="16" /> Unsubscribe</button>
          </div>
        </div>
      </div></li>`)}
    ${edit && html`<${FilterDialog} channel=${{ id: edit.channel_id, ...(edit.channel || {}) }} subscription=${edit}
      onClose=${() => setEdit(null)} onSaved=${load} />`}
    ${remove && html`<${Confirm} title="Unsubscribe?" danger confirmLabel="Unsubscribe" onConfirm=${doRemove} onClose=${() => setRemove(null)}
      message=${`New uploads from ${remove.channel?.title || 'this channel'} won't be cached any more.`}>
      <label class="check"><input type="checkbox" checked=${alsoFiles} onChange=${(e) => setAlsoFiles(e.target.checked)} />
        Also delete its ${remove.cached_count} cached video(s) (pinned ones are kept)</label>
      <p class="small muted">Otherwise they stay and are removed normally when space is needed.</p>
    <//>`}
  </ul>`;
}

// ---- queue -------------------------------------------------------------------------------

function ProgressLine({ p }) {
  if (!p) return html`<span class="small muted">Starting…</span>`;
  if (p.stage === 'metadata') return html`<span class="small muted">Fetching details…</span>`;
  if (p.stage === 'subtitles') return html`<span class="small muted">Fetching subtitles…</span>`;
  const frac = p.total ? p.downloaded / p.total : 0;
  return html`<div><div class="bar"><span style=${{ width: frac * 100 + '%' }}></span></div>
    <div class="tiny muted" style="margin-top:0.2rem">${fmtBytes(p.downloaded)}${p.total ? ' of ' + fmtBytes(p.total) : ''}
      ${p.speed ? ` · ${fmtBytes(p.speed)}/s` : ''}${p.eta ? ` · ${Math.ceil(p.eta / 60)} min left` : ''}</div></div>`;
}

function Queue() {
  const [q, setQ] = useState(null);
  const load = () => get('/queue').then(setQ).catch(toastError);
  usePolling(load, 2500);
  const act = (fn, msg) => async (v) => { try { await fn(v); if (msg) toast(msg); load(); } catch (e) { toastError(e); } };
  const retry = act((v) => post(`/queue/${v.id}/retry`), 'Queued again');
  const cancel = act((v) => del(`/queue/${v.id}`), 'Cancelled');
  if (!q) return html`<p class="muted">Loading…</p>`;
  const section = (title, items, render, empty) => html`<section>
    <div class="section-head"><h2>${title}</h2><span class="small muted">${items.length || ''}</span></div>
    ${items.length ? html`<div class="vlist">${items.map(render)}</div>` : html`<p class="small muted">${empty}</p>`}</section>`;
  const errorLine = (v) => v.last_error && html`<details class="tiny" style="margin-top:0.3rem" onClick=${(e) => e.stopPropagation()}>
    <summary>${v.last_error_class || 'error'}: ${v.last_error.split('\n')[0].slice(0, 120)}</summary><pre class="stderr">${v.last_error}</pre></details>`;
  return html`<div>
    ${section('Downloading', q.active, (v) => html`<${VideoCard} key=${v.id} v=${v} extra=${html`<${ProgressLine} p=${v.progress} />
      <button class="small" style="margin-top:0.35rem" onClick=${(e) => { e.preventDefault(); cancel(v); }}>Cancel</button>`} />`, 'Nothing is downloading.')}
    ${section('Waiting', q.queued, (v) => html`<${VideoCard} key=${v.id} v=${v}
      sub=${v.next_attempt_at ? `retry ${fmtTime(v.next_attempt_at)}${v.attempts ? ` (attempt ${v.attempts + 1})` : ''}` : v.origin === 'manual' ? 'requested by hand' : v.channel?.title}
      extra=${html`${errorLine(v)}<button class="small" style="margin-top:0.35rem" onClick=${(e) => { e.preventDefault(); cancel(v); }}>Cancel</button>`} />`, 'The queue is empty.')}
    ${q.deferred.length > 0 && section('Waiting for space', q.deferred, (v) => html`<${VideoCard} key=${v.id} v=${v}
      sub=${v.last_error} extra=${html`<button class="small" style="margin-top:0.35rem" onClick=${(e) => { e.preventDefault(); retry(v); }}>Try again</button>`} />`)}
    ${section('Failed', q.failed, (v) => html`<${VideoCard} key=${v.id} v=${v} extra=${html`${errorLine(v)}
      <div class="row" style="margin-top:0.35rem"><button class="small" onClick=${(e) => { e.preventDefault(); retry(v); }}>Retry</button>
      <button class="small" onClick=${(e) => { e.preventDefault(); cancel(v); }}>Dismiss</button></div>`} />`, 'No failures.')}
    ${q.unavailable.length > 0 && section('Unavailable on YouTube', q.unavailable, (v) => html`<${VideoCard} key=${v.id} v=${v}
      sub=${(v.last_error || '').split(':')[0]} extra=${html`<button class="small" style="margin-top:0.35rem" onClick=${(e) => { e.preventDefault(); retry(v); }}>Try again</button>`} />`)}
  </div>`;
}

// ---- storage -----------------------------------------------------------------------------

function Storage() {
  const [s, setS] = useState(null);
  usePolling(() => get('/storage').then(setS).catch(toastError), 15000);
  if (!s) return html`<p class="muted">Loading…</p>`;
  const u = s.usage;
  const pct = (b) => Math.min(100, (b / u.max_bytes) * 100) + '%';
  const unpinned = u.cached_bytes - u.pinned_bytes;
  return html`<div class="stack">
    <div class="card stack">
      <div class="row between"><h2>${fmtBytes(u.cached_bytes)} of ${fmtBytes(u.max_bytes)}</h2>
        <span class="small muted">${u.cached_count} videos</span></div>
      <div class="bar" style="height:12px"><span style=${{ width: pct(unpinned) }}></span>
        <span class="pinned" style=${{ width: pct(u.pinned_bytes) }}></span><span class="reserved" style=${{ width: pct(u.reserved_bytes) }}></span></div>
      <div class="row wrap small muted">
        <span><span class="dot" style="background:var(--accent);display:inline-block"></span> cached</span>
        <span><span class="dot" style="background:var(--info);display:inline-block"></span> pinned ${fmtBytes(u.pinned_bytes)} (limit ${fmtBytes(u.pinned_limit)})</span>
        <span><span class="dot" style="background:var(--warn);display:inline-block"></span> reserved for downloads ${fmtBytes(u.reserved_bytes)}</span>
      </div>
      ${u.fs_free !== null && html`<p class="small muted">Disk: ${fmtBytes(u.fs_free)} free of ${fmtBytes(u.fs_total)}; ThyTube always leaves ${fmtBytes(u.min_free_bytes)} free.</p>`}
    </div>
    <section><div class="section-head"><h2>By channel</h2></div>
      <div class="card flush"><table class="plain"><thead><tr><th>Channel</th><th class="num">Videos</th><th class="num">Size</th></tr></thead>
        <tbody>${s.per_channel.map((c) => html`<tr><td><a href=${href('/channel/' + c.channel_id)}>${c.title || c.channel_id || '—'}</a></td>
          <td class="num">${c.count}</td><td class="num">${fmtBytes(c.bytes)}</td></tr>`)}</tbody></table></div></section>
    <section><div class="section-head"><h2>Next to be removed</h2><span class="small muted">when space is needed</span></div>
      <p class="small muted">Least re-watched first, then longest unwatched, then largest. Unwatched downloads from the last ${s.grace_days} days are protected unless nothing else is left; pinned videos and anything playing right now are never removed.</p>
      ${s.eviction_preview.length ? html`<ol class="list">${s.eviction_preview.map((v, i) => html`<li class="row">
        <span class="tiny faint" style="width:1.6em">${i + 1}</span>
        <div class="grow"><${VideoCard} v=${v} sub=${`${v.channel?.title || ''} · ${fmtBytes(v.size_bytes)} · watched ${v.total_watch_count}×`}
          extra=${v.protected ? html`<span class="badge info">Protected (new)</span>` : null} /></div></li>`)}</ol>`
        : html`<p class="small muted">Nothing can be removed.</p>`}
    </section>
  </div>`;
}

export function Manage({ tab }) {
  const counts = useStore((s) => s.status?.queue || {});
  const t = tab || 'subscriptions';
  const qn = (counts.queued || 0) + (counts.downloading || 0);
  return html`<div>
    <div class="seg" style="margin-bottom:1rem">
      <button class=${t === 'subscriptions' ? 'on' : ''} onClick=${() => (location.hash = '#/manage')}>Subscriptions</button>
      <button class=${t === 'queue' ? 'on' : ''} onClick=${() => (location.hash = '#/manage/queue')}>Queue${qn ? ` (${qn})` : ''}</button>
      <button class=${t === 'storage' ? 'on' : ''} onClick=${() => (location.hash = '#/manage/storage')}>Storage</button>
    </div>
    ${t === 'subscriptions' && html`<${Subscriptions} />`}
    ${t === 'queue' && html`<${Queue} />`}
    ${t === 'storage' && html`<${Storage} />`}
  </div>`;
}
