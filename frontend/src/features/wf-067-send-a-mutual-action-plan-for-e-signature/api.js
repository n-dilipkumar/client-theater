/**
 * Mutual action plan e-signature API (WF-067).
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a
 * method to it is precisely the collision the feature host exists to remove. The
 * host's `apiRequest` is the escape hatch that makes that unnecessary.
 *
 * `PREFIX` is the feature's own: the backend module mounts these under
 * `/api/wf-067`, and `apiRequest` already prepends `/api`.
 *
 * Every payload is sent in the research's own spelling - `subject`, `message`,
 * `signing_order`, `recipients[]` with `email`/`name`/`role`/`fields[]`, and an
 * `X-Documenso-Secret` header - so the documented flow can be followed against
 * the API directly and a reader can match what the page sends against what the
 * research says should be sent. The room list is read from the core records
 * route, so this page depends on the HTTP contract rather than on another
 * module's idea of the shape of a room.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-067'

/** Drop empty values so we never send `?status=` and confuse a filter. */
function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const mapApi = {
  /**
   * The published vocabularies: the five recipient roles with the approver's
   * rule, the five envelope statuses, the fourteen webhook events with their
   * meanings, the seven milestones and which are refusals, the field types, the
   * percentage coordinates, and the two invariants this product keeps. Every
   * picker and every badge on the page renders from this rather than from a list
   * compiled into this file, so a value added on the server reaches every client
   * at once.
   */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),

  /**
   * Every judgement call this workflow rests on: what the research fixes, what it
   * leaves open, and which reading this build took. Rendered verbatim on the
   * page's last tab so a reviewer reads the list instead of reconstructing it
   * from a diff.
   */
  inferences: () => apiRequest(`${PREFIX}/inferences`),

  /** What the vendor needs to configure a webhook, in one response. */
  webhookContract: () => apiRequest(`${PREFIX}/whoami`),

  templates: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/templates`),
  createTemplate: (roomId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/templates`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  template: (roomId, templateId) => apiRequest(`${PREFIX}/rooms/${roomId}/templates/${templateId}`),

  plans: (roomId, params = {}) => apiRequest(`${PREFIX}/rooms/${roomId}/plans${query(params)}`),
  createPlan: (roomId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/plans`, { method: 'POST', body: JSON.stringify(payload) }),
  plan: (roomId, planId) => apiRequest(`${PREFIX}/rooms/${roomId}/plans/${planId}`),
  distribute: (roomId, planId) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/plans/${planId}/distribute`, { method: 'POST' }),
  cancel: (roomId, planId) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/plans/${planId}/cancel`, { method: 'POST' }),

  /**
   * The researched approver gate, asked per person: "APPROVER | Must approve
   * before signers can sign". A `GET` because it checks and writes nothing.
   */
  canSign: (roomId, planId, email) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/plans/${planId}/can-sign${query({ email })}`),

  /**
   * The verified webhook the vendor posts to. Present as a function so a
   * developer can drive the documented flow from this file; the page itself never
   * calls it, because a room must not be able to forge its own vendor events.
   */
  webhook: (roomId, secret, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/webhook`, {
      method: 'POST',
      headers: { 'X-Documenso-Secret': secret },
      body: JSON.stringify(payload),
    }),

  events: (roomId, params = {}) => apiRequest(`${PREFIX}/rooms/${roomId}/events${query(params)}`),
  notices: (roomId, params = {}) => apiRequest(`${PREFIX}/rooms/${roomId}/notices${query(params)}`),
  acknowledge: (roomId, payload = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/notices/acknowledge`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  summary: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/summary`),

  rooms: () => apiRequest('/records/room?limit=100'),
}

/**
 * The tone a milestone is rendered in, from the published vocabulary.
 *
 * Reading the server's list rather than keeping a local copy means a milestone
 * added there renders here on the next load instead of falling through to a
 * neutral badge that says nothing. The tone is decoration only: every badge on
 * the page carries its milestone's label as text, because status is never
 * conveyed by colour alone.
 */
export function milestoneTone(vocabulary, milestone) {
  if (milestone === 'approved') return 'insert'
  if (milestone === 'refused_by_approver' || milestone === 'refused_by_signer') return 'delete'
  if (milestone === 'cancelled') return 'restore'
  if (milestone === 'awaiting_signature') return 'update'
  return 'neutral'
}

/** The server's own sentence for a milestone, so the page never paraphrases it. */
export function milestoneMeaning(vocabulary, milestone) {
  const entry = (vocabulary?.milestones || []).find((row) => row.milestone === milestone)
  return entry?.label || milestone || 'unknown'
}

/** The server's own sentence for an event, so the log never paraphrases it. */
export function eventMeaning(vocabulary, event) {
  const entry = (vocabulary?.events || []).find((row) => row.event === event)
  return entry?.meaning || event
}

/**
 * Whether this milestone is a refusal, and by whom.
 *
 * The two refusals are separate milestones because the research makes them
 * different problems: an approver refuses before signers can sign, so the seller
 * can fix the plan and send it again, while a signer refuses the terms. A page
 * that collapsed them would lose the one distinction the seller needs.
 */
export function isRefusal(milestone) {
  return milestone === 'refused_by_signer' || milestone === 'refused_by_approver'
}

/** Whether a milestone can still move. */
export function isTerminal(milestone) {
  return milestone === 'approved' || milestone === 'cancelled' || isRefusal(milestone)
}

/**
 * The one sentence saying what the seller should do about this plan.
 *
 * An awaiting-signature plan whose approver has not approved is the one that
 * needs a person, so the page's whole job is to make that row impossible to miss
 * and to name who is holding it up. This helper exists so the "needs you" list
 * and the full table cannot disagree about which is which.
 */
export function needsAttention(plan) {
  return plan?.milestone === 'awaiting_signature' && !plan?.signing_unlocked
}

/**
 * The researched refusal reasons, straight from the server.
 *
 * Read from the served vocabulary rather than typed here, because the point of
 * the list is to be the sentences the product actually uses - a page that typed
 * its own would be free to drift from them.
 */
export function reasons(vocabulary) {
  return vocabulary?.reasons || {}
}

/**
 * Why a refusal was refused, in the caller's own words.
 *
 * The server returns the detail on every refusal, so a seller fixing one sees
 * the whole sentence rather than discovering the second problem on the retry.
 */
export function refusalSummary(error) {
  return String(error?.detail || error?.message || error || 'The request was refused.')
}