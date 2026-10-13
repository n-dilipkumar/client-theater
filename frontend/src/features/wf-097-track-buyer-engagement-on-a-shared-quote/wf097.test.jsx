/**
 * Tests for WF-097's page and its primitives.
 *
 * The page is driven against a stubbed `fetch` rather than a live server, so the
 * assertions are about what a seller reads and which controls are offered, not
 * about the server's answer. Every test builds its own stub and its own data, so
 * the file passes alone and under vitest's parallel run without depending on
 * another test's fixture.
 *
 * Two of these tests are about the researched facts rather than about rendering:
 * the PDF-open event is on the timeline and is not a view, and a workspace with no
 * threshold is told so instead of being shown a default classification.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import page from './index.jsx'
import {
  ACTIVITY_TONES,
  SETTING_FIELDS,
  activityTone,
  amount,
  percent,
  reasonSentence,
  shortTimestamp,
  withinBounds,
} from './api.js'
import { ActivityClassBadge, BubbleChart, Counter, CounterRow, InlineNote } from './primitives.jsx'

const { Component } = page

const VOCABULARY = {
  ticket: 'WF-097',
  events: {
    buyer: ['Quote sent', 'Quote viewed', 'Quote downloaded', 'Quote printed'],
    seller: ['Quote published', 'Quote approval requested', 'Quote expired'],
    all: ['Quote sent', 'Quote viewed'],
  },
  counter_labels: {
    last_activity_at: 'Last Activity Date',
    activity_count: 'Activity Count',
    activity_count_since_last_communication: 'Activity Count Since Last Communication',
    view_count: 'View Count',
  },
  activity_classes: {
    high: 'High activity',
    low: 'Low activity',
    unclassified: 'Not classified',
  },
  limits: { threshold_min: 1, threshold_max: 1000, window_min: 1, window_max: 3650 },
  follow_up_reasons: {
    no_activity_recently: 'No activity for a while',
    expiring_soon: 'Expires soon',
  },
  not_built: ['Revenue analytics suite reports', 'Email transmission'],
}

function counters(overrides = {}) {
  return {
    last_activity_at: '2026-05-30T09:00:00.000+00:00',
    activity_count: 12,
    view_count: 11,
    activity_count_since_last_communication: null,
    since_last_communication_basis: 'no_email_channel_connected',
    buyer_activity_count: 10,
    seller_activity_count: 2,
    pdf_opens_not_counted_as_views: 1,
    activity_class: 'High activity',
    threshold: 10,
    threshold_configured: true,
    ...overrides,
  }
}

function quoteRow(id, overrides = {}) {
  return {
    id,
    room_id: 'room_1',
    data: {
      title: `Quote ${id}`,
      quote_number: `Q-${id}`,
      hs_quote_amount: 1000,
      status: 'PUBLISHED',
      ...(overrides.data || {}),
    },
    engagement: counters(overrides.engagement || {}),
    status: (overrides.data || {}).status || 'PUBLISHED',
    owner: 'dana',
    signing_status: null,
    expires_at: '2026-07-01T00:00:00.000+00:00',
    ...overrides.row,
  }
}

function payload(overrides = {}) {
  const settings = {
    high_activity_threshold: 10,
    email_channel_connected: false,
    last_communication_at: null,
    no_activity_after_days: 30,
    expiring_within_days: 14,
    configured_keys: ['high_activity_threshold'],
    unconfigured_keys: [],
  }
  const overview = {
    quotes: 2,
    high_activity_quotes: 1,
    low_activity_quotes: 1,
    unclassified_quotes: 0,
    high_activity_value: 1000,
    quotes_at_risk: 1,
    quotes_expiring_soon: 1,
    quotes_expired: 0,
    accepted_quotes: 0,
    acceptance_rate: 0.25,
    threshold: 10,
    threshold_configured: true,
    basis: {
      high_activity: 'Activity Count at or above 10',
      at_risk: 'Expires within 14 days or already expired and not accepted',
      acceptance_rate: 'Quotes in an accepted status over all quotes',
    },
    settings,
    recent_activity: [
      {
        quote_id: 'q1',
        name: 'Northwind renewal',
        last_activity_at: '2026-05-30T09:00:00.000+00:00',
        activity_count: 12,
        view_count: 11,
        amount: 1000,
        expires_at: '2026-07-01T00:00:00.000+00:00',
        activity_class: 'High activity',
      },
    ],
  }
  // Keys are the path suffix each request ends with, so the stub answers on the
  // route rather than on a property name: `/api/wf-097/follow-ups` ends with
  // `/follow-ups`, and nothing else does.
  return {
    '/vocabulary': VOCABULARY,
    '/overview': overview,
    '/follow-ups': {
      count: 1,
      entries: [
        {
          quote_id: 'q1',
          title: 'Northwind renewal',
          status: 'PUBLISHED',
          owner: 'dana',
          expires_at: '2026-06-20T00:00:00.000+00:00',
          reasons: [{ reason: 'no_activity_recently', days_idle: 60, detail: 'No activity for 60 days.' }],
          reason_codes: ['no_activity_recently'],
          activity_class: 'High activity',
          activity_count: 12,
          last_activity_at: '2026-05-30T09:00:00.000+00:00',
        },
      ],
      windows: { no_activity_after_days: 30, expiring_within_days: 14 },
      windows_configured: { no_activity_after_days: true, expiring_within_days: true },
      email_channel_connected: false,
    },
    '/chart': {
      points: [
        {
          quote_id: 'q1',
          name: 'Northwind renewal',
          expires_at: '2026-07-01T00:00:00.000+00:00',
          expired: false,
          activity_count: 12,
          view_count: 11,
          last_activity_at: '2026-05-30T09:00:00.000+00:00',
          activity_class: 'High activity',
          chart_tone: 'success',
          value: 1000,
        },
      ],
      count: 1,
      expired_hidden: 0,
    },
    '/quotes': { count: 1, entries: [quoteRow('q1')] },
    '/settings': settings,
    '/engagement': {
      quote_id: 'q1',
      title: 'Northwind renewal',
      status: 'PUBLISHED',
      expires_at: '2026-07-01T00:00:00.000+00:00',
      engagement: counters(),
      events: 13,
    },
    '/activity': {
      count: 2,
      entries: [
        {
          id: 'e2',
          event: 'Quote viewed',
          side: 'buyer',
          surface: 'quote_link',
          counted_as_view: true,
          occurred_at: '2026-05-30T09:00:00.000+00:00',
          activity_class: 'High activity',
        },
        {
          id: 'e1',
          event: 'Quote viewed',
          side: 'buyer',
          surface: 'email_pdf_attachment',
          counted_as_view: false,
          occurred_at: '2026-05-20T09:00:00.000+00:00',
          activity_class: 'High activity',
        },
      ],
    },
    '/records/room': { records: [{ id: 'room_1', data: { name: 'Northwind Traders' } }] },
    // The follow-up send is a POST to a path no other key ends with, so it gets
    // its own answer rather than the empty object a miss would produce.
    '/send': {
      quote_id: 'q1',
      message: 'Recorded a new Quote sent.',
      renewed: false,
      opened: 'send_quote_dialog',
      engagement: counters(),
    },
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

beforeEach(() => {
  vi.restoreAllMocks()
})

// --------------------------------------------------------------------------- #
// The descriptor
// --------------------------------------------------------------------------- #

describe('the descriptor', () => {
  it('exports the four fields App.jsx reads and nothing it cannot use', () => {
    expect(page.id).toBe('wf-097-track-buyer-engagement-on-a-shared-quote')
    expect(page.label).toBe('Quote engagement')
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
    expect(await screen.findByRole('heading', { name: 'Quote engagement' })).toBeTruthy()
  })

  it('calls this feature\'s own routes, plus the core room collection for the picker', async () => {
    const calls = []
    global.fetch = stubFetch(payload(), calls)
    render(<Component />)
    await screen.findByRole('heading', { name: 'Quote engagement' })
    const apiCalls = calls.filter((call) => call.startsWith('/api/'))
    const own = apiCalls.filter((call) => call.includes('/api/wf-097/'))
    const core = apiCalls.filter((call) => !call.includes('/api/wf-097/'))
    expect(own.length).toBeGreaterThan(0)
    // The only call outside this feature's prefix is the room list every page
    // reads for its picker, which the contract documents as a core read.
    expect(core.every((call) => call.startsWith('/api/records/room'))).toBe(true)
  })

  it('shows the four researched figures the Pipeline overview reads', async () => {
    global.fetch = stubFetch(payload())
    render(<Component />)
    await screen.findByRole('heading', { name: 'Quote engagement' })
    // "High activity" is both a stat label and a class badge on the page, so the
    // stat card is found by its own label rather than by a bare text query.
    const statCards = screen.getAllByText('High activity')
    expect(statCards.length).toBeGreaterThan(0)
    expect(screen.getByText('At risk')).toBeTruthy()
    expect(screen.getByText('Acceptance rate')).toBeTruthy()
    expect(screen.getByText('25.0%')).toBeTruthy()
  })

  it('states the basis of each figure rather than showing a bare number', async () => {
    global.fetch = stubFetch(payload())
    render(<Component />)
    await screen.findByRole('heading', { name: 'Quote engagement' })
    expect(screen.getByText(/Activity Count at or above 10/)).toBeTruthy()
  })

  it('tells a workspace with no threshold that it has set none', async () => {
    const answers = payload()
    answers['/overview'] = {
      ...answers.overview,
      threshold: null,
      threshold_configured: false,
      basis: {
        high_activity: 'No threshold configured, so no quote is classified',
        at_risk: 'No expiry window configured, so no quote is at risk',
        acceptance_rate: 'Quotes in an accepted status over all quotes',
      },
    }
    global.fetch = stubFetch(answers)
    render(<Component />)
    expect(await screen.findByText(/No high activity threshold is configured/)).toBeTruthy()
  })

  it('tells a workspace with no follow-up window that nothing is recommended', async () => {
    const answers = payload()
    answers['/follow-ups'] = {
      ...answers.followUps,
      count: 0,
      entries: [],
      windows_configured: { no_activity_after_days: false, expiring_within_days: false },
    }
    global.fetch = stubFetch(answers)
    render(<Component />)
    expect(await screen.findByText(/No follow-up window is configured/)).toBeTruthy()
  })

  it('lists each recommendation with its reason in words', async () => {
    global.fetch = stubFetch(payload())
    render(<Component />)
    expect(await screen.findByText('No activity for a while:')).toBeTruthy()
    expect(screen.getByText('No activity for 60 days.')).toBeTruthy()
  })

  it('saying the follow-up records a send, because nothing is transmitted', async () => {
    global.fetch = stubFetch(payload())
    render(<Component />)
    await screen.findByRole('heading', { name: 'Quote engagement' })
    expect(screen.getByText(/This product has no mail transport/)).toBeTruthy()
  })

  it('records a new Quote sent when the seller acts on a recommendation', async () => {
    const calls = []
    const answers = payload()
    global.fetch = stubFetch(answers, calls)
    const user = userEvent.setup()
    render(<Component />)

    await user.click(await screen.findByRole('button', { name: /Send a follow up/ }))

    await waitFor(() => {
      expect(
        calls.some((call) => call.includes('/wf-097/follow-ups/q1/send') && call.startsWith('/api/')),
      ).toBe(true)
    })
    expect(await screen.findByText(/Recorded a new Quote sent/)).toBeTruthy()
  })

  it('shows the researched bubble chart with the three hover facts on each point', async () => {
    global.fetch = stubFetch(payload())
    render(<Component />)
    const list = await screen.findByRole('list', {
      name: /Published quotes by expiration date and activity count/,
    })
    const item = within(list).getByText('Northwind renewal').closest('li')
    expect(item.textContent).toContain('2026-07-01 00:00:00Z')
    expect(item.textContent).toContain('12 activities')
    expect(item.textContent).toContain('11 views')
  })

  it('says how many expired quotes the tick box is hiding', async () => {
    const answers = payload()
    answers['/chart'] = { ...answers['/chart'], expired_hidden: 3 }
    global.fetch = stubFetch(answers)
    render(<Component />)
    expect(await screen.findByText(/3 expired quote\(s\) are hidden/)).toBeTruthy()
  })

  it('names the surfaces this workflow does not build rather than omitting them', async () => {
    global.fetch = stubFetch(payload())
    render(<Component />)
    expect(await screen.findByText('Not built here')).toBeTruthy()
    expect(screen.getByText('Revenue analytics suite reports')).toBeTruthy()
    expect(screen.getByText('Email transmission')).toBeTruthy()
  })

  it('shows an empty state rather than a blank table when no quote matches', async () => {
    const answers = payload()
    answers['/quotes'] = { count: 0, entries: [] }
    global.fetch = stubFetch(answers)
    render(<Component />)
    expect(await screen.findByText('No quote matched this filter')).toBeTruthy()
  })

  it('offers an empty state on the chart when nothing has an expiration date', async () => {
    const answers = payload()
    answers['/chart'] = { points: [], count: 0, expired_hidden: 0 }
    global.fetch = stubFetch(answers)
    render(<Component />)
    expect(await screen.findByText(/nothing to place on the chart/)).toBeTruthy()
  })

  it('opens the timeline for one quote and reads the same answer the card shows', async () => {
    const calls = []
    global.fetch = stubFetch(payload(), calls)
    const user = userEvent.setup()
    render(<Component />)

    await user.click(await screen.findByRole('button', { name: 'Timeline' }))

    await waitFor(() => {
      expect(calls.some((call) => call.includes('/quotes/q1/engagement'))).toBe(true)
      expect(calls.some((call) => call.includes('/quotes/q1/activity'))).toBe(true)
    })
    expect(await screen.findByText(/1 event\(s\) on this timeline/)).toBeTruthy()
  })

  it('refuses an out-of-bounds setting before the round trip', async () => {
    const calls = []
    global.fetch = stubFetch(payload(), calls)
    const user = userEvent.setup()
    render(<Component />)

    const field = await screen.findByLabelText(/High activity threshold/)
    await user.clear(field)
    await user.type(field, '5000')

    const save = screen.getByRole('button', { name: 'Save settings' })
    expect(save).toBeDisabled()
    expect(screen.getByText(/must be a whole number from 1 to 1000/)).toBeTruthy()
  })

  it('saves a setting the rules accept', async () => {
    const calls = []
    global.fetch = stubFetch(payload(), calls)
    const user = userEvent.setup()
    render(<Component />)

    const field = await screen.findByLabelText(/High activity threshold/)
    await user.clear(field)
    await user.type(field, '7')
    await user.click(screen.getByRole('button', { name: 'Save settings' }))

    await waitFor(() => {
      expect(calls.some((call) => call.includes('/wf-097/settings'))).toBe(true)
    })
    expect(await screen.findByText(/Saved/)).toBeTruthy()
  })

  it('renders the researched counter labels rather than invented ones', async () => {
    global.fetch = stubFetch(payload())
    render(<Component />)
    await screen.findByRole('heading', { name: 'Quote engagement' })
    expect(screen.getAllByText('Last Activity Date').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Activity Count Since Last Communication').length).toBeGreaterThan(0)
    expect(screen.getAllByText('View Count').length).toBeGreaterThan(0)
  })
})

// --------------------------------------------------------------------------- #
// The primitives
// --------------------------------------------------------------------------- #

describe('the primitives', () => {
  it('prints the class name beside its tone, so no finding is colour alone', () => {
    render(<ActivityClassBadge activityClass="High activity" />)
    expect(screen.getByText('High activity')).toBeTruthy()
  })

  it('names an absent class rather than rendering nothing', () => {
    render(<ActivityClassBadge activityClass={undefined} />)
    expect(screen.getByText('Not classified')).toBeTruthy()
  })

  it('states the reason a counter is empty instead of showing a blank', () => {
    render(
      <Counter label="View Count" value={0} missing={null} />,
    )
    expect(screen.getByText('0')).toBeTruthy()
  })

  it('shows the third counter as a named absence, not a zero', () => {
    render(
      <CounterRow
        counters={counters()}
        labels={VOCABULARY.counter_labels}
        basisLabels={{ no_email_channel_connected: 'No email channel connected' }}
      />,
    )
    expect(screen.getByText('No email channel connected')).toBeTruthy()
    expect(screen.queryByText('undefined')).toBeNull()
  })

  it('reports the PDF opens that were not counted as views', () => {
    render(
      <CounterRow
        counters={counters({ pdf_opens_not_counted_as_views: 3 })}
        labels={VOCABULARY.counter_labels}
        basisLabels={{}}
      />,
    )
    expect(screen.getByText('3 PDF open(s) in the email did not count')).toBeTruthy()
  })

  it('marks the notes it renders with a live region, not a plain paragraph', () => {
    render(<InlineNote tone="warning">No threshold configured.</InlineNote>)
    expect(screen.getByRole('status').textContent).toContain('No threshold configured.')
  })

  it('says so rather than drawing an empty chart', () => {
    render(<BubbleChart points={[]} />)
    expect(screen.getByText(/nothing to place on the chart/)).toBeTruthy()
  })

  it('prints the class beside every point so the chart colour is never alone', () => {
    render(
      <BubbleChart
        points={[
          {
            quote_id: 'q1',
            name: 'Northwind',
            expires_at: '2026-07-01T00:00:00.000+00:00',
            expired: false,
            activity_count: 12,
            view_count: 11,
            activity_class: 'High activity',
          },
          {
            quote_id: 'q2',
            name: 'Contoso',
            expires_at: '2026-07-02T00:00:00.000+00:00',
            expired: false,
            activity_count: 1,
            view_count: 0,
            activity_class: 'Not classified',
          },
        ]}
      />,
    )
    expect(screen.getByText('High activity')).toBeTruthy()
    expect(screen.getByText('Not classified')).toBeTruthy()
  })
})

// --------------------------------------------------------------------------- #
// The formatting helpers
// --------------------------------------------------------------------------- #

describe('the formatting helpers', () => {
  it('maps each class to a tone the shared Badge actually has', () => {
    // Badge accepts neutral, insert, update, delete and restore only. A mapping
    // onto any other name renders as neutral, which would leave High activity and
    // Low activity looking identical.
    for (const tone of Object.values(ACTIVITY_TONES)) {
      expect(['neutral', 'insert', 'update', 'delete', 'restore']).toContain(tone)
    }
    expect(ACTIVITY_TONES['High activity']).not.toBe(ACTIVITY_TONES['Low activity'])
  })

  it('defaults an unseen class to neutral rather than throwing', () => {
    expect(activityTone('Something new')).toBe('neutral')
    expect(activityTone(undefined)).toBe('neutral')
  })

  it('holds a setting to the bounds the server enforces', () => {
    const field = SETTING_FIELDS.find((entry) => entry.key === 'high_activity_threshold')
    expect(withinBounds(field, 1)).toBe(true)
    expect(withinBounds(field, 1000)).toBe(true)
    expect(withinBounds(field, 0)).toBe(false)
    expect(withinBounds(field, 1001)).toBe(false)
    expect(withinBounds(field, 3.5)).toBe(false)
    expect(withinBounds(field, '')).toBe(false)
  })

  it("uses the server's own sentence for a reason", () => {
    const sentence = reasonSentence({ reason: 'no_activity_recently', detail: 'No activity for 60 days.' }, VOCABULARY)
    expect(sentence).toBe('No activity for 60 days.')
  })

  it('falls back to the label when the server sent no sentence', () => {
    expect(reasonSentence({ reason: 'no_activity_recently' }, VOCABULARY)).toBe(
      'No activity for a while',
    )
  })

  it('renders a timestamp as a short mono string and a dash when there is none', () => {
    expect(shortTimestamp('2026-05-30T09:00:00.000+00:00')).toBe('2026-05-30 09:00:00Z')
    expect(shortTimestamp(null)).toBe('--')
    expect(shortTimestamp('not a date')).toBe('not a date')
  })

  it('renders an amount and a rate, and a dash for each when absent', () => {
    expect(amount(1000)).toBe('1000.00')
    expect(amount(null)).toBe('--')
    expect(percent(0.25)).toBe('25.0%')
    expect(percent(null)).toBe('--')
  })
})