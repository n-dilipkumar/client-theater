/**
 * CRM sync API calls (WF-016).
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a
 * method to it is precisely the conflict the feature host exists to remove. The
 * branch added fourteen methods to it; the host's `apiRequest` is the escape
 * hatch that makes that unnecessary.
 *
 * `PREFIX` is the feature's own: the backend module mounts these under
 * `/api/wf-016`, and `apiRequest` already prepends `/api`.
 *
 * The payloads stay schema-flexible. The field map inside an automation is
 * arbitrary JSON keyed by whatever the CRM calls its fields, and the pickers on
 * the page are rendered from the vocabulary endpoint rather than from a list
 * compiled into this file, so a team that adds a CRM field ships a record
 * instead of a change to this file.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-016'

function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const crmApi = {
  /** The published enums. Drives every picker on the page. */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),
  presets: () => apiRequest(`${PREFIX}/presets`),

  /**
   * Every design inference the workflow rests on, and how to change each one.
   *
   * The research for this workflow states its own limits: it documents the
   * webhook contract and the five page statuses, and explicitly makes no claims
   * about a Salesforce endpoint. The parts that are therefore judgement calls
   * are served as data rather than buried in comments, so a rep or a reviewer
   * can disagree with a named entry instead of having to find it in a diff.
   */
  inferences: () => apiRequest(`${PREFIX}/inferences`),

  /** Declared CRM fields. Optional, but it is what makes the lint specific. */
  listFields: () => apiRequest(`${PREFIX}/fields`),
  registerField: (payload) =>
    apiRequest(`${PREFIX}/fields`, { method: 'POST', body: JSON.stringify(payload) }),

  listSubscriptions: (params = {}) => apiRequest(`${PREFIX}/subscriptions${query(params)}`),
  subscribe: (payload) =>
    apiRequest(`${PREFIX}/subscriptions`, { method: 'POST', body: JSON.stringify(payload) }),
  unsubscribe: (id) => apiRequest(`${PREFIX}/subscriptions/${id}`, { method: 'DELETE' }),

  listAutomations: () => apiRequest(`${PREFIX}/automations`),
  createAutomation: (payload) =>
    apiRequest(`${PREFIX}/automations`, { method: 'POST', body: JSON.stringify(payload) }),
  updateAutomation: (id, payload) =>
    apiRequest(`${PREFIX}/automations/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteAutomation: (id) => apiRequest(`${PREFIX}/automations/${id}`, { method: 'DELETE' }),

  listEvents: (params = {}) => apiRequest(`${PREFIX}/events${query(params)}`),
  recordEvent: (payload, params = {}) =>
    apiRequest(`${PREFIX}/events${query(params)}`, { method: 'POST', body: JSON.stringify(payload) }),

  /** The Activity Log: the surface a rep actually reads. */
  activity: (params = {}) => apiRequest(`${PREFIX}/activity${query(params)}`),
}
