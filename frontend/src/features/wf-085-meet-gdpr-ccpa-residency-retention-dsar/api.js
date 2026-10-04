/**
 * WF-085's own API wrapper.
 *
 * `apiRequest` from `@/lib/api` is the transport, exactly as the contract asks: the
 * shared `api` object grows no methods, so a hundred features can each talk to their own
 * `/api/<feature>` routes without anyone editing a shared file.
 *
 * Reads outnumber writes here, and that is the shape the workflow has: the board, the
 * policy, the schedule, the gate's vocabulary and the erasure requests are all reads. The
 * five writes are the blocking changes and they all take a role.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-085'

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

export const privacyApi = {
  // -- the board and the research --------------------------------------------

  /** The headline numbers, read back from the store. */
  summary: (roomId) => call(`/summary${query({ room_id: roomId })}`),

  /**
   * The researched vocabulary, so the page cannot drift from the rules behind it: the
   * regions, the retention classes and their windows, the gate's words and the personal
   * fields a DSAR has to reach.
   */
  vocabulary: () => call('/vocabulary'),

  /** Every judgement call this workflow made, with the alternative it rejected. */
  decisions: () => call('/decisions'),

  // -- residency -------------------------------------------------------------

  /**
   * Where this deployment's content and engagement data is processed.
   *
   * The response carries `vendor_claims` with `verified: false` and a note saying no
   * certification is claimed for this deployment. The page shows those claims as claims.
   */
  residency: (roomId) => call(`/residency${query({ room_id: roomId })}`),

  /**
   * Pin the deployment to a region. Administrator-only.
   *
   * The move re-stamps every record the room holds, so the response carries
   * `relocation.records_scanned` and `relocation.records_moved` and the page renders
   * both rather than a single boolean.
   */
  setResidency: (payload, { roomId, role, actor } = {}) =>
    send(`/residency${query({ room_id: roomId, role, actor })}`, 'POST', payload),

  // -- retention -------------------------------------------------------------

  /** The three classes, their windows, their evidence, and what nothing ages. */
  retentionPolicy: (roomId) => call(`/retention/policy${query({ room_id: roomId })}`),

  /** Set the windows. Shorter than the ceiling, never longer. Administrator-only. */
  setRetentionPolicy: (windows, { roomId, role, actor } = {}) =>
    send(`/retention/policy${query({ room_id: roomId, role, actor })}`, 'POST', { windows }),

  /** What is due for erasure now, by class, and when each class next falls due. */
  retentionSchedule: (roomId) => call(`/retention/schedule${query({ room_id: roomId })}`),

  /** Erase every record past its window. Administrator-only. */
  runRetention: ({ roomId, role, actor } = {}) =>
    send(`/retention/run${query({ room_id: roomId, role, actor })}`, 'POST', {}),

  // -- consent ---------------------------------------------------------------

  /**
   * The gate's vocabulary, plus the decision for one region without writing anything.
   *
   * Naming a `region` evaluates the gate for that case, so the page can show what the
   * room would do before recording it.
   */
  consent: (params = {}) => call(`/consent${query(params)}`),

  /**
   * Record one consent signal. Not administrator-gated: the specification says consent is
   * captured at the page, so the caller here is the room's own page.
   */
  recordConsent: (payload, { roomId } = {}) =>
    send(`/consent${query({ room_id: roomId })}`, 'POST', payload),

  // -- the DSAR --------------------------------------------------------------

  /** Every data-subject request, oldest first. */
  dsarRequests: (roomId) => call(`/dsar/requests${query({ room_id: roomId })}`),

  /** Open a request for one subject and record everything it found. Administrator-only. */
  openDsar: (payload, { roomId, role, actor } = {}) =>
    send(`/dsar/requests${query({ room_id: roomId, role, actor })}`, 'POST', payload),

  /** Erase one subject's records and report the residue. Administrator-only. */
  fulfilDsar: (requestId, { role, actor } = {}) =>
    send(`/dsar/requests/${encode(requestId)}/fulfil${query({ role, actor })}`, 'POST', {}),
}

