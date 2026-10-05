/**
 * Chase and reroute API (WF-107).
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a method
 * to it is exactly the collision the feature host exists to remove. The host's
 * `apiRequest` is the escape hatch that makes that unnecessary.
 *
 * `PREFIX` is the feature's own: the backend module mounts these under `/api/wf-107`, and
 * `apiRequest` already prepends `/api`.
 *
 * Every payload is sent in the research's own spelling -- `kind` for the two triggers
 * ("If customer has been unresponsive" is `customer_idle`), the nine step kinds, and an
 * integer `duration_seconds` -- so the documented flow can be driven against the API
 * directly and a reader can match what this page sends against what the research says
 * should be sent.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-107'

/** Drop empty values so we never send `?state=` and confuse a filter. */
function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const chaseApi = {
  /**
   * The published terms: both triggers with their labels, the nine step blocks, the two
   * interruption events, the two anchors, the exclusive duration bounds, the conversation
   * and run states, the eight skip reasons with their text, the office-hours vocabulary,
   * and every refusal code with its status. Every picker and every badge on the page
   * renders from this rather than from a list compiled into this file, so a rule changed
   * on the server reaches every client at once.
   */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),

  /**
   * Every judgement call this workflow rests on: what the research fixes, what it leaves
   * open, which reading this build took, and what was rejected. Rendered on the page's
   * last section so a reviewer reads the list instead of reconstructing it from a diff.
   */
  inferences: () => apiRequest(`${PREFIX}/inferences`),

  decision: (id) => apiRequest(`${PREFIX}/decisions/${id}`),

  /** The room list comes from the core records route, so this page depends on the HTTP
   *  contract rather than on another module's idea of a room's shape. */
  rooms: () => apiRequest('/records/room?limit=100'),

  summary: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/summary`),

  // -- triggers ------------------------------------------------------------- //

  triggers: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/triggers`),
  trigger: (roomId, triggerId) => apiRequest(`${PREFIX}/rooms/${roomId}/triggers/${triggerId}`),
  createTrigger: (roomId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/triggers`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  patchTrigger: (roomId, triggerId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/triggers/${triggerId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  /** Step 7 of the flow: "Save both and set live". A draft fires nothing. */
  goLive: (roomId, triggerId) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/triggers/${triggerId}/go-live`, { method: 'POST' }),
  deleteTrigger: (roomId, triggerId) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/triggers/${triggerId}`, { method: 'DELETE' }),

  // -- the sweep ------------------------------------------------------------ //

  /**
   * Evaluate the inactivity triggers and fire the ones that are due. Nothing fires until
   * this is called; the response carries `due`, which is the count the next call would
   * act on, and every skip with a published reason code.
   */
  evaluate: (roomId, payload = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/evaluate`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  // -- runs ----------------------------------------------------------------- //

  runs: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/runs`),
  run: (roomId, runId) => apiRequest(`${PREFIX}/rooms/${roomId}/runs/${runId}`),
  advance: (roomId, runId, payload = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/runs/${runId}/advance`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  /** Finish a wait, or end the run because it was interrupted. */
  resolve: (roomId, runId, payload = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/runs/${runId}/resolve`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  // -- conversations -------------------------------------------------------- //

  conversations: (roomId, params = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/conversations${query(params)}`),
  conversation: (roomId, conversationId) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/conversations/${conversationId}`),
  openConversation: (roomId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/conversations`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  /**
   * Write a customer, teammate or workflow message. A customer message re-arms the
   * trigger: that is the whole of "can only trigger once per customer message".
   */
  addMessage: (roomId, conversationId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/conversations/${conversationId}/messages`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  /** Step 6: **Assign conversation** to reroute to the desired inbox. */
  reroute: (roomId, conversationId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/conversations/${conversationId}/reroute`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  close: (roomId, conversationId, payload = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/conversations/${conversationId}/close`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  snooze: (roomId, conversationId, payload = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/conversations/${conversationId}/snooze`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  activity: (roomId, conversationId) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/conversations/${conversationId}/activity`),

  // -- office hours --------------------------------------------------------- //

  /**
   * The weekly schedule. `derived` is true when nothing has been stored, so the page can
   * tell the built-in default from one somebody set, and `derivation` carries the sentence
   * the model was taken from.
   */
  officeHours: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/office-hours`),
  saveOfficeHours: (roomId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/office-hours`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
}

export default chaseApi