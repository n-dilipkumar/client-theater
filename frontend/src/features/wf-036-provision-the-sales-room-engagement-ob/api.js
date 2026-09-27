/**
 * CRM provisioning API calls (WF-036).
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a
 * method to it is precisely the conflict the feature host exists to remove. The
 * host's `apiRequest` is the escape hatch that makes that unnecessary.
 *
 * `PREFIX` is the feature's own: the backend module mounts these under
 * `/api/wf-036`, and `apiRequest` already prepends `/api`.
 *
 * The manifest body sent to `registerManifest` is arbitrary JSON — a deployment
 * ships its own object descriptor, and nothing in this file knows the shape of
 * one. The pickers on the page are rendered from the vocabulary endpoint rather
 * than from a list compiled here, so a team that adds a property type ships a
 * mapping in `crm_provisioning/vendors.py` and nothing here changes.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-036'

function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const provisioningApi = {
  /** Every value the workflow enforces against, plus both vendor surfaces. */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),

  /**
   * Every judgement call the workflow rests on, and how to change each one.
   *
   * The research states its own gap about Salesforce in those words. The parts
   * that are therefore judgement are served as data rather than buried in
   * comments, so a reviewer can disagree with a named entry instead of having to
   * find it in a diff.
   */
  inferences: () => apiRequest(`${PREFIX}/inferences`),

  listConnections: (params = {}) => apiRequest(`${PREFIX}/connections${query(params)}`),
  createConnection: (payload) =>
    apiRequest(`${PREFIX}/connections`, { method: 'POST', body: JSON.stringify(payload) }),
  readConnection: (id) => apiRequest(`${PREFIX}/connections/${id}`),

  listManifests: (params = {}) => apiRequest(`${PREFIX}/manifests${query(params)}`),
  registerManifest: (payload) =>
    apiRequest(`${PREFIX}/manifests`, { method: 'POST', body: JSON.stringify(payload) }),
  readManifest: (id, params = {}) => apiRequest(`${PREFIX}/manifests/${id}${query(params)}`),

  /**
   * The dry-run diff view. A read, not a mode: it cannot write, so asking for a
   * preview can never create something.
   */
  diff: (params = {}) => apiRequest(`${PREFIX}/diff${query(params)}`),

  /** The endpoint an admin presses and a CI job calls. */
  install: (payload) =>
    apiRequest(`${PREFIX}/install`, { method: 'POST', body: JSON.stringify(payload) }),

  listObjects: (params = {}) => apiRequest(`${PREFIX}/objects${query(params)}`),
  readObject: (id) => apiRequest(`${PREFIX}/objects/${id}`),
  listObjectProperties: (id) => apiRequest(`${PREFIX}/objects/${id}/properties`),
  addObjectProperty: (id, payload) =>
    apiRequest(`${PREFIX}/objects/${id}/properties`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  listKeys: (params = {}) => apiRequest(`${PREFIX}/keys${query(params)}`),
  readKey: (id) => apiRequest(`${PREFIX}/keys/${id}`),
  pollKey: (id) => apiRequest(`${PREFIX}/keys/${id}/poll`, { method: 'POST' }),
  reactivateKey: (id) => apiRequest(`${PREFIX}/keys/${id}/reactivate`, { method: 'POST' }),

  /** The run log: every installer run, with the requests it sent. */
  listInstallations: (params = {}) => apiRequest(`${PREFIX}/installations${query(params)}`),
  readInstallation: (id) => apiRequest(`${PREFIX}/installations/${id}`),

  roomObjects: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/objects`),
  roomSummary: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/summary`),
}
