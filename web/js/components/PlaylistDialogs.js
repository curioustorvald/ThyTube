// Dialogs for playlists: make/edit one, import one from YouTube, and add a video to playlists.
import { html, useEffect, useState } from '../../vendor/preact-htm.js';
import { del, get, patch, post } from '../api.js';
import { t } from '../i18n.js';
import { go } from '../router.js';
import { toast, toastError } from '../store.js';
import { fmtDuration } from '../util.js';
import { Dialog } from './Dialog.js';

/** Make a playlist (playlist = null) or amend one. onSaved(playlist). */
export function PlaylistDialog({ playlist, onClose, onSaved }) {
  const [title, setTitle] = useState(playlist?.title || '');
  const [description, setDescription] = useState(playlist?.description || '');
  const [autoCache, setAutoCache] = useState(!!playlist?.auto_cache);
  const [busy, setBusy] = useState(false);
  async function save(e) {
    e?.preventDefault();
    if (!title.trim()) return;
    setBusy(true);
    try {
      const body = { title, description, ...(playlist?.source ? { auto_cache: autoCache } : {}) };
      const p = playlist ? await patch('/playlists/' + playlist.id, body) : await post('/playlists', body);
      onSaved?.(p);
      onClose();
    } catch (err) { toastError(err); } finally { setBusy(false); }
  }
  return html`<${Dialog} title=${playlist ? t('playlist.editTitle') : t('playlist.newTitle')} onClose=${onClose} narrow
    footer=${html`<button onClick=${onClose}>${t('common.cancel')}</button>
      <button class="primary" disabled=${busy || !title.trim()} onClick=${save}>${playlist ? t('playlist.save') : t('playlist.create')}</button>`}>
    <form class="stack" onSubmit=${save}>
      <label class="field"><span>${t('playlist.name')}</span>
        <input type="text" value=${title} maxlength="120" autofocus onInput=${(e) => setTitle(e.target.value)} /></label>
      <label class="field"><span>${t('playlist.description')}</span>
        <textarea maxlength="2000" value=${description} onInput=${(e) => setDescription(e.target.value)}></textarea></label>
      ${playlist?.source && html`<label class="check"><input type="checkbox" checked=${autoCache} onChange=${(e) => setAutoCache(e.target.checked)} />
        ${t('playlist.autoCache')}</label>`}
    </form>
  <//>`;
}

