/**
 * API calls for CRM connections (WF-034).
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a
 * method to it is precisely the conflict the feature host exists to remove. The
 * host's `apiRequest` is the escape hatch that makes that unnecessary.
 *
 * `PREFIX` is the feature's own: the backend module mounts these under
 * `/api/wf-034`, and `apiRequest` already prepends `/api`.
 *
 * Nothing here hard-codes a vendor, a status or a scope. The vendor pickers, the
 * state badges and the scope hints all come from `/vocabulary` and `/connectors`,
 * and a team that adds a fourth CRM publishes a connector rather than changing
 * this file. The one thing that is spelled out is the callback's query
 * parameters, because those are the vendor's redirect and are fixed by the
 * researched flow: `?code=…&state=…`.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-034'

function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const crmApi = {
  /** The researched contract: the six steps, the quoted evidence, the gaps. */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),

  /** Every design inference this workflow rests on, and how to change each one. */
  inferences: () => apiRequest(`${PREFIX}/inferences`),

  /** Every registered connector, its four methods, and what is sourced. */
  connectors: () => apiRequest(`${PREFIX}/connectors`),

  /** Counts across every connection, plus the vault key's origin. */
  summary: () => apiRequest(`${PREFIX}/summary`),

  // -- step 1: the connection ---------------------------------------------- //

  listConnections: (params = {}) => apiRequest(`${PREFIX}/connections${query(params)}`),
  createConnection: (payload) =>
    apiRequest(`${PREFIX}/connections`, { method: 'POST', body: JSON.stringify(payload) }),
  readConnection: (id) => apiRequest(`${PREFIX}/connections/${id}`),
  updateConnection: (id, payload) =>
    apiRequest(`${PREFIX}/connections/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  disconnect: (id) => apiRequest(`${PREFIX}/connections/${id}`, { method: 'DELETE' }),

  // -- steps 2 to 5: authorize, consent, callback ------------------------- //

  /**
   * The researched step 2. Returns the vendor's URL and the `state` that binds
   * the callback to this authorization, so the page can open the URL and leave
   * the room's own `/callback` route registered for the vendor's redirect.
   */
  authorizeUrl: (id, scope) => apiRequest(`${PREFIX}/connections/${id}/authorize-url${query({ scope })}`),

  /** The researched callback: the vendor redirects back with `?code=…&state=…`. */
  callback: (id, { code, state }) =>
    apiRequest(`${PREFIX}/connections/${id}/callback${query({ code, state })}`, { method: 'POST' }),

  listGrants: (params = {}) => apiRequest(`${PREFIX}/grants${query(params)}`),
  cancelGrant: (id) => apiRequest(`${PREFIX}/grants/${id}`, { method: 'DELETE' }),

  // -- the automation, and step 6 ----------------------------------------- //

  /** [sourced] "Token refresh before expiry." The same path the TTL takes. */
  refresh: (id) => apiRequest(`${PREFIX}/connections/${id}/refresh`, { method: 'POST' }),

  /**
   * [sourced] "Test connection": one low-cost authenticated call.
   *
   * Answers 200 even when the vendor rejects the token, so the caller reads
   * `ok` and `outcome` from the body rather than the HTTP status.
   */
  test: (id) => apiRequest(`${PREFIX}/connections/${id}/test`, { method: 'POST' }),

  health: (id) => apiRequest(`${PREFIX}/connections/${id}/health`),
  tokenEvents: (id, params = {}) => apiRequest(`${PREFIX}/connections/${id}/token-events${query(params)}`),

  // -- room-scoped views --------------------------------------------------- //

  roomConnections: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/connections`),

  /** What is missing before this room's connections can do anything. */
  readiness: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/readiness`),

  /**
   * The sweep the room's own scheduler calls. Nothing pushes at us, so this is
   * what a deployment's scheduler hits; `force` checks the ones that are not due.
   */
  healthCheck: (roomId, force = false) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/health-check${query({ force })}`, { method: 'POST' }),
}
