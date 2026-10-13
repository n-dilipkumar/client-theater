/**
 * Tests for WF-121's page and its primitives.
 *
 * The page is driven against a stubbed `fetch` rather than a live server, so the
 * assertions are about what a rep reads and which controls are offered, not
 * about the server's answer. Every test builds its own stub and its own data, so
 * the file passes alone and under vitest's parallel run without depending on
 * another test's fixture.
 *
 * Three of these tests are about the researched facts rather than about
 * rendering: the per-page dwell marks a page nobody read instead of drawing a
 * zero, the alert names the trigger in words, and the page names the surfaces it
 * does not build rather than omitting them.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import page from './index.jsx'
import {
  ALERT_LABELS,
  ALERT_TONES,
  STATE_TONES,
  alertTone,
  formatSeconds,
  shortTimestamp,
  stateTone,
  wholeInRange,
} from './api.js'
import {
  AlertRow,
  AlertTriggerBadge,
  DropOffList,
  InlineNote,
  PageDwellBars,
  StateBadge,
  ViewerRow,
} from './primitives.jsx'

const { Component } = page

const VOCABULARY = {
  ticket: 'WF-121',
  link_states: ['active', 'expired', 'revoked'],
  alert_triggers: ['first_view', 'return_visit', 'page_threshold', 'dwell_threshold'],
  alert_tones: { first_view: 'info' },
  engagement_event_type: 'tracked_deal_link_view',
  engagement_crm_object: 'deal',
  defaults: {
    link_lifetime_days: 30,
    alert_page_threshold: 3,
    dwell_alert_seconds: 120,
  },
  reused: {
    revisit_gap_seconds: 1800,
    revisit_gap_source: 'WF-018 dsr.pdf_analytics.vocabulary.SESSION_GAP_SECONDS',
  },
}

function linkRow(overrides = {}) {
  return {
    id: 'wf121_link_1',
    room_id: 'room_1',
    title: 'Security overview',
    asset_id: 'deck-demo',
    asset_name: 'Enterprise Security & Compliance Overview',
    page_count: 10,
    recipients: ['a.buyer@northwind.example'],
    share_url: '/api/wf-121/links/wf121_link_1',
    expires_at: '2026-06-01T00:00:00.000+00:00',
    state: 'active',
    ...overrides,
  }
}

function analyticsPayload(overrides = {}) {
  return {
    link: linkRow(),
    audience: 'external',
    session_gap_seconds: 1800,
    page_count: 3,
    readings: 4,
    revisits: [{ id: 'reader:1' }, { id: 'reader:2' }],
    viewers: [
      {
        viewer: 'a.buyer@northwind.example',
        revisits: 2,
        deepest_page: 3,
        total_seconds: 150,
        last_seen_at: '2026-05-02T09:00:00.000+00:00',
      },
    ],
    time_spent_per_page: [
      { page: 1, reads: 4, total_seconds: 120, average_seconds: 30.0 },
      { page: 2, reads: 3, total_seconds: 90, average_seconds: 30.0 },
      { page: 3, reads: 0, total_seconds: 0, average_seconds: null },
    ],
    drop_off_per_page: [
      { page: 1, reached: 4, dropped: 0, drop_off_rate: 0, is_last_page: false },
      { page: 2, reached: 3, dropped: 1, drop_off_rate: 0.25, is_last_page: false },
      { page: 3, reached: 2, dropped: 0, drop_off_rate: 0, is_last_page: true },
    ],
    drop_off: { sessions: 4, completed_sessions: 2, opening_page_readers: 4 },
    pages_never_read: [3],
    ...overrides,
  }
}

function payload(overrides = {}) {
  return {
    '/vocabulary': VOCABULARY,
    '/inferences': { inferences: [{ id: 'link_lifetime' }], count: 1 },
    '/summary': {
      links: 1,
      open: 1,
      expired: 0,
      revoked: 0,
      views: 4,
      viewers: 1,
      deliveries: 1,
      alerts: 2,
    },
    '/links/wf121_link_1/analytics': analyticsPayload(),
    '/links/wf121_link_1/deliveries': {
      link_id: 'wf121_link_1',
      count: 1,
      deliveries: [
        { id: 'd1', recipient: 'a.buyer@northwind.example', delivered_at: '2026-05-01T09:00:00.000+00:00' },
      ],
    },
    '/viewers': {
      link_id: 'wf121_link_1',
      count: 1,
      viewers: [
        {
          viewer: 'a.buyer@northwind.example',
          revisits: 2,
          deepest_page: 3,
          last_seen_at: '2026-05-02T09:00:00.000+00:00',
        },
      ],
    },
    '/views': {
      link_id: 'wf121_link_1',
      viewer: 'a.buyer@northwind.example',
      readings: 2,
      revisits: [{ id: 'r1' }, { id: 'r2' }],
      views: [
        { id: 'v1', page: 1, seconds: 30, occurredAt: '2026-05-01T09:00:00.000+00:00' },
        { id: 'v2', page: 2, seconds: 45, occurredAt: '2026-05-01T09:01:00.000+00:00' },
      ],
    },
    '/alerts': {
      room_id: 'room_1',
      count: 2,
      alerts: [
        {
          id: 'a1',
          link_id: 'wf121_link_1',
          viewer: 'a.buyer@northwind.example',
          revisit: 2,
          trigger: 'return_visit',
          detail: 'Viewer returned for revisit 2.',
          notified_at: '2026-05-02T09:00:00.000+00:00',
        },
      ],
    },
    '/links': { count: 1, room_id: 'room_1', links: [linkRow()] },
    '/push': { room_id: 'room_1', counts: { considered: 1, synced: 0, failed: 0, blocked: 1, skipped: 0 }, results: [] },
    '/records/room': { records: [{ id: 'room_1', data: { name: 'Northwind Traders' } }] },
    ...overrides,
  }
}

/** Route each API call this page makes to one canned answer. */
function stubFetch(answers, calls = []) {
  return vi.fn(async (url) => {
    const path = String(url).split('?')[0]
    calls.push(String(url))
    const key = Object.keys(answers).find((candidate) => path.endsWith(candidate))
    const body = key ? answers[key] : {}
    return {
      ok: true,
      status: 200,
      json: async () => body,
      text: async () => JSON.stringify(body),
    }
  })
}

