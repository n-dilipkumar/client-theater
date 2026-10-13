/**
 * WF-087's own API wrapper.
 *
 * `apiRequest` from `@/lib/api` is the transport, exactly as the feature contract
 * asks: the shared `api` object grows no methods, so a hundred features can each
 * talk to their own `/api/wf-087` routes without anyone editing a shared file.
 *
 * The writes go through `requestWithBody` instead. `apiRequest` reads the error
 * body to build a message and then throws it away, so a refusal survives as a
 * status plus one string. Three responses in this workflow need more than that:
 *
 *   - a 422 carries `errors`, a field-keyed map, so a form can put each message
 *     beside the input that caused it. Every tier refusal in this workflow is
 *     keyed by `tiers[n].min_qty`, so the message has to reach the row;
 *   - a 409 carries `errors` too, for the activation refusal, whose message names
 *     the product and the currencies it is missing;
 *   - a 404 carries `error`, a stable token.
 *
 * The shared client would need more fields on one function, which is a shared
 * file and a platform decision. Until then the calls that need the body read it
 * themselves. Recorded as promotion work, not smuggled across the boundary.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-087'

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

export const catalogueApi = {
  // -- the board ----------------------------------------------------------- //

  summary: (roomId) => call(`/summary${query({ room_id: roomId })}`),

  /**
   * The researched vocabulary, including every field label and refusal sentence.
   *
   * The page reads its enumerations, its form labels and its field descriptions
   * from here rather than hard-coding them, so a change to the rules cannot leave
   * the page calling a state the API does not serve.
   */
  vocabulary: () => call('/vocabulary'),

  /** Every decision, every exclusion, and the record shape a reader may rely on. */
  inferences: () => call('/inferences'),

  // -- the product library ------------------------------------------------- //

  products: (roomId, term, activeOnly) =>
    call(`/products${query({ room_id: roomId, term, active_only: activeOnly })}`),

  product: (productId) => call(`/products/${encode(productId)}`),

  createProduct: (payload, roomId) =>
    send(`/products${query({ room_id: roomId })}`, 'POST', payload),

  editProduct: (productId, changes) =>
    send(`/products/${encode(productId)}`, 'PATCH', changes),

  /** The Active toggle. Retires a product from new quotes without deleting it. */
  setProductActive: (productId, active) =>
    send(`/products/${encode(productId)}/active`, 'POST', { active }),

  removeProduct: (productId) => send(`/products/${encode(productId)}`, 'DELETE', {}),

  /**
   * The researched moment: evaluate the tier table against a quantity, then let
   * the price-book layer override the product default price.
   */
  price: (productId, quantity, priceBookId, currency) =>
    send(
      `/products/${encode(productId)}/price${query({ price_book_id: priceBookId, currency })}`,
      'POST',
      { quantity },
    ),

  // -- price books --------------------------------------------------------- //

  priceBooks: (roomId) => call(`/price-books${query({ room_id: roomId })}`),

  priceBook: (bookId) => call(`/price-books/${encode(bookId)}`),

  createPriceBook: (payload, roomId) =>
    send(`/price-books${query({ room_id: roomId })}`, 'POST', payload),

  editPriceBook: (bookId, changes) => send(`/price-books/${encode(bookId)}`, 'PATCH', changes),

  /**
   * Switch a book on or off. Switching it on is refused while any product in it
   * is missing a price in a currency the book supports, and the refusal names
   * both, so the page shows the vendor's rule instead of hiding it.
   */
  setPriceBookActive: (bookId, active) =>
    send(`/price-books/${encode(bookId)}/active`, 'POST', { active }),

  // -- price-book rows ----------------------------------------------------- //

  bookItems: (roomId, priceBookId, productId) =>
    call(`/price-book-items${query({ price_book_id: priceBookId, product_id: productId })}`),

  addBookItem: (payload, roomId) =>
    send(`/price-book-items${query({ room_id: roomId })}`, 'POST', payload),

  editBookItem: (itemId, changes) => send(`/price-book-items/${encode(itemId)}`, 'PATCH', changes),

  removeBookItem: (itemId) => send(`/price-book-items/${encode(itemId)}`, 'DELETE', {}),
}

// --------------------------------------------------------------------------- //
// Presentation. Every mapping below pairs a token with a word.
//
// A status is never shown by colour alone, so each tone here always travels with
// the label beside it. These are the page's own words: the backend owns the
// meaning and the tokens, and a server reword cannot turn into a stale sentence
// in the interface.
// --------------------------------------------------------------------------- //

/** The words for a pricing method, and whether it reads a tier table. */
export const METHOD_WORDS = {
  flat: 'Flat price',
  volume_based: 'Volume based',
  graduated: 'Graduated',
  stair_step: 'Stair step',
}

/**
 * The one line that distinguishes the three tiered methods.
 *
 * A seller reading "Volume based" and "Graduated" has no way to tell which of
 * them discounts a large order, because the names describe the vendor's
 * vocabulary rather than the arithmetic. This sentence does describe the
 * arithmetic, so the page says it next to the name.
 */
