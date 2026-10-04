/**
 * WF-089: quote in a transaction currency with FX conversion, and the refusals.
 *
 * `apiRequest` from `@/lib/api` is the transport. The shared `api` object grows no method
 * for this workflow, so it cannot collide with the other ninety-nine features.
 *
 * One helper here is not a convenience. `requestWithBody` keeps the parsed error body on
 * the thrown error, because this workflow's refusals live in that body: a pricing refusal
 * carries a `pricing_error_code` and its own wording, and a 409 currency change carries the
 * line count and the remedy. A page that could only read the status code would render
 * "409" where the workflow has an answer.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-089'

/** Drop empty values so we never send `?room_id=` and confuse a filter. */
export function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

/**
 * As `apiRequest`, but keeps the parsed error body on the thrown error.
 *
 * This workflow answers most of its interesting states with a normal response rather than
 * an error, so this is not the main path. It exists for the four error bodies: the 400
 * field map, the 404, the 409 currency-change refusal and the 409 missing-rate refusal.
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
      // A non-JSON error body. The status line is then the best information available.
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

function call(path) {
  return apiRequest(`${BASE}${path}`)
}

function send(path, method, payload) {
  return requestWithBody(path, { method, body: JSON.stringify(payload ?? {}) })
}

const encode = encodeURIComponent

export const quoteCurrencyApi = {
  // -- the board and the research ----------------------------------------------

  /** The headline numbers, read back from the store. */
  summary: (roomId) => call(`/summary${query({ room_id: roomId })}`),

  /**
   * The researched vocabulary, so the page cannot drift from the rules behind it: both
   * money field lists, the two refusal codes, the six recalculation triggers and the rate
   * policy all come from the same tables the engine reads.
   */
  vocabulary: () => call('/vocabulary'),

  /** Every judgement call this workflow made, with the alternative it rejected. */
  decisions: () => call('/decisions'),

  /** One judgement call by id. */
  decision: (decisionId) => call(`/decisions/${encode(decisionId)}`),

  // -- currencies ---------------------------------------------------------------

  /** Every transaction currency record, with its rate and where the rate came from. */
  currencies: (roomId) => call(`/currencies${query({ room_id: roomId })}`),

  /**
   * Register one transaction currency record.
   *
   * A rate may only be supplied for a `Custom` record, and the server refuses otherwise,
   * so the page sends `currency_type` first and lets the server hold the rule.
   */
  registerCurrency: (payload, { roomId, actor } = {}) =>
    send(`/currencies${query({ room_id: roomId, actor })}`, 'POST', payload),

  /**
   * Stamp a custom rate on a `Custom` record.
   *
   * Existing quotes keep the base figures they were priced with. The response names how
   * many, so the page can say so rather than implying a re-priced quote.
   */
  stampRate: (currencyId, exchangeRate, { roomId, actor } = {}) =>
    send(
      `/currencies/${encode(currencyId)}/rate${query({ room_id: roomId, actor })}`,
      'POST',
      { exchange_rate: exchangeRate },
    ),

  // -- price lists and price rows -------------------------------------------------

  /** Every price list, with its currency and the products it prices. */
  priceLists: (roomId) => call(`/price-lists${query({ room_id: roomId })}`),

  /** Create a price list in one currency. */
  createPriceList: (payload, { roomId, actor } = {}) =>
    send(`/price-lists${query({ room_id: roomId, actor })}`, 'POST', payload),

  /** The price rows on one list: the rows a unit price resolves from. */
  priceItems: (priceListId, roomId) =>
    call(`/price-lists/${encode(priceListId)}/items${query({ room_id: roomId })}`),

  /** Create one price row. The list fixes the currency, so no currency is sent. */
  createPriceItem: (priceListId, payload, { roomId, actor } = {}) =>
    send(`/price-lists/${encode(priceListId)}/items${query({ room_id: roomId, actor })}`, 'POST', payload),

  // -- quotes and lines -----------------------------------------------------------

  /** Every quote with its header currency, its price list, its rate and both totals. */
  quotes: (roomId) => call(`/quotes${query({ room_id: roomId })}`),

  /** Create a quote header stamped with a transaction currency. */
  createQuote: (payload, { roomId, actor } = {}) =>
    send(`/quotes${query({ room_id: roomId, actor })}`, 'POST', payload),

  /** One quote with its lines and both sets of totals. */
  quote: (quoteId, roomId) => call(`/quotes/${encode(quoteId)}${query({ room_id: roomId })}`),

  /** Add a line item. A product and a quantity, and no price: the price list supplies it. */
  addLine: (quoteId, payload, { roomId, actor } = {}) =>
    send(`/quotes/${encode(quoteId)}/lines${query({ room_id: roomId, actor })}`, 'POST', payload),

  /**
   * Change a quote's transaction currency.
   *
   * A `PATCH`, because the currency is a field of the quote. A quote that holds line items
   * answers 409 with the line count, the sourced sentence and the remedy, all of which
   * `requestWithBody` keeps for the page to render.
   */
  changeCurrency: (quoteId, payload, { roomId, actor } = {}) =>
    send(`/quotes/${encode(quoteId)}/currency${query({ room_id: roomId, actor })}`, 'PATCH', payload),

  // -- pricing ---------------------------------------------------------------------

  /**
   * Run the pricing.
   *
   * Answers 200 either way. A wrong-currency combination comes back as
   * `outcome: refused` with a `pricing_error_code`, which is a recorded outcome rather than
   * a failed request, so this call resolves for both and the page renders both.
   */
  priceQuote: (quoteId, payload, { roomId, actor } = {}) =>
    send(`/quotes/${encode(quoteId)}/price${query({ room_id: roomId, actor })}`, 'POST', payload),

  /** Every pricing run on one quote, newest first. A refused run is in the list too. */
  pricingRuns: (quoteId, roomId) =>
    call(`/quotes/${encode(quoteId)}/pricing-runs${query({ room_id: roomId })}`),

  /** Every rate this quote's pricing raised an event for, newest first. */
  rateReads: (quoteId, roomId) =>
    call(`/quotes/${encode(quoteId)}/rate-reads${query({ room_id: roomId })}`),
}

