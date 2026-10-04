/**
 * WF-075's own API wrapper.
 *
 * `apiRequest` from `@/lib/api` is the transport, exactly as the contract asks: the
 * shared `api` object grows no methods, so a hundred features can each talk to their
 * own `/api/<feature>` routes without anyone editing a shared file.
 *
 * Every call here is a read except the two ingest calls, and the reads are the majority
 * because this workflow presents view events rather than producing them.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-075'

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
    const error = new Error(body?.detail || body?.error || `${response.status} ${response.statusText}`)
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

export const engagementApi = {
  // -- the board ------------------------------------------------------------ //

  /** The two counts, kept apart: persistent visitors and view events. */
  summary: (dataroomId) => call(`/summary${query({ dataroom_id: dataroomId })}`),

  /** The researched vocabulary, so the board cannot drift from the rules behind it. */
  vocabulary: () => call('/vocabulary'),

  /** Every judgement call this workflow made, with the alternative it rejected. */
  decisions: () => call('/decisions'),

  // -- the viewers list ------------------------------------------------------ //

  /** One row per buyer email. `email` filters to a single address. */
  visitors: (email, dataroomId) => call(`/visitors${query({ email, dataroom_id: dataroomId })}`),

  /** One visitor, with the proof state and their view history. */
  visitor: (visitorId) => call(`/visitors/${encode(visitorId)}`),

  /** That visitor's history on its own, newest first. */
  visitorViews: (visitorId, limit) => call(`/visitors/${encode(visitorId)}/views${query({ limit })}`),

  // -- the aggregate --------------------------------------------------------- //

  /**
   * The cached room totals. `since` and `until` are Unix milliseconds.
   *
   * The response carries `cached` and `computed_at`, and the board renders both, so a
   * reader can tell a fresh answer from a cached one. That matters here because
   * polling is the documented integration path until webhooks ship.
   */
  dataroomStats: (dataroomId, bounds = {}) =>
    call(`/analytics/datarooms/${encode(dataroomId)}${query(bounds)}`),

  /** One link's totals on the same window. */
  linkStats: (linkId, bounds = {}) => call(`/analytics/links/${encode(linkId)}${query(bounds)}`),

  /**
   * One view's full breakdown: page dwell, location and client.
   *
   * The response carries `geography_source`, which says the geolocation provider is an
   * inference. The board shows that sentence rather than naming a vendor.
   */
  view: (viewId) => call(`/analytics/views/${encode(viewId)}`),

  // -- the per-link view list ------------------------------------------------ //

  /** Every view of one link, newest first. Anonymous views stay in this list. */
  linkViews: (linkId, bounds = {}) => call(`/links/${encode(linkId)}/views${query(bounds)}`),

  // -- the write path this workflow owns ------------------------------------- //

  createVisitor: (payload) => send('/visitors', 'POST', payload),
  createView: (linkId, payload) => send(`/links/${encode(linkId)}/views`, 'POST', payload),
}

/**
 * The three proof states, and what each one means.
 *
 * The specification reads `verified` "to confirm the identity was actually proven (not
 * merely typed in)". A boolean cannot say that for a row where no proof was recorded,
 * so the workflow serves three words and the page renders all three.
 */
export const VERIFICATION_STATES = [
  {
    value: 'verified',
    label: 'Verified',
    meaning: 'The identity was proven.',
    tone: 'success',
  },
  {
    value: 'unverified',
    label: 'Not verified',
    meaning: 'An address was typed and the identity was not proven.',
    tone: 'warning',
  },
  {
    value: 'unknown',
    label: 'No proof recorded',
    meaning: 'No proof step has run for this row yet.',
    tone: 'neutral',
  },
]

/** One verification state by value, with a fallback so the page never renders blank. */
export function verificationState(value) {
  return VERIFICATION_STATES.find((state) => state.value === value) || VERIFICATION_STATES[2]
}

/**
 * The state of the geolocation source, in the specification's own words.
 *
 * The specification marks the provider as an inference and says so itself: "viewer IP
 * to geolocation provider [inferred - the API returns location.country/city but names no
 * vendor]". The board shows this sentence rather than naming a vendor, and a test
 * asserts no vendor name appears here.
 */
export const GEOGRAPHY_SOURCE =
  'viewer IP to geolocation provider [inferred - the API returns location.country/city but names no vendor]'

/** Unix milliseconds as the workflow's unit, stated once for the page to render. */
export const TIME_UNIT = 'unix_ms'

/**
 * A Unix millisecond timestamp as a readable string.
 *
 * The workflow stores and serves milliseconds because the specification's `--since` and
 * `--until` bounds are in "Unix ms". A rep reads a date, so the page converts. The unit
 * is never guessed: a value the page cannot read renders as "unknown" rather than as
 * 1970, which would be a real date the rep could misread.
 */
export function formatInstant(milliseconds) {
  if (!milliseconds) return 'unknown'
  const date = new Date(Number(milliseconds))
  if (Number.isNaN(date.getTime())) return 'unknown'
  return date.toISOString().replace('T', ' ').replace(/\.\d+Z$/, 'Z')
}

/** Seconds as minutes and seconds, because a dwell figure is read as a duration. */
export function formatDuration(seconds) {
  const total = Math.max(0, Math.round(Number(seconds) || 0))
  const minutes = Math.floor(total / 60)
  const rest = total % 60
  if (minutes === 0) return `${rest}s`
  return `${minutes}m ${rest}s`
}