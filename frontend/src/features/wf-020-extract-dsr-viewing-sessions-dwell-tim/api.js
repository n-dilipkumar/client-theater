/**
 * Viewing sessions API calls (WF-020).
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a
 * method to it is precisely the conflict the feature host exists to remove.
 * `apiRequest` is the host's generic escape hatch: it handles the envelope and
 * puts the HTTP status on the thrown error, which this page needs in order to
 * tell "a sweep is not due yet" (409) from "the call failed".
 *
 * The export route is the one exception to the JSON-only convention: it serves
 * both documented `Accept` values, so it is fetched rather than parsed and
 * handed to the browser as a blob.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-020'

function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value))
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const sessionsApi = {
  /** The machine-readable extraction contract, with its evidence block. */
  contract: () => apiRequest(`${PREFIX}/contract`),

  /** The next modifiedAt page, the query to send, and whether a sweep is due. */
  window: (params = {}) => apiRequest(`${PREFIX}/window${query(params)}`),

  /** Open the next extraction, or 409 while the refresh SLA has not elapsed. */
  sweep: (params = {}) => apiRequest(`${PREFIX}/sweep${query(params)}`, { method: 'POST' }),

  /** Land a batch of rows the ETL job pulled. */
  extract: (payload) =>
    apiRequest(`${PREFIX}/extract`, { method: 'POST', body: JSON.stringify(payload) }),

  runs: (params = {}) => apiRequest(`${PREFIX}/runs${query(params)}`),
  run: (runId) => apiRequest(`${PREFIX}/runs/${runId}`),

  rooms: (params = {}) => apiRequest(`${PREFIX}/rooms${query(params)}`),
  landRooms: (payload) =>
    apiRequest(`${PREFIX}/rooms`, { method: 'POST', body: JSON.stringify(payload) }),

  sessions: (params = {}) => apiRequest(`${PREFIX}/sessions${query(params)}`),
  summary: (params = {}) => apiRequest(`${PREFIX}/summary${query(params)}`),
  dwell: (params = {}) => apiRequest(`${PREFIX}/dwell${query(params)}`),
  geography: (params = {}) => apiRequest(`${PREFIX}/geography${query(params)}`),
  viewers: (params = {}) => apiRequest(`${PREFIX}/viewers${query(params)}`),

  roomSessions: (roomId, params = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/sessions${query(params)}`),

  /**
   * The documented `Accept: text/csv` alternative, as a download.
   *
   * Fetched rather than linked because a plain <a> cannot set the header, and the
   * header is the documented mechanism.
   */
  async downloadCsv(params = {}) {
    const response = await fetch(`/api${PREFIX}/export${query({ ...params, format: 'csv' })}`, {
      headers: { Accept: 'text/csv' },
    })
    if (!response.ok) {
      throw new Error(`${response.status} ${response.statusText}`)
    }
    const blob = await response.blob()
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = 'dsr-viewing-sessions.csv'
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
  },
}
