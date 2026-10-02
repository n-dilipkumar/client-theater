/**
 * Revocation API calls (WF-076).
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a
 * method to it is precisely the conflict the feature host exists to remove. The
 * host's `apiRequest` is the escape hatch that makes that unnecessary.
 *
 * `PREFIX` is the feature's own: the backend module mounts these under
 * `/api/wf-076`, and `apiRequest` already prepends `/api`.
 *
 * The two destructive calls - `deleteGroup` and `purge` - take a `confirm`
 * argument the caller has to pass explicitly. That is the backend's gate, not a
 * courtesy this file adds: the researched workflow calls both irreversible, and
 * a confirmation the client can send blind gates nothing. Every caller on the
 * page therefore has to supply the id it is about to destroy, which is what
 * makes a hard-coded `true` impossible here as well.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-076'

function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value))
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

function post(path, payload) {
  return apiRequest(path, { method: 'POST', body: JSON.stringify(payload ?? {}) })
}

export const revocationApi = {
  /** The researched operations, the quote behind each, and the guarantee. */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),

  /** What the research leaves open, what this build chose, and how to change it. */
  inferences: () => apiRequest(`${PREFIX}/inferences`),

  /** Counts per collection and per room, from the records rather than a counter. */
  summary: () => apiRequest(`${PREFIX}/summary`),

  roomState: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/state`),

  listLinks: (roomId, params = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/links${query(params)}`),
  link: (roomId, linkId, params = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/links/${linkId}${query(params)}`),
  createLink: (roomId, payload) => post(`${PREFIX}/rooms/${roomId}/links`, payload),

  /**
   * Cut the public URL. Immediate, with no grace period and no way back.
   *
   * The response carries the retained row and its audit trail, so the page can
   * show the evidence in the same breath as the cut.
   */
  revokeLink: (roomId, linkId, payload = {}) =>
    post(`${PREFIX}/rooms/${roomId}/links/${linkId}/revoke`, payload),

  listGroups: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/groups`),
  group: (roomId, groupId) => apiRequest(`${PREFIX}/rooms/${roomId}/groups/${groupId}`),
  createGroup: (roomId, payload) => post(`${PREFIX}/rooms/${roomId}/groups`, payload),

  /** Irreversible. `confirm` must equal the group id. */
  deleteGroup: (roomId, groupId, confirm) =>
    post(`${PREFIX}/rooms/${roomId}/groups/${groupId}/delete`, { confirm }),

  addMember: (roomId, groupId, payload) =>
    post(`${PREFIX}/rooms/${roomId}/groups/${groupId}/members`, payload),

  /** Removes one buyer's membership. The underlying viewer record is kept. */
  removeMember: (roomId, groupId, memberId) =>
    post(`${PREFIX}/rooms/${roomId}/groups/${groupId}/members/${memberId}/remove`),

  /** Flip `view` / `download` for one item, or several at once. */
  setPermissions: (roomId, groupId, permissions) =>
    post(`${PREFIX}/rooms/${roomId}/groups/${groupId}/permissions`, { permissions }),

  listViewers: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/viewers`),

  listDocuments: (roomId, params = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/documents${query(params)}`),
  attachDocument: (roomId, payload) => post(`${PREFIX}/rooms/${roomId}/documents`, payload),
  detachDocument: (roomId, documentId) =>
    post(`${PREFIX}/rooms/${roomId}/documents/${documentId}/detach`),

  /** Unrecoverable. `confirm` must equal the room id. */
  purge: (roomId, confirm) => post(`${PREFIX}/rooms/${roomId}/purge`, { confirm }),

  /**
   * Does this public URL still work, for this requester, right now?
   *
   * The researched guarantee as an endpoint: request-time, not scheduled. Call
   * it before and after a revoke and the answer changes on the revoke.
   */
  resolve: (roomId, params = {}) => apiRequest(`${PREFIX}/rooms/${roomId}/resolve${query(params)}`),

  slugStatus: (roomId, slug) => apiRequest(`${PREFIX}/rooms/${roomId}/slugs/${slug}`),

  /** A revoked record and the audit trail written for it. */
  retained: (roomId, recordId) => apiRequest(`${PREFIX}/rooms/${roomId}/revocations/${recordId}`),

  /** Every audit row this feature's own routes wrote in one room, newest first. */
  trail: (roomId, params = {}) => apiRequest(`${PREFIX}/rooms/${roomId}/trail${query(params)}`),
}

export default revocationApi