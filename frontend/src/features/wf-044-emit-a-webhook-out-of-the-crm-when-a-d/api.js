/**
 * The CRM webhook API (WF-044).
 *
 * These calls live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a
 * method to it is exactly the collision the feature host exists to remove.
 * `apiRequest` already prepends `/api`, so `PREFIX` is this feature's own, and the
 * query string is built into the path because `apiRequest` forwards its options
 * straight to `fetch`, which has no `params` option to forward.
 *
 * Every write carries a `permissions` string, and that is not decoration. The
 * research states two separate rules - *"To set up webhook actions in workflows,
 * users must have Edit permissions for workflows or Super Admin permissions. To
 * publish workflows, users must have Publish permissions for workflows."* - and
 * this product has no login of its own, so the page has to be told which
 * permissions the operator holds in the CRM. The server enforces the rule either
 * way; a page that sent a permission it did not have would get the researched 403
 * back, which is the behaviour worth seeing.
 *
 * There is no `deliver` call here on purpose. The inbound endpoint is a webhook
 * the CRM posts to, not something a browser invokes by hand, so the page shows
 * the endpoint's own sample preview - the same bytes the endpoint will verify -
 * rather than pretending to be the CRM.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-044'

/** Drop empty values so we never send `?outcome=` and confuse a filter. */
function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const webhookApi = {
  /**
   * The published vocabularies: two methods, the HTTPS rule, three authentication
   * types, two body modes, three objects, two permissions, the 1,000-per-app cap,
   * and the reason vocabulary a delivery can be refused with.
   */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),

  /**
   * Every judgement call this workflow rests on, and how to change each one. The
   * research names the CRM-side editor in detail and says almost nothing about
   * the receiving end, so the edges are served as data rather than left for a
   * reader to reconstruct from a diff.
   */
  inferences: () => apiRequest(`${PREFIX}/inferences`),

  /** The room's Webhook settings page: the endpoint URL and the secret. */
  endpoint: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/endpoint`),
  register: (roomId, payload, permissions) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/endpoint${query({ permissions })}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  amend: (roomId, payload, permissions) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/endpoint${query({ permissions })}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),

  /** Step 5: Save, then Publish. Two permissions, because the source separates them. */
  publish: (roomId, permissions) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/endpoint/publish${query({ permissions })}`, {
      method: 'POST',
    }),
  unpublish: (roomId, permissions) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/endpoint/unpublish${query({ permissions })}`, {
      method: 'POST',
    }),

  /** The body and the signature the built-in **Test** control will produce. */
  sample: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/endpoint/sample`),

  /** The CRM-side automations pointed at this room's endpoint. */
  automations: (roomId, params = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/automations${query(params)}`),
  addAutomation: (roomId, payload, permissions) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/automations${query({ permissions })}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  retireAutomation: (roomId, id, permissions) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/automations/${id}${query({ permissions })}`, {
      method: 'DELETE',
    }),

  /** The delivery log, refused deliveries included unless they are asked out. */
  deliveries: (roomId, params = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/deliveries${query(params)}`),
  delivery: (roomId, id) => apiRequest(`${PREFIX}/rooms/${roomId}/deliveries/${id}`),

  /** The deal panel, and what the endpoint told the rep. */
  deals: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/deals`),
  deal: (roomId, id) => apiRequest(`${PREFIX}/rooms/${roomId}/deals/${id}`),
  notices: (roomId, params = {}) => apiRequest(`${PREFIX}/rooms/${roomId}/notices${query(params)}`),
  acknowledge: (roomId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/notices/acknowledge`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  summary: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/summary`),

  /** Rooms come from the core records route, so this page depends on the HTTP contract. */
  rooms: () => apiRequest('/records/room?limit=100'),
}

/**
 * The endpoint's object, as a label, from the served vocabulary.
 *
 * Built from the vocabulary rather than from a list in this file, because the
 * object picker the rep fills in the CRM is the server's list and the two must not
 * be able to disagree.
 */
export function objectLabel(vocabulary, name) {
  const row = (vocabulary?.objects || []).find((entry) => entry.name === name)
  return row?.label || name
}

/**
 * The authentication type's own rule, in the vendor's words.
 *
 * The three modes are enumerated in the sources, so a client that wants to explain
 * one to a rep should read the sentence the source published rather than a
 * paraphrase compiled into a page.
 */
export function authRule(vocabulary, mode) {
  const row = (vocabulary?.auth_modes || []).find((entry) => entry.mode === mode)
  return row?.rule || ''
}

/**
 * The full sentence behind a refusal, from the served reason list.
 *
 * "It did not work" is not an answer a rep can act on; the vocabulary carries a
 * sentence per reason precisely so the page can show it.
 */
export function reasonText(vocabulary, reason) {
  return vocabulary?.reasons?.[reason] || reason || ''
}

/**
 * Whether an effect is one worth a notification.
 *
 * The server decides this and records `notified` on the delivery; the page mirrors
 * the same rule so a delivery that changed nothing is visibly quiet rather than
 * looking like a notification that was lost.
 */
export function notifiedByEffect(effect) {
  return effect === 'stage_changed' || effect === 'properties_only'
}
