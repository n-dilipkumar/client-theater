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
    // The status travels with the error so callers can react to a specific
    // refusal rather than string-matching the message: the Share dialog needs
    // to tell "you may not" (403) from "confirm this first" (428).
    const error = new Error(detail)
    error.status = response.status
    error.code = detail
    throw error
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

/**
 * WF-004: the Share dialog and *Who Has Access*.
 *
 * These are workflow routes rather than generic record routes, because the
 * server has to apply rules a client cannot: the delegation rule, the 48-hour
 * acceptance window, and the end-of-day UTC expiry. `actor` is who the console
 * user is acting as; the server resolves their role in the room and refuses
 * anything that role does not permit.
 */
function withQuery(path, params = {}) {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') query.set(key, value)
  }
  return query.toString() ? `${path}?${query}` : path
}

export const accessApi = {
  roles: (actorRole) => request(withQuery('/access/roles', { actor_role: actorRole })),

  /** Members, pending invitations, and the imminent-expiry banner, in one read. */
  snapshot: (roomId, actor) => request(withQuery(`/rooms/${roomId}/access`, { actor })),

  invite: (roomId, { emails, role, access_valid_until }, actor) =>
    request(withQuery(`/rooms/${roomId}/invitations`, { actor }), {
      method: 'POST',
      body: JSON.stringify({ emails, role, access_valid_until }),
    }),

  accept: (invitationId, actor) =>
    request(withQuery(`/invitations/${invitationId}/accept`, { actor }), { method: 'POST' }),

  updateAccess: (accessId, { role, access_valid_until, set_expiry }, { actor, confirm } = {}) =>
    request(withQuery(`/access/${accessId}`, { actor, confirm: confirm || undefined }), {
      method: 'PATCH',
      body: JSON.stringify({ role, access_valid_until, set_expiry }),
    }),

  removeAccess: (accessId, { actor, confirm } = {}) =>
    request(withQuery(`/access/${accessId}`, { actor, confirm: confirm || undefined }), {
      method: 'DELETE',
    }),
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
