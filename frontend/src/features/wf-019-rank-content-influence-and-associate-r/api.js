/**
 * The WF-019 Content Influence API, owned by this feature.
 *
 * Every call goes through `apiRequest`, the escape hatch `@/lib/api` provides
 * for exactly this case. The alternative - appending methods to the shared `api`
 * object - is what made the twelve original workflow branches mutually
 * unmergeable: they all edited the same object and none could merge.
 *
 * The filter set is built in one place and reused by every read, because the
 * researched report is three surfaces describing the same question. A page that
 * sent a different filter to the table than to the tiles would show numbers that
 * do not add up, and the reviewer would blame the data.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-019'

/** Drop empty values so a filter is never sent as `?collection=`. */
function query(filters = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const suffix = search.toString() ? `?${search.toString()}` : ''
  return suffix
}

/** The research's filter set, minus the workspace, which is a path segment. */
export function contentFilters(state) {
  return {
    collection: state.collection,
    activity_from: state.activityFrom,
    activity_to: state.activityTo,
    shared_from: state.sharedFrom,
    shared_to: state.sharedTo,
  }
}

export const influenceApi = {
  /** Grains, sortable columns, accepted actions and window names. */
  vocabulary: () => apiRequest(`${BASE}/vocabulary`),

  /** Every design choice this build made, with what would change it. */
  inferences: () => apiRequest(`${BASE}/inferences`),

  /** The five researched portfolio metrics. */
  portfolio: (filters) => apiRequest(`${BASE}/portfolio${query(filters)}`),

  /** The researched trend, at day / week / month / quarter / year. */
  engagement: (filters, grain) => apiRequest(`${BASE}/engagement${query({ ...filters, grain })}`),

  /** The researched Top content table, sortable by any column. */
  topContent: (filters, { sort, direction, limit, offset } = {}) =>
    apiRequest(`${BASE}/top-content${query({ ...filters, sort, direction, limit, offset })}`),

  /** Content & Sales Influence. `strict` turns a missing link into a 428. */
  salesInfluence: (filters) => apiRequest(`${BASE}/sales-influence${query(filters)}`),

  /** Which CRM links hold, on their own, for a setup checklist. */
  preconditions: () => apiRequest(`${BASE}/sales-influence/preconditions`),

  /** Library collections, for the filter picker. */
  collections: () => apiRequest(`${BASE}/collections`),

  /** One asset's card, with every event behind its numbers. */
  asset: (assetId, filters) => apiRequest(`${BASE}/assets/${assetId}${query(filters)}`),

  /** The same report, computed over one workspace. */
  room: (roomId, filters) => apiRequest(`${BASE}/rooms/${roomId}/influence${query(filters)}`),

  /** The recorded CRM links, so a reader can check the join's inputs. */
  links: () => apiRequest(`${BASE}/links`),

  /** Record one asset.viewed / .shared / .downloaded. */
  recordEvent: (payload, params = {}) =>
    apiRequest(`${BASE}/events${query(params)}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /** Project the product's own activity rows into the event log. */
  ingestActivity: (params = {}) =>
    apiRequest(`${BASE}/events/ingest-activity${query(params)}`, { method: 'POST' }),

  /** Connect a CRM deal to a workspace and the assets it influenced. */
  linkDeal: (payload, params = {}) =>
    apiRequest(`${BASE}/links/deals${query(params)}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /** Register the CRM account a deal is attributed to. */
  linkAccount: (payload, params = {}) =>
    apiRequest(`${BASE}/links/accounts${query(params)}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /** Every workspace, for the workspace scope. */
  rooms: () => apiRequest('/records/room?limit=100'),
}
