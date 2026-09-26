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
    let body = null
    try {
      body = await response.json()
      detail = body.detail || body.error || detail
    } catch {
      // Non-JSON error body; the status line is the best we have.
    }
    // Keep the structured parts: the access policy validator returns a
    // field-keyed `errors` map, and the UI puts each message next to the input
    // that caused it rather than showing one combined string.
    const error = new Error(detail)
    error.status = response.status
    error.code = body?.error
    error.errors = body?.errors || null
    error.body = body
    throw error
  }

  if (response.status === 204) return null
  return response.json()
}

export const api = {
  health: () => request('/health'),
  stats: () => request('/stats'),

  collections: () => request('/collections'),

  listRecords: (collection, params = {}) =>
    request(`/records/${collection}${query(params)}`),
  getRecord: (collection, id) => request(`/records/${collection}/${id}`),
  createRecord: (collection, payload, params = {}) =>
    request(`/records/${collection}${query(params)}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  updateRecord: (collection, id, payload) =>
    request(`/records/${collection}/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteRecord: (collection, id) => request(`/records/${collection}/${id}`, { method: 'DELETE' }),
  restoreRecord: (collection, id) => request(`/records/${collection}/${id}/restore`, { method: 'POST' }),

  audit: (params = {}) => request(`/audit${query(params)}`),

  // -- WF-015: buyer identity and email-domain restriction ----------------- //
  // The access policy is a record like any other, so it is also reachable
  // through listRecords/getRecord above. These are the behavioural calls: the
  // ones that resolve a tier, run the gate, or verify a token.

  roomAccess: (roomId) => request(`/rooms/${roomId}/access`),
  setRoomAccess: (roomId, policy) =>
    request(`/rooms/${roomId}/access`, { method: 'PUT', body: JSON.stringify(policy) }),
  clearRoomAccess: (roomId) => request(`/rooms/${roomId}/access`, { method: 'DELETE' }),

  templateAccess: (templateId) => request(`/templates/${templateId}/access`),
  setTemplateAccess: (templateId, policy) =>
    request(`/templates/${templateId}/access`, { method: 'PUT', body: JSON.stringify(policy) }),

  accessRequirements: (roomId) => request(`/rooms/${roomId}/access/requirements`),
  submitAccess: (roomId, payload) =>
    request(`/rooms/${roomId}/access/sessions`, { method: 'POST', body: JSON.stringify(payload) }),
  verifyAccess: (roomId, token) => request(`/rooms/${roomId}/access/verify?token=${encodeURIComponent(token)}`),
  checkAccess: (roomId, token) =>
    request(`/rooms/${roomId}/access/session${token ? `?token=${encodeURIComponent(token)}` : ''}`),
  accessSessions: (roomId, params = {}) =>
    request(`/rooms/${roomId}/access/sessions${query(params)}`),
  accessOutbox: (roomId) => request(`/rooms/${roomId}/access/outbox`),
}

/** Build a query string from a params object, skipping empty values. */
function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const text = search.toString()
  return text ? `?${text}` : ''
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
