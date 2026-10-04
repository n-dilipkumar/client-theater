/**
 * WF-060's own API wrapper.
 *
 * `apiRequest` from `@/lib/api` is the transport, exactly as the contract asks: the
 * shared `api` object grows no methods, so a hundred features can each talk to their
 * own `/api/<feature>` routes without anyone editing a shared file.
 *
 * The write and step calls go through `requestWithBody` instead, and that is a finding
 * rather than a preference - the same one WF-015 and WF-069 reported, arriving
 * independently.
 *
 * `apiRequest` reads the error body to build a message and then throws the rest of it
 * away, so a failure survives as `status` plus one string. That is not enough for the
 * two responses this workflow exists to produce well:
 *
 *   - a 400 from the profile validator carries `errors`, a field-keyed map, so the form
 *     can put each message beside the input that caused it;
 *   - a 409 from the state machine carries `state`, `step` and `allowed`, so the page
 *     can tell "the recording is blocked until somebody consents" from "that step is
 *     not available from here" without string-matching an English sentence.
 *
 * The shared client would need three fields added to one function, which is a shared
 * file and a platform decision. Until then the calls that need the body read it
 * themselves. Recorded as promotion work, not smuggled across the boundary.
 *
 * Nothing here ever holds a secret. This workflow stores consent decisions, not
 * credentials.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-060'

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
    error.errors = body?.errors || null
    error.state = body?.state || null
    error.step = body?.step || null
    error.allowed = body?.allowed || null
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

export const consentApi = {
  // -- the board ----------------------------------------------------------- //

  summary: (roomId) => call(`/summary${query({ room_id: roomId })}`),

  /**
   * The researched vocabulary, so a form cannot drift from the rules.
   *
   * The closed sets the backend validates against are fetched rather than typed here.
   * A dropdown listing three link kinds when the server accepts two is a form that
   * submits something the server will refuse.
   */
  vocabulary: () => call('/vocabulary'),

  /**
   * Every judgement call this workflow made, with what it rejected.
   *
   * Served by the backend rather than written here, because the research requires the
   * derivation to be recorded and a copy of the record on the client is a second copy
   * that can drift from the first.
   */
  decisions: () => call('/decisions'),
  decision: (inferenceId) => call(`/decisions/${encode(inferenceId)}`),

  /** The researched integration-status answer, computed from local state. */
  integrationStatus: () => call('/integration/status'),

  // -- the consent profile ------------------------------------------------- //

  profiles: (roomId) => call(`/profiles${query({ room_id: roomId })}`),
  readProfile: (profileId) => call(`/profiles/${encode(profileId)}`),

  /** `error.errors` reaches the form, so each message lands beside its input. */
  createProfile: (roomId, payload) =>
    send(`/profiles${query({ room_id: roomId })}`, 'POST', payload),

  /**
   * Tri-state: omit a field to leave it alone, send a value to set it, send `null` to
   * clear it. `undefined` is dropped from the body for exactly that reason.
   */
  updateProfile: (profileId, changes) => {
    const body = {}
    for (const [key, value] of Object.entries(changes)) {
      if (value !== undefined) body[key] = value
    }
    return send(`/profiles/${encode(profileId)}`, 'PATCH', body)
  },

  setDefaultProfile: (profileId) => send(`/profiles/${encode(profileId)}/default`, 'POST'),

  /** Step 3's consent page preview, as the buyer would receive it. */
  consentPage: (profileId, baseUrl) =>
    call(`/profiles/${encode(profileId)}/consent-page${query({ base_url: baseUrl })}`),

  // -- the directory ------------------------------------------------------- //

  users: (roomId) => call(`/users${query({ room_id: roomId })}`),
  addUser: (roomId, payload) => send(`/users${query({ room_id: roomId })}`, 'POST', payload),

  /**
   * Which profile applies to an organiser, and how it was found.
   *
   * Resolved on `organizerEmail` because the research says the consent page follows
   * the settings of the user who created the meeting.
   */
  resolveOrganizer: (organizerEmail) => call(`/users/resolve${query({ organizer_email: organizerEmail })}`),

  /** Whether an organiser's own settings permit recording, asked before booking. */
  canRecord: (organizerEmail) => call(`/organizers/${encode(organizerEmail)}/can-record`),

  // -- the booking ---------------------------------------------------------- //

  bookings: (roomId) => call(`/bookings${query({ room_id: roomId })}`),
  readBooking: (bookingId) => call(`/bookings/${encode(bookingId)}`),

  /** Issue a consent-enabled link and record the state the profile implies. */
  openBooking: (roomId, payload) => send(`/bookings${query({ room_id: roomId })}`, 'POST', payload),

  /**
   * The participant's answer, or the bot joining without one.
   *
   * The third value is only admitted where the profile allows it, and where it is the
   * recording is cancelled.
   */
  recordConsent: (bookingId, decision) =>
    send(`/bookings/${encode(bookingId)}/consent${query({ decision })}`, 'POST'),

  /** Refused from a blocked recording, which is what makes the gate real. */
  startRecording: (bookingId) => send(`/bookings/${encode(bookingId)}/recording/start`, 'POST'),
  finishRecording: (bookingId) => send(`/bookings/${encode(bookingId)}/recording/finish`, 'POST'),
  cancelRecording: (bookingId) => send(`/bookings/${encode(bookingId)}/recording/cancel`, 'POST'),

  /**
   * The link that currently joins the call.
   *
   * Pass `meetingId` to ask whether an invite is still current. A 409 means the link in
   * that invite was replaced, which is a different problem from a booking that has no
   * link at all.
   */
  currentLink: (bookingId, meetingId) =>
    call(`/bookings/${encode(bookingId)}/link${query({ meeting_id: meetingId })}`),

  /** Change the link, disabling the previous one as a state rather than a delete. */
  reissueLink: (bookingId) => send(`/bookings/${encode(bookingId)}/link`, 'POST'),

  /** The recording lifecycle for one booking, oldest first. */
  runs: (bookingId) => call(`/bookings/${encode(bookingId)}/runs`),

  /**
   * Send the pre-call email if the window is open.
   *
   * Always 200. A window that has not opened is not a failure, so the answer carries
   * the reason rather than an error status.
   */
  planPrecallEmail: (bookingId, vars = {}) =>
    send(`/bookings/${encode(bookingId)}/precall-email${query(vars)}`, 'POST'),

  emails: (bookingId) => call(`/bookings/${encode(bookingId)}/emails`),

  /** What the participant is shown. Never 403s, so a closed booking still answers. */
  participantPage: (bookingId) => call(`/consent/${encode(bookingId)}`),
}

/** Every room, so the page can scope the board. Read from the core collection. */
export const listRooms = () => apiRequest('/records/room?limit=100')