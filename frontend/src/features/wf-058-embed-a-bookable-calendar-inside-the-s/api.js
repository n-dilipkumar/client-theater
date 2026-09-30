/**
 * Bookable calendar API calls (WF-058).
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a
 * method to it is precisely the conflict the feature host exists to remove. The
 * host's `apiRequest` is the escape hatch that makes that unnecessary.
 *
 * `PREFIX` is the feature's own: the backend module mounts these under
 * `/api/wf-058`, and `apiRequest` already prepends `/api`.
 *
 * Every picker on the page is rendered from the `/vocabulary` endpoint rather than
 * from a list compiled into this file, so a team that adds a slot selector or an
 * embed component ships a record instead of a change here.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-058'

function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value))
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const calendarApi = {
  /** The researched vocabulary. Drives every picker and every explanation. */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),

  /**
   * What the research leaves open, what this build chose, and how to change it.
   *
   * WF-058 is a build rather than a port, so most of the workflow is this build's
   * decisions rather than quoted behaviour. Those decisions are product behaviour,
   * not comments, so they are served as data a reviewer can disagree with by name.
   */
  inferences: () => apiRequest(`${PREFIX}/inferences`),

  summary: (params = {}) => apiRequest(`${PREFIX}/summary${query(params)}`),

  // -- step 1: the OAuth client -------------------------------------------- //

  listClients: (params = {}) => apiRequest(`${PREFIX}/oauth-clients${query(params)}`),
  createClient: (payload) =>
    apiRequest(`${PREFIX}/oauth-clients`, { method: 'POST', body: JSON.stringify(payload) }),
  updateClient: (id, payload) =>
    apiRequest(`${PREFIX}/oauth-clients/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  /** Record that a token exists. The value is never sent, because it is never stored. */
  grant: (id, payload) =>
    apiRequest(`${PREFIX}/oauth-clients/${id}/grant`, { method: 'POST', body: JSON.stringify(payload) }),
  deleteClient: (id) => apiRequest(`${PREFIX}/oauth-clients/${id}`, { method: 'DELETE' }),

  // -- step 2: event types, calendars, routing forms ----------------------- //

  listEventTypes: (params = {}) => apiRequest(`${PREFIX}/event-types${query(params)}`),
  createEventType: (payload) =>
    apiRequest(`${PREFIX}/event-types`, { method: 'POST', body: JSON.stringify(payload) }),
  updateEventType: (id, payload) =>
    apiRequest(`${PREFIX}/event-types/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteEventType: (id) => apiRequest(`${PREFIX}/event-types/${id}`, { method: 'DELETE' }),

  listCalendars: (params = {}) => apiRequest(`${PREFIX}/calendars${query(params)}`),
  connectCalendar: (payload) =>
    apiRequest(`${PREFIX}/calendars`, { method: 'POST', body: JSON.stringify(payload) }),
  disconnectCalendar: (id) => apiRequest(`${PREFIX}/calendars/${id}`, { method: 'DELETE' }),

  listRoutingForms: (params = {}) => apiRequest(`${PREFIX}/routing-forms${query(params)}`),
  createRoutingForm: (payload) =>
    apiRequest(`${PREFIX}/routing-forms`, { method: 'POST', body: JSON.stringify(payload) }),
  updateRoutingForm: (id, payload) =>
    apiRequest(`${PREFIX}/routing-forms/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteRoutingForm: (id) => apiRequest(`${PREFIX}/routing-forms/${id}`, { method: 'DELETE' }),

  // -- the embed, per room -------------------------------------------------- //

  embed: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/embed`),
  saveEmbed: (roomId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/embed`, { method: 'PUT', body: JSON.stringify(payload) }),

  // -- the Booker's grid, per room ----------------------------------------- //

  /**
   * The slot grid. Writes nothing, and says so: the routing read is a read
   * because the research says the response is not saved.
   */
  slots: (roomId, params = {}) => apiRequest(`${PREFIX}/rooms/${roomId}/slots${query(params)}`),

  routedSlots: (roomId, params = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/routed-slots${query(params)}`),

  // -- holds, per room ------------------------------------------------------ //

  listHolds: (roomId, params = {}) => apiRequest(`${PREFIX}/rooms/${roomId}/holds${query(params)}`),
  reserve: (roomId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/holds`, { method: 'POST', body: JSON.stringify(payload) }),
  extendHold: (roomId, uid, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/holds/${uid}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  releaseHold: (roomId, uid) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/holds/${uid}`, { method: 'DELETE' }),

  // -- bookings, per room --------------------------------------------------- //

  listBookings: (roomId, params = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/bookings${query(params)}`),
  booking: (roomId, uid) => apiRequest(`${PREFIX}/rooms/${roomId}/bookings/${uid}`),
  book: (roomId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/bookings`, { method: 'POST', body: JSON.stringify(payload) }),
  cancelBooking: (roomId, uid, reason) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/bookings/${uid}${query({ reason })}`, { method: 'DELETE' }),
  bookingEvents: (roomId, params = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/booking-events${query(params)}`),
}
