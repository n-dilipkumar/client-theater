/**
 * Intent stream API (WF-032).
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a
 * method to it is precisely the collision the feature host exists to remove. The
 * host's `apiRequest` is the escape hatch that makes that unnecessary.
 *
 * `PREFIX` is the feature's own: the backend module mounts these under
 * `/api/wf-032`, and `apiRequest` already prepends `/api`.
 *
 * Bodies carry the researched field names verbatim - `sendMode`, `payload`,
 * `conditions.segmentIds`, `contactFilter.keywords`, `contactFilter.requiredFields`,
 * `leadId` - so the documented flow can be followed against the API directly and
 * a reader can match what the page sends against what the research says should be
 * sent. Query parameters are snake_case, which is what the routes declare.
 *
 * The room list and the lead list are read from this feature's own routes rather
 * than from another module's idea of the shape of a room, so this page depends on
 * the HTTP contract only.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-032'

/** Drop empty values so we never send `?state=` and confuse a filter. */
function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

function send(path, method, body, params) {
  const encoded = new URLSearchParams(params || {}).toString()
  return apiRequest(`${PREFIX}${path}${encoded ? `?${encoded}` : ''}`, {
    method,
    body: body === undefined ? undefined : JSON.stringify(body),
  })
}

export const intentApi = {
  /**
   * The published vocabularies: the workflow type, the two send modes, the two
   * payload modes, the Segment rule grammar, and the contact fields a keyword is
   * matched against. Every picker on the page renders from this rather than from
   * a list compiled into the page.
   */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),

  /**
   * The researched explainer, the seven-step flow, and the quoted evidence each
   * claim rests on. A sentence on this page is traceable to a source through
   * this, which is the point of showing them at all.
   */
  explain: () => apiRequest(`${PREFIX}/explain`),

  /**
   * Every judgement call the workflow rests on, and how to change each one.
   *
   * The research names the steps, the two send modes, the two payload outputs
   * and the token. It does not say how the edges behave, so the edges are served
   * as data rather than left for a reader to reconstruct from a diff.
   */
  inferences: () => apiRequest(`${PREFIX}/inferences`),

  /** The researched destinations: webhook recipes, and the wider integration surfaces. */
  destinations: () => apiRequest(`${PREFIX}/destinations`),

  summary: () => apiRequest(`${PREFIX}/summary`),
  roomSummary: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/summary`),
  rooms: () => apiRequest('/records/room?limit=100'),

  segments: () => apiRequest(`${PREFIX}/segments`),
  saveSegment: (payload, actor) => send('/segments', 'POST', payload, { actor }),
  amendSegment: (id, payload, actor) => send(`/segments/${id}`, 'PATCH', payload, { actor }),
  removeSegment: (id, actor) => send(`/segments/${id}`, 'DELETE', undefined, { actor }),

  /**
   * Run one Segment against a lead and see every rule's verdict. A read with no
   * side effect, so nothing is sent - the only way to tell "this company is not
   * in my Segment" from "this Segment is broken" without waiting for a real visit.
   */
  evaluateSegment: (id, leadId) => send(`/segments/${id}/evaluate`, 'POST', { leadId }),

  leads: (params) => apiRequest(`${PREFIX}/leads${query(params)}`),
  lead: (id) => apiRequest(`${PREFIX}/leads/${id}`),
  contacts: (id) => apiRequest(`${PREFIX}/leads/${id}/contacts`),

  visits: (params) => apiRequest(`${PREFIX}/visits${query(params)}`),

  /**
   * The researched trigger, in one call: a company visit, which refreshes the
   * lead's activity data, evaluates every workflow, and records one delivery row
   * per workflow per outcome - including the outcomes where nothing was sent.
   */
  recordVisit: (leadId, pagesViewed, actor) =>
    send('/visits', 'POST', { leadId, pagesViewed, actor }, { actor }),

  workflows: (params) => apiRequest(`${PREFIX}/workflows${query(params)}`),
  workflow: (id) => apiRequest(`${PREFIX}/workflows/${id}`),
  saveWorkflow: (payload, actor) => send('/workflows', 'POST', payload, { actor }),
  amendWorkflow: (id, payload, actor) => send(`/workflows/${id}`, 'PATCH', payload, { actor }),
  removeWorkflow: (id, actor) => send(`/workflows/${id}`, 'DELETE', undefined, { actor }),

  /** The one place the generated token is shown again. Every read path masks it. */
  revealToken: (id) => send(`/workflows/${id}/token`, 'POST', undefined, {}),

  /** The exact JSON that would be POSTed for a lead, and whether it would be. */
  preview: (workflowId, leadId) => apiRequest(`${PREFIX}/workflows/${workflowId}/preview${query({ lead_id: leadId })}`),

  deliveries: (params) => apiRequest(`${PREFIX}/deliveries${query(params)}`),
  delivery: (id) => apiRequest(`${PREFIX}/deliveries/${id}`),
  resend: (id, actor) => send(`/deliveries/${id}/resend`, 'POST', undefined, { actor }),

  fields: () => apiRequest(`${PREFIX}/fields`),
}

/** The delivery state, rendered as the sentence an operator actually needs. */
export const SKIP_REASONS = {
  already_sent: 'already sent - this workflow sends a lead only once',
  workflow_inactive: 'not sent - the workflow is paused',
  segment_not_matched: 'not sent - no saved Segment matched this company',
}

/** The badge tone for a delivery state. A skip is never shown as a failure. */
export const STATE_TONES = {
  delivered: 'insert',
  failed: 'delete',
  skipped: 'neutral',
}

/**
 * The researched send mode, in the words the research uses.
 *
 * "only send a lead once" and "send updates as well" are two different promises,
 * and a lead that has been sent twice is either the second one working or a bug,
 * depending on which mode the workflow is in. So the mode is always on the row.
 */
export function sendModeLabel(workflow) {
  if (!workflow) return ''
  return workflow.sendMode === 'updates' ? 'Sends updates as well' : 'Sends a lead only once'
}

/** The payload composition, in the words the research uses. */
export function payloadLabel(workflow) {
  if (!workflow) return ''
  return workflow.payload === 'company_contacts' ? 'Company + Contacts' : 'Company only'
}
