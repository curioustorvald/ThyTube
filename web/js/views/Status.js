import { html, useEffect, useState } from '../../vendor/preact-htm.js';
import { get, post } from '../api.js';
import { Confirm } from '../components/Dialog.js';
import { healthLabel, healthState } from '../components/StatusPill.js';
import { t } from '../i18n.js';
import { Icon } from '../icons.js';
import { isIOS, isStandalone, promptInstall } from '../pwa.js';
import { toast, toastError, useStore } from '../store.js';
import { ago, fmtBytes, fmtTime } from '../util.js';

const STATES = ['ok', 'degraded', 'blocked', 'updating'];
const title = (st) => t('status.title.' + (STATES.includes(st) ? st : 'unknown'));

// The plain-language explanation (the API's `health.explanation` is the same in plain English).
function explain(h) {
  const st = h.effective_state || h.state;
  if (st === 'degraded') return t('status.explain.degraded', { reason: h.last_failure?.reason || t('status.explain.unknownReason') });
  if (st === 'blocked') {
    const next = h.next_probe_at ? ' ' + t('status.explain.nextCheck', { time: fmtTime(h.next_probe_at) }) : '';
    return t('status.explain.blocked') + next;
  }
  return STATES.includes(st) ? t('status.explain.' + st) : h.explanation;
}

function Events() {
  const [level, setLevel] = useState('info');
  const [items, setItems] = useState([]);
  const [next, setNext] = useState(null);
  const load = (more) => get('/events', { level, limit: 50, before: more ? next : undefined })
    .then((r) => { setItems((old) => (more ? [...old, ...r.items] : r.items)); setNext(r.next_before); }).catch(toastError);
  useEffect(() => { load(false); const iv = setInterval(() => load(false), 20000); return () => clearInterval(iv); }, [level]);
  return html`<section>
    <div class="section-head"><h2>${t('status.events')}</h2>
      <select style="width:auto;min-height:36px" value=${level} onChange=${(e) => setLevel(e.target.value)}>
        <option value="info">${t('status.levelAll')}</option><option value="warn">${t('status.levelWarn')}</option><option value="error">${t('status.levelError')}</option></select></div>
    <div class="card"><ul class="list">${items.map((e) => html`<li class="event">
      <time title=${e.ts}>${fmtTime(e.ts)}</time>
      <div class="msg"><span class="badge ${e.level === 'error' ? 'bad' : e.level === 'warn' ? 'warn' : ''}">${e.kind}</span> ${e.message}
        ${e.data?.stderr && html`<details class="tiny"><summary>stderr</summary><pre class="stderr">${e.data.stderr}</pre></details>`}</div>
    </li>`)}</ul>
    ${!items.length && html`<p class="small muted">${t('status.noEvents')}</p>`}
    ${next && html`<div class="load-more"><button class="small" onClick=${() => load(true)}>${t('status.older')}</button></div>`}</div>
  </section>`;
}

function AppCard() {
  const canInstall = useStore((s) => s.canInstall);
  let body;
  if (isStandalone()) body = html`<p class="small">${t('status.installed')}</p>`;
  else if (canInstall) body = html`<p class="small">${t('status.canInstall')}</p>
    <div><button class="small primary" onClick=${promptInstall}><${Icon} name="download" size="16" /> ${t('status.install')}</button></div>`;
  else if (isIOS()) body = html`<p class="small">${t('status.iosInstall', { add: html`<strong>${t('status.iosAdd')}</strong>` })}</p>`;
  else if (!window.isSecureContext) body = html`<p class="small">${t('status.needsHttps')}</p>`;
  else body = html`<p class="small">${t('status.browserInstall')}</p>`;
  return html`<div class="card stack"><h2 class="row"><${Icon} name="phone" size="20" /> ${t('status.app')}</h2>${body}</div>`;
}

