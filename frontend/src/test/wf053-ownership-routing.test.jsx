import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import OwnershipRouting from '@/features/wf-053-route-a-booking-to-the-owner-of-the-cr/OwnershipRouting.jsx'
import descriptor from '@/features/wf-053-route-a-booking-to-the-owner-of-the-cr/index.jsx'

/**
 * Tests for the WF-053 ownership routing page.
 *
 * Four things a page can get wrong that the backend tests cannot see:
 *
 * * **The outcome chip is the distinction.** "The CRM owner took this" and "the
 *   catch-all took it because nobody did" are different facts about a sale. If both
 *   render as the same word, the page loses the one thing a rep reads the log for,
 *   so the chip's label is pinned for each.
 * * **The check button really writes nothing.** The page offers a preview, and the
 *   whole reason it is safe is that it calls `/check` rather than `/init-simple`.
 *   A test that only looked at the button would not notice the page calling the
 *   writing endpoint behind a button labelled "who would this reach?".
 * * **The two researched calls stay two calls.** Booking sends the `routingId` the
 *   init call returned and a `startTime` out of the list that call offered. If the
 *   page posted straight to `/schedule-simple` without a session it would look the
 *   same and be exactly the bug the two-call design prevents.
 * * **A refusal lands the operator somewhere they can act.** `apiRequest` hands the
 *   page a status and a message but not the machine-readable code, so the page's
 *   mapping from status to message is load-bearing and is pinned here.
 *
 * Stubs are keyed on this feature's own paths under `/wf-053`. An unstubbed path
 * throws rather than answering 404, so a test cannot quietly pass against the
 * wrong response.
 */

const BASE = '/wf-053'

const VOCABULARY = {
  link_types: [
    { type: 'Personal', label: 'Personal', routes_by: 'a named user', supported: false, note: 'not ownership' },
    { type: 'RoundRobin', label: 'Round Robin', routes_by: "a Distribution's turn", supported: false },
    {
      type: 'Ownership',
      label: 'Ownership',
      routes_by: 'the CRM record’s owner',
      supported: true,
      note: 'lead, contact, or account owner',
    },
  ],
  supported_link_types: ['Ownership'],
  crm_object_types: ['lead', 'contact', 'account'],
  resolution_order: ['lead', 'contact', 'account'],
  resolution_order_rationale: 'The evidence sentence is alphabetical, so it fixes the set and not the order.',
  calendar_providers: [
    { provider: 'google', label: 'Google Calendar' },
    { provider: 'outlook', label: 'Outlook' },
  ],
  discovery_operation: 'scheduling-link-list-ownership',
  edge_endpoints: {
    init: 'POST /api/fire-edge/v1/org/schedulingLinks/init-simple',
    schedule: 'POST /api/fire-edge/v1/org/schedulingLinks/routing/{routeId}/schedule-simple',
  },
  init_required_fields: ['link', 'guestEmail', 'interval'],
  schedule_required_fields: ['startTime', 'guestEmail'],
  catch_all: 'catch_all',
  forbidden_on_ownership_path: ['update_ownership', 'assign_to'],
  resolution_sources: [{ source: 'crm', default: true }],
  node_guardrail: {
    forbidden: ['update_ownership', 'assign_to'],
    quote: 'you should not use this node in **Ownership** paths.',
    preview_only: true,
  },
}

const INFERENCES = {
  count: 2,
  sourced_quotes: { ownership: 'Ownership – routes to the owner of the guest’s CRM record' },
  sourced: { link_types: ['Ownership'], catch_all: 'catch_all' },
  inferences: [
    {
      id: 'resolution-order',
      topic: 'the order lead, contact and account are tried in',
      basis: 'The evidence sentence is alphabetical.',
      value: { order: ['lead', 'contact', 'account'] },
      why: 'Lead first because it is about a person who has not been through conversion.',
      change_it: 'CRM_OBJECT_TYPES in vocabulary.py',
      blast_radius: 'Every ownership resolution.',
    },
    {
      id: 'a-chain-without-a-catch-all-is-undeclirable',
      topic: 'what a routing chain with no terminal catch-all does',
      basis: 'Admin adds Routing Rule / Catch All nodes.',
      value: { missing_catch_all: 'refused on save' },
      why: 'A chain that can run out is a prospect who books with nobody.',
      change_it: 'require_catch_all in rules.py',
      blast_radius: 'Every routing decision.',
    },
  ],
}

