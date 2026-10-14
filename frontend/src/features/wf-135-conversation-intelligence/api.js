/**
 * WF-135's own API wrapper.
 *
 * `apiRequest` from `@/lib/api` is the transport, exactly as the contract asks:
 * the shared `api` object grows no methods, so a hundred features can each talk
 * to their own `/api/wf-135` routes without anyone editing a shared file.
 *
 * The writes go through `requestWithBody` instead, because `apiRequest` reads the
 * error body to build a message and then throws it away. Two responses in this
 * workflow need more than a single string:
 *
 *   - a 400 from the validator carries `errors`, a field-keyed map, so a form can
 *     put each message beside the input that caused it;
 *   - a 409 carries `reason`, a stable token, so the page can say "the fan-out is
 *     incomplete" instead of printing an English sentence and hoping the server
 *     did not reword it.
 *
 * That is a finding for the shared client, not a preference: recorded as
 * promotion work rather than smuggled across the boundary.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-135'

function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

/** The shared client. Fine for everything whose failure is just a failure. */
function call(path) {
  return apiRequest(`${BASE}${path}`)
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

export const conversationApi = {
  /** The headline numbers, optionally scoped to one room. */
  summary: (roomId) => call(`/summary${query({ room_id: roomId })}`),

  /** The researched vocabulary and the decisions behind it. */
  vocabulary: () => call('/vocabulary'),

  /** MEDDICC coverage across the captures in scope, with no score. */
  coverage: (roomId) => call(`/coverage${query({ room_id: roomId })}`),

  /** Every capture, newest state included. */
  captures: (roomId) => call(`/captures${query({ room_id: roomId })}`),
  roomCaptures: (roomId) => call(`/rooms/${encode(roomId)}/captures`),

  capture: (captureId) => call(`/captures/${encode(captureId)}`),
  destinations: (captureId) => call(`/captures/${encode(captureId)}/destinations`),

  /**
   * One entry: record the conversation and fan it out. The server writes the
   * room's notes, the handoff packet and the CRM custom fields, then reads them
   * back, so a 409 here means the fan-out did not land.
   */
  record: (payload) => send('/captures', 'POST', payload),

  /** Report the outbound outcome of one destination row. */
  reportDestination: (rowId, payload) => send(`/destinations/${encode(rowId)}/report`, 'POST', payload),
}

/** Every room, so the page can attach a capture to one. */
export const listRooms = () => apiRequest('/records/room?limit=100')

/** Words for each source kind, so nothing is carried by a value alone. */
export const SOURCE_WORDS = {
  call: 'Call',
  meeting: 'Meeting',
  email_thread: 'Email thread',
  calendar_event: 'Calendar event',
  spoken_playbook: 'Spoken playbook',
}

/** Words for each destination, matching the backend labels. */
export const DESTINATION_WORDS = {
  room_note: 'Room notes',
  handoff_packet: 'Handoff packet',
  crm_field: 'CRM custom fields',
}

/** The three destinations, in the order the fan-out writes them. */
export const DESTINATION_ORDER = ['room_note', 'handoff_packet', 'crm_field']

export const DESTINATION_NOTES = {
  room_note: 'The room timeline carries the summary, the next steps and the requirements.',
  handoff_packet: 'Post-sales reads the requirements here without re-listening to the calls.',
  crm_field:
    'The extraction staged as CRM custom fields. This build holds no live CRM connection, so the outcome is reported here.',
}

export const STATE_WORDS = { written: 'Written', failed: 'Failed' }
export const STATE_TONES = { written: 'success', failed: 'destructive' }

/** The CRM outcome names, re-declared from WF-038's vocabulary. */
export const CRM_OUTCOMES = ['created', 'updated', 'submitted', 'failed', 'rejected', 'rolled_back']
export const CRM_OUTCOME_WORDS = {
  created: 'Created',
  updated: 'Updated',
  submitted: 'Submitted',
  failed: 'Failed',
  rejected: 'Rejected',
  rolled_back: 'Rolled back',
}
export const CRM_SUCCESS_WORDS = ['Created', 'Updated', 'Submitted']

/** What each destination row accepts as a report, matching the backend rule. */
export const REPORTABLE_OUTCOMES = {
  room_note: ['written', 'failed'],
  handoff_packet: ['written', 'failed'],
  crm_field: CRM_OUTCOMES,
}

/** The sentence for a conflict, from the stable reason token. */
export const CONFLICTS = {
  fanout_incomplete:
    'The fan-out reached fewer destinations than the workflow requires, so the capture is not complete.',
  write_not_visible: 'The server wrote the change but could not read it back, so it refused it.',
}

export function conflictText(reason) {
  return CONFLICTS[reason] || 'That change was refused.'
}

/** The field-keyed message for an input, from a 400 body. */
export function fieldError(error, field) {
  return error?.errors?.[field] || null
}
