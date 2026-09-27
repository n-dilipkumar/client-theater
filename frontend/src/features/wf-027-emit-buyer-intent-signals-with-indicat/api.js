/**
 * Intent signals API (WF-027).
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a
 * method to it is precisely the collision the feature host exists to remove. The
 * branch this workflow was built from would have added a dozen methods there; the
 * host's `apiRequest` is the escape hatch that makes that unnecessary.
 *
 * `PREFIX` is the feature's own: the backend module mounts these under
 * `/api/wf-027`, and `apiRequest` already prepends `/api`.
 *
 * Every payload is sent exactly as the research names it - `signal_name`,
 * `data_shape`, `idempotency_key`, `occurred_at`, `email_tracked_content_id` - so
 * the documented flow can be followed against the API directly and a reader can
 * match what the page sends against what the research says should be sent.
 *
 * The room list is read from the core records route, so this page depends on the
 * HTTP contract rather than on another module's idea of the shape of a room.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-027'

/** Drop empty values so we never send `?seller=` and confuse a filter. */
function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const signalApi = {
  /**
   * The published vocabularies: urgencies, attribution values and their
   * precedence, the indicator claim grammar, and the research's own
   * specific-versus-vague indicator pair. Every picker on the page renders from
   * this rather than from a list compiled into this file.
   */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),

  /**
   * Every judgement call the workflow rests on, and how to change each one.
   *
   * The research names the fields, the three urgencies, the idempotency rule and
   * the indicator example. It does not say how a sender behaves on the edges of
   * that, so the edges are served as data rather than left for a reader to
   * reconstruct from a diff.
   */
  inferences: () => apiRequest(`${PREFIX}/inferences`),

  registrations: (params = {}) => apiRequest(`${PREFIX}/registrations${query(params)}`),
  register: (payload) =>
    apiRequest(`${PREFIX}/registrations`, { method: 'POST', body: JSON.stringify(payload) }),
  amend: (id, payload) =>
    apiRequest(`${PREFIX}/registrations/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  withdraw: (id) => apiRequest(`${PREFIX}/registrations/${id}`, { method: 'DELETE' }),

  summary: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/summary`),
  signals: (roomId, params = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/signals${query(params)}`),
  emit: (roomId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/signals`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  signal: (roomId, signalId, params = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/signals/${signalId}${query(params)}`),

  /**
   * The classified route: send the raw interaction and its measurements, and the
   * server decides which registered indicators it satisfies and why. Preferred
   * over `emit` on the page, because it is the researched flow and because it
   * reports a non-qualifying interaction rather than refusing it.
   */
  interact: (roomId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/interactions`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  liveFeed: (roomId, params = {}) => apiRequest(`${PREFIX}/rooms/${roomId}/live-feed${query(params)}`),

  rooms: () => apiRequest('/records/room?limit=100'),
}

/**
 * A version 4 UUID for `idempotency_key`.
 *
 * The research specifies it as a UUID4 in both the registration and the emission
 * field lists, and the server checks the version nibble rather than just the
 * shape, so anything else is refused. `crypto.randomUUID` is exactly that when
 * the browser has it; the fallback derives one from `crypto.getRandomValues`,
 * which is still random rather than derived from the clock, so two tabs emitting
 * the same interaction at the same moment do not collide.
 */
export function newIdempotencyKey() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  const bytes = new Uint8Array(16)
  crypto.getRandomValues(bytes)
  bytes[6] = (bytes[6] & 0x0f) | 0x40 // version 4
  bytes[8] = (bytes[8] & 0x3f) | 0x80 // RFC 4122 variant
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

/** The rendered sentence, or a short reason it has none. */
export function signalText(signal) {
  const rendered = signal?.rendered
  if (!rendered) return 'Not rendered.'
  if (rendered.text) return rendered.text
  const first = rendered.indicators?.find((row) => row.text)
  if (first) return first.text
  return 'No sentence: the registration has no description this locale can render.'
}

/**
 * Whether an indicator's claim was actually checked against its own evidence.
 *
 * The research says an indicator "should have high value" and should "drive a
 * seller to act", and its only worked example is a key carrying a bound. An
 * indicator that states no bound cannot be checked, so it qualifies on trust -
 * and the page says so rather than presenting it as a verified claim.
 */
export function isChecked(qualification) {
  return Boolean(qualification?.checked)
}