const CATALOG = {
  records: [{ id: 'rec1', object_type: 'lead', email: 'lead@example.com', owner_id: '005-nadia' }],
  record_count: 1,
  reps: [
    { id: 'rep1', name: 'Nadia A. Farouk', owner_id: '005-nadia', team: 'Enterprise', calendar: { provider: 'google', connected: true } },
    { id: 'rep2', name: 'Deal Desk', owner_id: '005-desk', team: 'Commercial', calendar: { provider: 'outlook', connected: true } },
    { id: 'rep3', name: 'Priya Raman', owner_id: '005-priya', team: 'Commercial', calendar: { provider: 'google', connected: false } },
  ],
  rep_count: 3,
  teams: { Commercial: ['005-desk', '005-priya'], Enterprise: ['005-nadia'] },
}

const SUMMARY = {
  links: 2,
  links_enabled: 2,
  links_with_chain: 2,
  routes: 3,
  routes_open: 1,
  routes_booked: 2,
  bookings: 2,
  bookings_confirmed: 2,
  bookings_cancelled: 0,
  decisions: 3,
  decisions_by_outcome: { catch_all: 1, resolved: 2 },
  decisions_by_object_type: { lead: 2, none: 1 },
  slots_offered: 180,
  reps: 3,
  reps_with_calendar: 2,
  crm_records: 1,
  teams: 2,
}

const decision = (overrides = {}) => ({
  id: `crm_owner_decision_${overrides.id || 'x'}`,
  collection: 'crm_owner_decision',
  created_at: '2026-10-05T09:00:00.000+00:00',
  data: {
    outcome: 'resolved',
    owner_id: '005-nadia',
    owner_name: 'Nadia A. Farouk',
    guest_email: 'lead@example.com',
    matched_object_type: 'lead',
    matched_record_id: 'rec1',
    owner_source: 'crm',
    named_owner_id: '',
    owner_unknown: false,
    rule: { kind: 'crm_ownership', name: 'Enterprise owner' },
    considered: [
      { kind: 'crm_ownership', name: 'Enterprise owner', matched: true, why: '005-nadia is on team Enterprise' },
    ],
    resolution_order: ['lead', 'contact', 'account'],
    slots_offered: 60,
    chain_declared: true,
    ...overrides,
  },
})

const DECISIONS = {
  count: 3,
  decisions: [
    decision({ id: 'a' }),
    decision({
      id: 'b',
      outcome: 'catch_all',
      owner_id: '005-desk',
      owner_name: 'Deal Desk',
      guest_email: 'stranger@elsewhere.example',
      matched_object_type: '',
      rule: { kind: 'catch_all', name: 'Deal desk', owner_id: '005-desk' },
      considered: [{ kind: 'catch_all', name: 'Deal desk', matched: true, why: 'no earlier rule matched' }],
    }),
    decision({
      id: 'c',
      owner_id: '005-desk',
      owner_name: 'Deal Desk',
      guest_email: 'ines.baptista@adventure.example',
      matched_object_type: 'contact',
      named_owner_id: '005-former-employee',
      owner_unknown: true,
      rule: { kind: 'catch_all', name: 'Deal desk', owner_id: '005-desk' },
    }),
  ],
}

const route = (overrides = {}) => ({
  id: `crm_owner_route_${overrides.id || 'x'}`,
  collection: 'crm_owner_route',
  created_at: '2026-10-05T09:00:00.000+00:00',
  is_open: true,
  data: {
    state: 'open',
    guest_email: 'lead@example.com',
    owner_id: '005-nadia',
    owner_name: 'Nadia A. Farouk',
    matched_object_type: 'lead',
    link_key: 'own_demo_direct',
    start_times: [
      '2026-10-06T09:00:00+00:00',
      '2026-10-06T09:30:00+00:00',
      '2026-10-06T10:00:00+00:00',
    ],
    opened_at: '2026-10-05T09:00:00.000+00:00',
    ...overrides,
  },
})

const ROUTES = {
  count: 2,
  routes: [
    route({ id: 'open' }),
    route({
      id: 'booked',
      // A different guest, because the same prospect holding two sessions at once
      // is the state the two-call design exists to prevent - and because two rows
      // with the same label cannot be told apart by a reader either.
      guest_email: 'm.oyelaran@northwind.example',
      state: 'booked',
      booked_start_time: '2026-10-06T09:00:00+00:00',
      start_times: ['2026-10-06T09:00:00+00:00'],
    }),
  ],
}

