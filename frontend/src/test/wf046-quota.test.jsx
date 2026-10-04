import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ThrottleQuota from '@/features/wf-046-throttle-and-retry-under-vendor-api-r/ThrottleQuota.jsx'
import descriptor from '@/features/wf-046-throttle-and-retry-under-vendor-api-r/index.jsx'
import {
  BudgetBar,
  formatSeconds,
  kindLabel,
  stateTone,
} from '@/features/wf-046-throttle-and-retry-under-vendor-api-r/primitives.jsx'

/**
 * Tests for the WF-046 quota page.
 *
 * Five things the backend tests cannot see, and each one is a way this page would
 * otherwise be quietly wrong:
 *
 * * **The page is discovered at all.** `lib/features.js` globs
 *   `src/features/*\/index.jsx`. A feature that ships its page and its primitives
 *   but no descriptor builds green and appears in the product zero times, so the
 *   descriptor is pinned here.
 * * **An unreported half renders as "not reported", never as an empty bar.**
 *   HubSpot omits the daily rate-limit headers on OAuth responses, so a connection
 *   that sent none has an *unknown* daily budget. A bar drawn at 0% would tell an
 *   operator they have spent everything, which is the exact failure this feature
 *   exists to prevent.
 * * **No status is conveyed by colour alone.** Every state renders its own word.
 *   A badge tinted by tone is invisible to a screen reader and to a colour-blind
 *   reader, so the assertion is on the text, not the class.
 * * **Every wait says where its number came from.** `Retry-After: 45` and "the
 *   room's ladder" are different facts and an operator deciding whether to wait
 *   has to be able to tell them apart.
 * * **The pause control is a real button that reaches its route**, and it says that
 *   resuming does not refill the bucket.
 *
 * Stubs are keyed on this feature's own paths. An unstubbed path throws, so a test
 * cannot quietly pass against the wrong response.
 */

const ROOM = 'room_1'
const HUBSPOT = 'throttle_connection_hub'
const SALESFORCE = 'throttle_connection_sf'

const VOCABULARY = {
  flow: [{ step: 1, served_by: 'dsr.throttle.bucket' }],
  data_flow: '... idempotency key',
  batch_states: [
    { value: 'proceeding', terminal: false, what: 'going out now' },
    { value: 'deferred', terminal: false, what: 'queued' },
    { value: 'needs_action', terminal: true, what: 'a person decides' },
    { value: 'complete', terminal: true, what: 'accepted' },
  ],
  live_states: ['proceeding', 'deferred', 'retrying'],
}

const HUBSPOT_CONNECTION = {
  id: HUBSPOT,
  label: 'Northwind - HubSpot',
  vendor: 'hubspot',
  paused: false,
  bucket: { tokens: 42, capacity: 110 },
  effective: { preemptive: true, tokens_per_second: 11 },
}

const SALESFORCE_CONNECTION = {
  id: SALESFORCE,
  label: 'Contoso - Salesforce',
  vendor: 'salesforce',
  paused: true,
  pause_reason: 'the vendor is refusing',
  bucket: { tokens: 0, capacity: 0 },
  effective: { preemptive: false, tokens_per_second: 0 },
}

const BATCHES = {
  count: 2,
  counts: { deferred: 1, needs_action: 1 },
  batches: [
    {
      id: 'throttle_batch_a',
      vendor: 'hubspot',
      state: 'deferred',
      terminal: false,
      decision_reason: 'empty',
      signal: { kind: 'lock', code: '' },
      schedule: {
        seconds: 2,
        source: 'retry_after',
        basis: "the vendor sent \"Retry-After: 1\", so the room waits 2s, which is this class's documented 2s floor",
      },
      keys: [{ idempotency_key: 'aa11' }, { idempotency_key: 'bb22' }],
      keys_reused: true,
    },
    {
      id: 'throttle_batch_b',
      vendor: 'salesforce',
      state: 'needs_action',
      terminal: true,
      decision_reason: 'proceed',
      signal: null,
      schedule: { seconds: null, source: 'beyond_cap', basis: 'longer than the room waits alone' },
      keys: [{ idempotency_key: 'cc33' }],
      keys_reused: false,
    },
  ],
}

const LOG = {
  count: 1,
  events: [
    {
      id: 'log1',
      at: '2026-09-27T12:00:00+00:00',
      event: 'batch_deferred_rate_limit',
      detail: 'the vendor sent "Retry-After: 45"',
      wait_seconds: 45,
    },
  ],
}

