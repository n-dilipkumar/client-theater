/**
 * WF-130's own API wrapper.
 *
 * `apiRequest` from `@/lib/api` is the transport, exactly as the contract asks.
 * The shared `api` object grows no methods, so a hundred features can each talk
 * to their own `/api/wf-130` routes without anyone editing a shared file.
 *
 * The error keeps its status: a 409 means the artefact's state refused the
 * change (a withdrawn artefact, or an empty required section), a 422 means a
 * rule refused the payload, and a 404 means the collateral, room or asset does
 * not exist. The page branches on those three.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-130'

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

export const collateralApi = {
  /** The values this workflow accepts, and which half is sourced. */
  vocabulary: () => call('/vocabulary'),

  /** Every decision the research does not make. */
  decisions: () => call('/decisions'),

  summary: (roomId) => call('/summary', { room_id: roomId }),

  /** The content library: case studies, decks, pricing. */
  assets: () => call('/assets'),
  createAsset: (payload) => send('/assets', 'POST', payload),
  updateAsset: (assetId, payload) => send(`/assets/${encode(assetId)}`, 'PATCH', payload),
  refreshAsset: (assetId) => send(`/assets/${encode(assetId)}/refresh`, 'POST', {}),

  list: (roomId) => call('/collateral', { room_id: roomId }),
  create: (roomId, payload) => send('/collateral', 'POST', payload, { room_id: roomId }),
  read: (collateralId) => call(`/collateral/${encode(collateralId)}`),
  update: (collateralId, payload) => send(`/collateral/${encode(collateralId)}`, 'PATCH', payload),
  publish: (collateralId) => send(`/collateral/${encode(collateralId)}/publish`, 'POST', {}),
  withdraw: (collateralId) => send(`/collateral/${encode(collateralId)}/withdraw`, 'POST', {}),
  events: (collateralId) => call(`/collateral/${encode(collateralId)}/events`),
}

/** Every room, so the page can offer one. Read from the core collection. */
export const listRooms = () => apiRequest('/records/room?limit=100')

/** The words a seller reads for each artefact state. */
export const STATE_LABELS = {
  draft: 'Draft',
  published: 'Published',
  withdrawn: 'Withdrawn',
}

/** The tone each state renders with. The state word is always printed too. */
export const STATE_TONES = {
  draft: 'neutral',
  published: 'insert',
  withdrawn: 'restore',
}

/** The words a seller reads for each artefact kind. */
export const KIND_LABELS = {
  document: 'Document',
  deck: 'Deck',
}

/** The words a seller reads for each section kind. */
export const SECTION_KIND_LABELS = {
  narrative: 'Narrative',
  case_study: 'Case study',
  pricing: 'Pricing',
  mutual_action_plan: 'Mutual action plan',
  proof: 'Proof',
}

/** The words a seller reads for each deal stage. */
export const STAGE_LABELS = {
  discovery: 'Discovery',
  technical_validation: 'Technical validation',
  business_case: 'Business case',
  procurement: 'Procurement',
  closed_won: 'Closed won',
}

export function stateTone(state) {
  return STATE_TONES[state] || 'neutral'
}

export function stateLabel(state) {
  return STATE_LABELS[state] || state || 'Unknown'
}

export function kindLabel(kind) {
  return KIND_LABELS[kind] || kind || 'Collateral'
}

export function stageLabel(stage) {
  return STAGE_LABELS[stage] || stage || 'Unknown'
}

/**
 * A section's visibility as a word a seller reads.
 *
 * The state is carried by the words, not by the colour: a hidden section says
 * which stage reveals it, so the reason travels with the fact.
 */
export function sectionVisibility(section) {
  if (section?.visible) return 'Visible'
  if (section?.reveal_at_stage) return `Hidden until ${stageLabel(section.reveal_at_stage)}`
  return 'Hidden'
}

/** A timestamp as a short string, or a dash when there is none. */
export function shortTimestamp(value) {
  if (!value) return '--'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return String(value)
  return parsed.toISOString().replace('T', ' ').replace(/\.\d+Z$/, 'Z')
}

/** Parse a comma-separated token list into a trimmed array. */
export function parseTokens(text) {
  return String(text || '')
    .split(',')
    .map((value) => value.trim())
    .filter(Boolean)
}
