/**
 * WF-082's own API wrapper.
 *
 * `apiRequest` from `@/lib/api` is the transport, exactly as the contract asks: the shared
 * `api` object grows no methods, so a hundred features can each talk to their own
 * `/api/<feature>` routes without anyone editing a shared file.
 *
 * One call here cannot go through it, and it is a finding rather than a preference.
 *
 * `deliver` posts a real `multipart/form-data` body with a signed `Content-Sha256` header.
 * `apiRequest` always sends `Content-Type: application/json` and stringifies its body, so it
 * cannot produce a request shaped the way the provider produces one. The specification is
 * explicit that the request is `multipart/form-data` with the payload in a part named `json`,
 * so the page has to be able to fire a delivery that looks like a real one rather than a JSON
 * approximation of one. A demo that cannot reproduce the shape it is documenting teaches the
 * wrong thing.
 *
 * That call reads its own errors and keeps the parsed body, because a refusal in this workflow
 * *is* the answer: `error_name`, `http_status`, `remediation` and `retryable` come from the
 * machine-readable catalogue the specification asks for, and `apiRequest` throws all of that
 * away in favour of one string.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf082'

function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

const encode = encodeURIComponent

/** The shared client. Fine for everything whose failure is just a failure. */
function call(path) {
  return apiRequest(`${BASE}${path}`)
}

function send(path, method, payload) {
  return apiRequest(`${BASE}${path}`, {
    method,
    body: JSON.stringify(payload),
  })
}

/**
 * Build the `multipart/form-data` body the provider sends, and the digest over it.
 *
 * The digest covers the **part's** bytes and not the multipart envelope around them, because
 * the specification says `echo -n $json | openssl dgst -sha256 -hmac $apiKey` and `$json` is
 * the payload. Signing the envelope is the mistake this function exists to make impossible: it
 * produces a well-formed base64 header over the wrong bytes, and every delivery is then refused
 * at the second check with a message about a proxy.
 *
 * The HMAC over the event is computed here too, in the browser, so the page can fire a
 * genuinely signed delivery rather than asking the server to sign one. The key never leaves the
 * input that registered it, and the page says so: this is a demonstration of the provider's
 * scheme, not a way to see a stored secret.
 */
export function buildDelivery({ apiKey, eventId, eventType, eventTime }) {
  // Web Crypto's subtle API is asynchronous, so this is a promise rather than a value.
  return (async () => {
    const eventHash = await hmacHex(apiKey, `${eventTime}${eventType}`)
    const payload = {
      event: {
        event_time: eventTime,
        event_type: eventType,
        event_id: eventId,
        event_hash: eventHash,
        event_metadata: {
          related_signature_id: 'sr_demo_001',
          reported_for_account_id: 'acct_demo',
          reported_for_app_id: 'app_demo',
        },
      },
      signature_request: { id: 'sr_demo_001' },
    }
    const part = JSON.stringify(payload)
    const bytes = new TextEncoder().encode(part)
    const contentSha256 = await hmacBase64(apiKey, bytes)
    return { part: bytes, contentSha256, eventHash }
  })()
}

async function hmacKey(apiKey) {
  return crypto.subtle.importKey(
    'raw',
    new TextEncoder().encode(apiKey),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign'],
  )
}

async function hmacBase64(apiKey, bytes) {
  const key = await hmacKey(apiKey)
  const signature = await crypto.subtle.sign('HMAC', key, bytes)
  return btoa(String.fromCharCode(...new Uint8Array(signature)))
}

async function hmacHex(apiKey, text) {
  const key = await hmacKey(apiKey)
  const signature = await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(text))
  return Array.from(new Uint8Array(signature))
    .map((byte) => byte.toString(16).padStart(2, '0'))
    .join('')
}

/**
 * Fire one delivery at the callback, shaped the way the provider shapes it.
 *
 * A refusal resolves rather than rejects only when the server answered in a shape this can
 * read. Everything else throws with the catalogue entry attached, because a delivery page that
 * reported an IP refusal as "request failed" has thrown away the only actionable part of the
 * answer.
 */
async function deliver(roomId, { apiKey, eventId, eventType, eventTime }, params = {}) {
  const { part, contentSha256 } = await buildDelivery({ apiKey, eventId, eventType, eventTime })

  const boundary = '----dsrwf082boundary'
  const body =
    `--${boundary}\r\n` +
    `Content-Disposition: form-data; name="json"\r\n` +
    'Content-Type: application/json\r\n' +
    '\r\n'
  const envelope = `${body}${new TextDecoder().decode(part)}\r\n--${boundary}--\r\n`

  const response = await fetch(`/api${BASE}/rooms/${encode(roomId)}/events${query(params)}`, {
    method: 'POST',
    headers: {
      'Content-Type': `multipart/form-data; boundary=${boundary}`,
      'Content-Sha256': contentSha256,
    },
    body: new TextEncoder().encode(envelope),
  })

  let payload = null
  try {
    payload = await response.json()
  } catch {
    // A non-JSON body. The status line is all there is.
  }

  if (!response.ok) {
    const error = new Error(payload?.catalogue?.cause || payload?.error || `HTTP ${response.status}`)
    error.status = response.status
    error.code = payload?.error_name || payload?.error || null
    error.failedCheck = payload?.failed_check || null
    error.catalogue = payload?.catalogue || null
    error.checks = payload?.checks || []
    error.body = payload
    throw error
  }
  return payload
}

