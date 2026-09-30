/**
 * API calls for the booking writeback (WF-065).
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a
 * method to it is precisely the conflict the feature host exists to remove. The
 * host's `apiRequest` is the escape hatch that makes that unnecessary.
 *
 * `PREFIX` is the feature's own: the backend module mounts these under
 * `/api/wf-065`, and `apiRequest` already prepends `/api`.
 *
 * Nothing here hard-codes a payload shape. The node palette, the six branch
 * labels, the related objects and the two selection rules are all rendered from
 * the server's own `/vocabulary`, so a deployment that adds a fourth node ships a
 * record rather than a change to this file.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-065'

function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

const room = (roomId, tail = '') => `/rooms/${encodeURIComponent(roomId)}${tail}`

export const writebackApi = {
  /** The researched vocabulary: the nodes, the branches, the rules, the quotes. */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),

  /** Every design inference this workflow rests on, and how to change each one. */
  inferences: () => apiRequest(`${PREFIX}/inferences`),

  // -- connectors: the global CRM connection --------------------------------- //

  listConnectors: () => apiRequest(`${PREFIX}/connectors`),
  createConnector: (payload) =>
    apiRequest(`${PREFIX}/connectors`, { method: 'POST', body: JSON.stringify(payload) }),

  // -- meeting types, and the Sync Meeting Type to the CRM toggle ------------ //

  listMeetingTypes: (roomId) => apiRequest(room(roomId, '/meeting-types')),
  createMeetingType: (roomId, payload) =>
    apiRequest(room(roomId, '/meeting-types'), { method: 'POST', body: JSON.stringify(payload) }),
  updateMeetingType: (roomId, id, payload) =>
    apiRequest(room(roomId, `/meeting-types/${encodeURIComponent(id)}`), {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  deleteMeetingType: (roomId, id) =>
    apiRequest(room(roomId, `/meeting-types/${encodeURIComponent(id)}`), { method: 'DELETE' }),

  /**
   * Cal's per-event-type CRM sync errors, under the meeting type that owns the
   * event type - because that is the shape the vendor's endpoint has.
   */
  crmSyncErrors: (roomId, meetingTypeId) =>
    apiRequest(room(roomId, `/meeting-types/${encodeURIComponent(meetingTypeId)}/crm-sync-errors`)),

  // -- flows: the declared nodes, and the ordering check --------------------- //

  listFlows: (roomId, params = {}) => apiRequest(room(roomId, `/flows${query(params)}`)),
  createFlow: (roomId, payload) =>
    apiRequest(room(roomId, '/flows'), { method: 'POST', body: JSON.stringify(payload) }),
  updateFlow: (roomId, flowId, payload) =>
    apiRequest(room(roomId, `/flows/${encodeURIComponent(flowId)}`), {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  deleteFlow: (roomId, flowId) =>
    apiRequest(room(roomId, `/flows/${encodeURIComponent(flowId)}`), { method: 'DELETE' }),

  /**
   * Check a node list against the researched rules, writing nothing. Takes a
   * candidate as well as the stored one, so a reorder can be tried before saving.
   */
  validateFlow: (roomId, flowId, payload = {}) =>
    apiRequest(room(roomId, `/flows/${encodeURIComponent(flowId)}/validate`), {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  // -- the writeback --------------------------------------------------------- //

  /** Run a declared flow against one booking. */
  writeback: (roomId, flowId, payload) =>
    apiRequest(room(roomId, `/flows/${encodeURIComponent(flowId)}/writeback`), {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /**
   * The researched automation: the booking knows its path, the flow is found.
   * A path with no declared flow comes back as a 428, which is a state a page
   * shows rather than a failure it hides.
   */
  writebackForPath: (roomId, payload) =>
    apiRequest(room(roomId, '/writeback'), { method: 'POST', body: JSON.stringify(payload) }),

  // -- the run log, and what the CRM now holds ------------------------------- //

  listRuns: (roomId, params = {}) => apiRequest(room(roomId, `/runs${query(params)}`)),
  readRun: (roomId, runId) => apiRequest(room(roomId, `/runs/${encodeURIComponent(runId)}`)),
  listCrmRecords: (roomId, params = {}) => apiRequest(room(roomId, `/crm-records${query(params)}`)),
  summary: (roomId) => apiRequest(room(roomId, '/summary')),

  // -- Meetings Activity -> Events History ------------------------------------ //

  listHistory: (roomId, params = {}) => apiRequest(room(roomId, `/events-history${query(params)}`)),

  /**
   * The export the research's feature list names, as a URL.
   *
   * A plain href rather than a client call: `apiRequest` parses JSON, and a CSV is
   * not JSON. The browser handles the download, which is also what makes
   * "Export to CSV" a one-click affordance rather than a blob handed to
   * `URL.createObjectURL`.
   */
  exportUrl: (roomId, params = {}) =>
    `/api${PREFIX}${room(roomId, `/events-history/export.csv${query(params)}`)}`,

  /** Retry one failed Event. A row that succeeded comes back `retried: false`. */
  retry: (roomId, historyId) =>
    apiRequest(room(roomId, `/events-history/${encodeURIComponent(historyId)}/retry`), {
      method: 'POST',
    }),
}

/** Rooms, read through the shared generic surface rather than this feature's. */
export const listRooms = (params = {}) => apiRequest(`/records/room${query(params)}`)