export function Status() {
  const status = useStore((s) => s.status);
  const statusError = useStore((s) => s.statusError);
  const [confirmRollback, setConfirmRollback] = useState(false);
  if (!status) {
    return statusError
      ? html`<div class="stack"><div class="error-box">${statusError}</div><${AppCard} /></div>`
      : html`<p class="muted">${t('common.loading')}</p>`;
  }
  const h = status.health;
  const hs = healthState(status);
  const y = status.ytdlp;
  const admin = (path, msg) => post(path).then(() => toast(msg)).catch(toastError);
  const lu = y.last_update;
  return html`<div class="stack">
    <div class="card health-card ${hs.state} stack">
      <div class="row"><span class="dot ${hs.state}"></span><h1>${status.worker.alive ? title(h.effective_state) : t('status.workerDown')}</h1></div>
      <p>${status.worker.alive ? explain(h) : t('status.workerDownHint')}</p>
      <dl class="kv">
        <dt>${t('status.state')}</dt><dd>${h.since ? t('status.since', { state: healthLabel(h.effective_state), time: fmtTime(h.since), ago: ago(h.since) }) : healthLabel(h.effective_state)}</dd>
        <dt>${t('status.lastSuccess')}</dt><dd>${h.last_success_at ? `${fmtTime(h.last_success_at)} (${ago(h.last_success_at)})` : '—'}</dd>
        ${h.last_failure && html`<dt>${t('status.lastFailure')}</dt><dd>${fmtTime(h.last_failure.at)} · ${h.last_failure.source}: <strong>${h.last_failure.reason}</strong>
          <div class="tiny muted">${h.last_failure.message}</div></dd>`}
        ${h.consecutive_systemic > 0 && html`<dt>${t('status.systemicInRow')}</dt><dd>${h.consecutive_systemic}</dd>`}
        ${h.next_probe_at && html`<dt>${t('status.nextCheck')}</dt><dd>${fmtTime(h.next_probe_at)} (${ago(h.next_probe_at)})</dd>`}
        ${h.last_probe && html`<dt>${t('status.lastCheck')}</dt><dd>${fmtTime(h.last_probe.at)} · ${h.last_probe.ok ? t('status.passed') : t('status.failedAt', { stage: h.last_probe.stage })}</dd>`}
        <dt>${t('status.worker')}</dt><dd>${status.worker.alive ? t('status.running') : t('status.offline')}${status.worker.task ? ` · ${status.worker.task}` : ''}
          ${status.worker.heartbeat_at ? html` <span class="tiny faint">${t('status.seen', { ago: ago(status.worker.heartbeat_at) })}</span>` : ''}</dd>
      </dl>
      <div class="row wrap">
        <button class="small" onClick=${() => admin('/admin/probe', t('status.checking'))}><${Icon} name="refresh" size="16" /> ${t('status.checkNow')}</button>
        <button class="small" onClick=${() => admin('/admin/update-ytdlp', t('status.updating'))}><${Icon} name="download" size="16" /> ${t('status.update')}</button>
        <button class="small" onClick=${() => setConfirmRollback(true)}>${t('status.rollback')}</button>
      </div>
    </div>

    <div class="grid2">
      <div class="card stack"><h2>yt-dlp</h2>
        <dl class="kv"><dt>${t('status.version')}</dt><dd class="mono">${y.version || t('status.notInstalled')}</dd>
          <dt>${t('status.channel')}</dt><dd>${y.channel}</dd>
          ${lu && html`<dt>${t('status.lastUpdate')}</dt><dd>${fmtTime(lu.at)} · ${lu.message}</dd>`}
          <dt>${t('status.cookies')}</dt><dd>${status.cookies_enabled ? t('status.cookiesOn') : t('status.cookiesOff')}</dd></dl>
        ${y.history?.length > 1 && html`<details><summary class="small">${t('status.history')}</summary><ul class="list small">
          ${y.history.map((u) => html`<li><span class="faint">${fmtTime(u.at)}</span> ${u.message}</li>`)}</ul></details>`}
      </div>
      <div class="card stack"><h2>${t('status.library')}</h2>
        <dl class="kv">
          <dt>${t('status.cached')}</dt><dd>${t('status.cachedLine', { videos: t('common.videos', { count: status.queue.cached || 0 }), used: fmtBytes(status.pool.cached_bytes), max: fmtBytes(status.pool.max_bytes) })}</dd>
          <dt>${t('status.queued')}</dt><dd>${(status.queue.queued || 0) + (status.queue.downloading || 0)}${status.queue.deferred_space ? ' · ' + t('status.waitingSpace', { count: status.queue.deferred_space }) : ''}</dd>
          <dt>${t('status.failed')}</dt><dd>${status.queue.failed || 0}${status.queue.unavailable ? ' · ' + t('status.unavailable', { count: status.queue.unavailable }) : ''}</dd>
          <dt>${t('status.removed')}</dt><dd>${status.queue.evicted || 0}</dd>
        </dl>
        <a class="small" href="#/manage/queue">${t('status.openQueue')}</a>
      </div>
    </div>
    <${AppCard} />
    <${Events} />
    ${confirmRollback && html`<${Confirm} title=${t('status.rollbackTitle')} confirmLabel=${t('status.rollbackConfirm')} onClose=${() => setConfirmRollback(false)}
      onConfirm=${() => admin('/admin/rollback-ytdlp', t('status.rollingBack'))}
      message=${t('status.rollbackMessage')} />`}
  </div>`;
}
