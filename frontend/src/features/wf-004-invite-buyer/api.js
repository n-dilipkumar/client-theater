/**
 * The WF-004 Share API, owned by this feature.
 *
 * The branch added an `accessApi` object to the shared `api` in `@/lib/api`, and
 * also changed that file's `request()` to put the HTTP status on the thrown
 * error. Both edits are refused. `lib/api.js` is read by every feature, so
 * appending to it is the collision the plugin host exists to prevent, and CI
 * fails a diff that touches it. The good news is that the status is *already*
 * there: `apiRequest` is the same function and it already sets `error.status`,
 * which is exactly what the Share dialog needs to tell "you may not" (403) from
 * "confirm this first" (428) and from "the call failed".
 *
 * The name is `rolesApi` rather than `accessApi` for the same reason the module
 * is `dsr.roles` and not `dsr.access`: this workflow is about granting roles, and
 * `access` is the module name the sibling workflow, which verifies identity,
 * owns. See the note in `backend/dsr/roles.py`.
 *
 * `PREFIX` is the one place the backend's prefix is written down on this side,
 * and it matches `router.prefix` in `backend/dsr/roles_api.py` exactly.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-004-invite-buyer'

/**
 * Build a query string, dropping empty values so a whole form can be passed.
 *
 * `confirm` is a boolean the server reads as a flag: `false` and an absent flag
 * mean the same thing there, and sending `confirm=false` would be noise.
 */
function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '' && value !== false) {
      search.set(key, value === true ? 'true' : value)
    }
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const rolesApi = {
  /** The role vocabulary, annotated with what an actor may assign. */
  roles: (actorRole) => apiRequest(`${PREFIX}/access/roles${query({ actor_role: actorRole })}`),

  /** Members, pending invitations, and the imminent-expiry banner, in one read. */
  snapshot: (roomId, actor) => apiRequest(`${PREFIX}/rooms/${roomId}/access${query({ actor })}`),

  invite: (roomId, { emails, role, access_valid_until }, actor) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/invitations${query({ actor })}`, {
      method: 'POST',
      body: JSON.stringify({ emails, role, access_valid_until }),
    }),

  accept: (invitationId, actor) =>
    apiRequest(`${PREFIX}/invitations/${invitationId}/accept${query({ actor })}`, {
      method: 'POST',
    }),

  updateAccess: (accessId, { role, access_valid_until, set_expiry }, { actor, confirm } = {}) =>
    apiRequest(`${PREFIX}/access/${accessId}${query({ actor, confirm })}`, {
      method: 'PATCH',
      body: JSON.stringify({ role, access_valid_until, set_expiry }),
    }),

  removeAccess: (accessId, { actor, confirm } = {}) =>
    apiRequest(`${PREFIX}/access/${accessId}${query({ actor, confirm })}`, {
      method: 'DELETE',
    }),
}
