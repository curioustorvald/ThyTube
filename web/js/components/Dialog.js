import { html, useEffect } from '../../vendor/preact-htm.js';
import { t } from '../i18n.js';
import { Icon } from '../icons.js';

export function Dialog({ title, onClose, children, footer, narrow }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.removeEventListener('keydown', onKey); document.body.style.overflow = prev; };
  }, []);
  return html`
    <div class="backdrop" onClick=${(e) => e.target === e.currentTarget && onClose()}>
      <div class="dialog ${narrow ? 'narrow' : ''}" role="dialog" aria-modal="true" aria-label=${title}>
        <header><h2 class="grow">${title}</h2>
          <button class="ghost icon" onClick=${onClose} aria-label=${t('dialog.close')}><${Icon} name="close" /></button></header>
        <div class="body">${children}</div>
        ${footer && html`<footer>${footer}</footer>`}
      </div>
    </div>`;
}

export function Confirm({ title, message, confirmLabel, danger, onConfirm, onClose, children }) {
  return html`<${Dialog} title=${title} onClose=${onClose} narrow
    footer=${html`<button onClick=${onClose}>${t('common.cancel')}</button>
      <button class=${danger ? 'danger solid' : 'primary'} onClick=${() => { onConfirm(); onClose(); }}>${confirmLabel || t('common.ok')}</button>`}>
    <p>${message}</p>${children}
  <//>`;
}
