/**
 * Round robin booking API calls (WF-054).
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a
 * method to it is precisely the conflict the feature host exists to remove. The
 * host's `apiRequest` is the escape hatch that makes that unnecessary.
 *
 * `PREFIX` is the feature's own: the backend module mounts these under
 * `/api/wf054`, and `apiRequest` already prepends `/api`.
 *
 * Every picker on the page is rendered from the `/vocabulary` endpoint rather than
 * from a list compiled into this file, so a team that adds a mode or a link type
 * ships a record instead of a change here.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf054'

function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value))
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const roundRobinApi = {
  /** The researched vocabulary: link types, modes, license gate, credit ledger. */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),

  /**
   * What the research leaves open, what this build chose, and how to change it.
   *
   * The research for WF-054 is precise about the two modes, the license gate and
   * the credit ledger's two directions. It is silent about which of union or
   * intersection applies to which mode, so that choice is served as data a
   * reviewer can disagree with by name.
   */
  inferences: () => apiRequest(`${PREFIX}/inferences`),

  /** The teams and distributions the rotation reads, with eligibility. */
  catalog: (params = {}) => apiRequest(`${PREFIX}/catalog${query(params)}`),

  /** Counts for the page header, over exactly the rows the filters return. */
  summary: (params = {}) => apiRequest(`${PREFIX}/summary${query(params)}`),

  teams: (params = {}) => apiRequest(`${PREFIX}/teams${query(params)}`),
  team: (teamId) => apiRequest(`${PREFIX}/teams/${teamId}`),
  createTeam: (payload) =>
    apiRequest(`${PREFIX}/teams`, { method: 'POST', body: JSON.stringify(payload) }),

  distributions: (params = {}) => apiRequest(`${PREFIX}/distributions${query(params)}`),
  distribution: (id) => apiRequest(`${PREFIX}/distributions/${id}`),
  createDistribution: (payload) =>
    apiRequest(`${PREFIX}/distributions`, { method: 'POST', body: JSON.stringify(payload) }),

  /**
   * Who would this prospect reach? Writes nothing.
   *
   * The read-only half of `initSimple`: same selection, same calendar
   * arithmetic, no routing session and no credit consumed.
   */
  check: (roomId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/check`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /** The researched init call: evaluate the distribution, offer the window. */
  initSimple: (roomId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/init-simple`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /** The researched schedule call: book one of the offered slots. */
  scheduleSimple: (roomId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/schedule-simple`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  routes: (params = {}) => apiRequest(`${PREFIX}/routes${query(params)}`),
  route: (routeId) => apiRequest(`${PREFIX}/routes/${routeId}`),

  bookings: (params = {}) => apiRequest(`${PREFIX}/bookings${query(params)}`),
  booking: (bookingId) => apiRequest(`${PREFIX}/bookings/${bookingId}`),
  cancelBooking: (bookingId) =>
    apiRequest(`${PREFIX}/bookings/${bookingId}/cancel`, { method: 'POST' }),

  /** Step 5: mark the prospect No-Show so the member is credited back. */
  markNoShow: (bookingId, payload = {}) =>
    apiRequest(`${PREFIX}/bookings/${bookingId}/no-show`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  noShows: (params = {}) => apiRequest(`${PREFIX}/no-shows${query(params)}`),

  /** Every credit that moved, in either direction. */
  credits: (params = {}) => apiRequest(`${PREFIX}/credits${query(params)}`),
}