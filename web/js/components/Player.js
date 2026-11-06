// The video player: one <video> element for the whole session, reused across videos and across
// visits to the watch page (so autoplay keeps working on iOS once a tap has unlocked it), our own controls, our own subtitle overlay (plus native tracks
// for iPhone fullscreen), resume, chapters on the seek bar, touch gestures, keyboard shortcuts,
// Media Session metadata and watch heartbeats.
import { html, useEffect, useRef, useState } from '../../vendor/preact-htm.js';
import { beacon, post } from '../api.js';
import { t as tr } from '../i18n.js';
import { Icon } from '../icons.js';
import { prefs, setPref, store } from '../store.js';
import { activeCues, langName, loadCues } from '../subs.js';
import { fmtDuration, uuid } from '../util.js';

const SPEEDS = [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2];
const SUB_SIZES = { s: '0.9rem', m: '1.15rem', l: '1.45rem', xl: '1.85rem' };
const SUB_BGS = [[0, 'bgNone'], [0.45, 'bgLight'], [0.75, 'bgSolid']]; // [opacity, player.* key]
const HEARTBEAT_MS = 15000;
const IS_IPHONE = /iPhone|iPod/.test(navigator.userAgent);

function trackKey(s) { return s ? `${s.lang}.${s.kind}` : 'off'; }

// iOS (and Safari generally) only lets an element start playback with sound after a user gesture
// has touched *that element*; the unlock then sticks for the element's lifetime. So the player
// never re-creates its <video>: it borrows this one, and the first tap anywhere in the app unlocks
// it (load() inside a gesture is enough), which lets "click a video → it starts" work from the
// library on the very first visit. Desktop/Android browsers only need sticky page activation.
let shared = null;
function sharedVideo() {
  if (!shared) {
    shared = document.createElement('video');
    shared.playsInline = true;
    shared.setAttribute('playsinline', '');
    shared.preload = 'metadata';
    const unlock = () => {
      if (!shared.isConnected && !shared.getAttribute('src')) shared.load();
      document.removeEventListener('click', unlock, true);
      document.removeEventListener('touchend', unlock, true);
    };
    document.addEventListener('click', unlock, true);
    document.addEventListener('touchend', unlock, true);
  }
  return shared;
}
sharedVideo(); // create it (and its unlock hook) at start-up, before the first tap

