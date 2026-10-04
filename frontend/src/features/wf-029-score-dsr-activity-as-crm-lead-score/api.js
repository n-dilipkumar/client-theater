/**
 * Lead-score API (WF-029).
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a
 * method to it is precisely the collision the feature host exists to remove.
 * `apiRequest` is the escape hatch that makes that unnecessary.
 *
 * `PREFIX` is this feature's own: the backend module mounts these under
 * `/api/wf-029`, and `apiRequest` already prepends `/api`.
 *
 * Every payload is sent with the names the research uses - `family`, `bucket`,
 * `score`, `refinements` - so the documented flow can be followed against the API
 * directly and a reader can match what the page sends against what the research
 * says should be sent.
 *
 * The room list is read from the core records route, so this page depends on the
 * HTTP contract rather than on another module's idea of the shape of a room.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-029'

/** Drop empty values so we never send `?contact=` and confuse a filter. */
function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const leadScoreApi = {
  /**
   * The published vocabularies: the score property, the two buckets and their signs,
   * the five Dock activity properties and the filters each publishes, the matcher's
   * reasons, the required scopes, the lifecycle constraint and the CRM endpoints.
   *
   * Every picker on the page renders from this rather than from a list compiled into
   * this file, so the editor and the server's validator can never disagree about what
   * is legal.
   */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),

  /**
   * Every judgement call the workflow rests on, and how to change each one.
   *
   * The research names the score property, the buckets, the five Dock properties, the
   * filters worth setting, the scopes and the endpoints. It does not say whether a
   * score accumulates or is recomputed, whether it has a floor, or what a criterion
   * against an unprovisioned property does.
   */
  inferences: () => apiRequest(`${PREFIX}/inferences`),

  /** Step 1: is the integration on, and is this room connected to a deal? */
  integrations: () => apiRequest(`${PREFIX}/integrations`),
  registerIntegration: (payload) =>
    apiRequest(`${PREFIX}/integrations`, { method: 'POST', body: JSON.stringify(payload) }),
  amendIntegration: (id, payload) =>
    apiRequest(`${PREFIX}/integrations/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),

  /** Step 1b: the Dock activity properties a criterion scores against. */
  properties: () => apiRequest(`${PREFIX}/properties`),
  provisionProperty: (payload) =>
    apiRequest(`${PREFIX}/properties`, { method: 'POST', body: JSON.stringify(payload) }),

  /** Steps 2 to 5: the criteria, and the publish step the research does not have. */
  criteria: (params = {}) => apiRequest(`${PREFIX}/criteria${query(params)}`),
  create: (payload) =>
    apiRequest(`${PREFIX}/criteria`, { method: 'POST', body: JSON.stringify(payload) }),
  amend: (id, payload) =>
    apiRequest(`${PREFIX}/criteria/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  withdraw: (id) => apiRequest(`${PREFIX}/criteria/${id}`, { method: 'DELETE' }),

  /** Would this criterion match this event? Nothing is saved and nothing is sent. */
  preview: (payload) =>
    apiRequest(`${PREFIX}/preview`, { method: 'POST', body: JSON.stringify(payload) }),

  /** The source side, room-scoped: the webhook end, and the score it moved. */
  activity: (roomId, params = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/activity${query(params)}`),
  recordActivity: (roomId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/activity`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /** The continuous rule, for the moment somebody needs to see it fire. */
  score: (roomId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/score`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /** What a page reads: the scores, one contact's score, the runs, the summary. */
  scores: (roomId, params = {}) => apiRequest(`${PREFIX}/rooms/${roomId}/scores${query(params)}`),
  contactScore: (roomId, contactId) => apiRequest(`${PREFIX}/rooms/${roomId}/scores/${contactId}`),
  history: (roomId, params = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/history${query(params)}`),
  summary: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/summary`),

  rooms: () => apiRequest('/records/room?limit=100'),
}

/**
 * The filters one Dock activity property publishes, from the served matrix.
 *
 * Built from the vocabulary rather than from a list in this file, because the matrix
 * is the researched surface and the editor must not carry a second copy of it. A
 * property with no published filter yields an empty list, and the page says so rather
 * than offering a control the server will refuse.
 */
export function refinementsFor(vocabulary, family) {
  const row = (vocabulary?.families || []).find((entry) => entry.name === family)
  return row?.refinements || []
}

/**
 * The label to put on a filter control, from the served filter list.
 */
export function refinementLabel(vocabulary, name) {
  const row = (vocabulary?.refinements || []).find((entry) => entry.name === name)
  return row?.label || name
}

/**
 * The sentence a person reads for a Dock property, in the source's own words.
 */
export function familyLabel(vocabulary, family) {
  const row = (vocabulary?.families || []).find((entry) => entry.name === family)
  return row?.label || family
}

/**
 * The sentence behind a bucket, in the source's own words.
 */
export function bucketMeaning(vocabulary, bucket) {
  const row = (vocabulary?.buckets || []).find((entry) => entry.name === bucket)
  return row?.label || ''
}

/**
 * The signed value a bucket contributes for one matching event.
 *
 * Read from the served signs rather than from a `+`/`-` in this file, because the
 * bucket carries the sign on the server and a second copy here could disagree with it.
 */
export function signedScore(vocabulary, bucket, score) {
  const row = (vocabulary?.buckets || []).find((entry) => entry.name === bucket)
  const sign = row ? row.sign : 0
  return sign * Number(score || 0)
}
