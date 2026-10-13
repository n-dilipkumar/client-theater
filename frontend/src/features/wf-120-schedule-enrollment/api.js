/**
 * WF-120's own API wrapper.
 *
 * `apiRequest` from `@/lib/api` is the transport, exactly as the feature
 * contract asks. The shared `api` object grows no methods, so a hundred features
 * each talk to their own `/api/wf-120` routes without anyone editing a shared
 * file.
 *
 * The generic records route is used only to list rooms for the picker.
 * Everything this workflow does goes through its own routes.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-120'

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

export const enrollmentApi = {
  vocabulary: () => call('/vocabulary'),
  inferences: () => call('/inferences'),
  records: (roomId, objectName) => call('/records', { room_id: roomId, object: objectName }),
  schedules: (roomId) => call('/schedules', { room_id: roomId }),
  schedule: (scheduleId) => call(`/schedules/${encode(scheduleId)}`),
  runs: (params) => call('/runs', params),
  readRun: (runId) => call(`/runs/${encode(runId)}`),
  enrollments: (params) => call('/enrollments', params),
  readEnrollment: (enrollmentId) => call(`/enrollments/${encode(enrollmentId)}`),
  events: (params) => call('/events', params),
  notifications: (params) => call('/notifications', params),
  summary: (roomId) => call('/summary', { room_id: roomId }),

  createSchedule: (body) => send('/schedules', 'POST', body),
  updateSchedule: (scheduleId, body) => send(`/schedules/${encode(scheduleId)}`, 'PATCH', body),
  deleteSchedule: (scheduleId) => send(`/schedules/${encode(scheduleId)}`, 'DELETE', {}),
  publish: (scheduleId, published, backfill) =>
    send(`/schedules/${encode(scheduleId)}/publish`, 'POST', { published, backfill }),
  evaluate: (scheduleId, at) => send(`/schedules/${encode(scheduleId)}/evaluate`, 'POST', { at }),
  run: (scheduleId, at) => send(`/schedules/${encode(scheduleId)}/run`, 'POST', { at }),
}

/** Rooms to offer in a picker. Read from the core collection. */
export const listRooms = () => apiRequest('/records/room?limit=100')

export const STATUS_LABELS = {
  queued: 'Queued',
  completed: 'Completed',
  unenrolled: 'Unenrolled',
}

export const REASON_LABELS = {
  matched: 'Enrolled',
  re_enrolled: 'Re-enrolled',
  no_filter_match: 'Did not meet the filter',
  already_enrolled: 'Already enrolled',
  single_enrollment: 'Already enrolled once',
  deferred: 'Deferred at activation',
  unenrolled: 'Unenrolled',
}

export const MODE_LABELS = {
  single: 'Single enrollment',
  after_completion: 'Re-enroll after completion',
}

export const BACKFILL_LABELS = {
  existing_and_future: 'Enroll existing matches now',
  future_only: 'Only records that match after turning it on',
}

export const OUTCOME_LABELS = {
  enrolled: 'Enrolled',
  re_enrolled: 'Re-enrolled',
  released: 'Actions fired',
  unenrolled: 'Unenrolled',
  skipped: 'Skipped',
  failed: 'Failed',
}

export const RUN_KIND_LABELS = {
  activation: 'Activation',
  schedule: 'Scheduled run',
  backfill: 'Backfill at publish',
}

export function statusLabel(status) {
  return STATUS_LABELS[status] || status || 'Unknown'
}

export function reasonLabel(reason) {
  return REASON_LABELS[reason] || reason || 'Unknown'
}

export function modeLabel(mode) {
  return MODE_LABELS[mode] || mode || 'Unknown'
}

export function backfillLabel(choice) {
  return BACKFILL_LABELS[choice] || choice || 'Unknown'
}

export function outcomeLabel(outcome) {
  return OUTCOME_LABELS[outcome] || outcome || 'Unknown'
}

export function runKindLabel(kind) {
  return RUN_KIND_LABELS[kind] || kind || 'Run'
}

