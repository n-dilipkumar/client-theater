/**
 * Thin client for the Digital Sales Room API.
 *
 * The API is schema-flexible, so nothing here hard-codes a record shape:
 * payloads are passed through as plain objects and responses are returned
 * as-is. That keeps this client valid as teams add fields server-side.
 */

const BASE = '/api'

async function request(path, options = {}) {
  const response = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`
    try {
      const body = await response.json()
      detail = body.detail || body.error || detail
    } catch {
      // Non-JSON error body; the status line is the best we have.
    }
    throw new Error(detail)
  }

  if (response.status === 204) return null
  return response.json()
}

export const api = {
  health: () => request('/health'),
  stats: () => request('/stats'),

  collections: () => request('/collections'),

  listRecords: (collection, params = {}) => {
    const query = new URLSearchParams()
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null && value !== '') query.set(key, value)
    }
    const suffix = query.toString() ? `?${query}` : ''
    return request(`/records/${collection}${suffix}`)
  },
  getRecord: (collection, id) => request(`/records/${collection}/${id}`),
  createRecord: (collection, payload, params = {}) => {
    const query = new URLSearchParams(params).toString()
    return request(`/records/${collection}${query ? `?${query}` : ''}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  },
  updateRecord: (collection, id, payload) =>
    request(`/records/${collection}/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteRecord: (collection, id) => request(`/records/${collection}/${id}`, { method: 'DELETE' }),
  restoreRecord: (collection, id) => request(`/records/${collection}/${id}/restore`, { method: 'POST' }),

  audit: (params = {}) => {
    const query = new URLSearchParams()
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null && value !== '') query.set(key, value)
    }
    const suffix = query.toString() ? `?${query}` : ''
    return request(`/audit${suffix}`)
  },
}

/** Format an ISO timestamp as a compact relative string plus absolute time. */
export function relativeTime(iso) {
  if (!iso) return '—'
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return iso
  const seconds = Math.round((Date.now() - then) / 1000)
  if (seconds < 5) return 'just now'
  if (seconds < 60) return `${seconds}s ago`
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.round(hours / 24)
  if (days < 30) return `${days}d ago`
  return new Date(iso).toLocaleDateString()
}

export function absoluteTime(iso) {
  if (!iso) return '—'
  const parsed = new Date(iso)
  return Number.isNaN(parsed.getTime()) ? iso : parsed.toLocaleString()
}
