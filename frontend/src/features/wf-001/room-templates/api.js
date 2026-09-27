/**
 * The API client for WF-001, owned by this feature.
 *
 * The branch added a `roomTemplates` / `accounts` / `createRoom` / `listRooms`
 * group to the shared `api` object in `@/lib/api`, and also changed that file's
 * `request()` to put a machine-readable `error.code` on the thrown error. Both
 * edits are refused: `lib/api.js` is read by every feature, so appending to it is
 * the collision the plugin host exists to prevent, and CI fails a diff that
 * touches it.
 *
 * What the shared client *does* already give back is everything the wizard needs.
 * `apiRequest` is the same `request()`, it already sets `error.status`, and
 * `error.message` is already the server's operator-facing `detail`. What it does
 * not do is keep the machine-readable `error` code the server also sends, and
 * this feature does not reach around the client with a raw `fetch` to recover it.
 * It does not need to: see `stepForStatus` in `RoomTemplates.jsx` for why the
 * status alone is enough to send the operator back to the right step.
 *
 * `PREFIX` is the one place the backend's prefix is written down on this side and
 * it matches `router.prefix` in `backend/dsr/features/wf001_rooms.py` exactly.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-001'

/** Build a query string, dropping empty values so a whole form state can be passed. */
function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const roomTemplatesApi = {
  /** Wizard step 2: shipped catalogue overlaid with store templates. */
  templates: () => apiRequest(`${PREFIX}/room-templates`),

  /** Wizard step 1: the accounts a room can be bound to. */
  accounts: (params) => apiRequest(`${PREFIX}/accounts${query(params)}`),

  /** The Rooms list, one status at a time. */
  rooms: (params) => apiRequest(`${PREFIX}/rooms${query(params)}`),

  /** Wizard steps 1-3, in one call: the room and its site, or nothing at all. */
  createRoom: (payload, params) =>
    apiRequest(`${PREFIX}/rooms${query(params)}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
}
