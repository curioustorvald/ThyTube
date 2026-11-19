// Playlists: the list of them (#/playlists) and one playlist (#/playlist/<id>).
import { html, useEffect, useState } from '../../vendor/preact-htm.js';
import { del, get, post, put } from '../api.js';
import { Confirm } from '../components/Dialog.js';
import { ImportDialog, PlaylistDialog } from '../components/PlaylistDialogs.js';
import { Avatar } from '../components/Profiles.js';
import { VideoCard } from '../components/VideoCard.js';
import { t } from '../i18n.js';
import { Icon } from '../icons.js';
import { go, href } from '../router.js';
import { setPref, toast, toastError, useStore } from '../store.js';
import { ago, fmtDuration } from '../util.js';

/** Remembered per tab: which videos a shuffled playlist has already played (reset by "Shuffle"). */
export function playlistPlayed(pid) {
  try { return JSON.parse(sessionStorage.getItem('thytube.plPlayed.' + pid)) || []; } catch { return []; }
}
export function setPlaylistPlayed(pid, ids) {
  try { sessionStorage.setItem('thytube.plPlayed.' + pid, JSON.stringify(ids.slice(-2000))); } catch { /* ignore */ }
}

function Mosaic({ thumbs }) {
  if (!thumbs?.length) return html`<div class="thumb pl-mosaic empty-thumb"><${Icon} name="playlist" size="32" /></div>`;
  if (thumbs.length < 4) return html`<div class="thumb pl-mosaic"><img src=${thumbs[0]} alt="" loading="lazy" /></div>`;
  return html`<div class="thumb pl-mosaic four">${thumbs.map((u) => html`<img src=${u} alt="" loading="lazy" />`)}</div>`;
}

const countsLine = (p) => [
  t('common.videos', { count: p.count }),
  p.cached_count < p.count ? t('playlist.cachedOf', { count: p.cached_count }) : null,
  p.duration_s ? fmtDuration(p.duration_s) : null,
].filter(Boolean).join(' · ');

export function Playlists() {
  const [items, setItems] = useState(null);
  const [error, setError] = useState(null);
  const [making, setMaking] = useState(false);
  const [importing, setImporting] = useState(false);
  useEffect(() => { get('/playlists').then((r) => setItems(r.items)).catch((e) => setError(e.message)); }, []);
  return html`<div>
    <div class="section-head"><h2>${t('playlist.listTitle')}</h2></div>
    <div class="row wrap" style="margin-bottom:0.9rem">
      <button class="small primary" onClick=${() => setMaking(true)}><${Icon} name="plus" size="16" /> ${t('playlist.new')}</button>
      <button class="small" onClick=${() => setImporting(true)}><${Icon} name="download" size="16" /> ${t('playlist.importFromYouTube')}</button>
    </div>
    ${error && html`<div class="error-box">${error}</div>`}
    ${items === null && !error && html`<p class="muted">${t('common.loading')}</p>`}
    ${items && !items.length && html`<div class="empty"><h2>${t('playlist.emptyTitle')}</h2><p>${t('playlist.emptyHint')}</p></div>`}
    ${items?.length > 0 && html`<div class="vlist">${items.map((p) => html`<a class="vcard" key=${p.id} href=${href('/playlist/' + p.id)}>
      <${Mosaic} thumbs=${p.thumbs} />
      <div class="vmeta">
        <div class="vtitle">${p.title}</div>
        <div class="vsub">${countsLine(p)}</div>
        <div class="vbadges">
          ${p.source && html`<span class="badge info">${t('playlist.fromYouTube')}</span>`}
          ${p.queued_count > 0 && html`<span class="badge info">${t('playlist.awaiting', { count: p.queued_count })}</span>`}
          ${p.owner && html`<span class="badge">${p.owner.name}</span>`}
        </div>
      </div></a>`)}</div>`}
    ${making && html`<${PlaylistDialog} onClose=${() => setMaking(false)} onSaved=${(p) => go('/playlist/' + p.id)} />`}
    ${importing && html`<${ImportDialog} onClose=${() => setImporting(false)} />`}
  </div>`;
}

