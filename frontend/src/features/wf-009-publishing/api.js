/**
 * The WF-009 approval and publication API, owned by this feature.
 *
 * The branch added a `publishing` namespace to the shared `api` object in
 * `@/lib/api`. That file is read by every feature, so appending to it is exactly
 * the collision the plugin host exists to prevent. Everything here goes through
 * `apiRequest`, the escape hatch the host provides for precisely this case, so
 * the shared client is never edited.
 *
 * The room and document lists are fetched from the core `/records` collections
 * rather than from `api`, so this feature depends on the HTTP contract and not on
 * another module's idea of its shape.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/publishing'

/** Drop empty values so we never send `?status=` and confuse a filter. */
function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const suffix = search.toString() ? `?${search}` : ''
  return suffix
}

export const publishingApi = {
  // -- approval processes ------------------------------------------------- //

  processes: () => apiRequest(`${BASE}/processes`),
  createProcess: (payload) =>
    apiRequest(`${BASE}/processes`, { method: 'POST', body: JSON.stringify(payload) }),

  // -- submissions and the queue ------------------------------------------- //

  submit: (content, processId) =>
    apiRequest(`${BASE}/submissions`, {
      method: 'POST',
      body: JSON.stringify({ content, process_id: processId }),
    }),

  workflows: (params = {}) => apiRequest(`${BASE}/workflows${query(params)}`),
  workflow: (id) => apiRequest(`${BASE}/workflows/${id}`),

  // `actor` travels as a query parameter and the verdict in the body, matching
  // the API: the reviewer is the caller, the verdict is the payload.
  decide: (workflowId, stepKey, decision, { actor, comment } = {}) =>
    apiRequest(`${BASE}/workflows/${workflowId}/steps/${stepKey}${query({ actor })}`, {
      method: 'POST',
      body: JSON.stringify({ decision, comment }),
    }),

  // -- publication --------------------------------------------------------- //

  publish: (payload) => apiRequest(`${BASE}/publish`, { method: 'POST', body: JSON.stringify(payload) }),
  publications: (params = {}) => apiRequest(`${BASE}/publications${query(params)}`),
  runDue: (now) =>
    apiRequest(`${BASE}/publications/due`, {
      method: 'POST',
      body: JSON.stringify(now ? { now } : {}),
    }),

  // -- destinations and subscribers ---------------------------------------- //

  folders: () => apiRequest(`${BASE}/folders`),
  createFolder: (payload) =>
    apiRequest(`${BASE}/folders`, { method: 'POST', body: JSON.stringify(payload) }),
  subscriptions: () => apiRequest(`${BASE}/subscriptions`),
  createSubscription: (payload) =>
    apiRequest(`${BASE}/subscriptions`, { method: 'POST', body: JSON.stringify(payload) }),

  // -- core collections this page reads ------------------------------------ //

  documents: () => apiRequest('/records/document?limit=100'),
}
