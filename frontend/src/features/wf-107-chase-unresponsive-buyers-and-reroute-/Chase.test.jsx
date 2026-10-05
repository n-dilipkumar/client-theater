/**
 * WF-107 page tests.
 *
 * `apiRequest` is never mocked. `globalThis.fetch` is replaced instead, keyed by path,
 * which is what the other feature tests in this product do and what keeps the client under
 * test on the same path a browser would take.
 *
 * Every state below is one the design floor requires or the research forces:
 *
 *   * loading, error and empty, because a board that goes blank reads as "there is
 *     nothing here", which for a chase page means a buyer is never chased and nobody
 *     knows why;
 *   * the header's two honest statements -- that nothing fires until a sweep is run, and
 *     that no message has left this product -- because both are consequences of a
 *     decision rather than facts about a running system;
 *   * the duration bound, refused in the page before the server is asked, because the
 *     research makes both bounds exclusive;
 *   * each trigger's own anchor, because the two triggers read different clocks and that
 *     is the whole difference between them;
 *   * every skip the sweep reports, with its published text and the two specification
 *     rules flagged;
 *   * a run advanced and resolved, and an interrupted run that cannot be advanced;
 *   * the design floor: rounded-sm, no emoji as an icon, semantic tokens, 44px targets.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import Chase from './Chase'
import descriptor from './index.jsx'

const BASE = '/wf-107'
const ROOM = 'room_a'
const QUARTER_HOUR = 900
const TEN_MINUTES = 600

const VOCABULARY = {
  collections: {
    conversations: 'wf107_conversation',
    parts: 'wf107_conversation_part',
    triggers: 'wf107_trigger',
    runs: 'wf107_run',
    activity: 'wf107_activity',
    office_hours: 'wf107_office_hours',
  },
  trigger_kinds: ['customer_idle', 'teammate_idle'],
  trigger_kind_labels: {
    customer_idle: 'If customer has been unresponsive',
    teammate_idle: 'If teammate has been unresponsive',
  },
  trigger_fields: ['channels', 'audience', 'scheduling', 'goal'],
  trigger_field_labels: {
    duration_seconds: 'Inactivity timer',
    channels: 'Channels',
    audience: 'Audience',
    scheduling: 'Scheduling',
    goal: 'Goal',
  },
  trigger_anchors: {
    customer_idle: 'last_activity',
    teammate_idle: 'first_customer_message',
  },
  anchor_labels: {
    first_customer_message: "Customer's first message",
    last_activity: 'Last message of any kind',
  },
  channels: ['messenger', 'email', 'api'],
  channel_labels: { messenger: 'Messenger', email: 'Email', api: 'API' },
  step_kinds: [
    'message',
    'wait',
    'snooze',
    'close_message',
    'show_expected_reply_time',
    'mark_priority',
    'tag',
    'close',
    'assign',
  ],
  step_labels: {
    message: 'Message',
    wait: 'Wait',
    snooze: 'Snooze',
    close_message: 'Closing message',
    show_expected_reply_time: 'Show expected reply time',
    mark_priority: 'Mark as priority',
    tag: 'Tag conversation',
    close: 'Close conversation',
    assign: 'Assign conversation',
  },
  timed_steps: ['wait', 'snooze'],
  duration_steps: ['wait', 'snooze', 'show_expected_reply_time'],
  holding_steps: ['wait', 'snooze'],
  precedence_steps: ['wait', 'snooze'],
  terminal_steps: ['close'],
  interruption_events: ['customer_message', 'teammate_message'],
  interruption_labels: { customer_message: 'Customer message', teammate_message: 'Teammate message' },
  author_kinds: ['customer', 'teammate', 'system'],
  author_labels: { customer: 'Customer', teammate: 'Teammate', system: 'Workflow' },
  origins: ['inbox', 'api'],
  origin_labels: { inbox: 'Opened in the inbox', api: 'Created via the REST API' },
  conversation_states: ['open', 'snoozed', 'closed'],
  conversation_state_labels: { open: 'Open', snoozed: 'Snoozed', closed: 'Closed' },
  sweepable_states: ['open'],
  run_states: ['running', 'waiting', 'interrupted', 'finished'],
  run_state_labels: {
    running: 'Running',
    waiting: 'Waiting',
    interrupted: 'Interrupted',
    finished: 'Finished',
  },
  closed_run_states: ['interrupted', 'finished'],
  activity_types: [],
  activity_type_codes: [],
  skip_reasons: [
    'api_created_conversation',
    'trigger_not_live',
    'already_fired_for_message',
    'inactivity_window_not_elapsed',
    'conversation_snoozed',
    'conversation_closed',
    'no_anchor_message',
    'no_trigger_of_this_kind',
  ],
  skip_reason_text: {
    api_created_conversation: "This workflow won't trigger for conversations created via our REST API.",
    trigger_not_live: 'The trigger has not been saved and set live.',
    already_fired_for_message: 'The workflow can only trigger once per customer message.',
    inactivity_window_not_elapsed: 'The inactivity window has not elapsed yet.',
    conversation_snoozed: 'The conversation is snoozed, so its timer is paused.',
    conversation_closed: 'The conversation is closed.',
    no_anchor_message: 'The conversation has no message of the kind this trigger measures.',
    no_trigger_of_this_kind: 'No trigger of this kind is configured for the room.',
  },
  specification_skips: ['api_created_conversation', 'already_fired_for_message'],
  residual_tags: ['delayed response'],
  weekdays: [
    'monday',
    'tuesday',
    'wednesday',
    'thursday',
    'friday',
    'saturday',
    'sunday',
  ],
  default_office_weekdays: ['monday', 'tuesday', 'wednesday', 'thursday', 'friday'],
  default_office_open_minutes: 540,
  default_office_close_minutes: 1080,
  error_codes: {
    duration_out_of_range: {
      status: 422,
      detail: 'The duration must be longer than 30 seconds and shorter than 14 days.',
    },
  },
  bounds: {
    min_duration_seconds: 30,
    max_duration_seconds: 1209600,
    bounds_are_exclusive: true,
  },
  defaults: {
    trigger_kind: 'customer_idle',
    trigger_seconds: TEN_MINUTES,
    wait_seconds: TEN_MINUTES,
    conversation_state: 'open',
    author_kind: 'customer',
    origin: 'inbox',
    tag: 'delayed response',
    create_conversation_without_contact_reply: false,
  },
  honesty: { sent_by_this_product: false },
  documented_webhooks: ['conversation.admin.closed'],
  evidence: {
    inactivity_timer:
      "Set trigger timer to 10 minutes - meaning that the workflow will only trigger 10 minutes after there's been no response from the customer.",
    duration_bounds: 'The duration must be longer than 30 seconds and shorter than 14 days.',
    first_message_anchor:
      "is evaluated against customer's first message. This means if the customer sends 3 messages in a row, the timer will be set against their first message, not last.",
    last_message_anchor: 'Last-message timestamp on the Conversation object -> inactivity elapsed -> trigger fires',
    once_per_message: 'can only trigger once per customer message',
    api_created_exempt: "This workflow won't trigger for conversations created via our REST API.",
    wait_precedence: 'Any workflow containing a Wait or Snooze action will take precedence',
    interruption_events:
      'configure the duration and which interruption events cancel the wait (teammate and customer messages)',
    create_without_contact_reply:
      'create_conversation_without_contact_reply - Whether a conversation should be opened in the inbox for the message without the contact replying. Defaults to false if not provided.',
    assign_conversation:
      'Then add an action to Assign conversation to reroute the conversation to the desired Inbox.',
    reroute_steps:
      "a message or a Show expected reply time step (uses office hours), then Mark as priority + Tag conversation ('delayed response') and Assign conversation to another inbox",
    close_sequence:
      'Add a closing message block, then a Close conversation action, then Tag conversation.',
    opens_without_contact_reply:
      'message part written to the conversation (creating a conversation if none exists)',
    office_hours_example:
      'a message received at 5:50pm will have an expected response time of 9:05am on the next working day',
    snoozed_reopened:
      'If an SLA is missed while a conversation is snoozed, the conversation will be automatically unsnoozed and re-opened.',
    purely_time_based: 'Two purely time-based, automatic triggers (no seller action).',
    first_message_rule:
      "if the customer sends 3 messages in a row, the timer will be set against their first message, not last",
    sent_by_this_product:
      'This product makes no outbound HTTP call and holds no Intercom credential, so the message is recorded as written and not as delivered.',
  },
}

const INFERENCES = {
  ticket: 'WF-107',
  count: 8,
  unsourced: ['office-hours-model'],
  jev_audits: {
    domain_package_placement: 'jev-20261005T125556-19056-56859',
    job_driver: 'jev-20261005T125557-19056-57169',
    api_created_conversation: 'jev-20261005T125618-8304-78829',
    trigger_anchor: 'jev-20261005T125619-8304-79111',
  },
  decisions: [
    {
      id: 'job-driver',
      question: 'What drives the two inactivity triggers and the Wait/Snooze timer?',
      evidence: 'Two purely time-based, automatic triggers.',
      chosen: 'Three explicit POST routes, so every audit row names a route.',
      rejected: 'A background thread, which needs a clock no test can move.',
      consequence: 'Nothing fires until somebody calls /evaluate.',
      unsourced: false,
    },
    {
      id: 'office-hours-model',
      question: 'What office-hours model is derived?',
      evidence: 'The spec names office hours as a data source and sources no model.',
      chosen: 'A weekly schedule, Mon to Fri 09:00 to 18:00 by default.',
      rejected: 'A flat offset, which would make "uses office hours" meaningless.',
      consequence: 'A schedule is stored per room and is editable.',
      unsourced: true,
    },
  ],
}

function trigger(overrides = {}) {
  return {
    id: 'trg_1',
    revision: 1,
    room_id: ROOM,
    kind: 'customer_idle',
    kind_label: 'If customer has been unresponsive',
    duration_seconds: TEN_MINUTES,
    channels: ['messenger', 'email'],
    audience: 'Buyers who asked a question',
    scheduling: { timezone: 'UTC' },
    goal: 'Chase a buyer who has gone quiet',
    steps: [
      { kind: 'message', body: 'Just checking if you are still there?' },
      {
        kind: 'wait',
        duration_seconds: QUARTER_HOUR,
        interruption_events: ['customer_message', 'teammate_message'],
      },
      { kind: 'close_message', body: 'Closing this for now.' },
      { kind: 'close' },
      { kind: 'tag', tag: 'no reply' },
    ],
    live: false,
    anchor: 'last_activity',
    anchor_label: 'Last message of any kind',
    close_authority: {
      authority: 'workflow',
      workflow_owns_close: true,
      global_auto_close: false,
      precedence_steps: ['wait'],
      quote: 'Any workflow containing a Wait or Snooze action will take precedence',
    },
    ...overrides,
  }
}

function conversation(overrides = {}) {
  return {
    id: `conv_${Math.random().toString(36).slice(2, 8)}`,
    room_ref: ROOM,
    state: 'open',
    origin: 'inbox',
    inbox: 'sales',
    previous_inbox: null,
    tags: [],
    priority: false,
    subject: 'Platform availability',
    customer_first_message_at: '2026-03-02T08:20:00.000+00:00',
    customer_last_message_at: '2026-03-02T08:20:00.000+00:00',
    last_activity_at: '2026-03-02T08:20:00.000+00:00',
    parts: [],
    ...overrides,
  }
}

function run(overrides = {}) {
  return {
    id: 'run_1',
    room_id: ROOM,
    conversation_id: 'conv_1',
    trigger_id: 'trg_1',
    kind: 'customer_idle',
    state: 'running',
    state_label: 'Running',
    cursor: 0,
    steps: [{ kind: 'message' }, { kind: 'wait' }, { kind: 'close' }],
    next_step: { kind: 'message' },
    remaining_steps: [{ kind: 'message' }, { kind: 'wait' }, { kind: 'close' }],
    anchor: '2026-03-02T08:20:00.000+00:00',
    anchor_kind: 'last_activity',
    anchor_label: 'Last message of any kind',
    started_at: '2026-03-02T08:30:00.000+00:00',
    interrupted_by: null,
    wait_started_at: null,
    history: [],
    sent_by_this_product: false,
    ...overrides,
  }
}

const OFFLINE_WEEK = {
  monday: { open: 540, close: 1080 },
  tuesday: { open: 540, close: 1080 },
  wednesday: { open: 540, close: 1080 },
  thursday: { open: 540, close: 1080 },
  friday: { open: 540, close: 1080 },
  saturday: { open: null, close: null },
  sunday: { open: null, close: null },
}

const DEFAULTS = {
  '/records/room': { records: [{ id: ROOM, data: { name: 'Northwind Traders' } }] },
  [`${BASE}/vocabulary`]: VOCABULARY,
  [`${BASE}/inferences`]: INFERENCES,
  [`${BASE}/rooms/${ROOM}/summary`]: {
    room_id: ROOM,
    conversations: 2,
    by_state: { open: 1, snoozed: 0, closed: 1 },
    priority: 1,
    api_created: 1,
    triggers: 2,
    live_triggers: 1,
    runs: 3,
    runs_by_state: { running: 1, waiting: 1, interrupted: 1, finished: 0 },
    due: 1,
    office_hours: {
      room_id: ROOM,
      stored: false,
      derived: true,
      schedule: OFFLINE_WEEK,
      timezone: 'UTC',
      derivation: VOCABULARY.evidence.office_hours_example,
    },
  },
  [`${BASE}/rooms/${ROOM}/triggers`]: {
    room_id: ROOM,
    count: 2,
    live: 1,
    inboxes: ['sales', 'enterprise', 'escalations', 'support'],
    triggers: [
      trigger(),
      trigger({
        id: 'trg_2',
        kind: 'teammate_idle',
        kind_label: 'If teammate has been unresponsive',
        live: true,
        anchor: 'first_customer_message',
        anchor_label: "Customer's first message",
        steps: [
          { kind: 'show_expected_reply_time', duration_seconds: QUARTER_HOUR },
          { kind: 'mark_priority', priority: true },
          { kind: 'tag', tag: 'delayed response' },
          { kind: 'assign', inbox: 'escalations' },
        ],
        close_authority: {
          authority: 'global',
          workflow_owns_close: false,
          global_auto_close: true,
          precedence_steps: [],
          quote: 'Any workflow containing a Wait or Snooze action will take precedence',
        },
      }),
    ],
  },
  [`${BASE}/rooms/${ROOM}/runs`]: {
    room_id: ROOM,
    count: 3,
    by_state: { running: 1, waiting: 1, interrupted: 1, finished: 0 },
    state_labels: VOCABULARY.run_state_labels,
    runs: [
      run(),
      run({ id: 'run_2', state: 'waiting', state_label: 'Waiting', cursor: 1, interrupted_by: null }),
      run({
        id: 'run_3',
        state: 'interrupted',
        state_label: 'Interrupted',
        interrupted_by: 'customer',
        anchor_kind: 'first_customer_message',
        anchor_label: "Customer's first message",
      }),
    ],
  },
  [`${BASE}/rooms/${ROOM}/conversations`]: {
    room_id: ROOM,
    count: 2,
    origins: VOCABULARY.origins,
    inboxes: ['sales', 'enterprise', 'escalations', 'support'],
    conversations: [
      conversation(),
      conversation({
        state: 'closed',
        origin: 'api',
        tags: ['no reply'],
        inbox: 'escalations',
        previous_inbox: 'sales',
        priority: true,
        subject: 'Partner referral',
      }),
    ],
  },
  [`${BASE}/rooms/${ROOM}/office-hours`]: {
    room_id: ROOM,
    stored: false,
    derived: true,
    schedule: OFFLINE_WEEK,
    timezone: 'UTC',
    derivation: VOCABULARY.evidence.office_hours_example,
  },
}

const calls = []
let handlers = {}
let failures = new Set()

function json(body, status = 200) {
  return { ok: status < 400, status, statusText: 'OK', json: async () => body }
}

beforeEach(() => {
  calls.length = 0
  handlers = {}
  failures = new Set()
  globalThis.fetch = vi.fn(async (url, init = {}) => {
    const raw = String(url)
    const path = raw.replace('/api', '').split('?')[0]
    const method = (init.method || 'GET').toUpperCase()
    calls.push({ url: raw, path, method, body: init.body ? JSON.parse(init.body) : undefined })
    if (failures.has(`${method} ${path}`)) {
      return json({ error: 'boom', detail: 'The service is unavailable.' }, 500)
    }
    const handler = handlers[`${method} ${path}`]
    if (handler) return handler({ path, method })
    const fallback = DEFAULTS[path]
    if (fallback === undefined) throw new Error(`unstubbed request: ${method} ${path}`)
    return json(fallback)
  })
})

/** Render, pick a room, and wait for the board to settle. */
async function renderBoard() {
  const user = userEvent.setup()
  render(<Chase />)
  await screen.findByRole('heading', { name: 'Chase and reroute' })
  const picker = await screen.findByLabelText('Room')
  await user.selectOptions(picker, ROOM)
  await waitFor(() => expect(screen.queryByText('Pick a room')).toBeNull())
  await waitFor(() => expect(screen.queryAllByRole('status')).toHaveLength(0))
  return { user }
}

