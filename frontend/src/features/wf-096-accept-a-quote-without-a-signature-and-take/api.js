/**
 * WF-096's own API wrapper: accept a quote without a signature and take payment in the quote.
 *
 * `apiRequest` from `@/lib/api` is the transport for reads. The shared `api` object grows no
 * methods, so a hundred features each talk to their own `/wf-096` routes without editing a
 * shared file.
 *
 * Writes go through `fetch` so the server's *parsed* error body survives on the thrown error.
 * The page needs three things off a refusal: the status code, the published `reason` code, and
 * the field-keyed `errors` map that lands next to its input. Reconstructing those from a
 * statusText would drift from `dsr.quote_payment`, so the server stays the authority and every
 * refusal text on the page is the server's.
 *
 * A declined charge is **not** an error here. The researched rule "the total amount due must be
 * more than $0.50" is a payment-processor outcome, so the server answers 200 with
 * `outcome: 'declined'` and a `reason`. `pay` returns that shape either way, and the page reads
 * a declined charge and a recorded one the same way.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-096'

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
 * A 400 carries `{ error, detail, errors }`; a 409 carries `{ error, reason, detail }`. The
 * page reads all three rather than guessing which one a status code would have produced.
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
    error.reason = body?.reason || null
    error.errors = body?.errors || null
    error.body = body
    throw error
  }

  if (response.status === 204) return null
  return response.json()
}

function send(path, method, payload, params) {
  return requestWithBody(`${path}${query(params)}`, {
    method,
    body: payload === undefined ? undefined : JSON.stringify(payload),
  })
}

/**
 * The researched and derived vocabulary, mirrored so the page can render a choice label
 * without a round trip. The server is still the authority: `vocabulary()` returns the same
 * lists and the same evidence sentences, and the page prefers what it returns.
 */
export const FACTS = {
  acceptanceMethods: [
    { value: 'clickwrap', label: 'Accept without signature' },
    { value: 'esignature', label: 'E-signature (owned by WF-095)' },
    { value: 'print_and_sign', label: 'Print and sign (never with online payments)' },
  ],
  paymentMethods: [
    { value: 'CREDIT_OR_DEBIT_CARD', label: 'Credit or debit card' },
    { value: 'ACH', label: 'ACH' },
    { value: 'SEPA', label: 'SEPA direct debit' },
    { value: 'BACS', label: 'BACS direct debit' },
    { value: 'PADS', label: 'PADS pre-authorized debit' },
  ],
  billingFrequencies: [
    { value: 'one_time', label: 'One time' },
    { value: 'monthly', label: 'Monthly' },
    { value: 'quarterly', label: 'Quarterly' },
    { value: 'semi_annual', label: 'Every six months' },
    { value: 'annual', label: 'Annual' },
  ],
  effectiveDateModes: [
    { value: 'on_agreement', label: 'On agreement' },
    { value: 'custom_date', label: 'Custom date' },
    { value: 'delayed_days', label: 'Delayed start (days)' },
    { value: 'delayed_months', label: 'Delayed start (months)' },
  ],
  netTerms: [
    { value: 'NET_0', label: 'NET 0' },
    { value: 'NET_15', label: 'NET 15' },
    { value: 'NET_30', label: 'NET 30' },
    { value: 'NET_45', label: 'NET 45' },
    { value: 'NET_60', label: 'NET 60' },
  ],
  //: The research's numbers. The server returns each of these from `/summary` and
  //: `/vocabulary`; these are the fallbacks the page draws before that read answers.
  minimumChargeUsd: 0.5,
  taxIdLimit: 3,
  subsequentInvoicePeriods: 3,
  invoiceLeadDays: 10,
  anonymousBuyer: 'Buyer (no contact specified)',
}

/**
 * The acceptance-to-payment rail, in the order the research puts the steps. The buyer accepts
 * first (the click-to-accept), then optionally sets up payment; a quote that has both is done.
 */
export const RAIL = [
  { key: 'published', label: 'Published for payments' },
  { key: 'accepted', label: 'Accepted without signature' },
  { key: 'paid', label: 'Payment taken' },
]

/** Where a quote sits on the rail: `published` -> `accepted` -> `paid`. */
export function railStep(detail) {
  if (!detail?.setup) return -1
  if ((detail.charges || []).some((charge) => charge.outcome === 'recorded')) return 2
  if (detail.quote?.accepted) return 1
  return 0
}

/** The one label a quote's payment state carries, so status is never colour alone. */
export function paymentState(detail) {
  const charge = (detail?.charges || [])[0]
  if (charge?.outcome === 'recorded') return 'Paid'
  if (charge?.outcome === 'declined') return 'Payment declined'
  if (detail?.quote?.accepted) return 'Awaiting payment'
  if (detail?.setup) return 'Awaiting acceptance'
  return 'Not published for payments'
}