export function Player({ video, onEnded, onNext, onTime, api: apiRef, autoplay }) {
  const box = useRef(null);
  const slot = useRef(null);
  const vid = useRef(sharedVideo());
  const hb = useRef({ session: null, videoId: null, played: 0, lastT: null, lastSent: 0 });
  const hideTimer = useRef(null);
  const tap = useRef({ t: 0, side: null, timer: null });
  const [t, setT] = useState(0);
  const [dur, setDur] = useState(video.duration_s || 0);
  const [buffered, setBuffered] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [started, setStarted] = useState(false);
  const [showControls, setShowControls] = useState(true);
  const [menu, setMenu] = useState(null);
  const [p, setP] = useState(prefs);
  const [track, setTrack] = useState(null);
  const [cues, setCues] = useState([]);
  const [lines, setLines] = useState([]);
  const [fs, setFs] = useState(false);
  const [hint, setHint] = useState(null);
  const [resumeFrom, setResumeFrom] = useState(null);
  const [error, setError] = useState(null);
  const [scrub, setScrub] = useState(null);
  const [muted, setMuted] = useState(false);

  const chapters = video.chapters || [];
  const subs = video.subtitles || [];

  // ---- native <track>s ----------------------------------------------------------------
  // Our overlay draws the subtitles; the native tracks exist only for iPhone's native fullscreen.
  // Browsers may switch a track to 'showing' on their own, after we added it (automatic caption
  // selection from the OS/browser accessibility settings, Safari's "Automatic" captions…), so
  // every addtrack/change event re-applies what we want: all disabled, except the chosen track
  // while the native fullscreen player is up. The user's own pick in that player is left alone.
  const native = useRef({ subs, track: null, fs: false });
  native.current.subs = subs;
  native.current.track = track;
  function syncNativeTracks(force = false) {
    const n = native.current;
    if (n.fs && !force) return;
    // <track> elements are rendered in the same order as `subs`.
    Array.from(vid.current.textTracks).forEach((tt, i) => {
      const mode = n.fs && n.track && trackKey(n.subs[i]) === trackKey(n.track) ? 'showing' : 'disabled';
      if (tt.mode !== mode) tt.mode = mode;
    });
  }

  // ---- heartbeats ---------------------------------------------------------------
  function heartbeatPayload() {
    const v = vid.current;
    const h = hb.current;
    const profile = store.get().profile;
    return { session: h.session, video: h.videoId, profile: profile ? profile.id : null,
      position: v ? v.currentTime : 0, played_delta: Math.round(h.played * 10) / 10 };
  }
  function flush(useBeacon = false) {
    const h = hb.current;
    if (!h.session || !h.videoId) return;
    if (h.played < 0.5 && Date.now() - h.lastSent < HEARTBEAT_MS) return;
    const payload = heartbeatPayload();
    h.played = 0;
    h.lastSent = Date.now();
    if (useBeacon) beacon('/playback/heartbeat', payload);
    else post('/playback/heartbeat', payload).catch(() => {});
  }
  useEffect(() => {
    const iv = setInterval(() => { if (!vid.current?.paused) flush(); }, HEARTBEAT_MS);
    const onHide = () => flush(true);
    const onVis = () => { if (document.visibilityState === 'hidden') flush(true); };
    window.addEventListener('pagehide', onHide);
    document.addEventListener('visibilitychange', onVis);
    return () => { clearInterval(iv); window.removeEventListener('pagehide', onHide); document.removeEventListener('visibilitychange', onVis); flush(true); };
  }, []);

  // ---- borrow the shared <video> ----------------------------------------------------------
  // Element events go through a ref so the listeners (attached once) always see this render.
  const on = useRef({});
  on.current = {
    play: () => { setPlaying(true); setStarted(true); },
    pause: () => setPlaying(false),
    timeupdate: () => onTimeUpdate(),
    seeking: () => onSeeking(),
    ended: () => { flush(); onEnded?.(); },
    error: () => { if (vid.current.getAttribute('src')) setError(tr('player.cantPlay')); },
    volumechange: () => setMuted(vid.current.muted),
  };
  useEffect(() => {
    const v = vid.current;
    slot.current.appendChild(v);
    setMuted(v.muted);
    const ls = Object.keys(on.current).map((k) => [k, (e) => on.current[k](e)]);
    for (const [k, f] of ls) v.addEventListener(k, f);
    const sync = () => syncNativeTracks();
    const beginFs = () => { native.current.fs = true; syncNativeTracks(true); };
    const endFs = () => { native.current.fs = false; syncNativeTracks(); };
    v.textTracks.addEventListener('addtrack', sync);
    v.textTracks.addEventListener('change', sync);
    v.addEventListener('webkitbeginfullscreen', beginFs);
    v.addEventListener('webkitendfullscreen', endFs);
    return () => {
      flush(true); // report the position before the element is reset
      for (const [k, f] of ls) v.removeEventListener(k, f);
      v.textTracks.removeEventListener('addtrack', sync);
      v.textTracks.removeEventListener('change', sync);
      v.removeEventListener('webkitbeginfullscreen', beginFs);
      v.removeEventListener('webkitendfullscreen', endFs);
      native.current.fs = false;
      v.pause();
      v.removeAttribute('src');
      v.removeAttribute('poster');
      for (const el of v.querySelectorAll('track')) el.remove();
      v.load(); // abort the download
      v.remove();
    };
  }, []);

  // ---- switching videos -------------------------------------------------------------
  useEffect(() => {
    const v = vid.current;
    if (hb.current.videoId && hb.current.videoId !== video.id) flush(true);
    hb.current = { session: uuid(), videoId: video.id, played: 0, lastT: null, lastSent: Date.now() };
    // An ended video reports paused; it was playing, so keep going (auto-advance).
    const wasPlaying = !v.paused || v.ended || autoplay;
    setError(null); setT(0); setBuffered(0); setDur(video.duration_s || 0); setMenu(null); setScrub(null);
    // Native <track>s (for iPhone fullscreen), in the same order as `subs`.
    for (const el of v.querySelectorAll('track')) el.remove();
    for (const s of subs) {
      const el = document.createElement('track');
      el.kind = 'subtitles'; el.src = s.url; el.srclang = s.lang;
      el.label = s.kind === 'auto' ? tr('player.autoShort', { lang: langName(s.lang) }) : langName(s.lang);
      v.appendChild(el);
    }
    v.src = video.media_url;
    v.poster = video.thumb || '';
    v.playbackRate = p.speed;
    const pos = video.me?.position_s || 0;
    const onMeta = () => {
      setDur(v.duration || video.duration_s || 0);
      if (pos > 5 && (!v.duration || pos < v.duration - 10)) {
        v.currentTime = pos;
        setResumeFrom(pos);
        setTimeout(() => setResumeFrom(null), 7000);
      }
      v.playbackRate = prefs().speed;
    };
    v.addEventListener('loadedmetadata', onMeta, { once: true });
    v.load();
    // Autoplay can still be refused (e.g. a direct link opened with no tap yet): show the big play button.
    if (wasPlaying) v.play().catch((e) => { if (e.name === 'NotAllowedError') setStarted(false); });
    setStarted(wasPlaying);
    // Subtitle choice: remember the language across videos.
    const want = prefs().subLang;
    const pick = want === 'off' ? null : subs.find((s) => s.lang === want) || null;
    setTrack(pick);
    // Media Session.
    if ('mediaSession' in navigator) {
      navigator.mediaSession.metadata = new MediaMetadata({
        title: video.title || video.id,
        artist: video.channel?.title || '',
        album: 'ThyTube',
        artwork: video.thumb ? [{ src: new URL(video.thumb, location.href).href, sizes: '480x270', type: 'image/jpeg' }] : [],
      });
    }
    return () => v.removeEventListener('loadedmetadata', onMeta);
  }, [video.id]);

  // ---- subtitles ------------------------------------------------------------------------
  useEffect(() => {
    let alive = true;
    setCues([]); setLines([]);
    if (track) loadCues(track.url).then((c) => alive && setCues(c)).catch(() => alive && setCues([]));
    syncNativeTracks();
    return () => { alive = false; };
  }, [track && track.url]);

  useEffect(() => {
    if (!cues.length) { setLines([]); return; }
    let raf, last = '';
    const loop = () => {
      if (!vid.current) return;
      const now = activeCues(cues, vid.current.currentTime).map((c) => c.text);
      const key = now.join('\u0000');
      if (key !== last) { last = key; setLines(now); }
      raf = requestAnimationFrame(loop);
    };
    loop();
    return () => cancelAnimationFrame(raf);
  }, [cues]);

  // ---- media session handlers -----------------------------------------------------------
  useEffect(() => {
    if (!('mediaSession' in navigator)) return;
    const ms = navigator.mediaSession;
    const set = (a, f) => { try { ms.setActionHandler(a, f); } catch { /* unsupported */ } };
    set('play', () => vid.current.play());
    set('pause', () => vid.current.pause());
    set('seekbackward', () => seekBy(-10));
    set('seekforward', () => seekBy(10));
    set('seekto', (d) => { vid.current.currentTime = d.seekTime; });
    set('nexttrack', onNext ? () => onNext() : null);
    return () => ['play', 'pause', 'seekbackward', 'seekforward', 'seekto', 'nexttrack'].forEach((a) => set(a, null));
  }, [onNext]);

  // ---- element events ------------------------------------------------------------------
  function onTimeUpdate() {
    const v = vid.current;
    const ct = v.currentTime;
    const h = hb.current;
    if (h.lastT !== null && !v.seeking) {
      const d = ct - h.lastT;
      if (d > 0 && d < 3) h.played += d;
    }
    h.lastT = ct;
    setT(ct);
    if (v.buffered.length) {
      for (let i = 0; i < v.buffered.length; i++) {
        if (v.buffered.start(i) <= ct + 0.5) setBuffered(v.buffered.end(i));
      }
    }
    onTime?.(ct);
  }
  function onSeeking() { hb.current.lastT = null; }

  // Expose a tiny imperative API (seek from description timestamps / chapters).
  useEffect(() => {
    if (!apiRef) return;
    apiRef.current = {
      seek: (s) => { const v = vid.current; v.currentTime = s; if (v.paused) v.play().catch(() => {}); box.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); },
      flush: () => flush(true),
    };
  });

  // ---- controls visibility ----------------------------------------------------------------
  function poke() {
    setShowControls(true);
    clearTimeout(hideTimer.current);
    hideTimer.current = setTimeout(() => { if (!vid.current?.paused && !menuOpen()) setShowControls(false); }, 2800);
  }
  const menuOpen = () => !!document.querySelector('.player .menu');
  useEffect(() => { if (!playing) setShowControls(true); else poke(); }, [playing]);

  function togglePlay() {
    const v = vid.current;
    if (v.paused) { v.play().catch((e) => setError(e.message)); setStarted(true); } else v.pause();
  }
  function seekBy(s) {
    const v = vid.current;
    v.currentTime = Math.max(0, Math.min((v.duration || dur) - 0.1, v.currentTime + s));
  }
  function flash(side, text) {
    setHint({ side, text, k: Date.now() });
    setTimeout(() => setHint((h) => (h && Date.now() - h.k >= 550 ? null : h)), 600);
  }

  // Touch: single tap toggles controls; double tap on the left/right third seeks ±10 s.
  function onSurfacePointerUp(e) {
    if (e.target.closest('.controls, .menu, .resume-chip, .big-play')) return;
    if (e.pointerType === 'mouse') { togglePlay(); poke(); return; }
    const rect = box.current.getBoundingClientRect();
    const x = (e.clientX - rect.left) / rect.width;
    const side = x < 0.35 ? 'l' : x > 0.65 ? 'r' : 'c';
    const now = Date.now();
    const st = tap.current;
    if (now - st.t < 300 && side === st.side && side !== 'c') {
      clearTimeout(st.timer);
      seekBy(side === 'l' ? -10 : 10);
      flash(side, side === 'l' ? '−10 s' : '+10 s');
      st.t = now; // allow triple-tap to keep seeking
      return;
    }
    st.t = now; st.side = side;
    clearTimeout(st.timer);
    st.timer = setTimeout(() => {
      if (!vid.current) return;
      if (showControlsRef.current && !vid.current.paused) setShowControls(false);
      else poke();
    }, 280);
  }
  const showControlsRef = useRef(showControls);
  showControlsRef.current = showControls;

  // ---- fullscreen -------------------------------------------------------------------------
  const canContainerFs = !IS_IPHONE && !!(document.fullscreenEnabled || document.webkitFullscreenEnabled);
  function toggleFs() {
    const el = box.current;
    const v = vid.current;
    if (canContainerFs) {
      const cur = document.fullscreenElement || document.webkitFullscreenElement;
      if (cur) (document.exitFullscreen || document.webkitExitFullscreen).call(document);
      else (el.requestFullscreen || el.webkitRequestFullscreen).call(el).catch?.(() => {});
    } else if (v.webkitEnterFullscreen) {
      v.webkitEnterFullscreen(); // iPhone: native player; native tracks take over
    }
  }
  useEffect(() => {
    const on = () => setFs(!!(document.fullscreenElement || document.webkitFullscreenElement));
    document.addEventListener('fullscreenchange', on);
    document.addEventListener('webkitfullscreenchange', on);
    return () => {
      document.removeEventListener('fullscreenchange', on);
      document.removeEventListener('webkitfullscreenchange', on);
    };
  }, []);

  // ---- keyboard ---------------------------------------------------------------------------
  useEffect(() => {
    const onKey = (e) => {
      if (e.target.closest?.('input, textarea, select, [contenteditable]') || e.metaKey || e.ctrlKey || e.altKey) return;
      if (store.get().dialog) return;
      const v = vid.current;
      const k = e.key;
      let handled = true;
      if (k === ' ' || k === 'k') togglePlay();
      else if (k === 'j') { seekBy(-10); flash('l', '−10 s'); }
      else if (k === 'l') { seekBy(10); flash('r', '+10 s'); }
      else if (k === 'ArrowLeft') { seekBy(-5); flash('l', '−5 s'); }
      else if (k === 'ArrowRight') { seekBy(5); flash('r', '+5 s'); }
      else if (k === 'f') toggleFs();
      else if (k === 'm') { v.muted = !v.muted; setMuted(v.muted); }
      else if (k === 'c') cycleSubs();
      else if (k === '>' || k === '<') {
        const i = SPEEDS.indexOf(v.playbackRate);
        const ni = Math.max(0, Math.min(SPEEDS.length - 1, (i < 0 ? 2 : i) + (k === '>' ? 1 : -1)));
        setSpeed(SPEEDS[ni]); flash('c', SPEEDS[ni] + '×');
      } else if (k === 'N' && onNext) onNext();
      else if (/^[0-9]$/.test(k)) v.currentTime = (parseInt(k, 10) / 10) * (v.duration || dur);
      else handled = false;
      if (handled) { e.preventDefault(); poke(); }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  });

  // ---- settings -----------------------------------------------------------------------------
  function setSpeed(s) { vid.current.playbackRate = s; setP(setPref('speed', s)); }
  function chooseTrack(s) { setTrack(s); setP(setPref('subLang', s ? s.lang : 'off')); setMenu(null); }
  function cycleSubs() {
    if (!subs.length) return;
    const i = subs.findIndex((s) => trackKey(s) === trackKey(track));
    const next = i + 1 < subs.length ? subs[i + 1] : null;
    chooseTrack(next);
    flash('c', next ? tr('player.subtitlesOn', { lang: langName(next.lang) }) : tr('player.subtitlesOff'));
  }

  // ---- seek bar -----------------------------------------------------------------------------
  const seekRef = useRef(null);
  function fracAt(e) {
    const r = seekRef.current.getBoundingClientRect();
    return Math.max(0, Math.min(1, (e.clientX - r.left) / r.width));
  }
  function onSeekDown(e) {
    e.preventDefault();
    seekRef.current.setPointerCapture(e.pointerId);
    setScrub({ frac: fracAt(e), drag: true });
  }
  function onSeekMove(e) {
    if (scrub?.drag || e.pointerType === 'mouse') setScrub({ frac: fracAt(e), drag: !!scrub?.drag });
    poke();
  }
  function onSeekUp(e) {
    if (!scrub?.drag) return;
    const f = fracAt(e);
    vid.current.currentTime = f * (vid.current.duration || dur);
    setScrub(null);
  }
  const total = dur || 1;
  const shownT = scrub?.drag ? scrub.frac * total : t;
  const chapterAt = (s) => [...chapters].reverse().find((c) => c.start <= s);

  const subStyle = { '--sub-size': SUB_SIZES[p.subSize] || SUB_SIZES.m, '--sub-bg': p.subBg };

  return html`
    <div ref=${box} class="player ${showControls ? 'controls-on' : ''}" style=${subStyle}
      onPointerUp=${onSurfacePointerUp} onPointerMove=${(e) => e.pointerType === 'mouse' && poke()}
      onDblClick=${(e) => !e.target.closest('.controls') && toggleFs()}>
      <div class="video-slot" ref=${slot}></div>

      ${lines.length > 0 && html`<div class="subs-overlay" aria-live="off">${lines.map((l) => html`<span>${l}</span>`)}</div>`}
      ${hint && html`<div class="tap-hint ${hint.side}" key=${hint.k}
        style=${hint.side === 'c' ? { left: '50%', transform: 'translate(-50%,-50%)' } : null}>${hint.text}</div>`}
      ${!started && !error && html`<button class="big-play" aria-label=${tr('player.play')} onClick=${togglePlay}><${Icon} name="play" /></button>`}
      ${error && html`<div class="notcached" style="position:absolute;inset:0;border-radius:0"><div class="over"><p>${error}</p></div></div>`}
      ${resumeFrom !== null && html`<div class="resume-chip">${tr('player.resumedAt', { time: fmtDuration(resumeFrom) })}
        <button class="small" onClick=${() => { vid.current.currentTime = 0; setResumeFrom(null); }}>${tr('player.startOver')}</button></div>`}

      <div class="controls" onPointerUp=${(e) => e.stopPropagation()}>
        <div class="seek" ref=${seekRef} onPointerDown=${onSeekDown} onPointerMove=${onSeekMove} onPointerUp=${onSeekUp}
          onPointerLeave=${() => !scrub?.drag && setScrub(null)} role="slider" aria-label=${tr('player.seek')}
          aria-valuemin="0" aria-valuemax=${Math.round(total)} aria-valuenow=${Math.round(t)}>
          <div class="track">
            <div class="buf" style=${{ width: (buffered / total) * 100 + '%' }}></div>
            <div class="done" style=${{ width: (shownT / total) * 100 + '%' }}></div>
            ${chapters.filter((c) => c.start > 0).map((c) => html`<span class="mark" style=${{ left: (c.start / total) * 100 + '%' }}></span>`)}
          </div>
          <div class="knob" style=${{ left: (shownT / total) * 100 + '%' }}></div>
          ${scrub && html`<div class="hover-tip" style=${{ left: scrub.frac * 100 + '%' }}>
            ${fmtDuration(scrub.frac * total)}${chapterAt(scrub.frac * total) ? ' · ' + chapterAt(scrub.frac * total).title : ''}</div>`}
        </div>
        <div class="crow">
          <button onClick=${togglePlay} aria-label=${playing ? tr('player.pause') : tr('player.play')}><${Icon} name=${playing ? 'pause' : 'play'} /></button>
          <button onClick=${() => { seekBy(-10); flash('l', '−10 s'); }} aria-label=${tr('player.back10')}><${Icon} name="back10" /></button>
          <button onClick=${() => { seekBy(10); flash('r', '+10 s'); }} aria-label=${tr('player.forward10')}><${Icon} name="fwd10" /></button>
          ${onNext && html`<button onClick=${onNext} aria-label=${tr('player.next')}><${Icon} name="next" /></button>`}
          <span class="time">${fmtDuration(shownT)} / ${fmtDuration(total)}</span>
          <span class="grow"></span>
          ${!IS_IPHONE && html`<button onClick=${() => { const v = vid.current; v.muted = !v.muted; }} aria-label=${muted ? tr('player.unmute') : tr('player.mute')}>
            <${Icon} name=${muted ? 'mute' : 'volume'} /></button>`}
          ${p.speed !== 1 && html`<button class="txt" onClick=${() => setMenu(menu === 'settings' ? null : 'settings')}>${p.speed}×</button>`}
          ${subs.length > 0 && html`<button onClick=${() => setMenu(menu === 'subs' ? null : 'subs')} aria-label=${tr('player.subtitles')}
            class=${track ? 'active' : ''} aria-pressed=${!!track}><${Icon} name="cc" /></button>`}
          <button onClick=${() => setMenu(menu === 'settings' ? null : 'settings')} aria-label=${tr('player.settings')}><${Icon} name="gear" /></button>
          <button onClick=${toggleFs} aria-label=${tr('player.fullscreen')}><${Icon} name=${fs ? 'exitfull' : 'full'} /></button>
        </div>
      </div>

      ${menu === 'subs' && html`<div class="menu" onPointerUp=${(e) => e.stopPropagation()}>
        <h4>${tr('player.subtitles')}</h4>
        <button class=${!track ? 'on' : ''} onClick=${() => chooseTrack(null)}>${tr('player.off')}</button>
        ${subs.map((s) => html`<button class=${trackKey(s) === trackKey(track) ? 'on' : ''} onClick=${() => chooseTrack(s)}>
          ${s.kind === 'auto' ? tr('player.autoLong', { lang: langName(s.lang) }) : langName(s.lang)}</button>`)}
        <h4>${tr('player.size')}</h4>
        <div class="row">${Object.keys(SUB_SIZES).map((k) => html`<button class=${p.subSize === k ? 'on' : ''}
          onClick=${() => setP(setPref('subSize', k))}>${k.toUpperCase()}</button>`)}</div>
        <h4>${tr('player.background')}</h4>
        <div class="row">${SUB_BGS.map(([v, l]) => html`<button class=${p.subBg === v ? 'on' : ''}
          onClick=${() => setP(setPref('subBg', v))}>${tr('player.' + l)}</button>`)}</div>
      </div>`}
      ${menu === 'settings' && html`<div class="menu" onPointerUp=${(e) => e.stopPropagation()}>
        <h4>${tr('player.speed')}</h4>
        ${SPEEDS.map((s) => html`<button class=${p.speed === s ? 'on' : ''} onClick=${() => { setSpeed(s); setMenu(null); }}>${s === 1 ? tr('player.normal') : s + '×'}</button>`)}
      </div>`}
    </div>`;
}
