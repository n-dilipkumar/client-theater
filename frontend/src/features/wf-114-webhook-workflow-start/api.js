/**
 * Start a workflow from an external system's webhook API (WF-114).
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a
 * method to it is precisely the collision the feature host exists to remove.
 *
 * `PREFIX` is the feature's own: the backend module mounts under `/api/wf-114`,
 * and `apiRequest` already prepends `/api`.
 *
 * Every label, every picker option and every status word on the page renders from
 * `vocabulary()` and `inferences()` rather than from a list compiled here, so a
 * value added server-side reaches every client at once and the page cannot
 * disagree with the validator about what is legal.
 */

import { apiRequest } from '@/lib/api'

/** Drop empty values so we never send `?state=` and confuse a filter. */
function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

const PREFIX = '/wf-114'

/**
 * The documented payload example, quoted in the research: "Documented payload
 * example {"customer_id": "CUST_001", "amount": 100, "currency": "USD",
 * "status": "successful"}". It is the payload the page sends as a test request.
 */
export const DOCUMENTED_PAYLOAD = {
  customer_id: 'CUST_001',
  amount: 100,
  currency: 'USD',
  status: 'successful',
}

/**
 * The mapping the page declares on a new trigger: the three unconditional
 * variables the payload carries, plus one row that binds only when the payload's
 * status says the payment succeeded. The conditional row is what makes an
 * unmatched payload a state a reviewer can see rather than a crash.
 */
export const DEFAULT_MAPPINGS = [
  { variable: 'customer_ref', path: 'customer_id', type: 'text' },
  { variable: 'amount', path: 'amount', type: 'number' },
  { variable: 'currency', path: 'currency', type: 'text' },
  {
    variable: 'lead_status',
    path: 'status',
    type: 'text',
    when: { path: 'status', operator: 'equals', value: 'successful' },
  },
]

