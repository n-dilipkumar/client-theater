/**
 * Page outreach API (WF-106).
 *
 * These calls live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a method
 * to it is precisely the collision the feature host exists to remove.
 *
 * `PREFIX` is this feature's own: the backend module mounts these under `/api/wf-106`,
 * and `apiRequest` already prepends `/api`. Nothing here calls a route outside that
 * prefix, apart from the core room list, and a test in `PageOutreach.test.jsx` asserts
 * it.
 *
 * Room is a query parameter rather than a path segment. The research's blocks are
 * room-scoped, and a client that already holds a room list gets the room it cares about
 * from a picker, so a path segment would only make the URL longer.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-106'

/** Drop empty values, so we never send `?state=` and confuse a filter. */
function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const outreachApi = {
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),
  inferences: () => apiRequest(`${PREFIX}/inferences`),
  summary: (roomId) => apiRequest(`${PREFIX}/summary${query({ room_id: roomId })}`),

  workflows: (roomId) => apiRequest(`${PREFIX}/workflows${query({ room_id: roomId })}`),
  workflow: (id) => apiRequest(`${PREFIX}/workflows/${encodeURIComponent(id)}`),
  createWorkflow: (payload, roomId) =>
    apiRequest(`${PREFIX}/workflows${query({ room_id: roomId })}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  replaceWorkflow: (id, payload) =>
    apiRequest(`${PREFIX}/workflows/${encodeURIComponent(id)}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  setState: (id, state) =>
    apiRequest(`${PREFIX}/workflows/${encodeURIComponent(id)}/state`, {
      method: 'POST',
      body: JSON.stringify({ state }),
    }),
  deleteWorkflow: (id) =>
    apiRequest(`${PREFIX}/workflows/${encodeURIComponent(id)}`, { method: 'DELETE' }),

  evaluate: (payload) =>
    apiRequest(`${PREFIX}/views/evaluate`, { method: 'POST', body: JSON.stringify(payload) }),
  recordView: (payload, roomId) =>
    apiRequest(`${PREFIX}/views${query({ room_id: roomId })}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  views: (params = {}) => apiRequest(`${PREFIX}/views${query(params)}`),

  deliveries: (params = {}) => apiRequest(`${PREFIX}/deliveries${query(params)}`),
  delivery: (id) => apiRequest(`${PREFIX}/deliveries/${encodeURIComponent(id)}`),

  receipts: (params = {}) => apiRequest(`${PREFIX}/receipts${query(params)}`),
  recordInteraction: (deliveryId, payload) =>
    apiRequest(`${PREFIX}/deliveries/${encodeURIComponent(deliveryId)}/interactions`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  prospects: (roomId) => apiRequest(`${PREFIX}/prospects${query({ room_id: roomId })}`),

  /** The core room list. The page is handed no props, so it fetches its own rooms. */
  rooms: () => apiRequest('/records/room?limit=100'),
}

/**
 * Turn a domain refusal into something a seller can act on.
 *
 * Kept here so the page never pattern-matches a raw detail string. Each rule names the
 * mistake the caller can fix, and anything unrecognised is shown verbatim rather than
 * swallowed.
 */
export function refusalMessage(error) {
  const text = String(error?.message || error)
  if (error?.status === 409 && /already exists in this room/i.test(text)) {
    return 'A workflow with that name already exists in this room. Two live workflows with one name would show the same buyer the same block twice.'
  }
  if (error?.status === 404 && /no workflow/i.test(text)) {
    return 'That workflow no longer exists. It may have been retired; the views recorded while it was live are still in the list.'
  }
  if (/implements/i.test(text)) {
    return 'This workflow reads url, dwell, utm_source and utm_campaign rules only. A rule on any other kind can never fire, so it is refused rather than stored.'
  }
  if (/carries field/i.test(text)) {
    return 'That page view carries a field no rule reads on. Remove it and send only path, dwell_seconds, utm_source and utm_campaign.'
  }
  if (/carries key/i.test(text)) {
    return 'That pane carries a key nothing reads. The audience pane holds company_keys, tags and segments.'
  }
  if (/needs text/i.test(text)) {
    return 'Every block needs text. A block with no words in it reaches a buyer as an empty box.'
  }
  if (/cannot both be right/i.test(text)) {
    return 'Two paths answer to the same key. The buyer answered one question, so two branches cannot both be right.'
  }
  if (/no outbound transport/i.test(text)) {
    return 'Only an in-app block is available. The research names the Messenger as the surface and this product has no outbound transport, so a channel that could not be delivered is refused rather than stored.'
  }
  if (/path_key must be one of/i.test(text)) {
    return 'That path selection names a branch the workflow does not declare. The researched flow branches on the buyer’s answer, so the receipt would point at nothing.'
  }
  return text
}

/** A short label for one workflow in the list. */
export function workflowLabel(row) {
  return row.name || row.id || 'Unnamed workflow'
}

/**
 * One rule as a phrase a seller reads at a glance.
 *
 * The expected value is quoted, not interpolated bare, so a rule on a value with a
 * space in it reads the same on the page as it does in the record.
 */
export function ruleSentence(rule = {}) {
  const kind = String(rule.kind || '')
  const value = String(rule.expected || rule.value || '')
  const mode = String(rule.mode || '')
  if (kind === 'url') {
    if (mode === 'exact') return `the path is exactly ${value}`
    if (mode === 'contains') return `the path contains ${value}`
    return `the path starts with ${value}, at a slash boundary`
  }
  if (kind === 'dwell') return `the buyer spent ${value} seconds or more on the page`
  if (kind === 'utm_source') return `the visit came from utm_source ${value}`
  if (kind === 'utm_campaign') return `the visit came from utm_campaign ${value}`
  return `${kind || 'an unknown rule'} on ${value}`
}

/** How far a prospect has got, as one sentence. */
export function progressSentence(prospect = {}) {
  if (prospect.engaged) return 'Engaged: chose a branch, or the goal fired'
  if (prospect.hidden_for_session) return 'Dismissed or Messenger opened in that session'
  if (prospect.deliveries) return 'Shown the block, nothing recorded against it yet'
  return 'Browsed a targeted page and was not shown the block'
}