/**
 * The two researched refusal codes, as this page renders them.
 *
 * `tone` is decoration only: every badge on this page carries the code as text, because
 * status is never conveyed by colour alone. The wording is the specification's own, and a
 * code this build has not heard of falls through to its raw value rather than to an empty
 * badge.
 */
export const PRICING_ERROR_CODES = [
  {
    value: '34',
    label: '34 Invalid Price Level Currency',
    meaning:
      'The price list is in a different currency from the quote, so the base record and its line items would not use the same currency.',
    tone: 'warning',
  },
  {
    value: '38',
    label: '38 Transaction currency is not set for the product price list item',
    meaning:
      'A line item names a product with no price row on the selected price list in the transaction currency, so the quote cannot be priced.',
    tone: 'warning',
  },
]

/** One refusal code by value, with a fallback so the page never renders blank. */
export function pricingErrorCode(value) {
  return (
    PRICING_ERROR_CODES.find((row) => row.value === value) || {
      value: value || 'none',
      label: value ? `Pricing error ${value}` : 'No pricing error',
      meaning: '',
      tone: 'neutral',
    }
  )
}

/** The three states a quote can read as. `refused` is a state, not a fault. */
export const PRICING_OUTCOMES = [
  {
    value: 'priced',
    label: 'Priced',
    meaning: 'Both sets of totals were computed and stored against the rate stamped here.',
    tone: 'insert',
  },
  {
    value: 'refused',
    label: 'Refused to price',
    meaning:
      'The platform refused and set a pricing error code. No totals were stored, and the run is on the audit log.',
    tone: 'warning',
  },
  {
    value: null,
    label: 'Not priced yet',
    meaning: 'No pricing run has answered for this quote, so it holds no totals.',
    tone: 'neutral',
  },
]

/** One pricing outcome by value, with a fallback. */
export function pricingOutcome(value) {
  return PRICING_OUTCOMES.find((row) => row.value === value) || PRICING_OUTCOMES[2]
}

/**
 * The six recalculation triggers, as a select the page can drive a run from.
 *
 * The order is the research's: record open, create, update, then the three product events.
 * `record_open` leads because it is what the page does on a user's first click, and it is
 * also the server's default when no trigger is sent.
 */
export const RECALCULATION_TRIGGERS = [
  'record_open',
  'record_create',
  'record_update',
  'product_add',
  'product_update',
  'product_delete',
]

/**
 * A money figure as the page renders it.
 *
 * The figures arrive as decimal strings and are rendered as they arrive. This page never
 * multiplies or rounds a money value: the arithmetic is the server's, in `Decimal`, at the
 * currency's own precision, and a page that reformatted a figure would show a number the
 * quote does not carry. A figure the server has not computed renders as a dash rather than
 * as a zero, because "not computed" and "nothing" are different facts.
 */
export function money(value, isoCode) {
  if (value === null || value === undefined || value === '') return '-'
  return isoCode ? `${value} ${isoCode}` : String(value)
}

/** An exchange rate as the page renders it, or a dash when there is none. */
export function rate(value) {
  if (value === null || value === undefined || value === '') return 'no rate'
  return Number(value).toLocaleString(undefined, {
    minimumFractionDigits: 0,
    maximumFractionDigits: 6,
  })
}

/** A count with a singular and a plural noun, so a page never says "1 quotes". */
export function plural(count, noun) {
  const total = Number(count) || 0
  return `${total} ${noun}${total === 1 ? '' : 's'}`
}

/**
 * The refusal the server returned, in its own words.
 *
 * A 409 carries the line count and the remedy, and this workflow's whole reason for
 * returning a body rather than a bare status code is so this function has something to
 * render.
 */
export function refusalSummary(error) {
  if (error?.body?.remedy) return `${error.body.detail} ${error.body.remedy}`
  if (error?.body?.detail) return error.body.detail
  const fields = error?.errors || error?.body?.errors
  if (fields) {
    return Object.entries(fields)
      .map(([field, message]) => `${field}: ${message}`)
      .join(' ')
  }
  return String(error?.message || error || 'The request was refused.')
}