/**
 * WF-063 API calls.
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a
 * method to it is exactly the conflict the feature host exists to remove. The
 * host's `apiRequest` is the escape hatch that makes it unnecessary.
 *
 * `PREFIX` is the feature's own: the backend module mounts these under
 * `/api/wf-063`, and `apiRequest` already prepends `/api`.
 *
 * The pickers on the page are rendered from `/vocabulary` and `/outcomes` rather
 * than from lists compiled into this file, so a term the backend adds reaches
 * the page with no change here. That is the whole point of serving the researched
 * vocabulary as data.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-063'

/**
 * Build a query string, dropping anything empty.
 *
 * Empty values are dropped rather than sent as `?tab=`, because a page that
 * flickers between "filter on nothing" and "no filter" when a field is cleared
 * is a page nobody trusts.
 */
export function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue
    search.set(key, Array.isArray(value) ? value.join(',') : String(value))
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const reassignApi = {
  /** The researched vocabulary: surfaces, the editable axis, the two locks, the quotes. */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),

  /** The ten decision outcomes, each with the researched sentence behind it. */
  outcomes: () => apiRequest(`${PREFIX}/outcomes`),

  /** The two webhooks a reassignment fires, and the scope rule for each. */
  webhooks: () => apiRequest(`${PREFIX}/webhooks`),

  /**
   * Every design decision the research does not fix, with what was quoted and
   * what was chosen. The page renders this from the server rather than from copy
   * in this folder, so the page cannot drift from the registry a reviewer is
   * asked to disagree with.
   */
  inferences: () => apiRequest(`${PREFIX}/inferences`),

  distributions: () => apiRequest(`${PREFIX}/distributions`),
  hosts: (params = {}) => apiRequest(`${PREFIX}/hosts${query(params)}`),

  /** The Meetings Activity list: the Upcoming and Past tabs, and the five filters. */
  meetings: (roomId, params = {}) => apiRequest(`${PREFIX}/rooms/${roomId}/meetings${query(params)}`),

  meeting: (roomId, meetingId) => apiRequest(`${PREFIX}/rooms/${roomId}/meetings/${meetingId}`),

  /**
   * The researched "Export to CSV". A plain browser navigation rather than a
   * fetch, because the response is a file and there is nothing to parse.
   */
  exportUrl: (roomId, tab) => `/api${PREFIX}/rooms/${roomId}/meetings/export.csv${query({ tab })}`,

  /** The researched "known and free" pick: every host, with why the others cannot. */
  availability: (roomId, meetingId, params = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/meetings/${meetingId}/availability${query(params)}`),

  /** Run the decision and report it. Writes nothing. */
  preview: (roomId, meetingId, payload = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/meetings/${meetingId}/preview`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /** The workflow, end to end. */
  reassign: (roomId, meetingId, payload = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/meetings/${meetingId}/reassign`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  reassignments: (roomId, params = {}) => apiRequest(`${PREFIX}/rooms/${roomId}/reassignments${query(params)}`),

  /** The researched Events History tab: who, to whom, when, and the source. */
  eventsHistory: (roomId, params = {}) => apiRequest(`${PREFIX}/rooms/${roomId}/events-history${query(params)}`),

  /** One meeting's history, plus whether its notice window has already closed. */
  meetingHistory: (roomId, meetingId) => apiRequest(`${PREFIX}/rooms/${roomId}/meetings/${meetingId}/history`),

  summary: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/summary`),
}
