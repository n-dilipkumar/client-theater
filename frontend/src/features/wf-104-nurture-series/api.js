/**
 * Nurture series API (WF-104).
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a method to it
 * is exactly the collision the feature host exists to remove. The host's `apiRequest` is the
 * escape hatch that makes that unnecessary.
 *
 * `PREFIX` is the feature's own: the backend module mounts these under `/api/wf-104`, and
 * `apiRequest` already prepends `/api`. Every payload is sent in the research's own spelling -
 * `blocks` with `kind: rule | content | wait | tag`, a channel, a `send_window_seconds`, and a
 * rule `wait_mode` of `for` or `once` - so the documented flow can be driven against the API
 * directly and a reader can match what this page sends against what the research says.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-104'

export const seriesApi = {
  /** The terms, the block kinds, the filter vocabulary and every refusal code. */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),

  /** Every judgement call this workflow rests on, for the page's last section. */
  inferences: () => apiRequest(`${PREFIX}/inferences`),

  /** The core records route, so this page depends on the HTTP contract for a room's shape. */
  rooms: () => apiRequest('/records/room?limit=100'),

  summary: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/summary`),

  series: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/series`),
  createSeries: (roomId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/series`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  goLive: (roomId, seriesId) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/series/${seriesId}/go-live`, { method: 'POST' }),
  preview: (roomId, seriesId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/series/${seriesId}/preview`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  exportSeries: (roomId, seriesId) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/series/${seriesId}/export`),

  /** The 30-minute poll. The audience is supplied, because this feature stores no contacts. */
  evaluate: (roomId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/evaluate`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  runs: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/runs`),
  run: (roomId, runId) => apiRequest(`${PREFIX}/rooms/${roomId}/runs/${runId}`),
  advance: (roomId, runId, payload = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/runs/${runId}/advance`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  resolve: (roomId, runId, payload = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/runs/${runId}/resolve`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  recordEvent: (roomId, runId, event) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/runs/${runId}/events`, {
      method: 'POST',
      body: JSON.stringify({ event }),
    }),
}

export default seriesApi
