/**
 * WF-078's own API wrapper.
 *
 * `apiRequest` from `@/lib/api` is the transport, exactly as the contract asks: the shared
 * `api` object grows no methods, so a hundred features can each talk to their own
 * `/api/<feature>` routes without anyone editing a shared file.
 *
 * The write calls go through `requestWithBody` instead, and that is a finding rather than
 * a preference. `apiRequest` reads the error body to build a message and then throws it
 * away, so a failure survives as `status` plus one string. This workflow has two responses
 * whose body is the whole point:
 *
 *   - a 400 from the settings validator carries `errors`, a field-keyed map, so each
 *     message lands beside the input that caused it;
 *   - a 403 from the withheld body carries `place`, `method` and `withheld_reason`, which
 *     is what lets a recipient screen say which check is outstanding instead of showing a
 *     bare refusal.
 *
 * The shared client would need three more fields on one function, which is a shared file
 * and a platform decision. Until then the calls that need the body read it themselves.
 * Recorded as promotion work, not smuggled across the boundary.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-078'

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
    const error = new Error(body?.detail || body?.error || `${response.status} ${response.statusText}`)
    error.status = response.status
    error.code = body?.error || null
    error.errors = body?.errors || null
    // The withheld-body fields. A caller that cannot name the gate it is being asked for
    // cannot tell a recipient what to do, so these three reach the component.
    error.place = body?.place || null
    error.method = body?.method || null
    error.withheldReason = body?.withheld_reason || null
    error.body = body
    throw error
  }

  if (response.status === 204) return null
  return response.json()
}

function send(path, method, payload) {
  return requestWithBody(path, { method, body: JSON.stringify(payload) })
}

const encode = encodeURIComponent

export const verificationApi = {
  // -- the board ----------------------------------------------------------- //

  summary: (roomId) => call(`/summary${query({ room_id: roomId })}`),

  /**
   * The researched vocabulary, so the panel cannot drift from the rules that validate it.
   * Both gates with their sourced audiences, all four methods with the vendor's own field
   * names, the passcode bound, the E.164 bound, the three SMS states and the honesty
   * sentences all come from one place on the server.
   */
  vocabulary: () => call('/vocabulary'),

  /**
   * Every judgement call this workflow made, with the alternative it rejected, and the
   * three data sources the specification itself marked inferred.
   */
  decisions: () => call('/decisions'),
  assumptions: () => call('/assumptions'),

  // -- documents and their recipients -------------------------------------- //

  documents: (roomId) => call(`/documents${query({ room_id: roomId })}`),

  createDocument: (roomId, payload) => send(`/rooms/${encode(roomId)}/documents`, 'POST', payload),

  document: (documentId) => call(`/documents/${encode(documentId)}`),

  createRecipient: (documentId, payload) =>
    send(`/documents/${encode(documentId)}/recipients`, 'POST', payload),

  recipients: (roomId, documentId) => call(`/recipients${query({ room_id: roomId, document_id: documentId })}`),

  /**
   * Tri-state per gate, and the difference matters: omit a gate to leave it alone, send a
   * gate to replace what is stored for that gate, and send `null` to remove it. That is
   * what lets a recipient gain a second gate at a different moment without the first one
   * moving, which is the specification's own two-axis sentence.
   */
  updateRecipient: (recipientId, changes) => {
    const body = {}
    for (const [key, value] of Object.entries(changes)) {
      if (value !== undefined) body[key] = value
    }
    return send(`/recipients/${encode(recipientId)}`, 'PATCH', body)
  },

  /**
   * What the recipient is asked for at one gate, and never the answer. The questions come
   * back for a knowledge-based gate because a recipient cannot answer a question they
   * cannot see; the expected answers, the passcode and the one-time code do not.
   */
  prompt: (recipientId, place) => call(`/recipients/${encode(recipientId)}/prompt${query({ place })}`),

  /**
   * Issue a one-time code. A resend is allowed - the evidence says a signer "can request
   * the code to be sent again if needed" - and it supersedes the previous one, so at most
   * one code is ever valid. The code is never in the response.
   */
  sendCode: (recipientId) => send(`/recipients/${encode(recipientId)}/codes`, 'POST', {}),

  // -- attempts ------------------------------------------------------------ //

  /**
   * Run one attempt. A rejected attempt is a 201 with `outcome: "fail"` and a named
   * reason, not a 4xx: the attempt happened, and a rejection is exactly the row the
   * specification requires to be visible.
   */
  attempt: (recipientId, place, evidence) =>
    send(`/recipients/${encode(recipientId)}/attempts${query({ place })}`, 'POST', evidence),

  attempts: (roomId, recipientId, documentId) =>
    call(`/attempts${query({ room_id: roomId, recipient_id: recipientId, document_id: documentId })}`),

  // -- the withheld body --------------------------------------------------- //

  /**
   * The body, once the gate at `place` has cleared. 403 with the gate named when it has
   * not. `error.place` and `error.method` reach the component so a recipient screen can say
   * what is outstanding.
   */
  body: (documentId, recipientId, place) =>
    call(`/documents/${encode(documentId)}/body${query({ recipient_id: recipientId, place })}`),

  /**
   * Whether the body would be released right now, without delivering it. A page needs to
   * say "withheld until you verify" without handing over the document to say it.
   */
  bodyState: (documentId, recipientId, place) =>
    call(`/documents/${encode(documentId)}/body-state${query({ recipient_id: recipientId, place })}`),
}

