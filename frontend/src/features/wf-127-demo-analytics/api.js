/**
 * WF-127's own API wrapper.
 *
 * `apiRequest` from `@/lib/api` is the transport, exactly as the contract asks.
 * The shared `api` object grows no methods, so a hundred features can each talk
 * to their own `/api/wf-127` routes without anyone editing a shared file.
 *
 * The error keeps its status: a 410 means the demo is archived and accepts no
 * event, a 422 means a rule refused the payload, and a 404 means the demo or the
 * room does not exist. The page branches on those three.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-127'

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

export const demoApi = {
  /** The values this workflow accepts, and which half is sourced. */
  vocabulary: () => call('/vocabulary'),

  /** Every decision the research does not make. */
  inferences: () => call('/inferences'),

  summary: (roomId) => call('/summary', { room_id: roomId }),

  demos: (roomId) => call('/demos', { room_id: roomId }),

  demo: (demoId) => call(`/demos/${encode(demoId)}`),

  createDemo: (roomId, payload) => send('/demos', 'POST', payload, { room_id: roomId }),

  updateDemo: (demoId, payload) => send(`/demos/${encode(demoId)}`, 'PATCH', payload),

  publishDemo: (demoId) => send(`/demos/${encode(demoId)}/publish`, 'POST', {}),

  archiveDemo: (demoId) => send(`/demos/${encode(demoId)}/archive`, 'POST', {}),

  /** Record an initial send or a forward re-share. */
  recordShare: (demoId, payload) => send(`/demos/${encode(demoId)}/shares`, 'POST', payload),

  /** Record one viewer event. `shared_from` is the discovery signal. */
  recordEvent: (demoId, payload) => send(`/demos/${encode(demoId)}/events`, 'POST', payload),

  analytics: (demoId) => call(`/demos/${encode(demoId)}/analytics`),

  heatmap: (demoId) => call(`/demos/${encode(demoId)}/heatmap`),

  sections: (demoId) => call(`/demos/${encode(demoId)}/sections`),

  stakeholders: (demoId) => call(`/demos/${encode(demoId)}/stakeholders`),

  /** One of the six researched drill-down levels. */
  rollup: (demoId, level) => call(`/demos/${encode(demoId)}/rollup`, { level }),

  allStakeholders: (roomId) => call('/stakeholders', { room_id: roomId }),
}

/** Every room, so the page can offer one. Read from the core collection. */
export const listRooms = () => apiRequest('/records/room?limit=100')

/**
 * The default click path a recording is converted into.
 *
 * The researched flow is a recording converted into a branching demo. A real
 * screen-capture ingest is out of scope, so the build converts a recording into
 * this branching template, and a seller may paste their own tree instead.
 */
export const DEFAULT_CLICK_PATH_TEXT = [
  'overview | Overview | dashboard,reports',
  'dashboard | Dashboard | alerts,export',
  'reports | Reports | export',
  'alerts | Alerts | export',
  'export | Export |',
].join('\n')

/**
 * Parse a pasted click path: one node per line, `id | label | next,next`.
 *
 * Returns the nodes and a message when the tree is not usable, so the form can
 * refuse a broken tree before the round trip. The server validates the same
 * shape; this only means a mistake is caught sooner.
 */
export function parseClickPath(text) {
  const lines = String(text || '')
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
  if (lines.length === 0) return { nodes: [], error: 'The click path needs at least one node.' }
  const nodes = []
  const seen = new Set()
  for (const line of lines) {
    const [rawId, rawLabel, rawNext] = line.split('|').map((part) => (part || '').trim())
    if (!rawId) return { nodes: [], error: `Every line needs a node id: "${line}"` }
    if (seen.has(rawId)) return { nodes: [], error: `Node id "${rawId}" is used twice.` }
    seen.add(rawId)
    const next = rawNext ? rawNext.split(',').map((part) => part.trim()).filter(Boolean) : []
    nodes.push({ id: rawId, label: rawLabel || rawId, kind: next.length ? 'screen' : 'end', next })
  }
  for (const node of nodes) {
    for (const reference of node.next) {
      if (!seen.has(reference)) {
        return { nodes: [], error: `Node "${node.id}" branches to unknown node "${reference}".` }
      }
    }
  }
  return { nodes, error: null }
}

/** The tone each demo state renders with. The state word is always printed. */
export const STATE_TONES = {
  draft: 'neutral',
  published: 'insert',
  archived: 'restore',
}

export function stateTone(state) {
  return STATE_TONES[state] || 'neutral'
}

/** The tone each stakeholder role renders with. */
export const ROLE_TONES = {
  initial_recipient: 'neutral',
  discovered: 'insert',
  direct: 'update',
}

export function roleTone(role) {
  return ROLE_TONES[role] || 'neutral'
}

/** The words a seller reads for each role code. */
export const ROLE_LABELS = {
  initial_recipient: 'Initial recipient',
  discovered: 'Discovered',
  direct: 'Direct open',
}

/** The words a seller reads for each event code. */
export const EVENT_LABELS = {
  view: 'View',
  section_selected: 'Section selected',
  doc_downloaded: 'Document downloaded',
}

/** The words a seller reads for each level code. */
export const LEVEL_LABELS = {
  buyer_group: 'Buyer group',
  top_level: 'Top level',
  team: 'Team',
  individual: 'Individual',
  recently_shared: 'Most recently shared',
  all_demos: 'All individual demos',
}

/** A ratio as a whole percentage, or a dash when it cannot be computed. */
export function formatRate(value) {
  if (value === null || value === undefined || value === '') return '--'
  const number = Number(value)
  if (!Number.isFinite(number)) return '--'
  return `${Math.round(number * 100)}%`
}

/** Seconds as a compact human string. */
export function formatSeconds(value) {
  const seconds = Number(value)
  if (!Number.isFinite(seconds) || seconds <= 0) return '--'
  if (seconds < 60) return `${Math.round(seconds)}s`
  const minutes = Math.floor(seconds / 60)
  const rest = Math.round(seconds % 60)
  return rest ? `${minutes}m ${rest}s` : `${minutes}m`
}

/** A timestamp as a short string, or a dash when there is none. */
export function shortTimestamp(value) {
  if (!value) return '--'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return String(value)
  return parsed.toISOString().replace('T', ' ').replace(/\.\d+Z$/, 'Z')
}

/**
 * A whole number inside a range, or null when the value is not usable.
 *
 * The server is still the authority and still refuses; this only means a
 * mistake is caught before the round trip and the control is disabled rather
 * than offering a failure.
 */
export function wholeInRange(value, min, max) {
  const number = Number(value)
  if (!Number.isInteger(number)) return null
  if (number < min || number > max) return null
  return number
}
