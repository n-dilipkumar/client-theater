/**
 * WF-070's own API wrapper.
 *
 * `apiRequest` from `@/lib/api` is the transport, exactly as the contract asks: the
 * shared `api` object grows no methods, so a hundred features can each talk to their
 * own `/api/<feature>` routes without anyone editing a shared file.
 *
 * The write calls and the viewer calls go through `requestWithBody` instead, and
 * that is a finding rather than a preference - the same one WF-015 and WF-069
 * reported independently.
 *
 * `apiRequest` reads the error body to build a message and then throws it away, so a
 * failure survives as `status` plus one string. That is not enough for the two
 * responses this workflow exists to produce well:
 *
 *   - a 400 from the validator carries `errors`, a field-keyed map, so the form can
 *     put each message beside the input that caused it;
 *   - a 403 from the gate carries `reason`, a stable token, so the viewer page can
 *     tell "you have not accepted yet" from "this link has expired" without
 *     string-matching an English sentence.
 *
 * The shared client would need three fields added to one function, which is a shared
 * file and a platform decision. Until then the calls that need the body read it
 * themselves. Recorded as promotion work, not smuggled across the boundary.
 *
 * Nothing here ever holds a secret. The viewer session token comes back once, from
 * the gate, and is passed straight back to `accept` or `content`. It is never
 * written to storage by this module.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-070'

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

export const ndaApi = {
  // -- the seller's side ---------------------------------------------------- //

  summary: (roomId) => call(`/summary${query({ room_id: roomId })}`),

  /** The researched vocabulary, including what was deliberately not built. */
  vocabulary: () => call('/vocabulary'),

  /** No bodies. A list view needs titles, versions and whether a link can use it. */
  agreements: (roomId) => call(`/agreements${query({ room_id: roomId })}`),

  /** The full text. This is what a viewer is shown. */
  agreement: (agreementId) => call(`/agreements/${encode(agreementId)}`),

  /** `error.errors` reaches the form. */
  createAgreement: (roomId, payload) => send(`/rooms/${encode(roomId)}/agreements`, 'POST', payload),

  /** Changing the text moves the version and re-opens the gate. */
  updateAgreement: (agreementId, changes) =>
    send(`/agreements/${encode(agreementId)}`, 'PATCH', changes),

  /**
   * Retires an NDA. The response lists the gates that now fail closed, so the seller
   * can decide per link whether to point elsewhere or stop asking.
   */
  retireAgreement: (agreementId) => send(`/agreements/${encode(agreementId)}`, 'DELETE', {}),

  roomGates: (roomId) => call(`/rooms/${encode(roomId)}/gates`),
  linkGate: (linkId) => call(`/links/${encode(linkId)}/agreement`),

  /**
   * Tri-state: omit a field to leave it alone, send a boolean or `on`/`off` to set
   * it, send `null` to clear the agreement id. `undefined` is dropped from the body
   * for exactly that reason.
   *
   * Sending `agreement` alone is the CLI's convenience flag and does both jobs:
   * it enables the gate and sets the agreement id.
   */
  setLinkGate: (linkId, changes) => {
    const body = {}
    for (const [key, value] of Object.entries(changes)) {
      if (value !== undefined) body[key] = value
    }
    return send(`/links/${encode(linkId)}/agreement`, 'PATCH', body)
  },

  acceptances: (roomId, linkId) =>
    call(`/rooms/${encode(roomId)}/acceptances${query({ link_id: linkId })}`),

  sessions: (roomId, linkId) =>
    call(`/rooms/${encode(roomId)}/sessions${query({ link_id: linkId })}`),

  // -- the viewer's side ----------------------------------------------------- //

  /**
   * The viewer's first request. Returns the NDA and a viewer session, or the closed
   * wording for an expired or revoked link.
   *
   * Deliberately `requestWithBody`, not the shared client. This is the one call that
   * most needs the body: a closed link answers 403 with a `reason`, and `apiRequest`
   * reads the body to build a message and then throws it away. Going through it here
   * meant the closed page rendered a generic "This link could not be opened." instead
   * of the researched sentence, which is the one piece of copy that must not be
   * vague. A test caught it.
   */
  gate: (linkId) => requestWithBody(`/links/${encode(linkId)}/gate`),

  /**
   * Records the acceptance. `accepted` must be true: arriving at the route is not
   * consent. `error.reason` distinguishes "not accepted" from "the text changed".
   */
  accept: (linkId, sessionId, email) =>
    send(`/links/${encode(linkId)}/gate/agreement`, 'POST', {
      session_id: sessionId,
      accepted: true,
      email: email ?? null,
    }),

  /**
   * The room content, and only to a viewer whose acceptance still covers the
   * agreement. `remaining_gates` names the gates this workflow does not own.
   */
  content: (linkId, sessionId) =>
    call(`/links/${encode(linkId)}/content${query({ session_id: sessionId })}`),
}

/** Every room, so the page can offer one to attach an NDA to. */
export const listRooms = () => apiRequest('/records/room?limit=100')

/** Buyer links, so the page can offer one to gate. WF-069 owns them. */
export const listLinks = (roomId) =>
  apiRequest(`/records/wf069_gated_link${query({ room_ref: roomId, limit: 100 })}`)

/**
 * The reason tokens the gate refuses with, and what a viewer can do about each.
 *
 * Served here rather than imported from the backend so the page cannot drift from
 * the rules. The sentences are the backend's: a viewer reads one message per
 * refusal, and a page that words it differently from the API is a page that
 * misleads.
 */
export const REFUSALS = {
  agreement_not_accepted: 'Read the agreement and accept it to continue.',
  viewer_session_required: 'Open the link to start a pass through the gate.',
  viewer_session_unknown: 'Open the link again to start a new pass through the gate.',
  agreement_changed: 'The agreement changed. Read the current version and accept it.',
  agreement_unavailable: 'The agreement for this link is not available. Ask the sender.',
  link_closed: 'This link has expired. Ask the sender for a new one.',
}

/** The sentence for a refusal token, or a neutral one the backend never sent. */
export function refusalText(reason) {
  return REFUSALS[reason] || 'This link could not be opened.'
}