const ROUTES = {
  '/api/records/room?limit=100': { records: [{ id: ROOM, data: { name: 'Northwind' } }] },
  '/api/wf-046/connections?room_id=room_1': {
    count: 2,
    paused: 1,
    preemptive: 1,
    connections: [HUBSPOT_CONNECTION, SALESFORCE_CONNECTION],
  },
  '/api/wf-046/rooms/room_1/summary': {
    batches: 2,
    deferred: 1,
    needs_action: 1,
    connections: 2,
  },
  '/api/wf-046/rooms/room_1/quota': {
    count: 1,
    note: 'the room holds no credential',
    meters: [
      {
        connection_id: HUBSPOT,
        vendor: 'hubspot',
        // The window arrived; the daily half did not, which is the OAuth case.
        window_remaining: 97,
        window_total: 100,
        window_seconds: 10,
        daily_remaining: null,
        daily_total: null,
        known: true,
      },
    ],
  },
  '/api/wf-046/rooms/room_1/batches?limit=50': BATCHES,
  '/api/wf-046/rooms/room_1/throttle-log': LOG,
  '/api/wf-046/vocabulary': VOCABULARY,
}

function stubFetch(extra = {}) {
  const routes = { ...ROUTES, ...extra }
  return vi.fn(async (input) => {
    const path = String(input)
    if (!(path in routes)) throw new Error(`unstubbed path: ${path}`)
    return { ok: true, status: 200, statusText: 'OK', json: async () => routes[path] }
  })
}

async function renderPage(fetchImpl) {
  global.fetch = fetchImpl
  render(<ThrottleQuota />)
  await screen.findByText('Quota and throttling')
}

describe('WF-046 quota page', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('is discovered with the descriptor the backend feature id matches', () => {
    expect(descriptor.id).toBe('wf-046-throttle-and-retry-under-vendor-api-ra')
    expect(descriptor.label).toBe('Quota and throttling')
    expect(descriptor.Component).toBe(ThrottleQuota)
    expect(descriptor.iconPath).toBeTruthy()
  })

  it('renders both connections and says which can refuse a call before it sends', async () => {
    await renderPage(stubFetch())
    expect(await screen.findByText('Northwind - HubSpot')).toBeInTheDocument()
    expect(screen.getByText('Contoso - Salesforce')).toBeInTheDocument()
    // The words, not a colour: "refuses before it sends" is the difference a
    // reviewer has to be able to see.
    expect(screen.getByText(/refuses before it sends/)).toBeInTheDocument()
    expect(screen.getByText(/waits on the vendor/)).toBeInTheDocument()
  })

  it('reads an unreported quota half as not reported rather than as empty', async () => {
    await renderPage(stubFetch())
    await screen.findByText('Northwind - HubSpot')
    // Both connections render a daily bar, and neither can be drawn: the HubSpot
    // meter carries the window half only (the OAuth case HubSpot documents), and
    // the Salesforce connection has sent no headers at all. Every one of them must
    // say so rather than read as zero.
    const daily = screen.getAllByRole('progressbar', { name: /daily allowance/i })
    expect(daily).toHaveLength(2)
    for (const bar of daily) {
      expect(bar).toHaveAttribute('aria-valuetext', 'Budget not reported by the vendor')
      expect(bar).not.toHaveAttribute('aria-valuenow')
    }
    // The window half did arrive on the HubSpot meter, and it reads with both
    // numbers rather than a percentage the reader has to trust.
    const window = screen
      .getAllByRole('progressbar', { name: /burst window/i })
      .find((bar) => bar.getAttribute('aria-valuetext') !== 'Budget not reported by the vendor')
    expect(window).toBeDefined()
    expect(window).toHaveAttribute('aria-valuetext', '97 of 100 remaining')
    expect(window).toHaveAttribute('aria-valuenow', '97')
  })

  it('never conveys a state by colour alone', async () => {
    await renderPage(stubFetch())
    await screen.findByText('Northwind - HubSpot')
    expect(screen.getAllByText('deferred').length).toBeGreaterThan(0)
    expect(screen.getAllByText('needs_action').length).toBeGreaterThan(0)
    expect(screen.getByText('sending')).toBeInTheDocument()
    expect(screen.getByText('paused')).toBeInTheDocument()
  })

  it('says where every wait came from, including the two second lock floor', async () => {
    await renderPage(stubFetch())
    expect(await screen.findByText(/because the vendor said so/)).toBeInTheDocument()
    expect(screen.getByText(/documented 2s floor/)).toBeInTheDocument()
    // Matched on the sentence rather than on the words "Waiting for a person",
    // which also label the summary card.
    expect(
      screen.getByText(/longer wait than the room takes alone/),
    ).toBeInTheDocument()
  })

  it('shows that a retry reuses the keys the batch was first given', async () => {
    await renderPage(stubFetch())
    expect(await screen.findByText(/2 key\(s\), reused/)).toBeInTheDocument()
    expect(screen.getByText(/1 key\(s\)$/)).toBeInTheDocument()
  })

  it('posts to the pause route and says resuming does not refill the bucket', async () => {
    const fetchImpl = stubFetch({
      [`/api/wf-046/connections/${HUBSPOT}/pause`]: {},
    })
    await renderPage(fetchImpl)
    await userEvent.click(await screen.findByRole('button', { name: /pause sending/i }))

    await waitFor(() => {
      const posted = fetchImpl.mock.calls.filter((call) => String(call[1]?.method) === 'POST')
      expect(posted.some((call) => String(call[0]).includes(`/connections/${HUBSPOT}/pause`))).toBe(
        true,
      )
    })
    expect(await screen.findByText(/Paused Northwind - HubSpot/)).toBeInTheDocument()
  })

  it('says no status by colour when a connection is paused, with its reason', async () => {
    await renderPage(stubFetch())
    // Matched on the element's whole text, because the reason and the sentence
    // beside it are two text nodes in one paragraph.
    await waitFor(() => {
      expect(
        screen.getByText((content) => content.includes('the vendor is refusing')),
      ).toBeInTheDocument()
    })
    expect(screen.getByText(/Resuming does not refill the bucket/)).toBeInTheDocument()
    expect(screen.getByText('Paused by hand.')).toBeInTheDocument()
  })

  it('says so when a room has no connector yet', async () => {
    await renderPage(
      stubFetch({
        '/api/wf-046/connections?room_id=room_1': {
          count: 0,
          paused: 0,
          preemptive: 0,
          connections: [],
        },
      }),
    )
    expect(await screen.findByText(/No connector has been declared/)).toBeInTheDocument()
  })

  it('says so when nothing has ever been throttled', async () => {
    await renderPage(
      stubFetch({
        '/api/wf-046/rooms/room_1/batches?limit=50': { count: 0, counts: {}, batches: [] },
        '/api/wf-046/rooms/room_1/throttle-log': { count: 0, events: [] },
      }),
    )
    expect(await screen.findByText(/No batch has been submitted/)).toBeInTheDocument()
    expect(screen.getByText(/Nothing has been throttled/)).toBeInTheDocument()
  })
})

