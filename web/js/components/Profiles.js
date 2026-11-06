import { html, useEffect, useState } from '../../vendor/preact-htm.js';
import { get, post, patch, del } from '../api.js';
import { setProfile, store, toastError, useStore } from '../store.js';
import { t } from '../i18n.js';
import { LangPicker } from './LangPicker.js';
import { initials } from '../util.js';

export const COLOURS = ['#2a7f74', '#3b6ea8', '#8a5cc2', '#c2569b', '#d0663a', '#b7891f', '#4f8a3a', '#5d6870'];

export function Avatar({ p, size }) {
  return html`<span class="avatar" style=${{ background: p.colour, width: size, height: size }}>${initials(p.name)}</span>`;
}

export function ProfileScreen() {
  const current = useStore((s) => s.profile);
  const [items, setItems] = useState(null);
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState('');
  const [colour, setColour] = useState(COLOURS[0]);

  const load = () => get('/profiles').then((r) => {
    setItems(r.items);
    if (!r.items.length) setAdding(true);
    if (current && !r.items.some((p) => p.id === current.id)) setProfile(null);
  }).catch(toastError);
  useEffect(() => { load(); }, []);

  async function add(e) {
    e.preventDefault();
    try {
      const p = await post('/profiles', { name, colour });
      setName(''); setAdding(false);
      setProfile(p);
    } catch (err) { toastError(err); }
  }
  async function remove(p) {
    if (!confirm(t('profiles.confirmDelete', { name: p.name }))) return;
    try { await del('/profiles/' + p.id); if (current?.id === p.id) setProfile(null); load(); } catch (e) { toastError(e); }
  }
  async function rename(p) {
    const n = prompt(t('profiles.rename'), p.name);
    if (!n || n === p.name) return;
    try { const u = await patch('/profiles/' + p.id, { name: n }); if (current?.id === p.id) setProfile(u); load(); } catch (e) { toastError(e); }
  }

  return html`<div class="profiles-screen">
    <h1>${t('profiles.who')}</h1>
    ${items === null ? html`<p class="muted">${t('common.loading')}</p>` : html`
      <div class="profiles-grid">
        ${items.map((p) => html`<div class="stack" style="text-align:center">
          <button class="profile-tile" onClick=${() => (editing ? rename(p) : setProfile(p))}>
            <${Avatar} p=${p} /><span>${p.name}</span></button>
          ${editing && html`<button class="small danger" onClick=${() => remove(p)}>${t('profiles.delete')}</button>`}
        </div>`)}
        ${!adding && html`<button class="profile-tile" onClick=${() => setAdding(true)}>
          <span class="avatar" style="background:var(--surface-2);color:var(--muted)">+</span><span>${t('profiles.add')}</span></button>`}
      </div>`}
    ${adding && html`<form class="card stack" style="width:min(100%,380px)" onSubmit=${add}>
      <label class="field"><span>${t('profiles.name')}</span><input type="text" value=${name} maxlength="40" autofocus
        onInput=${(e) => setName(e.target.value)} required /></label>
      <div class="colours">${COLOURS.map((c) => html`<button type="button" class=${c === colour ? 'on' : ''}
        style=${{ background: c }} aria-label=${c} onClick=${() => setColour(c)}></button>`)}</div>
      <div class="row"><button class="primary" type="submit">${t('profiles.create')}</button>
        ${items?.length ? html`<button type="button" onClick=${() => setAdding(false)}>${t('common.cancel')}</button>` : null}</div>
    </form>`}
    <div class="row">
      ${items?.length ? html`<button class="ghost small" onClick=${() => setEditing(!editing)}>${editing ? t('profiles.done') : t('profiles.manage')}</button>` : null}
      ${current && html`<button class="ghost small" onClick=${() => store.set({ pickingProfile: false })}>${t('common.back')}</button>`}
    </div>
    <${LangPicker} showName />
  </div>`;
}
