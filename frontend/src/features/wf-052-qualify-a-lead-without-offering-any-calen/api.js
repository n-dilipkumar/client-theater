/**
 * Lead qualification API calls (WF-052).
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a
 * method to it is precisely the conflict the feature host exists to remove. The
 * host's `apiRequest` is the escape hatch that makes that unnecessary.
 *
 * `PREFIX` is the feature's own: the backend module mounts these under
 * `/api/wf-052`, and `apiRequest` already prepends `/api`.
 *
 * Every picker on the page is rendered from the `/vocabulary` endpoint rather
 * than from a list compiled into this file, so a team that adds a verdict or a
 * rule kind ships a record instead of a change here.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-052'

function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value))
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const leadQualificationApi = {
  /** The researched vocabulary: access patterns, verdicts, rule kinds, quotes. */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),

  /** What the research leaves open, what this build chose, and how to change it. */
  inferences: () => apiRequest(`${PREFIX}/inferences`),

  /** Counts for the page header, over exactly the rows the filters return. */
  summary: (params = {}) => apiRequest(`${PREFIX}/summary${query(params)}`),

  /** The declared routers, newest first. */
  routers: (params = {}) => apiRequest(`${PREFIX}/routers${query(params)}`),
  router: (routerSlug) => apiRequest(`${PREFIX}/routers/${routerSlug}`),
  createRouter: (payload) =>
    apiRequest(`${PREFIX}/routers`, { method: 'POST', body: JSON.stringify(payload) }),
  updateRouter: (routerSlug, payload) =>
    apiRequest(`${PREFIX}/routers/${routerSlug}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteRouter: (routerSlug) =>
    apiRequest(`${PREFIX}/routers/${routerSlug}`, { method: 'DELETE' }),

  /** Check a draft chain without saving it, and see which rule a sample lead hits. */
  previewRouter: (payload) =>
    apiRequest(`${PREFIX}/routers/preview`, { method: 'POST', body: JSON.stringify(payload) }),

  /** The users a rule may propose as an owner. */
  assignees: (params = {}) => apiRequest(`${PREFIX}/assignees${query(params)}`),
  createAssignee: (payload) =>
    apiRequest(`${PREFIX}/assignees`, { method: 'POST', body: JSON.stringify(payload) }),
  deleteAssignee: (userId) => apiRequest(`${PREFIX}/assignees/${userId}`, { method: 'DELETE' }),

  /**
   * The researched call. Writes nothing: no routing session, no verdict row.
   *
   * The body is `form` data and, deliberately, no `interval` - that one field is
   * the difference between qualifying a lead and booking it, and a body carrying
   * it is refused rather than half-honoured.
   */
  qualify: (payload, params = {}) =>
    apiRequest(`${PREFIX}/qualify${query(params)}`, { method: 'POST', body: JSON.stringify(payload) }),

  /** The other caller path: qualify the lead and keep the answer in one row. */
  recordVerdict: (roomId, payload, params = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/verdicts${query(params)}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /** Recorded verdicts, newest first, narrowed by the dynamic index. */
  verdicts: (params = {}) => apiRequest(`${PREFIX}/verdicts${query(params)}`),
  verdict: (verdictId) => apiRequest(`${PREFIX}/verdicts/${verdictId}`),
}
