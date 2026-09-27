/**
 * CRM workflows API (WF-030).
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a
 * method to it is precisely the collision the feature host exists to remove. The
 * twelve original workflow branches each appended to a hard-coded `ROUTES` array
 * and all twelve conflicted; `apiRequest` is the escape hatch that makes that
 * unnecessary.
 *
 * `PREFIX` is this feature's own: the backend module mounts these under
 * `/api/wf-030`, and `apiRequest` already prepends `/api`.
 *
 * Every payload is sent exactly as the research names it - `enrollment_type`,
 * `trigger.criteria.family`, `refinements`, the four action kinds - so the
 * documented flow can be followed against the API directly and a reader can match
 * what the page sends against what the research says should be sent.
 *
 * The room list is read from the core records route, so this page depends on the
 * HTTP contract rather than on another module's idea of the shape of a room.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-030'

/** Drop empty values so we never send `?contact=` and confuse a filter. */
function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const workflowApi = {
  /**
   * The published vocabularies: the five filter families and the refinements each
   * one publishes, the four action kinds, the matcher's reasons, the stage
   * constraint, and this product's action-to-family table.
   *
   * Every picker on the page renders from this rather than from a list compiled
   * into this file, so the editor and the server's validator can never disagree
   * about what is legal.
   */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),

  /**
   * Every judgement call the workflow rests on, and how to change each one.
   *
   * The research names the five families, the refinements, the contact-based rule,
   * the trigger, the four actions and the silence on the seller's screen. It does
   * not say what happens on the edges of that, so the edges are served as data
   * rather than left for a reader to reconstruct from a diff.
   */
  inferences: () => apiRequest(`${PREFIX}/inferences`),

  /** Step 1: is the integration on, and is this workspace connected to a deal? */
  integrations: () => apiRequest(`${PREFIX}/integrations`),
  registerIntegration: (payload) =>
    apiRequest(`${PREFIX}/integrations`, { method: 'POST', body: JSON.stringify(payload) }),
  amendIntegration: (id, payload) =>
    apiRequest(`${PREFIX}/integrations/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),

  /** Steps 2 to 7: the library. */
  workflows: (params = {}) => apiRequest(`${PREFIX}/workflows${query(params)}`),
  create: (payload) =>
    apiRequest(`${PREFIX}/workflows`, { method: 'POST', body: JSON.stringify(payload) }),
  workflow: (id) => apiRequest(`${PREFIX}/workflows/${id}`),
  amend: (id, payload) =>
    apiRequest(`${PREFIX}/workflows/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  withdraw: (id) => apiRequest(`${PREFIX}/workflows/${id}`, { method: 'DELETE' }),

  /** Step 8: publish, and the off switch that is not retirement. */
  publish: (id) => apiRequest(`${PREFIX}/workflows/${id}/publish`, { method: 'POST' }),
  unpublish: (id) => apiRequest(`${PREFIX}/workflows/${id}/unpublish`, { method: 'POST' }),

  /** The source side, room-scoped: the webhook end, and what it classified as. */
  activity: (roomId, params = {}) => apiRequest(`${PREFIX}/rooms/${roomId}/activity${query(params)}`),
  recordActivity: (roomId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/activity`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /**
   * The researched data flow, for one contact in one room. Preferred over calling
   * the two routes separately, because it reports every decision - which workflows
   * were skipped and why, which matched nothing and why, and what fired - rather
   * than making a caller reconstruct them from two responses.
   */
  evaluate: (roomId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/evaluate`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  enrollments: (roomId, params = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/enrollments${query(params)}`),
  enrollment: (roomId, id) => apiRequest(`${PREFIX}/rooms/${roomId}/enrollments/${id}`),
  summary: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/summary`),

  rooms: () => apiRequest('/records/room?limit=100'),
}

/**
 * The refinements a family publishes, from the served matrix.
 *
 * Built from the vocabulary rather than from a list in this file, because the
 * matrix is the researched surface and the editor must not carry a second copy of
 * it. A family with no published refinement yields an empty list, and the page says
 * so rather than offering a control the server will refuse.
 */
export function refinementsFor(vocabulary, family) {
  const row = (vocabulary?.filter_families || []).find((entry) => entry.name === family)
  return row?.refinements || []
}

/**
 * The label to put on a refinement control, from the served refinement list.
 */
export function refinementLabel(vocabulary, name) {
  const row = (vocabulary?.refinements || []).find((entry) => entry.name === name)
  return row?.label || name
}

/**
 * Whether an action kind is one this build resolves.
 *
 * The research says "send emails, slack notifications, update fields, change stages
 * **and more!**", so the action list is open where the filter list is closed. An
 * action outside the four is stored and reported unresolved rather than dropped,
 * and this is what the page uses to say so next to the stored action.
 */
export function isResolvableAction(vocabulary, kind) {
  return (vocabulary?.action_kinds || []).some((entry) => entry.kind === kind)
}

/**
 * The sentence a person reads for a filter, in the source's own words.
 *
 * Each family's gloss is served rather than compiled here, so a change to the
 * matrix reaches the page without a change to this file.
 */
export function familyLabel(vocabulary, family) {
  const row = (vocabulary?.filter_families || []).find((entry) => entry.name === family)
  return row?.label || family
}
