/**
 * The WF-008 external library API, owned by this feature.
 *
 * The branch added `librarySources`, `libraryConnections`, `libraryFolders`,
 * `listExternal`, `addExternal`, `externalSyncStatus`, `resyncExternal`,
 * `formatBytes`, `errorDetail`, `errorRemediation` and `errorCorrelationId` to
 * the shared `api` object in `@/lib/api`, and made `apiRequest` stash the whole
 * parsed body on the error. `lib/api.js` is read by every feature, so appending
 * to it is precisely the collision the plugin host exists to prevent.
 *
 * Everything here goes through `apiRequest`, the escape hatch the host provides
 * for exactly this case, so the shared client is never edited. Two consequences
 * worth stating rather than hiding:
 *
 * * The error helpers below are thinner than the branch's. `apiRequest` keeps
 *   only the message and the status, so the page has nothing to read
 *   `remediation` or `correlation_id` from. The server therefore composes both
 *   into the message (see the handler in `dsr/features/wf008_external_sync.py`),
 *   and the page shows that one sentence. Carrying the body through instead
 *   needs one line in `lib/api.js`, which is a shared-file change and so is a
 *   human's to make.
 * * `createConnection` goes through the core generic `/records/<collection>`
 *   route. A connection is an ordinary schema-flexible record, and this is what
 *   a team would actually do; the feature needs no dedicated write route for it.
 *
 * The room list is also read from the core records route, so this feature
 * depends on the HTTP contract rather than on another module's idea of the
 * shape of a room.
 *
 * There is no wrapper for ``GET /external/{content_id}/sync-status``. The list
 * route already returns a derived status beside every item, so the page has no
 * reason to ask twice; the branch carried a client for it and never called it.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/library'

/** Drop empty values so we never send `?where=` and confuse a filter. */
function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const suffix = search.toString() ? `?${search}` : ''
  return suffix
}

export const externalApi = {
  /** Supported sources, and whether this deployment has a connection. */
  sources: () => apiRequest(`${BASE}/sources`),

  /** Configured accounts, with the lineage back to each connection. */
  connections: () => apiRequest(`${BASE}/connections`),

  /** Target folders for a room, with the `root` keyword offered first. */
  folders: (roomId) => apiRequest(`${BASE}/folders${query({ roomId })}`),

  /** Every room, so the page can offer a library to add into. */
  rooms: () => apiRequest('/records/room?limit=100'),

  /** Externally sourced items, with their derived sync state. */
  listExternal: (params = {}) => apiRequest(`${BASE}/external${query(params)}`),

  /**
   * Add an external file. Request body fields are the researched names
   * (`externalSource`, `externalContentId`, `parentFolderId`, `autoSync`) so the
   * documented flow can be followed against the API directly.
   */
  addExternal: (payload, params = {}) =>
    apiRequest(`${BASE}/external${query(params)}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /**
   * Run the auto-sync pass, optionally scoped to one room.
   */
  resync: (payload = {}, params = {}) =>
    apiRequest(`${BASE}/external/resync${query(params)}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /**
   * Create a source connection.
   *
   * Deliberately not a dedicated endpoint. A connection is an ordinary record
   * with arbitrary fields, so the core generic route is the honest answer and
   * this feature needs no write route of its own for it.
   */
  createConnection: (payload, params = {}) =>
    apiRequest(`/records/external_connection${query(params)}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
}

/** The message a person should read for a failed call. */
export function describeError(error) {
  if (!error) return ''
  return String(error.message || error)
}

/** A 429 is a documented outcome here, not a fault, so it reads differently. */
export function isRateLimited(error) {
  return error?.status === 429
}

/** Format a byte count for display without pretending to more precision. */
export function formatBytes(bytes) {
  if (typeof bytes !== 'number' || Number.isNaN(bytes)) return '—'
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB']
  let value = bytes / 1024
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`
}
