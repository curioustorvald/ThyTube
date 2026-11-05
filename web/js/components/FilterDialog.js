// "Cache this creator…": subscription rules with a live preview against recent uploads.
import { html, useEffect, useMemo, useRef, useState } from '../../vendor/preact-htm.js';
import { api, patch, post } from '../api.js';
import { toast, toastError } from '../store.js';
import { debounce, durationInputValue, fmtDuration, parseDurationInput } from '../util.js';
import { Dialog } from './Dialog.js';

const HEIGHTS = [['', 'Default'], ['1080', '1080p'], ['720', '720p'], ['480', '480p'], ['360', '360p']];

function initial(sub) {
  return {
    include_regex: sub?.include_regex || '',
    exclude_regex: sub?.exclude_regex || '',
    case_sensitive: !!sub?.case_sensitive,
    min: durationInputValue(sub?.min_duration_s),
    max: durationInputValue(sub?.max_duration_s),
    include_shorts: !!sub?.include_shorts,
    include_streams: !!sub?.include_streams,
    backfill_count: String(sub?.backfill_count ?? 10),
    max_height: sub?.max_height ? String(sub.max_height) : '',
  };
}

function toRules(f) {
  const errors = {};
  const min = parseDurationInput(f.min);
  const max = parseDurationInput(f.max);
  if (Number.isNaN(min)) errors.min_duration_s = 'Use e.g. 2:30, 90s or 10m';
  if (Number.isNaN(max)) errors.max_duration_s = 'Use e.g. 1:00:00 or 45m';
  const backfill = parseInt(f.backfill_count || '0', 10);
  if (Number.isNaN(backfill) || backfill < 0 || backfill > 500) errors.backfill_count = '0 to 500';
  return {
    errors,
    rules: {
      include_regex: f.include_regex.trim() || null,
      exclude_regex: f.exclude_regex.trim() || null,
      case_sensitive: f.case_sensitive,
      min_duration_s: Number.isNaN(min) ? null : min,
      max_duration_s: Number.isNaN(max) ? null : max,
      include_shorts: f.include_shorts,
      include_streams: f.include_streams,
      backfill_count: Number.isNaN(backfill) ? 10 : backfill,
      max_height: f.max_height ? parseInt(f.max_height, 10) : null,
    },
  };
}

