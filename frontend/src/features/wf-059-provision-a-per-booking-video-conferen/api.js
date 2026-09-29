/**
 * Conference-link API calls (WF-059).
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a
 * method to it is precisely the conflict the feature host exists to remove. The
 * host's `apiRequest` is the escape hatch that makes that unnecessary.
 *
 * `PREFIX` is the feature's own: the backend module mounts these under
 * `/api/wf-059`, and `apiRequest` already prepends `/api`.
 *
 * Every picker on the page is rendered from `/vocabulary` rather than from a
 * list compiled into this file, so a team that adds a Location option or a
 * provider ships a record instead of a change here.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-059'

function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value))
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const linksApi = {
  /** The researched vocabulary: the seven Location options, 29 integrations, 8 location types. */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),

  /**
   * What the research leaves open, what this build chose, and how to change it.
   *
   * Those gaps are product behaviour rather than comments, so they are served as
   * data a reviewer can disagree with by name.
   */
  inferences: () => apiRequest(`${PREFIX}/inferences`),

  /** The Location catalogue, and one option in full. */
  locationKinds: () => apiRequest(`${PREFIX}/location-kinds`),
  locationKind: (kind) => apiRequest(`${PREFIX}/location-kinds/${encodeURIComponent(kind)}`),

  /** The providers, split into the three the picker offers and the rest of Cal's enum. */
  providers: () => apiRequest(`${PREFIX}/providers`),

  /** The Integrations tab. */
  listConnections: (params = {}) => apiRequest(`${PREFIX}/connections${query(params)}`),
  connect: (payload) =>
    apiRequest(`${PREFIX}/connections`, { method: 'POST', body: JSON.stringify(payload) }),
  connection: (id) => apiRequest(`${PREFIX}/connections/${id}`),
  amendConnection: (id, payload) =>
    apiRequest(`${PREFIX}/connections/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  reauthorize: (id, payload) =>
    apiRequest(`${PREFIX}/connections/${id}/reauthorize`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  disconnect: (id) => apiRequest(`${PREFIX}/connections/${id}`, { method: 'DELETE' }),

  /** The Meeting Type's Location picker, several of them with one the default. */
  listLocations: (params = {}) => apiRequest(`${PREFIX}/meeting-locations${query(params)}`),
  createLocation: (payload) =>
    apiRequest(`${PREFIX}/meeting-locations`, { method: 'POST', body: JSON.stringify(payload) }),
  location: (id) => apiRequest(`${PREFIX}/meeting-locations/${id}`),
  amendLocation: (id, payload) =>
    apiRequest(`${PREFIX}/meeting-locations/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  removeLocation: (id) => apiRequest(`${PREFIX}/meeting-locations/${id}`, { method: 'DELETE' }),
  setDefaultLocation: (id) => apiRequest(`${PREFIX}/meeting-locations/${id}/set-default`, { method: 'POST' }),

  /** Bookings, and the researched automation that provisions a link on create. */
  summary: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/summary`),
  listBookings: (roomId, params = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/bookings${query(params)}`),
  book: (roomId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/bookings`, { method: 'POST', body: JSON.stringify(payload) }),
  booking: (roomId, bookingUid) => apiRequest(`${PREFIX}/rooms/${roomId}/bookings/${bookingUid}`),
  conference: (roomId, bookingUid) => apiRequest(`${PREFIX}/rooms/${roomId}/bookings/${bookingUid}/conference`),
  invite: (roomId, bookingUid) => apiRequest(`${PREFIX}/rooms/${roomId}/bookings/${bookingUid}/invite`),
  history: (roomId, bookingUid) => apiRequest(`${PREFIX}/rooms/${roomId}/bookings/${bookingUid}/history`),
  providerStatus: (roomId, bookingUid) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/bookings/${bookingUid}/provider-status`),

  /**
   * The researched swap: re-provisions the link and emails the attendees.
   *
   * One request, because Cal's endpoint "also provisions a conference link" and
   * "Attendees are notified of the location change by email" in the same call.
   */
  swap: (roomId, bookingUid, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/bookings/${bookingUid}/location`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /** The retry path: a booking with no link yet, or a guest who has answered. */
  provision: (roomId, bookingUid, payload = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/bookings/${bookingUid}/provision`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /** Record the provider's own `appsStatus[]` report and apply the retry rule. */
  reportProviderStatus: (roomId, bookingUid, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/bookings/${bookingUid}/apps-status`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /** Render the invite body with the two researched dynamic tags resolved. */
  previewInvite: (payload) =>
    apiRequest(`${PREFIX}/invite-preview`, { method: 'POST', body: JSON.stringify(payload) }),
}