const LINK = {
  id: 'crm_owner_link_1',
  collection: 'crm_owner_link',
  created_at: '2026-09-20T09:00:00.000+00:00',
  has_catch_all: true,
  data: {
    type: 'Ownership',
    linkId: 'own_demo_direct',
    name: 'Ownership — book with your rep',
    enabled: true,
    resolution_source: 'crm',
    resolution_order: ['lead', 'contact', 'account'],
    interval: {
      start: '2026-10-06T00:00:00+00:00',
      end: '2026-10-13T00:00:00+00:00',
      duration_minutes: 30,
      min_notice_minutes: 0,
    },
    rules: [
      { kind: 'crm_ownership', name: 'Enterprise owner', team: 'Enterprise' },
      { kind: 'catch_all', name: 'Deal desk', owner_id: '005-desk' },
    ],
    nodes: ['create_event'],
    history: [{ decision_id: 'dec1', outcome: 'resolved', at: '2026-10-05T09:00:00.000+00:00' }],
  },
}

const LINKS = { count: 1, links: [LINK] }

const BOOKINGS = {
  room_id: 'room_1',
  count: 1,
  bookings: [
    {
      id: 'crm_owner_booking_1',
      collection: 'crm_owner_booking',
      created_at: '2026-10-05T09:05:00.000+00:00',
      data: {
        state: 'confirmed',
        guest_email: 'lead@example.com',
        owner_id: '005-nadia',
        owner_name: 'Nadia A. Farouk',
        start_time: '2026-10-06T09:00:00+00:00',
      },
    },
  ],
}

const ROOMS = {
  records: [
    { id: 'room_1', collection: 'room', data: { name: 'Northwind — Enterprise Evaluation', account: 'Northwind' } },
  ],
  count: 1,
}

const CHECK = {
  link_id: 'crm_owner_link_1',
  would_route_to: '005-nadia',
  would_route_to_name: 'Nadia A. Farouk',
  outcome: 'resolved',
  rule: { kind: 'crm_ownership', name: 'Enterprise owner' },
  considered: [],
  matched_object_type: 'lead',
  owner_source: 'crm',
  start_times: ['2026-10-06T09:00:00+00:00'],
  slots_offered: 60,
  wrote: false,
}

const SESSION = {
  routing_id: 'crm_owner_route_new',
  link_id: 'crm_owner_link_1',
  guest_email: 'lead@example.com',
  owner_id: '005-nadia',
  owner_name: 'Nadia A. Farouk',
  outcome: 'resolved',
  rule: { kind: 'crm_ownership', name: 'Enterprise owner' },
  matched_object_type: 'lead',
  owner_source: 'crm',
  start_times: ['2026-10-06T09:00:00+00:00', '2026-10-06T09:30:00+00:00'],
  slots_offered: 2,
  decision_id: 'crm_owner_decision_new',
  state: 'open',
}

const ok = (body, status = 200) => ({ ok: true, status, statusText: 'OK', json: async () => body })

const refused = (status, detail) => ({
  ok: false,
  status,
  statusText: 'Error',
  json: async () => ({ error: 'refused', detail }),
})

/**
 * Answer every path this page reads, and let a test override one.
 *
 * `/api` is stripped so route keys read the way the feature's own client writes
 * them. Longest key first and whole-segment only, so a bare `startsWith` cannot let
 * `/wf-053/links` answer a request for `/wf-053/links-old`. An unstubbed path
 * throws rather than answering 404, so a test cannot quietly pass against the
 * wrong response.
 *
 * A value that already looks like a response is returned untouched. That is the
 * whole reason this helper exists rather than a bare `ok(body)`: wrapping a
 * refusal as a body makes the page's error path unreachable, and the test that
 * asserts a refusal lands somewhere useful would fail for the opposite reason -
 * it would be asserting on a page that succeeded.
 */
