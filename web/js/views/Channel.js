import { html, useEffect, useState } from '../../vendor/preact-htm.js';
import { get, post } from '../api.js';
import { FilterDialog } from '../components/FilterDialog.js';
import { Icon } from '../icons.js';
import { toast, toastError } from '../store.js';
import { fmtBytes, fmtDuration } from '../util.js';
import { VideoList } from './Library.js';

export function rulesSummary(s) {
  const bits = [];
  if (s.include_regex) bits.push(html`title matches <code>${s.include_regex}</code>`);
  if (s.exclude_regex) bits.push(html`not <code>${s.exclude_regex}</code>`);
  if (s.min_duration_s) bits.push(`≥ ${fmtDuration(s.min_duration_s)}`);
  if (s.max_duration_s) bits.push(`≤ ${fmtDuration(s.max_duration_s)}`);
  bits.push(s.include_shorts ? 'with Shorts' : 'no Shorts');
  bits.push(s.include_streams ? 'with streams' : 'no streams');
  if (s.max_height) bits.push(`${s.max_height}p max`);
  return bits.reduce((acc, b, i) => (i ? [...acc, ' · ', b] : [b]), []);
}

export function Channel({ id }) {
  const [ch, setCh] = useState(null);
  const [error, setError] = useState(null);
  const [edit, setEdit] = useState(null);
  const load = () => get('/channels/' + id).then(setCh).catch((e) => setError(e.message));
  useEffect(() => { setCh(null); load(); }, [id]);
  if (error) return html`<div class="empty"><h2>Unknown channel</h2><p>${error}</p></div>`;
  if (!ch) return html`<p class="muted">Loading…</p>`;
  const channel = { id: ch.id, title: ch.title, handle: ch.handle, avatar: ch.avatar };
  return html`<div class="stack">
    <div class="channel-head">
      ${ch.avatar ? html`<img src=${ch.avatar} alt="" />` : html`<div class="ph"></div>`}
      <div class="grow"><h1>${ch.title || ch.id}</h1>
        <div class="small muted">${ch.handle || ''} · ${ch.cached_count} cached · ${fmtBytes(ch.cached_bytes)}</div></div>
      <a class="btn small" href=${`https://www.youtube.com/channel/${ch.id}`} target="_blank" rel="noopener noreferrer"><${Icon} name="external" size="16" /></a>
    </div>
    ${ch.subscriptions.length
      ? ch.subscriptions.map((s) => html`<div class="card row wrap">
          <div class="grow small"><strong>${s.enabled ? 'Subscribed' : 'Subscription paused'}:</strong> ${rulesSummary(s)}</div>
          <button class="small" onClick=${() => setEdit(s)}><${Icon} name="edit" size="16" /> Edit rules</button>
          <button class="small" onClick=${() => post(`/subscriptions/${s.id}/poll`).then(() => toast('Checking for new uploads…')).catch(toastError)}>
            <${Icon} name="refresh" size="16" /> Check now</button></div>`)
      : html`<div class="card row wrap"><span class="grow small muted">Not subscribed. Only videos you cached by hand are here.</span>
          <button class="small primary" onClick=${() => setEdit('new')}>Cache this creator…</button></div>`}
    <${VideoList} query=${{ channel: id, state: 'cached' }} showChannel=${false}
      emptyText=${html`<p>No cached videos from this channel yet.</p>`} />
    ${edit && html`<${FilterDialog} channel=${channel} subscription=${edit === 'new' ? null : edit}
      onClose=${() => setEdit(null)} onSaved=${load} />`}
  </div>`;
}
