/**
 * WF-080's own API wrapper.
 *
 * `apiRequest` from `@/lib/api` is the transport, exactly as the contract asks: the shared
 * `api` object grows no methods, so a hundred features can each talk to their own
 * `/api/<feature>` routes without anyone editing a shared file.
 *
 * Two calls here cannot go through it, and both are findings rather than preferences.
 *
 *   - `requestWithBody` keeps the parsed error body on the thrown error. This workflow has
 *     four responses whose body is the whole point: a 401 naming the endpoint a sandbox
 *     caller should use instead, a 409 naming the document state and saying the answer is
 *     not retryable, a 429 carrying the code `throttled` and a wait, and a 400 carrying a
 *     field-keyed `errors` map so each message lands beside the input that caused it. The
 *     shared client reads the error body to build a message and then throws that away, so
 *     a failure would survive as `status` plus one string.
 *   - `fetchSealedDownload` reads bytes. `apiRequest` parses JSON, and the sealed download
 *     answers `application/pdf`. It also answers 202 with *no body at all*, which is the
 *     shape the vendor documents, so a page that wants to know what happened has to look at
 *     the status and the `Retry-After` header rather than at a payload.
 *
 * The shared client would need three more fields on one function, which is a shared file
 * and a platform decision. Until then the calls that need those read for themselves.
 * Recorded as promotion work, not smuggled across the boundary.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-080'

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
    const error = new Error(body?.detail || body?.error || `${response.status} ${response.statusText}`)
    error.status = response.status
    error.code = body?.error || null
    error.errors = body?.errors || null
    error.state = body?.state || null
    error.retryable = body?.retryable ?? null
    error.retryAfter = body?.retry_after ?? null
    error.remedy = body?.remedy || null
    error.useInstead = body?.use_instead || null
    error.body = body
    throw error
  }

  if (response.status === 204) return null
  return response.json()
}

function send(path, method, payload) {
  return requestWithBody(path, { method, body: JSON.stringify(payload) })
}

/** As `send`, but sends extra request headers alongside the JSON body. */
function sendWithHeaders(path, method, payload, headers) {
  return requestWithBody(path, {
    method,
    headers: { 'Content-Type': 'application/json', ...headers },
    body: JSON.stringify(payload),
  })
}

/**
 * The sealed download, read as the vendor answers it.
 *
 * Three outcomes, and the caller has to be able to tell them apart:
 *
 *   - **200** with `application/pdf`. `bytes` is the payload and `digest` is what the room
 *     recorded, so a caller can check the two against each other.
 *   - **202** with a `Retry-After` header and an empty body. `retryAfter` is the number of
 *     seconds and `bytes` is empty, because the vendor returns no body and this call does
 *     not invent one.
 *   - anything else, thrown with the body attached so the route's own handler text reaches
 *     the component.
 *
 * A 202 is not an error. It is the documented back-pressure signal, and the reason this
 * function resolves rather than rejects on it is that a page which treats it as a failure
 * would teach its users that a working feature is broken.
 */
async function fetchSealedDownload(roomId, documentId) {
  const response = await fetch(
    `/api${BASE}/rooms/${encode(roomId)}/documents/${encode(documentId)}/download-protected`,
  )

  if (response.status === 202) {
    return {
      status: 202,
      outcome: 'back_pressure',
      retryAfter: Number(response.headers.get('Retry-After') || 0),
      bytes: 0,
      digest: null,
    }
  }

  if (!response.ok) {
    let body = null
    try {
      body = await response.json()
    } catch {
      // Non-JSON error body; the status line is the best we have.
    }
    const error = new Error(body?.detail || body?.error || `${response.status} ${response.statusText}`)
    error.status = response.status
    error.code = body?.error || null
    error.state = body?.state || null
    error.retryable = body?.retryable ?? null
    error.body = body
    throw error
  }

  const bytes = new Uint8Array(await response.arrayBuffer())
  return {
    status: 200,
    outcome: 'retrieved',
    retryAfter: 0,
    bytes: bytes.length,
    digest: response.headers.get('X-DSR-Digest') || null,
    mediaType: response.headers.get('Content-Type') || null,
  }
}

