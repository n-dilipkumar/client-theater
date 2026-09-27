/**
 * The WF-010 library-search API, owned by this feature.
 *
 * The branch added eight methods to the shared `api` object in `@/lib/api`. That
 * file is read by every feature, so appending to it is exactly the collision the
 * plugin host exists to prevent. Everything here goes through `apiRequest`, the
 * escape hatch the host provides for precisely this case, so the shared client is
 * never edited.
 *
 * `BASE` is this feature's own router prefix. Keeping it in one place is what
 * stops a path from drifting out of step with the backend module that serves it.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/library'

/** Drop empty values so we never send `?actor=` and confuse a filter. */
function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const suffix = search.toString() ? `?${search}` : ''
  return suffix
}

export const libraryApi = {
  /**
   * The operational contract: limits, field vocabulary, operators. The page reads
   * its own defaults from this rather than hard-coding a second copy.
   */
  contract: () => apiRequest(`${BASE}/contract`),

  /** Which fields actually carry a value in the library right now. */
  fields: () => apiRequest(`${BASE}/fields`),

  /**
   * Search the content library. The query body is passed through as given, so a
   * field this client has never heard of still reaches the server intact, which is
   * the whole point of a schema-flexible search surface.
   */
  search: (body, continuationToken) =>
    apiRequest(`${BASE}/search${query({ continuationToken })}`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  /** Attach the picked documents to a room. One transaction, one audit row. */
  assemble: (payload, params = {}) =>
    apiRequest(`${BASE}/assemble${query(params)}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /** The room list, from core records, so the page can offer somewhere to put it. */
  rooms: () => apiRequest('/records/room?limit=100'),

  savedSearches: () => apiRequest(`${BASE}/searches`),

  saveSearch: (payload, params = {}) =>
    apiRequest(`${BASE}/searches${query(params)}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  deleteSavedSearch: (recordId) =>
    apiRequest(`${BASE}/searches/${recordId}`, { method: 'DELETE' }),
}
