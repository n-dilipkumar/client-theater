/**
 * WF-115's own API wrapper and the pure helpers its page needs.
 *
 * `apiRequest` from `@/lib/api` is the transport, exactly as the feature
 * contract asks. The shared `api` object grows no methods, so a hundred features
 * can each talk to their own `/api/wf-115` routes without anyone editing a
 * shared file. Everything this workflow does goes through its own routes.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-115'

const encode = encodeURIComponent

export const marketplaceApi = {
  /** The rules, states, transitions, decisions and sources, from one place. */
  vocabulary: () => apiRequest(`${BASE}/vocabulary`),

  /** Counts per review state and per kind. */
  summary: () => apiRequest(`${BASE}/summary`),

  /** Every module, newest first, optionally filtered. */
  modules: (params = {}) => {
    const search = new URLSearchParams()
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null && value !== '') search.set(key, value)
    }
    const query = search.toString()
    return apiRequest(`${BASE}/modules${query ? `?${query}` : ''}`)
  },

  /** One module with its version, review, and delivery history. */
  module: (moduleId) => apiRequest(`${BASE}/modules/${encode(moduleId)}`),

  /** Resolve a module by its immutable key. */
  byKey: (key) => apiRequest(`${BASE}/keys/${encode(key)}`),

  create: (body) =>
    apiRequest(`${BASE}/modules`, { method: 'POST', body: JSON.stringify(body) }),

  update: (moduleId, body) =>
    apiRequest(`${BASE}/modules/${encode(moduleId)}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),

  submit: (moduleId, changelog) =>
    apiRequest(`${BASE}/modules/${encode(moduleId)}/submit`, {
      method: 'POST',
      body: JSON.stringify({ changelog }),
    }),

  review: (moduleId, decision, note) =>
    apiRequest(`${BASE}/modules/${encode(moduleId)}/review`, {
      method: 'POST',
      body: JSON.stringify({ decision, note }),
    }),

  publish: (moduleId) =>
    apiRequest(`${BASE}/modules/${encode(moduleId)}/publish`, { method: 'POST' }),

  withdraw: (moduleId) =>
    apiRequest(`${BASE}/modules/${encode(moduleId)}/withdraw`, { method: 'POST' }),

  remove: (moduleId) =>
    apiRequest(`${BASE}/modules/${encode(moduleId)}`, { method: 'DELETE' }),

  restore: (moduleId) =>
    apiRequest(`${BASE}/modules/${encode(moduleId)}/restore`, { method: 'POST' }),

  deliver: (moduleId, body) =>
    apiRequest(`${BASE}/modules/${encode(moduleId)}/deliveries`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
}

/**
 * The label and tone for a review state.
 *
 * The tone is a Badge tone, never the only signal: every badge carries the label
 * too, so a colour-blind reader still knows which state a module is in.
 */
export const STATE_LABELS = {
  draft: { label: 'Draft', tone: 'neutral' },
  submitted: { label: 'Submitted', tone: 'update' },
  in_review: { label: 'In review', tone: 'update' },
  changes_requested: { label: 'Changes requested', tone: 'restore' },
  approved: { label: 'Approved', tone: 'insert' },
  published: { label: 'Published', tone: 'insert' },
  withdrawn: { label: 'Withdrawn', tone: 'neutral' },
  rejected: { label: 'Rejected', tone: 'delete' },
  deleted: { label: 'Deleted', tone: 'delete' },
}

export function stateLabel(state) {
  return (STATE_LABELS[state] || { label: state || 'Unknown' }).label
}

export function stateTone(state) {
  return (STATE_LABELS[state] || { tone: 'neutral' }).tone
}

/** Whether the module's derived actions allow submitting it for review. */
export function canSubmit(module) {
  return Boolean(module && module.available_actions && module.available_actions.submit)
}

/** Whether the module's derived actions allow a reviewer to act on it. */
export function canReview(module) {
  return Boolean(module && module.available_actions && module.available_actions.review)
}

/**
 * Parse the sample JSON a builder pastes into the form.
 *
 * Returns `{ ok, value, error }`. The form refuses to submit until this is ok,
 * which is what makes the paste-a-payload step a validation rather than a
 * hopeful string that only fails on the server.
 */
export function parseSamplePayload(text) {
  const trimmed = String(text ?? '').trim()
  if (!trimmed) return { ok: false, value: null, error: 'Paste a sample JSON payload.' }
  let value
  try {
    value = JSON.parse(trimmed)
  } catch (error) {
    return { ok: false, value: null, error: `That is not valid JSON: ${error.message}` }
  }
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    return { ok: false, value: null, error: 'The sample payload must be a JSON object.' }
  }
  return { ok: true, value, error: null }
}

/**
 * The third-party endpoints a module records, as rows for the page.
 *
 * Every URL here is configuration the builder wrote down. The room records these
 * and never calls them; the trigger's own target URL is the platform's.
 */
export function endpointRows(module) {
  if (!module) return []
  const rows = []
  if (module.subscription_url) {
    rows.push({ label: 'Subscription URL (POST)', value: module.subscription_url, note: 'Recorded, not called by this room.' })
  }
  if (module.dynamic_filter_url) {
    rows.push({ label: 'Dynamic filter URL (POST)', value: module.dynamic_filter_url, note: 'Recorded, not called by this room.' })
  }
  const execution = module.execution
  if (execution && execution.method === 'url' && execution.url) {
    rows.push({ label: 'Execution URL (POST)', value: execution.url, note: 'Recorded, not called by this room.' })
  }
  if (execution && execution.method === 'custom_code') {
    rows.push({ label: 'Custom code (JS)', value: 'recorded', note: 'Recorded, never executed here.' })
  }
  if (module.pause_resume_webhook) {
    rows.push({ label: 'Pause/resume webhook', value: module.pause_resume_webhook, note: 'Recorded, not called by this room.' })
  }
  return rows
}
