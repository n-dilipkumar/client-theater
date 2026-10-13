/**
 * WF-118's own API wrapper and the pure helpers its page needs.
 *
 * `apiRequest` from `@/lib/api` is the transport, exactly as the feature
 * contract asks. The shared `api` object grows no methods, so a hundred features
 * can each talk to their own `/api/wf-118` routes without anyone editing a
 * shared file. Everything this workflow does goes through its own routes.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-118'

const encode = encodeURIComponent

export const guideApi = {
  /** The rules, units, decisions and sources, from one place. */
  vocabulary: () => apiRequest(`${BASE}/vocabulary`),

  /** Counts the ordering page leads with. */
  summary: () => apiRequest(`${BASE}/summary`),

  /** Every guide, filtered the way the ordering table filters. */
  guides: (params = {}) => {
    const search = new URLSearchParams()
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null && value !== '') search.set(key, value)
    }
    const query = search.toString()
    return apiRequest(`${BASE}/guides${query ? `?${query}` : ''}`)
  },

  guide: (key) => apiRequest(`${BASE}/guides/${encode(key)}`),

  create: (body) => apiRequest(`${BASE}/guides`, { method: 'POST', body: JSON.stringify(body) }),

  update: (key, body) =>
    apiRequest(`${BASE}/guides/${encode(key)}`, { method: 'PATCH', body: JSON.stringify(body) }),

  remove: (key) => apiRequest(`${BASE}/guides/${encode(key)}`, { method: 'DELETE' }),

  restore: (key) => apiRequest(`${BASE}/guides/${encode(key)}/restore`, { method: 'POST' }),

  /** The order list and throttle for an application. */
  order: (appId) => apiRequest(`${BASE}/applications/${encode(appId)}/order`),

  setOrder: (appId, body) =>
    apiRequest(`${BASE}/applications/${encode(appId)}/order`, {
      method: 'PUT',
      body: JSON.stringify(body),
    }),

  move: (appId, key, move) =>
    apiRequest(`${BASE}/applications/${encode(appId)}/order/move`, {
      method: 'POST',
      body: JSON.stringify({ key, move }),
    }),

  ranking: (appId, userId, at) => {
    const search = new URLSearchParams()
    if (userId) search.set('user_id', userId)
    if (at) search.set('at', at)
    const query = search.toString()
    return apiRequest(`${BASE}/applications/${encode(appId)}/ranking${query ? `?${query}` : ''}`)
  },

  next: (appId, body) =>
    apiRequest(`${BASE}/applications/${encode(appId)}/next`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  session: (appId, userId) =>
    apiRequest(`${BASE}/applications/${encode(appId)}/sessions/${encode(userId)}`),

  event: (appId, body) =>
    apiRequest(`${BASE}/applications/${encode(appId)}/events`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
}

/** The tier names the research uses: guides that ignore throttling are first. */
export function tierLabel(tier) {
  return tier === 0 ? 'Unthrottled' : 'Throttled'
}

/** A one-line activation summary, so a row does not make the reader re-derive it. */
export function activationSummary(activation) {
  if (!activation) return 'No activation options set.'
  const repeat = activation.repeat || {}
  const stop = activation.stop_after || {}
  const parts = []
  if (repeat.enabled) {
    parts.push(`Repeats every ${repeat.every} ${repeat.unit}${repeat.every === 1 ? '' : 's'}`)
  } else {
    parts.push('Does not repeat')
  }
  parts.push(stop.mode === 'never' ? 'never stops' : `stops after ${stop.count} dismissals`)
  if (activation.delay_seconds) parts.push(`delay ${activation.delay_seconds}s`)
  if (activation.ignore_throttling) parts.push('ignores throttling')
  return parts.join(' \u00b7 ')
}

/** The status badge tone. The label always travels with the tone. */
export function statusTone(status) {
  return status === 'public' ? 'insert' : 'restore'
}

export function statusLabel(status) {
  return status === 'public' ? 'Public' : 'Staging'
}

/** Why a guide is or is not a candidate, in words. */
export function eligibilityText(row) {
  if (!row) return 'Unknown'
  if (row.suppressed) return `Suppressed: ${row.suppress_reason}`
  if (row.active) return 'Displayed'
  if (row.eligible) return 'Eligible now'
  return `Eligible ${row.eligible_at}`
}

/** The stop-after label from the activation, matching the server's own words. */
export function stopAfterLabel(activation) {
  const stop = (activation && activation.stop_after) || {}
  if (!stop.mode || stop.mode === 'never') return 'Never'
  return `After ${stop.count} dismissals`
}

/** The row arrows the ordering table offers. */
export const ROW_MOVES = [
  { move: 'top', label: 'Top', description: 'Move to the top of its tier' },
  { move: 'up', label: 'Up', description: 'Move up one row' },
  { move: 'down', label: 'Down', description: 'Move down one row' },
  { move: 'bottom', label: 'Bottom', description: 'Move to the bottom of its tier' },
]

/** Parse the activation form into the payload the API expects. */
export function activationPayload(form) {
  return {
    repeat: {
      enabled: Boolean(form.repeatEnabled),
      every: Number(form.repeatEvery) || 1,
      unit: form.repeatUnit || 'day',
    },
    stop_after: {
      mode: form.stopMode || 'never',
      count: Number(form.stopCount) || 2,
    },
    ignore_throttling: Boolean(form.ignoreThrottling),
    delay_seconds: Number(form.delaySeconds) || 0,
  }
}

/** The inverse of {@link activationPayload}, for editing an existing guide. */
export function activationForm(activation) {
  const repeat = (activation && activation.repeat) || {}
  const stop = (activation && activation.stop_after) || {}
  return {
    repeatEnabled: Boolean(repeat.enabled),
    repeatEvery: repeat.every || 1,
    repeatUnit: repeat.unit || 'day',
    stopMode: stop.mode || 'never',
    stopCount: stop.count || 2,
    ignoreThrottling: Boolean((activation && activation.ignore_throttling) || false),
    delaySeconds: (activation && activation.delay_seconds) || 0,
  }
}
