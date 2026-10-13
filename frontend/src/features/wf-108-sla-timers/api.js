/**
 * SLA timers API (WF-108).
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a method
 * to it is exactly the collision the feature host exists to remove. The host's
 * `apiRequest` is the escape hatch that makes that unnecessary.
 *
 * `PREFIX` is the feature's own: the backend module mounts these under `/api/wf-108`,
 * and `apiRequest` already prepends `/api`.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-108'

function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value))
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const slaApi = {
  /** The published terms: the four metrics, the two object kinds, the badges and the codes. */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),

  /** What the research fixes, what this build decided, and what it rejected. */
  inferences: () => apiRequest(`${PREFIX}/inferences`),

  /** The room list comes from the core records route, so this page depends on the HTTP
   *  contract rather than on another module's idea of a room's shape. */
  rooms: () => apiRequest('/records/room?limit=100'),

  summary: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/summary`),
  officeHours: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/office-hours`),

  policies: (roomId, params = {}) => apiRequest(`${PREFIX}/rooms/${roomId}/policies${query(params)}`),
  createPolicy: (roomId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/policies`, { method: 'POST', body: JSON.stringify(payload) }),

  inbox: (roomId, params = {}) => apiRequest(`${PREFIX}/rooms/${roomId}/inbox${query(params)}`),
  report: (roomId, params = {}) => apiRequest(`${PREFIX}/rooms/${roomId}/report${query(params)}`),

  conversationSla: (roomId, conversationId) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/conversations/${conversationId}/sla`),
  applySla: (roomId, conversationId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/conversations/${conversationId}/sla`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  removeSla: (roomId, conversationId) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/conversations/${conversationId}/sla/remove`, {
      method: 'POST',
    }),

  /** The sweep, which is the timer in this product. */
  evaluate: (roomId, payload = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/timers/evaluate`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
}

/**
 * A rate as a whole percentage.
 *
 * The report's rates are fractions; the page renders them as percentages, and a rate with
 * no decided metric reads as `n/a` rather than as a misleading `0%`.
 */
export function ratePercent(value, decided = true) {
  if (value === null || value === undefined || !decided) return 'n/a'
  return `${Math.round(Number(value) * 100)}%`
}

/** The words for a badge, so a phone or a colour-blind reader gets the same answer. */
export function badgeLabel(badge) {
  const known = {
    grey: 'More than 5 minutes left',
    orange: 'Less than 5 minutes left',
    red: 'Overdue',
  }
  return known[String(badge)] || 'No timer'
}

/** The words for a timer state. */
export function timerStateLabel(state) {
  const known = { running: 'Running', paused: 'Paused', met: 'Met', breached: 'Breached' }
  return known[String(state)] || String(state || 'Unknown')
}

/** The words for a metric state. */
export function metricStateLabel(state) {
  const known = {
    pending: 'Countdown running',
    met: 'Met',
    breached: 'Breached',
    paused: 'Paused',
    not_applicable: 'Not applicable',
  }
  return known[String(state)] || String(state || 'Unknown')
}