/** The badge tone for a status. Always shown beside its text label. */
export function statusTone(status) {
  if (status === 'completed') return 'insert'
  if (status === 'unenrolled') return 'delete'
  return 'restore'
}

/** The badge tone for an enrollment reason. Always beside its text label. */
export function reasonTone(reason) {
  if (reason === 'matched' || reason === 're_enrolled') return 'insert'
  if (reason === 'unenrolled') return 'delete'
  if (reason === 'deferred') return 'restore'
  return 'neutral'
}

/** The badge tone for a run outcome. Always beside its text label. */
export function outcomeTone(outcome) {
  if (outcome === 'enrolled' || outcome === 're_enrolled' || outcome === 'released') return 'insert'
  if (outcome === 'failed') return 'delete'
  if (outcome === 'unenrolled') return 'restore'
  return 'neutral'
}

const OP_BY_SYMBOL = {
  '=': 'eq',
  '!=': 'ne',
  '~': 'contains',
  '>': 'gt',
  '>=': 'gte',
  '<': 'lt',
  '<=': 'lte',
}

const SYMBOL_BY_OP = {
  eq: '=',
  ne: '!=',
  contains: '~',
  gt: '>',
  gte: '>=',
  lt: '<',
  lte: '<=',
  in: '=',
  not_in: '!=',
  exists: 'is set',
}

/**
 * Read a filter written as lines of `field<symbol>value`.
 *
 * `stage=Qualification` becomes an equals condition, `value>=1000` a bound, and
 * `owner=a|b` a list. The server validates every condition, so a line this
 * parser does not understand is simply left out of the request rather than
 * guessed at.
 */
export function parseFilter(text) {
  const out = []
  for (const raw of String(text || '').split(/[\n]+/)) {
    const line = raw.trim()
    if (!line) continue
    const match = line.match(/^([\w.]+)\s*(>=|<=|!=|=|>|<|~)\s*(.+)$/)
    if (!match) continue
    const [, field, symbol, rest] = match
    const values = rest
      .split('|')
      .map((value) => value.trim())
      .filter(Boolean)
    if (values.length > 1) {
      out.push({ field, op: 'in', value: values })
    } else {
      out.push({ field, op: OP_BY_SYMBOL[symbol], value: rest.trim() })
    }
  }
  return out
}

/** Render a schedule's filter as text, the same shape `parseFilter` reads. */
export function filterText(schedule) {
  const conditions = schedule?.filters || []
  return conditions
    .map((condition) => {
      const symbol = SYMBOL_BY_OP[condition.op] || '='
      const value = Array.isArray(condition.value) ? condition.value.join('|') : condition.value
      return condition.op === 'exists' ? `${condition.field}~` : `${condition.field}${symbol}${value}`
    })
    .join('\n')
}

/** A sentence describing a schedule's filters, or that it has none. */
export function filterPhrase(schedule) {
  const conditions = schedule?.filters || []
  if (!conditions.length) return 'Every record of the object'
  return conditions
    .map((condition) => {
      const symbol = SYMBOL_BY_OP[condition.op] || '='
      const value = Array.isArray(condition.value) ? condition.value.join(', ') : condition.value
      return `${condition.field} ${symbol} ${value ?? ''}`.trim()
    })
    .join('; ')
}

/** A sentence describing the queue delay and the interval. */
export function cadencePhrase(schedule) {
  if (!schedule) return ''
  const interval = schedule.interval_minutes
  const delay = schedule.delay_minutes
  const queue =
    delay > 0 ? `queued for ${delay} minute${delay === 1 ? '' : 's'}` : 'actions fire on enrolment'
  return `Runs every ${interval} minute${interval === 1 ? '' : 's'}; ${queue}`
}

/** Format epoch milliseconds as a compact local date and time, or a dash. */
export function formatDateTime(ms) {
  if (ms === null || ms === undefined || ms === '') return '-'
  const parsed = new Date(Number(ms))
  if (Number.isNaN(parsed.getTime())) return '-'
  return parsed.toLocaleString()
}
