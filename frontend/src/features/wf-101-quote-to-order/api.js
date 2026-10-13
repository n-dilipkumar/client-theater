/**
 * WF-101's own API wrapper.
 *
 * `apiRequest` from `@/lib/api` is the transport, exactly as the feature
 * contract asks. The shared `api` object grows no methods, so a hundred features
 * can each talk to their own `/api/wf-101` routes without anyone editing a
 * shared file.
 *
 * The generic records route is used only to list quotes and rooms to offer in a
 * picker. Everything this workflow does goes through its own routes.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-101'

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
    body: JSON.stringify(payload ?? {}),
  })
}

export const orderApi = {
  /** The words, modes, settings and refusal reasons, from one place. */
  vocabulary: () => call('/vocabulary'),

  /** Every judgement call this workflow made, with the alternative it rejected. */
  decisions: () => call('/decisions'),

  summary: (roomId) => call('/summary', { room_id: roomId }),

  settings: (roomId) => call('/settings', { room_id: roomId }),

  updateSettings: (roomId, patch) => send('/settings', 'POST', patch, { room_id: roomId }),

  orders: (roomId) => call('/orders', { room_id: roomId }),

  order: (orderId) => call(`/orders/${encode(orderId)}`),

  quoteReadiness: (quoteId) => call(`/quotes/${encode(quoteId)}/readiness`),

  /** Convert the accepted and activated quote into an order. */
  convert: (quoteId, body = {}) => send(`/quotes/${encode(quoteId)}/order`, 'POST', body),

  setPricingMode: (orderId, mode) =>
    send(`/orders/${encode(orderId)}/pricing-mode`, 'POST', { mode }),

  reprice: (orderId) => send(`/orders/${encode(orderId)}/reprice`, 'POST', {}),

  addLine: (orderId, body) => send(`/orders/${encode(orderId)}/lines`, 'POST', body),

  updateLine: (orderId, lineId, patch) =>
    send(`/orders/${encode(orderId)}/lines/${encode(lineId)}`, 'PATCH', patch),

  closeOrder: (orderId, status, reason) =>
    send(`/orders/${encode(orderId)}/close`, 'POST', { status, reason }),

  getProducts: (orderId, body) => send(`/orders/${encode(orderId)}/products`, 'POST', body),
}

/** Quotes to offer in a picker. Read from the WF-086 collection. */
export const listQuotes = () => apiRequest('/records/wf086_quote?limit=50')

/** Rooms to offer in a picker. Read from the core collection. */
export const listRooms = () => apiRequest('/records/room?limit=100')

/**
 * The label and tone for an order status, in words.
 *
 * The tone is never the only signal: every badge carries this label too, so a
 * colour-blind seller still reads which status the order is in.
 */
export const STATUS_LABELS = {
  open: { label: 'Open', tone: 'update' },
  fulfilled: { label: 'Fulfilled', tone: 'insert' },
  cancelled: { label: 'Cancelled', tone: 'delete' },
}

export function statusLabel(status) {
  return (STATUS_LABELS[status] || { label: status || 'Unknown', tone: 'neutral' }).label
}

export function statusTone(status) {
  return (STATUS_LABELS[status] || { tone: 'neutral' }).tone
}

/**
 * Whether a readiness answer means a conversion can run now.
 *
 * A quote is ready when it is accepted and activated, the setting is on and no
 * order exists yet. The server refuses either way; this only decides which
 * control the page offers.
 */
export function canConvert(readiness) {
  if (!readiness) return false
  return Boolean(
    readiness.activated &&
      readiness.accepted &&
      !readiness.existing_order_id &&
      readiness.refusal === null,
  )
}

/** The reason a quote is not ready, in words, or null when it is. */
export function blockedReason(readiness) {
  if (!readiness) return 'Select a quote.'
  if (readiness.existing_order_id) return 'This quote already produced an order.'
  if (!readiness.activated) return 'Activate the quote before it can be converted.'
  if (!readiness.accepted) return 'Record the acceptance before converting the quote.'
  if (readiness.refusal) {
    return readiness.refusal_reason || 'The conversion is blocked.'
  }
  return null
}
