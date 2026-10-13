/**
 * WF-097's own API wrapper.
 *
 * `apiRequest` from `@/lib/api` is the transport, exactly as the contract asks.
 * The shared `api` object grows no methods, so a hundred features can each talk
 * to their own `/api/wf-097` routes without anyone editing a shared file.
 *
 * `apiRequest` reads the error body for its message and keeps the status on the
 * thrown error, and the status is the one field this workflow branches on: a 422
 * carries a rule refusal whose detail is worth showing beside the control that
 * caused it, and a 404 or a 500 is a different problem entirely.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-097'

const encode = encodeURIComponent

function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

function call(path, params) {
  return apiRequest(`${BASE}${path}${query(params)}`)
}

function send(path, method, payload) {
  return apiRequest(`${BASE}${path}`, { method, body: JSON.stringify(payload) })
}

export const engagementApi = {
  /** The event labels, the counter labels, the classes and the bounds. */
  vocabulary: () => call('/vocabulary'),

  summary: (roomId) => call('/summary', { room_id: roomId }),

  settings: (roomId) => call('/settings', { room_id: roomId }),

  /** A patch merges, so writing the threshold leaves the other keys alone. */
  saveSettings: (roomId, patch) => send('/settings', 'PUT', patch),

  quotes: (roomId, filters = {}) =>
    call('/quotes', {
      room_id: roomId,
      status: filters.status,
      activity_class: filters.activityClass,
      owner: filters.owner,
      signing_status: filters.signingStatus,
      where: filters.where ? JSON.stringify(filters.where) : undefined,
    }),

  engagement: (quoteId) => call(`/quotes/${encode(quoteId)}/engagement`),

  activity: (quoteId, limit) => call(`/quotes/${encode(quoteId)}/activity`, { limit }),

  /**
   * Record one event.
   *
   * The body carries the researched label and the surface it arrived on. The
   * surface is the whole of the researched view distinction, and it is a
   * parameter rather than a second event label because the researched vocabulary
   * has one label for it.
   */
  recordEvent: (quoteId, event, surface) =>
    send(`/quotes/${encode(quoteId)}/activity`, 'POST', { event, surface }),

  chart: (roomId, includeExpired) =>
    call('/chart', { room_id: roomId, include_expired: includeExpired }),

  overview: (roomId) => call('/overview', { room_id: roomId }),

  followUps: (roomId) => call('/follow-ups', { room_id: roomId }),

  sendFollowUp: (quoteId) => send(`/follow-ups/${encode(quoteId)}/send`, 'POST', {}),
}

/** Every room, so the page can offer one. Read from the core collection. */
export const listRooms = () => apiRequest('/records/room?limit=100')

/**
 * The tone each activity class renders with.
 *
 * The class name is always printed beside the tone, so this mapping is a second
 * signal and never the only one. It exists here for the same reason a form's
 * trigger has to be spelled locally: a badge whose colour arrives after the first
 * paint is a badge a seller reads once and stops reading.
 *
 * The tones are the four the shared `Badge` actually has, plus `insert` and
 * `delete`. It has no `success` or `warning` tone, and a badge asked for one
 * silently renders neutral, so a mapping onto a tone that does not exist would
 * leave High activity and Low activity looking identical.
 */
export const ACTIVITY_TONES = {
  'High activity': 'insert',
  'Low activity': 'restore',
  'Not classified': 'neutral',
}

/** The tone for an expired quote, in a badge a seller reads beside the class. */
export const EXPIRED_TONE = 'delete'

/** The tone for a class, defaulting to neutral for a class we have not seen. */
export function activityTone(activityClass) {
  return ACTIVITY_TONES[activityClass] || 'neutral'
}

/**
 * The three settings a workspace may configure, with the bounds the rules
 * enforce.
 *
 * The bounds are duplicated here so a seller cannot type a number the server will
 * refuse. The server remains the authority and still refuses; this only means the
 * mistake is caught before a round trip.
 */
export const SETTING_FIELDS = [
  {
    key: 'high_activity_threshold',
    label: 'High activity threshold',
    hint: 'A quote at or above this Activity Count is High activity. A workspace that sets none classifies nothing.',
    unit: 'activities',
    min: 1,
    max: 1000,
  },
  {
    key: 'no_activity_after_days',
    label: 'Recommend after this many days with no activity',
    hint: 'A workspace that sets none gets no quiet-quote recommendation.',
    unit: 'days',
    min: 1,
    max: 3650,
  },
  {
    key: 'expiring_within_days',
    label: 'Recommend a quote expiring within',
    hint: 'A workspace that sets none gets no expiring-soon recommendation.',
    unit: 'days',
    min: 1,
    max: 3650,
  },
]

/** Is a value inside the bounds the server enforces for that setting? */
export function withinBounds(field, value) {
  const number = Number(value)
  if (!Number.isInteger(number)) return false
  return number >= field.min && number <= field.max
}

/**
 * The reason a recommendation was given, as a sentence.
 *
 * The sentence comes from the server's own `detail`, so the page never restates a
 * reason the rules did not give. This only falls back to the label when the
 * server sent a reason with no sentence, which should not happen and is worth
 * seeing rather than hiding.
 */
export function reasonSentence(reason, vocabulary) {
  return reason?.detail || vocabulary?.follow_up_reasons?.[reason?.reason] || reason?.reason || ''
}

/**
 * A counter as a seller reads it, with the reason it is absent.
 *
 * The third counter is reported as unavailable when no email channel is
 * connected, and a page that rendered a blank cell there would read as a bug.
 * The reason is named instead.
 */
export function counterText(counter, reasonLabels = {}) {
  if (counter === null || counter === undefined) {
    return reasonLabels[counter] || 'Not available'
  }
  return String(counter)
}

/** A timestamp as a short local string, or a dash when there is none. */
export function shortTimestamp(value) {
  if (!value) return '--'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return String(value)
  return parsed.toISOString().replace('T', ' ').replace(/\.\d+Z$/, 'Z')
}

/** A money figure with two decimals, or a dash when the quote carries none. */
export function amount(value) {
  if (value === undefined || value === null || value === '') return '--'
  const number = Number(value)
  if (Number.isNaN(number)) return String(value)
  return number.toFixed(2)
}

/** A percentage for the acceptance rate, or a dash when there is no rate. */
export function percent(value) {
  if (value === undefined || value === null) return '--'
  return `${(Number(value) * 100).toFixed(1)}%`
}