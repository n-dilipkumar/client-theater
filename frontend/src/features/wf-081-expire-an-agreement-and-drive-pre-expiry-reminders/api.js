/**
 * Agreement expiry API (WF-081).
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a
 * method to it is exactly the collision the feature host exists to remove. The
 * host's `apiRequest` is the escape hatch that makes that unnecessary.
 *
 * `PREFIX` is the feature's own: the backend module mounts these under
 * `/api/wf-081`, and `apiRequest` already prepends `/api`.
 *
 * Every payload is sent in the research's own spelling - `expires_at`, an integer
 * epoch timestamp in seconds, and `signatures[]` with `email` / `name` /
 * `status_code` / `preferred_timezone` - so the documented flow can be driven
 * against the API directly and a reader can match what this page sends against
 * what the research says should be sent.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-081'

/** Drop empty values so we never send `?status=` and confuse a filter. */
function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const expiryApi = {
  /**
   * The published terms: the 1-to-90-day window, the hour rounding, the
   * 3-and-7-day cadence, the 24-hour dedupe, the signer statuses the sweep moves
   * and the ones it keeps, the two delivery modes and the sentence saying what
   * expiry is not. Every picker and every badge on the page renders from this
   * rather than from a list compiled into this file, so a rule changed on the
   * server reaches every client at once.
   */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),

  /**
   * Every judgement call this workflow rests on: what the research fixes, what it
   * leaves open, and which reading this build took. Rendered verbatim on the
   * page's last section so a reviewer reads the list instead of reconstructing it
   * from a diff.
   */
  inferences: () => apiRequest(`${PREFIX}/inferences`),

  /** The room list comes from the core records route, so this page depends on
   * the HTTP contract rather than on another module's idea of a room's shape. */
  rooms: () => apiRequest('/records/room?limit=100'),

  requests: (roomId, params = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/requests${query(params)}`),
  send: (roomId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/requests`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  request: (roomId, requestId, params = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/requests/${requestId}${query(params)}`),

  /** `expires_at: null` clears the deadline, which is not the same as omitting it. */
  setExpiry: (roomId, requestId, expiresAt) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/requests/${requestId}/expiry`, {
      method: 'PUT',
      body: JSON.stringify({ expires_at: expiresAt }),
    }),

  reminders: (roomId, params = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/reminders${query(params)}`),
  runReminders: (roomId, requestId, payload = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/requests/${requestId}/reminders`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  sign: (roomId, requestId, email) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/requests/${requestId}/sign`, {
      method: 'POST',
      body: JSON.stringify({ email }),
    }),

  /** The researched gate, asked per person. A `GET` because it writes nothing. */
  canSign: (roomId, requestId, params = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/requests/${requestId}/can-sign${query(params)}`),

  sweep: (roomId, payload = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/sweep`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  events: (roomId, params = {}) => apiRequest(`${PREFIX}/rooms/${roomId}/events${query(params)}`),
  summary: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/summary`),
}

/**
 * The tone a request status is rendered in, from the published vocabulary.
 *
 * Reading the server's list rather than keeping a local copy means a status
 * added there renders here on the next load instead of falling through to a
 * neutral badge that says nothing. The tone is decoration only: every badge on
 * the page carries the status as text, because status is never conveyed by
 * colour alone.
 */
export function statusTone(vocabulary, status) {
  const entry = (vocabulary?.request_statuses || []).find((row) => row.status === status)
  if (!entry) return 'neutral'
  if (status === 'completed') return 'insert'
  if (status === 'expired') return 'restore'
  return 'update'
}

/**
 * The tone a signer's own ``status_code`` is rendered in.
 *
 * Kept apart from :func:`statusTone` on purpose. A request that expired and a
 * signer who expired inside it are different facts, and collapsing them into one
 * map would make a signed signer look expired on a page where the request itself
 * is still open.
 */
export function signerTone(statusCode) {
  if (statusCode === 'signed' || statusCode === 'completed') return 'insert'
  if (statusCode === 'expired') return 'restore'
  return 'neutral'
}

/**
 * How many days are left, as the page reads it.
 *
 * ``null`` for a request with no expiry, which the page renders as "no expiry"
 * rather than as a zero. A missing deadline is not a deadline of zero, and the
 * research is explicit that such a request never expires.
 */
export function daysLeft(request) {
  if (!request?.has_expiry) return null
  return request.days_remaining
}

/**
 * Whether a reminder is due for this request right now.
 *
 * The server sends the open windows on every projection, so the page never
 * re-derives the cadence and the button can never disagree with the rule that
 * would run when it is pressed.
 */
export function remindersDue(request) {
  return (request?.reminders_due || []).length > 0
}

/**
 * The one sentence saying what the seller should do about this agreement.
 *
 * An open agreement inside a reminder window needs a person, so the page's job
 * is to make that row impossible to miss. This helper exists so the "needs you"
 * list and the full table cannot disagree about which is which.
 */
export function needsAttention(request) {
  if (!request) return false
  if (request.status !== 'pending') return false
  return remindersDue(request)
}

/** Why a reminder was not sent, in the product's own words. */
export function skipReasonText(reason) {
  const texts = {
    already_signed: 'This signer already completed their part.',
    no_window_open: 'No reminder window is open. Reminders go out 7 and 3 days before.',
    deduped_within_24h: 'This signer was already reminded inside 24 hours.',
  }
  return texts[reason] || reason || 'Not sent.'
}

/** The refusal the server returned, in its own words. */
export function refusalSummary(error) {
  return String(error?.detail || error?.message || error || 'The request was refused.')
}

/**
 * Turn a date input into the integer epoch seconds the API asks for.
 *
 * "expires_at must be an integer epoch timestamp in seconds", so the browser's
 * local date is read as a local instant and sent as a whole number of seconds.
 * The server rounds it down to the hour; the page shows what it sent rather than
 * pretending the unrounded value was stored.
 */
export function toEpochSeconds(localDateTimeValue) {
  if (!localDateTimeValue) return null
  const parsed = Date.parse(localDateTimeValue)
  return Number.isNaN(parsed) ? null : Math.floor(parsed / 1000)
}

/** The reverse, for pre-filling the form from a stored deadline. */
export function toLocalInput(epochSeconds) {
  if (!epochSeconds) return ''
  return new Date(epochSeconds * 1000).toISOString().slice(0, 16)
}