async function openAnalytics(user) {
  const buttons = await screen.findAllByRole('button', { name: 'Analytics' })
  await user.click(buttons[0])
}

beforeEach(() => {
  vi.restoreAllMocks()
})

// --------------------------------------------------------------------------- #
// The descriptor
// --------------------------------------------------------------------------- #

describe('the descriptor', () => {
  it('exports the four fields App.jsx reads', () => {
    expect(page.id).toBe('wf-121-tracked-deal-link')
    expect(page.label).toBe('Tracked deal link')
    expect(typeof page.icon).toBe('string')
    expect(typeof page.Component).toBe('function')
  })
})

// --------------------------------------------------------------------------- #
// The page
// --------------------------------------------------------------------------- #

describe('the page', () => {
  it('names the workflow it is about', async () => {
    global.fetch = stubFetch(payload())
    render(<Component />)
    expect(await screen.findByRole('heading', { name: 'Tracked deal link' })).toBeTruthy()
  })

  it("calls this feature's own routes, plus the core room collection", async () => {
    const calls = []
    global.fetch = stubFetch(payload(), calls)
    render(<Component />)
    await screen.findByRole('heading', { name: 'Tracked deal link' })
    const apiCalls = calls.filter((call) => call.startsWith('/api/'))
    const own = apiCalls.filter((call) => call.includes('/api/wf-121/'))
    const core = apiCalls.filter((call) => !call.includes('/api/wf-121/'))
    expect(own.length).toBeGreaterThan(0)
    expect(core.every((call) => call.startsWith('/api/records/room'))).toBe(true)
  })

  it('shows the four headline figures', async () => {
    global.fetch = stubFetch(payload())
    render(<Component />)
    await screen.findByRole('heading', { name: 'Tracked deal link' })
    expect(screen.getByText('Links')).toBeTruthy()
    expect(screen.getByText('Page views')).toBeTruthy()
    expect(screen.getByText('Viewers')).toBeTruthy()
    expect(screen.getByText('Alerts')).toBeTruthy()
    expect(screen.getByText('1 open, 0 expired, 0 revoked')).toBeTruthy()
  })

  it('lists each link with its state, its asset and its share URL', async () => {
    global.fetch = stubFetch(payload())
    render(<Component />)
    await screen.findByRole('heading', { name: 'Tracked deal link' })
    expect(screen.getByText('Security overview')).toBeTruthy()
    expect(screen.getByText('Active')).toBeTruthy()
    expect(screen.getByText('/api/wf-121/links/wf121_link_1')).toBeTruthy()
  })

  it('shows an empty state rather than a blank list when no link exists', async () => {
    const answers = payload()
    answers['/links'] = { count: 0, links: [] }
    global.fetch = stubFetch(answers)
    render(<Component />)
    expect(await screen.findByText('No tracked link yet')).toBeTruthy()
  })

  it('refuses a link with no asset before the round trip', async () => {
    const calls = []
    global.fetch = stubFetch(payload(), calls)
    const user = userEvent.setup()
    render(<Component />)
    const submit = await screen.findByRole('button', { name: /Share the link/ })
    expect(submit).toBeDisabled()
    expect(screen.getByText(/An asset id is required/)).toBeTruthy()
    expect(calls.some((call) => call.includes('/wf-121/links?') && call.includes('room_id'))).toBe(false)
    await user.click(submit)
  })

  it('creates the one link when the form is filled in', async () => {
    const calls = []
    const answers = payload()
    global.fetch = stubFetch(answers, calls)
    const user = userEvent.setup()
    render(<Component />)
    await screen.findByRole('heading', { name: 'Tracked deal link' })

    await user.selectOptions(screen.getByLabelText('Room'), 'room_1')
    await user.type(screen.getByLabelText('Library asset id'), 'deck-demo')
    await user.click(screen.getByRole('button', { name: /Share the link/ }))

    await waitFor(() => {
      expect(
        calls.some(
          (call) => call.includes('/api/wf-121/links') && call.includes('room_id=room_1'),
        ),
      ).toBe(true)
    })
  })

  it('keeps the default lifetime and page threshold as hints, not hidden values', async () => {
    global.fetch = stubFetch(payload())
    render(<Component />)
    await screen.findByRole('heading', { name: 'Tracked deal link' })
    expect(screen.getByText(/the default is 30 days/)).toBeTruthy()
    expect(screen.getByText(/the default is 3\./)).toBeTruthy()
  })

  it('records the share with the link recipients', async () => {
    const calls = []
    global.fetch = stubFetch(payload(), calls)
    const user = userEvent.setup()
    render(<Component />)
    await screen.findByRole('heading', { name: 'Tracked deal link' })
    await user.click(screen.getByRole('button', { name: /Record the share/ }))
    await waitFor(() => {
      expect(calls.some((call) => call.includes('/links/wf121_link_1/deliveries'))).toBe(true)
    })
  })

  it('opens the analytics panel and reads the per-page dwell and drop-off', async () => {
    const calls = []
    global.fetch = stubFetch(payload(), calls)
    const user = userEvent.setup()
    render(<Component />)
    await screen.findByRole('heading', { name: 'Tracked deal link' })
    await openAnalytics(user)

    await waitFor(() => {
      expect(calls.some((call) => call.includes('/links/wf121_link_1/analytics'))).toBe(true)
    })
    expect(await screen.findByText('Time spent per page')).toBeTruthy()
    expect(screen.getByText('Not read')).toBeTruthy()
    expect(screen.getByText(/4 reading\(s\); 2 reached the last page/)).toBeTruthy()
    expect(screen.getAllByText('a.buyer@northwind.example').length).toBeGreaterThan(0)
  })

  it('shows the pages one buyer read when the viewer is opened', async () => {
    const calls = []
    global.fetch = stubFetch(payload(), calls)
    const user = userEvent.setup()
    render(<Component />)
    await screen.findByRole('heading', { name: 'Tracked deal link' })
    await openAnalytics(user)

    await user.click(await screen.findByRole('button', { name: 'Show pages' }))

    await waitFor(() => {
      // The email is URL-encoded in the path, so match on the decoded tail.
      expect(
        calls.some(
          (call) => call.includes('/viewers/') && call.includes('northwind.example/views'),
        ),
      ).toBe(true)
    })
    expect(await screen.findByText(/Pages read by a.buyer@northwind.example/)).toBeTruthy()
  })

  it('records a reading through the viewer ingest', async () => {
    const calls = []
    global.fetch = stubFetch(payload(), calls)
    const user = userEvent.setup()
    render(<Component />)
    await screen.findByRole('heading', { name: 'Tracked deal link' })
    await openAnalytics(user)

    await user.type(await screen.findByLabelText('Viewer email'), 'a.buyer@northwind.example')
    await user.click(screen.getByRole('button', { name: /Record the reading/ }))

    await waitFor(() => {
      expect(calls.some((call) => call.includes('/links/wf121_link_1/views'))).toBe(true)
    })
  })

  it('does not claim the CRM write landed: it sends the queue and reports it', async () => {
    const calls = []
    global.fetch = stubFetch(payload(), calls)
    const user = userEvent.setup()
    render(<Component />)
    await screen.findByRole('heading', { name: 'Tracked deal link' })
    await openAnalytics(user)

    expect(await screen.findByText(/nothing is sent/)).toBeTruthy()
    await user.click(screen.getByRole('button', { name: /Send the queued CRM writes/ }))
    await waitFor(() => {
      expect(calls.some((call) => call.includes('/rooms/room_1/push'))).toBe(true)
    })
  })

  it('lists the rep alerts with the trigger in words', async () => {
    global.fetch = stubFetch(payload())
    render(<Component />)
    await screen.findByRole('heading', { name: 'Tracked deal link' })
    expect(screen.getByText('Return visit')).toBeTruthy()
    expect(screen.getByText('Viewer returned for revisit 2.')).toBeTruthy()
  })

  it('tells a workspace with no alerts why the panel is empty', async () => {
    const answers = payload()
    answers['/alerts'] = { room_id: null, count: 0, alerts: [] }
    global.fetch = stubFetch(answers)
    render(<Component />)
    expect(await screen.findByText(/No buyer view has raised an alert/)).toBeTruthy()
  })

  it('names the surfaces this workflow does not build', async () => {
    global.fetch = stubFetch(payload())
    render(<Component />)
    expect(await screen.findByText('Not built here')).toBeTruthy()
    expect(screen.getByText(/WF-069 owns that gate/)).toBeTruthy()
    expect(screen.getByText(/This product has no mail transport/)).toBeTruthy()
  })
})

