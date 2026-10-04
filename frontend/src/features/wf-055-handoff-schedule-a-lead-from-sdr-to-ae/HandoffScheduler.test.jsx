/**
 * WF-055 frontend tests.
 *
 * `apiRequest` is never mocked. `globalThis.fetch` is replaced instead, keyed by
 * method and path, which is what the other feature tests in this product do and
 * what keeps the client under test on the same path a browser would take.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import HandoffScheduler from './HandoffScheduler'
import descriptor from './index.jsx'

const BASE = '/wf-055'

const WORKSPACE = {
  id: 'handoff_workspace_1',
  collection: 'handoff_workspace',
  data: { name: 'EMEA commercial pod' },
  summary: { users: 3, bookers: 1, assignees: 2, calendar_not_connected: 0 },
  users: [
    {
      user_id: 'sdr-nadia',
      name: 'Nadia',
      roles: ['booker'],
      can_book: true,
      assignable: false,
      assignable_reason: 'not an assignee on this workspace',
    },
    { user_id: 'ae-rui', name: 'Rui', roles: ['assignee'], can_book: false, assignable: true },
    {
      user_id: 'se-sam',
      name: 'Sam',
      roles: ['assignee'],
      can_book: false,
      assignable: true,
    },
  ],
}

const ROUTER = {
  id: 'handoff_router_1',
  data: {
    name: 'EMEA discovery handoff',
    workspace_ref: WORKSPACE.id,
    paths: [
      {
        path_id: 'emea-standard',
        name: 'EMEA, any product line',
        assignee_ref: 'ae-rui',
        assignee_name: 'Rui',
        match: { region: 'emea' },
        gating_user_ids: ['ae-rui'],
        ignored_user_ids: ['se-sam'],
        invitees: [{ user_ref: 'se-sam', required: false }],
      },
      {
        path_id: 'emea-platform',
        name: 'EMEA platform, SE required',
        assignee_ref: 'ae-priya',
        assignee_name: 'Priya',
        match: { region: 'emea', product_line: 'platform' },
        gating_user_ids: ['ae-priya', 'se-sam'],
        ignored_user_ids: [],
        invitees: [{ user_ref: 'se-sam', required: true }],
      },
    ],
  },
  paths: [
    {
      path_id: 'emea-standard',
      name: 'EMEA, any product line',
      assignee_ref: 'ae-rui',
      assignee_name: 'Rui',
      match: { region: 'emea' },
      gating_user_ids: ['ae-rui'],
      ignored_user_ids: ['se-sam'],
    },
    {
      path_id: 'emea-platform',
      name: 'EMEA platform, SE required',
      assignee_ref: 'ae-priya',
      assignee_name: 'Priya',
      match: { region: 'emea', product_line: 'platform' },
      gating_user_ids: ['ae-priya', 'se-sam'],
      ignored_user_ids: [],
    },
  ],
}

function path(overrides = {}) {
  return {
    router_ref: ROUTER.id,
    router_name: 'EMEA discovery handoff',
    path_id: 'emea-standard',
    path_name: 'EMEA, any product line',
    assignee_ref: 'ae-rui',
    assignee_name: 'Rui',
    match: { region: 'emea' },
    matched_fields: { region: 'emea' },
    invitees: [{ user_ref: 'se-sam', required: false }],
    interval: { start: '2026-10-05T09:00:00+00:00', end: '2026-10-09T17:00:00+00:00' },
    start_times: ['2026-10-05T09:00:00+00:00', '2026-10-05T09:30:00+00:00'],
    slot_count: 2,
    window: {
      operation: 'intersection_of_required_calendars',
      derivation: 'inference_path_availability_is_intersection',
      gating_user_ids: ['ae-rui'],
      ignored_user_ids: ['se-sam'],
      busy_user_ids: [],
      unresolved_user_ids: [],
      slots: [{ start_at: '2026-10-05T09:00:00+00:00', end_at: '2026-10-05T09:30:00+00:00' }],
    },
    ...overrides,
  }
}

const BLOCKED = path({
  path_id: 'emea-platform',
  path_name: 'EMEA platform, SE required',
  assignee_ref: 'ae-priya',
  assignee_name: 'Priya',
  match: { region: 'emea', product_line: 'platform' },
  start_times: [],
  slot_count: 0,
  window: {
    ...path().window,
    gating_user_ids: ['ae-priya', 'se-sam'],
    ignored_user_ids: [],
    busy_user_ids: ['se-sam'],
    slots: [],
  },
})

const EVALUATION = {
  workspace_id: WORKSPACE.id,
  booker_ref: 'sdr-nadia',
  booker_name: 'Nadia',
  request_type: 'GuestEmailRequest',
  guest_email: 'lead@example.test',
  crm_explicits: { region: 'emea' },
  shadowed_explicit_keys: [],
  path_count: 2,
  paths_with_availability: 1,
  outcome: 'paths_offered',
  paths: [path(), BLOCKED],
}

const SUMMARY = {
  workspaces: 1,
  users: 3,
  routers: 1,
  paths: 2,
  routings: 1,
  routings_open: 1,
  routings_by_outcome: { paths_offered: 1, no_availability: 0 },
  meetings: 1,
  meetings_confirmed: 1,
  routing_paths_offered: 2,
  gated_and_ignored_users: 4,
}

const ROUTING = {
  id: 'handoff_routing_1',
  data: {
    state: 'open',
    outcome: 'paths_offered',
    guest_email: 'lead@example.test',
    crm_record_id: null,
    path_count: 2,
    paths_with_availability: 1,
  },
  paths: [path(), BLOCKED],
}

const MEETING = {
  id: 'handoff_meeting_1',
  data: {
    state: 'confirmed',
    booker_ref: 'sdr-nadia',
    booker_name: 'Nadia',
    assignee_ref: 'ae-rui',
    assignee_name: 'Rui',
    path_id: 'emea-standard',
    start_at: '2026-10-05T09:00:00+00:00',
    end_at: '2026-10-05T09:30:00+00:00',
  },
}

const VOCABULARY = {
  request_type_names: ['GuestEmailRequest', 'CrmRequest'],
  meeting_role_names: ['booker', 'assignee'],
  link_types: [{ link_type: 'Handoff', routed_by_this_workflow: true }],
  path_availability: {
    operation: 'intersection',
    offered_when: 'every one of those people is free for the whole slot',
    sourced_from: 'returns time slots per routing path',
  },
  required_toggle: { field: 'required', default: false },
}

const INFERENCES = {
  count: 1,
  inferences: [
    {
      id: 'inference_path_availability_is_intersection',
      decision: 'An intersection.',
      why: 'A path names one AE, so there is no choice for a union to make.',
      change_if: 'Use a union in path_window and drop the booking-time gate check.',
      jev_audit_id: 'jev-20261004T065905-27100-45006',
      jev_verdict: 'pass',
      jev_selected: 'intersection_with_gate_recheck',
    },
  ],
}

const DEFAULTS = {
  [`${BASE}/summary`]: SUMMARY,
  [`${BASE}/catalog`]: {
    link_types: [{ link_type: 'Handoff' }],
    workspaces: [WORKSPACE],
    routers: [ROUTER],
  },
  [`${BASE}/vocabulary`]: VOCABULARY,
  [`${BASE}/inferences`]: INFERENCES,
  [`${BASE}/routings`]: { count: 1, routings: [ROUTING] },
  [`${BASE}/meetings`]: { count: 1, meetings: [MEETING] },
}

const calls = []
let handlers = {}

function json(body, status = 200) {
  return { ok: status < 400, status, statusText: 'OK', json: async () => body }
}

beforeEach(() => {
  calls.length = 0
  handlers = {}
  globalThis.fetch = vi.fn(async (url, init = {}) => {
    const raw = String(url)
    const path = raw.replace('/api', '').split('?')[0]
    const method = (init.method || 'GET').toUpperCase()
    calls.push({ url: raw, path, method, body: init.body ? JSON.parse(init.body) : undefined })
    const handler = handlers[`${method} ${path}`]
    if (handler) return handler({ path, method })
    const fallback = DEFAULTS[path]
    if (fallback === undefined) throw new Error(`unstubbed request: ${method} ${path}`)
    return json(fallback)
  })
})

async function renderPage() {
  const user = userEvent.setup()
  render(<HandoffScheduler />)
  await screen.findByText('Handoff scheduler')
  await waitFor(() => expect(calls.length).toBeGreaterThan(0))
  return user
}

describe('the descriptor', () => {
  it('exports the id the backend feature claims', () => {
    expect(descriptor.id).toBe('wf-055-handoff-schedule-a-lead-from-sdr-to-ae')
  })

  it('carries a glyph by path rather than editing the shared icon map', () => {
    expect(typeof descriptor.iconPath).toBe('string')
    expect(descriptor.iconPath.length).toBeGreaterThan(0)
  })

  it('labels the page in the researched words', () => {
    expect(descriptor.label).toBe('Handoff scheduler')
  })

  it('exports a component the host can render', () => {
    expect(typeof descriptor.Component).toBe('function')
  })
})

describe('the registration', () => {
  it('never calls a route outside its own prefix', async () => {
    await renderPage()
    for (const call of calls) {
      expect(call.path.startsWith(BASE)).toBe(true)
    }
  })
})

describe('the page', () => {
  it('shows the researched counts', async () => {
    await renderPage()
    expect((await screen.findAllByText('Pods')).length).toBeGreaterThan(0)
    expect(screen.getAllByText('Routers').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Routings').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Meetings').length).toBeGreaterThan(0)
    expect(screen.getByText('2 routing paths')).toBeTruthy()
    expect(screen.getByText('1 still open')).toBeTruthy()
  })

  it('lists the pod and names who may not be assigned, in words', async () => {
    const user = await renderPage()
    await user.click(await screen.findByText('EMEA commercial pod'))
    expect(
      await screen.findByText('not an assignee on this workspace'),
    ).toBeTruthy()
    expect(screen.getAllByText('may be assigned a handoff').length).toBeGreaterThan(0)
  })

  it('shows each path gate set and names whose calendar is not read', async () => {
    const user = await renderPage()
    await user.click(await screen.findByText('EMEA commercial pod'))
    expect(await screen.findByText('gated by ae-rui')).toBeTruthy()
    expect(screen.getByText('calendar not read: se-sam')).toBeTruthy()
    expect(screen.getByText('gated by ae-priya, se-sam')).toBeTruthy()
  })

  it('uses no emoji as an icon', async () => {
    const { container } = render(<HandoffScheduler />)
    await screen.findByText('Handoff scheduler')
    expect(container.textContent).not.toMatch(/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u)
  })

  it('shows the derivation with the quote and the Jev audit id beside it', async () => {
    await renderPage()
    expect(
      await screen.findByText('every one of those people is free for the whole slot'),
    ).toBeTruthy()
    expect(screen.getByText(/A path names one AE/)).toBeTruthy()
    expect(screen.getByText(/jev-20261004T065905-27100-45006/)).toBeTruthy()
  })
})

describe('evaluating the router', () => {
  it('refuses to call the API without the researched fields and says which', async () => {
    const user = await renderPage()
    await user.click(
      await screen.findByRole('button', { name: 'Which paths would this lead reach?' }),
    )
    expect(await screen.findByText('Enter the room id the handoff is being taken for.')).toBeTruthy()
    expect(calls.some((call) => call.method === 'POST')).toBe(false)
  })

  it('previews with the read-only call and writes nothing', async () => {
    handlers[`POST ${BASE}/rooms/room-1/workspaces/${WORKSPACE.id}/check`] = () =>
      json(EVALUATION)

    const user = await renderPage()
    await user.click(await screen.findByText('EMEA commercial pod'))
    await user.selectOptions(await screen.findByLabelText('SDR, the booker'), 'sdr-nadia')
    await user.type(screen.getByLabelText('Room id'), 'room-1')
    await user.type(screen.getByLabelText('Guest email'), 'lead@example.test')
    await user.type(screen.getByLabelText('crmExplicits: region'), 'emea')
    await user.click(
      await screen.findByRole('button', { name: 'Which paths would this lead reach?' }),
    )

    expect(await screen.findByText('Matched routing paths')).toBeTruthy()
    expect(screen.getAllByText('paths_offered').length).toBeGreaterThan(0)
    expect(screen.getByText('paths with times')).toBeTruthy()

    const post = calls.find((call) => call.method === 'POST')
    expect(post.path).toBe(`${BASE}/rooms/room-1/workspaces/${WORKSPACE.id}/check`)
    expect(post.body.type).toBe('GuestEmailRequest')
    expect(post.body.guestEmail).toBe('lead@example.test')
    expect(post.body.booker_ref).toBe('sdr-nadia')
    expect(post.body.crmExplicits).toEqual({ region: 'emea' })
    expect(calls.some((call) => call.path.includes('init-simple'))).toBe(false)
  })

  it('shows a matched path with no free time, and names who emptied it', async () => {
    handlers[`POST ${BASE}/rooms/room-1/workspaces/${WORKSPACE.id}/check`] = () =>
      json(EVALUATION)

    const user = await renderPage()
    await user.click(await screen.findByText('EMEA commercial pod'))
    await user.selectOptions(await screen.findByLabelText('SDR, the booker'), 'sdr-nadia')
    await user.type(screen.getByLabelText('Room id'), 'room-1')
    await user.type(screen.getByLabelText('Guest email'), 'lead@example.test')
    await user.click(
      await screen.findByRole('button', { name: 'Which paths would this lead reach?' }),
    )

    expect(await screen.findByText('EMEA platform, SE required')).toBeTruthy()
    expect(screen.getByText(/no free time. Busy: se-sam/)).toBeTruthy()
  })

  it('shows the refusal when no routing path matched', async () => {
    handlers[`POST ${BASE}/rooms/room-1/workspaces/${WORKSPACE.id}/check`] = () =>
      json({ error: 'handoff_error', detail: 'no routing path matched this request' }, 400)

    const user = await renderPage()
    await user.click(await screen.findByText('EMEA commercial pod'))
    await user.selectOptions(await screen.findByLabelText('SDR, the booker'), 'sdr-nadia')
    await user.type(screen.getByLabelText('Room id'), 'room-1')
    await user.type(screen.getByLabelText('Guest email'), 'lead@example.test')
    await user.click(
      await screen.findByRole('button', { name: 'Which paths would this lead reach?' }),
    )

    expect(await screen.findByText('no routing path matched this request')).toBeTruthy()
  })

  it('reports a crmExplicits key that was dropped from the rule context', async () => {
    handlers[`POST ${BASE}/rooms/room-1/workspaces/${WORKSPACE.id}/check`] = () =>
      json({ ...EVALUATION, shadowed_explicit_keys: ['guest_email'] })

    const user = await renderPage()
    await user.click(await screen.findByText('EMEA commercial pod'))
    await user.selectOptions(await screen.findByLabelText('SDR, the booker'), 'sdr-nadia')
    await user.type(screen.getByLabelText('Room id'), 'room-1')
    await user.type(screen.getByLabelText('Guest email'), 'lead@example.test')
    await user.click(
      await screen.findByRole('button', { name: 'Which paths would this lead reach?' }),
    )

    expect(await screen.findByText(/Dropped from the rule context: guest_email/)).toBeTruthy()
  })

  it('reads a CRM record request from the second researched shape', async () => {
    handlers[`POST ${BASE}/rooms/room-1/workspaces/${WORKSPACE.id}/check`] = () =>
      json(EVALUATION)

    const user = await renderPage()
    await user.click(await screen.findByText('EMEA commercial pod'))
    await user.selectOptions(await screen.findByLabelText('SDR, the booker'), 'sdr-nadia')
    await user.type(screen.getByLabelText('Room id'), 'room-1')
    await user.selectOptions(await screen.findByLabelText('Request shape'), 'CrmRequest')
    await user.type(screen.getByLabelText('CRM record id'), '00Q5s00000AbCdE')
    await user.click(
      await screen.findByRole('button', { name: 'Which paths would this lead reach?' }),
    )

    await waitFor(() => {
      const post = calls.find((call) => call.method === 'POST')
      expect(post.body.type).toBe('CrmRequest')
      expect(post.body.id).toBe('00Q5s00000AbCdE')
      expect(post.body.guestEmail).toBeUndefined()
    })
  })
})

describe('booking a handoff', () => {
  async function preview(user) {
    handlers[`POST ${BASE}/rooms/room-1/workspaces/${WORKSPACE.id}/check`] = () =>
      json(EVALUATION)
    await user.click(await screen.findByText('EMEA commercial pod'))
    await user.selectOptions(await screen.findByLabelText('SDR, the booker'), 'sdr-nadia')
    await user.type(screen.getByLabelText('Room id'), 'room-1')
    await user.type(screen.getByLabelText('Guest email'), 'lead@example.test')
    await user.click(
      await screen.findByRole('button', { name: 'Which paths would this lead reach?' }),
    )
    await screen.findByText('Matched routing paths')
  }

  it('commits through both researched calls in order', async () => {
    handlers[`POST ${BASE}/rooms/room-1/workspaces/${WORKSPACE.id}/init-simple`] = () =>
      json({ ...EVALUATION, routing_id: 'handoff_routing_9' }, 201)
    handlers[
      `POST ${BASE}/rooms/room-1/routing/handoff_routing_9/router/${ROUTER.id}` +
        '/path/emea-standard/booker/sdr-nadia/schedule-simple'
    ] = () =>
      json(
        {
          meeting_id: 'handoff_meeting_9',
          start_at: '2026-10-05T09:00:00+00:00',
          end_at: '2026-10-05T09:30:00+00:00',
          router_id: ROUTER.id,
          path_id: 'emea-standard',
          meeting: { data: { state: 'confirmed', booker_name: 'Nadia', assignee_name: 'Rui' } },
        },
        201,
      )

    const user = await renderPage()
    await preview(user)
    await user.click(
      await screen.findByRole('button', { name: /Book the handoff starting 2026-10-05 09:00/ }),
    )

    expect(await screen.findByText('Meeting booked')).toBeTruthy()
    const init = calls.find((call) => call.path.includes('init-simple'))
    const schedule = calls.find((call) => call.path.includes('schedule-simple'))
    expect(init.body.type).toBe('GuestEmailRequest')
    expect(schedule.body.startTime).toBe('2026-10-05T09:00:00+00:00')
    expect(schedule.path).toContain(`/router/${ROUTER.id}/path/emea-standard/booker/sdr-nadia`)
    expect(calls.indexOf(init)).toBeLessThan(calls.indexOf(schedule))
  })

  it('shows the refusal when the slot has been taken since the routing opened', async () => {
    handlers[`POST ${BASE}/rooms/room-1/workspaces/${WORKSPACE.id}/init-simple`] = () =>
      json({ ...EVALUATION, routing_id: 'handoff_routing_9' }, 201)
    handlers[
      `POST ${BASE}/rooms/room-1/routing/handoff_routing_9/router/${ROUTER.id}` +
        '/path/emea-standard/booker/sdr-nadia/schedule-simple'
    ] = () =>
      json({ error: 'handoff_conflict', detail: 'now taken by ae-rui' }, 409)

    const user = await renderPage()
    await preview(user)
    await user.click(
      await screen.findByRole('button', { name: /Book the handoff starting 2026-10-05 09:00/ }),
    )
    expect(await screen.findByText('now taken by ae-rui')).toBeTruthy()
  })

  it('will not book a path that offered no times', async () => {
    const user = await renderPage()
    await preview(user)

    // The empty path is on screen and it says who emptied it.
    expect(await screen.findByText(/no free time. Busy: se-sam/)).toBeTruthy()

    const rows = screen
      .getAllByRole('button')
      .filter((button) => button.getAttribute('aria-pressed') !== null)
    const empty = rows.find((row) => row.textContent.includes('emea-platform'))
    expect(empty).toBeTruthy()
    expect(empty.disabled).toBe(true)
    expect(empty.getAttribute('aria-pressed')).toBe('false')

    // The path that did offer times is the one the page selected.
    const offered = rows.find((row) => row.textContent.includes('emea-standard'))
    expect(offered.getAttribute('aria-pressed')).toBe('true')
    expect(
      screen.getByRole('button', { name: 'Book the handoff starting 2026-10-05 09:00' }),
    ).toBeTruthy()
  })

  it('cancels a confirmed meeting', async () => {
    handlers[`POST ${BASE}/meetings/${MEETING.id}/cancel`] = () => json({ ...MEETING })

    const user = await renderPage()
    await user.click(await screen.findByRole('button', { name: 'Cancel this meeting' }))
    await waitFor(() => {
      const post = calls.find((call) => call.method === 'POST')
      expect(post.path).toBe(`${BASE}/meetings/${MEETING.id}/cancel`)
    })
  })
})

describe('the accessibility floor', () => {
  it('gives every control a text label, not an icon alone', async () => {
    await renderPage()
    for (const button of screen.getAllByRole('button')) {
      const label = button.textContent.trim()
      const aria = button.getAttribute('aria-label')
      expect(label.length > 0 || (aria && aria.length > 0)).toBe(true)
    }
  })

  it('labels every input with visible text', async () => {
    await renderPage()
    expect(screen.getByLabelText('Room id')).toBeTruthy()
    expect(screen.getByLabelText('SDR, the booker')).toBeTruthy()
    expect(screen.getByLabelText('Request shape')).toBeTruthy()
    expect(screen.getByLabelText('Guest email')).toBeTruthy()
    expect(screen.getByLabelText('crmExplicits: region')).toBeTruthy()
    expect(screen.getByLabelText('crmExplicits: product line')).toBeTruthy()
  })

  it('gives the start time buttons an accessible name carrying the time', async () => {
    handlers[`POST ${BASE}/rooms/room-1/workspaces/${WORKSPACE.id}/check`] = () =>
      json(EVALUATION)

    const user = await renderPage()
    await user.click(await screen.findByText('EMEA commercial pod'))
    await user.selectOptions(await screen.findByLabelText('SDR, the booker'), 'sdr-nadia')
    await user.type(screen.getByLabelText('Room id'), 'room-1')
    await user.type(screen.getByLabelText('Guest email'), 'lead@example.test')
    await user.click(
      await screen.findByRole('button', { name: 'Which paths would this lead reach?' }),
    )
    expect(
      await screen.findByRole('button', { name: 'Book the handoff starting 2026-10-05 09:00' }),
    ).toBeTruthy()
  })
})
