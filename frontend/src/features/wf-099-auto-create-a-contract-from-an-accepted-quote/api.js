/**
 * WF-099's own API wrapper.
 *
 * `apiRequest` from `@/lib/api` is the transport, exactly as the contract asks.
 * The shared `api` object grows no methods, so a hundred features can each talk
 * to their own `/api/wf-099` routes without anyone editing a shared file.
 *
 * The generic records route is used only to list quotes and rooms to offer in a
 * picker. Everything this workflow does goes through its own routes.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-099'

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
    body: JSON.stringify(payload),
  })
}

export const contractApi = {
  /** The modes, flows, refusal reasons and evidence, from one place. */
  vocabulary: () => call('/vocabulary'),

  summary: (roomId) => call('/summary', { room_id: roomId }),

  settings: (roomId) => call('/settings', { room_id: roomId }),

  updateSettings: (roomId, patch) => send('/settings', 'POST', patch, { room_id: roomId }),

  contracts: (roomId) => call('/contracts', { room_id: roomId }),

  contract: (contractId) => call(`/contracts/${encode(contractId)}`),

  createStandalone: (roomId, body) => send('/contracts', 'POST', body, { room_id: roomId }),

  quoteReadiness: (quoteId) => call(`/quotes/${encode(quoteId)}`),

  /** The acceptance path. Records the platform's acceptance, then creates. */
  accept: (quoteId, body = {}) => send(`/quotes/${encode(quoteId)}/acceptance`, 'POST', body),

  generate: (quoteId, body = {}) => send(`/quotes/${encode(quoteId)}/contract`, 'POST', body),
}

/** Quotes to offer in a picker. Read from the WF-086 collection. */
export const listQuotes = () => apiRequest('/records/wf086_quote?limit=50')

/** Rooms to offer in a picker. Read from the core collection. */
export const listRooms = () => apiRequest('/records/room?limit=100')

/**
 * The label and tone for a generation flow, in words.
 *
 * The tone is never the only signal: every badge carries this label too, so a
 * colour-blind seller still reads which flow ran.
 */
export const FLOW_LABELS = {
  contract_and_order: { label: 'Contract and order', tone: 'insert' },
  contract_only: { label: 'Contract only', tone: 'update' },
  none: { label: 'No contract', tone: 'neutral' },
  standalone: { label: 'Standalone', tone: 'restore' },
}

export function flowLabel(flow) {
  return (FLOW_LABELS[flow] || { label: flow || 'Unknown', tone: 'neutral' }).label
}

export function flowTone(flow) {
  return (FLOW_LABELS[flow] || { tone: 'neutral' }).tone
}

/**
 * Whether a readiness answer means a contract can be created now.
 *
 * A quote is ready when it is published, not legacy, the setting is on and no
 * contract exists yet. The server refuses either way; this only decides which
 * control the page offers.
 */
export function canAccept(readiness) {
  if (!readiness) return false
  return Boolean(
    readiness.published &&
      !readiness.legacy &&
      readiness.plan?.create_contract &&
      !readiness.existing_contract_id,
  )
}

/** The reason a quote is not ready, in words, or null when it is. */
export function blockedReason(readiness) {
  if (!readiness) return 'Select a quote.'
  if (!readiness.published) return 'Publish the quote before it can be accepted.'
  if (readiness.legacy) return 'This is a legacy quote; accepted legacy quotes create no contract.'
  if (readiness.existing_contract_id) return 'This quote already produced a contract.'
  if (!readiness.plan?.create_contract) {
    return 'Contract creation is off in the settings below.'
  }
  return null
}
