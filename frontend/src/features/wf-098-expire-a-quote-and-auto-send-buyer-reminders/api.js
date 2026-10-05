/**
 * Quote expiry and buyer reminders API (WF-098).
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a method
 * to it is exactly the collision the feature host exists to remove. The host's
 * `apiRequest` is the escape hatch that makes that unnecessary.
 *
 * `PREFIX` is the feature's own: the backend module mounts these under `/api/wf-098`, and
 * `apiRequest` already prepends `/api`.
 *
 * Every payload is sent in the research's own spelling - `hs_expiration_date` for the
 * deadline, `offset_kind` for which of the two offsets a rule uses, and an integer day
 * count - so the documented flow can be driven against the API directly and a reader can
 * match what this page sends against what the research says should be sent.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-098'

/** Drop empty values so we never send `?state=` and confuse a filter. */
function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const expiryApi = {
  /**
   * The published terms: the 1-to-365-day window, both reminder offsets with the
   * vendor's own label for each, the three acceptance methods, the three surviving buyer
   * actions, the eight quote states, the send count, the void and archive consequences,
   * the skip reasons, and the sentences that say what expiry is not. Every picker and
   * every badge on the page renders from this rather than from a list compiled into this
   * file, so a rule changed on the server reaches every client at once.
   */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),

  /**
   * Every judgement call this workflow rests on: what the research fixes, what it leaves
   * open, and which reading this build took. Rendered on the page's last section so a
   * reviewer reads the list instead of reconstructing it from a diff.
   */
  inferences: () => apiRequest(`${PREFIX}/inferences`),

  decision: (id) => apiRequest(`${PREFIX}/decisions/${id}`),

  /** The room list comes from the core records route, so this page depends on the HTTP
   * contract rather than on another module's idea of a room's shape. */
  rooms: () => apiRequest('/records/room?limit=100'),

  settings: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/settings`),
  saveSettings: (roomId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/settings`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),

  rules: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/reminder-rules`),
  addRule: (roomId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/reminder-rules`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  patchRule: (roomId, ruleId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/reminder-rules/${ruleId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  deleteRule: (roomId, ruleId) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/reminder-rules/${ruleId}`, { method: 'DELETE' }),
  preview: (roomId, ruleId, quoteId) =>
    apiRequest(
      `${PREFIX}/rooms/${roomId}/reminder-rules/${ruleId}/preview${query({ quote_id: quoteId })}`,
    ),

  quotes: (roomId, params = {}) => apiRequest(`${PREFIX}/rooms/${roomId}/quotes${query(params)}`),
  quote: (roomId, quoteId, params = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/quotes/${quoteId}${query(params)}`),
  createQuote: (roomId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/quotes`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /**
   * The three header controls, one at a time.
   *
   * `hs_expiration_date: null` clears the deadline deliberately, which is not the same as
   * omitting the field: a caller that sends only the label does not clear the date.
   */
  setExpiration: (roomId, quoteId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/quotes/${quoteId}/expiration`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),

  send: (roomId, quoteId, publish = false) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/quotes/${quoteId}/send`, {
      method: 'POST',
      body: JSON.stringify(publish ? { publish: true } : {}),
    }),

  recordAcceptance: (roomId, quoteId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/quotes/${quoteId}/acceptance`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  canAccept: (roomId, quoteId) => apiRequest(`${PREFIX}/rooms/${roomId}/quotes/${quoteId}/can-accept`),

  voidQuote: (roomId, quoteId) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/quotes/${quoteId}/void`, {
      method: 'POST',
      body: JSON.stringify({}),
    }),
  archiveQuote: (roomId, quoteId) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/quotes/${quoteId}/archive`, {
      method: 'POST',
      body: JSON.stringify({}),
    }),

  /** The researched scheduled dispatch. A `POST` because it writes a ledger row per decision. */
  dispatch: (roomId, payload = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/reminders`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  reminders: (roomId, params = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/reminders${query(params)}`),

  /** The researched background expiry check, driven by a caller rather than by a thread. */
  checkExpiry: (roomId, payload = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/expiry-check`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  activities: (roomId, params = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/activities${query(params)}`),
  summary: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/summary`),
}

/**
 * The tone a quote state is rendered in, read from the published vocabulary.
 *
 * Reading the server's list rather than keeping a local copy means a state added there
 * renders here on the next load instead of falling through to a neutral badge that says
 * nothing. The tone is decoration only: every badge carries the state as text, because
 * status is never conveyed by colour alone.
 */
export function stateTone(vocabulary, state) {
  const entry = (vocabulary?.quote_states || []).find((row) => row.state === state)
  if (!entry) return 'neutral'
  if (state === 'expired') return 'restore'
  if (state === 'accepted' || state === 'signed') return 'insert'
  if (state === 'voided' || state === 'archived') return 'delete'
  if (state === 'draft') return 'neutral'
  return 'update'
}

/**
 * The tone a reminder outcome is rendered in.
 *
 * A send and a skip are both rows and both are shown. A page that rendered only the sends
 * would hide the suppression rules, and those are the ones hardest to verify.
 */
export function outcomeTone(outcome) {
  return outcome === 'sent' ? 'insert' : 'neutral'
}

/**
 * Why a reminder was not sent, in the product's own words.
 *
 * The server ships the text with the vocabulary, so a reason added there appears here
 * without an edit to this file. This is the fallback for an older server.
 */
export function skipReasonText(vocabulary, reason) {
  const row = (vocabulary?.reminder_rule?.skip_reasons || []).find(
    (entry) => entry.reason === reason,
  )
  return row?.text || reason || 'Not sent.'
}

/** How many days are left, as the page reads it. `null` means no deadline at all. */
export function daysLeft(quote) {
  if (!quote?.expiration_enabled) return null
  return quote.days_remaining
}

/**
 * The one sentence saying what the seller should do about this quote.
 *
 * An open quote with a reminder due needs a person, so the page's job is to make that row
 * impossible to miss. This helper exists so the "needs you" list and the full table cannot
 * disagree about which is which.
 */
export function needsAttention(quote) {
  if (!quote) return false
  if (quote.terminal) return false
  if (quote.expiration_enabled && quote.expiring_soon) return true
  return (quote.reminders_due || []).length > 0
}

/** The refusal the server returned, in its own words. */
export function refusalSummary(error) {
  return String(error?.detail || error?.message || error || 'The request was refused.')
}

/**
 * Turn a date input into the ISO instant the API asks for.
 *
 * The research says "click the **date picker** to set a specific date" and gives no time of
 * day. The page sends the plain date, and the server reads a date with no time as midnight
 * UTC, which is the only reading that is the same instant on every machine.
 */
export function toDateInput(isoDate) {
  if (!isoDate) return ''
  return String(isoDate).slice(0, 10)
}

/** The reverse, for pre-filling the form from a stored deadline. */
export function fromDateInput(value) {
  if (!value) return null
  return `${value}T00:00:00.000Z`
}

/**
 * The instant a reminder rule fires, as this page reads it.
 *
 * The server sends the local reading on every projection, so the page never recomputes the
 * account's timezone arithmetic. A reading it could not resolve carries a note, and the
 * note is shown rather than dropped: a 09:00 reminder that is actually 09:00 UTC is worth
 * telling the reader about.
 */
export function localDueLabel(reading) {
  if (!reading?.local) return 'Not scheduled'
  return reading.known ? reading.local.replace('T', ' ') : `${reading.local.replace('T', ' ')} (UTC fallback)`
}