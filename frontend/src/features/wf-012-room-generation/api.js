/**
 * Room generation API calls (WF-012).
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a
 * method to it is precisely the conflict the feature host exists to remove.
 * `apiRequest` is the host's generic escape hatch: it handles the envelope and
 * puts the HTTP status on the thrown error, which this page needs in order to
 * tell "no templates yet" (404 on one id) from "the call failed".
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-012'

function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const generationApi = {
  listTemplates: (params = {}) => apiRequest(`${PREFIX}/templates${query(params)}`),
  declareTemplate: (payload) =>
    apiRequest(`${PREFIX}/templates`, { method: 'POST', body: JSON.stringify(payload) }),

  /** Render a request without writing anything: the operator's safety net. */
  preview: (payload) =>
    apiRequest(`${PREFIX}/templates/preview`, { method: 'POST', body: JSON.stringify(payload) }),

  generate: (payload) =>
    apiRequest(`${PREFIX}/generations`, { method: 'POST', body: JSON.stringify(payload) }),
  generateMany: (items) =>
    apiRequest(`${PREFIX}/generations/bulk`, { method: 'POST', body: JSON.stringify(items) }),
  listGenerations: (params = {}) => apiRequest(`${PREFIX}/generations${query(params)}`),
  publish: (roomId) => apiRequest(`${PREFIX}/generations/${roomId}/publish`, { method: 'POST' }),
}
