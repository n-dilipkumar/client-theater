/**
 * Pipeline triage API calls (WF-022).
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a
 * method to it is precisely the conflict the feature host exists to remove. The
 * host's `apiRequest` is the escape hatch that makes that unnecessary.
 *
 * `PREFIX` is the feature's own: the backend module mounts these under
 * `/api/wf-022`, and `apiRequest` already prepends `/api`.
 *
 * The pickers on the page are rendered from the vocabulary endpoint rather than
 * from a list compiled into this file, so a team that starts syncing a new CRM
 * field ships a record rather than a change to a shared file.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-022'

function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const triageApi = {
  /** Columns, operators, and how the two filter params differ. Drives every picker. */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),

  /** The five default views, each with the research's definition where it has one. */
  defaultViews: () => apiRequest(`${PREFIX}/default-views`),

  /**
   * Every design inference this workflow rests on, and how to change each one.
   *
   * The research documents this workflow as a UI capability and gives two of the
   * five default views a definition. The parts that are therefore judgement calls
   * are served as data rather than buried in comments, so a reviewer can disagree
   * with a named entry instead of hunting through a diff.
   */
  inferences: () => apiRequest(`${PREFIX}/inferences`),

  /** Step one of the flow: the remembered open views, the defaults, your own views. */
  dashboard: (actor) => apiRequest(`${PREFIX}/dashboard${query({ actor })}`),

  listViews: (params = {}) => apiRequest(`${PREFIX}/views${query(params)}`),
  readView: (id, params = {}) => apiRequest(`${PREFIX}/views/${id}${query(params)}`),
  createView: (payload, params = {}) =>
    apiRequest(`${PREFIX}/views${query(params)}`, { method: 'POST', body: JSON.stringify(payload) }),
  updateView: (id, payload, params = {}) =>
    apiRequest(`${PREFIX}/views/${id}${query(params)}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteView: (id, params = {}) => apiRequest(`${PREFIX}/views/${id}${query(params)}`, { method: 'DELETE' }),
  cloneView: (id, payload = {}, params = {}) =>
    apiRequest(`${PREFIX}/views/${id}/clone${query(params)}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /** The joined, filtered, sorted slice. The triage table itself. */
  rows: (id, params = {}) => apiRequest(`${PREFIX}/views/${id}/rows${query(params)}`),

  /** One workspace's row, honouring the researched `properties` parameter. */
  roomRow: (roomId, properties) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/row${query({ properties: properties?.join(',') })}`),

  roomType: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/type`),
  setRoomType: (roomId, payload, params = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/type${query(params)}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),

  /** Template Settings: the type a template imposes on the workspaces made from it. */
  templates: () => apiRequest(`${PREFIX}/templates`),
  setTemplateType: (templateId, payload, params = {}) =>
    apiRequest(`${PREFIX}/templates/${templateId}${query(params)}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),

  /** Dynamic workspaces: which sections are shown, and why. */
  sections: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/sections`),
  setSections: (roomId, payload, params = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/sections${query(params)}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),

  /** "We'll remember which views you had open" - per user account. */
  openViews: (actor) => apiRequest(`${PREFIX}/open-views${query({ actor })}`),
  rememberOpenViews: (payload) =>
    apiRequest(`${PREFIX}/open-views`, { method: 'PUT', body: JSON.stringify(payload) }),
}