export const webhookWorkflowApi = {
  /** Every researched term: the methods, the variable types, the operators, the states. */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),
  /** Every judgement call this workflow rests on, and how to change it. */
  inferences: () => apiRequest(`${PREFIX}/inferences`),

  summary: (roomId) => apiRequest(`${PREFIX}/summary${query({ room_id: roomId })}`),

  workflows: (roomId) => apiRequest(`${PREFIX}/workflows${query({ room_id: roomId })}`),
  workflow: (id) => apiRequest(`${PREFIX}/workflows/${id}`),
  createWorkflow: (roomId, payload) =>
    apiRequest(`${PREFIX}/workflows${query({ room_id: roomId })}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  patchWorkflow: (id, payload) =>
    apiRequest(`${PREFIX}/workflows/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  /** The last clause of step 6: add downstream actions, then publish. */
  publishWorkflow: (id) => apiRequest(`${PREFIX}/workflows/${id}/publish`, { method: 'POST' }),

  variables: (workflowId) => apiRequest(`${PREFIX}/workflows/${workflowId}/variables`),
  declareVariable: (workflowId, payload) =>
    apiRequest(`${PREFIX}/workflows/${workflowId}/variables`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  triggers: (roomId) => apiRequest(`${PREFIX}/triggers${query({ room_id: roomId })}`),
  trigger: (id) => apiRequest(`${PREFIX}/triggers/${id}`),
  /** Step 1 and 2: select Inbound Webhook, then copy the generated URL. */
  createTrigger: (workflowId, payload) =>
    apiRequest(`${PREFIX}/workflows/${workflowId}/triggers`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  patchTrigger: (id, payload) =>
    apiRequest(`${PREFIX}/triggers/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  /** Step 6: Click Save Trigger. */
  saveTrigger: (id) => apiRequest(`${PREFIX}/triggers/${id}/save`, { method: 'POST' }),

  /**
   * The generated webhook URL. In the product an external service calls this, not
   * the page. The page calls it so a team can walk the flow end to end, and a test
   * request (step 4) validates the mapping without starting the workflow.
   *
   * A delivery has to be signed, and this build fails closed, so the page signs it
   * in the browser with the trigger's one-time signing value as the external
   * service would: HMAC-SHA256 over `{timestamp}.{body}`, hex encoded. The value
   * is only in memory for the trigger this session created.
   */
  deliverToHook: async (token, payload, { test = false, signingValue } = {}) => {
    const body = JSON.stringify(payload)
    const headers = await signedHeaders(signingValue, body)
    return apiRequest(`${PREFIX}/hooks/${token}${query({ test: test ? 1 : undefined })}`, {
      method: 'POST',
      body,
      headers,
    })
  },

  invocations: (roomId, params = {}) =>
    apiRequest(`${PREFIX}/invocations${query({ room_id: roomId, ...params })}`),
  runs: (roomId, params = {}) => apiRequest(`${PREFIX}/runs${query({ room_id: roomId, ...params })}`),

  rooms: () => apiRequest('/records/room?limit=100'),
}

/** Hex-encode a byte array, which is how the signature rides on the wire. */
function toHex(bytes) {
  return [...bytes].map((byte) => byte.toString(16).padStart(2, '0')).join('')
}

/**
 * The two headers a sender computes to sign one delivery.
 *
 * `crypto.subtle` is available in every secure context, and `127.0.0.1` and
 * `localhost` are secure contexts, so the app serves this page over one. The
 * function rejects rather than sending an unsigned delivery, because an unsigned
 * delivery would be refused at the far end and the reader would have to guess why.
 */
export async function signedHeaders(signingValue, body, { when = new Date() } = {}) {
  if (!signingValue) throw new Error('No signing value for this trigger in this session.')
  if (!globalThis.crypto?.subtle) {
    throw new Error('This browser cannot sign a delivery. Send it from the external service.')
  }
  const encoder = new TextEncoder()
  const timestamp = String(Math.floor(when.getTime() / 1000))
  const key = await globalThis.crypto.subtle.importKey(
    'raw',
    encoder.encode(signingValue),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign'],
  )
  const digest = await globalThis.crypto.subtle.sign(
    'HMAC',
    key,
    encoder.encode(`${timestamp}.${body}`),
  )
  return {
    'X-DSR-Signature': toHex(new Uint8Array(digest)),
    'X-DSR-Timestamp': timestamp,
    'Content-Type': 'application/json',
  }
}

/** The word a workflow's badge carries, so no status is by colour alone. */
export function workflowWord(row) {
  return row?.status === 'published' ? 'published' : 'draft'
}

/** The tone a workflow's badge carries. Draft is neutral, published is live. */
export function workflowTone(row) {
  return row?.status === 'published' ? 'insert' : 'neutral'
}

/** The word a trigger's saved state carries. */
export function triggerSavedWord(row) {
  return row?.saved ? 'saved' : 'not saved'
}

/** The tone a trigger's saved state carries. */
export function triggerSavedTone(row) {
  return row?.saved ? 'insert' : 'restore'
}

/** The word an invocation's badge carries. */
export function invocationWord(row) {
  const state = row?.state
  if (state === 'unmapped') return 'unmapped'
  if (state === 'duplicate') return 'duplicate'
  if (state === 'rejected') return 'rejected'
  return 'accepted'
}

/** The tone an invocation's badge carries. */
export function invocationTone(row) {
  if (row?.state === 'unmapped') return 'restore'
  if (row?.state === 'rejected') return 'delete'
  if (row?.state === 'duplicate') return 'update'
  return 'insert'
}

/**
 * Why a delivery is in the state it is, in one sentence a person can act on.
 *
 * "the trigger is not saved" and "the payload did not carry a required key" are
 * different problems with different fixes, so a page that rendered both as
 * "rejected" would be hiding the thing the reader came for.
 */
export function invocationNote(row) {
  if (!row) return ''
  const reasons = {
    unknown_endpoint: 'the URL was not one this deployment issued.',
    trigger_not_saved: 'the trigger is not saved. Choose Save trigger, then send it again.',
    workflow_not_published: 'the workflow is still a draft. Publish it, then send it again.',
    signature_missing: 'the delivery carried no signature. Every generated URL is signed.',
    timestamp_missing: 'the delivery carried no signature timestamp.',
    signature_stale: 'the signature is outside the five-minute window.',
    signature_mismatch: 'the signature did not verify against the trigger signing value.',
    malformed_payload: 'the body was not JSON.',
    body_not_allowed_on_get: 'a GET carried a body. A GET reads its payload from the query string.',
    method_not_served: 'the trigger does not serve that method.',
    required_mapping_unresolved: 'a mapping required a key the payload did not carry.',
    no_mapping_matched: 'no mapping matched, so the workflow did not start.',
  }
  if (row.state === 'accepted') {
    return row.mode === 'test'
      ? 'Test request validated the mapping and started nothing.'
      : `Started the workflow with ${row.bindings?.length ?? 0} variable(s) bound.`
  }
  if (row.state === 'duplicate') {
    return 'This payload was sent before, so the workflow did not start again.'
  }
  return reasons[row.reason] || row.reason || 'no reason was recorded'
}

/** The methods a trigger serves, as one line. */
export function methodsLine(row) {
  const methods = row?.methods || []
  return methods.length ? methods.join(', ') : 'none'
}

/** The one signing rule, as a line a reader can check against their own code. */
export function signingRuleLine(vocabulary) {
  const signing = vocabulary?.signing
  if (!signing) return ''
  return `${signing.rule}, in ${signing.signature_header} and ${signing.timestamp_header}`
}

/** A short, copyable form of a fingerprint, so a table row stays readable. */
export function shortHash(value) {
  const text = String(value || '')
  return text.length > 12 ? `${text.slice(0, 12)}...` : text
}

/**
 * Whether the page can send a delivery to a trigger.
 *
 * The delivery has to be signed, and the signing value is served once, when the
 * trigger is created. So only the trigger this session created has a value in
 * memory; every other trigger can be read but not exercised from here.
 */
export function canSendFromPage(trigger) {
  return Boolean(trigger?.token) && Boolean(trigger?.signingValue)
}

/** The binding rows a delivery produced, as `variable = value` lines. */
export function bindingLines(rows) {
  return (rows || []).map((row) => `${row.variable} = ${JSON.stringify(row.value)}`)
}
