import { html } from '../../vendor/preact-htm.js';
import { t } from '../i18n.js';
import { useStore } from '../store.js';
import { href } from '../router.js';

const STATES = ['ok', 'degraded', 'blocked', 'updating'];
export const healthLabel = (st) => (STATES.includes(st) ? t('health.' + st) : st);

export function healthState(status) {
  if (!status) return { state: 'unknown', label: '…' };
  if (!status.worker?.alive) return { state: 'blocked', label: t('health.workerOffline') };
  const st = status.health.effective_state || status.health.state;
  const active = status.worker.active?.length || 0;
  let label = healthLabel(st);
  if (st === 'ok' && active) label = active > 1 ? t('health.downloadingMany', { count: active }) : t('health.downloading');
  return { state: st, label };
}

export function StatusPill() {
  const { status, statusError } = useStore((s) => ({ status: s.status, statusError: s.statusError }));
  const h = statusError ? { state: 'blocked', label: t('health.unreachable') } : healthState(status);
  return html`<a class="pill" href=${href('/status')} title=${t('health.pillTitle')}>
    <span class="dot ${h.state}"></span><span>${h.label}</span></a>`;
}
