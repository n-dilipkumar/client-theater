/**
 * WF-069's own API wrapper.
 *
 * `apiRequest` from `@/lib/api` is the transport, exactly as the contract asks: the
 * shared `api` object grows no methods, so a hundred features can each talk to their
 * own `/api/<feature>` routes without anyone editing a shared file.
 *
 * The write and gate-step calls go through `requestWithBody` instead, and that is a
 * finding rather than a preference - the same one WF-015 reported, arriving
 * independently.
 *
 * `apiRequest` reads the error body to build a message and then throws it away, so a
 * failure survives as `status` plus one string. That is not enough for the two
 * responses this workflow exists to produce well:
 *
 *   - a 400 from the settings validator carries `errors`, a field-keyed map, so the
 *     form can put each message beside the input that caused it;
 *   - a 403 from the gate carries `reason`, a stable token, so the buyer page can
 *     tell "that password did not match" from "this link has expired" without
 *     string-matching an English sentence.
 *
 * The shared client would need three fields added to one function, which is a shared
 * file and a platform decision. Until then the calls that need the body read it
 * themselves. Recorded as promotion work, not smuggled across the boundary.
 *
 * Nothing here ever holds a secret. The password is write-only and goes out in a
 * request body; the one-time code arrives by email and is typed by the buyer; the view
 * token comes back once, from the grant, and is not stored by the browser.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-069'

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

export const gateApi = {
  // -- the seller's side ---------------------------------------------------- //

  summary: (roomId) => call(`/summary${query({ room_id: roomId })}`),

  /** The researched vocabulary, so the form cannot drift from the rules. */
  vocabulary: () => call('/vocabulary'),

  presets: () => call('/presets'),
  createPreset: (payload) => send('/presets', 'POST', payload),

  roomLinks: (roomId) => call(`/rooms/${encode(roomId)}/links`),
  readLink: (linkId) => call(`/links/${encode(linkId)}`),

  /** `error.errors` reaches the form. */
  createLink: (roomId, payload) => send(`/rooms/${encode(roomId)}/links`, 'POST', payload),

  /**
   * Tri-state: omit a field to leave it alone, send a boolean to set it, send `null`
   * to clear it. `undefined` is dropped from the body for exactly that reason.
   */
  updateLink: (linkId, changes) => {
    const body = {}
    for (const [key, value] of Object.entries(changes)) {
      if (value !== undefined) body[key] = value
    }
    return send(`/links/${encode(linkId)}`, 'PATCH', body)
  },

  revokeLink: (linkId) => send(`/links/${encode(linkId)}`, 'DELETE', {}),

  views: (roomId, linkId) => call(`/rooms/${encode(roomId)}/views${query({ link_id: linkId })}`),
  notifications: (roomId) => call(`/rooms/${encode(roomId)}/notifications`),

  /** The persisted, verified buyer identity. */
  visitor: (visitorId) => call(`/visitors/${encode(visitorId)}`),

  // -- the buyer's side ----------------------------------------------------- //

  /** Which step the buyer is on. Never 403s: a closed link answers with the page. */
  gate: (linkId) => call(`/links/${encode(linkId)}/gate`),

  /** Says a code went out. Deliberately does not say what the code was. */
  submitEmail: (linkId, email) => send(`/links/${encode(linkId)}/gate/email`, 'POST', { email }),

  submitCode: (linkId, challengeId, code) =>
    send(`/links/${encode(linkId)}/gate/code`, 'POST', { challenge_id: challengeId, code }),

  /** Returns `view_token` once, on the grant. `error.reason` distinguishes refusals. */
  submitPassword: (linkId, password, challengeId) =>
    send(`/links/${encode(linkId)}/gate/password`, 'POST', {
      password,
      challenge_id: challengeId ?? null,
    }),

  document: (linkId, viewToken) => call(`/links/${encode(linkId)}/document${query({ view_token: viewToken })}`),
}

/** Every room, so the page can offer one to gate. Read from the core collection. */
export const listRooms = () => apiRequest('/records/room?limit=100')

/** Documents the core library holds, for the `document_id | dataroom_id` target. */
export const listDocuments = () => apiRequest('/records/document?limit=100')
