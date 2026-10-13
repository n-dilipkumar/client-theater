/**
 * WF-117's own API wrapper.
 *
 * `apiRequest` from `@/lib/api` is the transport, exactly as the feature
 * contract asks. The shared `api` object grows no methods, so a hundred features
 * each talk to their own `/api/wf-117` routes without anyone editing a shared
 * file.
 *
 * The generic records route is used only to list rooms for the picker.
 * Everything this workflow does goes through its own routes.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-117'

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
  return apiRequest(`${BASE}${path}`, {
    method,
    body: JSON.stringify(payload || {}),
  })
}

export const sweepApi = {
  vocabulary: () => call('/vocabulary'),
  inferences: () => call('/inferences'),
  opportunities: (roomId) => call('/opportunities', { room_id: roomId }),
  sweeps: (roomId) => call('/sweeps', { room_id: roomId }),
  sweep: (sweepId) => call(`/sweeps/${encode(sweepId)}`),
  runs: (params) => call('/runs', params),
  readRun: (runId) => call(`/runs/${encode(runId)}`),
  escalations: (params) => call('/escalations', params),
  notifications: (params) => call('/notifications', params),
  summary: (roomId) => call('/summary', { room_id: roomId }),

  createSweep: (body) => send('/sweeps', 'POST', body),
  updateSweep: (sweepId, body) => send(`/sweeps/${encode(sweepId)}`, 'PATCH', body),
  deleteSweep: (sweepId) => send(`/sweeps/${encode(sweepId)}`, 'DELETE', {}),
  publish: (sweepId, published) => send(`/sweeps/${encode(sweepId)}/publish`, 'POST', { published }),
  evaluate: (sweepId) => send(`/sweeps/${encode(sweepId)}/evaluate`, 'POST', {}),
  run: (sweepId) => send(`/sweeps/${encode(sweepId)}/run`, 'POST', {}),
}

/** Rooms to offer in a picker. Read from the core collection. */
export const listRooms = () => apiRequest('/records/room?limit=100')

/** The two cadence steps, in the order the research lists them. */
export const STEP_KINDS = ['nudge', 'escalate']

export const REASON_LABELS = {
  matched: 'Matched',
  no_activity_evidence: 'No activity evidence',
  below_threshold: 'Below the threshold',
  stage_filtered: 'Filtered by stage',
  value_filtered: 'Filtered by value band',
  already_escalated: 'Already escalated',
}

export const REFERENCE_LABELS = {
  last_activity: 'Last activity date',
  stage_entry: 'Date entered stage',
  latest_of_both: 'The more recent of the two',
}

export const STEP_LABELS = {
  nudge: 'Nudge',
  escalate: 'Escalate',
}

export const OUTCOME_LABELS = {
  escalated: 'Escalated',
  skipped: 'Skipped',
  failed: 'Failed',
}

export function reasonLabel(reason) {
  return REASON_LABELS[reason] || reason || 'Unknown'
}

export function referenceLabel(reference) {
  return REFERENCE_LABELS[reference] || reference || 'Unknown'
}

export function stepLabel(step) {
  return STEP_LABELS[step] || step || 'Escalate'
}

export function outcomeLabel(outcome) {
  return OUTCOME_LABELS[outcome] || outcome || 'Unknown'
}

/** The badge tone for a reason. Always shown beside its text label. */
export function reasonTone(reason) {
  if (reason === 'matched') return 'insert'
  if (reason === 'already_escalated') return 'restore'
  if (reason === 'stage_filtered' || reason === 'value_filtered') return 'update'
  return 'neutral'
}

/** The badge tone for a run outcome. Always shown beside its text label. */
export function outcomeTone(outcome) {
  if (outcome === 'escalated') return 'insert'
  if (outcome === 'failed') return 'delete'
  return 'neutral'
}

/** Format epoch milliseconds as a compact local date and time, or a dash. */
export function formatDateTime(ms) {
  if (ms === null || ms === undefined || ms === '') return '-'
  const parsed = new Date(Number(ms))
  if (Number.isNaN(parsed.getTime())) return '-'
  return parsed.toLocaleString()
}

/** A sentence for how long an opportunity has been idle. */
export function idlePhrase(decision) {
  if (!decision || decision.idle_days === null || decision.idle_days === undefined) {
    return 'No activity evidence'
  }
  const days = Number(decision.idle_days)
  return `Idle ${days} day${days === 1 ? '' : 's'} since the ${referenceLabel(decision.reference_field)}`
}

/** A sentence describing a sweep's cadence and threshold. */
export function cadencePhrase(sweep) {
  if (!sweep) return ''
  const steps = (sweep.cadence || [])
    .map((step) => `${stepLabel(step.action)} at ${step.after_days} day${step.after_days === 1 ? '' : 's'}`)
    .join(', ')
  return `Fires after ${sweep.threshold_days} idle day${sweep.threshold_days === 1 ? '' : 's'}: ${steps}`
}

/** A sentence describing a sweep's filters, or that it has none. */
export function filterPhrase(sweep) {
  const filters = sweep?.filters || {}
  const parts = []
  if (filters.stages && filters.stages.length) {
    parts.push(`${filters.stage_mode === 'exclude' ? 'Excluding' : 'Only'} ${filters.stages.join(', ')}`)
  }
  if (filters.value_min !== null && filters.value_min !== undefined) {
    parts.push(`value at least ${filters.value_min}`)
  }
  if (filters.value_max !== null && filters.value_max !== undefined) {
    parts.push(`value at most ${filters.value_max}`)
  }
  return parts.length ? parts.join('; ') : 'No filters'
}