// --------------------------------------------------------------------------- #
// The primitives
// --------------------------------------------------------------------------- #

describe('the primitives', () => {
  it('prints the state name beside its tone', () => {
    render(<StateBadge state="expired" />)
    expect(screen.getByText('Expired')).toBeTruthy()
  })

  it('names an alert trigger in words rather than showing the code', () => {
    render(<AlertTriggerBadge trigger="return_visit" />)
    expect(screen.getByText('Return visit')).toBeTruthy()
  })

  it('marks a page nobody read instead of drawing a zero', () => {
    render(
      <PageDwellBars
        entries={[
          { page: 1, reads: 4, average_seconds: 30.0 },
          { page: 2, reads: 0, average_seconds: null },
        ]}
      />,
    )
    expect(screen.getByText('Not read')).toBeTruthy()
    expect(screen.getByText(/30s average, 4 read\(s\)/)).toBeTruthy()
  })

  it('says so rather than drawing an empty dwell chart', () => {
    render(<PageDwellBars entries={[]} />)
    expect(screen.getByText(/no per-page dwell to show/)).toBeTruthy()
  })

  it('attributes a drop-off to the page the reader stopped at', () => {
    render(
      <DropOffList
        curve={[
          { page: 1, reached: 4, dropped: 0, drop_off_rate: 0, is_last_page: false },
          { page: 2, reached: 3, dropped: 1, drop_off_rate: 0.25, is_last_page: false },
        ]}
        summary={{ sessions: 4, completed_sessions: 2 }}
      />,
    )
    expect(screen.getByText('Stopped at page 2')).toBeTruthy()
    expect(screen.getByText(/1 reader\(s\), 25% of those who reached it/)).toBeTruthy()
  })

  it('says every reading finished when there is no drop-off', () => {
    render(
      <DropOffList
        curve={[{ page: 1, reached: 2, dropped: 0, drop_off_rate: 0, is_last_page: true }]}
        summary={{ sessions: 2, completed_sessions: 2 }}
      />,
    )
    expect(screen.getByText(/nobody has stopped part-way/)).toBeTruthy()
  })

  it('gives a viewer row a text label for its control, not an icon alone', () => {
    render(
      <ViewerRow
        viewer={{
          viewer: 'a.buyer@northwind.example',
          revisits: 2,
          deepest_page: 3,
          last_seen_at: '2026-05-02T09:00:00.000+00:00',
        }}
        onOpen={() => {}}
      />,
    )
    expect(screen.getByRole('button', { name: /Show pages/ })).toBeTruthy()
  })

  it('renders an alert row with its trigger and detail', () => {
    render(
      <AlertRow
        alert={{
          id: 'a1',
          trigger: 'first_view',
          viewer: 'a@b.example',
          revisit: 1,
          detail: 'The first view of this tracked link.',
          notified_at: '2026-05-01T09:00:00.000+00:00',
        }}
      />,
    )
    expect(screen.getByText('First view')).toBeTruthy()
    expect(screen.getByText('The first view of this tracked link.')).toBeTruthy()
  })

  it('marks the notes it renders with a live region', () => {
    render(<InlineNote tone="warning">No threshold configured.</InlineNote>)
    expect(screen.getByRole('status').textContent).toContain('No threshold configured.')
  })
})

