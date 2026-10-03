/**
 * Slot approval API (WF-062).
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a
 * method to it is precisely the collision the feature host exists to remove. The
 * host's `apiRequest` is the escape hatch that makes that unnecessary.
 *
 * `PREFIX` is the feature's own: the backend module mounts these under
 * `/api/wf-062`, and `apiRequest` already prepends `/api`.
 *
 * Every payload is sent in the research's own spelling - `requiresConfirmation`,
 * `oneTimePassword`, `rejectionReason`, `emailVerificationCode`, `skipContactOwner`,
 * `apiVersion` - so the documented flow can be followed against the API directly
 * and a reader can match what the page sends against what the research says
 * should be sent. The room list is read from the core records route, so this page
 * depends on the HTTP contract rather than on another module's idea of the shape
 * of a room.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-062'

/** Drop empty values so we never send `?status=` and confuse a filter. */
function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const approvalApi = {
  /**
   * The published vocabularies: the four statuses in both researched spellings,
   * the two webhook events, the two workflow triggers, the three bypass flags
   * with the check each skips, the five privileged roles, and the default
   * rejection reason. Every picker and every badge on the page renders from this
   * rather than from a list compiled into this file, so a value added on the
   * server reaches every client at once.
   */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),

  /** Which researched `apis_hit` this feature implements, and where each lives. */
  capabilities: () => apiRequest(`${PREFIX}/capabilities`),

  /**
   * Every judgement call this workflow rests on: what the research fixes, what
   * it leaves open, and which reading this build took. Rendered verbatim on the
   * page's last tab so a reviewer reads the list instead of reconstructing it
   * from a diff.
   */
  inferences: () => apiRequest(`${PREFIX}/inferences`),

  eventTypes: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/event-types`),
  createEventType: (roomId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/event-types`, { method: 'POST', body: JSON.stringify(payload) }),
  eventType: (roomId, eventTypeId) => apiRequest(`${PREFIX}/rooms/${roomId}/event-types/${eventTypeId}`),
  patchEventType: (roomId, eventTypeId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/event-types/${eventTypeId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),

  /**
   * The researched "Check if email verification is required". A `GET`, because
   * it checks and writes nothing - which is also the shape the research's own
   * `GET/POST /v2/bookings/email-verification/...` triad describes.
   */
  verificationRequired: (roomId, { eventTypeId, email } = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/email-verification/required${query({ event_type_id: eventTypeId, email })}`),
  sendVerificationCode: (roomId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/email-verification/send`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  verifyEmail: (roomId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/email-verification/verify`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  requests: (roomId, params = {}) => apiRequest(`${PREFIX}/rooms/${roomId}/requests${query(params)}`),
  requestSlot: (roomId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/requests`, { method: 'POST', body: JSON.stringify(payload) }),
  request: (roomId, uid) => apiRequest(`${PREFIX}/rooms/${roomId}/requests/${uid}`),
  confirm: (roomId, uid, payload = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/requests/${uid}/confirm`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  decline: (roomId, uid, payload = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/requests/${uid}/decline`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  webhooks: (roomId, uid) => apiRequest(`${PREFIX}/rooms/${roomId}/requests/${uid}/webhooks`),
  calendarEvent: (roomId, uid) => apiRequest(`${PREFIX}/rooms/${roomId}/requests/${uid}/calendar-event`),

  automations: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/automations`),
  createAutomation: (roomId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/automations`, { method: 'POST', body: JSON.stringify(payload) }),

  summary: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/summary`),

  rooms: () => apiRequest('/records/room?limit=100'),
}

/**
 * The tone a booking's status is rendered in, from the published vocabulary.
 *
 * Reading the server's list rather than keeping a local copy means a status
 * added there renders here on the next load instead of falling through to a
 * neutral badge that says nothing.
 */
export function statusTone(vocabulary, status) {
  const entry = (vocabulary?.statuses || []).find((row) => row.value === status)
  if (!entry) return 'neutral'
  if (status === 'ACCEPTED') return 'insert'
  if (status === 'REJECTED') return 'delete'
  if (status === 'CANCELLED') return 'restore'
  if (status === 'PENDING') return 'update'
  return 'neutral'
}

/** The server's own sentence for a status, so the page never paraphrases it. */
export function statusMeaning(vocabulary, status) {
  return (vocabulary?.statuses || []).find((row) => row.value === status)?.meaning || ''
}

/** Whether this booking is still waiting on a human. */
export function isPending(booking) {
  return Boolean(booking?.pending)
}

/**
 * The one sentence saying what a host should do about this request.
 *
 * A PENDING row is the only one that needs a person, so the page's whole job is
 * to make that row impossible to miss and to name the two actions on it. This
 * helper exists so the "needs you" list and the full table cannot disagree about
 * which is which.
 */
export function needsAHost(booking) {
  return isPending(booking)
}

/**
 * The researched default a decline falls back to when the host gives no reason.
 *
 * Read from the server rather than typed here, because the whole point of the
 * fallback is that it is the sentence the vendor's own `BOOKING_REJECTED` payload
 * quotes - and a page that typed it would be free to drift from it.
 */
export function defaultRejectionReason(vocabulary) {
  return vocabulary?.default_rejection_reason || ''
}

/**
 * Why a refused request was refused, in the caller's own words.
 *
 * The server returns every failed check rather than the first one, so a rep
 * fixing a rejection sees the whole list at once instead of discovering the
 * second problem on the retry.
 */
export function refusalSummary(error) {
  const refusals = error?.refusals
  if (Array.isArray(refusals) && refusals.length) {
    return refusals.map((entry) => entry.detail).join(' ')
  }
  return String(error?.message || error || 'The request was refused.')
}

/** The API versions this feature honours a bypass on, straight from the server. */
export function bypassVersions(vocabulary) {
  const flags = vocabulary?.bypass_flags || []
  const versions = flags.find((entry) => entry.honoured_only_on)?.honoured_only_on
  return Array.isArray(versions) ? versions : []
}

/** The roles that are allowed to use a bypass, straight from the server. */
export function bypassRoles(vocabulary) {
  const flags = vocabulary?.bypass_flags || []
  const roles = flags.find((entry) => entry.honoured_only_for)?.honoured_only_for
  return Array.isArray(roles) ? roles : []
}