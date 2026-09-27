/**
 * The WF-017 white-label API, owned by this feature.
 *
 * The branch added ten methods to the shared `api` object in `@/lib/api` and
 * routed them at `/api/white-label/*` and `/api/rooms/{id}/white-label*`. That
 * file is read by every feature, so appending to it is precisely the collision
 * the plugin host exists to prevent: twelve workflow branches each added methods
 * to one object and none of them could merge. Everything here goes through
 * `apiRequest`, the escape hatch the host provides for exactly this case, so the
 * shared client is never edited.
 *
 * The room list is read from the core `/records/room` collection rather than from
 * `api.listRecords`, so this feature depends on the HTTP contract and not on
 * another module's idea of a record's shape. The API is schema-flexible, which
 * is the point: a room with fields this feature has never heard of still lists.
 *
 * `BASE` mirrors the router prefix in
 * `backend/dsr/features/wf_017_white_label.py`. It is written out here rather
 * than imported because the backend is Python; the two are held together by
 * `backend/tests/test_wf017.py`, which asserts the ten paths from the registry,
 * and by `src/test/fixtures.js`, which stubs exactly these paths.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-017-white-label'

/** Drop empty values so a filter is never sent as `?host=`. */
function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  return search.toString() ? `?${search}` : ''
}

const wl = (roomId, suffix) => `${BASE}/rooms/${roomId}/white-label${suffix}`

export const whiteLabelApi = {
  /** What this deployment asks a customer to point a CNAME at. */
  config: () => apiRequest(`${BASE}/config`),

  /** Every room, so the page can offer a room to white-label. */
  rooms: () => apiRequest('/records/room?limit=100'),

  /** A room's public identity: domain state, share links, brand tokens. */
  forRoom: (roomId) => apiRequest(wl(roomId, '')),

  /** Mint the share-link secret if the room has none. Idempotent. */
  mintLinkSecret: (roomId) => apiRequest(wl(roomId, '/link-secret'), { method: 'POST' }),

  /**
   * Check a candidate domain without claiming it.
   *
   * A separate call from `claimDomain` because the researched flow is
   * verify-then-save: the operator has to be able to see what is wrong before
   * committing to a change.
   */
  verifyDomain: (domain) =>
    apiRequest(`${BASE}/verify`, { method: 'POST', body: JSON.stringify({ domain }) }),

  /**
   * Attach a domain. `force` is the researched escape hatch for the state
   * mid-propagation; it cannot force past a domain another room already holds,
   * and the server says so with a 409 rather than pretending otherwise.
   */
  claimDomain: (roomId, domain, { force = false } = {}) =>
    apiRequest(wl(roomId, '/domain') + query({ force: force || undefined }), {
      method: 'POST',
      body: JSON.stringify({ domain }),
    }),

  releaseDomain: (roomId) => apiRequest(wl(roomId, '/domain'), { method: 'DELETE' }),

  /** Re-run DNS verification for the domain the room already holds. */
  recheckDomain: (roomId) => apiRequest(wl(roomId, '/recheck'), { method: 'POST' }),

  /** Merge brand tokens. Unknown keys pass through untouched. */
  saveBranding: (roomId, branding) =>
    apiRequest(wl(roomId, '/branding'), { method: 'PATCH', body: JSON.stringify(branding) }),

  /**
   * Resolve a full share-link path to its room.
   *
   * `host` is the Host header the page was served on, which is what lets the
   * server answer "was this link opened on the room's own domain or on the
   * default one" -- the researched guarantee that a link works on both.
   */
  resolveLink: (path, host) => apiRequest(`${BASE}/resolve${query({ path, host })}`),
}
