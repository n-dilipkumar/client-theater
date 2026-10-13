/**
 * Nurture series API calls (WF-105).
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a
 * method to it is precisely the conflict the feature host exists to remove. The
 * host's `apiRequest` is the escape hatch that makes that unnecessary.
 *
 * `PREFIX` is the feature's own: the backend module mounts these under
 * `/api/wf105`, and `apiRequest` already prepends `/api`.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf105'

function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value))
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const seriesApi = {
  /** The researched vocabulary: the poll cadence, the toggle, the tag block, the exit. */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),

  /** What the research leaves open, what this build chose, and how to change it. */
  inferences: () => apiRequest(`${PREFIX}/inferences`),

  /** Counts for the page header, over exactly the rows the filters return. */
  summary: (params = {}) => apiRequest(`${PREFIX}/summary${query(params)}`),

  /** The series, tags, enrollments and tag applications the poll reads. */
  catalog: (params = {}) => apiRequest(`${PREFIX}/catalog${query(params)}`),

  tags: (params = {}) => apiRequest(`${PREFIX}/tags${query(params)}`),
  createTag: (payload) =>
    apiRequest(`${PREFIX}/tags`, { method: 'POST', body: JSON.stringify(payload) }),

  series: (params = {}) => apiRequest(`${PREFIX}/series${query(params)}`),
  seriesOne: (id) => apiRequest(`${PREFIX}/series/${id}`),
  createSeries: (payload) =>
    apiRequest(`${PREFIX}/series`, { method: 'POST', body: JSON.stringify(payload) }),
  publishSeries: (id) => apiRequest(`${PREFIX}/series/${id}/publish`, { method: 'POST' }),

  /** Run one poll: evaluate entry, and progression when an event asks for it. */
  pollSeries: (id, events) =>
    apiRequest(`${PREFIX}/series/${id}/poll`, {
      method: 'POST',
      body: JSON.stringify({ events }),
    }),

  advance: (seriesId, contactId, contact) =>
    apiRequest(`${PREFIX}/series/${seriesId}/contacts/${contactId}/advance`, {
      method: 'POST',
      body: JSON.stringify({ contact }),
    }),

  exit: (seriesId, contactId, contact) =>
    apiRequest(`${PREFIX}/series/${seriesId}/contacts/${contactId}/exit`, {
      method: 'POST',
      body: JSON.stringify({ contact }),
    }),

  enrollments: (params = {}) => apiRequest(`${PREFIX}/enrollments${query(params)}`),
  tagApplications: (params = {}) => apiRequest(`${PREFIX}/tag-applications${query(params)}`),
  evaluations: (params = {}) => apiRequest(`${PREFIX}/evaluations${query(params)}`),
}

/**
 * The words and badge tone for an enrollment state.
 *
 * The state is always rendered as a word, so a reader who cannot distinguish the
 * tones gets the same answer. Colour is a second signal, never the only one.
 */
export function stateBadge(state) {
  const spec = {
    enrolled: { label: 'Enrolled', tone: 'insert' },
    completed: { label: 'Completed', tone: 'update' },
    exited: { label: 'Exited (cannot re-enter)', tone: 'delete' },
  }[String(state)]
  return spec || { label: String(state || 'Unknown'), tone: 'neutral' }
}

/** The words for an entry outcome, so a skip reads as a decision, not a failure. */
export function outcomeLabel(outcome) {
  const known = {
    enrolled: 'Enrolled',
    reentered: 'Re-entered',
    skipped_already_active: 'Skipped: already active',
    skipped_exited_sticky: 'Skipped: exited, cannot re-enter',
    skipped_no_match: 'Skipped: did not match',
    skipped_reenter_disabled: 'Skipped: re-entry is off',
    skipped_not_live: 'Skipped: series is not live',
  }
  return known[String(outcome)] || String(outcome || 'Unknown')
}

/** The words for a series state. */
export function seriesStateLabel(state) {
  const known = { draft: 'Draft', live: 'Live', paused: 'Paused' }
  return known[String(state)] || String(state || 'Unknown')
}