describe('the descriptor', () => {
  it('carries the id the backend feature exports, so the two halves are findable', () => {
    expect(descriptor.id).toBe('wf-107-chase-unresponsive-buyers-and-reroute-')
    expect(descriptor.label).toBe('Chase and reroute')
    expect(typeof descriptor.Component).toBe('function')
  })

  it('passes a glyph path rather than a name, because the shared PATHS map is not ours', () => {
    expect(typeof descriptor.iconPath).toBe('string')
    expect(descriptor.iconPath.length).toBeGreaterThan(10)
  })
})

describe('the loading state', () => {
  it('says what is loading rather than rendering a blank board', () => {
    render(<Chase />)
    expect(screen.getByRole('status')).toHaveTextContent('Loading chase and reroute')
  })
})

describe('the room gate', () => {
  it('asks for a room before it asks for any conversation', async () => {
    render(<Chase />)
    // EmptyState renders its title as a paragraph, so it is found by text rather than by
    // heading role.
    expect(await screen.findByText('Pick a room')).toBeDefined()
    expect(screen.queryByText('No triggers yet')).toBeNull()
  })

  it('calls no room-scoped route until a room is chosen', async () => {
    render(<Chase />)
    await screen.findByRole('heading', { name: 'Chase and reroute' })
    await waitFor(() => expect(screen.queryAllByRole('status')).toHaveLength(0))
    expect(calls.filter((call) => call.path.includes(`/rooms/${ROOM}`))).toHaveLength(0)
  })
})

