/**
 * SLA escalation API (WF-109).
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a method to
 * it is exactly the collision the feature host exists to remove. The host's `apiRequest`
 * is the escape hatch that makes that unnecessary.
 *
 * `PREFIX` is the feature's own: the backend module mounts these under `/api/wf-109`, and
 * `apiRequest` already prepends `/api`.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-109'

function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value))
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const escalationApi = {
  /** The published terms: the two triggers, the four targets, the actions and the codes. */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),

  /** What the research fixes, what this build decided, and what it rejected. */
  inferences: () => apiRequest(`${PREFIX}/inferences`),

  /** The room list comes from the core records route, so this page depends on the HTTP
   *  contract rather than on another module's idea of a room's shape. */
  rooms: () => apiRequest('/records/room?limit=100'),

  summary: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/summary`),
  workflows: (roomId, params = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/workflows${query(params)}`),
  createWorkflow: (roomId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/workflows`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  goLive: (roomId, workflowId) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/workflows/${workflowId}/go-live`, { method: 'POST' }),

  /** The sweep, which is the SLA check in this product. */
  evaluate: (roomId, payload = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/escalations/evaluate`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  runs: (roomId, params = {}) => apiRequest(`${PREFIX}/rooms/${roomId}/runs${query(params)}`),
  alerts: (roomId, params = {}) => apiRequest(`${PREFIX}/rooms/${roomId}/alerts${query(params)}`),
  inbox: (roomId, params = {}) => apiRequest(`${PREFIX}/rooms/${roomId}/inbox${query(params)}`),
}

/** The words for an escalation level, so a phone or a colour-blind reader gets the answer. */
export function levelLabel(level) {
  const known = { pre_warning: 'Pre-warning', breach: 'Breached' }
  return known[String(level)] || 'Not escalated'
}

/** The words for a trigger. */
export function triggerLabel(trigger) {
  const known = { before_breach: 'Before SLA breach', on_breach: 'When SLA breaches' }
  return known[String(trigger)] || String(trigger || 'Unknown trigger')
}

/** The words for an alert's outcome. */
export function alertLabel(alert) {
  if (alert && alert.success) return 'Alert sent'
  if (alert && alert.error_type) return `Alert failed: ${alert.error_type}`
  return 'No alert'
}

/** Seconds as `1h 05m` or `45s`, for a countdown a person reads rather than computes. */
export function formatSeconds(value) {
  if (value === null || value === undefined) return 'no target'
  const total = Math.max(0, Math.round(Number(value)))
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const seconds = total % 60
  if (hours > 0) return `${hours}h ${String(minutes).padStart(2, '0')}m`
  if (minutes > 0) return `${minutes}m ${String(seconds).padStart(2, '0')}s`
  return `${seconds}s`
}

/** The words for one action a run applied. */
export function actionLabel(action) {
  const known = {
    assign: 'Assigned',
    notify: 'Notified',
    tag: 'Tagged',
    note: 'Noted',
    alert: 'Alerted',
  }
  const verb = known[String(action && action.kind)] || 'Acted'
  return `${verb}: ${(action && action.summary) || ''}`.trim()
}
