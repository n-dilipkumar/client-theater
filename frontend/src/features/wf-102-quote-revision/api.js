/**
 * WF-102's own API wrapper.
 *
 * `apiRequest` from `@/lib/api` is the transport, exactly as the feature
 * contract asks. The shared `api` object grows no methods, so a hundred features
 * each talk to their own `/api/wf-102` routes without anyone editing a shared
 * file.
 *
 * The generic records route is used only to list rooms and quotes for the
 * pickers. Everything this workflow does goes through its own routes.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-102'

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

function send(path, method, payload, params) {
  return apiRequest(`${BASE}${path}${query(params)}`, {
    method,
    body: JSON.stringify(payload),
  })
}

export const revisionApi = {
  /** The statuses, close reasons, option values and refusal reasons, from one place. */
  vocabulary: () => call('/vocabulary'),

  summary: (roomId) => call('/summary', { room_id: roomId }),

  quotes: (roomId, status) => call('/quotes', { room_id: roomId, status }),

  quote: (quoteId) => call(`/quotes/${encode(quoteId)}`),

  lineage: (quoteId) => call(`/quotes/${encode(quoteId)}/lineage`),

  activity: (quoteId) => call(`/quotes/${encode(quoteId)}/activity`),

  revise: (quoteId, body = {}) => send(`/quotes/${encode(quoteId)}/revise`, 'POST', body),

  close: (quoteId, reason) => send(`/quotes/${encode(quoteId)}/close`, 'POST', { reason }),

  clone: (quoteId, body = {}) => send(`/quotes/${encode(quoteId)}/clone`, 'POST', body),

  reprice: (quoteId) => send(`/quotes/${encode(quoteId)}/reprice`, 'POST', {}),

  recall: (quoteId) => send(`/quotes/${encode(quoteId)}/recall`, 'POST', {}),

  republish: (quoteId) => send(`/quotes/${encode(quoteId)}/republish`, 'POST', {}),
}

/** Quotes to offer in a picker. Read from the WF-086 collection. */
export const listQuotes = (roomId) =>
  apiRequest(`/records/wf086_quote?limit=100${roomId ? `&room_id=${encode(roomId)}` : ''}`)

/** Rooms to offer in a picker. Read from the core collection. */
export const listRooms = () => apiRequest('/records/room?limit=100')

/** The five statuses this workflow reads, in words, so a colour is never the only signal. */
export const STATUS_LABELS = {
  draft: 'Draft',
  published: 'Active',
  revised: 'Revised',
  lost: 'Lost',
  canceled: 'Canceled',
}

export function statusLabel(status) {
  return STATUS_LABELS[status] || status || 'Unknown'
}

/** The badge tone for a status. Always paired with `statusLabel`. */
export function statusTone(status) {
  if (status === 'lost' || status === 'canceled') return 'delete'
  if (status === 'revised') return 'update'
  if (status === 'published') return 'restore'
  if (status === 'draft') return 'insert'
  return 'neutral'
}

/** The three close reasons, in the order the research lists them. */
export const CLOSE_REASONS = ['lost', 'canceled', 'revised']

/**
 * Whether a revise control should be offered.
 *
 * The server refuses either way; this only decides which control is live. A
 * quote is revisable while it is open, has line items, and is not a project
 * quote.
 */
export function canRevise(quote) {
  if (!quote) return false
  return Boolean(quote.open && (quote.line_items || []).length > 0 && !isProjectQuote(quote))
}

export function isProjectQuote(quote) {
  return quote?.quote_scope === 'project_quote'
}

/** Why a quote cannot be revised, in words, or null when it can. */
export function reviseBlockedReason(quote) {
  if (!quote) return 'Select a quote.'
  if (isProjectQuote(quote)) return 'A project quote does not support revisions.'
  if (!quote.open) return 'This quote is closed. Clone it to start a new version.'
  if ((quote.line_items || []).length === 0) return 'Add a line item before revising.'
  return null
}

/** Why a quote cannot be recalled, in words, or null when it can. */
export function recallBlockedReason(quote) {
  if (!quote) return 'Select a quote.'
  if (quote.status !== 'published') return 'Only an active quote can be recalled.'
  if (quote.online_payments_enabled || quote.esignature_enabled) {
    return 'Online payments or e-signature are enabled, so this quote cannot be restored.'
  }
  const blocking = ['approved', 'accepted', 'paid']
  const states = [
    quote.hs_status,
    quote.quote_status,
    quote.payment_status,
    quote.hs_payment_status,
  ].map((value) => String(value || '').toLowerCase())
  if (states.some((value) => blocking.includes(value))) {
    return 'This quote is approved, accepted or paid, so it cannot be recalled.'
  }
  return null
}

/** The pricing sentence for a quote, from its own mode rather than a guess. */
export function pricingNote(quote) {
  if (!quote?.pricing) return ''
  return quote.pricing.mode === 'live'
    ? 'Draft. Prices follow the current price list.'
    : 'Active or closed. Prices are frozen.'
}
