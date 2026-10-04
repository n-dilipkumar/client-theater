/**
 * WF-124's own API wrapper.
 *
 * `apiRequest` from `@/lib/api` is the transport, exactly as the contract asks: the
 * shared `api` object grows no methods, so a hundred features can each talk to their
 * own `/api/wf-124` routes without anyone editing a shared file.
 *
 * The write calls go through `requestWithBody` instead, and that is a finding rather
 * than a preference. `apiRequest` reads the error body to build a message and then
 * throws it away, so a failure survives as `status` plus one string. Two responses
 * in this workflow need more than that:
 *
 *   - a 400 from the validator carries `errors`, a field-keyed map, so a form can put
 *     each message beside the input that caused it;
 *   - a 409 from the graph carries `reason`, a stable token, so the page can say
 *     "clear the task that waits on this one" instead of printing an English sentence
 *     and hoping the server did not reword it.
 *
 * The shared client would need three more fields on one function, which is a shared
 * file and a platform decision. Until then the calls that need the body read it
 * themselves. Recorded as promotion work, not smuggled across the boundary.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-124'

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

export const mapApi = {
  // -- the board ------------------------------------------------------------ //

  summary: (roomId) => call(`/summary${query({ room_id: roomId })}`),

  /**
   * The researched vocabulary, including what was deliberately not built.
   *
   * The page reads its statuses, visibilities and reminder lead from here rather
   * than hard-coding them, so a change to the rules cannot leave the page calling a
   * state the API does not serve.
   */
  vocabulary: () => call('/vocabulary'),

  // -- templates ------------------------------------------------------------ //

  templates: () => call('/templates'),
  template: (templateId) => call(`/templates/${encode(templateId)}`),
  createTemplate: (payload) => send('/templates', 'POST', payload),

  // -- plans ---------------------------------------------------------------- //

  plans: (roomId) => call(`/plans${query({ room_id: roomId })}`),
  roomPlans: (roomId) => call(`/rooms/${encode(roomId)}/plans`),
  plan: (planId) => call(`/plans/${encode(planId)}`),
  createPlan: (roomId, payload) => send(`/rooms/${encode(roomId)}/plans`, 'POST', payload),
  renamePlan: (planId, changes) => send(`/plans/${encode(planId)}`, 'PATCH', changes),

  /**
   * The buyer's view of a plan. The internal-only task is absent from this
   * response rather than hidden by the page, so this is the call that enforces the
   * visibility rule.
   */
  sharedPlan: (planId, audience) => call(`/plans/${encode(planId)}/shared${query({ audience })}`),

  /**
   * Marks the plan closed-won and carries its tasks into the implementation plan.
   * The server does the conversion, so the page never rebuilds a plan itself.
   */
  closeWon: (planId) => send(`/plans/${encode(planId)}/close-won`, 'POST', {}),

  // -- tasks ---------------------------------------------------------------- //

  tasks: (planId, audience) => call(`/plans/${encode(planId)}/tasks${query({ audience })}`),
  task: (taskId, audience) => call(`/tasks/${encode(taskId)}${query({ audience })}`),
  addTask: (planId, payload) => send(`/plans/${encode(planId)}/tasks`, 'POST', payload),

  /**
   * `error.errors` reaches the form and `error.reason` reaches the conflict notice,
   * so neither needs the page to parse an English sentence.
   */
  updateTask: (taskId, changes) => send(`/tasks/${encode(taskId)}`, 'PATCH', changes),
  deleteTask: (taskId) => send(`/tasks/${encode(taskId)}`, 'DELETE', {}),

  events: (planId) => call(`/plans/${encode(planId)}/events`),
  escalations: (planId) => call(`/plans/${encode(planId)}/escalations`),
}

/** Every room, so the page can offer one to attach a plan to. */
export const listRooms = () => apiRequest('/records/room?limit=100')

/**
 * The words for each task state, served here so the page cannot drift from the rules.
 *
 * `blocked` is the derived state, not a stored one: the page says so in its hint,
 * because a seller who marks a task blocked by hand and watches it revert would
 * conclude the board is broken.
 */
export const STATUS_WORDS = {
  todo: 'To do',
  in_progress: 'In progress',
  blocked: 'Blocked',
  done: 'Done',
}

/** The tone for each state. A word always accompanies it, so colour never stands alone. */
export const STATUS_TONES = {
  todo: 'neutral',
  in_progress: 'info',
  blocked: 'warning',
  done: 'success',
}

/** The words for each side. The plan tracks people on both sides of the deal. */
export const SIDE_WORDS = {
  seller: 'Seller',
  buyer: 'Buyer',
}

/** The sentence for a graph conflict, from the stable reason token. */
export const CONFLICTS = {
  plan_has_dependents: 'A task that is still open waits on this one. Close it first.',
  task_status_unknown: 'That status is not one this plan serves.',
}

/** The sentence for a conflict token, or a neutral one the backend never sent. */
export function conflictText(reason) {
  return CONFLICTS[reason] || 'That change was refused.'
}
