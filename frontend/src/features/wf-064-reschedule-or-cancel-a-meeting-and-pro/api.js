/**
 * Meeting changes API calls (WF-064).
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a
 * method to it is precisely the conflict the feature host exists to remove. The
 * host's `apiRequest` is the escape hatch that makes that unnecessary.
 *
 * `PREFIX` is the feature's own: the backend module mounts these under
 * `/api/wf-064`, and `apiRequest` already prepends `/api`.
 *
 * Every picker on the page is rendered from `/vocabulary` rather than from a list
 * compiled into this file, so a team that widens a vocabulary ships a record
 * instead of a change here.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-064'

function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value))
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const meetingsApi = {
  /** The researched vocabulary. Drives every picker on the page. */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),

  /**
   * What the research leaves open, what this build chose, and how to change it.
   *
   * These gaps are product behaviour rather than comments, so they are served as
   * data a reviewer can disagree with by name.
   */
  inferences: () => apiRequest(`${PREFIX}/inferences`),

  /** Counts for the header, over exactly the rows the filters below return. */
  summary: (params = {}) => apiRequest(`${PREFIX}/summary${query(params)}`),

  listMeetingTypes: (params = {}) => apiRequest(`${PREFIX}/meeting-types${query(params)}`),
  createMeetingType: (payload) =>
    apiRequest(`${PREFIX}/meeting-types`, { method: 'POST', body: JSON.stringify(payload) }),
  updateMeetingType: (id, payload) =>
    apiRequest(`${PREFIX}/meeting-types/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteMeetingType: (id) => apiRequest(`${PREFIX}/meeting-types/${id}`, { method: 'DELETE' }),

  listBookings: (params = {}) => apiRequest(`${PREFIX}/bookings${query(params)}`),
  createBooking: (payload, params = {}) =>
    apiRequest(`${PREFIX}/bookings${query(params)}`, { method: 'POST', body: JSON.stringify(payload) }),
  booking: (uid) => apiRequest(`${PREFIX}/bookings/${uid}`),
  bookingChanges: (uid) => apiRequest(`${PREFIX}/bookings/${uid}/changes`),
  /** The invite body, with CP.Meeting.RescheduleUrl / CancelUrl resolved. */
  invite: (uid) => apiRequest(`${PREFIX}/bookings/${uid}/invite`),

  /**
   * The recomputed availability.
   *
   * `bookingUidToReschedule` is passed under its researched name because it *is*
   * the researched behaviour: without it the booking's own time is not on offer
   * and the meeting cannot be moved to the time it is already at.
   */
  availability: (params = {}) => apiRequest(`${PREFIX}/availability${query(params)}`),

  /** Every link, and whether it still works. */
  listLinks: (params = {}) => apiRequest(`${PREFIX}/links${query(params)}`),
  link: (token) => apiRequest(`${PREFIX}/links/${token}`),

  /** Events History, newest first. */
  listChanges: (params = {}) => apiRequest(`${PREFIX}/changes${query(params)}`),
  change: (id) => apiRequest(`${PREFIX}/changes/${id}`),

  /** What the fan-out actually pushed, and where the notices went. */
  listWebhooks: (params = {}) => apiRequest(`${PREFIX}/webhooks${query(params)}`),
  listNotifications: (params = {}) => apiRequest(`${PREFIX}/notifications${query(params)}`),
  listCrmEvents: (params = {}) => apiRequest(`${PREFIX}/crm-events${query(params)}`),
  listCalendarEvents: (params = {}) => apiRequest(`${PREFIX}/calendar-events${query(params)}`),

  listRescheduleRequests: (params = {}) => apiRequest(`${PREFIX}/reschedule-requests${query(params)}`),
  rescheduleRequest: (id) => apiRequest(`${PREFIX}/reschedule-requests/${id}`),
  completeRequest: (id, payload) =>
    apiRequest(`${PREFIX}/reschedule-requests/${id}/complete`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  roomMeetings: (roomId, params = {}) => apiRequest(`${PREFIX}/rooms/${roomId}/meetings${query(params)}`),
  roomChanges: (roomId, params = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/meeting-changes${query(params)}`),

  /**
   * Report what a change would do, and write none of it.
   *
   * Same rules as the writes, so the answer is the answer the write gives -
   * including a refusal, which comes back as the same error.
   */
  plan: (roomId, uid, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/bookings/${uid}/plan`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /** The three researched intents, end to end. */
  reschedule: (roomId, uid, payload = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/bookings/${uid}/reschedule`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  requestReschedule: (roomId, uid, payload = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/bookings/${uid}/request-reschedule`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  cancel: (roomId, uid, payload = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/bookings/${uid}/cancel`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
}

export default meetingsApi
