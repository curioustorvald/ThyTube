import { html, useCallback, useEffect, useRef, useState } from '../../vendor/preact-htm.js';
import { del, get, post } from '../api.js';
import { Confirm } from '../components/Dialog.js';
import { Player } from '../components/Player.js';
import { VideoBadges, VideoCard } from '../components/VideoCard.js';
import { t } from '../i18n.js';
import { Icon } from '../icons.js';
import { go, href } from '../router.js';
import { prefs, setPref, store, toast, toastError, useStore } from '../store.js';
import { fmtBytes, fmtDate, fmtDuration, parseTimestamp, reasonText, stateLabel } from '../util.js';

// [mode, label key, icon]
const MODES = [['channel', 'watch.modeChannel', 'shuffle'], ['queue', 'watch.modeQueue', 'list'], ['repeat', 'watch.modeRepeat', 'repeat'], ['off', 'watch.modeOff', 'stop']];

// Session-scoped play history (for "don't repeat until all are used up") and the user-built queue.
function played() { try { return JSON.parse(sessionStorage.getItem('thytube.played')) || []; } catch { return []; } }
function addPlayed(id) {
  const p = played().filter((x) => x !== id);
  p.push(id);
  try { sessionStorage.setItem('thytube.played', JSON.stringify(p.slice(-500))); } catch { /* ignore */ }
}
export function getQueue() { try { return JSON.parse(sessionStorage.getItem('thytube.queue')) || []; } catch { return []; } }
export function setQueue(q) {
  try { sessionStorage.setItem('thytube.queue', JSON.stringify(q)); } catch { /* ignore */ }
  store.set({ queueVersion: Date.now() });
}
export function enqueue(v) {
  const q = getQueue().filter((x) => x.id !== v.id);
  q.push({ id: v.id, title: v.title, thumb: v.thumb, duration_s: v.duration_s, channel: v.channel, state: v.state });
  setQueue(q);
  toast(t('watch.addedToQueue', { count: q.length }));
}

const TS = /(\b(?:\d{1,2}:)?\d{1,2}:\d{2}\b)/;
const URLRX = /(https?:\/\/[^\s<]+[^\s<.,;:!?)\]'"])/;

function Description({ text, onSeek }) {
  const [open, setOpen] = useState(false);
  if (!text) return null;
  const parts = text.split(new RegExp(`${URLRX.source}|${TS.source}`, 'g')).filter((x) => x !== undefined && x !== '');
  const long = text.length > 280 || text.split('\n').length > 5;
  return html`<div>
    <div class="desc ${long && !open ? 'clamped' : ''}">${parts.map((p) => {
      if (URLRX.test(p) && p.startsWith('http')) return html`<a href=${p} target="_blank" rel="noopener noreferrer">${p}</a>`;
      if (/^(?:\d{1,2}:)?\d{1,2}:\d{2}$/.test(p)) {
        const s = parseTimestamp(p);
        return html`<a href="#" onClick=${(e) => { e.preventDefault(); onSeek(s); }}>${p}</a>`;
      }
      return p;
    })}</div>
    ${long && html`<button class="ghost small" onClick=${() => setOpen(!open)}>${open ? t('watch.showLess') : t('watch.showMore')}</button>`}
  </div>`;
}

function Chapters({ chapters, time, onSeek }) {
  if (!chapters?.length) return null;
  const cur = [...chapters].reverse().find((c) => c.start <= time + 0.25);
  return html`<div class="card flush"><div style="padding:0.7rem 0.9rem 0.2rem"><h3>${t('watch.chapters')}</h3></div>
    <ul class="chapters">${chapters.map((c) => html`<li class=${cur === c ? 'on' : ''}>
      <button onClick=${() => onSeek(c.start)}><span class="t">${fmtDuration(c.start)}</span><span class="grow" style="text-align:left">${c.title}</span></button></li>`)}
    </ul></div>`;
}

