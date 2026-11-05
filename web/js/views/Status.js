import { html, useEffect, useState } from '../../vendor/preact-htm.js';
import { get, post } from '../api.js';
import { Confirm } from '../components/Dialog.js';
import { healthState } from '../components/StatusPill.js';
import { Icon } from '../icons.js';
import { isIOS, isStandalone, promptInstall } from '../pwa.js';
import { toast, toastError, useStore } from '../store.js';
import { ago, fmtBytes, fmtTime } from '../util.js';

const TITLES = { ok: 'Everything is working', degraded: 'YouTube is being difficult', blocked: 'Downloads are paused',
  updating: 'Updating yt-dlp', unknown: 'Checking…' };

function Events() {
  const [level, setLevel] = useState('info');
  const [items, setItems] = useState([]);
  const [next, setNext] = useState(null);
  const load = (more) => get('/events', { level, limit: 50, before: more ? next : undefined })
    .then((r) => { setItems((old) => (more ? [...old, ...r.items] : r.items)); setNext(r.next_before); }).catch(toastError);
  useEffect(() => { load(false); const iv = setInterval(() => load(false), 20000); return () => clearInterval(iv); }, [level]);
  return html`<section>
    <div class="section-head"><h2>Event log</h2>
      <select style="width:auto;min-height:36px" value=${level} onChange=${(e) => setLevel(e.target.value)}>
        <option value="info">Everything</option><option value="warn">Warnings and errors</option><option value="error">Errors</option></select></div>
    <div class="card"><ul class="list">${items.map((e) => html`<li class="event">
      <time title=${e.ts}>${fmtTime(e.ts)}</time>
      <div class="msg"><span class="badge ${e.level === 'error' ? 'bad' : e.level === 'warn' ? 'warn' : ''}">${e.kind}</span> ${e.message}
        ${e.data?.stderr && html`<details class="tiny"><summary>stderr</summary><pre class="stderr">${e.data.stderr}</pre></details>`}</div>
    </li>`)}</ul>
    ${!items.length && html`<p class="small muted">No events.</p>`}
    ${next && html`<div class="load-more"><button class="small" onClick=${() => load(true)}>Older</button></div>`}</div>
  </section>`;
}

function AppCard() {
  const canInstall = useStore((s) => s.canInstall);
  let body;
  if (isStandalone()) body = html`<p class="small">Thou art using ThyTube as an installed app.</p>`;
  else if (canInstall) body = html`<p class="small">Install ThyTube on this device: it opens in its own window and its icon sits beside thine other apps.</p>
    <div><button class="small primary" onClick=${promptInstall}><${Icon} name="download" size="16" /> Install ThyTube</button></div>`;
  else if (isIOS()) body = html`<p class="small">To install on this device, tap the Share button in Safari, then <strong>Add to Home Screen</strong>.</p>`;
  else if (!window.isSecureContext) body = html`<p class="small">Thy browser only offers to install ThyTube (and keeps it usable offline) when the server is reached over HTTPS. The README explains how to set that up on thy LAN; until then, thy browser's “Add to Home screen” still makes a shortcut.</p>`;
  else body = html`<p class="small">Use thy browser's menu to install ThyTube (“Install app” or “Add to Home screen”).</p>`;
  return html`<div class="card stack"><h2 class="row"><${Icon} name="phone" size="20" /> App</h2>${body}</div>`;
}

