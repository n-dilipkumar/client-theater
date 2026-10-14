/**
 * WF-134's own API wrapper, and the words this page needs.
 *
 * `apiRequest` from `@/lib/api` is the transport, exactly as the contract asks:
 * the shared `api` object grows no methods, so a hundred features can each talk
 * to their own `/api/wf-134` routes without anyone editing a shared file.
 *
 * The write calls go through `requestWithBody` instead, and that is a finding
 * rather than a preference. `apiRequest` reads the error body to build a message
 * and then throws it away, so a failure survives as `status` plus one string.
 * Two responses in this workflow need more than that:
 *
 *   - a 400 from the validator carries `errors`, a field-keyed map, so a form can
 *     put each message beside the input that caused it;
 *   - a 409 from a cue that already has an outcome carries `reason`, a stable
 *     token, so the page can say "that cue is closed" instead of printing an
 *     English sentence and hoping the server did not reword it.
 *
 * The shared client would need three more fields on one function, which is a
 * shared file and a platform decision. Until then the calls that need the body
 * read it themselves. Recorded as promotion work, not smuggled across the
 * boundary.
 *
 * Every state this page shows is carried by a word first and a colour second.
 * The word maps below are where the words live, so nothing depends on colour
 * alone and a screen reader reads the state.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-134'

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

export const dealHealthApi = {
  // -- the board ------------------------------------------------------------ //

  summary: (roomId) => call(`/summary${query({ room_id: roomId })}`),

  /**
   * The researched vocabulary and the decisions behind it.
   *
   * The page reads its bands, its reasons and its frameworks from here rather
   * than hard-coding them, so a change to the rules cannot leave the page
   * naming a state the API does not serve.
   */
  vocabulary: () => call('/vocabulary'),

  // -- deals ---------------------------------------------------------------- //

  deals: (roomId) => call(`/rooms/${encode(roomId)}/deals`),
  createDeal: (roomId, payload) => send(`/rooms/${encode(roomId)}/deals`, 'POST', payload),
  deal: (dealId) => call(`/deals/${encode(dealId)}`),
  updateDeal: (dealId, changes) => send(`/deals/${encode(dealId)}`, 'PATCH', changes),

  // -- scoring -------------------------------------------------------------- //

  /**
   * A live reading of the deal. The server computes it from the evidence on
   * every read, so the card cannot go stale, and `last_recorded` carries the
   * most recent stored snapshot.
   */
  score: (dealId) => call(`/deals/${encode(dealId)}/score`),
  scoreNow: (dealId) => send(`/deals/${encode(dealId)}/score`, 'POST'),
  history: (dealId) => call(`/deals/${encode(dealId)}/history`),

  // -- engagement intake ---------------------------------------------------- //

  events: (dealId) => call(`/deals/${encode(dealId)}/events`),
  recordEvent: (dealId, payload) => send(`/deals/${encode(dealId)}/events`, 'POST', payload),

  // -- the buying group ----------------------------------------------------- //

  stakeholders: (dealId) => call(`/deals/${encode(dealId)}/stakeholders`),
  addStakeholder: (dealId, payload) =>
    send(`/deals/${encode(dealId)}/stakeholders`, 'POST', payload),
  updateStakeholder: (dealId, contactId, changes) =>
    send(`/deals/${encode(dealId)}/stakeholders/${encode(contactId)}`, 'PATCH', changes),
  coverage: (dealId) => call(`/deals/${encode(dealId)}/coverage`),

  // -- key material --------------------------------------------------------- //

  content: (dealId) => call(`/deals/${encode(dealId)}/content`),
  addContent: (dealId, payload) => send(`/deals/${encode(dealId)}/content`, 'POST', payload),
  reviewContent: (dealId, assetId, reviewed) =>
    send(`/deals/${encode(dealId)}/content/${encode(assetId)}`, 'PATCH', { reviewed }),

  // -- risk alerts ---------------------------------------------------------- //

  alerts: (dealId) => call(`/deals/${encode(dealId)}/alerts`),
  resolveAlert: (alertId) => send('/alerts/resolve', 'POST', { alert_id: alertId }),

  // -- coaching and the closed loop ---------------------------------------- //

  coaching: (dealId, openOnly = false) =>
    call(`/deals/${encode(dealId)}/coaching${query({ open_only: openOnly ? 'true' : '' })}`),
  recordOutcome: (cueId, state, note) =>
    send('/coaching/outcome', 'POST', { cue_id: cueId, state, note }),
  readiness: (rep) => call(`/readiness${query({ rep })}`),
}

