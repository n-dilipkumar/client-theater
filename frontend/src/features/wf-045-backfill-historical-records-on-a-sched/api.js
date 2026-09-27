/**
 * Backfill history API (WF-045).
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a
 * method to it is precisely the collision the feature host exists to remove.
 * The host's `apiRequest` is the escape hatch that makes that unnecessary.
 *
 * `PREFIX` is the feature's own: the backend module mounts these under
 * `/api/wf-045`, and `apiRequest` already prepends `/api`.
 *
 * Every payload is sent in the research's own spelling - `objectName`,
 * `objectTypeId`, `from`, `to`, `fieldMap` - so the documented flow can be
 * followed against the API directly and a reader can match what the page sends
 * against what the research says should be sent. The room list is read from the
 * core records route, so this page depends on the HTTP contract rather than on
 * another module's idea of the shape of a room.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-045'

/** Drop empty values so we never send `?state=` and confuse a filter. */
function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const backfillApi = {
  /**
   * The published vocabularies: the strategies and what each one means, the
   * directions, the run states and which are terminal, the per-run log's event
   * kinds, the cursor kinds, and the three researched numbers with the sentence
   * that fixes each one. Every picker on the page renders from this rather than
   * from a list compiled into this file.
   */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),

  /**
   * Every judgement call the workflow rests on, and how to change each one.
   * The research fixes the numbers; it does not say what a room does when two of
   * its rules disagree, so the edges are served as data rather than left for a
   * reader to reconstruct from a diff.
   */
  inferences: () => apiRequest(`${PREFIX}/inferences`),

  /** The registered vendor adapters and what each implements. */
  vendors: () => apiRequest(`${PREFIX}/vendors`),

  connections: (params = {}) => apiRequest(`${PREFIX}/connections${query(params)}`),
  createConnection: (payload) =>
    apiRequest(`${PREFIX}/connections`, { method: 'POST', body: JSON.stringify(payload) }),
  connection: (id) => apiRequest(`${PREFIX}/connections/${id}`),

  runs: (roomId, params = {}) => apiRequest(`${PREFIX}/rooms/${roomId}/backfills${query(params)}`),
  start: (roomId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/backfills`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  run: (roomId, runId) => apiRequest(`${PREFIX}/rooms/${roomId}/backfills/${runId}`),
  log: (roomId, runId, params = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/backfills/${runId}/log${query(params)}`),

  /**
   * The scheduler's route. It respects the run's fixed interval and answers
   * `not_due` rather than asking the vendor again, because the research
   * documents a *daily* limit on exactly this vendor.
   */
  poll: (roomId, runId) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/backfills/${runId}/poll`, { method: 'POST' }),

  /**
   * The recovery route. It forces the poll, because a room that has just
   * restarted has not been respecting anybody's poll interval. A cursor the
   * vendor has aged out comes back as a 409 and the run is marked `stalled` -
   * the message names the backfill to open instead.
   */
  resume: (roomId, runId) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/backfills/${runId}/resume`, { method: 'POST' }),

  cancel: (roomId, runId, payload = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/backfills/${runId}/cancel`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  cursors: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/cursors`),
  replica: (roomId, params = {}) => apiRequest(`${PREFIX}/rooms/${roomId}/replica${query(params)}`),
  summary: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/backfill-summary`),

  rooms: () => apiRequest('/records/room?limit=100'),
}

/**
 * The progress figure, as something a bar can render.
 *
 * The research asks for "a percentage" and quotes Salesforce saying it "doesn't
 * guarantee a service level agreement". So a run the vendor has not given a
 * total for is reported as indeterminate rather than as 0%, and no state
 * carries an ETA: an asynchronous vendor has declined in writing to be held to
 * one, and a bar that guesses a finish time is a promise this product cannot
 * keep.
 */
export function progressPercent(progress) {
  if (!progress) return null
  if (typeof progress.percent === 'number') return Math.max(0, Math.min(100, progress.percent))
  return null
}

/** One line saying what a run is waiting for, or why it stopped. */
export function progressNote(progress) {
  if (!progress) return ''
  if (progress.percent === 100) return progress.detail || 'Complete'
  if (progress.indeterminate) return progress.detail || 'The vendor has not reported a total yet'
  return progress.detail || ''
}

/** Whether this run still has work the poller or an operator can do to it. */
export function isLive(run) {
  return Boolean(run) && !run.terminal
}

/**
 * The tone a run's state should be rendered in, from the published vocabulary
 * rather than from a list compiled into the page, so a state added on the
 * server reaches every client at once.
 */
export function stateTone(vocabulary, state) {
  const entry = (vocabulary?.run_states || []).find((row) => row.value === state)
  if (!entry) return 'neutral'
  if (state === 'complete') return 'insert'
  if (state === 'failed' || state === 'stalled') return 'delete'
  if (state === 'running') return 'update'
  return 'neutral'
}

/** The researched number a picker should default to, if the caller has none. */
export function defaultOf(vocabulary, name) {
  return vocabulary?.numbers?.[name]?.value
}

/** Whether a run's findings stopped a job from being created. */
export function blockedByPreflight(run) {
  return Boolean(run?.data?.findings?.length) && !run?.data?.job?.id
}