export function Status() {
  const status = useStore((s) => s.status);
  const statusError = useStore((s) => s.statusError);
  const [confirmRollback, setConfirmRollback] = useState(false);
  if (!status) {
    return statusError
      ? html`<div class="stack"><div class="error-box">${statusError}</div><${AppCard} /></div>`
      : html`<p class="muted">Loading…</p>`;
  }
  const h = status.health;
  const hs = healthState(status);
  const y = status.ytdlp;
  const admin = (path, msg) => post(path).then(() => toast(msg)).catch(toastError);
  const lu = y.last_update;
  return html`<div class="stack">
    <div class="card health-card ${hs.state} stack">
      <div class="row"><span class="dot ${hs.state}"></span><h1>${status.worker.alive ? TITLES[h.effective_state] : 'The worker is not running'}</h1></div>
      <p>${status.worker.alive ? h.explanation : 'Downloads and channel checks are stopped. Start it with `thytube worker` or check its service.'}</p>
      <dl class="kv">
        <dt>State</dt><dd>${h.effective_state}${h.since ? ` since ${fmtTime(h.since)} (${ago(h.since)})` : ''}</dd>
        <dt>Last success</dt><dd>${h.last_success_at ? `${fmtTime(h.last_success_at)} (${ago(h.last_success_at)})` : '—'}</dd>
        ${h.last_failure && html`<dt>Last failure</dt><dd>${fmtTime(h.last_failure.at)} · ${h.last_failure.source}: <strong>${h.last_failure.reason}</strong>
          <div class="tiny muted">${h.last_failure.message}</div></dd>`}
        ${h.consecutive_systemic > 0 && html`<dt>Systemic failures in a row</dt><dd>${h.consecutive_systemic}</dd>`}
        ${h.next_probe_at && html`<dt>Next check</dt><dd>${fmtTime(h.next_probe_at)} (${ago(h.next_probe_at)})</dd>`}
        ${h.last_probe && html`<dt>Last check</dt><dd>${fmtTime(h.last_probe.at)} · ${h.last_probe.ok ? 'passed' : `failed at ${h.last_probe.stage}`}</dd>`}
        <dt>Worker</dt><dd>${status.worker.alive ? 'running' : 'offline'}${status.worker.task ? ` · ${status.worker.task}` : ''}
          ${status.worker.heartbeat_at ? html` <span class="tiny faint">(seen ${ago(status.worker.heartbeat_at)})</span>` : ''}</dd>
      </dl>
      <div class="row wrap">
        <button class="small" onClick=${() => admin('/admin/probe', 'Checking YouTube now…')}><${Icon} name="refresh" size="16" /> Check now</button>
        <button class="small" onClick=${() => admin('/admin/update-ytdlp', 'Updating yt-dlp…')}><${Icon} name="download" size="16" /> Update yt-dlp now</button>
        <button class="small" onClick=${() => setConfirmRollback(true)}>Roll back to previous version</button>
      </div>
    </div>

    <div class="grid2">
      <div class="card stack"><h2>yt-dlp</h2>
        <dl class="kv"><dt>Version</dt><dd class="mono">${y.version || 'not installed'}</dd>
          <dt>Channel</dt><dd>${y.channel}</dd>
          ${lu && html`<dt>Last update</dt><dd>${fmtTime(lu.at)} · ${lu.message}</dd>`}
          <dt>Cookies</dt><dd>${status.cookies_enabled ? 'in use (account risk)' : 'not used'}</dd></dl>
        ${y.history?.length > 1 && html`<details><summary class="small">Update history</summary><ul class="list small">
          ${y.history.map((u) => html`<li><span class="faint">${fmtTime(u.at)}</span> ${u.message}</li>`)}</ul></details>`}
      </div>
      <div class="card stack"><h2>Library</h2>
        <dl class="kv">
          <dt>Cached</dt><dd>${status.queue.cached || 0} videos · ${fmtBytes(status.pool.cached_bytes)} of ${fmtBytes(status.pool.max_bytes)}</dd>
          <dt>Queued</dt><dd>${(status.queue.queued || 0) + (status.queue.downloading || 0)}${status.queue.deferred_space ? ` · ${status.queue.deferred_space} waiting for space` : ''}</dd>
          <dt>Failed</dt><dd>${status.queue.failed || 0}${status.queue.unavailable ? ` · ${status.queue.unavailable} unavailable` : ''}</dd>
          <dt>Removed</dt><dd>${status.queue.evicted || 0}</dd>
        </dl>
        <a class="small" href="#/manage/queue">Open the queue</a>
      </div>
    </div>
    <${AppCard} />
    <${Events} />
    ${confirmRollback && html`<${Confirm} title="Roll back yt-dlp?" confirmLabel="Roll back" onClose=${() => setConfirmRollback(false)}
      onConfirm=${() => admin('/admin/rollback-ytdlp', 'Rolling back…')}
      message="Switches to the previously installed yt-dlp version. The next scheduled update may move forward again." />`}
  </div>`;
}
