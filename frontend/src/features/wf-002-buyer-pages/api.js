import { apiRequest } from '@/lib/api'

/**
 * WF-002's API calls, on the feature's own prefix.
 *
 * These live here rather than on the shared `api` object because adding a method
 * there means editing a file every other feature is also editing, which is the
 * conflict the feature host exists to remove. `apiRequest` handles the envelope
 * and puts the HTTP status on the error, which this page needs: a 403 from a
 * room that gates its pages is a state to render, not a failure to report.
 *
 * The core reads at the bottom are the same idea. The editor needs the room's
 * documents, because a document selector takes "one file from the room's
 * documents, the same files listed in the room's Documents view", and the buyer
 * view needs the room record for the archived notice. Both go through
 * `apiRequest` against the generic record routes rather than through `api`, so
 * this folder talks to the whole product and nothing here has to be shared.
 */

const BASE = '/wf-002'

function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

function withParams(path, params = {}) {
  return `${path}${query(params)}`
}

function send(method, path, body) {
  return apiRequest(path, {
    method,
    body: body === undefined ? undefined : JSON.stringify(body),
  })
}

export const pagesApi = {
  // -- the fragment catalogue, discovered rather than hard-coded ------------ //
  catalogue: (params = {}) => apiRequest(`${BASE}/fragment-sets${query(params)}`),
  createFragmentSet: (payload, params = {}) =>
    send('POST', withParams(`${BASE}/fragment-sets`, params), payload),
  createFragment: (payload, params = {}) =>
    send('POST', withParams(`${BASE}/fragments`, params), payload),

  // -- pages: the editor --------------------------------------------------- //
  list: (roomId) => apiRequest(`${BASE}/rooms/${roomId}/pages`),
  create: (roomId, payload, params = {}) =>
    send('POST', withParams(`${BASE}/rooms/${roomId}/pages`, params), payload),
  get: (roomId, pageId) => apiRequest(`${BASE}/rooms/${roomId}/pages/${pageId}`),
  update: (roomId, pageId, payload, params = {}) =>
    send('PATCH', withParams(`${BASE}/rooms/${roomId}/pages/${pageId}`, params), payload),
  remove: (roomId, pageId, params = {}) =>
    send('DELETE', withParams(`${BASE}/rooms/${roomId}/pages/${pageId}`, params)),

  // -- blocks: placing and configuring fragments --------------------------- //
  addBlock: (roomId, pageId, payload, params = {}) =>
    send('POST', withParams(`${BASE}/rooms/${roomId}/pages/${pageId}/blocks`, params), payload),
  updateBlock: (roomId, pageId, blockId, payload, params = {}) =>
    send(
      'PATCH',
      withParams(`${BASE}/rooms/${roomId}/pages/${pageId}/blocks/${blockId}`, params),
      payload,
    ),
  removeBlock: (roomId, pageId, blockId, params = {}) =>
    send(
      'DELETE',
      withParams(`${BASE}/rooms/${roomId}/pages/${pageId}/blocks/${blockId}`, params),
    ),
  reorder: (roomId, pageId, order, params = {}) =>
    send(
      'PUT',
      withParams(`${BASE}/rooms/${roomId}/pages/${pageId}/blocks/order`, params),
      { order },
    ),

  // -- publish: the only step a buyer can see ------------------------------- //
  publish: (roomId, pageId, payload, params = {}) =>
    send(
      'POST',
      withParams(`${BASE}/rooms/${roomId}/pages/${pageId}/publish`, params),
      payload || {},
    ),
  unpublish: (roomId, pageId, params = {}) =>
    send('POST', withParams(`${BASE}/rooms/${roomId}/pages/${pageId}/unpublish`, params)),
  revisions: (roomId, pageId) => apiRequest(`${BASE}/rooms/${roomId}/pages/${pageId}/revisions`),

  // -- the buyer read path ------------------------------------------------- //
  view: (roomId) => apiRequest(`${BASE}/rooms/${roomId}/view`),
  viewPage: (roomId, slug) => apiRequest(`${BASE}/rooms/${roomId}/view/${slug}`),

  // -- core reads this page needs ------------------------------------------ //
  rooms: () => apiRequest('/records/room?limit=200&order_by=created_at&descending=false'),
  room: (roomId) => apiRequest(`/records/room/${roomId}`),
  documents: (roomId) =>
    apiRequest(`/records/document${withParams({ room_id: roomId, limit: 200 })}`),
}

/** 403 is the documented Room Collaborator requirement, not a failure to report. */
export function isPermissionDenied(error) {
  return error?.status === 403
}