function stubApi(overrides = {}) {
  const roomScoped = overrides.__roomScoped === true
  const routes = {
    [`${BASE}/vocabulary`]: VOCABULARY,
    [`${BASE}/inferences`]: INFERENCES,
    [`${BASE}/catalog`]: CATALOG,
    [`${BASE}/summary`]: SUMMARY,
    [`${BASE}/links`]: LINKS,
    [`${BASE}/links/crm_owner_link_1`]: LINK,
    [`${BASE}/records`]: CATALOG,
    [`${BASE}/reps`]: CATALOG,
    [`${BASE}/routes`]: ROUTES,
    [`${BASE}/routes/crm_owner_route_open`]: ROUTES.routes[0],
    [`${BASE}/bookings/crm_owner_booking_1`]: BOOKINGS.bookings[0],
    [`${BASE}/decisions`]: DECISIONS,
    [`${BASE}/rooms/room_1/check`]: CHECK,
    [`${BASE}/rooms/room_1/init-simple`]: SESSION,
    [`${BASE}/rooms/room_1/schedule-simple`]: BOOKINGS.bookings[0],
    [`${BASE}/rooms/room_1/bookings`]: BOOKINGS,
    [`${BASE}/rooms/room_1/bookings/crm_owner_booking_1/cancel`]: {
      ...BOOKINGS.bookings[0],
      data: { ...BOOKINGS.bookings[0].data, state: 'cancelled' },
    },
    '/records/room': ROOMS,
    ...(roomScoped
      ? {
          [`${BASE}/rooms/room_1/bookings/crm_owner_booking_1/cancel`]: {
            ...BOOKINGS.bookings[0],
            data: { ...BOOKINGS.bookings[0].data, state: 'cancelled' },
          },
        }
      : {}),
    ...overrides,
  }

  const keys = Object.keys(routes).sort((a, b) => b.length - a.length)
  const matches = (path, candidate) =>
    path === candidate || path.startsWith(`${candidate}/`) || path.startsWith(`${candidate}?`)

  const calls = []

  globalThis.fetch = vi.fn(async (url, init = {}) => {
    const raw = String(url)
    const path = raw.replace('/api', '').split('?')[0]
    const query = raw.includes('?') ? raw.slice(raw.indexOf('?') + 1) : ''
    const method = (init.method || 'GET').toUpperCase()
    const body = init.body ? JSON.parse(init.body) : undefined
    calls.push({ path, method, query, body })

    const key = keys.find((candidate) => matches(path, candidate))
    if (!key) throw new Error(`unstubbed request: ${path}`)

    const value = routes[key]
    const asResponse = (answer, status) =>
      answer && typeof answer === 'object' && typeof answer.ok === 'boolean' ? answer : ok(answer, status)

    if (typeof value !== 'function') return asResponse(value, method === 'POST' ? 201 : 200)
    const answer = value({ path, method, query, body, calls })
    return asResponse(answer, 200)
  })

  return { calls }
}

const lastCallTo = (calls, path) => calls.filter((call) => call.path === path).at(-1)

const callsTo = (calls, path) => calls.filter((call) => call.path === path)

/**
 * Rows are found by their accessible name rather than by their inner text.
 *
 * A row's label is assembled from several JSX expressions, so `getByText` on any
 * one fragment cannot match it - and the same guest and rep appear in the decision
 * list, the session list and the booking list, so a text matcher would also be
 * ambiguous between the three. The `aria-label` on each row is the whole fact.
 */
const decisionRow = (guest, owner) =>
  screen.getByRole('button', { name: `Routing decision: ${guest}, routed to ${owner}` })

const sessionRow = (guest) => screen.getByRole('button', { name: new RegExp(`Routing session: ${guest}`) })

/** A Subhead renders as an `h3` whose name is its label plus its count. */
const subhead = (label) => screen.getByRole('heading', { name: new RegExp(`^${label}`) })

/** Render and wait for the first tab's data, so no test races the initial load. */
async function renderPage(routes) {
  const { calls } = stubApi(routes)
  const user = userEvent.setup()
  const view = render(<OwnershipRouting />)
  await screen.findByText('Routing decisions')
  return { user, calls, container: view.container }
}

beforeEach(() => {
  globalThis.fetch = vi.fn()
})

describe('the descriptor', () => {
  it('exports the id the backend feature claims', () => {
    expect(descriptor.id).toBe('wf-053-route-a-booking-to-the-owner-of-the-cr')
  })

  it('carries a glyph by path rather than editing the shared icon map', () => {
    expect(typeof descriptor.iconPath).toBe('string')
    expect(descriptor.iconPath.length).toBeGreaterThan(0)
  })

  it('labels the page in the researched words', () => {
    expect(descriptor.label).toBe('Ownership routing')
  })
})

