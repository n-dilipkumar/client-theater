/**
 * WF-129's own API wrapper.
 *
 * `apiRequest` from `@/lib/api` is the transport, exactly as the feature
 * contract asks. The shared `api` object grows no methods, so a hundred
 * features can each talk to their own `/api/wf-129` routes without anyone
 * editing a shared file.
 *
 * The generic records route is used only to offer quotes and rooms in a
 * picker. Everything this workflow does goes through its own routes.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-129'

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

export const orderFormApi = {
  /** The words this workflow enforces, from one place. */
  vocabulary: () => call('/vocabulary'),

  /** Every judgement call the research left open, with the alternative rejected. */
  decisions: () => call('/decisions'),

  summary: (roomId) => call('/summary', { room_id: roomId }),

  orderForms: (roomId) => call('/order-forms', { room_id: roomId }),

  orderForm: (formId) => call(`/order-forms/${encode(formId)}`),

  /** Render a quote as an itemised, buyer-editable order form inside the room. */
  create: (roomId, body) => send('/order-forms', 'POST', body, { room_id: roomId }),

  updateLine: (formId, lineId, patch) =>
    send(`/order-forms/${encode(formId)}/lines/${encode(lineId)}`, 'PATCH', patch),

  revisions: (formId) => call(`/order-forms/${encode(formId)}/revisions`),

  proposeRevision: (formId, body) => send(`/order-forms/${encode(formId)}/revisions`, 'POST', body),

  resolveRevision: (revisionId, decision) =>
    send(`/revisions/${encode(revisionId)}/resolve`, 'POST', { decision }),

  accept: (formId, body = {}) => send(`/order-forms/${encode(formId)}/accept`, 'POST', body),

  sign: (formId, body) => send(`/order-forms/${encode(formId)}/sign`, 'POST', body),

  file: (formId) => send(`/order-forms/${encode(formId)}/file`, 'POST', {}),

  certificate: (formId) => call(`/order-forms/${encode(formId)}/certificate`),
}

/** Quotes to offer in a picker. Read from the WF-086 collection. */
export const listQuotes = () => apiRequest('/records/wf086_quote?limit=50')

/** Rooms to offer in a picker. Read from the core collection. */
export const listRooms = () => apiRequest('/records/room?limit=100')

/**
 * The label and tone for an order form status, in words.
 *
 * The tone is never the only signal: every badge carries this label too, so a
 * colour-blind buyer still reads which state the form is in.
 */
export const STATUS_LABELS = {
  draft: { label: 'Draft', tone: 'update' },
  accepted: { label: 'Accepted, awaiting signature', tone: 'insert' },
  signed: { label: 'Signed', tone: 'insert' },
  filed: { label: 'Filed on the deal', tone: 'insert' },
}

export function statusLabel(status) {
  return (STATUS_LABELS[status] || { label: status || 'Unknown', tone: 'neutral' }).label
}

export function statusTone(status) {
  return (STATUS_LABELS[status] || { tone: 'neutral' }).tone
}

/** The label for a signature mode, in words. */
export const MODE_LABELS = {
  typed: 'Typed',
  drawn: 'Drawn',
  typed_photo: 'Typed and photographed',
}

export function modeLabel(mode) {
  return MODE_LABELS[mode] || mode || 'Unknown'
}

/** Whether a form's lines can still be edited or redlined. */
export function canEdit(form) {
  return Boolean(form && form.status === 'draft')
}

/** Whether a form can be accepted. */
export function canAccept(form) {
  return Boolean(form && form.status === 'draft')
}

/** Whether a form can be signed. The buyer accepts first. */
export function canSign(form) {
  return Boolean(form && form.status === 'accepted')
}

/** Whether a form can be filed on the deal. */
export function canFile(form) {
  return Boolean(form && form.status === 'signed')
}
