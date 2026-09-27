/**
 * Sales impact API calls (WF-023).
 *
 * These live in the feature folder rather than on the shared `api` object in `@/lib/api`,
 * because that file is shared and a hundred features each adding a method to it is
 * precisely the conflict the feature host exists to remove. The host's `apiRequest` is
 * the escape hatch that makes it unnecessary.
 *
 * `PREFIX` is the feature's own: the backend module mounts these under `/api/wf-023`, and
 * `apiRequest` already prepends `/api`.
 *
 * The payloads stay schema-flexible. A CRM field map is arbitrary JSON keyed by whatever
 * the CRM calls its fields, and the pickers on the page are rendered from the
 * `/vocabulary` endpoint rather than from a list compiled into this file, so a team that
 * adds a CRM field ships a record instead of a change to this file.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-023'

/**
 * Build a query string, dropping anything empty.
 *
 * Empty values are dropped rather than sent as `?team=` because the server treats a
 * present-but-empty filter as "filter on nothing" in some parsers and as "no filter" in
 * others, and a page that flickers between the two readings when a field is cleared is a
 * page nobody trusts.
 */
export function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue
    search.set(key, Array.isArray(value) ? value.join(',') : String(value))
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const impactApi = {
  /** The whole researched report. */
  report: (params = {}) => apiRequest(`${PREFIX}/report${query(params)}`),

  /** One workspace's contribution, and whether it is in the report at all. */
  room: (roomId, params = {}) => apiRequest(`${PREFIX}/report/rooms/${roomId}${query(params)}`),

  /** The tile drill-in: the filtered in-scope deals. */
  deals: (params = {}) => apiRequest(`${PREFIX}/deals${query(params)}`),

  /** Attach a CRM deal to a workspace. The one write a seller makes by hand. */
  registerDeal: (payload, params = {}) =>
    apiRequest(`${PREFIX}/deals${query(params)}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /** The stage/amount sync. */
  patchDeal: (dealId, payload) =>
    apiRequest(`${PREFIX}/deals/${dealId}`, { method: 'PATCH', body: JSON.stringify(payload) }),

  /** Detach a deal. Soft, on the server. */
  detachDeal: (dealId) => apiRequest(`${PREFIX}/deals/${dealId}`, { method: 'DELETE' }),

  /** The Most Engaged Buyers ranking. */
  buyers: (params = {}) => apiRequest(`${PREFIX}/buyers${query(params)}`),

  /** Buyer views, actions, and the views series. */
  engagement: (params = {}) => apiRequest(`${PREFIX}/engagement${query(params)}`),

  /** What is missing from the report, and which workspaces are responsible. */
  coverage: () => apiRequest(`${PREFIX}/coverage`),

  /** The CRM integration state the report's completeness depends on. */
  integration: () => apiRequest(`${PREFIX}/integration`),

  /** Turn the integration on or off. */
  setIntegration: (payload) =>
    apiRequest(`${PREFIX}/integration`, { method: 'PATCH', body: JSON.stringify(payload) }),

  /**
   * The published vocabulary and the documented API constraints.
   *
   * Drives every picker on the page: the stage classes, the won and lost stage sets, the
   * filter vocabulary, and the series buckets.
   */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),

  /**
   * Every design decision the research does not fix, with what was quoted and what was
   * chosen.
   *
   * The page renders this from the server rather than from copy in this folder, so the
   * page cannot drift from the registry a reviewer is asked to disagree with.
   */
  inferences: () => apiRequest(`${PREFIX}/inferences`),
}
