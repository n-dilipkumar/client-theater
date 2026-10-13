/**
 * WF-122's own API wrapper.
 *
 * `apiRequest` from `@/lib/api` is the transport, exactly as the contract asks:
 * the shared `api` object grows no methods, so a hundred features can each talk
 * to their own `/api/wf-122` routes without anyone editing a shared file.
 *
 * The write calls go through `requestWithBody` instead, and that is a finding
 * rather than a preference. `apiRequest` reads the error body to build a message
 * and then throws it away, so a failure survives as `status` plus one string.
 * Two responses in this workflow need more than that:
 *
 *   - a 400 from the validator carries `errors`, a field-keyed map, so a form can
 *     put each message beside the input that caused it;
 *   - a 409 from a revoked or unverified link carries `reason`, a stable token,
 *     so the page can say "this link was revoked" instead of printing an English
 *     sentence and hoping the server did not reword it.
 *
 * The shared client would need three more fields on one function, which is a
 * shared file and a platform decision. Until then the calls that need the body
 * read it themselves. Recorded as promotion work, not smuggled across the
 * boundary.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-122'

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
  return requestWithBody(path, { method, body: JSON.stringify(payload ?? {}) })
}

const encode = encodeURIComponent

export const tiersApi = {
  // -- the board ------------------------------------------------------------ //

  summary: (roomId) => call(`/summary${query({ room_id: roomId })}`),

  /**
   * The researched vocabulary and the decisions behind it.
   *
   * The page reads its states from here rather than hard-coding them, so a change
   * to the rules cannot leave the page calling a state the API does not serve.
   */
  vocabulary: () => call('/vocabulary'),

  // -- the room tree -------------------------------------------------------- //

  policy: (roomId) => call(`/rooms/${encode(roomId)}/policy`),
  setPolicy: (roomId, payload) => send(`/rooms/${encode(roomId)}/policy`, 'PUT', payload),
  sections: (roomId) => call(`/rooms/${encode(roomId)}/sections`),
  createSection: (roomId, payload) => send(`/rooms/${encode(roomId)}/sections`, 'POST', payload),
  updateSection: (sectionId, changes) =>
    send(`/sections/${encode(sectionId)}`, 'PATCH', changes),
  deleteSection: (sectionId) => send(`/sections/${encode(sectionId)}`, 'DELETE'),

  /**
   * The room as a viewer at one tier sees it. The sections this omits are absent
   * from the response, not hidden by the page.
   */
  preview: (roomId, tier) => call(`/rooms/${encode(roomId)}/view${query({ tier })}`),

  // -- counterparty links --------------------------------------------------- //

  recipients: (roomId) => call(`/rooms/${encode(roomId)}/recipients`),
  createRecipient: (roomId, payload) => send(`/rooms/${encode(roomId)}/recipients`, 'POST', payload),
  updateRecipient: (accessId, changes) => send(`/recipients/${encode(accessId)}`, 'PATCH', changes),

  /**
   * Raises a link's tier. The server writes and reads back before it reports
   * success, so a 409 here means the change did not land, not that the request
   * was wrong.
   */
  unlock: (accessId, tier) => send(`/recipients/${encode(accessId)}/unlock`, 'POST', { tier }),
  revoke: (accessId) => send(`/recipients/${encode(accessId)}/revoke`, 'POST'),
  rotate: (accessId) => send(`/recipients/${encode(accessId)}/rotate`, 'POST'),
  recipientView: (accessId) => call(`/recipients/${encode(accessId)}/view`),

  // -- section-level access ------------------------------------------------- //

  grant: (sectionId, recipientId) =>
    send(`/sections/${encode(sectionId)}/grant`, 'POST', { recipient_id: recipientId }),
  requestAccess: (sectionId, recipientId, note) =>
    send(`/sections/${encode(sectionId)}/request`, 'POST', {
      recipient_id: recipientId,
      note,
    }),

  requests: (roomId) => call(`/rooms/${encode(roomId)}/requests`),
  events: (roomId) => call(`/rooms/${encode(roomId)}/events`),
}

/** Every room, so the page can offer one to attach a tier policy to. */
export const listRooms = () => apiRequest('/records/room?limit=100')

/** The words for each disclosure, so nothing is carried by colour alone. */
export const DISCLOSURE_WORDS = {
  full: 'Full',
  teaser: 'Teaser',
  on_request: 'On request',
  hidden: 'Hidden',
}

export const DISCLOSURE_TONES = {
  full: 'success',
  teaser: 'warning',
  on_request: 'info',
  hidden: 'neutral',
}

/** The words for each link state. */
export const STATE_WORDS = {
  active: 'Active',
  revoked: 'Revoked',
  expired: 'Expired',
}

export const STATE_TONES = {
  active: 'success',
  revoked: 'destructive',
  expired: 'warning',
}

/** The sentence for a conflict, from the stable reason token. */
export const CONFLICTS = {
  link_revoked: 'This link was revoked. It discloses nothing.',
  link_expired: 'This link expired. It discloses nothing.',
  write_not_visible: 'The server wrote the change but could not read it back, so it refused it.',
}

export function conflictText(reason) {
  return CONFLICTS[reason] || 'That change was refused.'
}

/** The sentence for a below-tier state, so a preview explains itself. */
export const BELOW_TIER_NOTES = {
  teaser: 'Below-tier viewers see the teaser text and nothing else.',
  on_request: 'Below-tier viewers see an "available on request" marker.',
  hidden: 'Below-tier viewers see nothing at all: the section is absent.',
}