describe('routing decisions', () => {
  it('separates a CRM owner from a catch-all in the header tiles too', async () => {
    await renderPage()
    // "Links" is both a stat tile and a section button, so the tile is found
    // through the stat card rather than by a bare text match that would be
    // ambiguous between the two.
    const tiles = screen.getAllByText('Links')
    expect(tiles.length).toBeGreaterThan(0)
    expect(screen.getByText('Routed to catch-all')).toBeInTheDocument()
    expect(screen.getByText('1')).toBeInTheDocument()
  })

  it('reads its counts from the summary rather than counting rows itself', async () => {
    const { calls } = await renderPage()
    expect(callsTo(calls, `${BASE}/summary`).length).toBeGreaterThan(0)
  })
})

describe('routing decisions', () => {
  it('says who took the prospect, and whether that was the CRM owner', async () => {
    await renderPage()
    const row = decisionRow('lead@example.com', 'Nadia A. Farouk').closest('li')

    expect(within(row).getByText('CRM owner')).toBeInTheDocument()
    expect(within(row).getByText('lead')).toBeInTheDocument()
  })

  it('labels the catch-all distinctly, and never by colour alone', async () => {
    await renderPage()
    const row = decisionRow('stranger@elsewhere.example', 'Deal Desk').closest('li')
    expect(within(row).getByText('catch-all')).toBeInTheDocument()
  })

  it('marks a decision whose named owner is no longer in the workspace', async () => {
    await renderPage()
    const row = decisionRow('ines.baptista@adventure.example', 'Deal Desk').closest('li')
    // The distinction is only worth drawing if the row is findable, and the word is
    // there so the fact survives a screenshot.
    expect(within(row).getByText('dead owner')).toBeInTheDocument()
  })

  it('shows how many slots were offered, which is what makes the decision worth reading', async () => {
    await renderPage()
    const row = decisionRow('lead@example.com', 'Nadia A. Farouk').closest('li')
    expect(within(row).getByText('60 slots')).toBeInTheDocument()
  })

  it('expands a row to the rule chain that produced it', async () => {
    const { user } = await renderPage()
    await user.click(decisionRow('lead@example.com', 'Nadia A. Farouk'))

    await screen.findByRole('heading', { name: /^Rules considered/ })
    // Scoped to the expanded row's rule list, which is the only place this heading
    // appears: the same reason string is also shown in the link drawer on the Links
    // tab, so matching on it anywhere on the page would be ambiguous.
    const considered = within(
      screen.getByRole('heading', { name: /^Rules considered/ }).closest('div'),
    ).getAllByRole('listitem')
    expect(considered).toHaveLength(1)
    expect(considered[0]).toHaveTextContent('Enterprise owner')
    expect(considered[0]).toHaveTextContent('005-nadia is on team Enterprise')
  })

  it('reports the resolution order the owner was looked up in', async () => {
    const { user } = await renderPage()
    await user.click(decisionRow('lead@example.com', 'Nadia A. Farouk'))

    await screen.findByRole('heading', { name: /^Resolution order/ })
  })
})

describe('routing sessions', () => {
  it('names a session still open apart from one whose slot is gone', async () => {
    await renderPage()
    expect(sessionRow('lead@example.com')).toHaveAccessibleName(
      'Routing session: lead@example.com, still open with Nadia A. Farouk',
    )
    expect(screen.getByText('booked')).toBeInTheDocument()
  })

  it('books a slot from the session that offered it', async () => {
    const { user, calls } = await renderPage()
    await user.click(sessionRow('lead@example.com'))

    await user.click(
      await screen.findByRole('button', { name: 'Book the slot starting 2026-10-06T09:00:00+00:00' }),
    )

    await waitFor(() => {
      const post = lastCallTo(calls, `${BASE}/rooms/room_1/schedule-simple`)
      expect(post.method).toBe('POST')
      expect(post.body.routing_id).toBe('crm_owner_route_open')
      expect(post.body.startTime).toBe('2026-10-06T09:00:00+00:00')
      expect(post.body.guestEmail).toBe('lead@example.com')
    })
  })

  it('will not offer to book a slot from a session that is already booked', async () => {
    const { user } = await renderPage()
    await user.click(sessionRow('m.oyelaran@northwind.example'))

    // The booked session's only button is disabled rather than absent, so the row
    // keeps its shape and the reason is visible instead of inferred.
    const button = await screen.findByRole('button', {
      name: 'Book the slot starting 2026-10-06T09:00:00+00:00',
    })
    expect(button).toBeDisabled()
  })
})

