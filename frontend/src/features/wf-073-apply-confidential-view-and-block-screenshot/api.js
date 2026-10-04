/**
 * WF-073's own API wrapper.
 *
 * `apiRequest` from `@/lib/api` is the transport, exactly as the contract asks: the
 * shared `api` object grows no methods, so a hundred features can each talk to their
 * own `/api/<feature>` routes without anyone editing a shared file.
 *
 * The write calls go through `requestWithBody` instead, and that is a finding rather
 * than a preference - the same one WF-069 reported, arriving independently.
 *
 * `apiRequest` reads the error body to build a message and then throws it away, so a
 * failure survives as `status` plus one string. That is not enough for the two
 * responses this workflow exists to produce well:
 *
 *   - a 400 from the settings validator carries `errors`, a field-keyed map, so the
 *     access-controls panel can put each message beside the switch that caused it;
 *   - a 422 from the capture report carries `known_shortcuts`, so a viewer who pressed
 *     a combination this build does not know is told what it does know rather than
 *     being left with a bare error.
 *
 * The shared client would need two more fields on one function, which is a shared file
 * and a platform decision. Until then the calls that need the body read it themselves.
 * Recorded as promotion work, not smuggled across the boundary.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-073'

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
    const error = new Error(body?.detail || body?.error || `${response.status} ${response.statusText}`)
    error.status = response.status
    error.code = body?.error || null
    error.errors = body?.errors || null
    error.knownShortcuts = body?.known_shortcuts || null
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

export const confidentialApi = {
  // -- the board ----------------------------------------------------------- //

  summary: (roomId) => call(`/summary${query({ room_id: roomId })}`),

  /**
   * The researched vocabulary, so the panel cannot drift from the rules that validate
   * it. The two flags, their defaults, the vendor's own descriptions, the CLI flag
   * names, the capture shortcuts with a `blockable` flag on each, and the focus-band
   * arithmetic all come from one place on the server.
   */
  vocabulary: () => call('/vocabulary'),

  /**
   * Every judgement call this workflow made, with the alternative it rejected. Served
   * rather than hard-coded here, because the specification requires the derivation to
   * be recorded and a copy in this file would be a second record to keep in step.
   */
  decisions: () => call('/decisions'),

  // -- governance baselines ------------------------------------------------ //

  presets: () => call('/presets'),
  createPreset: (payload) => send('/presets', 'POST', payload),

  // -- the access-controls panel ------------------------------------------- //

  links: (roomId) => call(`/links${query({ room_id: roomId })}`),

  /** `error.errors` reaches the panel, field by field. */
  createLink: (roomId, payload) => send(`/rooms/${encode(roomId)}/links`, 'POST', payload),

  /**
   * Tri-state, and the difference matters: omit a field to leave it alone, send a
   * boolean to set it, send `null` to return it to the documented default. `undefined`
   * is dropped from the body for exactly that reason, so a switch that only changes
   * one control never resets the other.
   */
  updateLink: (linkId, changes) => {
    const body = {}
    for (const [key, value] of Object.entries(changes)) {
      if (value !== undefined) body[key] = value
    }
    return send(`/links/${encode(linkId)}`, 'PATCH', body)
  },

  // -- the buyer's side ---------------------------------------------------- //

  /**
   * Which band of one page is sharp for one viewport. Never refuses: confidential view
   * is a rendering transformation, not a gate, so there is no credential here and
   * nothing for it to reject.
   */
  render: (linkId, viewport) =>
    call(`/links/${encode(linkId)}/render${query({ page_height: viewport.pageHeight, viewport_height: viewport.viewportHeight, viewport_top: viewport.viewportTop })}`),

  /** `error.knownShortcuts` reaches the viewer when a combination is not recognised. */
  reportAttempt: (linkId, keys) =>
    send(`/links/${encode(linkId)}/attempts`, 'POST', { keys }),

  attempts: (roomId, linkId) =>
    call(`/attempts${query({ room_id: roomId, link_id: linkId })}`),
}

/** Every room, so the panel can offer one to govern a link on. Read from the core collection. */
export const listRooms = () => apiRequest('/records/room?limit=100')

/**
 * The word this workflow is measured by.
 *
 * The specification says screenshot blocking "is largely unenforceable from a browser;
 * treat as deterrence, and do not sell it as protection", so the one piece of copy this
 * feature owns outright is the sentence that says so. It is exported rather than
 * written into the page because a test imports it and a test that imports a literal
 * checks nothing.
 */
export const DETERRENT_NOT_PROTECTION =
  'Deterrence, not protection. Both controls raise the cost of an ordinary capture. Neither one prevents it.'

/**
 * The capture shortcuts this build knows, and what it can do about each.
 *
 * Mirrors what `GET /wf-073/vocabulary` serves. Written here as well so the board can
 * render the list while the vocabulary request is still in flight, and so the page has
 * a defined value to render in the loading and error states rather than a blank card.
 * The server's answer replaces it as soon as it arrives, and a test asserts the two
 * agree, so this cannot quietly become a second source of truth.
 */
export const KNOWN_SHORTCUTS = [
  { name: 'Command-Shift-3', action: 'capture_fullscreen', blockable: true },
  { name: 'Command-Shift-4', action: 'capture_selection', blockable: true },
  { name: 'Command-Shift-5', action: 'capture_toolbar', blockable: true, alsoRecording: true },
  { name: 'Control-Shift-S', action: 'capture_selection', blockable: true },
  { name: 'Command-Alt-R', action: 'start_recording', blockable: true },
  { name: 'Print Screen', action: 'capture_fullscreen', blockable: false },
]

/** How many of the named shortcuts a web page can intercept, and which it cannot. */
export function shortcutScope() {
  return {
    total: KNOWN_SHORTCUTS.length,
    blockable: KNOWN_SHORTCUTS.filter((s) => s.blockable).length,
    unblockable: KNOWN_SHORTCUTS.filter((s) => !s.blockable).map((s) => s.name),
  }
}