function UpNext({ video, mode, setMode, pick, reroll, playNext, queue }) {
  return html`<div class="upnext stack">
    <div class="row between"><h3>${t('watch.upNext')}</h3>
      <div class="seg" role="group" aria-label=${t('watch.afterThis')}>
        ${MODES.map(([k, label, icon]) => html`<button class=${mode === k ? 'on' : ''} title=${t(label)} aria-label=${t(label)}
          onClick=${() => setMode(k)}><${Icon} name=${icon} size="18" /></button>`)}
      </div></div>
    <div class="small muted">${t(MODES.find((m) => m[0] === mode)[1])}${mode === 'channel' ? t('watch.channelOdds') : ''}</div>
    ${mode === 'channel' && (pick
      ? html`<${VideoCard} v=${pick} showChannel=${false} />
          <div class="row"><button class="small" onClick=${reroll}><${Icon} name="refresh" size="16" /> ${t('watch.reroll')}</button>
            <button class="small primary" onClick=${playNext}><${Icon} name="next" size="16" /> ${t('watch.playNow')}</button></div>`
      : html`<p class="small muted">${t('watch.noOthers', { channel: video.channel?.title || t('common.thisChannel') })}</p>`)}
    ${mode === 'queue' && (queue.length
      ? html`<div class="stack">${queue.slice(0, 6).map((q, i) => html`<div class="row">
            <div class="grow"><${VideoCard} v=${q} /></div>
            <button class="ghost icon small" aria-label=${t('watch.removeFromQueue')} onClick=${() => setQueue(queue.filter((_, j) => j !== i))}><${Icon} name="close" size="16" /></button></div>`)}
          ${queue.length > 6 && html`<p class="small muted">${t('watch.andMore', { count: queue.length - 6 })}</p>`}
          <div class="row"><button class="small primary" onClick=${playNext}>${t('watch.playNextInQueue')}</button>
            <button class="small" onClick=${() => setQueue([])}>${t('watch.clear')}</button></div></div>`
      : html`<p class="small muted">${t('watch.queueEmpty', { action: t('watch.addToQueue') })}</p>`)}
  </div>`;
}