export const evaultApi = {
  // -- the board ----------------------------------------------------------- //

  summary: (roomId) => call(`/summary${query({ room_id: roomId })}`),

  /**
   * The researched vocabulary, so the panel cannot drift from the rules that validate it.
   * The one trigger, the dedupe header, both variants with their endpoints and their two
   * booleans, the environments, the four document states, the five fetch outcomes and the
   * throttle window all come from one place on the server.
   */
  vocabulary: () => call('/vocabulary'),

  /**
   * Every judgement call this workflow made, with the alternative it rejected: the retry
   * seconds, the throttle window, the payload shape, the shared key, the pre-completion
   * refusal and the empty 202 body.
   */
  decisions: () => call('/decisions'),

  /** What the vendor needs to configure, in one place. */
  contract: () => call('/whoami'),

  // -- subscriptions ------------------------------------------------------- //

  subscriptions: (roomId) => call(`/rooms/${encode(roomId)}/subscriptions`),

  createSubscription: (roomId, payload) =>
    send(`/rooms/${encode(roomId)}/subscriptions`, 'POST', payload),

  updateSubscription: (subscriptionId, changes) => {
    const body = {}
    for (const [key, value] of Object.entries(changes)) {
      if (value !== undefined) body[key] = value
    }
    return send(`/subscriptions/${encode(subscriptionId)}`, 'PATCH', body)
  },

  /**
   * Stop listening. The row survives: the record of what arrived under this subscription
   * is the evidence, so cancelling is a state change rather than a deletion.
   */
  cancelSubscription: (subscriptionId) =>
    requestWithBody(`/subscriptions/${encode(subscriptionId)}`, { method: 'DELETE' }),

  // -- executed documents -------------------------------------------------- //

  documents: (roomId) => call(`/rooms/${encode(roomId)}/documents`),

  createDocument: (roomId, payload) => send(`/rooms/${encode(roomId)}/documents`, 'POST', payload),

  document: (roomId, documentId) =>
    call(`/rooms/${encode(roomId)}/documents/${encode(documentId)}`),

  // -- the webhook --------------------------------------------------------- //

  /**
   * Deliver the ready event. The dedupe header goes on the request rather than in the body,
   * because the header is the mechanism the vendor documents and the body key is only a
   * fallback.
   */
  deliverReady: (roomId, vendorDocumentId, deliveryId, extra = {}) =>
    sendWithHeaders(
      `/rooms/${encode(roomId)}/events`,
      'POST',
      {
        event: PDF_READY_TRIGGER,
        data: { id: vendorDocumentId },
        ...extra,
      },
      { [DEDUPE_HEADER]: deliveryId },
    ),

  events: (roomId, documentId) =>
    call(`/rooms/${encode(roomId)}/events${query({ document_id: documentId })}`),

  // -- the two download endpoints ------------------------------------------ //

  /** The sealed retrieval. Records an attempt, so it is a write and answers JSON. */
  retrieveSealed: (roomId, documentId, payload = {}) =>
    send(`/rooms/${encode(roomId)}/documents/${encode(documentId)}/retrieve`, 'POST', payload),

  /** The plain retrieval, which is where a watermark belongs. */
  retrievePlain: (roomId, documentId, payload = {}) =>
    send(`/rooms/${encode(roomId)}/documents/${encode(documentId)}/retrieve-plain`, 'POST', payload),

  /** The mirrored vendor download. Bytes, or 202 with a `Retry-After` header and no body. */
  fetchSealed: (roomId, documentId) => fetchSealedDownload(roomId, documentId),

  attempts: (roomId, documentId) =>
    call(`/rooms/${encode(roomId)}/attempts${query({ document_id: documentId })}`),

  artifacts: (roomId, documentId) =>
    call(`/rooms/${encode(roomId)}/artifacts${query({ document_id: documentId })}`),
}

/** Every room, so the panel can offer one to govern a document in. Read from the core collection. */
export const listRooms = () => apiRequest('/records/room?limit=100')

/**
 * The ready event, as the specification names it.
 *
 * Written here rather than read from the vocabulary request because the form that creates
 * a subscription has to render before that request resolves, and a form whose trigger
 * changes under the user's hands is worse than a duplicated constant. A test asserts the
 * two agree against the served vocabulary, so this cannot quietly become a second source
 * of truth.
 */
export const PDF_READY_TRIGGER = 'document_completed_pdf_ready'

/** The dedupe header, spelled once. */
export const DEDUPE_HEADER = 'X-PandaDoc-Webhook-Event-Id'

/**
 * Fallbacks for the render before the first response lands.
 *
 * These three are close paraphrases of the server's sentences, not copies of them. The
 * server's `VARIANT_TRADEOFF` names both concrete vendor endpoints and this one does not,
 * because a sentence that arrives after the first paint is the wrong place to learn what a
 * seal is worth. Once any response has landed the page renders the server's own field, so
 * everything a reader actually reads comes from the API and the two cannot disagree.
 */
