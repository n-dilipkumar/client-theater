/**
 * The WF-007 content library API, owned by this feature.
 *
 * The branch added an `api.library` object to the shared client in
 * `@/lib/api`, plus a `byteSize` helper. That file is read by every feature, so
 * appending to it is exactly the collision the plugin host exists to prevent.
 * Everything here goes through `apiRequest`, the escape hatch the host provides
 * for precisely this case, so the shared client is never edited.
 *
 * The room list is fetched from the core `/records/room` collection rather than
 * imported from `api`, so this feature depends on the HTTP contract and not on
 * another module's idea of its shape.
 *
 * One shared-file defect is worked around here, and it needs a human to fix it
 * at the platform level
 * ------------------------------------------------------------------
 * `apiRequest` sets `Content-Type: application/json` on every request. That
 * header is wrong for a `FormData` body: the browser has to choose the
 * multipart content type itself so it can add the boundary, and a hand-set
 * header makes the request unparseable server-side. The branch fixed this in
 * `lib/api.js` by skipping the header when the body is a `FormData`, which is
 * the right fix and the wrong place for it from a feature.
 *
 * Rather than edit a shared file, the two multipart calls below go through
 * `postMultipart`, which omits the header and otherwise mirrors `apiRequest`'s
 * error contract, so the page handles one error shape everywhere. The
 * integrator should promote that one-line branch into `apiRequest` and delete
 * this helper; the ingest is the researched contract, not a quirk of this
 * screen, so the next workflow to upload a file will need it too.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/library'

/** Drop empty values so we never send `?search=` and confuse a filter. */
function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const suffix = search.toString() ? `?${search}` : ''
  return suffix
}

/**
 * Send a `FormData` body, without the JSON content type.
 *
 * Deliberately the same error shape `apiRequest` produces -- an `Error` whose
 * `message` is the server's `detail` or `error`, carrying `status` -- so a
 * caller can branch on the status without knowing which helper made the call.
 */
async function sendForm(path, method, form) {
  const response = await fetch(`/api${path}`, { method, body: form })

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`
    try {
      const body = await response.json()
      detail = body.detail || body.error || detail
    } catch {
      // Non-JSON error body; the status line is the best we have.
    }
    const error = new Error(detail)
    error.status = response.status
    throw error
  }
  return response.json()
}

export const libraryApi = {
  /** The fixed vocabulary, for a form to render without hard-coding it. */
  vocabulary: () => apiRequest(`${BASE}/vocabulary`),

  /** Every room, so the page can offer a library to look at. */
  rooms: () => apiRequest('/records/room?limit=100'),

  usage: (roomId) => apiRequest(`${BASE}/rooms/${roomId}/usage`),

  folders: (roomId) => apiRequest(`${BASE}/rooms/${roomId}/folders`),

  documents: (roomId, params = {}) => apiRequest(`${BASE}/rooms/${roomId}/documents${query(params)}`),

  document: (documentId) => apiRequest(`${BASE}/documents/${documentId}`),

  /**
   * Ingest a binary plus its metadata part.
   *
   * Multipart, because that is the researched contract: a JSON `metadata` part
   * and a binary `content` part, not a JSON body with the file encoded into it.
   */
  ingest: (roomId, { file, metadata, resolveNameCollision = true, rollbackOnError = true }) => {
    const form = new FormData()
    form.append('metadata', JSON.stringify(metadata))
    form.append('content', file, file.name)
    form.append('resolveNameCollision', String(resolveNameCollision))
    form.append('rollbackOnError', String(rollbackOnError))
    return sendForm(`${BASE}/rooms/${roomId}/documents`, 'POST', form)
  },

  /** Add a version. Never replaces the binary of an existing document. */
  addVersion: (documentId, file) => {
    const form = new FormData()
    form.append('content', file, file.name)
    return sendForm(`${BASE}/documents/${documentId}`, 'PUT', form)
  },

  deriveThumbnail: (documentId) =>
    apiRequest(`${BASE}/documents/${documentId}/thumbnail`, { method: 'POST' }),

  remove: (documentId, params = {}) =>
    apiRequest(`${BASE}/documents/${documentId}${query(params)}`, { method: 'DELETE' }),

  thumbnailUrl: (documentId) => `/api${BASE}/documents/${documentId}/thumbnail`,

  contentUrl: (documentId, versionId) => `/api${BASE}/documents/${documentId}/content${query({ versionId })}`,
}
