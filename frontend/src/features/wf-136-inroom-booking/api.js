/**
 * WF-136's own API wrapper.
 *
 * `apiRequest` from `@/lib/api` is the transport, exactly as the contract asks:
 * the shared `api` object grows no methods, so a hundred features can each talk
 * to their own `/api/wf-136` routes without anyone editing a shared file.
 *
 * The write calls go through `requestWithBody` instead, and that is a finding
 * rather than a preference, the same one WF-124 records. `apiRequest` reads the
 * error body to build a message and then throws it away, so a failure survives
 * as `status` plus one string. Two responses in this workflow need more:
 *
 *   - a 400 from the proposal validator carries `errors`, a field-keyed map, so
 *     a form can put each message beside the input that caused it;
 *   - a 409 from the accept carries `reason`, a stable token, so the page can say
 *     "that time has gone" instead of printing an English sentence and hoping
 *     the server did not reword it.
 *
 * The shared client would need more fields on one function, which is a shared
 * file and a platform decision. Until then the calls that need the body read it
 * themselves. Recorded as promotion work, not smuggled across the boundary.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-136'

function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

/** The shared client. Fine for everything whose failure is just a failure. */
function call(path, options) {
  return apiRequest(`${BASE}${path}`, options)
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
    error.reason = body?.reason || null
    error.errors = body?.errors || null
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

export const bookingApi = {
  summary: (roomId) => call(`/summary${query({ room_id: roomId })}`),
  vocabulary: () => call('/vocabulary'),

  proposals: (roomId) => call(`/rooms/${encode(roomId)}/proposals`),
  createProposal: (roomId, payload) => send(`/rooms/${encode(roomId)}/proposals`, 'POST', payload),
  proposal: (proposalId) => call(`/proposals/${encode(proposalId)}`),
  slots: (proposalId) => call(`/proposals/${encode(proposalId)}/slots`),
  accept: (proposalId, payload) => send(`/proposals/${encode(proposalId)}/accept`, 'POST', payload),
  decline: (proposalId, payload) =>
    send(`/proposals/${encode(proposalId)}/decline`, 'POST', payload),
  autoPropose: (roomId) => send(`/rooms/${encode(roomId)}/auto-propose`, 'POST', {}),

  meetings: (roomId) => call(`/rooms/${encode(roomId)}/meetings`),
  attachRecording: (meetingId, payload) =>
    send(`/meetings/${encode(meetingId)}/recording`, 'POST', payload),

  nextSteps: (roomId) => call(`/rooms/${encode(roomId)}/next-steps`),
}

/** Every room, so the page can offer one to propose a next step in. */
export const listRooms = () => apiRequest('/records/room?limit=100')

/**
 * The words for each proposal state, so a state is never carried by colour alone.
 *
 * `expired` is derived by the server from the proposal's clock, not stored by a
 * scheduler: the page says so in a hint, because a seller who sees an old
 * proposal lapse without anyone acting would otherwise wonder who expired it.
 */
export const STATE_WORDS = {
  proposed: 'Awaiting the buyer',
  accepted: 'Accepted',
  booked: 'Booked',
  declined: 'Declined',
  expired: 'Lapsed',
}

/** The tone for each state. A word always accompanies it. */
export const STATE_TONES = {
  proposed: 'info',
  accepted: 'success',
  booked: 'success',
  declined: 'neutral',
  expired: 'warning',
}

/** The sentence for a refusal token, from the stable reason the server returns. */
export const REASONS = {
  proposal_invalid: 'That next step is missing something. Check the fields above.',
  proposal_expired: 'That next step lapsed before it was answered. Ask the seller again.',
  proposal_answered: 'That next step was already answered.',
  slot_not_offered: 'That time has gone. Pick another slot.',
  start_required: 'Pick a time before you book.',
  attendee_required: 'Add the name and email of the person being booked.',
  room_not_bookable: 'This room has no bookable calendar yet.',
  room_has_no_embed: 'This room has no bookable calendar installed yet.',
  booking_refused: 'The room refused that booking. Try another time.',
  recording_not_attached: 'A recording is attached by an http or https link.',
  next_step_task_not_found: 'That task is not on this room action plan.',
  meeting_not_found: 'That meeting does not exist.',
  proposal_not_found: 'That next step does not exist.',
}

/** The sentence for a refusal token, or a neutral one the backend never sent. */
export function refusalText(reason) {
  return REASONS[reason] || 'That request was refused.'
}
