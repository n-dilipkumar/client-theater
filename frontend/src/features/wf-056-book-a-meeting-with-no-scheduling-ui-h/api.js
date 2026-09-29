/**
 * Headless booking API calls (WF-056).
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a
 * method to it is precisely the conflict the feature host exists to remove. The
 * host's `apiRequest` is the escape hatch that makes that unnecessary.
 *
 * `PREFIX` is the feature's own: the backend module mounts these under
 * `/api/wf-056`, and `apiRequest` already prepends `/api`.
 *
 * The two researched calls are named `discover` and `book`, and the split is
 * load-bearing rather than cosmetic. The research is emphatic that a booking is
 * two calls with a single-use session between them, so this client does not
 * offer a convenience wrapper that collapses them - a helper that opened a
 * session and booked it in one call would make the single-use rule impossible
 * to exercise, and would hide the retry the research forbids.
 *
 * One limitation of the shared client, worth stating rather than discovering: a
 * refusal from this feature's routes carries a machine-readable `reason` - one
 * of the published researched failure names, served at `/vocabulary` under
 * `schedule_failures` and `init_failures` - but `apiRequest` surfaces only
 * `detail` and `status`. So the page shows the sentence, which is what a person
 * needs, and a *programmatic* caller of these routes reads `reason` directly
 * from the response body. Widening `apiRequest` to carry the whole body is
 * platform work on a shared file, not something a feature may do here.
 *
 * Every picker on the page is rendered from `/vocabulary` rather than from a
 * list compiled into this file, so a team that adds a section ships a record
 * instead of a change here.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-056'

function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value))
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const headlessApi = {
  /** The researched vocabulary. Drives every picker, and the failure messages. */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),

  /**
   * What the research leaves open, what this build chose, and how to change it.
   *
   * The research is specific about the wire and silent about almost everything
   * around it, so the judgement calls are product behaviour rather than
   * comments, and they are served as data a reviewer can disagree with by name.
   */
  inferences: () => apiRequest(`${PREFIX}/inferences`),

  /**
   * The four sourced session rules on their own.
   *
   * Separate from `/inferences` on purpose: a reviewer checking what the
   * research said should not have to read this build's decisions to find it, and
   * a disagreement should be aimed at the right one of the two.
   */
  rules: () => apiRequest(`${PREFIX}/rules`),

  summary: (params = {}) => apiRequest(`${PREFIX}/summary${query(params)}`),

  /** Scoped tokens. A read never returns the secret. */
  listCredentials: (params = {}) => apiRequest(`${PREFIX}/credentials${query(params)}`),
  /**
   * Generate a token. The only response that ever contains the secret.
   *
   * `role` is required: "Only users with the Admin role can generate API tokens
   * in Command Center. Workspace Managers do not have access to the credentials
   * page."
   */
  createCredential: (payload) =>
    apiRequest(`${PREFIX}/credentials`, { method: 'POST', body: JSON.stringify(payload) }),
  readCredential: (id) => apiRequest(`${PREFIX}/credentials/${id}`),
  revokeCredential: (id) => apiRequest(`${PREFIX}/credentials/${id}`, { method: 'DELETE' }),

  /** The bookable targets: Concierge routers, scheduling links, handoff routers. */
  listAssets: (params = {}) => apiRequest(`${PREFIX}/assets${query(params)}`),
  createAsset: (payload, params = {}) =>
    apiRequest(`${PREFIX}/assets${query(params)}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  readAsset: (id) => apiRequest(`${PREFIX}/assets/${id}`),
  updateAsset: (id, payload) =>
    apiRequest(`${PREFIX}/assets/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteAsset: (id) => apiRequest(`${PREFIX}/assets/${id}`, { method: 'DELETE' }),

  /**
   * The busy blocks the availability engine reads.
   *
   * Keyed by host, not by asset: one person's calendar is the same whichever
   * surface books them, and a per-asset key would let the same host be
   * double-booked across two routers.
   */
  listCalendar: (params = {}) => apiRequest(`${PREFIX}/calendar${query(params)}`),
  addCalendarBlock: (payload, params = {}) =>
    apiRequest(`${PREFIX}/calendar${query(params)}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /** Sessions opened for a room, newest first. */
  listSessions: (roomId, params = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/sessions${query(params)}`),

  /**
   * Call #1: discover or route.
   *
   * Returns a `routeId` and a `schedulingData` list of `startTimes`. The session
   * it opens is single-use and short-lived - a hold on availability, not a
   * reservation.
   */
  discover: (roomId, payload, params = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/sessions${query(params)}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /** Read one session. A read, so it never spends the session. */
  readSession: (roomId, routeId) => apiRequest(`${PREFIX}/rooms/${roomId}/sessions/${routeId}`),

  /**
   * Call #2: book.
   *
   * `startTime` must be one of the strings `schedulingData` returned, passed
   * back verbatim. A failure spends the session, so the next step is always a
   * fresh `discover` - never a retry of this route with the same `routeId`.
   */
  book: (roomId, routeId, payload, params = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/sessions/${routeId}/book${query(params)}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /** Meetings this room booked. `invites_sent` is false on every row. */
  listMeetings: (roomId, params = {}) => apiRequest(`${PREFIX}/rooms/${roomId}/meetings${query(params)}`),
  readMeeting: (roomId, meetingId) => apiRequest(`${PREFIX}/rooms/${roomId}/meetings/${meetingId}`),

  /**
   * The headless call log, including the refused calls.
   *
   * This is where the researched instruction becomes visible rather than merely
   * obeyed: a caller that retries one `routeId` twice leaves two rows here, the
   * second saying `session_consumed`.
   */
  listCalls: (roomId, params = {}) => apiRequest(`${PREFIX}/rooms/${roomId}/calls${query(params)}`),
}
