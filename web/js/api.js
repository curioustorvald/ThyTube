// fetch wrapper: JSON in/out, X-Profile header, uniform errors.
import { store } from './store.js';

export class ApiError extends Error {
  constructor(status, code, message, extra = {}) {
    super(message);
    this.status = status;
    this.code = code;
    Object.assign(this, extra);
  }
}

export async function api(path, { method = 'GET', body, signal, query } = {}) {
  let url = '/api' + path;
  if (query) {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(query)) {
      if (v !== undefined && v !== null && v !== '') qs.set(k, v);
    }
    const s = qs.toString();
    if (s) url += (url.includes('?') ? '&' : '?') + s;
  }
  const headers = { Accept: 'application/json' };
  const profile = store.get().profile;
  if (profile) headers['X-Profile'] = String(profile.id);
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  let res;
  try {
    res = await fetch(url, { method, headers, body: body !== undefined ? JSON.stringify(body) : undefined, signal });
  } catch (e) {
    if (e.name === 'AbortError') throw e;
    throw new ApiError(0, 'network', 'Cannot reach the ThyTube server. Art thou on thy home network?');
  }
  let data = null;
  const text = await res.text();
  if (text) {
    try { data = JSON.parse(text); } catch { data = null; }
  }
  if (!res.ok) {
    const err = (data && data.error) || {};
    throw new ApiError(res.status, err.code || 'http_' + res.status, err.message || res.statusText || 'Request failed', err);
  }
  return data;
}

export const get = (path, query, opts = {}) => api(path, { ...opts, query });
export const post = (path, body) => api(path, { method: 'POST', body: body ?? {} });
export const patch = (path, body) => api(path, { method: 'PATCH', body });
export const del = (path, query) => api(path, { method: 'DELETE', query });

// For pagehide: sendBeacon can't set headers, so the profile goes in the body.
export function beacon(path, payload) {
  const blob = new Blob([JSON.stringify(payload)], { type: 'text/plain' });
  if (navigator.sendBeacon && navigator.sendBeacon('/api' + path, blob)) return;
  fetch('/api' + path, { method: 'POST', body: blob, keepalive: true }).catch(() => {});
}
