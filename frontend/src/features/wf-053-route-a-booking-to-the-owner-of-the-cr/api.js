/**
 * Ownership routing API calls (WF-053).
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a
 * method to it is precisely the conflict the feature host exists to remove. The
 * host's `apiRequest` is the escape hatch that makes that unnecessary.
 *
 * `PREFIX` is the feature's own: the backend module mounts these under
 * `/api/wf-053`, and `apiRequest` already prepends `/api`.
 *
 * Every picker on the page is rendered from the `/vocabulary` endpoint rather than
 * from a list compiled into this file, so a team that adds a link type or a node
 * name ships a record instead of a change here.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-053'

function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value))
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const ownershipApi = {
  /** The researched vocabulary: link types, CRM objects, Edge payloads, guardrail. */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),

  /**
   * What the research leaves open, what this build chose, and how to change it.
   *
   * The research for this workflow is precise about the link type, the required
   * guestEmail and the forbidden nodes, and silent about the resolution order, the
   * slot arithmetic, and what a second schedule call does. Those gaps are product
   * behaviour rather than comments, so they are served as data a reviewer can
   * disagree with by name.
   */
  inferences: () => apiRequest(`${PREFIX}/inferences`),

  /** The CRM records, reps and teams the resolution reads. */
  catalog: () => apiRequest(`${PREFIX}/catalog`),

  /** Counts for the page header, over exactly the rows the filters return. */
  summary: (params = {}) => apiRequest(`${PREFIX}/summary${query(params)}`),

  /** Which nodes an Ownership path refuses, checked without refusing. */
  guardrail: (params = {}) => apiRequest(`${PREFIX}/nodes/guardrail${query(params)}`),

  links: (params = {}) => apiRequest(`${PREFIX}/links${query(params)}`),
  createLink: (payload) =>
    apiRequest(`${PREFIX}/links`, { method: 'POST', body: JSON.stringify(payload) }),
  updateLink: (id, payload) =>
    apiRequest(`${PREFIX}/links/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteLink: (id) => apiRequest(`${PREFIX}/links/${id}`, { method: 'DELETE' }),

  /** The Lead / Contact / Account rows an Ownership link resolves against. */
  records: (params = {}) => apiRequest(`${PREFIX}/records${query(params)}`),

  /** Every rep with a connected calendar, which is what availability needs. */
  reps: (params = {}) => apiRequest(`${PREFIX}/reps${query(params)}`),

  /**
   * Who would this guest reach? Writes nothing.
   *
   * The read-only half of `initSimple`: same resolution, same chain, same calendar
   * arithmetic, no routing session and no decision record.
   */
  check: (roomId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/check`, { method: 'POST', body: JSON.stringify(payload) }),

  /** The researched init call: resolve the owner, read their calendar, offer slots. */
  initSimple: (roomId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/init-simple`, { method: 'POST', body: JSON.stringify(payload) }),

  /** The researched schedule call: book one of the offered slots. */
  scheduleSimple: (roomId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/schedule-simple`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  routes: (params = {}) => apiRequest(`${PREFIX}/routes${query(params)}`),
  route: (routeId) => apiRequest(`${PREFIX}/routes/${routeId}`),

  roomBookings: (roomId, params = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/bookings${query(params)}`),
  booking: (bookingId) => apiRequest(`${PREFIX}/bookings/${bookingId}`),
  cancelBooking: (roomId, bookingId) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/bookings/${bookingId}/cancel`, { method: 'POST' }),

  /** Every routing decision, newest first. */
  decisions: (params = {}) => apiRequest(`${PREFIX}/decisions${query(params)}`),
}
