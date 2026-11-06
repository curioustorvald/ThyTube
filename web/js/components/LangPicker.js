// Language picker: a native <select> laid invisibly over an icon, so phones show their own picker.
import { html } from '../../vendor/preact-htm.js';
import { currentLang, LANGUAGES, setLanguage, t } from '../i18n.js';
import { Icon } from '../icons.js';

export function LangPicker({ showName = false }) {
  const cur = currentLang();
  if (Object.keys(LANGUAGES).length < 2) return null;
  return html`<label class="lang-pick ${showName ? 'named' : ''}" title=${t('lang.choose')}>
    <${Icon} name="globe" />${showName && html`<span>${LANGUAGES[cur]}</span>`}
    <select aria-label=${t('lang.choose')} value=${cur} onChange=${(e) => setLanguage(e.target.value)}>
      ${Object.entries(LANGUAGES).map(([code, name]) => html`<option value=${code} lang=${code}>${name}</option>`)}
    </select></label>`;
}
