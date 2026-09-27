/**
 * The WF-013 conditional-rules API, owned by this feature.
 *
 * The branch added `ruleCatalog`, `ruleVariables`, `listBlocks`, `putRule` and
 * the rest to the shared `api` object in `@/lib/api`. That file is read by every
 * feature, so appending to it is exactly the collision the plugin host exists to
 * prevent: twelve workflow branches each added methods to one object and none
 * could merge. Everything here goes through `apiRequest`, the escape hatch the
 * host provides for precisely this case, so the shared client is never edited.
 *
 * The room list is read from the core `/records/room` collection rather than
 * imported from `api`, so this feature depends on the HTTP contract and not on
 * another module's idea of its shape.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-013'

/** Drop empty values so a filter is never sent as `?actor=`. */
function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const suffix = search.toString() ? `?${search}` : ''
  return suffix
}

export const rulesApi = {
  /** The rule vocabulary: categories, modifiers, limits, behaviours. */
  catalog: () => apiRequest(`${BASE}/catalog`),

  /** Every room, so the page can offer a room to personalise. */
  rooms: () => apiRequest('/records/room?limit=100'),

  /** Account and CRM variables merged, which is what a condition filters on. */
  variables: () => apiRequest(`${BASE}/variables`),

  listBlocks: (roomId) => apiRequest(`${BASE}/rooms/${roomId}/blocks`),

  createBlock: (roomId, payload) =>
    apiRequest(`${BASE}/rooms/${roomId}/blocks`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /** The block's rule, plus whether the block may carry one at all. */
  getRule: (roomId, blockId) => apiRequest(`${BASE}/rooms/${roomId}/blocks/${blockId}/rule`),

  putRule: (roomId, blockId, rule, params = {}) =>
    apiRequest(`${BASE}/rooms/${roomId}/blocks/${blockId}/rule${query(params)}`, {
      method: 'PUT',
      body: JSON.stringify(rule),
    }),

  deleteRule: (roomId, blockId, params = {}) =>
    apiRequest(`${BASE}/rooms/${roomId}/blocks/${blockId}/rule${query(params)}`, {
      method: 'DELETE',
    }),

  saveBlockToLibrary: (roomId, blockId, params = {}) =>
    apiRequest(`${BASE}/rooms/${roomId}/blocks/${blockId}/save-to-library${query(params)}`, {
      method: 'POST',
    }),

  // preview evaluates without writing; personalise records the decision. Both
  // return the same shape, so the page renders either from one code path.
  previewRoom: (roomId, variables) =>
    apiRequest(`${BASE}/rooms/${roomId}/preview`, {
      method: 'POST',
      body: JSON.stringify(variables),
    }),

  personaliseRoom: (roomId, variables) =>
    apiRequest(`${BASE}/rooms/${roomId}/personalise`, {
      method: 'POST',
      body: JSON.stringify(variables),
    }),

  listPersonalisations: (roomId) => apiRequest(`${BASE}/rooms/${roomId}/personalisations`),
}
