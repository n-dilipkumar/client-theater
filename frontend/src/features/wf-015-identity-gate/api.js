import { apiRequest } from '@/lib/api'

/**
 * WF-015's own API wrapper.
 *
 * `apiRequest` from `@/lib/api` is the transport, exactly as the contract asks: the
 * shared `api` object grows no methods, so a hundred features can each talk to their
 * own `/api/<feature>` routes without anyone editing a shared file.
 *
 * Two calls go through `requestWithBody` instead, and the reason is worth stating
 * because it is a finding rather than a preference.
 *
 * `apiRequest` reads the error body to build a message and then discards it, so the
 * only thing that survives a failure is `status` and one string. That is enough for
 * "something went wrong" and not enough for either of the two responses this
 * workflow exists to produce well:
 *
 *   - a 400 from the policy validator carries `errors`, a field-keyed map, so the
 *     form can put each message next to the input that caused it rather than
 *     showing one combined sentence;
 *   - a 403 from the gate carries `error`, a machine-readable reason, so the buyer
 *     page can choose the right explanation for a refused domain, a dead link, a
 *     retired link and a request that did not look like a browser - four very
 *     different messages.
 *
 * The branch solved this by adding `error.code`, `error.errors` and `error.body` to
 * every failed request in `lib/api.js`. That is a shared file, and it is a
 * three-line change that belongs to whoever owns it rather than to a feature. Until
 * it is made, the two calls that need the body read it themselves. Recorded in the
 * port report as promotion work.
 */

const BASE = '/wf-015-identity-gate'

function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

/** The shared client. Fine for everything whose failure is just a failure. */
function call(path, options) {
  return apiRequest(`${BASE}${path}`, options)
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
    const error = new Error(body?.detail || body?.error || `${response.status} ${response.statusText}`)
    error.status = response.status
    error.code = body?.error || null
    error.errors = body?.errors || null
    error.body = body
    throw error
  }

  if (response.status === 204) return null
  return response.json()
}

function send(path, method, payload) {
  return requestWithBody(path, { method, body: JSON.stringify(payload) })
}

export const accessApi = {
  // -- the seller's side ---------------------------------------------------- //
  roomAccess: (roomId) => call(`/rooms/${encodeURIComponent(roomId)}/access`),

  /** Field-keyed 400s, so `error.errors` reaches the form. */
  setRoomAccess: (roomId, policy) =>
    send(`/rooms/${encodeURIComponent(roomId)}/access`, 'PUT', policy),

  clearRoomAccess: (roomId) =>
    call(`/rooms/${encodeURIComponent(roomId)}/access`, { method: 'DELETE' }),

  templateAccess: (templateId) => call(`/templates/${encodeURIComponent(templateId)}/access`),

  setTemplateAccess: (templateId, policy) =>
    send(`/templates/${encodeURIComponent(templateId)}/access`, 'PUT', policy),

  sessions: (roomId, params = {}) =>
    call(`/rooms/${encodeURIComponent(roomId)}/access/sessions${query(params)}`),

  outbox: (roomId) => call(`/rooms/${encodeURIComponent(roomId)}/access/outbox`),

  // -- the buyer's side ----------------------------------------------------- //
  requirements: (roomId) => call(`/rooms/${encodeURIComponent(roomId)}/access/requirements`),

  /** `error.code` reaches the refusal copy, so a 403 says which one it was. */
  submit: (roomId, payload) => send(`/rooms/${encodeURIComponent(roomId)}/access/sessions`, 'POST', payload),

  check: (roomId, token) =>
    call(`/rooms/${encodeURIComponent(roomId)}/access/session${query({ token })}`),
}

/** Records a team may need, read through the shared generic surface. */
export const listRecords = (collection, params = {}) => apiRequest(`/records/${collection}${query(params)}`)

export const getRecord = (collection, id) =>
  apiRequest(`/records/${collection}/${encodeURIComponent(id)}`)