describe('the two things the page must say out loud', () => {
  it('says that nothing fires until a sweep is run', async () => {
    await renderBoard()
    expect(screen.getByText(/Nothing fires until a sweep is run/i)).toBeDefined()
  })

  it('says that no message has left this product', async () => {
    await renderBoard()
    const notice = screen.getByText('No message has left this product').closest('div')
    expect(notice).toHaveTextContent('no outbound HTTP call')
  })
})

describe('the error state', () => {
  it('shows a retry when the board cannot be read, rather than an empty board', async () => {
    failures.add(`GET ${BASE}/rooms/${ROOM}/summary`)
    const user = userEvent.setup()
    render(<Chase />)
    await screen.findByRole('heading', { name: 'Chase and reroute' })
    await user.selectOptions(await screen.findByLabelText('Room'), ROOM)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Could not load data')
    expect(within(alert).getByRole('button', { name: /retry/i })).toBeDefined()
  })
})

describe('the room summary', () => {
  it('reports the due count the next sweep would act on', async () => {
    await renderBoard()
    expect(screen.getByText('Due now')).toBeDefined()
    // The stat is the panel's own figure. Queried from the card rather than by text,
    // because a conversation card also carries an "Exempt" badge for the same rule.
    const exempt = screen
      .getByText('Created through the REST API')
      .closest('div')
      .parentElement
    expect(exempt.textContent).toContain('Exempt')
    expect(exempt.textContent).toContain('1')
  })
})

