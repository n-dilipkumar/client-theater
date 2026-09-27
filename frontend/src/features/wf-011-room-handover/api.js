/**
 * Publishing API calls (WF-011).
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a
 * method to it is precisely the collision the plugin host exists to remove. The
 * branch added an `api.publishing` object to `lib/api.js`; the port moves it
 * here and points it at `apiRequest`, the host's generic escape hatch, which
 * puts the HTTP status on the thrown error so the share dialog can tell "this
 * room is a draft, so it has no link" (409) from "the call failed".
 *
 * `PREFIX` is the one place the backend's prefix is written down on this side.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/publishing'

/**
 * Build a query string, dropping empty values so a whole form can be passed.
 *
 * Booleans are kept, `false` included: the server distinguishes
 * `?include_archived=false` from an absent flag.
 */
function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const publishingApi = {
  /** The board: every room with its status badge. */
  board: (params = {}) => apiRequest(`${PREFIX}/rooms${query(params)}`),

  /** Everything the share pop-up shows for one room. */
  share: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}`),

  /**
   * The audited change and the transition event in one call, so a subscriber
   * cannot learn about the publish later than the audit log says it happened.
   */
  setStatus: (roomId, body) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/status`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  /**
   * The link the seller copies. A GET on purpose: re-copying a link must not
   * write an audit row, and a 409 here means the room is not public yet.
   */
  shareLink: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/share-link`),

  setAccess: (roomId, body) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/access`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),

  events: (params = {}) => apiRequest(`${PREFIX}/events${query(params)}`),

  webhooks: (params = {}) => apiRequest(`${PREFIX}/webhooks${query(params)}`),
  subscribe: (body) =>
    apiRequest(`${PREFIX}/webhooks`, { method: 'POST', body: JSON.stringify(body) }),
  cancelSubscription: (id) => apiRequest(`${PREFIX}/webhooks/${id}`, { method: 'DELETE' }),
  deliveries: (id) => apiRequest(`${PREFIX}/webhooks/${id}/deliveries`),
}