describe('WF-046 primitives', () => {
  it('renders a known budget as a percentage of what is left', () => {
    render(<BudgetBar label="Daily" remaining={249812} total={250000} />)
    const bar = screen.getByRole('progressbar', { name: 'Daily' })
    expect(bar).toHaveAttribute('aria-valuetext', '249812 of 250000 remaining')
    expect(bar).toHaveAttribute('aria-valuenow', '100')
  })

  it('never renders a zero total as a full or an empty bar', () => {
    render(<BudgetBar label="Daily" />)
    expect(screen.getByRole('progressbar', { name: 'Daily' })).toHaveAttribute(
      'aria-valuetext',
      'Budget not reported by the vendor',
    )
  })

  it('formats a wait in the largest unit that still reads exactly', () => {
    expect(formatSeconds(45)).toBe('45s')
    expect(formatSeconds(600)).toBe('10m')
    expect(formatSeconds(7200)).toBe('2h')
    expect(formatSeconds(172800)).toBe('2d')
    expect(formatSeconds(null)).toBe('an unscheduled wait')
  })

  it('reads a tone from the published vocabulary, not from a compiled list', () => {
    expect(stateTone(VOCABULARY, 'complete')).toBe('insert')
    expect(stateTone(VOCABULARY, 'needs_action')).toBe('delete')
    expect(stateTone(VOCABULARY, 'deferred')).toBe('update')
    expect(stateTone(VOCABULARY, 'proceeding')).toBe('neutral')
    expect(stateTone(VOCABULARY, 'a_state_added_on_the_server')).toBe('neutral')
    expect(stateTone(undefined, 'complete')).toBe('neutral')
  })

  it('names a throttle kind in plain words', () => {
    expect(kindLabel('lock')).toBe('high-volume lock')
    expect(kindLabel('rate_limit')).toBe('rate limit')
    expect(kindLabel(null)).toBe('not a throttle')
  })
})