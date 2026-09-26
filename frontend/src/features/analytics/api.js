/**
 * Engagement analytics API calls (WF-006).
 *
 * These live here rather than on the shared `api` object so that adding a
 * feature does not mean editing a file every other feature is also editing.
 * `apiRequest` handles the envelope and puts the HTTP status on the error, which
 * this page needs: 428 means the workspace is not connected yet, which is a
 * state to render, not a failure to report.
 */

import { apiRequest } from '@/lib/api'

function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const analyticsApi = {
  /* Omitting `roomId` is the researched default scope: "All Rooms". */
  overview: (params = {}) => apiRequest(`/wf-006/overview${query(params)}`),
  room: (roomId, params = {}) => apiRequest(`/wf-006/rooms/${roomId}${query(params)}`),
  alerts: (params = {}) => apiRequest(`/wf-006/alerts${query(params)}`),
  timeline: (roomId, params = {}) => apiRequest(`/wf-006/rooms/${roomId}/timeline${query(params)}`),
  addTimelineNote: (roomId, payload, params = {}) =>
    apiRequest(`/wf-006/rooms/${roomId}/timeline${query(params)}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  recordEvent: (payload, params = {}) =>
    apiRequest(`/wf-006/events${query(params)}`, { method: 'POST', body: JSON.stringify(payload) }),
  config: () => apiRequest('/wf-006/config'),
  updateConfig: (payload) => apiRequest('/wf-006/config', { method: 'PATCH', body: JSON.stringify(payload) }),
}

/** 428 is the researched "not connected yet" state, not an error to show as one. */
export function isNotConnected(error) {
  return error?.status === 428
}
