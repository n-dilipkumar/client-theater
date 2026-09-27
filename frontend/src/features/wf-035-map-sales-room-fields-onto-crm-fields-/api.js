/**
 * Field mapping API (WF-035).
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a
 * method to it is precisely the collision the feature host exists to remove. The
 * branch this workflow was built from would have added a dozen methods there; the
 * host's `apiRequest` is the escape hatch that makes that unnecessary.
 *
 * `PREFIX` is the feature's own: the backend module mounts these under `/api/wf-035`,
 * and `apiRequest` already prepends `/api`.
 *
 * Every path is nested under its connection, because a mapping belongs to one and
 * the URL says so. A mapping read under the wrong connection answers 404 rather
 * than quietly showing another connection's grid.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-035'

/** Drop empty values so we never send `?state=` and confuse a filter. */
function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const mappingApi = {
  /** The published vocabulary: directions, provider types, the researched rules. */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),

  /** Every decision the research does not make, and how to change each one. */
  inferences: () => apiRequest(`${PREFIX}/inferences`),

  /** The transform registry plus every transform declared as data. */
  transforms: () => apiRequest(`${PREFIX}/transforms`),
  declareTransform: (payload) =>
    apiRequest(`${PREFIX}/transforms`, { method: 'POST', body: JSON.stringify(payload) }),

  /** The page's header: what is mapped, what is activatable, what has no metadata. */
  summary: () => apiRequest(`${PREFIX}/summary`),

  connections: (params = {}) => apiRequest(`${PREFIX}/connections${query(params)}`),
  createConnection: (payload, params = {}) =>
    apiRequest(`${PREFIX}/connections${query(params)}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  readConnection: (id) => apiRequest(`${PREFIX}/connections/${id}`),
  amendConnection: (id, payload) =>
    apiRequest(`${PREFIX}/connections/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),

  /**
   * The recorded property read for one object, plus the endpoints a connector
   * calls to make it. 409 when nothing is recorded, so the page shows the
   * endpoints rather than an empty grid.
   */
  properties: (connectionId, crmObject) =>
    apiRequest(`${PREFIX}/connections/${connectionId}/properties${query({ crm_object: crmObject })}`),
  recordProperties: (connectionId, payload) =>
    apiRequest(`${PREFIX}/connections/${connectionId}/properties`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  mappings: (connectionId, params = {}) =>
    apiRequest(`${PREFIX}/connections/${connectionId}/mappings${query(params)}`),
  createMapping: (connectionId, payload) =>
    apiRequest(`${PREFIX}/connections/${connectionId}/mappings`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  readMapping: (connectionId, mappingId) =>
    apiRequest(`${PREFIX}/connections/${connectionId}/mappings/${mappingId}`),
  deleteMapping: (connectionId, mappingId) =>
    apiRequest(`${PREFIX}/connections/${connectionId}/mappings/${mappingId}`, { method: 'DELETE' }),

  rows: (connectionId, mappingId) =>
    apiRequest(`${PREFIX}/connections/${connectionId}/mappings/${mappingId}/rows`),
  putRow: (connectionId, mappingId, payload) =>
    apiRequest(`${PREFIX}/connections/${connectionId}/mappings/${mappingId}/rows`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  patchRow: (connectionId, mappingId, rowId, payload) =>
    apiRequest(`${PREFIX}/connections/${connectionId}/mappings/${mappingId}/rows/${rowId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  deleteRow: (connectionId, mappingId, rowId) =>
    apiRequest(`${PREFIX}/connections/${connectionId}/mappings/${mappingId}/rows/${rowId}`, {
      method: 'DELETE',
    }),

  /**
   * The research's step 5. `record: true` stores the run, which is what
   * activation reads; without it the report is computed and thrown away, so
   * re-validating as an admin edits does not fill the audit log.
   */
  validate: (connectionId, mappingId, payload = {}) =>
    apiRequest(`${PREFIX}/connections/${connectionId}/mappings/${mappingId}/validate`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  validation: (connectionId, mappingId) =>
    apiRequest(`${PREFIX}/connections/${connectionId}/mappings/${mappingId}/validation`),
  validations: (connectionId, mappingId) =>
    apiRequest(`${PREFIX}/connections/${connectionId}/mappings/${mappingId}/validations`),

  activate: (connectionId, mappingId) =>
    apiRequest(`${PREFIX}/connections/${connectionId}/mappings/${mappingId}/activate`, {
      method: 'POST',
    }),
  deactivate: (connectionId, mappingId) =>
    apiRequest(`${PREFIX}/connections/${connectionId}/mappings/${mappingId}/deactivate`, {
      method: 'POST',
    }),

  syncKey: (connectionId, mappingId) =>
    apiRequest(`${PREFIX}/connections/${connectionId}/mappings/${mappingId}/sync-key`),
  pinSyncKey: (connectionId, mappingId, payload) =>
    apiRequest(`${PREFIX}/connections/${connectionId}/mappings/${mappingId}/sync-key`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  unpinSyncKey: (connectionId, mappingId) =>
    apiRequest(`${PREFIX}/connections/${connectionId}/mappings/${mappingId}/sync-key`, {
      method: 'DELETE',
    }),
  syncKeyRequest: (connectionId, mappingId, payload = {}) =>
    apiRequest(`${PREFIX}/connections/${connectionId}/mappings/${mappingId}/sync-key/request`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /** What one sync cycle would send. Writes nothing, so it is safe on every edit. */
  preview: (connectionId, mappingId, record, params = {}) =>
    apiRequest(`${PREFIX}/connections/${connectionId}/mappings/${mappingId}/preview${query(params)}`, {
      method: 'POST',
      body: JSON.stringify({ record }),
    }),

  roomConnections: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/connections`),
  roomMappings: (roomId, params = {}) => apiRequest(`${PREFIX}/rooms/${roomId}/mappings${query(params)}`),

  rooms: () => apiRequest('/records/room?limit=100'),
}

/** The one-line explanation the server gave for a refusal, or a generic fallback. */
export function detailOf(error) {
  return (
    error?.detail ||
    error?.message ||
    (typeof error === 'string' ? error : 'The request could not be completed.')
  )
}

/** A stable tone for a finding, from the server's own severity. */
const TONE = { error: 'bad', warning: 'warn', info: 'info', ok: 'good' }
export const toneFor = (severity) => TONE[severity] || 'info'

/** `13 Feb, 14:02` - short, and the raw value beside it as a title attribute. */
export function shortMoment(iso) {
  if (!iso) return 'never'
  const parsed = new Date(iso)
  if (Number.isNaN(parsed.getTime())) return iso
  return parsed.toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
}

/** `8 minutes ago`, for a metadata read a reader needs to judge. */
export function ago(seconds) {
  if (seconds === null || seconds === undefined) return 'unknown age'
  if (seconds < 90) return `${Math.max(0, Math.round(seconds))} seconds ago`
  const minutes = Math.round(seconds / 60)
  if (minutes < 90) return `${minutes} minutes ago`
  const hours = Math.round(minutes / 60)
  if (hours < 36) return `${hours} hours ago`
  return `${Math.round(hours / 24)} days ago`
}
