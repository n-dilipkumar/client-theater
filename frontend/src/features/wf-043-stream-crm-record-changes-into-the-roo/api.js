/**
 * CRM change stream API (WF-043).
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a
 * method to it is precisely the collision the feature host exists to remove. The
 * branch this workflow was built from would have added a dozen methods there; the
 * host's `apiRequest` is the escape hatch that makes that unnecessary.
 *
 * `PREFIX` is the feature's own: the backend module mounts these under
 * `/api/wf-043`, and `apiRequest` already prepends `/api`.
 *
 * Every payload is sent in the six spellings the research names - `changeType`,
 * `transactionKey`, `sequenceNumber`, `commitTimestamp`, `changedFields`, plus the
 * record payload - so the documented flow can be followed against the API
 * directly and a reader can match what the page sends against what the research
 * says should be sent.
 *
 * The room list is read from the core records route, so this page depends on the
 * HTTP contract rather than on another module's idea of the shape of a room.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-043'

/** Drop empty values so we never send `?name=` and confuse a filter. */
function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const streamApi = {
  /**
   * The published vocabularies: the four change types, the six fields a change
   * event carries, the standard channel and its case rule, the transports and
   * which of them support enrichment, the editions that have Change Data
   * Capture, the four query options Dataverse refuses, the annotation, HubSpot's
   * cap and its rate-limit exemption, and the 3 MB buffer recommendation. Every
   * picker on the page renders from this rather than from a list compiled here.
   */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),

  /**
   * Every judgement call the workflow rests on, and how to change each one.
   *
   * The research names the six fields of a change event, the four change types,
   * the buffering rule, the channel-name case rule, the enrichment isolation rule
   * and the enrichment-by-change-type rule, the Pub/Sub flow control, the 3 MB
   * buffer recommendation, the Dataverse header, the four refused query options,
   * the annotation, and HubSpot's cap and exemption. It does not say what happens
   * to the last transaction in a stream, where a buyer comes from, or what an
   * unresolvable event should do. Those edges are served as data rather than left
   * for a reader to reconstruct from a diff.
   */
  inferences: () => apiRequest(`${PREFIX}/inferences`),

  orgs: () => apiRequest(`${PREFIX}/orgs`),
  registerOrg: (payload) =>
    apiRequest(`${PREFIX}/orgs`, { method: 'POST', body: JSON.stringify(payload) }),
  readOrg: (id) => apiRequest(`${PREFIX}/orgs/${id}`),
  enableCdc: (id, payload) =>
    apiRequest(`${PREFIX}/orgs/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),

  /**
   * `name` is matched byte for byte, because "The channel name is
   * case-sensitive." The server does not fold it on our behalf, so the page shows
   * the names it holds rather than offering a search that would lie.
   */
  channels: (params = {}) => apiRequest(`${PREFIX}/channels${query(params)}`),
  createChannel: (payload) =>
    apiRequest(`${PREFIX}/channels`, { method: 'POST', body: JSON.stringify(payload) }),
  readChannel: (id) => apiRequest(`${PREFIX}/channels/${id}`),
  patchChannel: (id, payload) =>
    apiRequest(`${PREFIX}/channels/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteChannel: (id) => apiRequest(`${PREFIX}/channels/${id}`, { method: 'DELETE' }),
  enrich: (id, fields) =>
    apiRequest(`${PREFIX}/channels/${id}/enrichment`, {
      method: 'POST',
      body: JSON.stringify({ fields }),
    }),
  unenrich: (id, field) =>
    apiRequest(`${PREFIX}/channels/${id}/enrichment/${encodeURIComponent(field)}`, {
      method: 'DELETE',
    }),

  subscriptions: (params = {}) => apiRequest(`${PREFIX}/subscriptions${query(params)}`),
  openSubscription: (roomId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/subscriptions`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  readSubscription: (id) => apiRequest(`${PREFIX}/subscriptions/${id}`),
  /** A FetchRequest: how many more events the client is asking for. */
  fetchMore: (id, numRequested) =>
    apiRequest(`${PREFIX}/subscriptions/${id}/fetch`, {
      method: 'POST',
      body: JSON.stringify({ num_requested: numRequested }),
    }),
  /** Closes the subscription and flushes whatever transaction is still parked. */
  close: (id) => apiRequest(`${PREFIX}/subscriptions/${id}/close`, { method: 'POST' }),

  /**
   * The researched path for a change event. It refuses anything it cannot vouch
   * for - an update whose sync key is in neither the payload nor the enriched
   * fields is refused with the field to add named - so the page reports the
   * refusal rather than hiding it.
   */
  deliver: (roomId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/events`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  events: (roomId, params = {}) => apiRequest(`${PREFIX}/rooms/${roomId}/events${query(params)}`),
  buffer: (roomId, params = {}) => apiRequest(`${PREFIX}/rooms/${roomId}/buffer${query(params)}`),
  replica: (roomId, params = {}) => apiRequest(`${PREFIX}/rooms/${roomId}/replica${query(params)}`),
  dealPanel: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/deal-panel`),
  invalidations: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/invalidations`),

  tables: () => apiRequest(`${PREFIX}/dataverse/tables`),
  declareTable: (payload) =>
    apiRequest(`${PREFIX}/dataverse/tables`, { method: 'POST', body: JSON.stringify(payload) }),
  enableTracking: (id) => apiRequest(`${PREFIX}/dataverse/tables/${id}/track-changes`, { method: 'POST' }),
  /** Always refused: "After you enable change tracking for a table, you can't disable it." */
  disableTracking: (id) =>
    apiRequest(`${PREFIX}/dataverse/tables/${id}/track-changes`, { method: 'DELETE' }),
  poll: (id, payload) =>
    apiRequest(`${PREFIX}/dataverse/tables/${id}/poll`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  tableCount: (id, deltatoken) =>
    apiRequest(`${PREFIX}/dataverse/tables/${id}/count${query({ deltatoken })}`),

  hubspotSubscriptions: (params = {}) => apiRequest(`${PREFIX}/hubspot/subscriptions${query(params)}`),
  registerHubspot: (payload) =>
    apiRequest(`${PREFIX}/hubspot/subscriptions`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  deleteHubspot: (id) => apiRequest(`${PREFIX}/hubspot/subscriptions/${id}`, { method: 'DELETE' }),
  hubspotUsage: () => apiRequest(`${PREFIX}/hubspot/usage`),

  usage: (params = {}) => apiRequest(`${PREFIX}/usage${query(params)}`),
  summary: (params = {}) => apiRequest(`${PREFIX}/summary${query(params)}`),

  rooms: () => apiRequest('/records/room?limit=100'),
}

/** A change event with every field the research names, ready to edit. */
export function newEvent(subscriptionId, overrides = {}) {
  return {
    subscription_id: subscriptionId,
    changeType: 'UPDATE',
    transactionKey: `txn-${Math.random().toString(36).slice(2, 8)}`,
    sequenceNumber: 1,
    commitTimestamp: new Date().toISOString(),
    changedFields: [],
    payload: {},
    enrichedFields: {},
    ...overrides,
  }
}

/**
 * A short sentence for what one change event is doing to the room.
 *
 * The research's rule is that a change is parked under its transactionKey and only
 * commits when that key changes, so an event that is sitting in the buffer is not
 * a failure - it is a transaction that has not been closed yet. The page says so
 * rather than colouring an uncommitted change red.
 */
export function eventStateLabel(event) {
  if (event.state === 'committed') return 'committed to the replica'
  return 'parked — commits when the transactionKey changes'
}

/** The four researched change types, in the order the research lists them. */
export const CHANGE_TYPE_TONE = {
  CREATE: 'insert',
  UPDATE: 'update',
  DELETE: 'delete',
  UNDELETE: 'restore',
}
