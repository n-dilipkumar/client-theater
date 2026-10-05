/**
 * WF-095's own API wrapper: collect a quote's acceptance by e-signature, countersignature
 * and identity verification.
 *
 * `apiRequest` from `@/lib/api` is the transport. The shared `api` object grows no methods,
 * so a hundred features can each talk to their own `/wf-095` routes without anyone editing a
 * shared file.
 *
 * Reads outnumber writes here, and that is the shape of the workflow. The board, the
 * vocabulary, the six decisions, the envelopes, the signers, the events and the quota are
 * reads. The writes are the steps the research's user flow describes: open an envelope,
 * request and confirm verification, sign, reassign.
 *
 * This file knows the server's error-body shape (`error`, `detail`, `errors`) and nothing
 * about the domain rules. Transport and typing only. Every refusal text on the page is the
 * server's, so a page cannot drift from the rules in `dsr.quote_acceptance`.
 *
 * The verification-link token is returned in the body. The research names the emailed
 * from-address as a super-admin setting this build does not provision, so the buyer page can
 * present the token. A production deployment emails it instead.
 *
 * A refused signature is a 200 carrying `outcome: failed`, not an HTTP error, because the
 * research logs signing failures automatically. `sign` returns that shape either way, so the
 * page reads a failed attempt and a successful one the same way.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-095'

function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

const encode = encodeURIComponent

/** A read through `apiRequest`. Throws an `Error` with `.status` on a non-2xx. */
function call(path) {
  return apiRequest(`${BASE}${path}`)
}

/**
 * A write through `fetch`, so the parsed error body survives on the thrown error.
 *
 * The page needs the field-keyed `errors` map beside a 400 and the research quote beside a
 * refusal, and it needs `sign`'s `outcome: failed` body intact. Both are read from the parsed
 * JSON here rather than reconstructed.
 */
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
      // A non-JSON error body. The status line is the best answer available.
    }
    const error = new Error(
      body?.detail || body?.error || `${response.status} ${response.statusText}`,
    )
    error.status = response.status
    error.code = body?.error || null
    error.errors = body?.errors || null
    error.body = body
    throw error
  }

  if (response.status === 204) return null
  return response.json()
}

function send(path, method, payload, params) {
  return requestWithBody(`${path}${query(params)}`, { method, body: JSON.stringify(payload) })
}

/**
 * The researched rules, so the page can render the evidence sentence beside each number.
 *
 * These mirror constants in `dsr.quote_acceptance.vocabulary`. The server stays the authority:
 * every rule endpoint returns the same sentences, and these are the labels the page draws
 * around a value the server already computed.
 */
export const acceptanceFacts = {
  verificationWindowMinutes: 60,
  verificationWindowQuote:
    'Buyers have one hour to complete the signature process after clicking Verify email.',
  pdfSizeCapMb: 40,
  pdfSizeCapQuote: 'Quote PDFs larger than 40 MB may not be successfully verified or signed.',
  quotaCountsEnvelopeNotSignerQuote:
    'if a published quote with e-signatures enabled requires three signatures, this only counts as one usage toward your limit.',
  quotaConsumedOnEnableQuote:
    "An e-signature will count toward the limit as soon as the e-signature option is turned on for a published quote. The quote doesn't need to be signed to apply to the signature limit.",
  inSigningForcesEsignatureQuote:
    'Attachments can be marked as In signing to be included in the signing envelope of an e-signature. If an attachment is marked In signing, e-signature must be used for the quote.',
  authenticationOwnerQuote:
    'It is your responsibility to verify the identity of any user who views a document within the signing session. Some use cases legally require Identity Verification to be enabled.',
  contractIsDownstreamQuote:
    'if Automatically create contracts from accepted quotes is on, a contract is created. That consumer is WF-099, which is downstream of this ticket. This workflow writes the acceptance this consumer reads.',
  countersignerPoolQuote:
    "Countersigners are drawn from your own users. This build resolves that pool from this room's own users, because the research names no directory and no external provider.",
  reassignAfterSignQuote:
    'This quote signer has already signed, so the signature can no longer be reassigned. A buyer may reassign their own signing step before they sign it.',
  reassignAllowedLabel: 'Quote signer(s) can reassign',
  buyerSignersLabel: 'Buyer contacts required to sign',
  countersignersLabel: 'Countersigners',
  verifyEmailLabel: 'Verify email',
  drawLabel: 'Draw',
  typeLabel: 'Type',
  uploadLabel: 'Upload',
}

/** The signing-status order the research's data flow names, for the progress rail. */
export const SIGNING_ORDER = [
  'pending_signature',
  'viewed_pending_signature',
  'pending_countersignature',
  'accepted',
]

export const STATUS_LABELS = {
  pending_signature: 'Pending signature',
  viewed_pending_signature: 'Viewed - pending signature',
  pending_countersignature: 'Pending countersignature',
  accepted: 'Accepted',
}

/** The four activity labels the research names, in the order it lists them. */
export const ACTIVITY_LABELS = {
  quote_buyer_signed: 'Quote buyer signed',
  quote_countersigned: 'Quote countersigned',
  quote_reassigned: 'Quote reassigned',
  signing_attempt_failed: 'Signing attempt failed',
}