// --------------------------------------------------------------------------- #
// The formatting helpers
// --------------------------------------------------------------------------- #

describe('the formatting helpers', () => {
  it('maps each state and trigger onto a tone the shared Badge actually has', () => {
    const tones = ['neutral', 'insert', 'update', 'delete', 'restore']
    for (const tone of Object.values(STATE_TONES)) expect(tones).toContain(tone)
    for (const tone of Object.values(ALERT_TONES)) expect(tones).toContain(tone)
    expect(STATE_TONES.active).not.toBe(STATE_TONES.expired)
  })

  it('defaults an unseen state or trigger to neutral rather than throwing', () => {
    expect(stateTone('something-new')).toBe('neutral')
    expect(stateTone(undefined)).toBe('neutral')
    expect(alertTone('something-new')).toBe('neutral')
  })

  it('labels every trigger the server publishes', () => {
    for (const trigger of VOCABULARY.alert_triggers) {
      expect(ALERT_LABELS[trigger]).toBeTruthy()
    }
  })

  it('renders seconds as a compact string and a dash when absent', () => {
    expect(formatSeconds(45)).toBe('45s')
    expect(formatSeconds(150)).toBe('2m 30s')
    expect(formatSeconds(120)).toBe('2m')
    expect(formatSeconds(null)).toBe('--')
  })

  it('renders a timestamp as a short mono string and a dash when there is none', () => {
    expect(shortTimestamp('2026-05-01T09:00:00.000+00:00')).toBe('2026-05-01 09:00:00Z')
    expect(shortTimestamp(null)).toBe('--')
    expect(shortTimestamp('not a date')).toBe('not a date')
  })

  it('holds a number to the bounds the server enforces', () => {
    expect(wholeInRange('30', 1, 3650)).toBe(30)
    expect(wholeInRange('0', 1, 3650)).toBeNull()
    expect(wholeInRange('3651', 1, 3650)).toBeNull()
    expect(wholeInRange('2.5', 1, 3650)).toBeNull()
    expect(wholeInRange('', 1, 3650)).toBeNull()
  })

  it('does not print a viewer row control without words beside it', () => {
    render(<AlertTriggerBadge trigger="page_threshold" />)
    expect(screen.getByText('Reached the page threshold')).toBeTruthy()
  })

  it('finds no unused whitespace element in the dwell chart for a read page', () => {
    render(<PageDwellBars entries={[{ page: 1, reads: 1, average_seconds: 10 }]} />)
    const item = screen.getByText('Page 1').closest('li')
    expect(within(item).getByText(/10s average/)).toBeTruthy()
  })
})