/**
 * The gate's three answers, and what each one means.
 *
 * The specification requires the gate to fail closed, so `deny` is the state the page
 * renders with the most detail: "a unique ID per page view and does not use cookies to
 * persist session data". `not_required` is a third named answer rather than a missing
 * field, because falling through to tracking silently is exactly "merely degrading".
 */
export const GATE_OUTCOMES = [
  {
    value: 'track',
    label: 'Tracking allowed',
    meaning: 'Explicit consent was granted in a region that requires it.',
    tone: 'insert',
  },
  {
    value: 'deny',
    label: 'Denied',
    meaning:
      'No persistent identifier and no cookies. A unique ID per page view, and no further tracking until new consent.',
    tone: 'delete',
  },
  {
    value: 'not_required',
    label: 'Not required here',
    meaning:
      "This region's jurisdiction is not in the configured consent list, so the gate does not enforce consent.",
    tone: 'neutral',
  },
]

/** One gate answer by value, with a fallback so the page never renders blank. */
export function gateOutcome(value) {
  return GATE_OUTCOMES.find((outcome) => outcome.value === value) || GATE_OUTCOMES[1]
}

/**
 * The three consent states a record can be in.
 *
 * `revoked` is a state rather than a flavour of `denied`, because the evidence makes
 * revocation its own event: a deny after a grant "clears the cookies from the user's
 * browser and prevent[s] further tracking until new consent is granted".
 */
export const CONSENT_STATES = [
  {
    value: 'active',
    label: 'Active',
    meaning: 'Consent was granted and has not been withdrawn.',
    tone: 'insert',
  },
  {
    value: 'revoked',
    label: 'Revoked',
    meaning: 'Consent was granted and then withdrawn. The cookies were cleared.',
    tone: 'delete',
  },
  {
    value: 'denied',
    label: 'Denied',
    meaning: 'Consent was never granted, so nothing was ever tracked under it.',
    tone: 'warning',
  },
]

/** One consent state by value, with a fallback so the page never renders blank. */
export function consentState(value) {
  return CONSENT_STATES.find((state) => state.value === value) || CONSENT_STATES[2]
}

/** The four states an erasure request can read as. */
export const DSAR_STATES = [
  { value: 'opened', label: 'Opened', meaning: 'Found, and not yet fulfilled.' },
  {
    value: 'partial',
    label: 'Partial',
    meaning:
      'The records were erased and something still names the subject: the audit trail, or the request row itself.',
  },
  { value: 'fulfilled', label: 'Fulfilled', meaning: 'Erased with nothing left behind.' },
  {
    value: 'nothing_found',
    label: 'Nothing found',
    meaning: 'No record held this subject, so there was nothing to erase.',
  },
]

/** One erasure state by value, with a fallback so the page never renders blank. */
export function dsarState(value) {
  return DSAR_STATES.find((state) => state.value === value) || DSAR_STATES[0]
}

/**
 * Whether a consent region needs the gate, from the served vocabulary.
 *
 * Read from the server rather than written here, so a deployment that configures the
 * list differently shows the configured answer on the page.
 */
export function isConsentJurisdiction(vocabulary, jurisdiction) {
  const list = (vocabulary?.consent_jurisdictions || []).map((entry) =>
    typeof entry === 'string' ? entry : entry.id,
  )
  return list.includes(jurisdiction)
}

/**
 * A Unix-second-free instant as the page renders it.
 *
 * Both accepted encodings - an ISO 8601 UTC string and Unix milliseconds - reach this
 * page, because the engagement rows store milliseconds and this workflow's own rows store
 * ISO. A value the page cannot read renders as `unknown` rather than as a date a rep
 * could misread.
 */
export function formatInstant(value) {
  if (value === undefined || value === null || value === '') return 'unknown'
  const text = typeof value === 'number' ? String(value) : String(value)
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
  if (total < 30) return `${total} days`
  if (total < 60) return `${total} days (about one month)`
  const months = Math.round(total / 30)
  return `${total} days (about ${months} months)`
}

/** A count with a singular and a plural noun, so a page never says "1 records". */
export function plural(count, noun) {
  const total = Number(count) || 0
  return `${total} ${noun}${total === 1 ? '' : 's'}`
}
