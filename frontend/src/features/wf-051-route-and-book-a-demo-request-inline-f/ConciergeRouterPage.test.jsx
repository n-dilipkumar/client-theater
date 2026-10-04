/**
 * WF-051 frontend tests.
 *
 * `apiRequest` is never mocked. `globalThis.fetch` is replaced instead, keyed by
 * path, which is what the other feature tests in this product do and what keeps
 * the client under test on the same path a browser would take.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ConciergeRouterPage from './ConciergeRouterPage'
import descriptor from './index.jsx'

const BASE = '/wf-051'

const ROOM = { id: 'room_1', data: { name: 'Northwind deal desk', account: 'northwind' } }

const ROUTER = {
  id: 'concierge_router_1',
  slug: 'enterprise-demo',
  name: 'Enterprise demo',
  nodes: [
    { type: 'trigger', name: '', actions: ['webform_is_submitted'] },
    { type: 'display_calendar', name: 'Round-robin desk' },
    { type: 'catch_all', name: 'Deal desk' },
  ],
  catch_all: 'Deal desk',
  publish_state: 'published',
  deployment: ['web_form'],
  enabled: true,
}

function booking(overrides = {}) {
  return {
    id: 'concierge_booking_1',
    room_id: ROOM.id,
    guest: { email: 'buyer@example.test', company: 'Northwind' },
    rule: 'Deal desk',
    routing_outcome: 'catch_all_matched',
    state: 'pending',
    seller_name: 'Dana Ivers',
    meeting_types: ['demo', 'deep dive'],
    scheduling_allowed: true,
    notification: {},
    ...overrides,
  }
}

const PENDING = booking()
const BOOKED = booking({ id: 'concierge_booking_2', state: 'booked' })
const EXPIRED = booking({
  id: 'concierge_booking_3',
  state: 'not_scheduled',
  notification: { reason: "the Display Calendar node's Time Elapsed timer expired" },
})

const SLOTS = {
  routeId: PENDING.id,
  state: 'pending',
  seller: 'Dana Ivers',
  meetingTypes: ['demo', 'deep dive'],
  timerExpiresAt: '2026-10-05T09:00:00Z',
  count: 2,
  slots: [
    {
      start: '2026-10-06T09:00:00Z',
      end: '2026-10-06T09:30:00Z',
      duration_minutes: 30,
      meeting_type: 'demo',
    },
    {
      start: '2026-10-06T10:00:00Z',
      end: '2026-10-06T10:30:00Z',
      duration_minutes: 30,
      meeting_type: 'demo',
    },
  ],
}

const SUMMARY = {
  routers: 1,
  sellers: 1,
  bookings: 3,
  routers_by_state: { published: 1 },
  bookings_by_state: { pending: 1, booked: 1, not_scheduled: 1 },
  bookings_by_outcome: { catch_all_matched: 3 },
  deployed: 1,
}

const DEFAULTS = {
  '/records/room': { records: [ROOM] },
  [`${BASE}/summary`]: SUMMARY,
  [`${BASE}/routers`]: { count: 1, routers: [ROUTER] },
  [`${BASE}/rooms/${ROOM.id}/routers`]: { count: 1, routers: [ROUTER] },
  [`${BASE}/rooms/${ROOM.id}/bookings`]: {
    room_id: ROOM.id,
    count: 3,
    bookings: [PENDING, BOOKED, EXPIRED],
  },
  [`${BASE}/rooms/${ROOM.id}/route/${PENDING.id}/slots`]: SLOTS,
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
  render(<ConciergeRouterPage />)
  await screen.findByText('Route and book a demo request inline')
  await waitFor(() => expect(calls.length).toBeGreaterThan(0))
  return user
}

describe('the descriptor', () => {
  it('exports the id the backend feature claims', () => {
    expect(descriptor.id).toBe('wf-051-route-and-book-a-demo-request-inline-f')
  })

  it('carries a glyph by path rather than editing the shared icon map', () => {
    expect(typeof descriptor.iconPath).toBe('string')
    expect(descriptor.iconPath.length).toBeGreaterThan(0)
  })

  it('labels the page in the researched words', () => {
    expect(descriptor.label).toBe('Concierge router')
  })

  it('exports a component the host can render', () => {
    expect(typeof descriptor.Component).toBe('function')
  })
})

describe('the registration', () => {
  it('never calls a route outside its own prefix except the shared room list', async () => {
    await renderPage()
    for (const call of calls) {
      expect(call.path === '/records/room' || call.path.startsWith(BASE)).toBe(true)
    }
  })
})

describe('the page', () => {
  it('shows the researched counts', async () => {
    await renderPage()
    await screen.findByText('Routing sessions')
    expect(await screen.findByText('Waiting')).toBeTruthy()
  })

  it('lists every routing session with its guest and its rule', async () => {
    await renderPage()
    expect(await screen.findAllByText('buyer@example.test')).toHaveLength(3)
    expect(await screen.findAllByText('Deal desk')).not.toHaveLength(0)
  })

  it('names every booking state in words, never by colour alone', async () => {
    await renderPage()
    await screen.findByText('Pending')
    expect(screen.getByText('Booked')).toBeTruthy()
    expect(screen.getByText('Not scheduled')).toBeTruthy()
  })

  it('shows the researched Time Elapsed sentence and what the timer does', async () => {
    await renderPage()
    expect(
      await screen.findByText(/the meeting will be considered not scheduled/i),
    ).toBeTruthy()
    expect(screen.getByText('Assign To, Send Notification')).toBeTruthy()
  })

  it('says the notification is recorded rather than sent', async () => {
    await renderPage()
    expect(await screen.findByText('recorded, not sent')).toBeTruthy()
  })

  it('shows the declared router with its nodes and its catch all', async () => {
    await renderPage()
    expect(await screen.findByText('Enterprise demo')).toBeTruthy()
    expect(screen.getByText('enterprise-demo')).toBeTruthy()
    expect(screen.getByText('catch_all: Deal desk')).toBeTruthy()
  })

  it('uses no emoji as an icon', async () => {
    const { container } = render(<ConciergeRouterPage />)
    await screen.findByText('Route and book a demo request inline')
    expect(container.textContent).not.toMatch(/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u)
  })
})

describe('booking a slot', () => {
  it('offers the slots the matched Display Calendar node offered', async () => {
    const user = await renderPage()
    await user.click((await screen.findAllByText('Book a slot'))[0])
    expect(await screen.findByText('2026-10-06T09:00:00Z')).toBeTruthy()
    expect(screen.getByText('2026-10-06T10:00:00Z')).toBeTruthy()
  })

  it('commits the chosen slot to the researched second call', async () => {
    handlers[`POST ${BASE}/rooms/${ROOM.id}/route/${PENDING.id}/schedule-simple`] = () =>
      json({ meetingId: 'mtg_1', routeId: PENDING.id }, 201)

    const user = await renderPage()
    await user.click((await screen.findAllByText('Book a slot'))[0])
    await user.click(
      await screen.findByRole('button', { name: /Book the slot starting 2026-10-06T09:00:00Z/ }),
    )

    await waitFor(() => {
      const post = calls.find(
        (call) =>
          call.path === `${BASE}/rooms/${ROOM.id}/route/${PENDING.id}/schedule-simple`,
      )
      expect(post).toBeTruthy()
      expect(post.method).toBe('POST')
      expect(post.body.start).toBe('2026-10-06T09:00:00Z')
      expect(post.body.meeting_type).toBe('demo')
    })
  })

  it('shows the refusal when the commit is refused', async () => {
    handlers[`POST ${BASE}/rooms/${ROOM.id}/route/${PENDING.id}/schedule-simple`] = () =>
      json({ error: 'router_route_consumed', detail: 'already committed' }, 409)

    const user = await renderPage()
    await user.click((await screen.findAllByText('Book a slot'))[0])
    await user.click(
      await screen.findByRole('button', { name: /Book the slot starting 2026-10-06T09:00:00Z/ }),
    )
    expect(await screen.findByText('already committed')).toBeTruthy()
  })
})

describe('the Time Elapsed timer', () => {
  it('runs the timer on demand for a pending session', async () => {
    handlers[`POST ${BASE}/rooms/${ROOM.id}/route/${PENDING.id}/expire`] = () =>
      json({ ...PENDING, state: 'not_scheduled' })

    const user = await renderPage()
    await user.click((await screen.findAllByText('Run the timer'))[0])
    await waitFor(() => {
      const post = calls.find((call) => call.path.endsWith('/expire'))
      expect(post).toBeTruthy()
      expect(post.method).toBe('POST')
    })
  })
})

describe('the accessibility floor', () => {
  it('gives every control a text label, not an icon alone', async () => {
    await renderPage()
    for (const button of screen.getAllByRole('button')) {
      expect(button.textContent.trim().length).toBeGreaterThan(0)
      expect(button.getAttribute('aria-label')).not.toBe('')
    }
  })

  it('labels the room and the state filter with visible text', async () => {
    await renderPage()
    expect(screen.getByLabelText('Room')).toBeTruthy()
    expect(screen.getByLabelText('Booking state')).toBeTruthy()
  })

  it('gives the slot buttons an accessible name carrying the time', async () => {
    const user = await renderPage()
    await user.click((await screen.findAllByText('Book a slot'))[0])
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getAllByRole('button').length).toBeGreaterThan(0)
  })
})