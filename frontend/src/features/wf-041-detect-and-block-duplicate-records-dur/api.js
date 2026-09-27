/**
 * Duplicate guard API calls (WF-041).
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a
 * method to it is precisely the conflict the feature host exists to remove. The
 * host's `apiRequest` is the escape hatch that makes that unnecessary.
 *
 * `PREFIX` is the feature's own: the backend module mounts these under
 * `/api/wf-041`, and `apiRequest` already prepends `/api`.
 *
 * Every picker on the page is rendered from the `/vocabulary` endpoint rather
 * than from a list compiled into this file, so a team that adds a matching key
 * ships a record instead of a change here.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-041'

function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value))
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const dedupeApi = {
  /** The researched vocabulary. Drives every picker and the header preview. */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),

  /**
   * What the research leaves open, what this build chose, and how to change it.
   *
   * The research for this workflow states its own limits - most sharply that
   * Salesforce auto-merge "is therefore *not* claimed" - and those gaps are
   * product behaviour rather than comments, so they are served as data a
   * reviewer can disagree with by name.
   */
  inferences: () => apiRequest(`${PREFIX}/inferences`),

  /** The matcher registry, including any a third party has registered. */
  matchers: () => apiRequest(`${PREFIX}/matchers`),

  /** The per-connection policy enum, each with the sentence it comes from. */
  policies: () => apiRequest(`${PREFIX}/policies`),
  policy: (name) => apiRequest(`${PREFIX}/policies/${name}`),

  /** The header a policy would send, in structured and wire form. */
  header: (params = {}) => apiRequest(`${PREFIX}/header${query(params)}`),

  summary: (params = {}) => apiRequest(`${PREFIX}/summary${query(params)}`),

  listConnections: (params = {}) => apiRequest(`${PREFIX}/connections${query(params)}`),
  createConnection: (payload) =>
    apiRequest(`${PREFIX}/connections`, { method: 'POST', body: JSON.stringify(payload) }),
  updateConnection: (id, payload) =>
    apiRequest(`${PREFIX}/connections/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteConnection: (id) => apiRequest(`${PREFIX}/connections/${id}`, { method: 'DELETE' }),

  /** The account and contact rows the duplicate rules match against. */
  listRecords: (params = {}) => apiRequest(`${PREFIX}/records${query(params)}`),
  createRecord: (payload, params = {}) =>
    apiRequest(`${PREFIX}/records${query(params)}`, { method: 'POST', body: JSON.stringify(payload) }),

  /** Every decision, newest first. */
  listDecisions: (params = {}) => apiRequest(`${PREFIX}/decisions${query(params)}`),

  /**
   * Evaluate an inbound row without writing anything.
   *
   * The read-only half of `ingest`: same evaluation, same answer, no decision
   * record, no CRM row, no room annotation.
   */
  check: (roomId, payload, params = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/check${query(params)}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /** The whole workflow: evaluate, act, and log the decision on the room. */
  ingest: (roomId, payload, params = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/ingest${query(params)}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /** The researched step-5 annotation, read off the room row itself. */
  roomDedupe: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/dedupe`),
  roomDecisions: (roomId, params = {}) => apiRequest(`${PREFIX}/rooms/${roomId}/decisions${query(params)}`),
  decision: (roomId, decisionId) => apiRequest(`${PREFIX}/rooms/${roomId}/decisions/${decisionId}`),
}
