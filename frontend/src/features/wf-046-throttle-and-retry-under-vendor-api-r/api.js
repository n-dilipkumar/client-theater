/**
 * Quota and throttling API (WF-046).
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a
 * method to it is precisely the collision the feature host exists to remove. The
 * host's `apiRequest` is the escape hatch that makes that unnecessary.
 *
 * `PREFIX` is the feature's own: the backend module mounts these under
 * `/api/wf-046`, and `apiRequest` already prepends `/api`.
 *
 * Every vendor answer is *handed in* rather than fetched. The room holds no
 * vendor credential, so `observe` is the seam where a connector reports what the
 * vendor said - status, error code, headers - and the room decides what to do
 * about it. A quota reading costs the vendor nothing extra to take, which is what
 * makes this feature possible at all.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-046'

/** Drop empty values so we never send `?state=` and confuse a filter. */
function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const throttleApi = {
  /**
   * The five researched steps, the batch states, the throttle signals with their
   * quotes, the wait ladder, and the per-vendor policies. Every picker and every
   * label on the page renders from this rather than from a list compiled here.
   */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),

  /**
   * Every judgement call this workflow rests on, and how to change each one. The
   * research names no backoff base, no jitter seed and no cap on `Retry-After`,
   * so each of those is served here with the choice and the cost of the other one.
   */
  inferences: () => apiRequest(`${PREFIX}/inferences`),

  /** The registered per-vendor rate-limit policies, and which can pre-empt. */
  policies: () => apiRequest(`${PREFIX}/policies`),

  /** The throttle table itself, one row per vendor answer, with its quote. */
  signals: () => apiRequest(`${PREFIX}/signals`),

  /** The wait ladder with every rung. */
  backoff: () => apiRequest(`${PREFIX}/backoff`),

  connections: (params = {}) => apiRequest(`${PREFIX}/connections${query(params)}`),
  createConnection: (payload) =>
    apiRequest(`${PREFIX}/connections`, { method: 'POST', body: JSON.stringify(payload) }),
  connection: (id) => apiRequest(`${PREFIX}/connections/${id}`),
  patchConnection: (id, payload) =>
    apiRequest(`${PREFIX}/connections/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),

  /**
   * The researched step 5 control. A pause stops the sending; it does not stop the
   * connection from answering, so the person who pressed it can see what is left.
   */
  pause: (id, payload = {}) =>
    apiRequest(`${PREFIX}/connections/${id}/pause`, { method: 'POST', body: JSON.stringify(payload) }),
  resume: (id) => apiRequest(`${PREFIX}/connections/${id}/resume`, { method: 'POST' }),

  batches: (roomId, params = {}) => apiRequest(`${PREFIX}/rooms/${roomId}/batches${query(params)}`),
  submit: (roomId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/batches`, { method: 'POST', body: JSON.stringify(payload) }),
  batch: (roomId, batchId) => apiRequest(`${PREFIX}/rooms/${roomId}/batches/${batchId}`),

  /**
   * Steps 2 and 3. Hand the vendor's answer in and the decision comes back:
   * the quota headers are written to the room's meter, the tokens are spent, and
   * a throttle defers the batch under the wait the vendor or the ladder asks for.
   */
  observe: (roomId, batchId, answer) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/batches/${batchId}/observe`, {
      method: 'POST',
      body: JSON.stringify(answer),
    }),

  /**
   * The researched promise, as a callable route: the keys on the batch are reused
   * and never regenerated, so a retry cannot duplicate a CRM row. `force` sends
   * before the scheduled time.
   */
  retry: (roomId, batchId, payload = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/batches/${batchId}/retry`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /** The queue worker's route. Retries only what is due. */
  drain: (roomId, payload = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/drain`, { method: 'POST', body: JSON.stringify(payload) }),

  /** What each connection last reported, and what is left of it. */
  quota: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/quota`),
  throttleLog: (roomId, params = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/throttle-log${query(params)}`),
  summary: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/summary`),

  rooms: () => apiRequest('/records/room?limit=100'),
}

/** One line saying why a batch is where it is, in the record's own words. */
export function decisionNote(batch) {
  if (!batch) return ''
  if (batch.state === 'complete') return 'The vendor accepted every row.'
  if (batch.state === 'needs_action') {
    return batch.signal
      ? 'The room will not retry this on its own. A person decides.'
      : 'The vendor answered with something the room does not recognise as a throttle. A person decides.'
  }
  if (batch.decision_reason === 'paused') return 'This connection is paused by hand, so nothing was sent.'
  if (batch.decision_reason === 'empty') return 'The token bucket was empty, so nothing was sent.'
  if (batch.decision_reason === 'unknown_capacity') {
    return 'This vendor publishes no burst limit, so the room cannot refuse a call before it is sent.'
  }
  return batch.decision_detail || ''
}

/** The vendor's remaining counts, as the bar wants them. */
export function budgetOf(meter) {
  if (!meter) return { daily: null, window: null }
  return {
    daily:
      typeof meter.daily_remaining === 'number'
        ? { remaining: meter.daily_remaining, total: meter.daily_total }
        : null,
    window:
      typeof meter.window_remaining === 'number'
        ? { remaining: meter.window_remaining, total: meter.window_total }
        : null,
  }
}

/** Whether a connection can refuse a call before it is sent. */
export function isPreemptive(connection) {
  return Boolean(connection?.effective?.preemptive)
}