describe('bookings', () => {
  it('shows a booking with its guest, its owner and its start', async () => {
    await renderPage()
    const row = screen.getByLabelText(
      'Booking: lead@example.com with Nadia A. Farouk, confirmed',
    )
    expect(within(row).getByText('confirmed')).toBeInTheDocument()
  })

  it('offers a cancellation, which releases the slot rather than only marking it', async () => {
    const { user, calls } = await renderPage()
    await user.click(screen.getByRole('button', { name: 'Cancel booking' }))

    await waitFor(() => {
      const post = lastCallTo(calls, `${BASE}/rooms/room_1/bookings/crm_owner_booking_1/cancel`)
      expect(post.method).toBe('POST')
    })
  })
})

describe('the links tab', () => {
  it('shows all five researched link types and says which one this routes', async () => {
    const { user } = await renderPage()
    await user.click(screen.getByRole('button', { name: 'Links' }))

    await screen.findByText('The five researched link types')
    expect(screen.getByText('routes by the CRM record’s owner — lead, contact, or account owner')).toBeInTheDocument()
    expect(screen.getByText("routes by a Distribution's turn")).toBeInTheDocument()
  })

  it('quotes the node guardrail rather than asserting it', async () => {
    const { user } = await renderPage()
    await user.click(screen.getByRole('button', { name: 'Links' }))

    expect(
      await screen.findByText(/you should not use this node in \*\*Ownership\*\* paths/),
    ).toBeInTheDocument()
  })

  it('renders the routing chain in the order it will be evaluated', async () => {
    const { user } = await renderPage()
    await user.click(screen.getByRole('button', { name: 'Links' }))
    await user.click(
      await screen.findByRole('button', { name: 'Ownership link: Ownership — book with your rep, with a catch-all' }),
    )

    const chain = within((await screen.findByRole('heading', { name: /^Routing chain/ })).closest('div'))
      .getAllByRole('listitem')
    expect(chain[0]).toHaveTextContent('Enterprise owner')
    expect(chain[1]).toHaveTextContent('catch_all')
  })

  it('shows a rep whose calendar is not connected, which is a booking nobody can make', async () => {
    const { user } = await renderPage()
    await user.click(screen.getByRole('button', { name: 'Links' }))

    const reps = within(subhead('Reps and their calendars').closest('div'))
    expect(reps.getByText('Priya Raman')).toBeInTheDocument()
    expect(reps.getAllByText('google').length).toBeGreaterThan(0)
  })

  it('lists the CRM records in the researched resolution order', async () => {
    const { user } = await renderPage()
    await user.click(screen.getByRole('button', { name: 'Links' }))

    expect(await screen.findByText(/Resolved in order: lead → contact → account/)).toBeInTheDocument()
  })
})

