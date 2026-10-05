/**
 * WF-083's own API wrapper.
 *
 * `apiRequest` from `@/lib/api` is the transport, exactly as the contract asks: the shared
 * `api` object grows no methods, so a hundred features can each talk to their own
 * `/api/<feature>` routes without anyone editing a shared file.
 *
 * Reads outnumber writes here, and that is the shape the workflow has. The board, the
 * vocabulary, the decisions, the recordings, the visits and the retention schedule are all
 * reads. The writes are the blocking changes, and every one of those takes a role, because
 * the specification says IP exclusion is an administrator action.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-083'

function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const text = search.toString()
  return text ? `?${text}` : ''
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
    error.body = body
    throw error
  }

  if (response.status === 204) return null
  return response.json()
}

function call(path) {
  return apiRequest(`${BASE}${path}`)
}

function send(path, method, payload) {
  return requestWithBody(path, { method, body: JSON.stringify(payload) })
}

const encode = encodeURIComponent

export const consentApi = {
  // -- the board and the research --------------------------------------------

  /** The headline numbers, read back from the store. */
  summary: (roomId) => call(`/summary${query({ room_id: roomId })}`),

  /**
   * The researched vocabulary, so the page cannot drift from the rules behind it: the two
   * consent axes, the three masking modes, the two retention windows, the five-label cap
   * and the two link kinds all come from the same tables the engine reads.
   */
  vocabulary: () => call('/vocabulary'),

  /** Every judgement call this workflow made, with the alternative it rejected. */
  decisions: () => call('/decisions'),

  // -- the project and its gate ----------------------------------------------

  /**
   * The recording project: its masking mode, its consent gate and its enforcement state.
   *
   * Answers an empty object on a room with no project, so the page renders an empty state
   * rather than an error.
   */
  project: (roomId) => call(`/project${query({ room_id: roomId })}`),

  /** Create a project with the gate on and masking at its documented default. */
  createProject: (payload, { roomId } = {}) =>
    send(`/project${query({ room_id: roomId })}`, 'POST', payload),

  /** Change the masking mode, which decides what leaves the room. Administrator-only. */
  setMasking: (maskingMode, selectors, { roomId, role, actor } = {}) =>
    send(`/project/masking${query({ room_id: roomId, role, actor })}`, 'PUT', {
      masking_mode: maskingMode,
      masking_selectors: selectors,
    }),

  // -- IP exclusion -----------------------------------------------------------

  /** Every blocked range on the project. */
  ipBlocks: (roomId) => call(`/ip-blocks${query({ room_id: roomId })}`),

  /**
   * Add an IPv4 address or range. Administrator-only.
   *
   * An IPv6 range is a 400 whose message names the reason, so the page can show it beside
   * the input rather than reporting a silent no-op the operator would read as a working
   * block.
   */
  blockIp: (cidr, { roomId, role, actor } = {}) =>
    send(`/ip-blocks${query({ room_id: roomId, role, actor })}`, 'POST', { cidr }),

  /** Remove one range. Administrator-only. */
  unblockIp: (rangeId, { roomId, role, actor } = {}) =>
    send(`/ip-blocks/${encode(rangeId)}${query({ room_id: roomId, role, actor })}`, 'DELETE'),

  // -- consent and ingest -----------------------------------------------------

  /**
   * Record one `consentv2` call.
   *
   * Not administrator-gated, because the specification says consent is captured at the
   * page. The response carries the outcome, the identity kind and, on a denial, the
   * vendor's own revoke call.
   */
  recordConsent: (payload, { roomId } = {}) =>
    send(`/consent${query({ room_id: roomId })}`, 'POST', payload),

  /**
   * Decide what happens to one visit.
   *
   * The response carries `recorded`, the `state`, and a reason when the visit was refused.
   * A refusal with no reason is indistinguishable from a bug, so the page renders the
   * reason rather than a bare "not recorded".
   */
  ingest: (payload, { roomId } = {}) => send(`/ingest${query({ room_id: roomId })}`, 'POST', payload),

  /**
   * Every visit the project has seen, including the ones it refused to record.
   *
   * The blocked visits are in this list on purpose: they hold no session, only the matched
   * range and the vendor's console message.
   */
  visits: (roomId) => call(`/visits${query({ room_id: roomId })}`),

  // -- recordings -------------------------------------------------------------

  /** The recordings list, optionally filtered to a segment. */
  recordings: (roomId, { dimension, value } = {}) =>
    call(`/recordings${query({ room_id: roomId, dimension, value })}`),

  /** One recording, including its masked frame and its labels. */
  recording: (recordingId) => call(`/recordings/${encode(recordingId)}`),

  /** Mark a recording a favourite, which moves it onto the nine-month window. */
  markFavourite: (recordingId, favourite, { roomId } = {}) =>
    send(`/recordings/${encode(recordingId)}/favourite${query({ room_id: roomId })}`, 'POST', {
      favourite,
    }),

  /**
   * Add labels to a recording.
   *
   * The sixth is a 400 whose message names the label and the cap, so the page shows it
   * beside the input rather than in a banner at the top.
   */
  addLabels: (recordingId, labels, { roomId } = {}) =>
    send(`/recordings/${encode(recordingId)}/labels${query({ room_id: roomId })}`, 'POST', { labels }),

  /**
   * Ask to delete one recording.
   *
   * This route exists so the refusal can be shown rather than described. The
   * specification says a single recording cannot be deleted, so the response is a 409
   * carrying both evidence sentences and the supported alternative.
   */
  deleteRecording: (recordingId) => send(`/recordings/${encode(recordingId)}`, 'DELETE'),

  /** Delete every recording, visit, label and link on the project. Administrator-only. */
  purgeProject: ({ roomId, role, actor } = {}) =>
    send(`/project/purge${query({ room_id: roomId, role, actor })}`, 'POST', {}),

  // -- share links ------------------------------------------------------------

  /** Every share link, each marked live or expired. */
  shareLinks: (roomId) => call(`/share-links${query({ room_id: roomId })}`),

  /**
   * Share one recording.
   *
   * A guest link gets a window and a team link never expires. A window supplied for a team
   * link is a 400 rather than a discarded value.
   */
  createShareLink: (payload, { roomId } = {}) =>
    send(`/share-links${query({ room_id: roomId })}`, 'POST', payload),

  // -- retention --------------------------------------------------------------

  /** Every recording with the window that applies to it and whether it has passed. */
  retention: (roomId) => call(`/retention${query({ room_id: roomId })}`),

  /** Age out every recording whose window has passed. Administrator-only. */
  runRetention: ({ roomId, role, actor } = {}) =>
    send(`/retention/run${query({ room_id: roomId, role, actor })}`, 'POST', {}),
}

