/**
 * WF-068's own API wrapper.
 *
 * `apiRequest` from `@/lib/api` is the transport, exactly as the contract asks: the
 * shared `api` object grows no methods, so a hundred features can each talk to
 * their own `/api/wf-068` routes without anyone editing a shared file.
 *
 * The write and step calls go through `requestWithBody` instead, and that is a
 * finding rather than a preference - the same one WF-060 and WF-066 reported,
 * arriving independently.
 *
 * `apiRequest` reads the error body to build a message and throws the rest of it
 * away, so a failure survives as `status` plus one string. That is not enough for
 * the three responses this workflow exists to produce well:
 *
 *   - a 400 from the validator carries `errors`, a field-keyed map, so a form can
 *     put each message beside the input that caused it;
 *   - a 409 from the state machine carries `status`, `requested` and `allowed`, so
 *     the page can render the three researched statuses as a control instead of
 *     string-matching an English sentence;
 *   - a 403 from the Engage licence gate carries the vendor's own sentence, which
 *     is the text an operator needs to see rather than a paraphrase.
 *
 * The shared client would need three fields added to one function, which is a
 * shared file and a platform decision. Until then the calls that need the body read
 * it themselves. Recorded as promotion work, not smuggled across the boundary.
 *
 * Nothing here ever holds a secret. This workflow stores consent-free business
 * state: CRM records, rules, queues. No credential passes through it.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-068'

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
    error.state = body?.status || null
    error.requested = body?.requested || null
    error.allowed = body?.allowed || null
    error.statuses = body?.statuses || null
    error.body = body
    throw error
  }

  if (response.status === 204) return null
  return response.json()
}

function send(path, method, payload) {
  return requestWithBody(path, { method, body: JSON.stringify(payload ?? {}) })
}

const encode = encodeURIComponent

export const followupApi = {
  // -- the board ----------------------------------------------------------- //

  summary: (roomId) => call(`/summary${query({ room_id: roomId })}`),

  /**
   * The researched vocabulary, so a form cannot drift from the rules.
   *
   * The closed sets the backend validates against are fetched rather than typed
   * here. A dropdown listing four triggers when the server accepts five is a form
   * that submits something the server will refuse.
   */
  vocabulary: () => call('/vocabulary'),

  /**
   * Every derivation this workflow made, with what it rejected.
   *
   * Served by the backend rather than written here, because the research requires
   * the derivation to be recorded and a copy of the record on the client is a
   * second copy that can drift from the first.
   */
  decisions: () => call('/decisions'),
  decision: (decisionId) => call(`/decisions/${encode(decisionId)}`),

  // -- the seller directory, which the licence gate resolves against --------- //

  users: () => call('/users'),

  /** A person and a boolean. Never a credential, so a plain `send` is fine. */
  addUser: (payload) => send(`/users${query({ room_id: payload.room_id })}`, 'POST', payload),

  // -- the CRM records the automations read --------------------------------- //

  crmRecords: (objectType) => call(`/crm/records${query({ object_type: objectType })}`),

  /**
   * Create or update one entity.
   *
   * `error.errors` reaches the form, so each message lands beside its input. That
   * matters most for `modificationDate`, which the vendor makes mandatory on an
   * update and which a form that never sent it would discover only at submit.
   */
  upsertCrmRecord: (payload) =>
    send(`/crm/records${query({ room_id: payload.room_id })}`, 'POST', payload),

  /**
   * Record a sync run.
   *
   * Always via `requestWithBody`: a cadence outside the researched one-to-five
   * minutes comes back as a 400 whose `errors` map names the field.
   */
  syncCrm: (payload) => send(`/crm/sync${query({ room_id: payload.room_id })}`, 'POST', payload),
  syncRuns: () => call('/crm/sync/runs'),

  // -- flows ---------------------------------------------------------------- //

  flows: () => call('/flows'),
  readFlow: (flowId) => call(`/flows/${encode(flowId)}`),
  createFlow: (payload) => send(`/flows${query({ room_id: payload.room_id })}`, 'POST', payload),

  /** The researched Cal half: the `POST /v2/workflows` body this flow would carry. */
  calWorkflow: (flowId, params = {}) =>
    call(`/flows/${encode(flowId)}/cal-workflow${query(params)}`),

  // -- automations ----------------------------------------------------------- //

  automations: () => call('/automations'),
  readAutomation: (automationId) => call(`/automations/${encode(automationId)}`),

  /** `error.errors` reaches the form, so a refused assignee is named per field. */
  createAutomation: (payload) =>
    send(`/automations${query({ room_id: payload.room_id })}`, 'POST', payload),

  /**
   * Evaluate one event.
   *
   * Always 200. An automation that did not fire is a fact about the event, and a
   * caller that got a 400 for it would retry forever.
   */
  evaluate: (event) => send('/automations/evaluate', 'POST', event),

  /**
   * The daily date-trigger schedule.
   *
   * The morning is a parameter, not the browser's date, so the page can show the
   * run it is about to perform and the caller can name one.
   */
  morningRun: (on) => send(`/automations/morning-run${query({ on })}`, 'POST', {}),

  // -- the batch assignment -------------------------------------------------- //

  /**
   * Assign prospects to a flow.
   *
   * The response splits exactly as the vendor documents. The 403 and the 409 both
   * carry the vendor's own sentence in `detail`, which is the text the page shows.
   */
  assign: (payload) =>
    send(
      `/flows/prospects/assign${query({ room_id: payload.room_id })}`,
      'POST',
      payload,
    ),

  assignments: () => call('/assignments'),

  // -- instances and the to-do queue ------------------------------------------ //

  instances: (ownerEmail) => call(`/instances${query({ owner_email: ownerEmail })}`),
  readInstance: (flowInstanceId) => call(`/instances/${encode(flowInstanceId)}`),

  /**
   * Move an instance between the three researched statuses.
   *
   * The 409 carries `allowed`, so the page can render the alternatives as buttons
   * rather than telling the user a step is unavailable.
   */
  setInstanceStatus: (flowInstanceId, status) =>
    send(`/instances/${encode(flowInstanceId)}/status`, 'POST', { status }),

  todos: (params = {}) => call(`/todos${query(params)}`),

  completeTodo: (todoId) => send(`/todos/${encode(todoId)}/complete`, 'POST', {}),

  /** The grouped queue, each row carrying its researched `1/5` position label. */
  queue: (ownerEmail, flowReference) =>
    call(`/queue${query({ owner_email: ownerEmail, flow_reference: flowReference })}`),

  // -- the prep page ---------------------------------------------------------- //

  prepPages: () => call('/prep'),

  /**
   * The prep page as a rep reads it.
   *
   * `mobile` asks for the mobile view, which omits the recent-activity feed.
   */
  prepPage: (meetingId, mobile) => call(`/prep/${encode(meetingId)}${query({ mobile })}`),

  createPrepPage: (payload) => send(`/prep${query({ room_id: payload.room_id })}`, 'POST', payload),

  /** Rehearse with a participant built from the meeting context. Nothing is spoken. */
  dryRun: (meetingId, payload) =>
    send(`/prep/${encode(meetingId)}/dry-run`, 'POST', payload),

  /** Ask anything. The answer names every prep section it drew on. */
  ask: (meetingId, question) =>
    send(`/prep/${encode(meetingId)}/ask`, 'POST', { question }),

  /** Whether the "30 minutes before each external meeting" push window is open. */
  pushWindow: (meetingId) => call(`/prep/${encode(meetingId)}/push-window`),

  // -- AI tracker detections --------------------------------------------------- //

  recordTracker: (payload) => send(`/trackers${query({ room_id: payload.room_id })}`, 'POST', payload),
  trackers: () => call('/trackers'),

  // -- Cal --------------------------------------------------------------------- //

  /** The link is recorded, not fetched, so the page says the fetch did not happen. */
  receiveWebhook: (payload) => send('/webhooks/cal', 'POST', payload),
  webhooks: () => call('/webhooks'),

  /** Always 200: a workflow this product cannot run is a workflow for Cal. */
  validateCalWorkflow: (payload) => send('/cal-workflows/validate', 'POST', payload),

  // -- Gong Forecast reminders --------------------------------------------------- //

  reminders: () => call('/reminders'),

  /**
   * Configure one in-app reminder.
   *
   * The two Slack rows are refused. The 400 says why, so the page can show the
   * researched timing rather than a bare error.
   */
  addReminder: (payload) => send(`/reminders${query({ room_id: payload.room_id })}`, 'POST', payload),
}

/** Every room, so the page can scope the board. Read from the core collection. */
export const listRooms = () => apiRequest('/records/room?limit=100')