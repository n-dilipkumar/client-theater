/**
 * WF-037 API calls.
 *
 * These live in the feature folder rather than on the shared `api` object in `@/lib/api`,
 * because that file is shared and a hundred features each adding a method to it is
 * precisely the conflict the feature host exists to remove. The host's `apiRequest` is the
 * escape hatch that makes it unnecessary.
 *
 * `PREFIX` is the feature's own: the backend module mounts these under `/api/wf-037`, and
 * `apiRequest` already prepends `/api`.
 *
 * Payloads stay schema-flexible. A field map is arbitrary JSON keyed by whatever the CRM
 * calls its properties, and the pickers on the page are rendered from the `/vocabulary`
 * endpoint rather than from a list compiled into this file, so a team that adds a CRM field
 * ships a record instead of a change to this file.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-037'

/**
 * Build a query string, dropping anything empty.
 *
 * Empty values are dropped rather than sent as `?type=` because a page that flickers
 * between "filter on nothing" and "no filter" when a field is cleared is a page nobody
 * trusts.
 */
export function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue
    search.set(key, Array.isArray(value) ? value.join(',') : String(value))
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const engagementApi = {
  /** The researched contract: the three create surfaces and what each one answers. */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),

  /** The named, versioned transforms a field map may choose from. */
  transforms: () => apiRequest(`${PREFIX}/transforms`),

  /**
   * Every design decision the research does not fix, with what was quoted and what was
   * chosen. The page renders this from the server rather than from copy in this folder,
   * so the page cannot drift from the registry a reviewer is asked to disagree with.
   */
  inferences: () => apiRequest(`${PREFIX}/inferences`),

  connectors: () => apiRequest(`${PREFIX}/connectors`),
  connector: (connectorId) => apiRequest(`${PREFIX}/connectors/${connectorId}`),
  registerConnector: (payload, roomId) =>
    apiRequest(`${PREFIX}/connectors${query({ room_id: roomId })}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  patchConnector: (connectorId, payload) =>
    apiRequest(`${PREFIX}/connectors/${connectorId}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteConnector: (connectorId) => apiRequest(`${PREFIX}/connectors/${connectorId}`, { method: 'DELETE' }),

  eventTypes: () => apiRequest(`${PREFIX}/event-types`),
  registerEventType: (payload) =>
    apiRequest(`${PREFIX}/event-types`, { method: 'POST', body: JSON.stringify(payload) }),
  patchEventType: (eventTypeId, payload) =>
    apiRequest(`${PREFIX}/event-types/${eventTypeId}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteEventType: (eventTypeId) => apiRequest(`${PREFIX}/event-types/${eventTypeId}`, { method: 'DELETE' }),

  fieldMaps: (params = {}) => apiRequest(`${PREFIX}/field-maps${query(params)}`),
  registerFieldMap: (payload) =>
    apiRequest(`${PREFIX}/field-maps`, { method: 'POST', body: JSON.stringify(payload) }),
  patchFieldMap: (fieldMapId, payload) =>
    apiRequest(`${PREFIX}/field-maps/${fieldMapId}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteFieldMap: (fieldMapId) => apiRequest(`${PREFIX}/field-maps/${fieldMapId}`, { method: 'DELETE' }),

  /** A buyer acted. Records the event, enqueues the CRM write, and fires the queue. */
  recordEngagement: (roomId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/engagements`, { method: 'POST', body: JSON.stringify(payload) }),
  recordEngagementDeferred: (roomId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/engagements${query({ fire_queue: 'false' })}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /** The room's Analytics / Engagement feed. */
  feed: (roomId, params = {}) => apiRequest(`${PREFIX}/rooms/${roomId}/engagements${query(params)}`),

  /** One event, its queue row, and every write the worker made for it. */
  event: (roomId, engagementId) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/engagements/${engagementId}`),

  /** Whether this room could sync at all, and what is missing if it could not. */
  readiness: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/readiness`),

  /** The queue, oldest first. */
  queue: (roomId, params = {}) => apiRequest(`${PREFIX}/rooms/${roomId}/queue${query(params)}`),

  /** What a drain would do, and why. Writes nothing. */
  preview: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/queue/preview`, { method: 'POST' }),

  /** The researched "the room's queue worker fires it". */
  drain: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/queue/drain`, { method: 'POST' }),

  /** Fire one failed or blocked row again, by hand. */
  retry: (roomId, queueId) => apiRequest(`${PREFIX}/rooms/${roomId}/queue/${queueId}/retry`, { method: 'POST' }),

  /** The Sync log / Errors admin panel. */
  syncLog: (params = {}) => apiRequest(`${PREFIX}/sync-log${query(params)}`),
  syncLogEntry: (logId) => apiRequest(`${PREFIX}/sync-log/${logId}`),
}
