/**
 * WF-086's own API wrapper.
 *
 * `apiRequest` from `@/lib/api` is the transport, exactly as the feature contract
 * asks: the shared `api` object grows no methods, so a hundred features can each
 * talk to their own `/api/wf-086` routes without anyone editing a shared file.
 *
 * The write calls go through `requestWithBody` instead, and that is a finding
 * rather than a preference. `apiRequest` reads the error body to build a message
 * and then throws it away, so a failure survives as `status` plus one string. Two
 * responses in this workflow need more than that:
 *
 *   - a 422 from the validator carries `errors`, a field-keyed map, so a form can
 *     put each message beside the input that caused it;
 *   - a 409 carries `reason`, a stable token, so the page can say "this quote's
 *     expiration date has passed" instead of printing an English sentence and
 *     hoping the server did not reword it.
 *
 * The shared client would need two more fields on one function, which is a shared
 * file and a platform decision. Until then the calls that need the body read it
 * themselves. Recorded as promotion work, not smuggled across the boundary.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-086'

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
      // A non-JSON error body. The status line is the best there is.
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

export const quoteApi = {
  // -- the board ------------------------------------------------------------ //

  summary: (roomId) => call(`/summary${query({ room_id: roomId })}`),

  /**
   * The researched vocabulary, including what was deliberately not built.
   *
   * The page reads its statuses, module keys and conflict wording from here
   * rather than hard-coding them, so a change to the rules cannot leave the page
   * calling a state the API does not serve.
   */
  vocabulary: () => call('/vocabulary'),

  // -- the product library --------------------------------------------------- //

  /**
   * Search the product library, as "Select from product library" does.
   *
   * The response carries `catalogue_available`, because WF-087 has not shipped.
   * An empty result with the flag false is a correct answer the page explains,
   * not a failed search.
   */
  catalog: (term, limit) => call(`/catalog${query({ term, limit })}`),

  // -- templates ------------------------------------------------------------ //

  templates: (roomId) => call(`/templates${query({ room_id: roomId })}`),
  createTemplate: (payload) => send('/templates', 'POST', payload),

  // -- quotes ---------------------------------------------------------------- //

  quotes: (roomId, status) => call(`/quotes${query({ room_id: roomId, status })}`),
  quote: (quoteId) => call(`/quotes/${encode(quoteId)}`),
  createQuote: (payload, roomId) =>
    send(`/quotes${query({ room_id: roomId })}`, 'POST', payload),
  editQuote: (quoteId, changes) => send(`/quotes/${encode(quoteId)}`, 'PATCH', changes),

  /**
   * Publishes the quote. The server copies the total contract value onto the deal
   * amount and replaces the deal's line items, so the page never does the
   * write-back itself.
   */
  publish: (quoteId) => send(`/quotes/${encode(quoteId)}/publish`, 'POST', {}),

  // -- line items ------------------------------------------------------------- //

  lineItems: (quoteId) => call(`/quotes/${encode(quoteId)}/line-items`),
  addLineItem: (quoteId, payload) =>
    send(`/quotes/${encode(quoteId)}/line-items`, 'POST', payload),
  editLineItem: (lineId, changes) => send(`/line-items/${encode(lineId)}`, 'PATCH', changes),
  removeLineItem: (lineId) => send(`/line-items/${encode(lineId)}`, 'DELETE', {}),
}

/** Every room, so the page can offer one to attach a quote to. */
export const listRooms = () => apiRequest('/records/room?limit=100')

/** Every mirrored deal, so the page can offer one to quote from. */
export const listDeals = (roomId) =>
  apiRequest(`/records/crm_deal${query({ room_id: roomId, limit: 200 })}`)

/**
 * The sentence for each conflict token.
 *
 * The tokens come from the backend's `refusal_reasons` map, which is the
 * authority. These are the page's own words for them, so a server reword cannot
 * turn into a stale sentence in the interface.
 */
export const CONFLICTS = {
  custom_module_not_api_authorable:
    'A custom coded module cannot be added here. Build it in the quote editor and record it.',
  custom_module_needs_ui_provenance:
    'A custom module must say it was built in the editor.',
  module_key_unknown: 'That is not a module this quote can show.',
  already_published: 'This quote has already been published.',
  quote_expired: "This quote's expiration date has passed. Extend the date before publishing.",
  quote_is_empty: 'A quote with no line items cannot be published.',
  quote_frozen: 'A published quote cannot change. Create a new quote instead.',
  deal_not_found: 'No deal record matches that reference.',
}

/** The sentence for a conflict token, or a neutral one the backend never sent. */
export function conflictText(reason) {
  return CONFLICTS[reason] || 'That change was refused.'
}

/**
 * The words for each status. A quote is either being written or has been sent.
 */
export const STATUS_WORDS = {
  draft: 'Draft',
  published: 'Published',
}

/** The tone for each status. A word always accompanies it, so colour never stands alone. */
export const STATUS_TONES = {
  draft: 'info',
  published: 'success',
}

/**
 * The totals, in the order the page shows them.
 *
 * `total_contract_value` is first because it is the figure the publish writes
 * onto the deal, and a seller who reads one number before sending wants that one.
 * The rest follow so the arithmetic can be checked by eye.
 */
export const TOTAL_ROWS = [
  { key: 'subtotal', label: 'Subtotal', hint: 'Quantity times unit price, before any discount.' },
  { key: 'discount', label: 'Discount', hint: 'The sum of every line discount.' },
  { key: 'tax', label: 'Tax', hint: 'Charged on each line after its discount.' },
  { key: 'total', label: 'Total', hint: 'Subtotal, less discount, plus tax.' },
  { key: 'future_payments', label: 'Future payments', hint: 'Scheduled payments dated after today.' },
]

/** The figure that goes onto the deal amount, named so nothing has to guess. */
export const CONTRACT_VALUE_KEY = 'total_contract_value'

/**
 * Format money for display. Two decimals, and the currency code beside it.
 *
 * The code is beside the number rather than in the column header because a table
 * of line items can be read in isolation once scrolled, and a bare 4200.00 in a
 * EUR quote reads as dollars.
 */
export function formatMoney(value, currency) {
  const amount = Number(value ?? 0)
  const text = Number.isFinite(amount)
    ? amount.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : '0.00'
  return currency ? `${text} ${currency}` : text
}
