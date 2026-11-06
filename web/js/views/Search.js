import { html, useEffect, useState } from '../../vendor/preact-htm.js';
import { get, post } from '../api.js';
import { FilterDialog } from '../components/FilterDialog.js';
import { locale, t } from '../i18n.js';
import { Icon } from '../icons.js';
import { go, href } from '../router.js';
import { toast, toastError } from '../store.js';
import { fmtDuration, looksLikeUrl, STATE_BADGE, stateLabel } from '../util.js';

function VideoResult({ r, onCreator, onCached }) {
  const [preview, setPreview] = useState(false);
  const [state, setState] = useState(r.state);
  async function cache() {
    try {
      const v = await post(`/videos/${r.id}/cache`, { title: r.title, channel: r.channel, duration_s: r.duration_s, thumb: r.thumb });
      setState(v.state); toast(t('common.queuedForCaching')); onCached?.();
    } catch (e) { toastError(e); }
  }
  const watchable = state === 'cached';
  const busy = state === 'queued' || state === 'downloading';
  return html`<li>
    <div class="result">
      <a class="thumb" href=${href('/watch/' + r.id)}><img src=${r.thumb} alt="" loading="lazy" />
        ${r.duration_s ? html`<span class="dur">${fmtDuration(r.duration_s)}</span>` : null}</a>
      <div class="grow">
        <a href=${href('/watch/' + r.id)} class="vtitle" style="color:var(--ink)">${r.title}</a>
        <div class="vsub">${[r.channel?.title, r.live_status === 'is_live' ? t('search.liveNow') : r.live_status === 'is_upcoming' ? t('search.upcoming') : null].filter(Boolean).join(' · ')}</div>
        ${state && html`<div class="vbadges"><span class="badge ${STATE_BADGE[state] || ''}">${stateLabel(state)}</span></div>`}
        <div class="result-actions">
          ${watchable
            ? html`<a class="btn small primary" href=${href('/watch/' + r.id)}><${Icon} name="play" size="16" /> ${t('search.watch')}</a>`
            : html`<button class="small primary" disabled=${busy} onClick=${cache}><${Icon} name="download" size="16" /> ${busy ? t('search.queued') : t('common.cacheThisVideo')}</button>`}
          ${r.channel?.id && html`<button class="small" onClick=${() => onCreator(r.channel)}>${t('common.cacheThisCreator')}</button>`}
          ${!watchable && html`<button class="small ghost" onClick=${() => setPreview(!preview)}><${Icon} name="eye" size="16" /> ${preview ? t('search.closePreview') : t('search.preview')}</button>`}
        </div>
      </div>
    </div>
    ${preview && html`<div class="embed" style="margin-top:0.6rem"><iframe src=${`https://www.youtube-nocookie.com/embed/${r.id}?autoplay=1&rel=0`}
      allow="autoplay; encrypted-media; picture-in-picture; fullscreen" allowfullscreen title=${t('common.youtubePreview')}></iframe></div>`}
  </li>`;
}

function ChannelResult({ c, onCreator }) {
  return html`<li class="row">
    ${c.avatar ? html`<img class="ch-avatar" style="width:52px;height:52px" src=${c.avatar} alt="" loading="lazy" />` : html`<span class="ch-avatar" style="width:52px;height:52px"></span>`}
    <div class="grow"><div class="vtitle">${c.title}</div>
      <div class="vsub">${[c.handle, c.followers ? t('search.subscribers', { count: Intl.NumberFormat(locale(), { notation: 'compact' }).format(c.followers) }) : null].filter(Boolean).join(' · ')}</div></div>
    ${c.subscribed
      ? html`<a class="btn small" href=${href('/channel/' + c.id)}>${t('search.subscribed')}</a>`
      : html`<button class="small primary" onClick=${() => onCreator(c)}>${t('common.cacheThisCreator')}</button>`}
  </li>`;
}

export function Search({ query }) {
  const [text, setText] = useState(query.q || '');
  const [results, setResults] = useState(null);
  const [resolved, setResolved] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [creator, setCreator] = useState(null);
  const type = query.type === 'channel' ? 'channel' : 'video';
  const q = query.q || '';

  useEffect(() => {
    setText(q);
    if (!q) { setResults(null); setResolved(null); return; }
    let alive = true;
    setLoading(true); setError(null); setResults(null); setResolved(null);
    const req = looksLikeUrl(q) ? get('/resolve', { url: q }) : get('/search', { q, type });
    req.then((r) => {
      if (!alive) return;
      if (r.items) setResults(r.items);
      else if (r.type === 'video' && r.video.state === 'cached') go('/watch/' + r.video.id, null, { replace: true });
      else setResolved(r);
    }).catch((e) => alive && setError(e.message)).finally(() => alive && setLoading(false));
    return () => { alive = false; };
  }, [q, type]);

  function submit(e) {
    e.preventDefault();
    const v = text.trim();
    if (v) go('/search', { q: v, type: type === 'video' ? '' : type });
  }

  return html`<div class="stack">
    <form class="row" onSubmit=${submit} role="search">
      <input type="search" placeholder=${t('search.placeholder')} value=${text} autofocus=${!q}
        onInput=${(e) => setText(e.target.value)} enterkeyhint="search" autocapitalize="off" />
      <button class="primary icon" type="submit" aria-label=${t('search.submit')}><${Icon} name="search" /></button>
    </form>
    <div class="seg">
      <button class=${type === 'video' ? 'on' : ''} onClick=${() => go('/search', { q })}>${t('search.videos')}</button>
      <button class=${type === 'channel' ? 'on' : ''} onClick=${() => go('/search', { q, type: 'channel' })}>${t('search.creators')}</button>
    </div>
    ${loading && html`<p class="muted">${t('search.asking')}</p>`}
    ${error && html`<div class="error-box">${error}</div>`}
    ${resolved?.type === 'video' && html`<ul class="list"><${VideoResult} r=${resolved.video} onCreator=${setCreator} /></ul>`}
    ${resolved?.type === 'channel' && html`<ul class="list"><${ChannelResult} c=${resolved.channel} onCreator=${setCreator} /></ul>`}
    ${results && (results.length
      ? html`<ul class="list results">${results.map((r) => (r.type === 'channel'
        ? html`<${ChannelResult} key=${r.id} c=${r} onCreator=${setCreator} />`
        : html`<${VideoResult} key=${r.id} r=${r} onCreator=${setCreator} />`))}</ul>`
      : html`<div class="empty">${t('search.noResults')}</div>`)}
    ${!q && html`<div class="empty"><p>${t('search.intro')}</p></div>`}
    ${creator && html`<${FilterDialog} channel=${creator} onClose=${() => setCreator(null)} />`}
  </div>`;
}