export function PlaylistView({ id }) {
  const [pl, setPl] = useState(null);
  const [error, setError] = useState(null);
  const [editing, setEditing] = useState(false);
  const [arranging, setArranging] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const profile = useStore((s) => s.profile);
  const load = () => get('/playlists/' + id).then((p) => { setPl(p); setError(null); }).catch((e) => setError(e.status === 404 ? t('playlist.unknown') : e.message));
  useEffect(() => { setPl(null); load(); }, [id, profile?.id]);
  // Follow downloads while some of the playlist is on its way.
  useEffect(() => {
    if (!pl?.queued_count) return;
    const iv = setInterval(() => document.visibilityState === 'visible' && load(), 8000);
    return () => clearInterval(iv);
  }, [pl?.queued_count, id]);

  if (error) return html`<div class="empty"><h2>${error}</h2><a href="#/playlists">${t('playlist.backToList')}</a></div>`;
  if (!pl) return html`<p class="muted">${t('common.loading')}</p>`;

  const cached = pl.items.filter((v) => v.state === 'cached');
  const missing = pl.items.filter((v) => !['cached', 'queued', 'downloading'].includes(v.state)).length;
  const watchHref = (v) => href('/watch/' + v.id, { list: pl.id });

  function play(shuffle) {
    if (!cached.length) return;
    setPref('playlistShuffle', shuffle);
    setPlaylistPlayed(pl.id, []);
    const first = shuffle ? cached[Math.floor(Math.random() * cached.length)] : cached[0];
    go('/watch/' + first.id, { list: pl.id });
  }
  async function cacheMissing() {
    try { const r = await post(`/playlists/${pl.id}/cache`); toast(t('playlist.queuedMissing', { count: r.queued })); load(); } catch (e) { toastError(e); }
  }
  async function sync() {
    setSyncing(true);
    try {
      const r = await post(`/playlists/${pl.id}/sync`);
      toast(r.added ? t('playlist.syncAdded', { count: r.added }) : t('playlist.syncNothing'));
      load();
    } catch (e) { toastError(e); } finally { setSyncing(false); }
  }
  async function move(i, by) {
    const j = i + by;
    if (j < 0 || j >= pl.items.length) return;
    const items = [...pl.items];
    [items[i], items[j]] = [items[j], items[i]];
    setPl({ ...pl, items });
    try { await put(`/playlists/${pl.id}/order`, { video_ids: items.map((v) => v.id) }); } catch (e) { toastError(e); load(); }
  }
  async function remove(v) {
    setPl({ ...pl, items: pl.items.filter((x) => x.id !== v.id) });
    try { await del(`/playlists/${pl.id}/items/${v.id}`); load(); } catch (e) { toastError(e); load(); }
  }
  async function doDelete() {
    try { await del('/playlists/' + pl.id); toast(t('playlist.deleted')); go('/playlists', null, { replace: true }); } catch (e) { toastError(e); }
  }

  return html`<div class="stack">
    <div class="pl-head">
      <${Mosaic} thumbs=${pl.thumbs} />
      <div class="grow">
        <h1>${pl.title}</h1>
        <div class="small muted">${countsLine(pl)}</div>
        <div class="tiny muted row wrap" style="gap:0.35rem;margin-top:0.25rem">
          ${pl.owner && html`<${Avatar} p=${pl.owner} size="20px" /><span>${t('playlist.madeBy', { name: pl.owner.name })}</span>`}
          ${pl.source && html`<span>${t('playlist.sourceLine', {
            link: html`<a href=${pl.source.url} target="_blank" rel="noopener noreferrer">${pl.source.title || pl.source.id}</a>`,
            owner: pl.source.owner || '?',
          })}</span>`}
          ${pl.synced_at && html`<span>· ${t('playlist.synced', { when: ago(pl.synced_at) })}</span>`}
        </div>
      </div>
    </div>
    ${pl.description && html`<p class="desc small">${pl.description}</p>`}
    <div class="row wrap">
      <button class="small primary" disabled=${!cached.length} onClick=${() => play(false)}><${Icon} name="play" size="16" /> ${t('playlist.playAll')}</button>
      <button class="small" disabled=${!cached.length} onClick=${() => play(true)}><${Icon} name="shuffle" size="16" /> ${t('playlist.shuffle')}</button>
      ${missing > 0 && html`<button class="small" onClick=${cacheMissing}><${Icon} name="download" size="16" /> ${t('playlist.cacheMissing', { count: missing })}</button>`}
      ${pl.source && html`<button class="small" disabled=${syncing} onClick=${sync}><${Icon} name="refresh" size="16" /> ${syncing ? t('playlist.syncing') : t('playlist.sync')}</button>`}
      <button class=${arranging ? 'small primary' : 'small'} aria-pressed=${arranging} onClick=${() => setArranging(!arranging)}><${Icon} name="list" size="16" /> ${arranging ? t('playlist.arrangeDone') : t('playlist.arrange')}</button>
      <button class="small" onClick=${() => setEditing(true)}><${Icon} name="edit" size="16" /> ${t('playlist.edit')}</button>
      <button class="small danger" onClick=${() => setConfirmDelete(true)}><${Icon} name="trash" size="16" /> ${t('playlist.delete')}</button>
    </div>
    ${pl.items.length
      ? html`<ol class="pl-items">${pl.items.map((v, i) => html`<li key=${v.id}>
          <span class="pl-num">${i + 1}</span>
          <div class="grow"><${VideoCard} v=${v} to=${watchHref(v)} /></div>
          ${arranging && html`<div class="pl-tools">
            <button class="ghost icon small" aria-label=${t('playlist.moveUp')} disabled=${i === 0} onClick=${() => move(i, -1)}><${Icon} name="up" size="18" /></button>
            <button class="ghost icon small" aria-label=${t('playlist.moveDown')} disabled=${i === pl.items.length - 1} onClick=${() => move(i, 1)}><${Icon} name="down" size="18" /></button>
            <button class="ghost icon small" aria-label=${t('playlist.removeItem')} onClick=${() => remove(v)}><${Icon} name="close" size="18" /></button>
          </div>`}
        </li>`)}</ol>`
      : html`<div class="empty"><p>${t('playlist.noItems', { action: t('playlist.addToPlaylist') })}</p></div>`}
    ${editing && html`<${PlaylistDialog} playlist=${pl} onClose=${() => setEditing(false)} onSaved=${load} />`}
    ${confirmDelete && html`<${Confirm} title=${t('playlist.deleteTitle')} danger confirmLabel=${t('playlist.delete')}
      message=${t('playlist.deleteMessage')} onConfirm=${doDelete} onClose=${() => setConfirmDelete(false)} />`}
  </div>`;
}