/** Every room, so the page can offer one to score a deal in. */
export const listRooms = () => apiRequest('/records/room?limit=100')

// --------------------------------------------------------------------------- //
// The words. One place for each state, so no state is carried by colour alone.
// --------------------------------------------------------------------------- //

/** The band a score sits in, as a word. */
export const BAND_WORDS = {
  healthy: 'Healthy',
  watch: 'Watch',
  at_risk: 'At risk',
}

/** The band as a tone on a badge. The word is what carries the meaning. */
export const BAND_TONES = {
  healthy: 'success',
  watch: 'warning',
  at_risk: 'destructive',
}

/** The three score factors, as a rep reads them. */
export const FACTOR_WORDS = {
  engagement: 'Engagement',
  content: 'Key material',
  stakeholder: 'Stakeholder coverage',
}

/** A risk reason, as a sentence a rep can act on. */
export const REASON_WORDS = {
  no_engagement: 'No engagement recorded',
  engagement_decay: 'Engagement decayed',
  unreviewed_key_material: 'Key material unreviewed',
  missing_stakeholders: 'Buying group not covered',
  below_health_target: 'Below the health target',
  score_drop: 'Score dropped',
}

/** An engagement event kind, as a word. */
export const EVENT_WORDS = {
  view: 'View',
  dwell: 'Read',
  download: 'Download',
  comment: 'Comment',
  share: 'Share',
  question: 'Question',
  meeting: 'Meeting',
}

/** A coaching outcome, as a word. */
export const OUTCOME_WORDS = {
  open: 'Open',
  acknowledged: 'Acknowledged',
  actioned: 'Actioned',
  dismissed: 'Dismissed',
}

/** The sentence for a conflict, from the stable reason token. */
export const CONFLICTS = {
  write_not_visible: 'The server wrote the change but could not read it back, so it refused it.',
  cue_already_closed: 'That coaching cue already has an outcome.',
}

export function conflictText(reason) {
  return CONFLICTS[reason] || 'That change was refused.'
}

export function bandWord(band) {
  return BAND_WORDS[band] || band || 'Unknown'
}

export function bandTone(band) {
  return BAND_TONES[band] || 'neutral'
}

export function reasonWord(reason) {
  return REASON_WORDS[reason] || reason || 'Risk'
}

export function factorWord(key) {
  return FACTOR_WORDS[key] || key
}

export function eventWord(kind) {
  return EVENT_WORDS[kind] || kind || 'Event'
}

export function outcomeWord(state) {
  return OUTCOME_WORDS[state] || state || 'Open'
}

/**
 * A factor's contribution, as the number the score actually used: the factor
 * against its weight. Shown so the score is read rather than trusted.
 */
export function weightedContribution(factor, weights, key) {
  const value = Number(factor?.factor)
  const weight = Number(weights?.[key])
  if (!Number.isFinite(value) || !Number.isFinite(weight)) return null
  return Math.round(value * weight * 100)
}

/** A factor as a whole percentage, for the bar. */
export function factorPercent(factor) {
  const value = Number(factor?.factor)
  if (!Number.isFinite(value)) return 0
  return Math.max(0, Math.min(100, Math.round(value * 100)))
}

/** How the score moved since the previous reading, as a sentence. */
export function deltaSentence(score) {
  const delta = score?.delta
  if (delta === null || delta === undefined) return 'First recorded reading.'
  if (delta === 0) return 'Unchanged since the previous reading.'
  const word = delta > 0 ? 'Up' : 'Down'
  return `${word} ${Math.abs(delta)} points since the previous reading.`
}

/** A whole number of days, as a short phrase. */
export function daysSince(value) {
  if (value === null || value === undefined || value === '') return 'never recorded'
  const days = Number(value)
  if (!Number.isFinite(days)) return 'never recorded'
  if (days < 1) return 'today'
  const whole = Math.round(days)
  return whole === 1 ? '1 day ago' : `${whole} days ago`
}
