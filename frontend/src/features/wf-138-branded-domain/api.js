/**
 * WF-138's own API wrapper.
 *
 * `apiRequest` from `@/lib/api` is the transport, exactly as the contract asks:
 * the shared `api` object grows no methods, so a hundred features can each talk
 * to their own `/api/wf-138` routes without anyone editing a shared file.
 *
 * The write calls go through `requestWithBody` instead, and that is a finding
 * rather than a preference, the same one WF-122 records. `apiRequest` reads the
 * error body to build a message and then throws it away, so a failure survives
 * as `status` plus one string. Two responses in this workflow need more:
 *
 *   - a 422 from the validator carries `errors`, a field-keyed map, so the brand
 *     form can put "white-label mode needs the client's name" beside the field
 *     that caused it and the token form can put "name a token, not a colour
 *     value" beside the token that caused it;
 *   - a 409 carries `reason`, a stable token, so the page can say "that host is
 *     already held by another brand" instead of printing a sentence and hoping
 *     the server did not reword it.
 *
 * The shared client would need three more fields on one function, which is a
 * platform decision. Until then the calls that need the body read it themselves,
 * and it is recorded as promotion work rather than smuggled across the boundary.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-138'

function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

/** The shared client. Fine for everything whose failure is just a failure. */
function call(path, options) {
  return apiRequest(`${BASE}${path}`, options)
}

/** As `apiRequest`, but keeps the parsed error body on the thrown error. */
async function requestWithBody(path, options) {
  const response = await fetch(`/api${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })

  if (!response.ok) {
    let body = null
    try {
      body = await response.json()
    } catch {
      // Non-JSON error body; the status line is the best we have.
    }
    const error = new Error(
      body?.detail || body?.error || `${response.status} ${response.statusText}`,
    )
    error.status = response.status
    error.code = body?.error || null
    error.errors = body?.errors || null
    error.reason = body?.reason || null
    error.body = body
    throw error
  }

  if (response.status === 204) return null
  return response.json()
}

function send(path, method, payload) {
  return requestWithBody(path, { method, body: JSON.stringify(payload ?? {}) })
}

const encode = encodeURIComponent

export const brandedDomainApi = {
  // -- the board ------------------------------------------------------------ //

  summary: () => call('/summary'),

  /**
   * The researched vocabulary and the decisions behind it.
   *
   * The page reads its token names and claim states from here rather than
   * hard-coding them, so a change to the rules cannot leave the page offering a
   * state the API does not serve.
   */
  vocabulary: () => call('/vocabulary'),

  /** What this deployment asks a customer to point at, and what it cannot do. */
  config: () => call('/config'),

  // -- accounts and brands -------------------------------------------------- //

  accounts: () => call('/accounts'),
  createAccount: (payload) => send('/accounts', 'POST', payload),
  brand: (accountId) => call(`/accounts/${encode(accountId)}/brand`),
  saveBrand: (accountId, payload) =>
    send(`/accounts/${encode(accountId)}/brand`, 'PUT', payload),

  /**
   * Replace the brand's token override.
   *
   * The body is the map itself: `{ accent: 'accent-bright' }`. A colour value is
   * refused by the server, so this call can only ever send token names.
   */
  saveTokens: (accountId, tokens) => send(`/accounts/${encode(accountId)}/tokens`, 'PUT', tokens),
  theme: (accountId) => call(`/accounts/${encode(accountId)}/theme`),
  events: (accountId) => call(`/accounts/${encode(accountId)}/events`),

  // -- hosts ---------------------------------------------------------------- //

  domains: (accountId) => call(`/accounts/${encode(accountId)}/domains`),
  verifyHost: (accountId, host) =>
    send(`/accounts/${encode(accountId)}/domains/verify`, 'POST', { host }),

  /** `force` saves a host whose CNAME has not propagated yet. */
  attachHost: (accountId, host, force = false) =>
    send(`/accounts/${encode(accountId)}/domains${query({ force: force || undefined })}`, 'POST', {
      host,
    }),

  releaseHost: (accountId, host) =>
    send(`/accounts/${encode(accountId)}/domains${query({ host })}`, 'DELETE'),
  recheckHosts: (accountId) => send(`/accounts/${encode(accountId)}/domains/recheck`, 'POST'),

  /** Write the brand's current tokens to every room of the brand. */
  propagate: (accountId) => send(`/accounts/${encode(accountId)}/propagate`, 'POST'),

  // -- rooms ---------------------------------------------------------------- //

  attachRoom: (roomId, payload) => send(`/rooms/${encode(roomId)}/brand`, 'POST', payload),
  roomTheme: (roomId) => call(`/rooms/${encode(roomId)}/theme`),

  /** Every room that has joined this brand, with the tokens it carries. */
  rooms: (accountId) => call(`/accounts/${encode(accountId)}/rooms`),

  // -- resolution ----------------------------------------------------------- //

  /** Which brand serves a Host header. The first step of the researched flow. */
  resolve: (host) => call(`/resolve${query({ host })}`),
}

/** Every room, so the page can offer one to attach to a brand. */
export const listRooms = () => apiRequest('/records/room?limit=100')

/**
 * The tokens a tenant may override, and the ones it may point at.
 *
 * Held here as a fallback only. The page reads the live list from
 * `/vocabulary` and falls back to this so a failed vocabulary call degrades to
 * a usable form rather than an empty one.
 */
export const TENANT_TOKENS = [
  'primary',
  'accent',
  'accent-bright',
  'accent-soft',
  'muted',
  'border-subtle',
  'info',
  'success',
  'warning',
  'destructive',
]

/** The words for each host state, so nothing is carried by colour alone. */
export const STATE_WORDS = {
  verified: 'Live',
  unverified: 'Awaiting DNS',
  failed: 'Check failed',
  released: 'Released',
}

export const STATE_TONES = {
  verified: 'success',
  unverified: 'warning',
  failed: 'destructive',
  released: 'neutral',
}

/** The sentence for a conflict, from the stable reason token. */
export const REASON_WORDS = {
  host_taken: 'Another brand already holds that host',
  not_ready: 'That host did not pass its checks',
  no_host: 'This brand holds no host',
}

export const REASON_NOTES = {
  host_taken: 'A verified host belongs to one brand. Release it there before claiming it here.',
  not_ready: 'Save anyway only while the CNAME is still propagating.',
  no_host: 'Add a host before re-checking or releasing one.',
}

export function reasonText(reason) {
  return REASON_WORDS[reason] || 'That change was refused'
}

export function reasonNote(reason) {
  return REASON_NOTES[reason] || ''
}

/** The words for each brand event. */
export const EVENT_WORDS = {
  brand_created: 'Brand created',
  tokens_set: 'Tokens set',
  white_labelled: 'White-label mode on',
  domain_claimed: 'Host attached',
  domain_released: 'Host released',
  room_attached: 'Room attached',
  propagated: 'Brand applied to rooms',
}

/**
 * A token map as scoped custom properties, local to the page.
 *
 * The server sends this shape already; this mirrors it so a preview can render
 * before a save round-trips. Nothing here ever produces a colour value: the
 * right-hand side is another token, so the stylesheet keeps the hex.
 */
export function tokenVariables(tokens = {}) {
  return Object.fromEntries(
    Object.entries(tokens || {})
      .filter(([key, value]) => key && value)
      .map(([key, value]) => [`--color-${key}`, `var(--color-${value})`]),
  )
}
