/**
 * WF-119's own API wrapper and the pure helpers its page needs.
 *
 * `apiRequest` from `@/lib/api` is the transport, exactly as the feature
 * contract asks. The shared `api` object grows no methods, so a hundred features
 * can each talk to their own `/api/wf-119` routes without anyone editing a
 * shared file. Everything this workflow does goes through its own routes.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-119'

const encode = encodeURIComponent

export const gateApi = {
  /** The rules, the envelope, the window and the decisions, from one place. */
  vocabulary: () => apiRequest(`${BASE}/vocabulary`),

  /** Every judgement this build recorded, with its audit id. */
  decisions: () => apiRequest(`${BASE}/decisions`),

  /** Counts the page leads with. */
  summary: () => apiRequest(`${BASE}/summary`),

  /** Every configured pause node, with the URLs this app serves for it. */
  gates: () => apiRequest(`${BASE}/gates`),

  createGate: (body) => apiRequest(`${BASE}/gates`, { method: 'POST', body: JSON.stringify(body) }),

  reviseGate: (key, body) =>
    apiRequest(`${BASE}/gates/${encode(key)}`, { method: 'PATCH', body: JSON.stringify(body) }),

  /** Hold a contact at the gate, with the documented action envelope. */
  pause: (key, body) =>
    apiRequest(`${BASE}/gates/${encode(key)}/pause`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  /** Every held contact, newest first. */
  holds: (params = {}) => {
    const search = new URLSearchParams()
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null && value !== '') search.set(key, value)
    }
    const query = search.toString()
    return apiRequest(`${BASE}/holds${query ? `?${query}` : ''}`)
  },

  events: (holdId) => apiRequest(`${BASE}/holds/${encode(holdId)}/events`),

  /** Decide one hold from inside the product. */
  resume: (holdId, body) =>
    apiRequest(`${BASE}/holds/${encode(holdId)}/resume`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  /** Expire every hold that has lapsed. */
  sweep: () => apiRequest(`${BASE}/sweep`, { method: 'POST', body: JSON.stringify({}) }),
}

/** The two outcomes the page offers, in the order the spec names the URLs. */
export const OUTCOMES = [
  { value: 'approved', label: 'Approve', description: 'Resume on the success branch' },
  { value: 'refused', label: 'Refuse', description: 'Resume on the failed branch' },
]

/** True when a hold has lapsed, using the server's own derived flag. */
export function hasLapsed(hold) {
  if (!hold) return false
  if (hold.state === 'expired') return true
  return Boolean(hold.expired)
}

/**
 * The hold's state in words. `expired` is read first, because a hold past its
 * window is expired even when its stored state is still `held`: expiry is
 * evaluated lazily, so the server reports both facts.
 */
export function stateLabel(hold) {
  if (hasLapsed(hold)) return 'Expired'
  if (hold && hold.state === 'resumed') {
    return hold.outcome === 'refused' ? 'Refused' : 'Approved'
  }
  return 'Held'
}

/** The badge tone. The label always travels with it, so state is never colour-only. */
export function stateTone(hold) {
  if (hasLapsed(hold)) return 'restore'
  if (hold && hold.state === 'resumed') return hold.outcome === 'refused' ? 'delete' : 'insert'
  return 'update'
}

/** A one-line summary of what a contact is waiting on, or decided. */
export function holdSummary(hold) {
  if (!hold) return 'No hold.'
  const label = stateLabel(hold)
  if (label === 'Held') return `Held until ${hold.expires_at}`
  if (label === 'Expired') return 'Lapsed with no decision'
  return `${label} on ${hold.branch}`
}

/** The hold window in words: seconds, hours and whole days when they divide. */
export function windowLabel(seconds) {
  if (!Number.isFinite(seconds)) return '—'
  if (seconds % 86400 === 0) return `${seconds / 86400} day${seconds / 86400 === 1 ? '' : 's'}`
  if (seconds % 3600 === 0) return `${seconds / 3600} hour${seconds / 3600 === 1 ? '' : 's'}`
  return `${seconds} seconds`
}

/** How long a hold has left, from the server's own countdown. */
export function remainingLabel(hold) {
  if (!hold) return '—'
  if (hasLapsed(hold)) return 'Lapsed'
  const seconds = hold.expires_in_seconds
  if (!Number.isFinite(seconds)) return '—'
  if (seconds <= 0) return 'Lapsed'
  if (seconds < 3600) return `in ${Math.ceil(seconds / 60)} min`
  if (seconds < 86400) return `in ${Math.ceil(seconds / 3600)} h`
  return `in ${Math.ceil(seconds / 86400)} d`
}

/** The contact a hold is about, which is the part of the key a person reads. */
export function contactLabel(extras) {
  if (!extras) return '—'
  return extras.contactId || extras.workflowId || '—'
}

/** The three extras fields as one readable line, so the key is visible. */
export function extrasLabel(extras) {
  if (!extras) return '—'
  return `${extras.locationId || '—'} / ${extras.contactId || '—'} / ${extras.workflowId || '—'}`
}

/**
 * The resume body for one hold.
 *
 * The branch is the gate's own branch for the outcome, which is what the resume
 * webhook expects and what the server verifies; the page never invents one.
 */
export function resumeBody(outcome, gate, approver = '') {
  const branch = (gate && gate.branches && gate.branches[outcome]) || ''
  return { outcome, branchId: branch, approver: approver || undefined }
}

/** The action envelope the page posts when it holds a contact. */
export function pauseEnvelope({ locationId, contactId, workflowId, metaKey, version }) {
  return {
    data: {},
    extras: {
      locationId: locationId || 'loc_northwind',
      contactId,
      workflowId,
    },
    meta: { key: metaKey || 'proposal-approval', version: Number(version) || 1 },
  }
}

/** The gate payload the create form sends. */
export function gatePayload(form) {
  return {
    key: form.key,
    workflow_id: form.workflowId,
    action_key: form.actionKey,
    name: form.name,
    pause_execution: Boolean(form.pauseExecution),
    branches: { approved: form.approvedBranch, refused: form.refusedBranch },
  }
}
