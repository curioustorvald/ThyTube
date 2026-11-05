import { html } from '../../vendor/preact-htm.js';
import { useStore } from '../store.js';
import { href } from '../router.js';

const LABEL = { ok: 'Healthy', degraded: 'Degraded', blocked: 'Blocked', updating: 'Updating' };

export function healthState(status) {
  if (!status) return { state: 'unknown', label: '…' };
  if (!status.worker?.alive) return { state: 'blocked', label: 'Worker offline' };
  const st = status.health.effective_state || status.health.state;
  const active = status.worker.active?.length || 0;
  let label = LABEL[st] || st;
  if (st === 'ok' && active) label = `Downloading${active > 1 ? ' ×' + active : ''}`;
  return { state: st, label };
}

export function StatusPill() {
  const { status, statusError } = useStore((s) => ({ status: s.status, statusError: s.statusError }));
  const h = statusError ? { state: 'blocked', label: 'Server unreachable' } : healthState(status);
  return html`<a class="pill" href=${href('/status')} title="Status">
    <span class="dot ${h.state}"></span><span>${h.label}</span></a>`;
}
