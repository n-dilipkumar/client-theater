/**
 * API calls for find a time that works for a multi-person panel (WF-057).
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a
 * method to it is precisely the conflict the feature host exists to remove. The
 * host's `apiRequest` is the escape hatch that makes that unnecessary.
 *
 * `PREFIX` is the feature's own: the backend module mounts these under
 * `/api/wf-057`, and `apiRequest` already prepends `/api`.
 *
 * Nothing here hard-codes a payload shape. The status vocabulary, the
 * `emptySuggestionsReason` values, the re-call adjustments and the house-rule
 * keys are all rendered from the server's own vocabulary and inference
 * endpoints, so a deployment that adds a sixth status or a seventh adjustment
 * ships a record, not a change to this file.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-057'

function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

const room = (roomId, tail = '') => `/rooms/${encodeURIComponent(roomId)}${tail}`
const panel = (roomId, panelId, tail = '') =>
  room(roomId, `/panels/${encodeURIComponent(panelId)}${tail}`)
const search = (roomId, searchId, tail = '') =>
  room(roomId, `/searches/${encodeURIComponent(searchId)}${tail}`)

const post = (path, payload = {}) =>
  apiRequest(path, { method: 'POST', body: JSON.stringify(payload) })

export const panelTimeApi = {
  /** The researched contract: endpoints, scopes, weights, limits, the user flow. */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),

  /** Every design inference this workflow rests on, and how to change each one. */
  inferences: () => apiRequest(`${PREFIX}/inferences`),

  // -- calendars: whose availability is read -------------------------------- //

  listCalendars: (params = {}) => apiRequest(`${PREFIX}/calendars${query(params)}`),
  createCalendar: (payload) => post(`${PREFIX}/calendars`, payload),
  readCalendar: (calendarId) =>
    apiRequest(`${PREFIX}/calendars/${encodeURIComponent(calendarId)}`),
  updateCalendar: (calendarId, payload) =>
    apiRequest(`${PREFIX}/calendars/${encodeURIComponent(calendarId)}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  deleteCalendar: (calendarId) =>
    apiRequest(`${PREFIX}/calendars/${encodeURIComponent(calendarId)}`, { method: 'DELETE' }),

  // -- panels: the saved find-a-time request --------------------------------- //

  listPanels: (roomId, params = {}) => apiRequest(room(roomId, `/panels${query(params)}`)),
  createPanel: (roomId, payload) => post(room(roomId, '/panels'), payload),
  readPanel: (roomId, panelId) => apiRequest(panel(roomId, panelId)),
  updatePanel: (roomId, panelId, payload) =>
    apiRequest(panel(roomId, panelId), { method: 'PATCH', body: JSON.stringify(payload) }),
  deletePanel: (roomId, panelId) =>
    apiRequest(panel(roomId, panelId), { method: 'DELETE' }),

  // -- the researched call -------------------------------------------------- //

  /**
   * A dry run: the same computation the real call makes, plus the exact request
   * that would go on the wire, and no row. This is what the page's controls
   * re-render against, so a rep can try a different grid or a different bar and
   * watch the shortlist change before committing to one.
   */
  preview: (roomId, panelId, payload = {}) => post(panel(roomId, panelId, '/preview'), payload),

  /** The researched write. One explicit call, one search row. */
  find: (roomId, panelId, payload = {}) => post(panel(roomId, panelId, '/find'), payload),

  // -- the run log ---------------------------------------------------------- //

  listSearches: (roomId, params = {}) => apiRequest(room(roomId, `/searches${query(params)}`)),
  readSearch: (roomId, searchId) => apiRequest(search(roomId, searchId)),
  listBookings: (roomId, params = {}) => apiRequest(room(roomId, `/bookings${query(params)}`)),
  readBooking: (roomId, bookingId) =>
    apiRequest(room(roomId, `/bookings/${encodeURIComponent(bookingId)}`)),

  // -- the researched re-call and the researched commit --------------------- //

  /** What to change for this search's emptySuggestionsReason, and why. */
  adjustments: (roomId, searchId) => apiRequest(search(roomId, searchId, '/adjustments')),

  /**
   * The documented automation: apply one named adjustment to the parameters that
   * produced an empty result, and call again. Writes a *new* row beside the old
   * one rather than overwriting it.
   */
  retune: (roomId, searchId, payload = {}) => post(search(roomId, searchId, '/retune'), payload),

  /** The researched commit: the event on the organizer's calendar. */
  book: (roomId, searchId, payload = {}) => post(search(roomId, searchId, '/book'), payload),
}

/** Rooms, read through the shared generic surface rather than this feature's. */
export const listRooms = (params = {}) => apiRequest(`/records/room${query(params)}`)