/** Import a YouTube playlist: look it up, show what it holds, then import. */
export function ImportDialog({ initialUrl = '', initialPreview = null, onClose }) {
  const [url, setUrl] = useState(initialUrl);
  const [preview, setPreview] = useState(initialPreview);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [title, setTitle] = useState(initialPreview?.title || '');
  const [cache, setCache] = useState(true);
  const [busy, setBusy] = useState(false);

  async function look(e) {
    e?.preventDefault();
    if (!url.trim()) return;
    setLoading(true); setError(null); setPreview(null);
    try {
      const p = await get('/playlists/youtube', { url: url.trim() });
      setPreview(p); setTitle(p.title || '');
    } catch (err) { setError(err.message); } finally { setLoading(false); }
  }
  useEffect(() => { if (initialUrl && !initialPreview) look(); }, []);

  async function doImport() {
    setBusy(true);
    try {
      const p = await post('/playlists/import', { url: url.trim() || preview.id, title: title.trim() || undefined, cache, again: !!preview.imported_as });
      toast(t('playlist.imported', { count: p.added }));
      onClose();
      go('/playlist/' + p.id);
    } catch (err) { toastError(err); } finally { setBusy(false); }
  }

  const missing = preview ? preview.count - preview.cached : 0;
  return html`<${Dialog} title=${t('playlist.importTitle')} onClose=${onClose}
    footer=${html`<button onClick=${onClose}>${t('common.cancel')}</button>
      <button class="primary" disabled=${!preview || !preview.count || busy} onClick=${doImport}>${busy ? t('playlist.importing') : t('playlist.import')}</button>`}>
    <div class="stack">
      <form class="row" onSubmit=${look}>
        <input type="text" placeholder=${t('playlist.urlPlaceholder')} value=${url} autofocus=${!initialUrl}
          autocapitalize="off" spellcheck="false" onInput=${(e) => setUrl(e.target.value)} />
        <button type="submit" disabled=${loading || !url.trim()}>${t('playlist.look')}</button>
      </form>
      ${loading && html`<p class="muted small">${t('playlist.asking')}</p>`}
      ${error && html`<div class="error-box">${error}</div>`}
      ${preview && html`<div class="stack">
        <div><strong>${preview.title}</strong>${preview.owner && html`<span class="muted small"> · ${preview.owner}</span>`}
          <div class="small muted">${t('playlist.previewCounts', { count: preview.count, cached: preview.cached })}</div>
          ${preview.truncated && html`<div class="small" style="color:var(--warn)">${t('playlist.truncated', { count: preview.count })}</div>`}</div>
        ${preview.imported_as && html`<div class="error-box small">${t('playlist.alreadyImported', { open: html`<a href=${'#/playlist/' + preview.imported_as} onClick=${onClose}>${t('playlist.openIt')}</a>` })}</div>`}
        <label class="field"><span>${t('playlist.name')}</span>
          <input type="text" value=${title} maxlength="120" onInput=${(e) => setTitle(e.target.value)} /></label>
        <label class="check"><input type="checkbox" checked=${cache} onChange=${(e) => setCache(e.target.checked)} />
          ${t('playlist.cacheOnImport', { count: missing })}</label>
        <p class="tiny muted">${t('playlist.importNote')}</p>
        <ul class="preview-list">${preview.items.map((i) => html`<li>
          <span class="mark ${i.state === 'cached' ? 'yes' : 'no'}">${i.state === 'cached' ? '✓' : '·'}</span>
          <span class="ttl grow">${i.title || i.id}</span>
          <span class="muted tiny">${i.duration_s ? fmtDuration(i.duration_s) : ''}</span></li>`)}</ul>
        ${preview.count > preview.items.length && html`<p class="tiny muted">${t('watch.andMore', { count: preview.count - preview.items.length })}</p>`}
      </div>`}
    </div>
  <//>`;
}

/** Tick the playlists a video belongs in. `video` may be a search result not yet known to the server. */
export function AddToPlaylist({ video, onClose }) {
  const [items, setItems] = useState(null);
  const [name, setName] = useState('');
  const load = () => get('/playlists', { video: video.id }).then((r) => setItems(r.items)).catch(toastError);
  useEffect(() => { load(); }, [video.id]);
  const particulars = { video_id: video.id, title: video.title, channel: video.channel, duration_s: video.duration_s, thumb: video.thumb };

  async function toggle(p) {
    try {
      if (p.contains) {
        await del(`/playlists/${p.id}/items/${video.id}`);
        toast(t('playlist.removedFrom', { title: p.title }));
      } else {
        await post(`/playlists/${p.id}/items`, particulars);
        toast(t('playlist.addedTo', { title: p.title }));
      }
      load();
    } catch (e) { toastError(e); }
  }
  async function create(e) {
    e.preventDefault();
    if (!name.trim()) return;
    try {
      const p = await post('/playlists', { title: name.trim() });
      await post(`/playlists/${p.id}/items`, particulars);
      toast(t('playlist.addedTo', { title: p.title }));
      setName('');
      load();
    } catch (err) { toastError(err); }
  }
  return html`<${Dialog} title=${t('playlist.addTitle')} onClose=${onClose} narrow
    footer=${html`<button class="primary" onClick=${onClose}>${t('playlist.done')}</button>`}>
    <div class="stack">
      ${items === null ? html`<p class="muted">${t('common.loading')}</p>`
        : items.length ? html`<div>${items.map((p) => html`<label class="check" key=${p.id}>
            <input type="checkbox" checked=${!!p.contains} onChange=${() => toggle(p)} />
            <span class="grow">${p.title}</span><span class="tiny muted">${p.count}</span></label>`)}</div>`
        : html`<p class="small muted">${t('playlist.noneYet')}</p>`}
      <form class="row" onSubmit=${create}>
        <input type="text" placeholder=${t('playlist.newPlaceholder')} value=${name} maxlength="120" onInput=${(e) => setName(e.target.value)} />
        <button type="submit" disabled=${!name.trim()}>${t('playlist.create')}</button>
      </form>
    </div>
  <//>`;
}
