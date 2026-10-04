/**
 * Handoff scheduler API calls (WF-055).
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a
 * method to it is precisely the conflict the feature host exists to remove. The
 * host's `apiRequest` is the escape hatch that makes that unnecessary.
 *
 * `PREFIX` is the feature's own: the backend module mounts these under
 * `/api/wf-055`, and `apiRequest` already prepends `/api`.
 *
 * The two researched calls keep their researched shape. `initSimple` is scoped to
 * the workspace and the booker, and `scheduleSimple` carries the routing, the
 * router, the path and the booker in its own path, because the researched payload
 * carries all four and a booking that could name a path the routing never offered
 * would lose the property that makes the flow safe.
 *
 * Every picker on the page is rendered from the `/vocabulary` endpoint rather than
 * from a list compiled into this file, so a team that adds a request shape or a
 * role ships a record instead of a change here.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-055'

function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value))
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const handoffApi = {
  /** The researched vocabulary: the two request shapes, the two roles, the toggle. */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),

  /**
   * What the research leaves open, what this build chose, and how to change it.
   *
   * The research for WF-055 never says which calendar operation combines the
   * assignee and a Required invitee on one path, so that choice is served as data a
   * reviewer can disagree with by name.
   */
  inferences: () => apiRequest(`${PREFIX}/inferences`),

  /** The pods and routers the handoff reads, with each path's gate set. */
  catalog: (params = {}) => apiRequest(`${PREFIX}/catalog${query(params)}`),

  /** Counts for the page header, over exactly the rows the filters return. */
  summary: (params = {}) => apiRequest(`${PREFIX}/summary${query(params)}`),

  workspaces: (params = {}) => apiRequest(`${PREFIX}/workspaces${query(params)}`),
  workspace: (workspaceId) => apiRequest(`${PREFIX}/workspaces/${workspaceId}`),
  createWorkspace: (payload) =>
    apiRequest(`${PREFIX}/workspaces`, { method: 'POST', body: JSON.stringify(payload) }),

  routers: (params = {}) => apiRequest(`${PREFIX}/routers${query(params)}`),
  router: (routerId) => apiRequest(`${PREFIX}/routers/${routerId}`),
  createRouter: (payload) =>
    apiRequest(`${PREFIX}/routers`, { method: 'POST', body: JSON.stringify(payload) }),

  /**
   * Who would this lead reach? Writes nothing.
   *
   * The read-only half of `initSimple`: same rule evaluation, same calendar
   * arithmetic, no routing row and no meeting.
   */
  check: (roomId, workspaceId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/workspaces/${workspaceId}/check`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /** The researched init call: evaluate the router, answer with its routing paths. */
  initSimple: (roomId, workspaceId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/workspaces/${workspaceId}/init-simple`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /** The researched schedule call: book one slot on one path of one routing. */
  scheduleSimple: (roomId, routingId, routerId, pathId, bookerId, payload) =>
    apiRequest(
      `${PREFIX}/rooms/${roomId}/routing/${routingId}/router/${routerId}` +
        `/path/${pathId}/booker/${bookerId}/schedule-simple`,
      { method: 'POST', body: JSON.stringify(payload) },
    ),

  routings: (params = {}) => apiRequest(`${PREFIX}/routings${query(params)}`),
  routing: (routingId) => apiRequest(`${PREFIX}/routings/${routingId}`),

  meetings: (params = {}) => apiRequest(`${PREFIX}/meetings${query(params)}`),
  meeting: (meetingId) => apiRequest(`${PREFIX}/meetings/${meetingId}`),
  cancelMeeting: (meetingId) =>
    apiRequest(`${PREFIX}/meetings/${meetingId}/cancel`, { method: 'POST' }),
}
