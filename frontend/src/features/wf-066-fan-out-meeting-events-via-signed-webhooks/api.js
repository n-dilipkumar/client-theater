/**
 * Meeting webhook fan-out API (WF-066).
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a
 * method to it is precisely the collision the feature host exists to remove. The
 * host's `apiRequest` is the escape hatch that makes that unnecessary.
 *
 * `PREFIX` is the feature's own: the backend module mounts these under
 * `/api/wf-066`, and `apiRequest` already prepends `/api`.
 *
 * Every label, every picker option and every status word on the page renders from
 * `vocabulary()` and `inferences()` rather than from a list compiled here. The
 * research names three subscription types and a payload `type` for each, and a
 * fourth added server-side has to reach every client at once - otherwise the page
 * can disagree with the validator about what is legal, and the disagreement only
 * shows up as a 422 on a form that looked right.
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

const PREFIX = '/wf-066'

export const meetingWebhookApi = {
  /**
   * The three researched event types and the payload `type` each fires, the status
   * control, the two header definitions, the signing rule, every documented
   * payload field, both deployment modes, the collection names, the outcomes and
   * the replay window with its owner.
   */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),

  /**
   * Every judgement call this workflow rests on, and how to change each one. The
   * research is specific about the signing rule and silent about the deployment,
   * the subscription identity and the retry policy, so those are served here with
   * the choice and the cost of the other one.
   */
  inferences: () => apiRequest(`${PREFIX}/inferences`),

  subscriptions: (roomId, params = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/subscriptions${query(params)}`),
  createSubscription: (roomId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/subscriptions`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  subscription: (roomId, id) => apiRequest(`${PREFIX}/rooms/${roomId}/subscriptions/${id}`),
  /**
   * Step 2's control. The researched flow is "clicks Create, then sets the row's
   * status to Enabled", so this is one patch rather than a separate enable route:
   * a reader looking at the table wants one row and one edit.
   */
  patchSubscription: (roomId, id, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/subscriptions/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  retireSubscription: (roomId, id) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/subscriptions/${id}`, { method: 'DELETE' }),

  /**
   * Step 3, which no vendor screen offers: the tenant obtains its HMAC signing
   * secret by emailing support, so this is how a tenant supplies the value
   * support issued them.
   */
  secret: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/secret`),
  setSecret: (roomId, secret) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/signing-key`, {
      method: 'POST',
      body: JSON.stringify({ secret }),
    }),

  /** The data flow: serialise once, sign, POST to every enabled subscriber. */
  emit: (roomId, eventType, meeting) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/events`, {
      method: 'POST',
      body: JSON.stringify({ event_type: eventType, meeting }),
    }),
  events: (roomId, params = {}) => apiRequest(`${PREFIX}/rooms/${roomId}/events${query(params)}`),
  event: (roomId, id) => apiRequest(`${PREFIX}/rooms/${roomId}/events/${id}`),

  /**
   * The research names no retry ladder, so this is a route a person calls rather
   * than a scheduler. `retryable` on a delivery row is advice and nothing acts on
   * it.
   */
  redeliver: (roomId, eventId) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/events/${eventId}/redeliver`, { method: 'POST' }),

  deliveries: (roomId, params = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/deliveries${query(params)}`),
  delivery: (roomId, id) => apiRequest(`${PREFIX}/rooms/${roomId}/deliveries/${id}`),

  /**
   * The exact bytes, the signing input and the signature, so a team can check
   * their subscriber before they have a real meeting to point it at. The
   * subscriber's own step 4 - recompute, compare in constant time, reject a stale
   * timestamp - cannot be tested without these.
   */
  sample: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/sample`),
  summary: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/summary`),

  rooms: () => apiRequest('/records/room?limit=100'),
}

/**
 * Why a delivery is in the state it is, in one sentence a person can act on.
 *
 * "It reached nobody" and "it reached them and they refused it" are different
 * problems with different fixes, so a page that renders both as "failed" is
 * hiding the thing the reader came for.
 */
export function deliveryNote(row) {
  if (!row) return ''
  if (row.outcome === 'skipped') {
    return row.reason === 'no_enabled_subscriptions'
      ? 'No enabled subscription wanted this event type, so nothing was sent.'
      : `Skipped: ${row.reason || 'no reason recorded'}.`
  }
  if (row.outcome === 'delivered') {
    return `The subscriber answered ${row.status}${
      row.duration_ms ? ` in ${Math.round(row.duration_ms)} ms` : ''
    }.`
  }
  if (row.status) return `The subscriber answered ${row.status}. ${row.error || ''}`.trim()
  return `The subscriber could not be reached. ${row.error || ''}`.trim()
}

/**
 * The signing rule as one line a reader can check against their own code.
 *
 * The research quotes this as the rule and records a re-serialised payload as the
 * cause of a signature mismatch, so the page shows the rule rather than a summary
 * of it: a team comparing their implementation needs the exact bytes.
 */
export function signingRuleLine(vocabulary) {
  if (!vocabulary) return ''
  const { signature, timestamp } = vocabulary.headers
  return `HMAC-SHA256(secret, "${'{timestamp}.{raw_body}'}"), hex, in ${signature} and ${timestamp}`
}

/** Whether a status word needs a tone that is not neutral. */
export function subscriptionTone(row) {
  if (row?.retired) return 'delete'
  return row?.status === 'enabled' ? 'insert' : 'neutral'
}

/** The word a badge carries, so no status is ever conveyed by colour alone. */
export function subscriptionWord(row) {
  if (row?.retired) return 'retired'
  return row?.status === 'enabled' ? 'enabled' : 'disabled'
}

/** Whether the room's deployment mode will accept the URL an admin typed. */
export function urlIsAcceptable(url, mode) {
  const text = String(url || '').trim()
  if (!text) return { ok: false, why: 'A subscriber URL is required.' }
  let parsed
  try {
    parsed = new URL(text)
  } catch {
    return { ok: false, why: 'That is not a URL the browser can parse.' }
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    return { ok: false, why: `Only http and https are accepted. This one is ${parsed.protocol}` }
  }
  if (mode !== 'saas') return { ok: true, why: '' }
  if (parsed.protocol !== 'https:') {
    return { ok: false, why: 'Cal.com SaaS accepts only HTTPS subscriber URLs.' }
  }
  const host = parsed.hostname.toLowerCase()
  if (host === 'localhost' || host.endsWith('.localhost')) {
    return { ok: false, why: 'Cal.com SaaS blocks localhost subscriber URLs.' }
  }
  return { ok: true, why: '' }
}
