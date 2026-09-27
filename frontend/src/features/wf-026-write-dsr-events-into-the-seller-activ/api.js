/**
 * API calls for the seller activity feed (WF-026).
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a
 * method to it is precisely the conflict the feature host exists to remove.
 * The host's `apiRequest` is the escape hatch that makes that unnecessary.
 *
 * `PREFIX` is the feature's own: the backend module mounts these under
 * `/api/wf-026`, and `apiRequest` already prepends `/api`.
 *
 * Nothing here hard-codes a payload shape. The pickers on the page are rendered
 * from the vocabulary endpoint, the event names and body text are free text, and
 * the JSON:API request body is something the server builds - so a team that
 * configures a new custom event ships a record, not a change to this file.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-026'

function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const feedApi = {
  /** The researched vocabulary: the write, the name grammar, the webhook contract,
   *  and the adjacent surfaces this build deliberately does not implement. */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),

  /** Every design inference this workflow rests on, and how to change each one. */
  inferences: () => apiRequest(`${PREFIX}/inferences`),

  // -- apps: the researched step 1, recorded ------------------------------- //

  listApps: () => apiRequest(`${PREFIX}/apps`),
  registerApp: (payload) =>
    apiRequest(`${PREFIX}/apps`, { method: 'POST', body: JSON.stringify(payload) }),
  updateApp: (id, payload) =>
    apiRequest(`${PREFIX}/apps/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteApp: (id) => apiRequest(`${PREFIX}/apps/${id}`, { method: 'DELETE' }),

  // -- custom events: the researched step 2 -------------------------------- //

  listEventTypes: () => apiRequest(`${PREFIX}/event-types`),
  createEventType: (payload) =>
    apiRequest(`${PREFIX}/event-types`, { method: 'POST', body: JSON.stringify(payload) }),
  updateEventType: (id, payload) =>
    apiRequest(`${PREFIX}/event-types/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteEventType: (id) =>
    apiRequest(`${PREFIX}/event-types/${id}`, { method: 'DELETE' }),

  // -- prospect links: who the feed belongs to ------------------------------ //

  listProspects: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/prospects`),
  linkProspect: (roomId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/prospects`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  unlinkProspect: (roomId, linkId) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/prospects/${linkId}`, { method: 'DELETE' }),

  // -- the automation: the researched step 3 -------------------------------- //

  /** What the seller's activity feed would show for a room, and how it got there. */
  roomFeed: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/feed`),

  /** A dry run: the same ledger, nothing written. */
  preview: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/preview`, { method: 'POST' }),

  /** The researched write. Safe to run repeatedly; a delivered event is not resent. */
  publish: (roomId, payload = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/publish`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  // -- the outbound log ----------------------------------------------------- //

  listDeliveries: (params = {}) => apiRequest(`${PREFIX}/deliveries${query(params)}`),
  retryDelivery: (id) => apiRequest(`${PREFIX}/deliveries/${id}/retry`, { method: 'POST' }),

  // -- the inbound half ----------------------------------------------------- //

  listSignals: (params = {}) => apiRequest(`${PREFIX}/signals${query(params)}`),
  roomSignals: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/signals`),
}
