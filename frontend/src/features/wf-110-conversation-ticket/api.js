/**
 * Conversation tickets API calls (WF-110).
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a
 * method to it is precisely the conflict the feature host exists to remove. The
 * host's `apiRequest` is the escape hatch that makes that unnecessary.
 *
 * `PREFIX` is the feature's own: the backend module mounts these under
 * `/api/wf-110`, and `apiRequest` already prepends `/api`.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-110'

function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value))
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const ticketApi = {
  /** The researched terms: the two triggers, the four actions, the two limits. */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),

  /** What the research leaves open, what this build chose, and how to change it. */
  inferences: () => apiRequest(`${PREFIX}/inferences`),

  /** Counts for the page header, over exactly the rows the filters return. */
  summary: (params = {}) => apiRequest(`${PREFIX}/summary${query(params)}`),

  /** The rows the workflow reads: tickets, triggers, tags and policies. */
  catalog: (params = {}) => apiRequest(`${PREFIX}/catalog${query(params)}`),

  teams: () => apiRequest(`${PREFIX}/teams`),
  tags: () => apiRequest(`${PREFIX}/tags`),
  slaPolicies: () => apiRequest(`${PREFIX}/sla-policies`),

  triggers: (params = {}) => apiRequest(`${PREFIX}/triggers${query(params)}`),
  createTrigger: (payload) =>
    apiRequest(`${PREFIX}/triggers`, { method: 'POST', body: JSON.stringify(payload) }),
  goLive: (id) => apiRequest(`${PREFIX}/triggers/${id}/go-live`, { method: 'POST' }),

  tickets: (params = {}) => apiRequest(`${PREFIX}/tickets${query(params)}`),
  ticketOne: (id) => apiRequest(`${PREFIX}/tickets/${id}`),
  createTicket: (payload) =>
    apiRequest(`${PREFIX}/tickets`, { method: 'POST', body: JSON.stringify(payload) }),
  changeState: (id, state) =>
    apiRequest(`${PREFIX}/tickets/${id}/state`, {
      method: 'POST',
      body: JSON.stringify({ state }),
    }),
  closeConversation: (id) =>
    apiRequest(`${PREFIX}/tickets/${id}/close_conversation`, { method: 'POST' }),

  notifications: (params = {}) => apiRequest(`${PREFIX}/notifications${query(params)}`),
  webhooks: (params = {}) => apiRequest(`${PREFIX}/webhooks${query(params)}`),
  evaluations: (params = {}) => apiRequest(`${PREFIX}/evaluations${query(params)}`),
}

/**
 * The words and badge tone for a ticket state.
 *
 * The state is always rendered as a word, so a reader who cannot distinguish the
 * tones gets the same answer. Colour is a second signal, never the only one.
 * `resolved` and `closed` are different words, because the research keeps them as
 * two different events.
 */
export function stateBadge(state) {
  const spec = {
    submitted: { label: 'Submitted', tone: 'insert' },
    in_progress: { label: 'In progress', tone: 'update' },
    waiting_on_customer: { label: 'Waiting on customer', tone: 'restore' },
    on_hold: { label: 'On hold', tone: 'restore' },
    resolved: { label: 'Resolved (state transition)', tone: 'update' },
    closed: { label: 'Closed (separate event)', tone: 'delete' },
  }[String(state)]
  return spec || { label: String(state || 'Unknown'), tone: 'neutral' }
}

/** The words for how the ticket came to exist, including the tracker exclusion. */
export function viaLabel(via) {
  const known = {
    conversation: 'Converted from a conversation',
    api: 'Created through the REST API',
    inbound: 'Created inbound',
    tracker: 'Created by the tracker (the trigger will not fire)',
  }
  return known[String(via)] || String(via || 'Unknown')
}

/** The words for an action outcome. A skip is a decision, not a failure. */
export function actionOutcomeLabel(outcome) {
  const known = {
    ran: 'Ran',
    skipped_not_converted: 'Skipped: the ticket was not converted from a conversation',
    skipped_unknown_team: 'Skipped: no owning team is assigned',
    skipped_unknown_sla_policy: 'Skipped: the SLA policy is not in the workspace',
    skipped_unknown_tag: 'Skipped: the tag is not in the workspace',
  }
  return known[String(outcome)] || String(outcome || 'Unknown')
}

/** The words for why a trigger did not fire. */
export function skipReasonLabel(reason) {
  const known = {
    trigger_not_live: 'The trigger is a draft',
    trigger_kind_not_matched: 'The trigger watches a different event',
    ticket_type_not_matched: 'The ticket type is outside the conditions',
    ticket_team_not_matched: 'The ticket team is outside the conditions',
    ticket_state_not_matched: 'The new state is outside the conditions',
    tracker_created_ticket: 'Tracker-created tickets never fire this trigger',
  }
  return known[String(reason)] || String(reason || 'Fired')
}

/** The words for a webhook payload shape, which differs by topic. */
export function shapeLabel(shape) {
  const known = {
    flat_item: 'Fields on data.item',
    nested_under_ticket: 'Ticket nested under data.item.ticket',
  }
  return known[String(shape)] || String(shape || 'Unknown')
}

/** The words for an action kind. */
export function actionLabel(kind) {
  const known = {
    notify_team: 'Notify the owning team in Slack',
    assign_team: 'Assign the owning team',
    apply_sla: 'Apply an SLA',
    tag_conversation: 'Tag the linked conversation',
  }
  return known[String(kind)] || String(kind || 'Unknown')
}

/** The words for a trigger kind. */
export function triggerKindLabel(kind) {
  const known = {
    ticket_created: 'A ticket is created',
    ticket_state_changed: 'Teammate changes the state of a ticket',
  }
  return known[String(kind)] || String(kind || 'Unknown')
}