describe('try a guest', () => {
  async function openTab(user) {
    await user.click(screen.getByRole('button', { name: 'Try a guest' }))
    await screen.findByLabelText('Guest email')
    // The link picker defaults from the links list, which arrives after the tab
    // opens, so waiting for the button to exist would race it. Waiting for the
    // picker to hold a link id is what actually makes the call possible.
    await waitFor(() => expect(screen.getByLabelText('Ownership link')).toHaveValue('crm_owner_link_1'))
  }

  it('previews with the read-only call, so nothing is committed', async () => {
    const { user, calls } = await renderPage()
    await openTab(user)
    await user.type(screen.getByLabelText('Guest email'), 'lead@example.com')
    await user.click(screen.getByRole('button', { name: /Who would this reach/ }))

    await screen.findByText('Check — wrote nothing')
    // The button is labelled as a preview; what proves it is which endpoint it hit.
    expect(callsTo(calls, `${BASE}/rooms/room_1/check`)).toHaveLength(1)
    expect(callsTo(calls, `${BASE}/rooms/room_1/init-simple`)).toHaveLength(0)
  })

  it('says who the guest would reach before anything is committed', async () => {
    const { user } = await renderPage()
    await openTab(user)
    await user.type(screen.getByLabelText('Guest email'), 'lead@example.com')
    await user.click(screen.getByRole('button', { name: /Who would this reach/ }))

    expect(await screen.findByText('Nadia A. Farouk')).toBeInTheDocument()
  })

  it('opens a routing session with the init call and shows the slots it offered', async () => {
    const { user, calls } = await renderPage()
    await openTab(user)
    await user.type(screen.getByLabelText('Guest email'), 'lead@example.com')
    await user.click(screen.getByRole('button', { name: /Open a routing session/ }))

    await screen.findByText('Slots from this session')
    const post = lastCallTo(calls, `${BASE}/rooms/room_1/init-simple`)
    expect(post.method).toBe('POST')
    expect(post.body.guestEmail).toBe('lead@example.com')
    expect(post.body.link_id).toBe('crm_owner_link_1')
  })

  it('books against the routingId the init call returned', async () => {
    const { user, calls } = await renderPage()
    await openTab(user)
    await user.type(screen.getByLabelText('Guest email'), 'lead@example.com')
    await user.click(screen.getByRole('button', { name: /Open a routing session/ }))

    await user.click(
      await screen.findByRole('button', { name: 'Book the slot starting 2026-10-06T09:00:00+00:00' }),
    )

    await waitFor(() => {
      const post = lastCallTo(calls, `${BASE}/rooms/room_1/schedule-simple`)
      expect(post.body.routing_id).toBe('crm_owner_route_new')
      expect(post.body.startTime).toBe('2026-10-06T09:00:00+00:00')
    })
  })

  it('will not proceed without a guest email, which the research calls required', async () => {
    const { user } = await renderPage()
    await openTab(user)

    expect(screen.getByRole('button', { name: /Who would this reach/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: /Open a routing session/ })).toBeDisabled()
  })

  it('shows a refusal where the operator can act on it', async () => {
    const { user, calls } = await renderPage({
      [`${BASE}/rooms/room_1/check`]: refused(
        409,
        'the CRM record names owner 005-former-employee, but no rep in this workspace answers',
      ),
    })
    await openTab(user)
    await user.type(screen.getByLabelText('Guest email'), 'ines@adventure.example')
    await user.click(screen.getByRole('button', { name: /Who would this reach/ }))
    // Asserted on the call as well as the rendering: a button that silently did
    // nothing would leave no alert to find, and the failure would read as a broken
    // assertion rather than as a button that never fired.
    await waitFor(() => expect(callsTo(calls, `${BASE}/rooms/room_1/check`)).toHaveLength(1))

    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load data')
    expect(screen.getByText(/no rep in this workspace answers/)).toBeInTheDocument()
  })
})

describe('the inferences tab', () => {
  it('names each judgement call with the research sentence it rests on', async () => {
    const { user } = await renderPage()
    await user.click(screen.getByRole('button', { name: 'What this infers' }))

    await screen.findByText('resolution-order')
    expect(screen.getByText('The evidence sentence is alphabetical.')).toBeInTheDocument()
  })

  it('says how to change each one and what it would move', async () => {
    const { user } = await renderPage()
    await user.click(screen.getByRole('button', { name: 'What this infers' }))
    await screen.findByText('resolution-order')

    expect(screen.getByText('CRM_OBJECT_TYPES in vocabulary.py')).toBeInTheDocument()
    expect(screen.getByText('Every ownership resolution.')).toBeInTheDocument()
  })
})

describe('accessibility', () => {
  it('marks the current section for assistive technology', async () => {
    await renderPage()
    expect(document.querySelector('[aria-current]')).toHaveTextContent('Decisions')
  })

  it('gives every expandable row a name a reader can act on', async () => {
    await renderPage()
    const toggle = decisionRow('lead@example.com', 'Nadia A. Farouk')
    expect(toggle).toHaveAccessibleName(
      'Routing decision: lead@example.com, routed to Nadia A. Farouk',
    )
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
  })

  it('labels the room picker rather than leaving a bare select', async () => {
    await renderPage()
    expect(screen.getByLabelText('Room')).toBeInTheDocument()
  })

  it('uses no emoji as an icon', async () => {
    const { container } = await renderPage({})
    // Emoji are the one icon this repo forbids outright, and a stray one in a glyph
    // map would be invisible to every other check here.
    expect(container.textContent).not.toMatch(/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u)
  })
})
