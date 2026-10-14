/**
 * WF-137's own API wrapper.
 *
 * `apiRequest` from `@/lib/api` is the transport, exactly as the contract asks.
 * The shared `api` object grows no methods, so a hundred features can each talk
 * to their own `/api/wf-137` routes without anyone editing a shared file.
 *
 * The error keeps its status: a 409 means the room's state refused the change
 * (already repurposed, an open milestone, an archived room), a 422 means a rule
 * refused the payload, and a 404 means the room, version or plan does not exist.
 * The page branches on those three.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-137'

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

export const governanceApi = {
  /** The values this workflow accepts, and the decision behind each. */
  vocabulary: () => call('/vocabulary'),

  /** Every decision the research does not make. */
  inferences: () => call('/inferences'),

  summary: () => call('/summary'),

  /** The brand token set and every governed room's compliance against it. */
  brand: () => call('/brand'),

  writeBrand: (payload) => send('/brand', 'POST', payload),

  roomBrand: (roomId) => call(`/rooms/${encode(roomId)}/brand`),

  applyBrand: (roomId) => send(`/rooms/${encode(roomId)}/brand/apply`, 'POST', {}),

  /** Every version of a room, oldest first. */
  history: (roomId) => call(`/rooms/${encode(roomId)}/history`),

  /** A publish (automatic) or an editing-session checkpoint. */
  snapshot: (roomId, payload, trigger = 'publish') =>
    send(`/rooms/${encode(roomId)}/snapshots`, 'POST', payload, { trigger }),

  /** What restoring a version would change, field by field. */
  preview: (roomId, version) => call(`/rooms/${encode(roomId)}/snapshots/${version}`),

  restore: (roomId, version) =>
    send(`/rooms/${encode(roomId)}/snapshots/${version}/restore`, 'POST', {}),

  branch: (roomId, version, payload) =>
    send(`/rooms/${encode(roomId)}/snapshots/${version}/branch`, 'POST', payload),

  plans: () => call('/plans'),

  writePlan: (payload) => send('/plans', 'POST', payload),

  onboarding: (roomId) => call(`/rooms/${encode(roomId)}/onboarding`),

  repurpose: (roomId, payload) => send(`/rooms/${encode(roomId)}/repurpose`, 'POST', payload),

  advance: (roomId) => send(`/rooms/${encode(roomId)}/onboarding/advance`, 'POST', {}),

  completeMilestone: (roomId, payload) =>
    send(`/rooms/${encode(roomId)}/onboarding/milestones/complete`, 'POST', payload),

  retentionBoard: () => call('/retention'),

  roomRetention: (roomId) => call(`/rooms/${encode(roomId)}/retention`),

  setRetention: (roomId, payload) => send(`/rooms/${encode(roomId)}/retention`, 'POST', payload),

  applyRetention: (roomId) => send(`/rooms/${encode(roomId)}/retention/apply`, 'POST', {}),
}

/** Every room, so the page can offer one. Read from the core collection. */
export const listRooms = () => apiRequest('/records/room?limit=100')

/** The words a seller reads for each snapshot trigger. */
export const TRIGGER_LABELS = {
  publish: 'Publish',
  session: 'Editing session',
  restore: 'Restore',
  branch: 'Branch',
}

/** The words a seller reads for each lifecycle state. */
export const LIFECYCLE_LABELS = {
  sales: 'Sales room',
  onboarding: 'Onboarding portal',
  onboarded: 'Onboarded',
  archived: 'Archived',
  deleted: 'Deleted',
}

/** The words a seller reads for each retention state. */
export const RETENTION_LABELS = {
  live: 'Live',
  archived: 'Archived',
  deleted: 'Deleted',
}

/** The tone each lifecycle and retention state renders with. The word always shows. */
export const STATE_TONES = {
  sales: 'neutral',
  onboarding: 'insert',
  onboarded: 'update',
  archived: 'restore',
  deleted: 'delete',
  live: 'neutral',
}

export function stateTone(state) {
  return STATE_TONES[state] || 'neutral'
}

/** A finding code as the words a seller reads. */
export const FINDING_LABELS = {
  missing_token: 'Missing token',
  unapproved_value: 'Unapproved value',
  unapproved_theme: 'Unapproved theme',
  unapproved_font: 'Unapproved font',
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
 * The server is still the authority and still refuses; this only means a
 * mistake is caught before the round trip and the control is disabled rather
 * than offering a failure.
 */
export function wholeInRange(value, min, max) {
  const number = Number(value)
  if (!Number.isInteger(number)) return null
  if (number < min || number > max) return null
  return number
}

/** A parseable ISO instant, or null. */
export function instantOrNull(value) {
  if (!value) return null
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? null : value
}
