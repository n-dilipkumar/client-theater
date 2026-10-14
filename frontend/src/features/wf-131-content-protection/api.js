/**
 * WF-131's own API wrapper.
 *
 * `apiRequest` from `@/lib/api` is the transport, exactly as the contract asks.
 * The shared `api` object grows no methods, so a hundred features can each talk
 * to their own `/api/wf-131` routes without anyone editing a shared file.
 *
 * The status is the one field this workflow branches on: 422 is a malformed
 * policy or action, 403 is a recipient the matrix refused, and 404 is a policy or
 * a link that is not there. The refusal body carries the decision, so a page can
 * name the protection that withheld the content rather than only that it was
 * withheld.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-131'

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

export const protectionApi = {
  /** Every term, with the workflow each one came from. */
  vocabulary: () => call('/vocabulary'),

  /** What this workflow is and is not worth, and the three items it declines. */
  honesty: () => call('/honesty'),

  /** Counts for the scope. */
  summary: (roomId) => call('/summary', { room_id: roomId }),

  /** The four protections and this workflow's matrix, joined onto one link. */
  compose: (linkId) => call(`/links/${encode(linkId)}`),

  /** Every recipient's policy for this link. */
  policies: (linkId, includeRevoked = true) =>
    call(`/links/${encode(linkId)}/policies`, { include_revoked: includeRevoked }),

  /** Every policy in scope, by room. The board's list. */
  policiesInScope: (roomId) => call('/policies', { room_id: roomId }),

  /** Bind a policy to a recipient. `plan` is checked only when a watermark flips on. */
  createPolicy: (linkId, payload, roomId, plan) =>
    send(`/links/${encode(linkId)}/policies`, 'POST', payload, { room_id: roomId, plan }),

  readPolicy: (policyId) => call(`/policies/${encode(policyId)}`),

  updatePolicy: (policyId, payload, plan) =>
    send(`/policies/${encode(policyId)}`, 'PATCH', payload, { plan }),

  revoke: (policyId) => send(`/policies/${encode(policyId)}/revoke`, 'POST', {}),

  restore: (policyId) => send(`/policies/${encode(policyId)}/restore`, 'POST', {}),

  /** Answer the matrix for one recipient, without writing anything. */
  evaluate: (policyId, params) => call(`/policies/${encode(policyId)}/evaluate`, params),

  /** A viewer entering the room: the attempt is logged, and a denial is 403. */
  admit: (policyId, context) => send(`/policies/${encode(policyId)}/admit`, 'POST', context),

  actions: (linkId, recipient) => call(`/links/${encode(linkId)}/actions`, { recipient }),

  recordAction: (linkId, payload, roomId) =>
    send(`/links/${encode(linkId)}/actions`, 'POST', payload, { room_id: roomId }),

  bulkAlert: (linkId, params) => call(`/links/${encode(linkId)}/bulk-alert`, params),
}

/** Every room, so the page can scope the list. Read from the core collection. */
export const listRooms = () => apiRequest('/records/room?limit=100')

/** The four logged verbs, and what each one means to a reader. */
export const ACTION_LABELS = {
  view: 'Viewed the room',
  download: 'Downloaded a file',
  share: 'Forwarded the link',
  revoke: 'Access revoked',
}

export const ACTION_TONES = {
  view: 'update',
  download: 'insert',
  share: 'restore',
  revoke: 'delete',
}

/** The protections the matrix reports, as words a reader sees rather than tokens. */
export const PROTECTION_LABELS = {
  revocation: 'Revocation',
  expiry: 'Expiry',
  ip: 'IP allow-list',
  email: 'Email verification',
  passcode: 'Passcode',
  nda: 'NDA acceptance',
  download: 'Download',
  watermark: 'Watermark',
}

/** The refusals WF-131 can raise, as a sentence a reader can act on. */
export const REASON_LABELS = {
  link_closed: 'The policy is closed (revoked or the link expired).',
  policy_expired: 'The policy has expired.',
  policy_revoked: 'The recipient was revoked.',
  ip_not_allowed: 'The address is not in the allow-list.',
  email_required: 'The inbox has not been verified.',
  passcode_required: 'The passcode has not been given.',
  nda_not_accepted: 'The agreement has not been accepted.',
}

/**
 * The policy state as a word. The word is always printed beside the tone, so the
 * colour is a second signal and never the only one.
 */
export function stateOf(policy) {
  if (!policy) return 'unknown'
  if (policy.revoked) return 'revoked'
  const hasGate = Boolean(
    policy.policy?.email_protected ||
      policy.policy?.email_authenticated ||
      policy.policy?.require_passcode ||
      policy.policy?.enable_agreement ||
      (policy.policy?.ip_allowlist || []).length,
  )
  return hasGate ? 'gated' : 'open'
}

export const STATE_LABELS = {
  revoked: 'Revoked',
  gated: 'Gated',
  open: 'Open',
  unknown: 'Unknown',
}

export const STATE_TONES = {
  revoked: 'delete',
  gated: 'insert',
  open: 'update',
  unknown: 'neutral',
}

export function stateTone(state) {
  return STATE_TONES[state] || 'neutral'
}

/** The switches this policy turns on, as short words. */
export function activeProtections(policy) {
  const fields = policy?.policy || {}
  const active = []
  if (fields.email_protected) active.push('email_protected')
  if (fields.email_authenticated) active.push('email_authenticated')
  if (fields.require_passcode) active.push('require_passcode')
  if (fields.enable_agreement) active.push('enable_agreement')
  if (fields.enable_watermark) active.push('enable_watermark')
  if (fields.allow_download) active.push('allow_download')
  if ((fields.ip_allowlist || []).length) active.push('ip_allowlist')
  return active
}

/** A space- or comma-separated allow-list string as a list, for the server. */
export function splitAllowlist(text) {
  return String(text || '')
    .replace(/\n/g, ',')
    .split(',')
    .map((part) => part.trim())
    .filter(Boolean)
}

/** A timestamp as a short string, or a dash when there is none. */
export function shortTimestamp(value) {
  if (!value) return '--'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return String(value)
  return parsed.toISOString().replace('T', ' ').replace(/\.\d+Z$/, 'Z')
}

/** A decision's headline, in words. */
export function decisionLabel(decision) {
  if (!decision) return 'No decision yet.'
  if (decision.allowed) return 'Allowed: every protection permits this request.'
  const names = (decision.denied_by || []).map((name) => PROTECTION_LABELS[name] || name)
  return `Refused by: ${names.join(', ')}.`
}
