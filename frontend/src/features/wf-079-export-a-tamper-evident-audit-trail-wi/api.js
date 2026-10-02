import { apiRequest } from '@/lib/api'

/**
 * The client for WF-079.
 *
 * Two things about this workflow's HTTP surface are worth stating once, here,
 * rather than at every call site.
 *
 * **The caller is a query parameter, not a header.** The researched rule is that
 * the export is "accessible to authorized workspace administrators only", and this
 * API takes `?role=`. That is the same trust model as the `X-Role` header the other
 * features use - both are a claim the caller makes, and neither is authentication -
 * but it means every call carries its role in the URL, including the download link
 * a compliance recipient follows. Building it here keeps that from being re-derived
 * per call.
 *
 * **Dates are `MM/DD/YYYY` text, not `type="date"`.** The source specifies that
 * format on the wire and rejects ISO. A native date input hands back `YYYY-MM-DD`,
 * which this API answers `invalid_date_format` to, so the page uses a text field
 * with the format in its label and hint rather than a control that cannot express
 * what it needs to send.
 */

const BASE = '/wf-079'

/** Drop empty values so a filter the operator cleared does not become `?actor=`. */
function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue
    search.set(key, String(value))
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

/** The source's date format, formatted from a Date. */
export function mmddyyyy(date) {
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${month}/${day}/${date.getFullYear()}`
}

/** A default range: thirty days back to today, which is inside every researched bound. */
export function thirtyDayRange() {
  const today = new Date()
  const start = new Date(today)
  start.setDate(start.getDate() - 30)
  return { startDate: mmddyyyy(start), endDate: mmddyyyy(today) }
}

export const vocabulary = () => apiRequest(`${BASE}/vocabulary`)

export const inferences = () => apiRequest(`${BASE}/inferences`)

export const rooms = () => apiRequest('/records/room?limit=200')

export const summary = (roomId, params) =>
  apiRequest(`${BASE}/rooms/${roomId}/audit-trail/summary${query(params)}`)

export const trail = (roomId, params) =>
  apiRequest(`${BASE}/rooms/${roomId}/audit-trail${query(params)}`)

export const verifyTrail = (roomId, params) =>
  apiRequest(`${BASE}/rooms/${roomId}/audit-trail/verify${query(params)}`)

export const pinAnchor = (roomId, params) =>
  apiRequest(`${BASE}/rooms/${roomId}/audit-trail/anchors${query(params)}`, { method: 'POST' })

export const documentTrail = (roomId, recordId, params) =>
  apiRequest(`${BASE}/rooms/${roomId}/documents/${recordId}/audit-trail${query(params)}`)

export const evidencePack = (roomId, recordId, params) =>
  apiRequest(`${BASE}/rooms/${roomId}/documents/${recordId}/evidence-pack${query(params)}`)

export const reports = (params) => apiRequest(`${BASE}/reports${query(params)}`)

export const requestReports = (roomId, payload, params) =>
  apiRequest(`${BASE}/reports${query({ ...params, room_id: roomId })}`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })

export const generateReport = (reportId, params) =>
  apiRequest(`${BASE}/reports/${reportId}/generate${query(params)}`, { method: 'POST' })

/**
 * The download link a report's delivery record issued.
 *
 * A plain href rather than a fetch: the recipient of a compliance report is a
 * person opening a browser, so the response has to be a file with a filename and a
 * content type rather than JSON this page would have to turn into a blob.
 */
export const downloadUrl = (reportId, token, params) =>
  `${BASE}/reports/${reportId}/download${query({ ...params, token })}`