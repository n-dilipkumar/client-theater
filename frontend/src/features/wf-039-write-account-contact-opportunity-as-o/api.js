/**
 * API calls for the atomic opportunity bundle (WF-039).
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a
 * method to it is precisely the conflict the feature host exists to remove. The
 * host's `apiRequest` is the escape hatch that makes that unnecessary.
 *
 * `PREFIX` is the feature's own: the backend module mounts these under
 * `/api/wf-039`, and `apiRequest` already prepends `/api`.
 *
 * Nothing here hard-codes a payload shape. The dialect, the rollback policy and
 * the ordering toggle are rendered from the server's own vocabulary endpoint, and
 * the subrequest order comes from the preview - so a deployment that adds a
 * fifth dialect ships a record, not a change to this file.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-039'

function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

const room = (roomId, tail = '') => `/rooms/${encodeURIComponent(roomId)}${tail}`

export const bundleApi = {
  /** The researched vocabulary: four dialects, two reference syntaxes, six limits. */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),

  /** Every design inference this workflow rests on, and how to change each one. */
  inferences: () => apiRequest(`${PREFIX}/inferences`),

  // -- connectors: which CRM, and how it is spoken to ----------------------- //

  listConnectors: () => apiRequest(`${PREFIX}/connectors`),
  createConnector: (payload) =>
    apiRequest(`${PREFIX}/connectors`, { method: 'POST', body: JSON.stringify(payload) }),
  updateConnector: (id, payload) =>
    apiRequest(`${PREFIX}/connectors/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  deleteConnector: (id) =>
    apiRequest(`${PREFIX}/connectors/${encodeURIComponent(id)}`, { method: 'DELETE' }),

  // -- bundles: the dependency graph, declared as data ----------------------- //

  listBundles: (roomId, params = {}) =>
    apiRequest(room(roomId, `/bundles${query(params)}`)),
  createBundle: (roomId, payload) =>
    apiRequest(room(roomId, '/bundles'), { method: 'POST', body: JSON.stringify(payload) }),
  readBundle: (roomId, bundleId) => apiRequest(room(roomId, `/bundles/${encodeURIComponent(bundleId)}`)),
  updateBundle: (roomId, bundleId, payload) =>
    apiRequest(room(roomId, `/bundles/${encodeURIComponent(bundleId)}`), {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  deleteBundle: (roomId, bundleId) =>
    apiRequest(room(roomId, `/bundles/${encodeURIComponent(bundleId)}`), { method: 'DELETE' }),

  // -- the researched bundle preview, and the commit ------------------------- //

  /**
   * A dry run: the subrequest order, the exact request, the warnings and the
   * blockers. Writes nothing, and accepts the same three settings a commit does,
   * so the other settings can be tried before committing to one.
   */
  preview: (roomId, bundleId, payload = {}) =>
    apiRequest(room(roomId, `/bundles/${encodeURIComponent(bundleId)}/preview`), {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /** The researched write. One explicit request, one run record. */
  commit: (roomId, bundleId, payload = {}) =>
    apiRequest(room(roomId, `/bundles/${encodeURIComponent(bundleId)}/commit`), {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  // -- the run log, and what the CRM now holds ------------------------------ //

  listRuns: (roomId, params = {}) => apiRequest(room(roomId, `/runs${query(params)}`)),
  readRun: (roomId, runId) => apiRequest(room(roomId, `/runs/${encodeURIComponent(runId)}`)),
  listTargets: (roomId) => apiRequest(room(roomId, '/targets')),
}

/** Rooms, read through the shared generic surface rather than this feature's. */
export const listRooms = (params = {}) => apiRequest(`/records/room${query(params)}`)
