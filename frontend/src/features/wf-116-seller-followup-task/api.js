/**
 * WF-116's own API wrapper.
 *
 * `apiRequest` from `@/lib/api` is the transport, exactly as the feature
 * contract asks. The shared `api` object grows no methods, so a hundred features
 * each talk to their own `/api/wf-116` routes without anyone editing a shared
 * file.
 *
 * The generic records route is used only to list rooms and contacts for the
 * pickers. Everything this workflow does goes through its own routes.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-116'

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
    body: JSON.stringify(payload || {}),
  })
}

export const taskApi = {
  vocabulary: () => call('/vocabulary'),
  inferences: () => call('/inferences'),

  contacts: (roomId) => call('/contacts', { room_id: roomId }),
  summary: (roomId) => call('/summary', { room_id: roomId }),
  tasks: (params) => call('/tasks', params),
  task: (taskId) => call(`/tasks/${encode(taskId)}`),
  events: (taskId) => call(`/tasks/${encode(taskId)}/events`),
  due: (at) => call('/reminders/due', { at }),
  bin: (roomId) => call('/bin', { room_id: roomId }),

  create: (body) => send('/tasks', 'POST', body),
  fromSignal: (signal, body) => send(`/signals/${encode(signal)}`, 'POST', body),
  update: (taskId, body) => send(`/tasks/${encode(taskId)}`, 'PATCH', body),
  complete: (taskId) => send(`/tasks/${encode(taskId)}/complete`, 'POST', {}),
  fireReminder: (taskId, at) => send(`/tasks/${encode(taskId)}/reminder`, 'POST', { at }),
  attach: (taskId, contactId) => send(`/tasks/${encode(taskId)}/attach`, 'POST', { contact_id: contactId }),
  detach: (taskId) => send(`/tasks/${encode(taskId)}/detach`, 'POST', {}),
  pin: (taskId, engagementId) =>
    send(`/tasks/${encode(taskId)}/pin`, 'POST', { engagement_id: engagementId }),
  remove: (taskId) => send(`/tasks/${encode(taskId)}`, 'DELETE', {}),
  restore: (taskId) => send(`/tasks/${encode(taskId)}/restore`, 'POST', {}),
  purgeBin: () => send('/bin/purge', 'POST', {}),
}

/** Rooms to offer in a picker. Read from the core collection. */
export const listRooms = () => apiRequest('/records/room?limit=100')

/** The three lifecycle triggers, in the order the research lists them. */
export const TRIGGERS = ['Task Added', 'Task Reminder', 'Task Completed']

/** The signals the workflow accepts, in the order the research lists them. */
export const SIGNALS = [
  'task_added',
  'task_reminder_due',
  'contact_tag_added',
  'engagement_score_rule_met',
  'opportunity_stage_changed',
  'inbound_webhook',
]

export const STATUS_LABELS = {
  NOT_STARTED: 'Not started',
  COMPLETED: 'Completed',
}

export const TYPE_LABELS = {
  EMAIL: 'Email',
  CALL: 'Call',
  TODO: 'To-do',
}

export const PRIORITY_LABELS = {
  LOW: 'Low',
  MEDIUM: 'Medium',
  HIGH: 'High',
}

export function statusLabel(status) {
  return STATUS_LABELS[status] || status || 'Unknown'
}

export function typeLabel(taskType) {
  return TYPE_LABELS[taskType] || taskType || 'Task'
}

export function priorityLabel(priority) {
  return PRIORITY_LABELS[priority] || priority || 'Medium'
}

/** The badge tone for a status. Always shown beside its text label. */
export function statusTone(status) {
  if (status === 'COMPLETED') return 'restore'
  return 'insert'
}

/** The badge tone for a priority. Always shown beside its text label. */
export function priorityTone(priority) {
  if (priority === 'HIGH') return 'delete'
  if (priority === 'LOW') return 'neutral'
  return 'update'
}

/** Format epoch milliseconds as a compact local date and time, or a dash. */
export function formatDateTime(ms) {
  if (ms === null || ms === undefined || ms === '') return '-'
  const parsed = new Date(Number(ms))
  if (Number.isNaN(parsed.getTime())) return '-'
  return parsed.toLocaleString()
}

/** A sentence for a due time, measured against a supplied reference instant. */
export function duePhrase(task, nowMs = Date.now()) {
  if (!task || task.due_at_ms === null || task.due_at_ms === undefined) return 'No due time'
  if (task.status === 'COMPLETED') return `Completed; was due ${formatDateTime(task.due_at_ms)}`
  const delta = Number(task.due_at_ms) - Number(nowMs)
  if (delta < 0) return `Overdue since ${formatDateTime(task.due_at_ms)}`
  const hours = Math.round(delta / (60 * 60 * 1000))
  if (hours < 24) return `Due in ${hours} hour${hours === 1 ? '' : 's'}`
  const days = Math.round(hours / 24)
  return `Due in ${days} day${days === 1 ? '' : 's'}`
}

/** Whether the reminder has been reached and not yet fired. */
export function isReminderDue(task, nowMs = Date.now()) {
  if (!task || task.status === 'COMPLETED') return false
  if (task.reminder_at_ms === null || task.reminder_at_ms === undefined) return false
  if (task.reminder_fired_at_ms !== null && task.reminder_fired_at_ms !== undefined) return false
  return Number(task.reminder_at_ms) <= Number(nowMs)
}

/** Why a reminder cannot fire, in words, or null when it can. */
export function reminderBlockedReason(task) {
  if (!task) return 'Select a task.'
  if (task.status === 'COMPLETED') return 'This task is completed. No reminder can fire.'
  if (task.reminder_at_ms === null || task.reminder_at_ms === undefined) {
    return 'This task has no reminder time. Set one first.'
  }
  if (task.reminder_fired_at_ms !== null && task.reminder_fired_at_ms !== undefined) {
    return `The reminder already fired at ${formatDateTime(task.reminder_fired_at_ms)}.`
  }
  return null
}

/** Why a task cannot be completed, in words, or null when it can. */
export function completeBlockedReason(task) {
  if (!task) return 'Select a task.'
  if (task.status === 'COMPLETED') return 'This task is already completed.'
  return null
}

/** The attachment sentence for a task. */
export function attachmentPhrase(task) {
  if (!task) return ''
  return task.attached
    ? `Attached to contact ${task.contact_id}`
    : 'Standalone internal task. No contact attached.'
}

/** Turn an ISO timestamp into the value a datetime-local input expects. */
export function toLocalInput(ms) {
  if (ms === null || ms === undefined || ms === '') return ''
  const parsed = new Date(Number(ms))
  if (Number.isNaN(parsed.getTime())) return ''
  const pad = (value) => String(value).padStart(2, '0')
  return (
    `${parsed.getFullYear()}-${pad(parsed.getMonth() + 1)}-${pad(parsed.getDate())}` +
    `T${pad(parsed.getHours())}:${pad(parsed.getMinutes())}`
  )
}

/** Turn a datetime-local value into an ISO string, or undefined when empty. */
export function fromLocalInput(value) {
  if (!value) return undefined
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return undefined
  return parsed.toISOString()
}