export function FilterDialog({ channel, subscription, onClose, onSaved }) {
  const [f, setF] = useState(() => initial(subscription));
  const [preview, setPreview] = useState(null);
  const [previewErr, setPreviewErr] = useState(null);
  const [serverErr, setServerErr] = useState({});
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const abort = useRef(null);
  const editing = !!subscription;
  const { rules, errors } = toRules(f);
  const allErrors = { ...errors, ...serverErr };
  // Changing the backfill of an existing subscription re-runs it in the worker.
  const oldBackfill = subscription?.backfill_count ?? 0;
  const backfillChanged = editing && subscription.backfill_done && !errors.backfill_count && rules.backfill_count !== oldBackfill;
  const backfillChange = !backfillChanged ? null
    : rules.backfill_count > oldBackfill
      ? `On saving, up to ${rules.backfill_count - oldBackfill} more of the channel's older videos will be queued.`
      : 'On saving, older videos beyond this number come off the queue; those already cached stay until evicted.';

  const runPreview = useMemo(() => debounce(async (rules) => {
    abort.current?.abort();
    const ctl = new AbortController();
    abort.current = ctl;
    setLoading(true);
    try {
      const r = await api('/subscriptions/preview', { method: 'POST', body: { channel_id: channel.id, ...rules }, signal: ctl.signal });
      setPreview(r); setPreviewErr(null); setServerErr({});
    } catch (e) {
      if (e.name === 'AbortError') return;
      if (e.field) setServerErr({ [e.field]: e.message });
      else setPreviewErr(e.message);
    } finally {
      if (abort.current === ctl) setLoading(false);
    }
  }, 600), [channel.id]);

  useEffect(() => {
    if (Object.keys(errors).length) return;
    runPreview(rules);
  }, [JSON.stringify(rules)]);
  useEffect(() => () => { runPreview.cancel(); abort.current?.abort(); }, []);

  const set = (k) => (e) => {
    const v = e.target.type === 'checkbox' ? e.target.checked : e.target.value;
    setF((old) => ({ ...old, [k]: v }));
    if (serverErr[k] || serverErr[k + '_regex']) setServerErr({});
  };

  async function save() {
    if (Object.keys(errors).length) return;
    setSaving(true);
    try {
      const saved = editing
        ? await patch('/subscriptions/' + subscription.id, rules)
        : await post('/subscriptions', { channel, ...rules });
      toast(!editing ? `Subscribed to ${channel.title || 'channel'} — backfill queued`
        : backfillChanged ? 'Subscription updated — re-running the backfill' : 'Subscription updated');
      onSaved?.(saved);
      onClose();
    } catch (e) {
      if (e.field) setServerErr({ [e.field]: e.message });
      else toastError(e);
    } finally {
      setSaving(false);
    }
  }

  const fieldErr = (k) => allErrors[k] && html`<div class="field-error">${allErrors[k]}</div>`;

  return html`<${Dialog} title=${editing ? `Rules for ${channel.title || channel.id}` : `Cache ${channel.title || 'this creator'}`}
    onClose=${onClose}
    footer=${html`<span class="grow small muted">${preview ? `${preview.matched} of ${preview.total} recent uploads would be cached` : ''}</span>
      <button onClick=${onClose}>Cancel</button>
      <button class="primary" disabled=${saving || Object.keys(allErrors).length} onClick=${save}>${editing ? 'Save' : 'Subscribe'}</button>`}>
    <div class="stack">
      <p class="small muted">A subscription is a filter: new uploads that pass it are downloaded automatically. Patterns are Python regular expressions matched anywhere in the title.</p>
      <div class="grid2">
        <label class="field"><span>Title must match</span>
          <input type="text" class="mono ${allErrors.include_regex ? 'invalid' : ''}" placeholder="e.g. sourdough|rye"
            value=${f.include_regex} onInput=${set('include_regex')} autocapitalize="off" spellcheck="false" />
          ${fieldErr('include_regex')}</label>
        <label class="field"><span>Title must not match</span>
          <input type="text" class="mono ${allErrors.exclude_regex ? 'invalid' : ''}" placeholder="e.g. #shorts|trailer"
            value=${f.exclude_regex} onInput=${set('exclude_regex')} autocapitalize="off" spellcheck="false" />
          ${fieldErr('exclude_regex')}</label>
        <label class="field"><span>Shortest</span>
          <input type="text" placeholder="no minimum" value=${f.min} onInput=${set('min')} class=${allErrors.min_duration_s ? 'invalid' : ''} />
          ${fieldErr('min_duration_s')}</label>
        <label class="field"><span>Longest</span>
          <input type="text" placeholder="no maximum" value=${f.max} onInput=${set('max')} class=${allErrors.max_duration_s ? 'invalid' : ''} />
          ${fieldErr('max_duration_s')}</label>
        <label class="field"><span>${editing ? 'Older videos to fetch' : 'Also fetch this many existing videos'}</span>
          <input type="number" min="0" max="500" value=${f.backfill_count} onInput=${set('backfill_count')} class=${allErrors.backfill_count ? 'invalid' : ''} />
          ${fieldErr('backfill_count') || (backfillChange && html`<div class="tiny faint">${backfillChange}</div>`)}</label>
        <label class="field"><span>Quality cap</span>
          <select value=${f.max_height} onChange=${set('max_height')}>
            ${HEIGHTS.map(([v, l]) => html`<option value=${v}>${l}</option>`)}</select></label>
      </div>
      <div class="row wrap">
        <label class="check"><input type="checkbox" checked=${f.case_sensitive} onChange=${set('case_sensitive')} /> Case-sensitive</label>
        <label class="check"><input type="checkbox" checked=${f.include_shorts} onChange=${set('include_shorts')} /> Include Shorts</label>
        <label class="check"><input type="checkbox" checked=${f.include_streams} onChange=${set('include_streams')} /> Include past live streams</label>
      </div>
      <div>
        <div class="section-head"><h3>Preview: recent uploads</h3>
          <span class="small muted">${loading ? 'Checking…' : preview ? `${preview.matched}/${preview.total} match` : ''}</span></div>
        ${previewErr && html`<div class="error-box small">Couldn't load the channel's uploads: ${previewErr}</div>`}
        ${preview && html`<ul class="preview-list">
          ${preview.items.map((i) => html`<li class=${i.match ? '' : 'no-match'}>
            <span class="mark ${i.match ? 'yes' : 'no'}">${i.match ? '✓' : '✗'}</span>
            <span class="grow ttl">${i.title}${i.is_short ? html` <span class="badge">Short</span>` : ''}</span>
            <span class="tiny faint nowrap">${i.match ? (i.duration ? fmtDuration(i.duration) : '') : i.reason}</span>
          </li>`)}
        </ul>
        <p class="tiny faint">${preview.note}</p>`}
      </div>
    </div>
  <//>`;
}