// --------------------------------------------------------------------------- //
// Vocabulary the page renders
// --------------------------------------------------------------------------- //

/**
 * The four answers a consent call can produce, and what each one means.
 *
 * A `signal` is its own answer rather than a flavour of `denied`, because the user flow
 * separates them: the CMP fires, and only then does the page call `clarity('consentv2',
 * ...)`. A stored signal says "a choice was requested". A stored decision says "a choice was
 * made". Those are different facts and collapsing them would make the audit trail lie.
 *
 * `denied` carries the most detail because it is the branch the evidence calls
 * destructive: "Clarity deletes any existing cookie for the website, ends the current
 * session, and restarts tracking in no-consent mode."
 */
export const CONSENT_OUTCOMES = [
  {
    value: 'granted',
    label: 'Granted on both axes',
    meaning:
      'Both axes were granted, so the visitor keeps a persistent identifier and the session is recorded.',
    tone: 'insert',
  },
  {
    value: 'denied',
    label: 'Denied',
    meaning:
      'The session was destroyed rather than skipped. The visitor is anonymous, no cookie persists, and the stored session was deleted.',
    tone: 'delete',
  },
  {
    value: 'signal',
    label: 'Signal only',
    meaning:
      'A consent prompt fired. No choice was reported, so no decision was stored and nothing is recorded.',
    tone: 'neutral',
  },
]

