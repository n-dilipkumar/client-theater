/**
 * WF-121's own API wrapper.
 *
 * `apiRequest` from `@/lib/api` is the transport, exactly as the contract asks.
 * The shared `api` object grows no methods, so a hundred features can each talk
 * to their own `/api/wf-121` routes without anyone editing a shared file.
 *
 * The status is the one field this workflow branches on: a 410 means the link is
 * closed and no view will be accepted, 428 means a view arrived with no viewer,
 * and 422 means a rule refused the payload. The thrown error keeps the status.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-121'

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

function send(path, method, payload, params) {
  return apiRequest(`${BASE}${path}${query(params)}`, {
    method,
    body: JSON.stringify(payload || {}),
  })
}

export const trackedLinkApi = {
  /** The values this workflow accepts, and which workflow each one is borrowed from. */
  vocabulary: () => call('/vocabulary'),

  /** Every decision the research does not make. */
  inferences: () => call('/inferences'),

  summary: (roomId) => call('/summary', { room_id: roomId }),

  links: (roomId) => call('/links', { room_id: roomId }),

  link: (linkId) => call(`/links/${encode(linkId)}`),

  createLink: (roomId, payload) => send('/links', 'POST', payload, { room_id: roomId }),

  updateLink: (linkId, payload) => send(`/links/${encode(linkId)}`, 'PATCH', payload),

  revokeLink: (linkId) => send(`/links/${encode(linkId)}`, 'DELETE', {}),

  deliveries: (linkId) => call(`/links/${encode(linkId)}/deliveries`),

  distribute: (linkId, recipients) =>
    send(`/links/${encode(linkId)}/deliveries`, 'POST', { recipients }),

  /** Record one viewer's open of the link. The CRM write stays queued. */
  recordView: (linkId, view) => send(`/links/${encode(linkId)}/views`, 'POST', view),

  analytics: (linkId) => call(`/links/${encode(linkId)}/analytics`),

  viewers: (linkId) => call(`/links/${encode(linkId)}/viewers`),

  viewerViews: (linkId, viewer) => call(`/links/${encode(linkId)}/viewers/${encode(viewer)}/views`),

  alerts: (roomId) => call('/alerts', { room_id: roomId }),

  /** Drain the queued CRM writes for this room, through WF-037's worker. */
  push: (roomId) => send(`/rooms/${encode(roomId)}/push`, 'POST', {}),
}

/** Every room, so the page can offer one. Read from the core collection. */
export const listRooms = () => apiRequest('/records/room?limit=100')

/**
 * The tone each link state renders with.
 *
 * The state name is always printed beside the tone, so the colour is a second
 * signal and never the only one. Only the four tones the shared `Badge` has are
 * used; a mapping onto any other name renders as neutral, which would leave
 * "active" and "expired" looking identical.
 */
export const STATE_TONES = {
  active: 'insert',
  expired: 'restore',
  revoked: 'delete',
}

export function stateTone(state) {
  return STATE_TONES[state] || 'neutral'
}

/** The tone each alert trigger renders with, from the server's own list. */
export const ALERT_TONES = {
  first_view: 'neutral',
  return_visit: 'insert',
  page_threshold: 'update',
  dwell_threshold: 'restore',
}

export function alertTone(trigger) {
  return ALERT_TONES[trigger] || 'neutral'
}

/**
 * The Alert trigger labels this page prints.
 *
 * The trigger codes are stable machine tokens; these are the words a rep reads.
 * They are local rather than served because the code is the contract and the
 * label is presentation, and the served vocabulary already carries the code.
 */
export const ALERT_LABELS = {
  first_view: 'First view',
  return_visit: 'Return visit',
  page_threshold: 'Reached the page threshold',
  dwell_threshold: 'Read for a long time',
}

/** Seconds as a compact human string. */
export function formatSeconds(value) {
  const seconds = Number(value)
  if (!Number.isFinite(seconds) || seconds <= 0) return '--'
  if (seconds < 60) return `${Math.round(seconds)}s`
  const minutes = Math.floor(seconds / 60)
  const rest = Math.round(seconds % 60)
  return rest ? `${minutes}m ${rest}s` : `${minutes}m`
}

/** A timestamp as a short string, or a dash when there is none. */
export function shortTimestamp(value) {
  if (!value) return '--'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return String(value)
  return parsed.toISOString().replace('T', ' ').replace(/\.\d+Z$/, 'Z')
}

/**
 * A whole number inside a range, or null when the value is not usable.
 *
 * The bounds are the same ones the server enforces; the server is still the
 * authority and still refuses. This only means a mistake is caught before the
 * round trip and the Save control is disabled rather than offering a failure.
 */
export function wholeInRange(value, min, max) {
  const number = Number(value)
  if (!Number.isInteger(number)) return null
  if (number < min || number > max) return null
  return number
}
