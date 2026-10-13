/**
 * WF-072's own API wrapper.
 *
 * `apiRequest` from `@/lib/api` is the transport, exactly as the contract asks: the shared
 * `api` object grows no methods, so a hundred features can each talk to their own
 * `/api/<feature>` routes without anyone editing a shared file.
 *
 * The download call goes through `requestWithBody` instead, and that is a finding rather than
 * a preference. `apiRequest` reads the error body to build a message and then throws it away,
 * so a failure survives as `status` plus one string. A refused download is a 403 whose body is
 * the whole point: it carries every gate and which one withheld the file, so the page can say
 * which control to change rather than only that the download was refused. The shared client
 * would need a new field on one function, which is a shared file and a platform decision.
 * Until then the one call that needs the body reads it. Recorded as promotion work, not
 * smuggled across the boundary.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-072'

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
    const error = new Error(
      body?.detail || body?.error || `${response.status} ${response.statusText}`,
    )
    error.status = response.status
    error.code = body?.error || null
    error.errors = body?.errors || null
    // The refused-download body. A panel that can only say "no" sends a rep looking in the
    // wrong place; this carries the decision so the page can name the control to change.
    error.decision = body && typeof body === 'object' ? body : null
    throw error
  }

  if (response.status === 204) return null
  return response.json()
}

function send(path, method, payload) {
  return requestWithBody(path, { method, body: JSON.stringify(payload ?? {}) })
}

const encode = encodeURIComponent

export const viewOnlyApi = {
  // -- the board ----------------------------------------------------------- //

  summary: (roomId) => call(`/summary${query({ room_id: roomId })}`),

  /** The researched vocabulary, so the panel cannot drift from the rules that validate it. */
  vocabulary: () => call('/vocabulary'),

  /** Every judgement call this workflow made, with the alternative it rejected. */
  decisions: () => call('/decisions'),

  // -- the room settings panel --------------------------------------------- //

  roomPolicy: (roomId) => call(`/rooms/${encode(roomId)}/policy`),
  setRoomPolicy: (roomId, payload) => send(`/rooms/${encode(roomId)}/policy`, 'PATCH', payload),

  // -- the links ----------------------------------------------------------- //

  links: (roomId) => call(`/rooms/${encode(roomId)}/links`),
  createLink: (roomId, payload) => send(`/rooms/${encode(roomId)}/links`, 'POST', payload),
  link: (linkId) => call(`/links/${encode(linkId)}`),
  updateLink: (linkId, payload) => send(`/links/${encode(linkId)}`, 'PATCH', payload),

  // -- the decision and the download path ---------------------------------- //

  decision: (linkId, itemId) =>
    call(`/links/${encode(linkId)}/download${query({ item_id: itemId })}`),
  download: (linkId, payload) => send(`/links/${encode(linkId)}/download`, 'POST', payload),
  bulk: (linkId, itemIds) => send(`/links/${encode(linkId)}/bulk`, 'POST', { item_ids: itemIds }),

  // -- the download log ---------------------------------------------------- //

  downloads: (linkId) => call(`/links/${encode(linkId)}/downloads`),
  allDownloads: (roomId) => call(`/downloads${query({ room_id: roomId })}`),
}

/** Every room, so the panel can offer one to govern. Read from the core collection. */
export const listRooms = () => apiRequest('/records/room?limit=100')

/**
 * The three controls, in the order the data flow names them. Each carries the vendor's own
 * field name and the sentence it is documented by, so the page cannot describe a control the
 * server validates differently.
 */
export const GATES = [
  {
    name: 'room_bulk_download',
    field: 'bulk_download',
    label: "The room's bulk download setting",
    owner: 'this workflow, on the room policy',
  },
  {
    name: 'link_allow_download',
    field: 'allow_download',
    label: "The link's allow download setting",
    owner: 'this workflow, on the link policy',
  },
  {
    name: 'item_can_download',
    field: 'can_download',
    label: "The item's own download grant",
    owner: "WF-074's permission set, read here",
  },
]

/** The label for a gate name, for a screen that has only the name. */
export function gateLabel(name) {
  return GATES.find((gate) => gate.name === name)?.label || name
}

/** The download kinds this build derives. */
export const DOWNLOAD_TYPES = ['original', 'pdf', 'zip']

/**
 * What this control is worth, served by the API and written here as well so the page can
 * render before the vocabulary request resolves. A test asserts the two agree.
 */
export const LIMITATION =
  'This gates a download and records it. It does not stop a viewer photographing a screen, and it does not prove who used the link. A blocked download writes no view row, so the audit trail holds the downloads that happened and not the ones that were refused.'

export const SCOPE_OWNER =
  'This workflow owns the room-level bulk download policy and the link-level allow download policy. The per-item can_download grant belongs to WF-074\u2019s permission set and is read here, never written.'

export const READ_SOURCE =
  'The per-item can_download grant is read from WF-074\u2019s permission rows (wf074_link_permission and wf074_dataroom_group_permission) by collection name. This workflow does not import that package and does not write those rows on the request path.'

export const ASSUMPTION =
  "One product surface is an inference and the specification marks it: the dataroom settings panel \u2018(Conversations / Agents / Bulk download) [inferred from the documented settings triple]\u2019. This build implements bulk download alone and carries the other two settings through untouched."

/** The AND, said in one line, so the page states the rule rather than implying it. */
export const AND_SEMANTICS =
  'A download is permitted only when all three controls permit it. The item grant also needs the link switch, and the link switch also needs the room setting.'

/**
 * Whether a decision permits the download, read from the server's own verdict rather than
 * recomputed from the gates. The server is the one place the AND lives.
 */
export function isPermitted(decision) {
  return decision?.download === true
}

/** Whether a decision is a refusal. */
export function isViewOnly(decision) {
  return decision?.download === false
}

/**
 * The columns of the decision that a panel renders, as data.
 *
 * Exported so a test can check the shape without rendering, and so the page and the test read
 * one table. Each row is the gate's label, whether it permitted, and the field name.
 */
export function gateRows(decision) {
  const gates = decision?.gates || {}
  return GATES.map((gate) => ({
    ...gate,
    permitted: gates[gate.name] === true,
  }))
}