export const HONESTY_FIELDS = ['effect', 'tradeoff', 'seal_scope', 'no_polling']

/**
 * What this build can verify, quoted from the server's own field.
 *
 * "A sealed artifact is byte-stable and immutable. This room records the bytes and their
 * SHA-256 digest, so what it can prove is that the file has not changed. It does not
 * validate a certificate chain, and it does not verify a signature it cannot see." The page
 * renders this above the fold rather than in a footnote, because a page that opens with a
 * download button and no caveat is the reading the specification forbids. This is the
 * pre-response fallback; once a response has landed the page renders `seal_scope` from it.
 */
export const SEAL_SCOPE_FALLBACK =
  'A sealed artifact is byte-stable and immutable. This room records the bytes and their ' +
  'SHA-256 digest, so what it can prove is that the file has not changed. It does not ' +
  'validate a certificate chain, and it does not verify a signature it cannot see.'

/**
 * The no-polling rule.
 *
 * "This room subscribes to the ready event and does not poll." Shown on the page because a
 * reader looking at a "refresh" button reasonably expects a poller behind it, and the
 * honest answer is that there is none: the room learns the PDF exists from the webhook and
 * from nothing else.
 */
export const NO_POLLING_FALLBACK =
  'This room subscribes to the ready event and does not poll. Nothing here asks a vendor ' +
  'how a document is doing, and no schedule in this feature sleeps waiting for a PDF.'

/**
 * The trade-off between the two endpoints, quoted from the specification.
 *
 * The guide says "the `/download-protected` endpoint always returns the same digitally
 * sealed PDF file, while `/download` allows for watermark customization", so the page names
 * which of the two it is about to use rather than offering one button labelled "download".
 */
export const VARIANT_TRADEOFF_FALLBACK =
  'The sealed endpoint is byte-stable and immutable, and its bytes are the evidence. The ' +
  'plain endpoint is where a watermark goes, and it is not a sealed artifact: applying a ' +
  'watermark changes the bytes, so a watermarked copy is a derived file and not the ' +
  'executed agreement.'

/**
 * The environments, and which of them may reach the sealed endpoint.
 *
 * "Production key only - This endpoint only works with a Production key. You'll get a 401
 * Unauthorized error when trying to use a Sandbox key", and "sandbox-based integration
 * tests must use the plain download endpoint".
 */
export const ENVIRONMENTS = [
  { id: 'production', label: 'Production', sealed: true },
  { id: 'sandbox', label: 'Sandbox', sealed: false },
]

/** The four document states, with the two booleans a seller actually reads them by. */
export const DOCUMENT_STATES = [
  { id: 'awaiting_signatures', label: 'Awaiting signatures', ready: false, waiting: false },
  { id: 'generating', label: 'PDF generating', ready: false, waiting: true },
  { id: 'sealed', label: 'Sealed in the e-vault', ready: true, waiting: false },
  { id: 'failed', label: 'Generation failed', ready: false, waiting: false },
]

/** The five fetch outcomes, and whether the caller should try again. */
export const FETCH_OUTCOMES = [
  { id: 'retrieved', label: 'Retrieved', tone: 'success', retry: false },
  { id: 'back_pressure', label: 'Wait and retry', tone: 'info', retry: true },
  { id: 'duplicate', label: 'Delivery already applied', tone: 'neutral', retry: false },
  { id: 'sandbox_key_rejected', label: 'Sandbox key refused the sealed endpoint', tone: 'warning', retry: false },
  { id: 'throttled', label: 'Throttled', tone: 'warning', retry: true },
]

/** The state label for a state id, for a screen that has only the id. */
export function stateLabel(state) {
  return DOCUMENT_STATES.find((row) => row.id === state)?.label || state
}

/** The outcome label for an outcome id, for a screen that has only the id. */
export function outcomeLabel(outcome) {
  return FETCH_OUTCOMES.find((row) => row.id === outcome)?.label || outcome
}

/** Whether the sealed endpoint answers at all in this environment. */
export function sealedAllowed(environment) {
  return ENVIRONMENTS.find((row) => row.id === environment)?.sealed ?? false
}

/** Whether this document's state means a PDF is being produced right now. */
export function isBackPressure(document) {
  return document?.back_pressure === true
}

/** Whether this document has an artifact a download could serve. */
export function isReady(document) {
  return document?.artifact_ready === true
}