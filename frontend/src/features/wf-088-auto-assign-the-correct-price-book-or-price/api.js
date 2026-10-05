/**
 * WF-088's own API wrapper.
 *
 * `apiRequest` from `@/lib/api` is the transport, exactly as the contract asks.
 * The shared `api` object grows no methods, so a hundred features can each talk
 * to their own `/api/WF-088` routes without anyone editing a shared file.
 *
 * The prefix is upper case because the ticket slug the issue names is
 * `/api/WF-088`, and `WF-091` and `WF-094` already ship that spelling. The host
 * matches paths literally, so the two spellings are not interchangeable.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/WF-088'

const encode = encodeURIComponent

function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

function call(path, params) {
  return apiRequest(`${BASE}${path}${query(params)}`)
}

export const priceBookApi = {
  /** The filters, operators, switches, modes, reasons and states, from one place on the server. */
  vocabulary: () => call('/vocabulary'),

  /** What the research left open, and which reading this build took. */
  inferences: () => call('/inferences'),

  /** How much is priced and what is still waiting on a person. */
  summary: (roomId) => call('/summary', { room_id: roomId }),

  /** The configured rules, oldest first. */
  rules: (roomId) => call('/rules', { room_id: roomId }),

  /**
   * Save a rule: its price book, its filters and its two switches, together.
   *
   * There is no separate "add filter" call. The research's own UI refuses to save
   * a rule that is missing any of its parts, so a half-configured rule is a state
   * this product does not create either.
   */
  createRule: (payload, { roomId, actor } = {}) =>
    apiRequest(`${BASE}/rules${query({ room_id: roomId, actor })}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /**
   * The **Inactive** switch and the **Auto-assigned** toggle, as a merge patch.
   *
   * A merge patch, so switching one does not silently reset the filters. Both are
   * revalidated by the server: a patch that would leave the rule without a price
   * book is refused rather than half applied.
   */
  patchRule: (ruleId, payload, { actor } = {}) =>
    apiRequest(`${BASE}/rules/${encode(ruleId)}${query({ actor })}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),

  removeRule: (ruleId, { actor } = {}) =>
    apiRequest(`${BASE}/rules/${encode(ruleId)}${query({ actor })}`, { method: 'DELETE' }),

  /**
   * The researched right panel: which books this deal matches, and why.
   *
   * A GET, because it checks and writes nothing. It runs the same evaluation the
   * assignment runs, so what an admin reviews while building a rule is what will
   * be enforced when a deal arrives.
   */
  conditions: (dealId) => call(`/deals/${encode(dealId)}/conditions`),

  /**
   * Run the rule on a deal. This is the researched moment: the deal row was created.
   *
   * `trigger` is `create` or `update`. It is sent rather than assumed because
   * "Price books are auto-assigned only when a deal is created" is a rule the
   * server has to see to refuse an update, and a page that hid the parameter
   * would be a page that could not show why nothing happened.
   */
  assign: (dealId, { trigger, roomId, actor } = {}) =>
    apiRequest(`${BASE}/deals/${encode(dealId)}/assign${query({ room_id: roomId, actor, trigger })}`, {
      method: 'POST',
      body: JSON.stringify({}),
    }),

  /** The *Line items* card: **Price book: None**, or the book's name. */
  priceBook: (dealId) => call(`/deals/${encode(dealId)}/price-book`),

  /**
   * **Change price book**. The researched override.
   *
   * This is destructive. "If the price book is changed, any line items associated
   * with the previous price book will be removed", so the server answers with every
   * id it removed and a page has to be able to warn before offering it. It is also
   * how a deal that matched more than one rule is resolved: the owner chooses here.
   */
  changePriceBook: (dealId, priceBook, { roomId, actor } = {}) =>
    apiRequest(`${BASE}/deals/${encode(dealId)}/price-book${query({ room_id: roomId, actor })}`, {
      method: 'POST',
      body: JSON.stringify({ price_book: priceBook }),
    }),

  /**
   * What a quote gets. A read, deliberately: "Users can't select a price book when
   * creating a quote; they must select it on the deal", so there is no setter.
   */
  quotePriceBook: (quoteId) => call(`/quotes/${encode(quoteId)}/price-book`),

  /** Every decision, including the ones that assigned nothing. */
  assignments: ({ dealId, outcome, roomId } = {}) =>
    call('/assignments', { deal_id: dealId, outcome, room_id: roomId }),

  assignment: (assignmentId) => call(`/assignments/${encode(assignmentId)}`),
}

/** Every room, so the page can offer one. Read from the core collection. */
export const listRooms = () => apiRequest('/records/room?limit=100')

/**
 * Every deal this workflow can be pointed at.
 *
 * The deals are a CRM mirror another workflow writes, so they are read from the
 * core `/records` route rather than from a list this feature keeps. A workspace
 * that mirrors them as `crm_opportunity` instead passes that name; the server
 * resolves the rest, and the rules bind to properties rather than to a collection,
 * so the rules themselves need no change.
 */
export function listDeals(collection = 'crm_deal') {
  return apiRequest(`/records/${encode(collection)}?limit=200`)
}

/**
 * Every quote this workflow can price, so the panel can show what a quote inherits.
 *
 * The quotes belong to WF-086, read rather than created here, for the same reason
 * as the deals.
 */
export function listQuotes(collection = 'wf086_quote') {
  return apiRequest(`/records/${encode(collection)}?limit=200`)
}

/**
 * The words a card shows for one reason, read from the server's vocabulary.
 *
 * Served rather than compiled, so a reason added on the server appears here with
 * no edit to this file. The fallback is the raw code rather than `undefined`: a
 * label that renders as nothing is worse than one that shows what it did not
 * recognise.
 */
export function reasonLabel(reason, vocabulary) {
  const entry = (vocabulary?.assignments || []).find((row) => row.reason === reason)
  return entry?.label || reason || 'Unknown'
}

/** The tone for a reason, as a second signal beside the label rather than instead of it. */
export function reasonTone(reason, writes) {
  if (writes) return 'success'
  switch (reason) {
    case 'needs_choice':
      return 'warning'
    case 'no_rule_matched':
    case 'matched_rule_is_inactive':
    case 'matched_rule_without_auto_assign':
      return 'neutral'
    default:
      return 'info'
  }
}

/** The words for one of the three researched modes. */
export function modeLabel(mode, vocabulary) {
  const entry = (vocabulary?.modes || []).find((row) => row.mode === mode)
  return entry?.label || mode || 'Unknown'
}

/** The words for one deal state, read from the server's vocabulary. */
export function stateLabel(state, vocabulary) {
  const entry = (vocabulary?.book_states || []).find((row) => row.state === state)
  return entry?.label || state || 'Unknown'
}

/**
 * The tone for a deal state.
 *
 * Never the only signal: every badge on this page renders its state as text beside
 * the colour, so a screen-reader user gets the same information.
 */
export function stateTone(state) {
  switch (state) {
    case 'assigned':
      return 'success'
    case 'needs_choice':
      return 'warning'
    default:
      return 'neutral'
  }
}

/** The books a deal may be moved to: the ones its rules matched. */
export function candidateBooks(candidates) {
  return (candidates || [])
    .map((entry) => entry.price_book)
    .filter((book) => book && (book.id || book.name))
}

/** A short label for a price book reference, for a dropdown option. */
export function bookLabel(book) {
  if (!book) return 'None'
  return book.name || book.id || 'None'
}