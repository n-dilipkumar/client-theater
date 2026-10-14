/**
 * WF-132's own API wrapper.
 *
 * `apiRequest` from `@/lib/api` is the transport, exactly as the contract asks:
 * the shared `api` object grows no methods, so this feature talks to its own
 * `/api/wf-132` routes without anyone editing a shared file.
 *
 * The write calls go through `requestWithBody` for the same reason WF-124's do:
 * `apiRequest` throws away the parsed error body, and two responses here need it.
 * A 409 carries `reason`, a stable token, so the page can say "that thread is
 * resolved, reopen it" instead of printing an English sentence and hoping the
 * server never rewords it. A 422 carries `errors`, a field-keyed map.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-132'

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
  return requestWithBody(path, { method, body: JSON.stringify(payload) })
}

const encode = encodeURIComponent

export const conversationApi = {
  /** The researched vocabulary, including the contested flag, so the page cannot drift. */
  vocabulary: () => apiRequest(`${BASE}/vocabulary`),

  summary: (roomId) => apiRequest(`${BASE}/summary${query({ room_id: roomId })}`),

  threads: (roomId, params = {}) =>
    apiRequest(`${BASE}/threads${query({ room_id: roomId, ...params })}`),

  thread: (threadId) => apiRequest(`${BASE}/threads/${encode(threadId)}`),

  openThread: (roomId, payload) => send(`/rooms/${encode(roomId)}/threads`, 'POST', payload),

  comment: (threadId, payload) => send(`/threads/${encode(threadId)}/comments`, 'POST', payload),

  resolve: (threadId, payload) => send(`/threads/${encode(threadId)}/resolve`, 'POST', payload),

  reopen: (threadId, payload) => send(`/threads/${encode(threadId)}/reopen`, 'POST', payload),

  tag: (threadId, payload) => send(`/threads/${encode(threadId)}/mentions`, 'POST', payload),

  watch: (threadId, payload) => send(`/threads/${encode(threadId)}/watch`, 'POST', payload),

  convert: (threadId, payload) =>
    send(`/threads/${encode(threadId)}/convert-to-task`, 'POST', payload),

  /** Who the acting principal is in this room: role, side and champion status. */
  identity: (roomId, actor) =>
    apiRequest(`${BASE}/rooms/${encode(roomId)}/identity${query({ actor })}`),

  edits: (roomId) => apiRequest(`${BASE}/rooms/${encode(roomId)}/edits`),

  recordEdit: (roomId, payload) => send(`/rooms/${encode(roomId)}/edits`, 'POST', payload),

  champions: (roomId) => apiRequest(`${BASE}/rooms/${encode(roomId)}/champions`),

  refreshEscalations: (roomId) =>
    send(`/rooms/${encode(roomId)}/escalations/refresh`, 'POST', {}),

  escalations: (roomId) => apiRequest(`${BASE}/rooms/${encode(roomId)}/escalations`),
}

/** Every room, so the page can offer one to read the conversation in. */
export const listRooms = () => apiRequest('/records/room?limit=100')

/** The words for each anchor kind. The vocabulary endpoint is the source. */
export const ANCHOR_WORDS = {
  page: 'Page',
  section: 'Section',
  asset: 'Asset',
  map_task: 'MAP task',
}

/** The words for each thread state. A word always accompanies the colour. */
export const STATE_WORDS = { open: 'Open', resolved: 'Resolved' }

export const STATE_TONES = { open: 'info', resolved: 'success' }

export const KIND_WORDS = { comment: 'Comment', reply: 'Reply', question: 'Question' }

export const SIDE_WORDS = { seller: 'Seller', buyer: 'Buyer' }

/** The sentence for a conflict token, from the stable reason the server sends. */
export const CONFLICTS = {
  thread_resolved: 'That thread is resolved. Reopen it before you comment.',
  thread_already_resolved: 'That thread is already resolved.',
  thread_not_resolved: 'That thread is not resolved yet, so there is nothing to reopen.',
  thread_already_converted: 'That thread is already linked to a MAP task.',
}

export function conflictText(reason) {
  return CONFLICTS[reason] || 'That change was refused.'
}