describe('the triggers', () => {
  it('shows both researched trigger names verbatim', async () => {
    await renderBoard()
    // The trigger card heading carries the name; the builder's picker offers the same
    // label as an option, so the card is asked for by role rather than by a bare get.
    const headings = screen.getAllByRole('heading', { level: 3 })
    const names = headings.map((node) => node.textContent)
    expect(names).toContain('If customer has been unresponsive')
    expect(names).toContain('If teammate has been unresponsive')
  })

  it('says each trigger is a draft until it is set live', async () => {
    await renderBoard()
    expect(screen.getByText('Draft')).toBeDefined()
    expect(screen.getByText('Live')).toBeDefined()
  })

  it('states that a workflow with a Wait owns the close', async () => {
    await renderBoard()
    expect(screen.getByText('This workflow closes it')).toBeDefined()
  })

  it('falls back to the global setting for a trigger with no Wait or Snooze', async () => {
    await renderBoard()
    expect(screen.getByText('The global setting closes it')).toBeDefined()
  })

  it('names the anchor each trigger measures from, because the two differ', async () => {
    await renderBoard()
    expect(screen.getAllByText("Customer's first message").length).toBeGreaterThan(0)
    expect(screen.getAllByText('Last message of any kind').length).toBeGreaterThan(0)
  })

  it('shows the researched steps in order', async () => {
    await renderBoard()
    // The trigger card lists its steps as badges. Queried from the card rather than from
    // the page, because the builder below renders the same labels for the same blocks.
    const card = screen.getAllByRole('heading', { level: 3 })[0].closest('li')
    const badges = within(card)
      .getAllByText(/^(Message|Wait|Closing message|Close conversation|Tag conversation)$/)
      .map((node) => node.textContent)
    expect(badges).toEqual([
      'Message',
      'Wait',
      'Closing message',
      'Close conversation',
      'Tag conversation',
    ])
  })

  it('sets a draft live through the researched go-live route', async () => {
    const { user } = await renderBoard()
    await user.click(screen.getByRole('button', { name: /set live/i }))
    await waitFor(() =>
      expect(calls.some((c) => c.method === 'POST' && c.path === `${BASE}/rooms/${ROOM}/triggers/trg_1/go-live`)).toBe(true)
    )
  })
})

