/**
 * The batch-upsert page's own API calls (WF-038).
 *
 * A feature calls its own routes through `apiRequest` rather than adding a method
 * to the shared `api` object in `lib/api.js`, because that file is shared and
 * twelve features each appending to it is how the original branches came to
 * conflict. Every path here is relative to `/api`.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-038'

function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const suffix = search.toString()
  return suffix ? `?${suffix}` : ''
}

export const batchUpsertApi = {
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),
  capabilities: () => apiRequest(`${PREFIX}/capabilities`),
  inferences: () => apiRequest(`${PREFIX}/inferences`),
  config: () => apiRequest(`${PREFIX}/config`),

  connections: (roomId) => apiRequest(`${PREFIX}/connections${query({ room_id: roomId })}`),
  createConnection: (payload, roomId) =>
    apiRequest(`${PREFIX}/connections${query({ room_id: roomId })}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  updateConnection: (id, payload) =>
    apiRequest(`${PREFIX}/connections/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),

  queue: (roomId, connectionId) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/queue${query({ connection_id: connectionId })}`),
  preview: (roomId, connectionId) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/preview${query({ connection_id: connectionId })}`),
  // Named apart from `readRun` on purpose: two `run` keys in one object literal
  // would silently keep the last, and the page would fetch a finished run when it
  // meant to start one.
  startRun: (roomId, connectionId) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/upsert${query({ connection_id: connectionId })}`, {
      method: 'POST',
    }),
  schedule: (roomId, connectionId) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/schedule${query({ connection_id: connectionId })}`, {
      method: 'POST',
    }),
  runs: (roomId, connectionId) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/runs${query({ connection_id: connectionId })}`),
  readRun: (runId) => apiRequest(`${PREFIX}/runs/${runId}`),
  queueEngagement: (roomId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/engagement`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
}