/** The three acceptance methods, with the label the seller's sidebar shows. */
export const ACCEPTANCE_METHODS = [
  { value: 'esignature', label: 'E-signature' },
  { value: 'clickwrap', label: 'Accept without signature' },
  { value: 'print_and_sign', label: 'Print and sign' },
]

/**
 * `plural(1, 'envelope')` gives `1 envelope`. A feature folder may not import from a sibling
 * feature folder, so this is local rather than borrowed.
 */
export function plural(count, one, many) {
  return `${count} ${count === 1 ? one : (many ?? `${one}s`)}`
}

/**
 * An ISO instant as a short local date and time. An em dash for an absent or unparsable
 * instant, because a reader must never see a broken timestamp on the board.
 */
export function formatInstant(value) {
  if (!value) return '--'
  const when = new Date(value)
  if (Number.isNaN(when.getTime())) return '--'
  return when.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/** A byte count as MB to one decimal place. An em dash when a quote has no document yet. */
export function documentSize(bytes) {
  if (bytes === null || bytes === undefined) return '--'
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/**
 * The zero-based index of a status on the research's chain, for the progress rail. `-1` for an
 * unknown status, so the rail shows nothing lit rather than guessing.
 */
export function signingStep(status) {
  return SIGNING_ORDER.indexOf(status)
}

export const acceptanceApi = {
  // -- the board and the research --------------------------------------------

  /** The headline numbers, read back from the store. */
  summary: (roomId) => call(`/summary${query({ room_id: roomId })}`),

  /** The researched vocabulary, so the page cannot drift from the rules behind it. */
  vocabulary: () => call('/vocabulary'),

  /** Every judgement call this workflow made, with the alternative it rejected. */
  decisions: () => call('/decisions'),

  // -- the quote and the document this workflow signs -------------------------

  /**
   * A quote to collect acceptance on. WF-086 provisions it in production. This route exists so
   * the flow is demonstrable before WF-086 lands, and writes into the same collection.
   */
  createQuote: (roomId, payload) => send(`/rooms/${encode(roomId)}/quotes`, 'POST', payload),

  /** A proposal document for the signature to bind to. WF-093 provisions it in production. */
  createDocument: (roomId, payload) => send(`/rooms/${encode(roomId)}/documents`, 'POST', payload),

  // -- the envelope -----------------------------------------------------------

  /** Every signing envelope, each with its signers and the signing status. */
  envelopes: (roomId) => call(`/envelopes${query({ room_id: roomId })}`),

  /** One envelope, its signers, and the signing status with the evidence beside it. */
  envelope: (envelopeId) => call(`/envelopes/${encode(envelopeId)}`),

  /**
   * Open a signing envelope. This is the seller's configuration step: tick the buyer contacts,
   * pick the countersigners, optionally allow reassignment, and turn identity verification on.
   * `isPublished` decides whether the envelope consumes quota on open.
   */
  openEnvelope: (payload, { roomId, isPublished, actor } = {}) =>
    send(`/envelopes`, 'POST', payload, {
      room_id: roomId,
      is_published: isPublished,
      actor,
    }),

  /** Every signature event on an envelope, and the activity each one wrote. */
  events: (envelopeId) => call(`/envelopes/${encode(envelopeId)}/events`),

  // -- the buyer's acceptance steps -------------------------------------------

  /** A buyer opened the quote. Advances pending to viewed-pending. Writes no activity. */
  markViewed: (envelopeId, { actor } = {}) =>
    send(`/envelopes/${encode(envelopeId)}/view`, 'POST', {}, { actor }),

  /**
   * Mint the one-hour verification link, on the buyer's *Verify email* click. The window opens
   * now, not at send.
   */
  requestVerification: (envelopeId, { actor } = {}) =>
    send(`/envelopes/${encode(envelopeId)}/verify`, 'POST', {}, { actor }),

  /**
   * Check a verification token against the envelope's one-hour window. A pass and a fail are
   * both 200. An expired window is an answer, not an error: the buyer clicks again to reopen.
   */
  confirmVerification: (envelopeId, token, { actor } = {}) =>
    send(`/envelopes/${encode(envelopeId)}/verify/confirm`, 'POST', { token }, { actor }),

  /**
   * Record one party's signature. `signatureMode` is draw, type or upload. Returns
   * `{outcome: 'signed' | 'failed', ...}` as a 200 in both cases.
   */
  sign: (signerId, { signatureMode, signaturePayload, verificationToken, actor } = {}) =>
    send(
      `/signers/${encode(signerId)}/sign`,
      'POST',
      {
        signature_mode: signatureMode,
        signature_payload: signaturePayload,
        verification_token: verificationToken,
      },
      { actor },
    ),

  /**
   * Reassign a signer who has not yet signed. Refused with a 400 when the quote has
   * reassignment off or the party has already signed. Appends a *Quote reassigned* activity.
   */
  reassign: (signerId, { name, email, actor } = {}) =>
    send(`/signers/${encode(signerId)}/reassign`, 'POST', { name, email }, { actor }),

  /** One signer, with the role label and whether they have verified and signed. */
  signer: (signerId) => call(`/signers/${encode(signerId)}`),

  // -- quota ------------------------------------------------------------------

  /**
   * The month's e-signature usage: envelopes charged and their cost. The ceiling is `null`
   * because the research states no number.
   */
  quota: (month) => call(`/quota${query({ month })}`),
}

export default acceptanceApi
