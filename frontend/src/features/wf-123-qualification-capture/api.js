/**
 * WF-123's own API wrapper and its pure helpers.
 *
 * `apiRequest` from `@/lib/api` is the transport, exactly as the feature
 * contract asks: the shared `api` object grows no methods, so this feature talks
 * to its own `/api/wf-123` routes without anyone editing a shared file.
 *
 * The generic records route is used only to list rooms for the picker. Every
 * write and every read of a form, a submission or the summary goes through this
 * workflow's own routes.
 *
 * The helpers below are pure functions of their arguments, so the page's labels
 * and its buyer-view guard can be checked without a browser or a server.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-123'

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
  return apiRequest(`${BASE}${path}`, { method, body: JSON.stringify(payload || {}) })
}

export const qualificationApi = {
  vocabulary: () => call('/vocabulary'),
  inferences: () => call('/inferences'),

  forms: (params) => call('/forms', params),
  form: (formId) => call(`/forms/${encode(formId)}`),
  createForm: (body) => send('/forms', 'POST', body),
  updateForm: (formId, body) => send(`/forms/${encode(formId)}`, 'PATCH', body),
  publishForm: (formId) => send(`/forms/${encode(formId)}/publish`, 'POST', {}),
  deleteForm: (formId) => send(`/forms/${encode(formId)}`, 'DELETE', {}),
  restoreForm: (formId) => send(`/forms/${encode(formId)}/restore`, 'POST', {}),

  roomForm: (roomId) => call(`/rooms/${encode(roomId)}/form`),
  autosave: (roomId, formId, body) =>
    send(`/rooms/${encode(roomId)}/forms/${encode(formId)}/autosave`, 'POST', body),
  submit: (roomId, formId, body) =>
    send(`/rooms/${encode(roomId)}/forms/${encode(formId)}/submit`, 'POST', body),

  submissions: (params) => call('/submissions', params),
  submission: (submissionId) => call(`/submissions/${encode(submissionId)}`),
  routeSubmission: (submissionId) => send(`/submissions/${encode(submissionId)}/route`, 'POST', {}),

  routes: (params) => call('/routes', params),
  qualification: (roomId) => call(`/rooms/${encode(roomId)}/qualification`),
}

/** Rooms to offer in a picker. Read from the core collection. */
export const listRooms = () => apiRequest('/records/room?limit=100')

/**
 * Every field the buyer must never receive. This mirrors the backend's
 * `SELLER_ONLY_FIELDS`; the page uses it as a local guard so a seller view that
 * leaked one would be caught in the browser rather than shown to a buyer.
 */
export const SELLER_ONLY_FIELDS = [
  'score',
  'max_score',
  'band',
  'band_label',
  'routing',
  'route_target',
  'routed',
]

const DIMENSION_LABELS = {
  budget: 'Budget',
  timeline: 'Timeline',
  authority: 'Authority',
  procurement_path: 'Procurement path',
}

const BAND_LABELS = {
  qualified: 'Qualified',
  developing: 'Developing',
  unqualified: 'Unqualified',
}

const ROUTE_LABELS = {
  account_executive: 'Account executive',
  sales_development: 'Sales development',
  nurture: 'Nurture',
}

const FORM_STATUS_LABELS = { draft: 'Draft', live: 'Live' }
const SUBMISSION_STATUS_LABELS = { draft: 'In progress', submitted: 'Submitted' }

export function dimensionLabel(dimension) {
  return DIMENSION_LABELS[dimension] || dimension || 'Dimension'
}

export function bandLabel(band) {
  return BAND_LABELS[band] || band || 'Unscored'
}

/** The badge tone for a band. Always shown beside its text label. */
export function bandTone(band) {
  if (band === 'qualified') return 'insert'
  if (band === 'developing') return 'update'
  return 'neutral'
}

export function routeLabel(target) {
  return ROUTE_LABELS[target] || target || 'Not routed'
}

export function formStatusLabel(status) {
  return FORM_STATUS_LABELS[status] || status || 'Unknown'
}

export function submissionStatusLabel(status) {
  return SUBMISSION_STATUS_LABELS[status] || status || 'Unknown'
}

/** Whether a submission has been scored. A draft has no band. */
export function isScored(submission) {
  return Boolean(submission) && submission.band !== null && submission.band !== undefined
}

/** A score as a share of its ceiling, rounded to a whole percent. */
export function scorePercent(score, maxScore) {
  if (!maxScore || score === null || score === undefined) return 0
  const ratio = Number(score) / Number(maxScore)
  if (Number.isNaN(ratio)) return 0
  return Math.max(0, Math.min(100, Math.round(ratio * 100)))
}

/** Format an ISO timestamp or epoch milliseconds as a compact local string. */
export function formatDateTime(value) {
  if (value === null || value === undefined || value === '') return '-'
  const parsed = typeof value === 'number' ? new Date(value) : new Date(String(value))
  if (Number.isNaN(parsed.getTime())) return '-'
  return parsed.toLocaleString()
}

/** The label for one answered option, read from the form's own option list. */
export function answerLabel(question, value) {
  if (!question || !Array.isArray(question.options)) return value || '-'
  const match = question.options.find((option) => option.value === value)
  return match ? match.label : value || '-'
}

/** True when a response carries any field a buyer must never receive. */
export function buyerViewHasSellerOnly(view) {
  if (!view || typeof view !== 'object') return false
  return SELLER_ONLY_FIELDS.some((field) => field in view)
}

/** A copy of a view with every seller-only field removed. */
export function stripSellerOnly(view) {
  if (!view || typeof view !== 'object') return view
  const copy = { ...view }
  for (const field of SELLER_ONLY_FIELDS) delete copy[field]
  return copy
}

/** The band counts of a summary, in the research's band order. */
export function bandBreakdown(summary) {
  const counts = (summary && summary.by_band) || {}
  return ['qualified', 'developing', 'unqualified'].map((band) => ({
    band,
    label: bandLabel(band),
    count: counts[band] || 0,
  }))
}