const PAYMENT_STATE_TONES = {
  Paid: 'success',
  'Payment declined': 'danger',
  'Awaiting payment': 'warning',
  'Awaiting acceptance': 'info',
  'Not published for payments': 'neutral',
}

export function paymentTone(detail) {
  return PAYMENT_STATE_TONES[paymentState(detail)] || 'neutral'
}

/** `plural(1, 'invoice')` gives `1 invoice`. Local, because a feature may not import a sibling. */
export function plural(count, one, many) {
  return `${count} ${count === 1 ? one : (many ?? `${one}s`)}`
}

/** Money as the quote's own currency, to two places. Two decimals is the research's unit. */
export function formatMoney(amount, currency = 'USD') {
  const value = Number(amount || 0)
  try {
    return new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(value)
  } catch {
    return `${value.toFixed(2)} ${currency}`
  }
}

/** A calendar date `YYYY-MM-DD`, with an em dash for an absent or unparsable value. */
export function formatDate(value) {
  if (!value) return '--'
  const when = new Date(`${value}T00:00:00`)
  if (Number.isNaN(when.getTime())) return '--'
  return when.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}

/** An ISO instant as a short local date and time, or an em dash. */
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

/** The amount due of a quote view, defaulted so a caller can always read `.total`. */
export function amountDue(quote) {
  return quote?.amount_due || { subtotal: 0, discount: 0, tax: 0, total: 0, line_count: 0 }
}

export const paymentsApi = {
  // -- the board and the research --------------------------------------------

  /** The headline numbers, read back from the store. */
  summary: (roomId) => call(`/summary${query({ room_id: roomId })}`),

  /** The researched vocabulary and its evidence sentences. */
  vocabulary: () => call('/vocabulary'),

  /** Every judgement call this workflow made, with the alternative it rejected. */
  decisions: () => call('/decisions'),

  // -- the quote WF-086 owns --------------------------------------------------

  /** A quote to accept and pay. WF-086 provisions it in production; this route demos the flow. */
  createQuote: (roomId, payload) => send(`/rooms/${encode(roomId)}/quotes`, 'POST', payload),

  /** A line item on a quote, priced the way WF-086 prices one. */
  createLineItem: (quoteId, payload) =>
    send(`/quotes/${encode(quoteId)}/line-items`, 'POST', payload),

  /** Every provisioned quote, each with its setup, acceptance, charges and invoices. */
  quotes: (roomId) => call(`/quotes${query({ room_id: roomId })}`),

  /** One quote's whole payment picture. `setup` is `null` when it was never published. */
  quote: (quoteId) => call(`/quotes/${encode(quoteId)}`),

  // -- publishing the payment configuration -----------------------------------

  /**
   * Publish a quote for online payments: the acceptance method, the allowed payment methods,
   * the billing frequency, the net terms and the effective date. Writes the derived
   * `hs_payment_type` and sets `hs_payment_status` to `PENDING`.
   */
  publish: (quoteId, payload, { actor } = {}) =>
    send(`/quotes/${encode(quoteId)}/publish`, 'POST', payload, { actor }),

  // -- the buyer's two steps --------------------------------------------------

  /**
   * Accept without a signature (the click-to-accept). Writes the acceptance, flips the quote's
   * `hs_status` to `ACCEPTED`, and creates the first invoice and the recurring subscriptions.
   * No money moves here.
   */
  accept: (quoteId, payload, { actor } = {}) =>
    send(`/quotes/${encode(quoteId)}/accept`, 'POST', payload, { actor }),

  /**
   * Take payment for the amount due. Returns `{outcome: 'recorded' | 'declined', ...}` as a 200
   * in both cases, because a declined charge is an outcome and not a malformed request.
   */
  pay: (quoteId, payload, { actor } = {}) =>
    send(`/quotes/${encode(quoteId)}/payment`, 'POST', payload, { actor }),

  // -- invoices, charges, tax ids, void and delete ----------------------------

  /** The first invoice and every scheduled later invoice, oldest first. */
  invoices: (quoteId) => call(`/quotes/${encode(quoteId)}/invoices`),

  /** Every charge attempt, whether it was recorded or declined. */
  charges: (quoteId) => call(`/quotes/${encode(quoteId)}/charges`),

  /** Add a buyer tax ID. Refused with a 400 once the researched cap of three is reached. */
  addTaxId: (quoteId, payload, { actor } = {}) =>
    send(`/quotes/${encode(quoteId)}/tax-ids`, 'POST', payload, { actor }),

  /** Void a quote. Refused with a 409 once it has been accepted. */
  voidQuote: (quoteId, { actor } = {}) =>
    send(`/quotes/${encode(quoteId)}/void`, 'POST', undefined, { actor }),

  /** Delete a quote. Refused with a 409 once it has been accepted. */
  deleteQuote: (quoteId, { actor } = {}) =>
    send(`/quotes/${encode(quoteId)}`, 'DELETE', undefined, { actor }),
}

export default paymentsApi
