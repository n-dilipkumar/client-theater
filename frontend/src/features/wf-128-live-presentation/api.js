/**
 * WF-128's own API wrapper.
 *
 * `apiRequest` from `@/lib/api` is the transport, exactly as the contract asks:
 * the shared `api` object grows no methods, so a hundred features can each talk
 * to their own `/api/wf-128` routes without anyone editing a shared file.
 *
 * The write calls go through `requestWithBody` instead, and that is a finding
 * rather than a preference. `apiRequest` reads the error body to build a message
 * and then throws it away, so a failure survives as `status` plus one string.
 * Two responses in this workflow need more than that:
 *
 *   - a 422 from the validator carries `errors`, a field-keyed map, so a form can
 *     put each message beside the input that caused it;
 *   - a 409 from the engine carries `reason`, a stable token, so the page can say
 *     "a step cannot be revealed before the one before it" instead of printing an
 *     English sentence and hoping the server did not reword it.
 *
 * The shared client would need two more fields on one function, which is a shared
 * file and a platform decision. Until then the calls that need the body read it
 * themselves. Recorded as promotion work, not smuggled across the boundary.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-128'

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
  return requestWithBody(path, { method, body: JSON.stringify(payload ?? {}) })
}

const encode = encodeURIComponent

export const sessionApi = {
  /** The researched vocabulary, including what was deliberately not built. */
  vocabulary: () => call('/vocabulary'),

  summary: (roomId) => call(`/summary${query({ room_id: roomId })}`),

  /** The room's buyers, reused from WF-004's grants and invitations. */
  recipients: (roomId) => call(`/recipients${query({ room_id: roomId })}`),

  sessions: (roomId, state) => call(`/sessions${query({ room_id: roomId, state })}`),
  session: (sessionId) => call(`/sessions/${encode(sessionId)}`),
  createSession: (payload) => send('/sessions', 'POST', payload),

  startSession: (sessionId, payload) =>
    send(`/sessions/${encode(sessionId)}/start`, 'POST', payload),
  endSession: (sessionId, payload) => send(`/sessions/${encode(sessionId)}/end`, 'POST', payload),
  reveal: (sessionId, payload) => send(`/sessions/${encode(sessionId)}/reveal`, 'POST', payload),

  state: (sessionId, viewer) =>
    call(`/sessions/${encode(sessionId)}/state${query({ viewer })}`),
  sync: (sessionId, payload) => send(`/sessions/${encode(sessionId)}/sync`, 'POST', payload),

  invite: (sessionId, payload) => send(`/sessions/${encode(sessionId)}/invite`, 'POST', payload),
  participants: (sessionId, status) =>
    call(`/sessions/${encode(sessionId)}/participants${query({ status })}`),
  events: (sessionId) => call(`/sessions/${encode(sessionId)}/events`),

  questions: (sessionId, status) =>
    call(`/sessions/${encode(sessionId)}/questions${query({ status })}`),
  askQuestion: (sessionId, payload) =>
    send(`/sessions/${encode(sessionId)}/questions`, 'POST', payload),
  answerQuestion: (questionId, payload) =>
    send(`/questions/${encode(questionId)}/answer`, 'POST', payload),

  attendance: (sessionId) => call(`/sessions/${encode(sessionId)}/attendance`),
  recordings: (sessionId) => call(`/sessions/${encode(sessionId)}/recordings`),
  notifications: (sessionId) => call(`/sessions/${encode(sessionId)}/notifications`),
}

/** Every room, so the page can offer one to start a session from. */
export const listRooms = () => apiRequest('/records/room?limit=100')

export const SESSION_STATE_WORDS = {
  scheduled: 'Scheduled, not started',
  live: 'Live',
  ended: 'Ended',
}

/** The tone for each state. A word always accompanies it, so colour never stands alone. */
export const SESSION_STATE_TONES = {
  scheduled: 'neutral',
  live: 'insert',
  ended: 'update',
}

export const PARTICIPANT_WORDS = {
  presenter: 'Presenter',
  follower: 'Follower',
}

export const PARTICIPANT_STATUS_WORDS = {
  invited: 'Invited',
  joined: 'Following',
  left: 'Left',
}

export const QUESTION_WORDS = {
  open: 'Open',
  answered: 'Answered',
}

export const ATTENDANCE_WORDS = {
  attended: 'Attended',
  invited_only: 'Invited, did not join',
}

export const RECORDING_WORDS = {
  recording: 'Recording now',
  published: 'Published as a replay',
}

export const EVENT_WORDS = {
  session_started: 'Session started',
  session_ended: 'Session ended',
  participant_invited: 'Invitee added',
  participant_joined: 'Participant joined',
  participant_left: 'Participant left',
  step_revealed: 'Step revealed',
  step_back: 'Stepped back',
  question_asked: 'Question asked',
  question_answered: 'Question answered',
  recording_published: 'Replay published',
  access_link_issued: 'Access link issued',
}

/** The sentence for a conflict token, or a neutral one the backend never sent. */
export const CONFLICTS = {
  not_the_presenter: 'Only the presenter drives the session.',
  reveal_out_of_order: 'A step cannot be revealed before the step before it.',
  step_out_of_range: 'That step is not on the agenda.',
  session_not_live: 'Start the session before revealing a step.',
  session_already_started: 'That session is already live.',
  session_already_ended: 'That session has ended and cannot start again.',
  follower_cannot_control: 'A follower cannot drive the session.',
  second_presenter_refused: 'A session has exactly one presenter.',
  question_already_answered: 'That question is already answered.',
  participant_already_left: 'That participant already left.',
}

export function conflictText(reason) {
  return CONFLICTS[reason] || 'That change was refused.'
}

export function sessionStateLabel(state) {
  return SESSION_STATE_WORDS[state] || state || 'Unknown'
}

export function sessionStateTone(state) {
  return SESSION_STATE_TONES[state] || 'neutral'
}

export function participantRoleLabel(role) {
  return PARTICIPANT_WORDS[role] || role || 'Participant'
}

export function participantStatusLabel(status) {
  return PARTICIPANT_STATUS_WORDS[status] || status || 'Unknown'
}

export function questionLabel(status) {
  return QUESTION_WORDS[status] || status || 'Question'
}

export function attendanceLabel(state) {
  return ATTENDANCE_WORDS[state] || state || 'Attendance'
}

export function recordingLabel(state) {
  return RECORDING_WORDS[state] || state || 'Recording'
}

export function eventLabel(kind) {
  return EVENT_WORDS[kind] || kind || 'Event'
}

/** Format a millisecond timestamp, or a dash. */
export function formatDateTime(ms) {
  if (!ms && ms !== 0) return '-'
  const parsed = new Date(Number(ms))
  return Number.isNaN(parsed.getTime()) ? '-' : parsed.toLocaleString()
}

/** Format a second count, or a dash. */
export function formatSeconds(value) {
  if (value === null || value === undefined || value === '') return '-'
  const total = Math.max(0, Math.round(Number(value)))
  const minutes = Math.floor(total / 60)
  const seconds = total % 60
  return `${minutes}m ${String(seconds).padStart(2, '0')}s`
}
