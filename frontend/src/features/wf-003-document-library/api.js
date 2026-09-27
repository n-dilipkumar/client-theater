/**
 * The WF-003 document library API, owned by this feature.
 *
 * The branch added these methods to the shared `api` object in `@/lib/api`.
 * That file is read by every feature, so appending to it is exactly the
 * collision the plugin host exists to prevent. Everything here goes through
 * `apiRequest`, the escape hatch the host provides for precisely this case, so
 * the shared client is never edited.
 *
 * The room list is fetched from the core `/records/room` collection rather than
 * imported from `api`, so this feature depends on the HTTP contract and not on
 * another module's idea of its shape.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-003'

/** Drop empty values so we never send `?search=` and confuse a filter. */
function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const suffix = search.toString() ? `?${search}` : ''
  return suffix
}

export const libraryApi = {
  /** The workflow vocabulary: statuses, transitions, roles, gallery slots. */
  workflow: () => apiRequest(`${BASE}/document-workflow`),

  /** Every room, so the page can offer a library to look at. */
  rooms: () => apiRequest('/records/room?limit=100'),

  listDocuments: (roomId, params = {}) =>
    apiRequest(`${BASE}/rooms/${roomId}/documents${query(params)}`),

  getDocument: (roomId, documentId, params = {}) =>
    apiRequest(`${BASE}/rooms/${roomId}/documents/${documentId}${query(params)}`),

  addDocument: (roomId, payload, params = {}) =>
    apiRequest(`${BASE}/rooms/${roomId}/documents${query(params)}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  updateDocument: (roomId, documentId, payload, params = {}) =>
    apiRequest(`${BASE}/rooms/${roomId}/documents/${documentId}${query(params)}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),

  setDocumentStatus: (roomId, documentId, status, params = {}) =>
    apiRequest(`${BASE}/rooms/${roomId}/documents/${documentId}/status${query(params)}`, {
      method: 'PUT',
      body: JSON.stringify({ status }),
    }),

  deleteDocument: (roomId, documentId, params = {}) =>
    apiRequest(`${BASE}/rooms/${roomId}/documents/${documentId}${query(params)}`, {
      method: 'DELETE',
    }),

  listGalleryBlocks: (roomId) => apiRequest(`${BASE}/rooms/${roomId}/document-gallery`),

  /** One call for create and replace: the server routes on the block id. */
  saveGalleryBlock: (roomId, payload, params = {}, blockId = null) =>
    blockId
      ? apiRequest(`${BASE}/rooms/${roomId}/document-gallery/${blockId}${query(params)}`, {
          method: 'PUT',
          body: JSON.stringify(payload),
        })
      : apiRequest(`${BASE}/rooms/${roomId}/document-gallery${query(params)}`, {
          method: 'POST',
          body: JSON.stringify(payload),
        }),
}