function NotCached({ id, video, reload }) {
  const [preview, setPreview] = useState(false);
  const progress = useStore((s) => s.status?.progress?.[id]);
  async function cache() {
    try { await post(`/videos/${id}/cache`, video ? { title: video.title, channel: video.channel, duration_s: video.duration_s, thumb: video.thumb } : {}); toast(t('common.queuedForCaching')); reload(); } catch (e) { toastError(e); }
  }
  const st = video?.state;
  const busy = st === 'queued' || st === 'downloading';
  if (preview) {
    return html`<div class="embed"><iframe src=${`https://www.youtube-nocookie.com/embed/${id}?autoplay=1&rel=0`}
      allow="autoplay; encrypted-media; picture-in-picture; fullscreen" allowfullscreen title=${t('common.youtubePreview')}></iframe></div>`;
  }
  return html`<div class="notcached">
    <img src=${video?.thumb || `https://i.ytimg.com/vi/${id}/hqdefault.jpg`} alt="" />
    <div class="over">
      <strong>${st ? stateLabel(st) : t('state.notCached')}</strong>
      ${progress?.stage === 'download' && progress.total ? html`<div class="bar" style="width:min(320px,80%)"><span style=${{ width: (progress.downloaded / progress.total) * 100 + '%' }}></span></div>` : null}
      ${progress && html`<span class="small">${progress.stage === 'metadata' ? t('common.fetchingDetails') : progress.stage === 'subtitles' ? t('common.fetchingSubtitles') : progress.total ? t('common.bytesOf', { done: fmtBytes(progress.downloaded), total: fmtBytes(progress.total) }) : ''}</span>`}
      ${video?.last_error && !busy && html`<span class="small" style="max-width:560px">${reasonText(video.filter_reason) || video.last_error}</span>`}
      <div class="row wrap" style="justify-content:center">
        ${!busy && html`<button class="primary" onClick=${cache}><${Icon} name="download" /> ${st === 'evicted' ? t('common.cacheAgain') : t('common.cacheThisVideo')}</button>`}
        <button onClick=${() => setPreview(true)}><${Icon} name="eye" /> ${t('watch.previewOnYouTube')}</button>
      </div>
    </div></div>`;
}

export function Watch({ id }) {
  const [video, setVideo] = useState(null);
  const [error, setError] = useState(null);
  const [time, setTime] = useState(0);
  const [mode, setModeState] = useState(() => prefs().nextMode);
  const [pick, setPick] = useState(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const skipped = useRef([]);
  const playerApi = useRef(null);
  // Opening a cached video starts it; one that finishes downloading while this page is open doesn't.
  const autoplay = useRef(true);
  const firstLoad = useRef(true);
  useStore((s) => s.queueVersion);
  const queue = getQueue();
  const profile = useStore((s) => s.profile);

  const load = useCallback(async () => {
    try {
      const v = await get('/videos/' + id);
      if (firstLoad.current) { firstLoad.current = false; autoplay.current = v.state === 'cached'; }
      setVideo(v); setError(null);
      return v;
    } catch (e) {
      if (e.status === 404) {
        setVideo({ id, state: null, missing: true });
        setError(null);
        get('/resolve', { url: id }).then((r) => r.type === 'video' && setVideo((cur) => (cur?.id === id ? { ...r.video, id, state: r.video.state, missing: true } : cur))).catch(() => {});
      } else setError(e.message);
    }
  }, [id, profile?.id]);

  useEffect(() => { firstLoad.current = true; load(); addPlayed(id); skipped.current = []; window.scrollTo({ top: 0 }); }, [id, profile?.id]);

  // Poll while queued/downloading.
  useEffect(() => {
    if (!video || !['queued', 'downloading'].includes(video.state)) return;
    const iv = setInterval(load, 3000);
    return () => clearInterval(iv);
  }, [video?.state, id]);

  const fetchPick = useCallback(async () => {
    if (!video || video.state !== 'cached') return;
    try {
      const r = await get('/next', { video: id, exclude: [...played(), ...skipped.current].join(',') });
      setPick(r.video);
    } catch { setPick(null); }
  }, [id, video?.state]);
  useEffect(() => { if (mode === 'channel') fetchPick(); }, [id, mode, video?.state]);

  function setMode(m) { setModeState(m); setPref('nextMode', m); }
  function reroll() { if (pick) skipped.current.push(pick.id); fetchPick(); }
  function playNext() {
    if (mode === 'queue') {
      const q = getQueue();
      if (!q.length) return;
      const [first, ...rest] = q;
      setQueue(rest);
      go('/watch/' + first.id);
    } else if (pick) go('/watch/' + pick.id);
  }
  function onEnded() {
    if (mode === 'repeat') { playerApi.current?.seek(0); return; }
    if (mode === 'off') return;
    playNext();
  }

  async function togglePin() {
    try { const v = video.pinned ? await del(`/videos/${id}/pin`) : await post(`/videos/${id}/pin`); setVideo({ ...video, pinned: v.pinned }); } catch (e) { toastError(e); }
  }
  async function deleteFiles() {
    try { await del(`/videos/${id}/files`); toast(t('watch.removing')); setTimeout(load, 2500); } catch (e) { toastError(e); }
  }

  if (error) return html`<div class="empty"><h2>${t('watch.loadError')}</h2><p>${error}</p></div>`;
  if (!video) return html`<p class="muted">${t('common.loading')}</p>`;
  const cached = video.state === 'cached' && video.media_url;
  const seek = (s) => playerApi.current?.seek(s);
  const myCount = video.me?.watch_count || 0;

  return html`<div class="watch">
    <div class="watch-main">
      ${cached
        ? html`<${Player} video=${video} api=${playerApi} autoplay=${autoplay.current} onEnded=${onEnded} onTime=${setTime}
            onNext=${(mode === 'channel' && pick) || (mode === 'queue' && queue.length) ? playNext : null} />`
        : html`<${NotCached} id=${id} video=${video.missing && !video.title ? null : video} reload=${load} />`}
      <h1 class="watch-title">${video.title || id}</h1>
      <div class="row wrap small muted" style="margin-top:0.35rem">
        ${video.channel?.id && html`<a href=${href('/channel/' + video.channel.id)} class="row" style="gap:0.4rem">
          ${video.channel.avatar && html`<img class="ch-avatar" style="width:26px;height:26px" src=${video.channel.avatar} alt="" />`}
          <strong>${video.channel.title || video.channel.id}</strong></a>`}
        ${video.published_at && html`<span>· ${fmtDate(video.published_at)}</span>`}
        ${video.duration_s ? html`<span>· ${fmtDuration(video.duration_s)}</span>` : null}
        ${video.height && html`<span>· ${video.height}p</span>`}
        ${video.size_bytes && cached ? html`<span>· ${fmtBytes(video.size_bytes)}</span>` : null}
        ${video.total_watch_count > 0 && html`<span>· ${t('watch.watched', { total: video.total_watch_count, mine: myCount })}</span>`}
      </div>
      <${VideoBadges} v=${video} />
      <div class="row wrap" style="margin:0.7rem 0">
        ${cached && html`<button class="small" onClick=${togglePin}><${Icon} name="pin" size="16" /> ${video.pinned ? t('watch.unpin') : t('watch.pin')}</button>`}
        ${cached && html`<button class="small" onClick=${() => enqueue(video)}><${Icon} name="plus" size="16" /> ${t('watch.addToQueue')}</button>`}
        ${cached && html`<button class="small danger" onClick=${() => setConfirmDelete(true)}><${Icon} name="trash" size="16" /> ${t('watch.removeFile')}</button>`}
        <a class="btn small" href=${`https://www.youtube.com/watch?v=${id}`} target="_blank" rel="noopener noreferrer"><${Icon} name="external" size="16" /> ${t('watch.youtube')}</a>
      </div>
      ${video.pinned && html`<p class="tiny muted">${t('watch.pinnedNote')}</p>`}
      <${Description} text=${video.description} onSeek=${seek} />
    </div>
    <aside class="stack">
      ${cached && html`<${UpNext} video=${video} mode=${mode} setMode=${setMode} pick=${pick} reroll=${reroll} playNext=${playNext} queue=${queue} />`}
      <${Chapters} chapters=${video.chapters} time=${time} onSeek=${seek} />
    </aside>
    ${confirmDelete && html`<${Confirm} title=${t('watch.removeTitle')} danger confirmLabel=${t('watch.removeConfirm')}
      message=${t('watch.removeMessage')}
      onConfirm=${deleteFiles} onClose=${() => setConfirmDelete(false)} />`}
  </div>`;
}
