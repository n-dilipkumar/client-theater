/**
 * WF-091's own API wrapper.
 *
 * `apiRequest` from `@/lib/api` is the transport, exactly as the contract asks.
 * The shared `api` object grows no methods, so a hundred features can each talk
 * to their own `/api/WF-091` routes without anyone editing a shared file.
 *
 * The prefix is upper case because the ticket slug the issue names is
 * `/api/WF-091`, and `WF-094` already ships that spelling. The host matches paths
 * literally, so the two spellings are not interchangeable.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/WF-091'

const encode = encodeURIComponent

function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

function call(path, params) {
  return apiRequest(`${BASE}${path}${query(params)}`)
}

export const approvalApi = {
  /** The filters, the cap, the requirements and the states, from one place on the server. */
  vocabulary: () => call('/vocabulary'),

  /** What the research left open, and which reading this build took. */
  inferences: () => call('/inferences'),

  /** What is waiting on an approver. */
  summary: (roomId) => call('/summary', { room_id: roomId }),

  /** The configured rules, oldest first. */
  rules: (roomId) => call('/rules', { room_id: roomId }),

  /**
   * Save a rule: the filters, the approvers, the requirement and the note together.
   *
   * There is no separate "add filter" call. The research's own UI refuses to save a
   * rule that is missing any of the four, so a half-configured rule is a state this
   * product does not create either.
   */
  createRule: (payload, { roomId, actor } = {}) =>
    apiRequest(`${BASE}/rules${query({ room_id: roomId, actor })}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /** The Quotes switch, and any other field, revalidated by the server. */
  patchRule: (ruleId, payload, { actor } = {}) =>
    apiRequest(`${BASE}/rules/${encode(ruleId)}${query({ actor })}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),

  removeRule: (ruleId, { actor } = {}) =>
    apiRequest(`${BASE}/rules/${encode(ruleId)}${query({ actor })}`, { method: 'DELETE' }),

  /**
   * The researched "View approval conditions".
   *
   * A GET, because it checks and writes nothing. It is the same evaluation the
   * submission runs, so what a seller is shown before submitting is what is enforced
   * after they submit.
   */
  conditions: (quoteId) => call(`/quotes/${encode(quoteId)}/conditions`),

  /** Which state a quote is in, and what releases a lock. */
  quoteState: (quoteId) => call(`/quotes/${encode(quoteId)}/state`),

  /**
   * Request approval.
   *
   * `trigger` is one of `submit_approval`, `publish` or `share`, because enrolment
   * fires on the publish and share attempts as well as on the button.
   */
  submit: (quoteId, { notesToApprover, trigger, roomId, actor } = {}) =>
    apiRequest(`${BASE}/quotes/${encode(quoteId)}/submit${query({ room_id: roomId, actor, trigger })}`, {
      method: 'POST',
      body: JSON.stringify({ notes_to_approver: notesToApprover }),
    }),

  /**
   * Approve, or request changes.
   *
   * `message` is optional on an approval and required on a change request: the server
   * answers 422 without one, and a change request with nothing in it is not an
   * instruction the seller can act on.
   */
  decide: (enrolmentId, { decision, message, approver, actor } = {}) =>
    apiRequest(`${BASE}/requests/${encode(enrolmentId)}/decide${query({ approver, actor })}`, {
      method: 'POST',
      body: JSON.stringify({ decision, message }),
    }),

  /** The queue, filtered to a status, a quote or one approver. */
  requests: ({ status, quoteId, approver, roomId } = {}) =>
    call('/requests', {
      status,
      quote_id: quoteId,
      approver,
      room_id: roomId,
    }),

  request: (enrolmentId) => call(`/requests/${encode(enrolmentId)}`),

  /** The share gate. A 409 here is the researched refusal, not a server fault. */
  share: (quoteId, { roomId, actor } = {}) =>
    apiRequest(`${BASE}/quotes/${encode(quoteId)}/share${query({ room_id: roomId, actor })}`, {
      method: 'POST',
      body: JSON.stringify({}),
    }),

  activity: (quoteId) => call(`/quotes/${encode(quoteId)}/activity`),

  notifications: (quoteId) => call('/notifications', { quote_id: quoteId }),
}

/** Every room, so the page can offer one. Read from the core collection. */
export const listRooms = () => apiRequest('/records/room?limit=100')

/**
 * Every quote this workflow can be pointed at.
 *
 * The quotes belong to WF-086, so they are read from that collection rather than
 * created here. A workspace that provisions its quotes under another collection
 * passes that name instead; the approval rules bind to properties, not to a
 * collection, so the rules themselves need no change.
 */
export function listQuotes(collection = 'wf086_quote') {
  return apiRequest(`/records/${encode(collection)}?limit=200`)
}

/**
 * The vote a status is asking for, as a label.
 *
 * Read from the server's vocabulary rather than compiled here, and given a fallback
 * for the status a page has not been told about yet. A label that renders as
 * `undefined` is worse than one that shows the raw value.
 */
export function statusLabel(status, vocabulary) {
  const entry = (vocabulary?.quote_states || []).find((row) => row.state === status)
  return entry?.label || status || 'Unknown'
}

/**
 * The badge tone for a status.
 *
 * A tone is never the only signal: every badge on this page renders its label as
 * text beside the colour, so a screen-reader user gets the same information.
 */
export function statusTone(status) {
  switch (status) {
    case 'PENDING_APPROVAL':
      return 'warning'
    case 'APPROVED':
      return 'success'
    case 'REJECTED':
      return 'destructive'
    case 'SHARED':
      return 'info'
    default:
      return 'neutral'
  }
}

/**
 * Whether a share is currently allowed.
 *
 * The server is the authority and refuses either way, so this is for hiding a button
 * rather than for permitting an action. A stale flag costs one error message; a
 * missing one costs a seller a click that cannot work.
 */
export function isShareable(state) {
  return state === 'APPROVED' || state === 'SHARED' || state === 'ACCEPTED'
}

/**
 * The label for an approver requirement, read from the server's own words.
 *
 * "All approvers required" and "At least one approver required" are the two the
 * research names, and they are not interchangeable, so the picker shows the vendor's
 * wording rather than a paraphrase.
 */
export function requirementLabel(requirement, vocabulary) {
  const entry = (vocabulary?.approvers?.requirements || []).find(
    (row) => row.requirement === requirement,
  )
  return entry?.label || requirement
}

/** How many approvers a rule names, for the count beside the rule. */
export function approverCount(rule) {
  return (rule?.data?.approvers || rule?.approvers || []).length
}