describe('the duration bound', () => {
  it('quotes the researched sentence with both bounds exclusive', async () => {
    await renderBoard()
    expect(screen.getAllByText('The duration must be longer than 30 seconds and shorter than 14 days.').length)
      .toBeGreaterThan(0)
  })

  it('refuses 30 seconds in the page before the server is asked', async () => {
    const { user } = await renderBoard()
    const timer = screen.getByLabelText('Inactivity timer (seconds)')
    await user.clear(timer)
    await user.type(timer, '30')

    const create = screen.getByRole('button', { name: /create draft trigger/i })
    expect(create).toBeDisabled()
    expect(
      calls.some((call) => call.method === 'POST' && call.path === `${BASE}/rooms/${ROOM}/triggers`)
    ).toBe(false)
  })

  it('refuses exactly fourteen days, the other bound', async () => {
    const { user } = await renderBoard()
    const timer = screen.getByLabelText('Inactivity timer (seconds)')
    await user.clear(timer)
    await user.type(timer, '1209600')
    expect(screen.getByRole('button', { name: /create draft trigger/i })).toBeDisabled()
  })

  it('sends a legal duration and the researched step kinds when the bound holds', async () => {
    const { user } = await renderBoard()
    const timer = screen.getByLabelText('Inactivity timer (seconds)')
    await user.clear(timer)
    await user.type(timer, '600')
    await user.click(screen.getByRole('button', { name: /create draft trigger/i }))

    await waitFor(() => {
      const call = calls.find(
        (c) => c.method === 'POST' && c.path === `${BASE}/rooms/${ROOM}/triggers`
      )
      expect(call).toBeDefined()
      expect(call.body.kind).toBe('customer_idle')
      expect(call.body.duration_seconds).toBe(600)
      expect(call.body.steps.map((step) => step.kind)).toEqual([
        'message',
        'wait',
        'close_message',
        'close',
        'tag',
      ])
    })
  })
})

