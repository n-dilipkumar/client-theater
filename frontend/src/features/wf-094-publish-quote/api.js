/**
 * WF-094's own API wrapper.
 *
 * `apiRequest` from `@/lib/api` is the transport, exactly as the contract asks.
 * The shared `api` object grows no methods, so a hundred features can each talk
 * to their own `/api/WF-094` routes without anyone editing a shared file.
 *
 * Nothing here needs to reach past it. `apiRequest` reads the error body for its
 * message and keeps the status on the thrown error, which is the one field this
 * workflow branches on: a 422 carries a rule refusal whose detail is worth
 * showing beside the control that caused it, and a 404 or a 500 is a different
 * problem entirely.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/WF-094'

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

function send(path, method, payload) {
  return apiRequest(`${BASE}${path}`, { method, body: JSON.stringify(payload) })
}

export const quoteApi = {
  /** The statuses, the caps and the ceilings, from one place on the server. */
  vocabulary: () => call('/vocabulary'),

  summary: (roomId) => call('/summary', { room_id: roomId }),

  settings: (roomId) => call('/settings', { room_id: roomId }),

  quotes: (roomId, status) => call('/quotes', { room_id: roomId, status }),

  quote: (quoteId) => call(`/quotes/${encode(quoteId)}`),

  /**
   * Publish, or share without publishing.
   *
   * The body carries no link, no slug and no domain. The server computes those
   * and refuses a body that tries to set them, so a caller here cannot offer a
   * field the workflow will not honour.
   */
  publish: (quoteId, { sharedOnly = false, actor } = {}) =>
    send(`/quotes/${encode(quoteId)}/publish`, 'POST', { shared_only: sharedOnly, actor }),

  unlock: (quoteId, target, actor) =>
    send(`/quotes/${encode(quoteId)}/unlock`, 'POST', { target, actor }),

  copyLink: (quoteId, actor) => send(`/quotes/${encode(quoteId)}/link`, 'POST', { actor }),

  sendEmail: (quoteId, payload) => send(`/quotes/${encode(quoteId)}/emails`, 'POST', payload),

  requestPdf: (quoteId, payload = {}) => send(`/quotes/${encode(quoteId)}/pdf`, 'POST', payload),

  activity: (quoteId) => call(`/quotes/${encode(quoteId)}/activity`),
}

/** Every room, so the page can offer one. Read from the core collection. */
export const listRooms = () => apiRequest('/records/room?limit=100')

/**
 * The quote statuses, with what a seller can do to each one.
 *
 * The `canPublish` and `canUnlock` flags mirror the server's `derived` block,
 * which is the authority. They are duplicated here for the same reason a form's
 * trigger has to be spelled locally: a control whose enabled state arrives after
 * the first paint is a control a seller clicks before it works. The server
 * refuses the action either way, so a stale flag costs one error message rather
 * than a wrong write.
 */
export const QUOTE_STATUSES = [
  { id: 'DRAFT', label: 'Draft', tone: 'neutral', canPublish: true, canUnlock: false },
  {
    id: 'PENDING_APPROVAL',
    label: 'Pending approval',
    tone: 'warning',
    canPublish: true,
    canUnlock: false,
  },
  { id: 'REJECTED', label: 'Rejected', tone: 'destructive', canPublish: true, canUnlock: false },
  { id: 'SHARED', label: 'Shared, total editable', tone: 'info', canPublish: true, canUnlock: false },
  { id: 'PUBLISHED', label: 'Published, total locked', tone: 'insert', canPublish: false, canUnlock: true },
]

/** The label for a status id, for a screen that has only the id. */
export function statusLabel(status) {
  return QUOTE_STATUSES.find((row) => row.id === status)?.label || status || 'Unknown'
}

/** The badge tone for a status id. */
export function statusTone(status) {
  return QUOTE_STATUSES.find((row) => row.id === status)?.tone || 'neutral'
}

/** Whether a status means the quote's figures can no longer be edited. */
export function isLocked(quote) {
  return quote?.data?.hs_locked === true
}

/** Whether the quote has a link a seller could hand to a buyer. */
export function isShareable(quote) {
  return Boolean(quote?.data?.hs_quote_link)
}

/**
 * Whether the email PDF would be dropped for being too large.
 *
 * The cap is 20 MB in the researched platform and the send still goes out, so a
 * panel that hid the fact would teach a seller that the email failed.
 */
export function exceedsAttachmentCap(sizeBytes, capBytes) {
  if (sizeBytes === undefined || sizeBytes === null || sizeBytes === '') return false
  if (!capBytes) return false
  return Number(sizeBytes) > capBytes
}

/** A megabyte figure with one decimal, for the sentence that explains a dropped PDF. */
export function megabytes(sizeBytes) {
  return (Number(sizeBytes || 0) / (1024 * 1024)).toFixed(1)
}