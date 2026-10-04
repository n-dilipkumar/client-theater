/**
 * Intent alerting API (WF-133).
 *
 * These calls live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a
 * method to it is precisely the collision the feature host exists to remove.
 *
 * `PREFIX` is this feature's own: the backend module mounts these under
 * `/api/wf-133`, and `apiRequest` already prepends `/api`. Nothing here calls a
 * route outside that prefix, and a test in `IntentAlerts.test.jsx` asserts it.
 *
 * Room is a query parameter rather than a path segment. The research's alerts are
 * room-scoped, and a client that already holds a room list gets the room it cares
 * about from a picker, so a path segment would only make the URL longer.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-133'

/** Drop empty values, so we never send `?state=` and confuse a filter. */
function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const intentApi = {
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),
  inferences: () => apiRequest(`${PREFIX}/inferences`),
  summary: (roomId) => apiRequest(`${PREFIX}/summary${query({ room_id: roomId })}`),

  watchlists: (roomId) => apiRequest(`${PREFIX}/watchlists${query({ room_id: roomId })}`),
  createWatchlist: (payload, roomId) =>
    apiRequest(`${PREFIX}/watchlists${query({ room_id: roomId })}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  deleteWatchlist: (id) => apiRequest(`${PREFIX}/watchlists/${encodeURIComponent(id)}`, { method: 'DELETE' }),

  rules: (roomId) => apiRequest(`${PREFIX}/rules${query({ room_id: roomId })}`),
  createRule: (payload, roomId) =>
    apiRequest(`${PREFIX}/rules${query({ room_id: roomId })}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  deleteRule: (id) => apiRequest(`${PREFIX}/rules/${encodeURIComponent(id)}`, { method: 'DELETE' }),

  evaluate: (payload) =>
    apiRequest(`${PREFIX}/signals/evaluate`, { method: 'POST', body: JSON.stringify(payload) }),
  raise: (payload, roomId) =>
    apiRequest(`${PREFIX}/signals${query({ room_id: roomId })}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  signals: (params = {}) => apiRequest(`${PREFIX}/signals${query(params)}`),
  signal: (id) => apiRequest(`${PREFIX}/signals/${encodeURIComponent(id)}`),

  alerts: (params = {}) => apiRequest(`${PREFIX}/alerts${query(params)}`),
  alert: (id) => apiRequest(`${PREFIX}/alerts/${encodeURIComponent(id)}`),

  tasks: (params = {}) => apiRequest(`${PREFIX}/tasks${query(params)}`),
  task: (id) => apiRequest(`${PREFIX}/tasks/${encodeURIComponent(id)}`),

  engagement: (roomId) => apiRequest(`${PREFIX}/engagement${query({ room_id: roomId })}`),

  actions: (params = {}) => apiRequest(`${PREFIX}/actions${query(params)}`),
  recordAction: (signalId, payload, actor) =>
    apiRequest(`${PREFIX}/signals/${encodeURIComponent(signalId)}/actions${query({ actor })}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /** The core room list. The page is handed no props, so it fetches its own rooms. */
  rooms: () => apiRequest('/records/room?limit=100'),
}

/**
 * Turn a domain refusal into something a seller can act on.
 *
 * Kept here so the page never pattern-matches a raw detail string. Each rule names
 * the mistake the caller can fix, and anything unrecognised is shown verbatim rather
 * than swallowed.
 */
export function refusalMessage(error) {
  const text = String(error?.message || error)
  if (error?.status === 409 && /no opportunity is on file/i.test(text)) {
    return 'This account has no opportunity in this room yet. Import the deal first, because the follow-up task has to hang from it.'
  }
  if (error?.status === 404 && /no identified company/i.test(text)) {
    return 'No company is on file under that key. This workflow alerts on companies the visitor-identification workflow has already identified.'
  }
  if (/carries field/i.test(text)) {
    return 'That observation carries a field no threshold reads on. Remove it and send only the five measurements.'
  }
  if (/not an email address/i.test(text)) {
    return 'Only an email address can be a recipient here. This build has no Slack surface, so a handle has nowhere to go.'
  }
  if (/needs a note/i.test(text)) {
    return 'A dismissal needs a note, so the log can answer which alerts were not worth a call.'
  }
  return text
}

/** A short label for one account in the engagement view. */
export function accountLabel(row) {
  return row.company_name || row.company_key || 'Unknown account'
}

/**
 * Seconds to a phrase a rep reads at a glance.
 *
 * Up to five minutes it stays in seconds, and that cutoff is not cosmetic. The
 * sourced threshold is 90 **seconds**, so a reading of 142 has to render as "142s"
 * for a rep to compare it against "90s" without doing arithmetic in their head. A
 * helper that turned 90 into "1m 30s" would hide the one number the research
 * actually states, and one that turned 142 into "2m 22s" would reintroduce the same
 * arithmetic one step up. Five minutes is past the longest single-page read a sales
 * room produces, so above it "m:ss" is the easier form and the window total lands
 * there.
 */
export function dwellLabel(seconds) {
  const value = Number(seconds || 0)
  if (value <= 0) return 'no dwell recorded'
  if (value < 300) return `${value}s`
  const minutes = Math.floor(value / 60)
  return `${minutes}m ${value % 60}s`
}

/**
 * The one researched "how long" fact, as one sentence.
 *
 * Both numbers are in it because the sourced threshold is on the longest page read
 * while a rep asking "how long were they in here" means the window total.
 * Reporting only one of the two makes the other a question the alert cannot answer.
 */
export function dwellSentence(dwell = {}) {
  const longest = dwellLabel(dwell.longest_page_seconds)
  const total = dwellLabel(dwell.window_total_seconds)
  return `${longest} on the longest page, ${total} in the window`
}

/** The threshold beside the measurement, so the arithmetic is visible. */
export function thresholdSentence(dwell = {}) {
  return `sourced threshold is ${dwellLabel(dwell.threshold_seconds)}`
}
