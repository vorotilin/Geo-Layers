// Клиент публичного API /v1. Токен — в sessionStorage вкладки (ADR-0010, KI-4).

const TOKEN_KEY = 'apc-layers-token'

export function getToken() {
  try { return sessionStorage.getItem(TOKEN_KEY) || '' } catch { return '' }
}

export function setToken(token) {
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token)
    else sessionStorage.removeItem(TOKEN_KEY)
  } catch { /* приватный режим — токен живёт до перезагрузки */ }
}

export class ApiError extends Error {
  constructor(status, body) {
    super(body?.message || `HTTP ${status}`)
    this.status = status
    this.body = body
  }
}

export async function api(method, path, { json, form, token = getToken() } = {}) {
  const headers = { Authorization: `Bearer ${token}` }
  let body
  if (json !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(json)
  } else if (form) {
    body = form
  }
  const resp = await fetch(path, { method, headers, body })
  const data = (resp.headers.get('content-type') || '').includes('json') ? await resp.json() : null
  if (!resp.ok) throw new ApiError(resp.status, data)
  return data
}

export const get = (path, opts) => api('GET', path, opts)
export const post = (path, json, opts) => api('POST', path, { ...opts, json })

export async function waitJob(statusUrl, onUpdate, intervalMs = 700) {
  const path = new URL(statusUrl, location.href).pathname
  for (;;) {
    const job = await get(path)
    onUpdate?.(job)
    if (['succeeded', 'failed', 'cancelled'].includes(job.status)) return job
    await new Promise((r) => setTimeout(r, intervalMs))
  }
}

export function formatValue(v) {
  if (v === null || v === undefined) return '—'
  return Number.isInteger(v) ? v.toLocaleString('ru') : v.toLocaleString('ru', { maximumSignificantDigits: 6 })
}