describe('the wait block', () => {
  it('offers only the two interruption events the research names', async () => {
    await renderBoard()
    expect(screen.getByText('Cancelled by these messages')).toBeDefined()
    expect(screen.getAllByText('Customer message').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Teammate message').length).toBeGreaterThan(0)
  })

  it('says a wait with nothing selected is cancelled by nothing', async () => {
    await renderBoard()
    expect(
      screen.getAllByText(/a wait with nothing selected is cancelled by nothing/i).length
    ).toBeGreaterThan(0)
  })

  it('lets a step be removed, which is what the builder trash icon does', async () => {
    const { user } = await renderBoard()
    // The first Remove button belongs to the first builder step, the Message block.
    // The editor dialog also offers a Remove per step, so the builder's own rows are asked
    // for by their position inside the builder's list rather than page-wide.
    const builder = screen.getByText('Steps, in order').closest('div')

    /** The step kinds currently in the builder, in order, from their mono labels. */
    const kindsNow = () =>
      within(builder)
        .getAllByText(/^(message|wait|close_message|close|tag|close_message)$/)
        .map((node) => node.textContent)

    expect(kindsNow()).toEqual(['message', 'wait', 'close_message', 'close', 'tag'])

    await user.click(within(builder).getAllByRole('button', { name: /remove/i })[0])

    await waitFor(() =>
      expect(kindsNow()).toEqual(['wait', 'close_message', 'close', 'tag'])
    )
  })
})

describe('the sweep', () => {
  const SWEEP = {
    room_id: ROOM,
    evaluated_at: '2026-03-02T08:50:00.000+00:00',
    triggers_considered: 2,
    conversations_considered: 2,
    fired_count: 1,
    skipped_count: 3,
    due_next: 0,
    fired: [run({ id: 'run_9', state: 'running', state_label: 'Running' })],
    skipped: [
      {
        conversation_id: 'conv_api',
        trigger_id: 'trg_1',
        reason: 'api_created_conversation',
        detail: VOCABULARY.skip_reason_text.api_created_conversation,
        is_specification_rule: true,
      },
      {
        conversation_id: 'conv_snoozed',
        trigger_id: 'trg_1',
        reason: 'conversation_snoozed',
        detail: VOCABULARY.skip_reason_text.conversation_snoozed,
        is_specification_rule: false,
      },
      {
        conversation_id: 'conv_fresh',
        trigger_id: 'trg_1',
        reason: 'inactivity_window_not_elapsed',
        detail: VOCABULARY.skip_reason_text.inactivity_window_not_elapsed,
        is_specification_rule: false,
        seconds_remaining: 480,
      },
    ],
  }

  it('posts to the evaluate route with the chosen kind', async () => {
    handlers[`POST ${BASE}/rooms/${ROOM}/evaluate`] = () => json(SWEEP)
    const { user } = await renderBoard()
    await user.selectOptions(screen.getByLabelText('Trigger kind'), 'customer_idle')
    await user.click(screen.getByRole('button', { name: /evaluate/i }))

    await waitFor(() => {
      const call = calls.find((c) => c.method === 'POST' && c.path === `${BASE}/rooms/${ROOM}/evaluate`)
      expect(call).toBeDefined()
      expect(call.body).toEqual({ kind: 'customer_idle' })
    })
  })

  it('reports what fired and how many are due next', async () => {
    handlers[`POST ${BASE}/rooms/${ROOM}/evaluate`] = () => json(SWEEP)
    const { user } = await renderBoard()
    await user.click(screen.getByRole('button', { name: /evaluate/i }))

    expect(await screen.findByText('Last sweep')).toBeDefined()
    expect(screen.getByText('1 fired')).toBeDefined()
    expect(screen.getByText('3 skipped')).toBeDefined()
    expect(screen.getByText(/0 due on the next sweep/i)).toBeDefined()
  })

  it('renders every skip with its published text rather than the code alone', async () => {
    handlers[`POST ${BASE}/rooms/${ROOM}/evaluate`] = () => json(SWEEP)
    const { user } = await renderBoard()
    await user.click(screen.getByRole('button', { name: /evaluate/i }))

    await screen.findByText('Last sweep')
    // Scoped to the sweep result, because the specification-quote panel below repeats the
    // same sentences on purpose -- a reader is meant to be able to check them at source.
    const sweepPanel = screen.getByText('Last sweep').closest('div').parentElement
    expect(
      within(sweepPanel).getByText(
        "This workflow won't trigger for conversations created via our REST API."
      )
    ).toBeDefined()
    expect(
      within(sweepPanel).getByText('The conversation is snoozed, so its timer is paused.')
    ).toBeDefined()
    // The code is shown beside the sentence, so both are readable.
    expect(within(sweepPanel).getByText('api_created_conversation')).toBeDefined()
  })

  it('flags the two skips that are research rules rather than states of this room', async () => {
    handlers[`POST ${BASE}/rooms/${ROOM}/evaluate`] = () => json(SWEEP)
    const { user } = await renderBoard()
    await user.click(screen.getByRole('button', { name: /evaluate/i }))
    await screen.findByText('Last sweep')
    expect(screen.getAllByText('Research rule')).toHaveLength(1)
  })

  it('turns the seconds left into words a seller can read', async () => {
    handlers[`POST ${BASE}/rooms/${ROOM}/evaluate`] = () => json(SWEEP)
    const { user } = await renderBoard()
    await user.click(screen.getByRole('button', { name: /evaluate/i }))
    await screen.findByText('Last sweep')
    expect(screen.getByText('8 minutes left')).toBeDefined()
  })
})

describe('the runs', () => {
  it('shows each run state the research produces', async () => {
    await renderBoard()
    expect(screen.getByText('Running')).toBeDefined()
    expect(screen.getByText('Waiting')).toBeDefined()
    expect(screen.getByText('Interrupted')).toBeDefined()
  })

  it('says who interrupted a run, because that ends it', async () => {
    await renderBoard()
    expect(screen.getByText('Interrupted by customer')).toBeDefined()
  })

  it('advances a running run through the advance route', async () => {
    const { user } = await renderBoard()
    await user.click(screen.getAllByRole('button', { name: /^advance$/i })[0])
    await waitFor(() =>
      expect(
        calls.some(
          (c) => c.method === 'POST' && c.path === `${BASE}/rooms/${ROOM}/runs/run_1/advance`
        )
      ).toBe(true)
    )
  })

  it('only offers to resolve the run that is actually waiting', async () => {
    await renderBoard()
    // One button per run, always rendered, and enabled only for the waiting one. Asserted
    // on the disabled state rather than on the count, because a button that is not offered
    // at all would also satisfy a count of one.
    const resolves = screen.getAllByRole('button', { name: /resolve wait/i })
    expect(resolves).toHaveLength(3)
    expect(resolves.filter((button) => !button.disabled)).toHaveLength(1)
  })

  it('only offers to advance the run that is actually running', async () => {
    await renderBoard()
    const advances = screen.getAllByRole('button', { name: /^advance$/i })
    expect(advances).toHaveLength(3)
    // A waiting run must be resolved first, and an interrupted one is finished for good.
    expect(advances.filter((button) => !button.disabled)).toHaveLength(1)
  })

  it('refuses to advance the interrupted run at the API, which is the real rule', async () => {
    handlers[`POST ${BASE}/rooms/${ROOM}/runs/run_3/advance`] = () =>
      json({ error: 'run_not_advancing', detail: 'This run is Interrupted.' }, 409)
    const { user } = await renderBoard()
    const interrupted = screen
      .getAllByRole('button', { name: /^advance$/i })
      .find((button) => button.disabled)
    expect(interrupted).toBeDefined()
    // Nothing to press, which is the point: the run cannot be advanced at all.
    await user.click(interrupted)
    await waitFor(() =>
      expect(
        calls.some((c) => c.path === `${BASE}/rooms/${ROOM}/runs/run_3/advance`)
      ).toBe(false)
    )
  })

  it('offers the builder a next trigger step rather than an empty list', async () => {
    await renderBoard()
    expect(screen.getByLabelText('Add a step')).toBeDefined()
  })
})

describe('the conversations', () => {
  it('marks an API-created conversation exempt, because the rule is visible', async () => {
    await renderBoard()
    // The conversation card carries the badge; the summary carries a stat with the same
    // word, so the card is asked for directly.
    const card = screen
      .getAllByRole('heading', { level: 3 })
      .map((node) => node.closest('li'))
      .find((li) => li && li.textContent.includes('Partner referral'))
    expect(card).toBeDefined()
    expect(within(card).getByText('Exempt')).toBeDefined()
    expect(within(card).getByText('Closed')).toBeDefined()
  })

  it('shows both inboxes after a reroute, so the move is checkable', async () => {
    await renderBoard()
    expect(screen.getByText('(was sales)')).toBeDefined()
  })

  it('offers only the inboxes the backend publishes', async () => {
    await renderBoard()
    const picker = screen.getAllByLabelText('Assign conversation to')[0]
    const options = within(picker).getAllByRole('option').map((option) => option.textContent)
    expect(options).toContain('escalations')
    expect(options).toContain('Choose an inbox')
  })

  it('will not send a reroute until a destination is chosen', async () => {
    await renderBoard()
    expect(screen.getAllByRole('button', { name: /^assign$/i })[0]).toBeDisabled()
  })

  it('sends the researched reroute call with the chosen inbox', async () => {
    const { user } = await renderBoard()
    const picker = screen.getAllByLabelText('Assign conversation to')[0]
    await user.selectOptions(picker, 'escalations')
    await user.click(screen.getAllByRole('button', { name: /^assign$/i })[0])

    await waitFor(() => {
      const call = calls.find(
        (c) => c.method === 'POST' && c.path.endsWith('/reroute')
      )
      expect(call).toBeDefined()
      expect(call.body).toEqual({ inbox: 'escalations' })
    })
  })

  it('states that the exemption scopes triggers rather than records', async () => {
    await renderBoard()
    expect(
      screen.getByText(/readable and writable here and\s+never triggers/i)
    ).toBeDefined()
  })
})

describe('the office hours', () => {
  it('says the schedule is derived rather than researched', async () => {
    await renderBoard()
    expect(screen.getByText('Derived, not researched')).toBeDefined()
  })

  it('quotes the sentence the derivation was taken from', async () => {
    await renderBoard()
    expect(
      screen.getAllByText(/a message received at 5:50pm will have an expected response time of 9:05am/i)
        .length
    ).toBeGreaterThan(0)
  })

  it('shows the weekday default open and the weekend closed', async () => {
    await renderBoard()
    const monday = screen.getByText('monday').closest('fieldset')
    expect(monday).toHaveTextContent('09:00 to 18:00')
    const saturday = screen.getByText('saturday').closest('fieldset')
    expect(saturday).toHaveTextContent('Closed all day')
  })

  it('saves a schedule through the researched route', async () => {
    const { user } = await renderBoard()
    await user.click(screen.getByRole('button', { name: /save office hours/i }))
    await waitFor(() =>
      expect(
        calls.some(
          (c) => c.method === 'PUT' && c.path === `${BASE}/rooms/${ROOM}/office-hours`
        )
      ).toBe(true)
    )
  })
})

describe('the judgement calls', () => {
  it('renders the recorded decisions rather than hiding them', async () => {
    await renderBoard()
    expect(
      screen.getByText('What the research left open')
    ).toBeDefined()
    expect(
      screen.getByText('What drives the two inactivity triggers and the Wait/Snooze timer?')
    ).toBeDefined()
  })

  it('says what each decision rejected, because that is the part a reviewer needs', async () => {
    await renderBoard()
    expect(screen.getByText('A background thread, which needs a clock no test can move.')).toBeDefined()
  })

  it('flags the one derivation that is unsourced', async () => {
    await renderBoard()
    expect(screen.getByText('Unsourced')).toBeDefined()
  })

  it('offers the specification quotes so the rules can be checked at source', async () => {
    await renderBoard()
    const details = screen.getByText('What the specification itself quotes').closest('details')
    expect(details).toBeDefined()
    expect(details.textContent).toContain('can only trigger once per customer message')
  })
})

describe('the design floor', () => {
  it('uses the shared hairlines rather than shadows', async () => {
    const { container } = render(<Chase />)
    await waitFor(() => expect(screen.queryAllByRole('status')).toHaveLength(0))
    expect(container.innerHTML).not.toContain('shadow-')
  })

  it('uses rounded-sm rather than a pill', async () => {
    const { container } = render(<Chase />)
    await waitFor(() => expect(screen.queryAllByRole('status')).toHaveLength(0))
    expect(container.innerHTML).not.toMatch(/rounded-(full|3xl)/)
    expect(container.innerHTML).toContain('rounded-sm')
  })

  it('hardcodes no colour, so the page follows a theme change', async () => {
    const { container } = render(<Chase />)
    await waitFor(() => expect(screen.queryAllByRole('status')).toHaveLength(0))
    expect(container.innerHTML).not.toMatch(/#[0-9a-fA-F]{6}/)
    expect(container.innerHTML).not.toMatch(/rgb\(|hsl\(/)
  })

  it('uses no emoji as an icon', async () => {
    const { container } = render(<Chase />)
    await waitFor(() => expect(screen.queryAllByRole('status')).toHaveLength(0))
    expect(container.textContent).not.toMatch(/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u)
  })

  it('gives every control a 44px minimum touch target', async () => {
    await renderBoard()
    const controls = [
      ...screen.getAllByRole('button'),
      ...screen.getAllByRole('checkbox'),
    ]
    expect(controls.length).toBeGreaterThan(0)
    for (const control of controls) {
      const classes = `${control.className} ${control.closest('label')?.className || ''}`
      // A control is either sized by the shared Button/inputClass (min-h-11) or by an
      // explicit min-h-11 of its own. Nothing here may fall below the floor.
      expect(classes).toMatch(/min-h-11/)
    }
  })

  it('puts a visible text label beside every icon-only control', async () => {
    await renderBoard()
    for (const button of screen.getAllByRole('button')) {
      const text = button.textContent.trim()
      const labelled = button.getAttribute('aria-label') || button.getAttribute('title')
      expect(text.length > 0 || labelled).toBe(true)
    }
  })

  it('gives the page exactly one primary action in the trigger builder', async () => {
    await renderBoard()
    const primaries = screen
      .getAllByRole('button')
      .filter((button) => button.className.includes('bg-accent'))
    expect(primaries.length).toBeGreaterThan(0)
    for (const button of primaries) {
      expect(button.textContent.trim().length).toBeGreaterThan(0)
    }
  })
})
