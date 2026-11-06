import { html, useEffect, useState } from '../../vendor/preact-htm.js';
import { get, post } from '../api.js';
import { VideoCard } from '../components/VideoCard.js';
import { t } from '../i18n.js';
import { href } from '../router.js';
import { toast, toastError, useStore, prefs } from '../store.js';
import { debounce } from '../util.js';
import { enqueue } from './Watch.js';

export function VideoList({ query, emptyText, showChannel = true }) {
  const [items, setItems] = useState(null);
  const [cursor, setCursor] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const profile = useStore((s) => s.profile);
  useStore((s) => s.queueVersion);
  const queueMode = prefs().nextMode === 'queue';
  const key = JSON.stringify(query);

  async function load(more = false) {
    setLoading(true);
    try {
      const r = await get('/videos', { ...query, cursor: more ? cursor : undefined });
      setItems((old) => (more && old ? [...old, ...r.items] : r.items));
      setCursor(r.next_cursor);
      setError(null);
    } catch (e) { setError(e.message); } finally { setLoading(false); }
  }
  useEffect(() => { setItems(null); load(false); }, [key, profile?.id]);

  async function cacheAgain(e, v) {
    e.preventDefault();
    try { await post(`/videos/${v.id}/cache`); toast(t('common.queuedForCaching')); load(false); } catch (err) { toastError(err); }
  }

  if (error) return html`<div class="error-box">${error}</div>`;
  if (items === null) return html`<p class="muted">${t('common.loading')}</p>`;
  if (!items.length) return html`<div class="empty">${emptyText}</div>`;
  return html`<div>
    <div class="vlist">${items.map((v) => html`<${VideoCard} key=${v.id} v=${v} showChannel=${showChannel}
      extra=${v.state === 'evicted'
        ? html`<button class="small" style="margin-top:0.35rem" onClick=${(e) => cacheAgain(e, v)}>${t('common.cacheAgain')}</button>`
        : queueMode && v.state === 'cached' ? html`<button class="small" style="margin-top:0.35rem" onClick=${(e) => { e.preventDefault(); enqueue(v); }}>${t('library.addToQueue')}</button>` : null} />`)}</div>
    ${cursor && html`<div class="load-more"><button onClick=${() => load(true)} disabled=${loading}>${loading ? t('common.loadingMore') : t('common.loadMore')}</button></div>`}
  </div>`;
}

function ContinueRow() {
  const [items, setItems] = useState([]);
  const profile = useStore((s) => s.profile);
  useEffect(() => { get('/videos/continue').then((r) => setItems(r.items)).catch(() => {}); }, [profile?.id]);
  if (!items.length) return null;
  return html`<section>
    <div class="section-head"><h2>${t('library.continue')}</h2></div>
    <div class="hscroll">${items.map((v) => html`<${VideoCard} key=${v.id} v=${v} />`)}</div>
  </section>`;
}

export function Library({ query }) {
  const [channels, setChannels] = useState([]);
  const channel = query.channel || '';
  const sort = query.sort || 'published';
  const showEvicted = query.evicted === '1';
  const [q, setQ] = useState(query.q || '');
  const [qLive, setQLive] = useState(query.q || '');
  const setQDebounced = useState(() => debounce(setQLive, 350))[0];

  useEffect(() => { get('/channels').then((r) => setChannels(r.items)).catch(() => {}); }, []);
  const nav = (patch) => {
    const next = { channel, sort: sort === 'published' ? '' : sort, evicted: showEvicted ? '1' : '', ...patch };
    location.hash = href('/', next);
  };

  const listQuery = { state: showEvicted ? 'library' : 'cached', channel: channel || undefined, sort, q: qLive || undefined };
  return html`<div>
    ${!channel && !qLive && html`<${ContinueRow} />`}
    <div class="section-head"><h2>${channel ? (channels.find((c) => c.id === channel)?.title || t('library.channelFallback')) : t('library.title')}</h2>
      ${channel && html`<a class="small" href=${href('/channel/' + channel)}>${t('library.channelPage')}</a>`}</div>
    ${channels.length > 1 && html`<div class="chips" style="margin-bottom:0.6rem">
      <button class="chip ${!channel ? 'on' : ''}" onClick=${() => nav({ channel: '' })}>${t('library.all')}</button>
      ${channels.filter((c) => c.cached_count > 0).map((c) => html`<button class="chip ${channel === c.id ? 'on' : ''}" onClick=${() => nav({ channel: c.id })}>
        ${c.avatar && html`<img src=${c.avatar} alt="" loading="lazy" />`}${c.title || c.id}</button>`)}
    </div>`}
    <div class="row wrap" style="margin-bottom:0.8rem">
      <input type="search" placeholder=${t('library.filterTitles')} value=${q} style="max-width:320px"
        onInput=${(e) => { setQ(e.target.value); setQDebounced(e.target.value); }} />
      <select value=${sort} style="width:auto" onChange=${(e) => nav({ sort: e.target.value === 'published' ? '' : e.target.value })}>
        <option value="published">${t('library.sortPublished')}</option>
        <option value="downloaded">${t('library.sortDownloaded')}</option>
        <option value="watched">${t('library.sortWatched')}</option>
        <option value="title">${t('library.sortTitle')}</option>
        <option value="duration">${t('library.sortDuration')}</option>
      </select>
      <label class="check small"><input type="checkbox" checked=${showEvicted} onChange=${(e) => nav({ evicted: e.target.checked ? '1' : '' })} /> ${t('library.showRemoved')}</label>
    </div>
    <${VideoList} query=${listQuery} emptyText=${html`<h2>${t('library.emptyTitle')}</h2>
      <p>${t('library.emptyHint', { search: html`<a href="#/search">${t('library.searchLink')}</a>` })}</p>`} />
  </div>`;
}
