// "Cache this creator…": subscription rules with a live preview against recent uploads.
import { html, useEffect, useMemo, useRef, useState } from '../../vendor/preact-htm.js';
import { api, patch, post } from '../api.js';
import { t } from '../i18n.js';
import { toast, toastError } from '../store.js';
import { debounce, durationInputValue, fmtDuration, parseDurationInput, reasonText } from '../util.js';
import { Dialog } from './Dialog.js';

const HEIGHTS = ['1080', '720', '480', '360'];

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
  if (Number.isNaN(min)) errors.min_duration_s = t('filter.minFormat');
  if (Number.isNaN(max)) errors.max_duration_s = t('filter.maxFormat');
  const backfill = parseInt(f.backfill_count || '0', 10);
  if (Number.isNaN(backfill) || backfill < 0 || backfill > 500) errors.backfill_count = t('filter.backfillRange');
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
      ? t('filter.backfillMore', { count: rules.backfill_count - oldBackfill })
      : t('filter.backfillLess');

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
      toast(!editing ? t('filter.subscribed', { channel: channel.title || t('common.thisChannel') })
        : backfillChanged ? t('filter.updatedBackfill') : t('filter.updated'));
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

  return html`<${Dialog} title=${editing ? t('filter.titleEdit', { channel: channel.title || channel.id }) : t('filter.titleNew', { channel: channel.title || t('common.thisCreator') })}
    onClose=${onClose}
    footer=${html`<span class="grow small muted">${preview ? t('filter.wouldCache', { matched: preview.matched, total: preview.total }) : ''}</span>
      <button onClick=${onClose}>${t('common.cancel')}</button>
      <button class="primary" disabled=${saving || Object.keys(allErrors).length} onClick=${save}>${editing ? t('filter.save') : t('filter.subscribe')}</button>`}>
    <div class="stack">
      <p class="small muted">${t('filter.intro')}</p>
      <div class="grid2">
        <label class="field"><span>${t('filter.include')}</span>
          <input type="text" class="mono ${allErrors.include_regex ? 'invalid' : ''}" placeholder=${t('filter.includeHint')}
            value=${f.include_regex} onInput=${set('include_regex')} autocapitalize="off" spellcheck="false" />
          ${fieldErr('include_regex')}</label>
        <label class="field"><span>${t('filter.exclude')}</span>
          <input type="text" class="mono ${allErrors.exclude_regex ? 'invalid' : ''}" placeholder=${t('filter.excludeHint')}
            value=${f.exclude_regex} onInput=${set('exclude_regex')} autocapitalize="off" spellcheck="false" />
          ${fieldErr('exclude_regex')}</label>
        <label class="field"><span>${t('filter.shortest')}</span>
          <input type="text" placeholder=${t('filter.noBound')} value=${f.min} onInput=${set('min')} class=${allErrors.min_duration_s ? 'invalid' : ''} />
          ${fieldErr('min_duration_s')}</label>
        <label class="field"><span>${t('filter.longest')}</span>
          <input type="text" placeholder=${t('filter.noBound')} value=${f.max} onInput=${set('max')} class=${allErrors.max_duration_s ? 'invalid' : ''} />
          ${fieldErr('max_duration_s')}</label>
        <label class="field"><span>${editing ? t('filter.backfillEdit') : t('filter.backfillNew')}</span>
          <input type="number" min="0" max="500" value=${f.backfill_count} onInput=${set('backfill_count')} class=${allErrors.backfill_count ? 'invalid' : ''} />
          ${fieldErr('backfill_count') || (backfillChange && html`<div class="tiny faint">${backfillChange}</div>`)}</label>
        <label class="field"><span>${t('filter.quality')}</span>
          <select value=${f.max_height} onChange=${set('max_height')}>
            <option value="">${t('filter.qualityDefault')}</option>
            ${HEIGHTS.map((v) => html`<option value=${v}>${v}p</option>`)}</select></label>
      </div>
      <div class="row wrap">
        <label class="check"><input type="checkbox" checked=${f.case_sensitive} onChange=${set('case_sensitive')} /> ${t('filter.caseSensitive')}</label>
        <label class="check"><input type="checkbox" checked=${f.include_shorts} onChange=${set('include_shorts')} /> ${t('filter.shorts')}</label>
        <label class="check"><input type="checkbox" checked=${f.include_streams} onChange=${set('include_streams')} /> ${t('filter.streams')}</label>
      </div>
      <div>
        <div class="section-head"><h3>${t('filter.previewHead')}</h3>
          <span class="small muted">${loading ? t('filter.checking') : preview ? t('filter.matchCount', { matched: preview.matched, total: preview.total }) : ''}</span></div>
        ${previewErr && html`<div class="error-box small">${t('filter.previewError', { error: previewErr })}</div>`}
        ${preview && html`<ul class="preview-list">
          ${preview.items.map((i) => html`<li class=${i.match ? '' : 'no-match'}>
            <span class="mark ${i.match ? 'yes' : 'no'}">${i.match ? '✓' : '✗'}</span>
            <span class="grow ttl">${i.title}${i.is_short ? html` <span class="badge">${t('badge.short')}</span>` : ''}</span>
            <span class="tiny faint nowrap">${i.match ? (i.duration ? fmtDuration(i.duration) : '') : reasonText(i.reason)}</span>
          </li>`)}
        </ul>
        <p class="tiny faint">${t('filter.previewNote')}</p>`}
      </div>
    </div>
  <//>`;
}
