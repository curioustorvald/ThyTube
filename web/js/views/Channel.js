import { html, useEffect, useState } from '../../vendor/preact-htm.js';
import { get, post } from '../api.js';
import { FilterDialog } from '../components/FilterDialog.js';
import { t } from '../i18n.js';
import { Icon } from '../icons.js';
import { toast, toastError } from '../store.js';
import { fmtBytes, fmtDuration } from '../util.js';
import { VideoList } from './Library.js';

export function rulesSummary(s) {
  const bits = [];
  if (s.include_regex) bits.push(t('rules.titleMatches', { pattern: html`<code>${s.include_regex}</code>` }));
  if (s.exclude_regex) bits.push(t('rules.titleNot', { pattern: html`<code>${s.exclude_regex}</code>` }));
  if (s.min_duration_s) bits.push(t('rules.atLeast', { duration: fmtDuration(s.min_duration_s) }));
  if (s.max_duration_s) bits.push(t('rules.atMost', { duration: fmtDuration(s.max_duration_s) }));
  bits.push(t(s.include_shorts ? 'rules.withShorts' : 'rules.noShorts'));
  bits.push(t(s.include_streams ? 'rules.withStreams' : 'rules.noStreams'));
  if (s.max_height) bits.push(t('rules.maxHeight', { height: s.max_height }));
  return bits.reduce((acc, b, i) => (i ? [...acc, ' · ', b] : [b]), []);
}

export function Channel({ id }) {
  const [ch, setCh] = useState(null);
  const [error, setError] = useState(null);
  const [edit, setEdit] = useState(null);
  const load = () => get('/channels/' + id).then(setCh).catch((e) => setError(e.message));
  useEffect(() => { setCh(null); load(); }, [id]);
  if (error) return html`<div class="empty"><h2>${t('channel.unknown')}</h2><p>${error}</p></div>`;
  if (!ch) return html`<p class="muted">${t('common.loading')}</p>`;
  const channel = { id: ch.id, title: ch.title, handle: ch.handle, avatar: ch.avatar };
  return html`<div class="stack">
    <div class="channel-head">
      ${ch.avatar ? html`<img src=${ch.avatar} alt="" />` : html`<div class="ph"></div>`}
      <div class="grow"><h1>${ch.title || ch.id}</h1>
        <div class="small muted">${t('channel.summary', { handle: ch.handle || '', cached: ch.cached_count, size: fmtBytes(ch.cached_bytes) })}</div></div>
      <a class="btn small" href=${`https://www.youtube.com/channel/${ch.id}`} target="_blank" rel="noopener noreferrer"
        aria-label=${t('channel.onYouTube')} title=${t('channel.onYouTube')}><${Icon} name="external" size="16" /></a>
    </div>
    ${ch.subscriptions.length
      ? ch.subscriptions.map((s) => html`<div class="card row wrap">
          <div class="grow small"><strong>${s.enabled ? t('channel.subscribed') : t('channel.paused')}:</strong> ${rulesSummary(s)}</div>
          <button class="small" onClick=${() => setEdit(s)}><${Icon} name="edit" size="16" /> ${t('channel.editRules')}</button>
          <button class="small" onClick=${() => post(`/subscriptions/${s.id}/poll`).then(() => toast(t('common.checkingUploads'))).catch(toastError)}>
            <${Icon} name="refresh" size="16" /> ${t('common.checkNow')}</button></div>`)
      : html`<div class="card row wrap"><span class="grow small muted">${t('channel.notSubscribed')}</span>
          <button class="small primary" onClick=${() => setEdit('new')}>${t('common.cacheThisCreator')}</button></div>`}
    <${VideoList} query=${{ channel: id, state: 'cached' }} showChannel=${false}
      emptyText=${html`<p>${t('channel.empty')}</p>`} />
    ${edit && html`<${FilterDialog} channel=${channel} subscription=${edit === 'new' ? null : edit}
      onClose=${() => setEdit(null)} onSaved=${load} />`}
  </div>`;
}