export const webhookApi = {
  // -- the board ----------------------------------------------------------- //

  summary: (roomId) => call(`/summary${query({ room_id: roomId })}`),

  /**
   * Every researched constant, served as data: the headers, the part name, the magic string,
   * the three checks in order, the two digests and how they differ, the retry ladder and the
   * error catalogue. The page renders its labels from this rather than from literals, so the
   * validator and the page cannot disagree about what is legal.
   */
  vocabulary: () => call('/vocabulary'),

  /** The retry ladder, the thirty second timeout and the self-disable threshold, as data. */
  retryPolicy: () => call('/retry-policy'),

  /** Every judgement call this workflow made, with the option it rejected. */
  decisions: () => call('/decisions'),

  decision: (decisionId) => call(`/decisions/${encode(decisionId)}`),

  /** What the provider needs to configure, in one place. */
  contract: () => call('/whoami'),

  /** The machine-readable error catalogue, keyed by error_name. */
  errorCodes: () => call('/error-codes'),

  // -- registrations ------------------------------------------------------- //

  callbacks: (roomId) => call(`/rooms/${encode(roomId)}/callbacks`),

  createCallback: (roomId, payload) => send(`/rooms/${encode(roomId)}/callbacks`, 'POST', payload),

  callback: (callbackId) => call(`/callbacks/${encode(callbackId)}`),

  updateCallback: (callbackId, payload) => send(`/callbacks/${encode(callbackId)}`, 'PATCH', payload),

  // -- the allowlist ------------------------------------------------------- //

  ranges: (roomId) => call(`/rooms/${encode(roomId)}/ranges`),

  refreshRanges: (roomId, payload) => send(`/rooms/${encode(roomId)}/ranges`, 'POST', payload),

  // -- deliveries ---------------------------------------------------------- //

  /** Fire one signed delivery. See the note above: this cannot go through `apiRequest`. */
  deliver: (roomId, delivery, params) => deliver(roomId, delivery, params),

  deliveries: (roomId, params) => call(`/rooms/${encode(roomId)}/deliveries${query(params)}`),

  delivery: (roomId, deliveryId) =>
    call(`/rooms/${encode(roomId)}/deliveries/${encode(deliveryId)}`),
}

/** Every room, so the panel can offer one to verify deliveries for. */
export const listRooms = () => apiRequest('/records/room?limit=100')

/**
 * The magic string, spelled once for the render before the vocabulary resolves.
 *
 * A close paraphrase rather than a copy of the server's constant. Once any response has landed
 * the page renders the server's own value, so the two cannot disagree; this only covers the
 * first paint. A test asserts the two agree against the served vocabulary.
 */
export const ACKNOWLEDGEMENT_FALLBACK = 'Hello API Event Received'

/**
 * The scope sentence, as a pre-response fallback.
 *
 * "This handler checks the source address it was given and the two digests the request carried.
 * It cannot prove the request was not replayed from a captured body." A verification page that
 * opens with a green tick and no caveat is the reading the specification forbids, so the scope
 * is rendered above the fold and the server sends the same sentence with every response.
 */
export const SCOPE_FALLBACK =
  'This handler checks the source address it was given and the two digests the request ' +
  'carried. It cannot prove the request was not replayed from a captured body, because that ' +
  'would need a nonce the provider does not send.'

/**
 * The two event types, and the warning the specification attaches to them.
 *
 * "Final document generation lags signing. If you plan to download the final files, wait for
 * `signature_request_downloadable`." Download on the second, never on the first.
 */
export const EVENT_TYPES = [
  {
    id: 'signature_request_all_signed',
    label: 'All signed',
    downloads: false,
    note: 'Signing is complete. Final document generation lags signing.',
  },
  {
    id: 'signature_request_downloadable',
    label: 'Downloadable',
    downloads: true,
    note: 'Final files are ready. Download on this event, not on the all-signed one.',
  },
]

/** The three delivery states, and what each one means for the provider's retry. */
export const DELIVERY_STATES = [
  {
    id: 'verified',
    label: 'Verified',
    tone: 'success',
    retry: false,
    note: 'All three checks passed. The handler acted and answered the magic string.',
  },
  {
    id: 'duplicate',
    label: 'Duplicate retry',
    tone: 'neutral',
    retry: false,
    note: 'The event id was already recorded. Answered with the magic string, not refused.',
  },
  {
    id: 'rejected',
    label: 'Refused',
    tone: 'destructive',
    retry: true,
    note: 'One check failed. The provider reads this as a failed callback and retries.',
  },
]

/** The state label for a state id, for a screen that has only the id. */
export function stateLabel(state) {
  return DELIVERY_STATES.find((row) => row.id === state)?.label || state
}

/** The state row for a state id, for a screen that needs the whole entry. */
export function stateRow(state) {
  return DELIVERY_STATES.find((row) => row.id === state) || DELIVERY_STATES[0]
}

/** Whether an event type is the one to download final files on. */
export function isDownloadable(eventType) {
  return EVENT_TYPES.find((row) => row.id === eventType)?.downloads === true
}