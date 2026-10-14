/**
 * WF-071's own API wrapper.
 *
 * `apiRequest` from `@/lib/api` is the transport, exactly as the contract asks.
 * The shared `api` object grows no methods, so a hundred features can each talk
 * to their own `/api/wf-071` routes without anyone editing a shared file.
 *
 * The status is the one field this workflow branches on: 403 means the plan does
 * not cover a watermark, 422 means a config field was refused, and 428 means the
 * link records who viewed so a stamp needs the viewer.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-071'

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

export const watermarkApi = {
  /** The values this workflow accepts, and which workflow each one is borrowed from. */
  vocabulary: () => call('/vocabulary'),

  /** Every decision the research does not make. */
  inferences: () => call('/inferences'),

  summary: (roomId) => call('/summary', { room_id: roomId }),

  /** Every tracked link, each with its watermark settings joined. */
  links: (roomId) => call('/links', { room_id: roomId }),

  read: (linkId) => call(`/links/${encode(linkId)}/watermark`),

  /** Enable the watermark and set its config. `plan` is checked only when turning on. */
  enable: (linkId, payload, plan) =>
    send(`/links/${encode(linkId)}/watermark`, 'POST', payload, { plan }),

  /** Replace the config, or clear it with `{ watermark_config: null }`. */
  patch: (linkId, payload, plan) =>
    send(`/links/${encode(linkId)}/watermark`, 'PATCH', payload, { plan }),

  disable: (linkId) => send(`/links/${encode(linkId)}/watermark`, 'DELETE', {}),

  /** Render a sample overlay from a config, with no write. */
  preview: (payload) => send('/preview', 'POST', payload),

  /** Stamp one view: interpolate the tokens for this viewer and composite. */
  stamp: (linkId, view) => send(`/links/${encode(linkId)}/stamp`, 'POST', view),

  stamps: (linkId) => call(`/links/${encode(linkId)}/stamps`),
}

/** Every room, so the page can scope the list. Read from the core collection. */
export const listRooms = () => apiRequest('/records/room?limit=100')

/** The nine anchors, as words a rep reads rather than the machine token. */
export const POSITION_LABELS = {
  'top-left': 'Top left',
  'top-center': 'Top centre',
  'top-right': 'Top right',
  'middle-left': 'Middle left',
  'middle-center': 'Centre',
  'middle-right': 'Middle right',
  'bottom-left': 'Bottom left',
  'bottom-center': 'Bottom centre',
  'bottom-right': 'Bottom right',
}

/** The five tokens, with what each one stamps. */
export const TOKEN_LABELS = {
  email: 'The viewer\u2019s email',
  date: 'The view date',
  time: 'The view time',
  link: 'The link URL',
  ipAddress: 'The viewer\u2019s IP address',
}

/**
 * The watermark state as a word plus a tone. The word is always printed beside
 * the tone, so the colour is a second signal and never the only one.
 */
export function stateOf(link) {
  if (!link?.enable_watermark) return 'off'
  if (!link.watermark_config) return 'cleared'
  return link.watermark_config.is_tiled ? 'tiled' : 'anchored'
}

export const STATE_TONES = {
  off: 'neutral',
  cleared: 'restore',
  tiled: 'insert',
  anchored: 'update',
}

export function stateTone(state) {
  return STATE_TONES[state] || 'neutral'
}

export const STATE_LABELS = {
  off: 'Off',
  cleared: 'On, no text',
  tiled: 'On, tiled',
  anchored: 'On, one anchor',
}

/** A timestamp as a short string, or a dash when there is none. */
export function shortTimestamp(value) {
  if (!value) return '--'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return String(value)
  return parsed.toISOString().replace('T', ' ').replace(/\.\d+Z$/, 'Z')
}

/** A whole number inside a range, or null when the value is not usable. */
export function wholeInRange(value, min, max) {
  const number = Number(value)
  if (!Number.isInteger(number)) return null
  if (number < min || number > max) return null
  return number
}

/** A decimal inside a range, or null when the value is not usable. */
export function numberInRange(value, min, max) {
  const number = Number(value)
  if (!Number.isFinite(number)) return null
  if (number < min || number > max) return null
  return number
}

/** The text with a token inserted at the end, comma-separated by a space. */
export function withToken(text, token) {
  const snippet = `{{${token}}}`
  const trimmed = String(text || '').trimEnd()
  if (!trimmed) return snippet
  if (trimmed.includes(snippet)) return trimmed
  return `${trimmed} ${snippet}`
}

/** The `{{tokens}}` a text uses, in first-seen order. */
export function tokensIn(text) {
  const found = []
  const pattern = /\{\{\s*([A-Za-z][A-Za-z0-9_]*)\s*\}\}/g
  let match = pattern.exec(String(text || ''))
  while (match) {
    if (!found.includes(match[1])) found.push(match[1])
    match = pattern.exec(String(text || ''))
  }
  return found
}