/** Every room, so the panel can offer one to govern a document in. Read from the core collection. */
export const listRooms = () => apiRequest('/records/room?limit=100')

/**
 * The word this workflow is measured by.
 *
 * The limitation says verification "does not prove who is holding the phone, and the
 * knowledge-based and ID checks compare what a recipient typed against what a sender
 * recorded. Neither one is a background check". Exported rather than written into the page
 * because a test imports it, and a test that imports a literal checks nothing. The server
 * sends the same sentence with every response, so the page and the API cannot disagree.
 */
export const NOT_PROOF =
  'A pass records that the right answer was given, not that the person who gave it is who the sender meant.'

/**
 * Who owns the authentication decision.
 *
 * Quoted from the vendor's own guidance, which the specification reproduces: "you are
 * solely responsible for making sure that your signer/end user authentication process is
 * sufficient and complies with any and all applicable laws and regulations." The room owns
 * this, and nothing in this feature delegates it to a provider by default.
 */
export const AUTHENTICATION_OWNER =
  'This room owns the authentication decision. The vendor’s own guidance is that you are solely responsible for making sure that your signer or end-user authentication process is sufficient and complies with any and all applicable laws and regulations.'

/**
 * The three things the specification marked inferred.
 *
 * Shown on the page rather than hidden in a docstring, because a seller who believes the
 * knowledge-based questions were generated from public records has been told something
 * this build does not do. The specification marks both data sources `[inferred]` and names
 * no vendor for either.
 */
export const ASSUMPTIONS = [
  {
    id: 'ASSUMED_KBA_PUBLIC_RECORD_SOURCE',
    label: 'Knowledge-based questions',
    detail:
      'The public-record source behind identity questions names no vendor in any cited source, so this room records the questions a sender wrote rather than generating any.',
  },
  {
    id: 'ASSUMED_ID_VERIFICATION_PROVIDER',
    label: 'Government-issued ID check',
    detail:
      'The provider behind the ID check names no vendor either, so this room compares the details a recipient stated against the details a sender recorded. It does not read a document and does not run a background check.',
  },
  {
    id: 'ASSUMED_RECIPIENT_SETTINGS_PANEL',
    label: 'The recipient settings panel',
    detail:
      'The panel that sets a method and a moment is an assumption about a screen. The verification settings object it edits is sourced.',
  },
]

/**
 * The two gates, with the audience each one applies to.
 *
 * Mirrors what `GET /wf-078/vocabulary` serves, written here as well so the board can
 * render before the vocabulary request resolves and so the loading and error states have
 * a defined value to render rather than a blank card. A test asserts the two agree, so
 * this cannot quietly become a second source of truth.
 */
export const PLACES = [
  {
    id: 'before_open',
    label: 'Before open',
    description: 'Before the recipient can view the document',
    audience: 'all_recipients',
    audienceLabel: 'Applies to all recipients',
  },
  {
    id: 'before_sign',
    label: 'Before sign',
    description: 'Before the recipient can sign',
    audience: 'signers_only',
    audienceLabel: 'Applies to signers only',
  },
]

/**
 * The four methods, as a discriminated union.
 *
 * A gate holds exactly one of them, and the one it holds decides what the recipient is
 * asked for. Mirrors the served vocabulary; a test asserts the two agree.
 */
export const METHODS = [
  { id: 'passcode', label: 'Typed passcode', vendorField: 'passcode_verification' },
  { id: 'sms', label: 'SMS one-time password', vendorField: 'phone_verification' },
  { id: 'kba', label: 'Knowledge-based authentication', vendorField: 'kba_verification' },
  { id: 'id', label: 'Government-issued ID check', vendorField: 'id_verification' },
]

/**
 * The three states an SMS number can be in.
 *
 * The specification's extensibility note: an SMS "can be an auth factor, a delivery
 * channel, or both". Three states, not one boolean, because the difference between a
 * number that proves identity and a number that only carries the document is the whole
 * point of the axis.
 */
export const SMS_TYPES = [
  { id: 'authentication', label: 'Authentication', meaning: 'The number carries the one-time code and nothing else.' },
  { id: 'delivery', label: 'Delivery only', meaning: 'The number receives the document and is not an authentication factor.' },
  { id: 'both', label: 'Both', meaning: 'The number receives the document and carries the one-time code.' },
]

/** The passcode bound, quoted from the specification. */
export const PASSCODE_RULE = 'A passcode must be 6 to 100 characters, with at least one letter and at least one digit.'

/** The phone bound, quoted from the specification. */
export const PHONE_RULE =
  'A phone number must be in international format, written as a plus and the country code, for example +1555667890.'

/** The place label for a gate id, for a screen that has only the id. */
export function placeLabel(id) {
  return PLACES.find((place) => place.id === id)?.label || id
}

/** The method label for a method id, for a screen that has only the id. */
export function methodLabel(id) {
  return METHODS.find((method) => method.id === id)?.label || id || 'No method'
}

/** Whether an SMS gate uses its number as an authentication factor rather than for delivery. */
export function isAuthFactor(gate) {
  return gate?.method === 'sms' && gate?.sms_type !== 'delivery'
}
