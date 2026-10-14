/**
 * WF-126's own API wrapper.
 *
 * `apiRequest` from `@/lib/api` is the transport, exactly as the contract asks:
 * the shared `api` object grows no methods, so a hundred features can each talk
 * to their own `/api/wf-126` routes without anyone editing a shared file.
 *
 * The write calls go through `requestWithBody` instead, and that is a finding
 * rather than a preference. `apiRequest` reads the error body to build a message
 * and then throws it away, so a failure survives as `status` plus one string.
 * Two responses in this workflow need more than that:
 *
 *   - a 422 from the validator carries `errors`, a field-keyed map, so a form can
 *     put each message beside the input that caused it;
 *   - a 409 from the engine carries `reason`, a stable token, so the page can say
 *     "that video belongs to another room" instead of printing an English
 *     sentence and hoping the server did not reword it.
 *
 * The shared client would need two more fields on one function, which is a shared
 * file and a platform decision. Until then the calls that need the body read it
 * themselves. Recorded as promotion work, not smuggled across the boundary.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-126'

function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

/** The shared client. Fine for everything whose failure is just a failure. */
function call(path) {
  return apiRequest(`${BASE}${path}`)
}

/** As `apiRequest`, but keeps the parsed error body on the thrown error. */
async function requestWithBody(path, options) {
  const response = await fetch(`/api${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })

  if (!response.ok) {
    let body = null
    try {
      body = await response.json()
    } catch {
      // Non-JSON error body; the status line is the best we have.
    }
    const error = new Error(
      body?.detail || body?.error || `${response.status} ${response.statusText}`,
    )
    error.status = response.status
    error.code = body?.error || null
    error.errors = body?.errors || null
    error.reason = body?.reason || null
    error.body = body
    throw error
  }

  if (response.status === 204) return null
  return response.json()
}

function send(path, method, payload) {
  return requestWithBody(path, { method, body: JSON.stringify(payload) })
}

const encode = encodeURIComponent

export const videoApi = {
  /** The researched vocabulary, including what was deliberately not built. */
  vocabulary: () => call('/vocabulary'),

  summary: (roomId) => call(`/summary${query({ room_id: roomId })}`),

  /** The room's buyers, reused from WF-004's grants and invitations. */
  recipients: (roomId) => call(`/recipients${query({ room_id: roomId })}`),

  recordings: (params = {}) => call(`/recordings${query(params)}`),
  recording: (recordingId) => call(`/recordings/${encode(recordingId)}`),
  createRecording: (payload) => send('/recordings', 'POST', payload),

  /** Place the video on the room's hero section and personalise it per buyer. */
  placeOnHero: (roomId, recordingId, payload) =>
    send(`/rooms/${encode(roomId)}/recordings/${encode(recordingId)}/place`, 'POST', payload),

  /** The buyer surface. With a viewer, that buyer's personalisation and state. */
  hero: (roomId, viewer) => call(`/rooms/${encode(roomId)}/hero${query({ viewer })}`),

  placements: (roomId, status) =>
    call(`/rooms/${encode(roomId)}/placements${query({ status })}`),

  withdrawPlacement: (placementId) => send(`/placements/${encode(placementId)}/withdraw`, 'POST', {}),

  /** Record one per-viewer playback event. The page drives this as the buyer. */
  playback: (recordingId, payload) =>
    send(`/recordings/${encode(recordingId)}/playback`, 'POST', payload),

  events: (roomId, recordingId) =>
    call(`/rooms/${encode(roomId)}/events${query({ recording_id: recordingId })}`),
  engagement: (roomId, recordingId) =>
    call(`/rooms/${encode(roomId)}/engagement${query({ recording_id: recordingId })}`),
  alerts: (roomId, status) => call(`/rooms/${encode(roomId)}/alerts${query({ status })}`),
  notifications: (roomId) => call(`/rooms/${encode(roomId)}/notifications`),
}

/** Every room, so the page can offer one to attach a video to. */
export const listRooms = () => apiRequest('/records/room?limit=100')

/** The words for each recording kind. Served here so the page cannot drift. */
export const KIND_WORDS = {
  personal_intro: 'Personal intro',
  walkthrough: 'Walkthrough',
}

export const SOURCE_WORDS = {
  camera: 'Camera and microphone',
  screen: 'Screen share',
  camera_and_screen: 'Camera and screen',
}

export const EVENT_WORDS = {
  start: 'Started',
  progress: 'Progress',
  complete: 'Completed',
  replay: 'Replayed',
}

export const STATE_WORDS = {
  not_started: 'Not started',
  started: 'Started, not finished',
  completed: 'Completed',
}

/** The tone for each state. A word always accompanies it, so colour never stands alone. */
export const STATE_TONES = {
  not_started: 'neutral',
  started: 'restore',
  completed: 'insert',
}

export const PLACEMENT_WORDS = {
  live: 'Live on the hero',
  superseded: 'Replaced by a newer video',
  withdrawn: 'Withdrawn',
}

/** The sentence for a conflict token, or a neutral one the backend never sent. */
export const CONFLICTS = {
  recording_belongs_to_another_room: 'That video belongs to another room. Record it in this room first.',
  placement_not_live: 'That placement is not live, so it cannot be withdrawn.',
}

export function conflictText(reason) {
  return CONFLICTS[reason] || 'That change was refused.'
}

export function kindLabel(kind) {
  return KIND_WORDS[kind] || kind || 'Recording'
}

export function sourceLabel(source) {
  return SOURCE_WORDS[source] || source || 'Unknown source'
}

export function eventLabel(event) {
  return EVENT_WORDS[event] || event || 'Event'
}

export function stateLabel(state) {
  return STATE_WORDS[state] || state || 'Unknown'
}

export function stateTone(state) {
  return STATE_TONES[state] || 'neutral'
}

export function placementLabel(status) {
  return PLACEMENT_WORDS[status] || status || 'Unknown'
}

/** A percentage as a short integer string, or a dash. */
export function percentText(value) {
  if (value === null || value === undefined || value === '') return '-'
  return `${Math.round(Number(value))}%`
}

/** Format a millisecond timestamp, or a dash. */
export function formatDateTime(ms) {
  if (!ms) return '-'
  const parsed = new Date(Number(ms))
  return Number.isNaN(parsed.getTime()) ? '-' : parsed.toLocaleString()
}
