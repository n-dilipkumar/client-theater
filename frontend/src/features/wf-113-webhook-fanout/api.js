/**
 * Webhook ingest and fan-out API (WF-113).
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a
 * method to it is precisely the collision the feature host exists to remove.
 *
 * `PREFIX` is the feature's own: the backend module mounts under `/api/wf-113`,
 * and `apiRequest` already prepends `/api`.
 *
 * Every label, every picker option and every status word on the page renders
 * from `vocabulary()` and `inferences()` rather than from a list compiled here,
 * so a value added server-side reaches every client at once and the page cannot
 * disagree with the validator about what is legal.
 */

import { apiRequest } from '@/lib/api'

/** Drop empty values so we never send `?state=` and confuse a filter. */
function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

const PREFIX = '/wf-113'

/**
 * The representative payload the user flow says to send once, so the room
 * "receives representative data" before the automation is started. It carries an
 * id (which resolves to an identifier), a nested customer object (which a
 * mapping can read as trigger data), and a three-member group for a Batch
 * Update.
 */
export const SAMPLE_ORDER = {
  id: 'ORD-10482',
  event: 'order.placed',
  placed_at: '2026-03-04T11:42:00Z',
  customer: { email: 'priya.raman@northwind.example', name: 'Priya Raman' },
  group: [{ id: 'seat-1' }, { id: 'seat-2' }, { id: 'seat-3' }],
}

export const webhookIngestApi = {
  /** Every researched term: the actions, the value types, the batch ceiling, the rate limits. */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),
  /** Every judgement call this workflow rests on, and how to change it. */
  inferences: () => apiRequest(`${PREFIX}/inferences`),
  /** The quoted request limits, published and enforced by nobody. */
  rateLimits: () => apiRequest(`${PREFIX}/rate-limits`),

  summary: (roomId) => apiRequest(`${PREFIX}/summary${query({ room_id: roomId })}`),

  automations: (roomId) => apiRequest(`${PREFIX}/automations${query({ room_id: roomId })}`),
  automation: (id) => apiRequest(`${PREFIX}/automations/${id}`),
  createAutomation: (roomId, payload) =>
    apiRequest(`${PREFIX}/automations${query({ room_id: roomId })}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  patchAutomation: (id, payload) =>
    apiRequest(`${PREFIX}/automations/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  /** Step 6 of the researched flow: Review items, then Start Automation. */
  startAutomation: (id) => apiRequest(`${PREFIX}/automations/${id}/start`, { method: 'POST' }),

  /**
   * The generated webhook URL. In the product an external service calls this, not
   * the page; the page calls it once so the room holds representative data,
   * which is exactly what step 2 of the flow offers as an alternative to giving
   * the URL away.
   */
  deliverToHook: (token, sample) =>
    apiRequest(`${PREFIX}/hooks/${token}`, { method: 'POST', body: JSON.stringify(sample) }),

  triggers: (roomId, params = {}) =>
    apiRequest(`${PREFIX}/triggers${query({ room_id: roomId, ...params })}`),
  events: (roomId, params = {}) =>
    apiRequest(`${PREFIX}/events${query({ room_id: roomId, ...params })}`),
  deliveries: (roomId, params = {}) =>
    apiRequest(`${PREFIX}/deliveries${query({ room_id: roomId, ...params })}`),

  rooms: () => apiRequest('/records/room?limit=100'),
}

/** The word an automation's badge carries, so no status is by colour alone. */
export function automationWord(row) {
  return row?.status === 'started' ? 'started' : 'draft'
}

/** The tone an automation's badge carries. Draft is neutral, started is live. */
export function automationTone(row) {
  return row?.status === 'started' ? 'insert' : 'neutral'
}

/** The word a trigger's badge carries. */
export function triggerWord(row) {
  if (row?.state === 'duplicate') return 'duplicate'
  if (row?.state === 'rejected') return 'rejected'
  return 'accepted'
}

/** The tone a trigger's badge carries. */
export function triggerTone(row) {
  if (row?.state === 'duplicate') return 'restore'
  if (row?.state === 'rejected') return 'delete'
  return 'insert'
}

/**
 * Why a delivery is in the outcome it is, in one sentence a person can act on.
 *
 * "The Slack block had no channel" and "the event had no name to send" are
 * different problems with different fixes, so a page that rendered both as
 * "failed" or "skipped" would be hiding the thing the reader came for.
 */
export function deliveryNote(row) {
  if (!row) return ''
  const action = row.action ? row.action.replace(/_/g, ' ') : 'the action'
  if (row.outcome === 'delivered') {
    return row.count && row.count > 1
      ? `${action} delivered ${row.count} events.`
      : `${action} delivered.`
  }
  if (row.outcome === 'skipped') {
    const reasons = {
      no_identifier: 'the payload carried no identifier to match a person on.',
      no_batch: 'the payload carried no group for a Batch Update.',
      no_channel: 'the Slack block has no channel to post to.',
      no_target: 'the Send data action has no target URL.',
    }
    return `${action} skipped because ${reasons[row.reason] || row.reason || 'no reason was recorded'}.`
  }
  if (row.reason === 'event_name_missing') {
    return `${action} failed because the event had no name.`
  }
  return `${action} failed: ${row.reason || 'no reason recorded'}.`
}

/** The delivery outcome as a word, never a colour alone. */
export function deliveryWord(row) {
  return row?.outcome || 'unknown'
}

/** Whether a delivery row is a problem that needs a change, or a normal state. */
export function deliveryTone(row) {
  if (row?.outcome === 'delivered') return 'insert'
  if (row?.outcome === 'failed') return 'delete'
  return 'restore'
}

/**
 * Whether the generated URL can be exercised from this page.
 *
 * A secret-bearing automation needs a signed delivery, and signing is the
 * sending service's job, not the browser's. The button is disabled rather than
 * present-and-failing, and the reason is a sentence on the page.
 */
export function canSendSample(row) {
  return Boolean(row?.token) && row?.has_secret !== true
}

/** A short, copyable form of a fingerprint, so a table row stays readable. */
export function shortHash(value) {
  const text = String(value || '')
  return text.length > 12 ? `${text.slice(0, 12)}...` : text
}

/** The one signing rule, as a line a reader can check against their own code. */
export function signingRuleLine(vocabulary) {
  const signing = vocabulary?.signing
  if (!signing) return ''
  return `${signing.rule}, in ${signing.signature_header} and ${signing.timestamp_header}`
}
