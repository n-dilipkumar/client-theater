/**
 * WF-125's own API wrapper.
 *
 * `apiRequest` from `@/lib/api` is the transport, exactly as the contract asks:
 * the shared `api` object grows no methods, so this feature talks to its own
 * `/api/wf-125` routes without anyone editing a shared file.
 *
 * The write calls read the error body themselves, which is a finding rather
 * than a preference. Two responses need more than `apiRequest` keeps:
 *
 *   - a 400 from the validator carries `errors`, a field-keyed map, so a form
 *     can put each message beside the input that caused it;
 *   - a 409 carries `reason`, a stable token, so the page can say "this rule is
 *     disabled" instead of printing an English sentence and hoping the server
 *     did not reword it.
 *
 * The shared client would need three more fields on one function, which is a
 * shared file and a platform decision. Until then the calls that need the body
 * read it themselves.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-125'

function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

/** As `apiRequest`, but keeps the parsed error body on the thrown error. */
async function requestWithBody(path, options) {
  const response = await fetch(`/api${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })

  if (!response.ok) {
    let body = null
    try {
      body = await response.json()
    } catch {
      // Non-JSON error body; the status line is the best we have.
    }
    const error = new Error(
      body?.detail || body?.error || `${response.status} ${response.statusText}`,
    )
    error.status = response.status
    error.code = body?.error || null
    error.errors = body?.errors || null
    error.reason = body?.reason || null
    error.body = body
    throw error
  }

  if (response.status === 204) return null
  return response.json()
}

function send(path, method, payload) {
  return requestWithBody(path, { method, body: JSON.stringify(payload ?? {}) })
}

const encode = encodeURIComponent

export const syncApi = {
  summary: () => apiRequest(`${BASE}/summary`),

  /**
   * The researched vocabulary and the decisions behind it. The page reads its
   * states from here rather than hard-coding them.
   */
  vocabulary: () => apiRequest(`${BASE}/vocabulary`),

  rules: () => apiRequest(`${BASE}/rules`),
  createRule: (payload) => send('/rules', 'POST', payload),

  /** Apply the enabled rules. Idempotent by (rule, deal). */
  evaluate: (ruleId) => send('/evaluate', 'POST', ruleId ? { rule_id: ruleId } : {}),

  records: (params) => apiRequest(`${BASE}/records${query(params)}`),
  ingestRecord: (payload) => send('/records', 'POST', payload),

  targets: () => apiRequest(`${BASE}/targets`),
  createTarget: (payload) => send('/targets', 'POST', payload),

  rooms: () => apiRequest(`${BASE}/rooms`),
  provisionRoom: (dealId) => send(`/deals/${encode(dealId)}/room`, 'POST'),

  events: () => apiRequest(`${BASE}/events`),
  recordEvent: (dealId, payload) =>
    send(`/deals/${encode(dealId)}/events`, 'POST', payload),

  writebacks: (params) => apiRequest(`${BASE}/writebacks${query(params)}`),
  retryWriteback: (id) => send(`/writebacks/${encode(id)}/retry`, 'POST'),
}

/** The words for each write-back state, so nothing is carried by colour alone. */
export const STATE_WORDS = { pending: 'Pending', synced: 'Synced', failed: 'Failed' }

export const STATE_TONES = { pending: 'warning', synced: 'success', failed: 'destructive' }

/** The words for each object kind. */
export const OBJECT_WORDS = { standard: 'Standard', custom: 'Custom' }

export const OBJECT_TONES = { standard: 'neutral', custom: 'info' }

/** How a room was made, as words. */
export const CREATED_WORDS = { rule: 'Rule', seller: 'Seller pick' }

/** The sentence for a conflict, from the stable reason token. */
export const CONFLICTS = {
  rule_disabled: 'That rule is disabled. Enable it before you apply it.',
  mirror_absent: 'The CRM object has no mirror yet, so the write-back failed.',
  already_synced: 'That write-back is already synced.',
  write_not_visible: 'The server wrote the change but could not read it back, so it refused it.',
}

export function conflictText(reason) {
  return CONFLICTS[reason] || 'That change was refused.'
}

/** One condition as a short sentence, so a rule explains itself. */
export function conditionText(condition) {
  const field = condition?.field || 'field'
  const op = condition?.op || 'eq'
  if (op === 'exists') return `${field} is present`
  return `${field} ${op} ${formatValue(condition?.value)}`
}

function formatValue(value) {
  if (Array.isArray(value)) return value.join(', ')
  if (value === true) return 'true'
  if (value === false) return 'false'
  return String(value)
}

/** The write-backs grouped by state, so a stat row can count them. */
export function countsByState(writebacks = []) {
  return writebacks.reduce(
    (counts, row) => ({ ...counts, [row.state]: (counts[row.state] || 0) + 1 }),
    { pending: 0, synced: 0, failed: 0 },
  )
}

/** A mirrored record's human label, falling back to its CRM id. */
export function recordLabel(record) {
  return record?.name || record?.account || record?.external_id || record?.id || 'unknown'
}

/** A timestamp as machine text, and a missing one as a dash. */
export function shortTimestamp(value) {
  if (!value) return '--'
  return String(value).replace('T', ' ').replace(/\.\d+/, '').replace('+00:00', 'Z')
}

/** The rooms with the deal each was generated from, keyed by deal id. */
export function roomsByDeal(rooms = []) {
  return Object.fromEntries(rooms.map((room) => [room.deal_id, room]))
}