export const METHOD_RULES = {
  flat: 'One price, whatever the quantity.',
  volume_based: 'Every unit takes the rate the quantity reaches.',
  graduated: 'Each unit takes the rate of its own range.',
  stair_step: 'The range picks one fee, charged once.',
}

/** The tone for each badge, grouped by what it describes.
 *
 * The tones are the ones `components/ui.jsx` actually carries: `neutral`,
 * `insert`, `update`, `delete` and `restore`. A tone that component does not
 * name falls back to neutral, so a status this page meant to colour would arrive
 * in grey and read as a bug rather than as a choice. Grouped in one map so the
 * call sites in `index.jsx` cannot spell a tone two ways.
 */
export const BADGE_TONES = {
  method: {
    flat: 'neutral',
    volume_based: 'update',
    graduated: 'insert',
    stair_step: 'restore',
  },
  source: {
    price_book: 'insert',
    tier: 'update',
    product_default: 'neutral',
    missing: 'delete',
  },
  status: {
    active: 'insert',
    retired: 'neutral',
  },
}

/** Where a unit price came from, as a phrase a seller can repeat to a buyer. */
export const SOURCE_WORDS = {
  price_book: 'Price book override',
  tier: 'Product tier table',
  product_default: 'Product default price',
  missing: 'No price',
}

/** The words for a product status. */
export const PRODUCT_STATUS_WORDS = {
  active: 'Active',
  retired: 'Retired',
}

/**
 * The sentence for each refusal token.
 *
 * The tokens come from the backend's `error_codes` map, which is the authority.
 * These are the page's own words for them, so a server reword cannot turn into a
 * stale sentence here. A token this map does not name still gets a sentence: the
 * backend may add a refusal before this page learns its name, and a blank error
 * is the worst thing a form can show.
 */
export const REFUSALS = {
  product_needs_a_name: 'Give the product a name so a seller can find it in the library.',
  product_needs_a_sku: 'Give the product a SKU so a quote line can name it.',
  sku_is_taken: 'Another product already uses that SKU.',
  product_needs_a_price: 'Give the product a price in a currency, or a tier table.',
  tier_needs_a_minimum: 'Give the tier a minimum quantity.',
  tier_minimum_must_be_whole: 'A tier minimum is a whole number of units.',
  tier_minimum_must_be_positive: 'A tier minimum is 1 unit or more.',
  tier_needs_a_price: 'Give the tier a unit price, or a fee for stair-step pricing.',
  tier_minimums_must_rise: 'Tier minimums must rise, with no repeat.',
  unknown_pricing_method: 'Choose flat, volume-based, graduated or stair-step.',
  price_book_needs_a_name: 'Give the price book a name.',
  price_book_needs_a_currency: 'Choose at least one currency for the price book.',
  currency_is_not_a_code: 'Use a three-letter currency code, such as USD or EUR.',
  price_book_row_missing_a_price: 'Give the row a price in every currency the book supports.',
  price_book_is_not_complete:
    'Every product needs a price in every currency the book supports before it can go live.',
  price_book_row_needs_a_product: 'Choose the product this row prices.',
  price_book_row_needs_a_book: 'Choose the price book this row belongs to.',
  one_row_per_product_and_book: 'That product is already in this price book.',
  bundle_products_are_not_in_a_price_book:
    'A bundle cannot be added to a price book. Add the products inside the bundle instead.',
  not_a_product: 'No such product.',
  not_a_price_book: 'No such price book.',
  not_a_price_book_item: 'No such price-book row.',
  price_is_negative: 'A price cannot be negative.',
  price_is_not_a_number: 'A price must be a number.',
  price_is_not_finite: 'A price must be a finite number.',
  quantity_must_be_positive: 'A quantity is 1 unit or more.',
  retired_product_is_not_priced: 'This product is retired. Turn Active back on before pricing it.',
}

/** The sentence for a refusal token, or the server's own words when it sent any. */
export function refusalText(code, fallback) {
  return REFUSALS[code] || fallback || 'That change was refused.'
}

/** Format money for display, with the currency code beside it. */
export function formatMoney(value, currency) {
  const amount = Number(value ?? 0)
  const text = Number.isFinite(amount)
    ? amount.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : '0.00'
  return currency ? `${text} ${currency}` : text
}

/**
 * A range as a person reads it: "1 to 9", "25 or more".
 *
 * The open-ended final range is written in words. A symbol would survive neither
 * a cp1252 console nor a screen reader, and the backend already stores that
 * range as an absent maximum rather than a sentinel.
 */
export function rangeLabel(tier) {
  if (!tier) return ''
  const low = Number(tier.min_qty ?? 0)
  const high = tier.max_qty
  if (high === null || high === undefined) return `${low} or more`
  return `${low} to ${Number(high)}`
}