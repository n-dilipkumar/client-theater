import { apiRequest } from '@/lib/api'

/**
 * Trend health API calls (WF-021).
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a
 * method to it is precisely the conflict the feature host exists to remove. The
 * host's `apiRequest` is the escape hatch that makes that unnecessary.
 *
 * `PREFIX` is the feature's own: the backend module mounts these under
 * `/api/wf-021`, and `apiRequest` already prepends `/api`.
 *
 * Every list is rendered from what the API serves rather than from a list
 * compiled here. The four Trend values, their labels, the event shapes the intake
 * accepts, and the sort keys all come from `/vocabulary` and from the response
 * itself, so a name added on the server reaches this page without a change to
 * this file.
 */

const PREFIX = '/wf-021'

function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const trendApi = {
  /** The four Trend values, their sourced sentences, the labels, the event shapes. */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),

  /**
   * Every judgement call the research left open, named and bounded.
   *
   * Served as data rather than buried in code comments, so a rep or a reviewer
   * can disagree with a named entry instead of inferring the assumptions from the
   * numbers on screen.
   */
  inferences: () => apiRequest(`${PREFIX}/inferences`),

  /** The windows and volume floors in force, and whether they were retuned. */
  rules: () => apiRequest(`${PREFIX}/rules`),

  /** Retune the classification. A partial patch; the write is audited. */
  updateRules: (payload) =>
    apiRequest(`${PREFIX}/rules`, { method: 'PATCH', body: JSON.stringify(payload) }),

  /** The stat row: how the portfolio is distributed across the four buckets. */
  summary: (params = {}) => apiRequest(`${PREFIX}/summary${query(params)}`),

  /** The Trend column: one row per workspace, filterable and sortable. */
  dashboard: (params = {}) => apiRequest(`${PREFIX}/dashboard${query(params)}`),

  /** One room's health badge, its arithmetic, and the decay that follows. */
  roomTrend: (roomId, params = {}) => apiRequest(`${PREFIX}/rooms/${roomId}/trend${query(params)}`),

  /** The engagement events behind the buckets. */
  events: (params = {}) => apiRequest(`${PREFIX}/events${query(params)}`),

  /** Record one engagement event - the researched `workspace.*` webhook seam. */
  recordEvent: (payload, params = {}) =>
    apiRequest(`${PREFIX}/events${query(params)}`, { method: 'POST', body: JSON.stringify(payload) }),
}