/** One consent answer by value, with a fallback so the page never renders blank. */
export function consentOutcome(value) {
  return CONSENT_OUTCOMES.find((outcome) => outcome.value === value) || CONSENT_OUTCOMES[0]
}

/**
 * What a recording attempt produced, and what it means.
 *
 * `blocked` is the answer that surprises a reader, so it says what actually happened: the
 * visitor was excluded at ingest and no session row exists. A page that showed only
 * "blocked" would leave a reader unable to tell a working exclusion from a failed recording.
 */
export const INGEST_STATES = [
  {
    value: 'recorded',
    label: 'Recorded',
    meaning: 'Both axes were granted, the frame was masked, and the session is stored.',
    tone: 'insert',
  },
  {
    value: 'scrubbed',
    label: 'Not recorded',
    meaning:
      'Consent was not granted on both axes, so no replay is stored. The visit itself is still counted.',
    tone: 'neutral',
  },
  {
    value: 'blocked',
    label: 'Blocked by IP',
    meaning:
      'The visitor was on the blocklist, so no session was recorded at all. The exclusion is kept in the audit log.',
    tone: 'delete',
  },
]

/** One ingest state by value, with a fallback so the page never renders blank. */
export function ingestState(value) {
  return INGEST_STATES.find((state) => state.value === value) || INGEST_STATES[1]
}

/** The two masking modes a project can move off the total-suppression default. */
export const MASKING_MODES = [
  {
    value: 'suppress_all',
    label: 'Suppress all content',
    detail: 'The documented default. Every value in every frame is replaced by a mask.',
  },
  {
    value: 'element_selector',
    label: 'Selected elements',
    detail: 'Only the named fields are masked. Everything else is stored as captured.',
  },
  {
    value: 'select_text',
    label: 'All text',
    detail: 'Text is masked and numbers are kept, so a replay shows counts without content.',
  },
]

/** One masking mode by value, with a fallback to the documented default. */
export function maskingMode(value) {
  return MASKING_MODES.find((mode) => mode.value === value) || MASKING_MODES[0]
}

/**
 * An instant as the page renders it.
 *
 * Both encodings reach this page: the ISO 8601 UTC string this workflow writes and Unix
 * milliseconds for any row written elsewhere. A value the page cannot read renders as
 * `unknown` rather than as a date a reviewer could misread.
 */
export function formatInstant(value) {
  if (value === undefined || value === null || value === '') return 'unknown'
  const text = String(value)
  const millis = /^-?\d+$/.test(text) ? Number(text) : Date.parse(text)
  if (!Number.isFinite(millis)) return 'unknown'
  const date = new Date(millis)
  if (Number.isNaN(date.getTime())) return 'unknown'
  return date.toISOString().replace('T', ' ').replace(/\.\d+Z$/, 'Z')
}

/** Days as a short phrase, because a window is read as a duration rather than a number. */
export function formatWindow(days) {
  const total = Number(days)
  if (!Number.isFinite(total) || total < 0) return 'unknown'
  if (total < 60) return `${total} days`
  const months = Math.round(total / 30)
  return `${total} days (about ${months} months)`
}

/** A count with a singular and a plural noun, so a page never says "1 records". */
export function plural(count, noun) {
  const total = Number(count) || 0
  return `${total} ${noun}${total === 1 ? '' : 's'}`
}

/**
 * How many labels a recording has left before the cap refuses the next one.
 *
 * The cap is on the recording, so this counts what that recording holds. It exists so the
 * label control can say "one left" before the sixth submission is rejected.
 */
export function labelsRemaining(held, cap = 5) {
  const count = Array.isArray(held) ? held.length : 0
  return Math.max(0, Number(cap) - count)
}
