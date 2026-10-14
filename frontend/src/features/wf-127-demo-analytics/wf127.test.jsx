/**
 * Tests for WF-127's page and its primitives.
 *
 * The page is driven against a stubbed `fetch` rather than a live server, so the
 * assertions are about what a seller reads and which controls are offered, not
 * about the server's answer. Every test builds its own stub and its own data, so
 * the file passes alone and under vitest's parallel run without depending on
 * another test's fixture.
 *
 * Three of these tests are about the researched facts rather than about
 * rendering: the heatmap prints each cell's count rather than shading a square,
 * the discovery is attributed to a named sender, and the page names the surfaces
 * it does not build rather than omitting them.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import page from './index.jsx'
import {
  DEFAULT_CLICK_PATH_TEXT,
  EVENT_LABELS,
  LEVEL_LABELS,
  ROLE_LABELS,
  ROLE_TONES,
  STATE_TONES,
  demoApi,
  formatRate,
  formatSeconds,
  parseClickPath,
  roleTone,
  shortTimestamp,
  stateTone,
  wholeInRange,
} from './api.js'
import {
  HeatmapGrid,
  InlineNote,
  RollupTable,
  RoleBadge,
  SectionCounts,
  StakeholderRow,
  StateBadge,
} from './primitives.jsx'

const { Component } = page

// `delay: null` makes typing synchronous, so a loaded runner cannot turn a
// keystroke into a timeout. The waitFor calls below carry their own generous
// timeout for the same reason.
const setupUser = () => userEvent.setup({ delay: null })

const VOCABULARY = {
  ticket: 'WF-127',
  demo_states: ['draft', 'published', 'archived'],
  demo_types: ['sandbox', 'tour', 'video'],
  event_types: ['view', 'section_selected', 'doc_downloaded'],
  levels: ['buyer_group', 'top_level', 'team', 'individual', 'recently_shared', 'all_demos'],
  weekday_names: ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'],
  heatmap_bucket_hours: 4,
  defaults: { dql_threshold: 60, dql_max: 100 },
}

function demoRow(overrides = {}) {
  return {
    id: 'wf127_demo_1',
    title: 'Northwind onboarding sandbox',
    demo_type: 'sandbox',
    state: 'published',
    recording_ref: 'rec-2026-04-28',
    team: 'Enterprise',
    owner: 'dana',
    entry: 'overview',
    node_count: 5,
    branch_count: 4,
    sections: ['overview', 'dashboard', 'reports', 'alerts', 'export'],
    published_at: '2026-05-01T09:00:00.000+00:00',
    ...overrides,
  }
}

function analyticsPayload(overrides = {}) {
  return {
    demo: demoRow(),
    metrics: {
      demos_sent: 3,
      stakeholders_discovered: 1,
      discovery_rate: 0.3333,
      views: 3,
      view_rate: 0.6667,
      features_selected: 3,
      docs_downloaded: 1,
      stakeholders: 4,
      viewers: 2,
      internal_previews: 1,
    },
    heatmap: {
      bucket_hours: 4,
      weekdays: VOCABULARY.weekday_names,
      buckets: [
        { index: 0, start_hour: 0, label: '00:00-04:00' },
        { index: 1, start_hour: 4, label: '04:00-08:00' },
        { index: 2, start_hour: 8, label: '08:00-12:00' },
        { index: 3, start_hour: 12, label: '12:00-16:00' },
        { index: 4, start_hour: 16, label: '16:00-20:00' },
        { index: 5, start_hour: 20, label: '20:00-24:00' },
      ],
      cells: [{ weekday: 'Friday', weekday_index: 4, bucket: 2, start_hour: 8, count: 3, viewers: 2 }],
      max_count: 3,
      total: 3,
      busiest: { weekday: 'Friday', weekday_index: 4, bucket: 2, count: 3, viewers: 2 },
    },
    sections: {
      sections: [
        { section: 'overview', label: 'Overview', count: 1, viewers: 1 },
        { section: 'dashboard', label: 'Dashboard', count: 1, viewers: 1 },
        { section: 'reports', label: 'Reports', count: 1, viewers: 1 },
        { section: 'alerts', label: 'Alerts', count: 0, viewers: 0 },
        { section: 'export', label: 'Export', count: 0, viewers: 0 },
      ],
      total: 3,
      most_selected: { section: 'overview', label: 'Overview', count: 1, viewers: 1 },
    },
    reach: [],
    stakeholders: [
      {
        viewer: 'buyer@northwind.example',
        role: 'initial_recipient',
        invited_by: null,
        first_seen_at: '2026-04-28T09:05:00.000+00:00',
        last_seen_at: '2026-04-29T10:00:00.000+00:00',
        views: 2,
        sections: ['dashboard', 'reports'],
        downloads: 1,
        dql: { score: 84, max: 100, threshold: 60, qualified: true },
      },
      {
        viewer: 'cfo@northwind.example',
        role: 'discovered',
        invited_by: 'buyer@northwind.example',
        first_seen_at: '2026-04-29T11:00:00.000+00:00',
        last_seen_at: '2026-04-29T11:02:00.000+00:00',
        views: 1,
        sections: ['overview'],
        downloads: 0,
        dql: { score: 38, max: 100, threshold: 60, qualified: false },
      },
    ],
    levels: VOCABULARY.levels,
    ...overrides,
  }
}

function rollupPayload(overrides = {}) {
  return {
    level: 'top_level',
    levels: VOCABULARY.levels,
    count: 1,
    groups: [
      {
        key: 'all',
        label: 'All demos',
        demos: 2,
        demo_ids: ['wf127_demo_1', 'wf127_demo_2'],
        metrics: {
          demos_sent: 3,
          views: 3,
          view_rate: 0.6667,
          stakeholders_discovered: 1,
          discovery_rate: 0.3333,
        },
      },
    ],
    ...overrides,
  }
}

function payload(overrides = {}) {
  return {
    '/vocabulary': VOCABULARY,
    '/inferences': { count: 10, inferences: [{ id: 'dql_formula' }] },
    '/summary': {
      demos: 2,
      published: 1,
      draft: 0,
      archived: 1,
      demos_sent: 3,
      views: 3,
      stakeholders: 4,
      stakeholders_discovered: 1,
      discovery_rate: 0.3333,
      internal_previews: 1,
    },
    '/demos': { count: 1, room_id: 'room_1', demos: [demoRow()] },
    '/analytics': analyticsPayload(),
    '/rollup': rollupPayload(),
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
  await screen.findByRole('heading', { name: 'Northwind onboarding sandbox' })
}

beforeEach(() => {
  vi.restoreAllMocks()
})

// --------------------------------------------------------------------------- #
// The descriptor
// --------------------------------------------------------------------------- #

describe('the descriptor', () => {
  it('exports the four fields App.jsx reads', () => {
    expect(page.id).toBe('wf-127-demo-analytics')
    expect(page.label).toBe('Interactive demos')
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
    expect(await screen.findByRole('heading', { name: 'Interactive demo analytics' })).toBeTruthy()
  })

  it("calls this feature's own routes, plus the core room collection", async () => {
    const calls = []
    global.fetch = stubFetch(payload(), calls)
    render(<Component />)
    await screen.findByRole('heading', { name: 'Interactive demo analytics' })
    const apiCalls = calls.filter((call) => call.startsWith('/api/'))
    const own = apiCalls.filter((call) => call.includes('/api/wf-127/'))
    const core = apiCalls.filter((call) => !call.includes('/api/wf-127/'))
    expect(own.length).toBeGreaterThan(0)
    expect(core.every((call) => call.startsWith('/api/records/room'))).toBe(true)
  })

  it('shows the four headline figures', async () => {
    global.fetch = stubFetch(payload())
    render(<Component />)
    await screen.findByRole('heading', { name: 'Interactive demo analytics' })
    expect(screen.getByText('Demos')).toBeTruthy()
    expect(screen.getByText('Demos sent')).toBeTruthy()
    expect(screen.getByText('Buyer views')).toBeTruthy()
    expect(screen.getByText('Discovered')).toBeTruthy()
    expect(screen.getByText('Discovery rate 33%')).toBeTruthy()
  })

  it('lists each demo with its state and its branch count', async () => {
    global.fetch = stubFetch(payload())
    render(<Component />)
    await screen.findByRole('heading', { name: 'Interactive demo analytics' })
    expect(screen.getByText('Northwind onboarding sandbox')).toBeTruthy()
    expect(screen.getByText('Published')).toBeTruthy()
    expect(screen.getByText(/5 node\(s\), 4 branch\(es\)/)).toBeTruthy()
  })

  it('shows an empty state rather than a blank list when no demo exists', async () => {
    const answers = payload()
    answers['/demos'] = { count: 0, demos: [] }
    global.fetch = stubFetch(answers)
    render(<Component />)
    expect(await screen.findByText('No interactive demo yet')).toBeTruthy()
  })

  it('refuses a demo with no title before the round trip', async () => {
    const calls = []
    global.fetch = stubFetch(payload(), calls)
    const user = setupUser()
    render(<Component />)
    const submit = await screen.findByRole('button', { name: /Build the demo/ })
    expect(submit).toBeDisabled()
    expect(screen.getByText(/A title is required/)).toBeTruthy()
    await user.click(submit)
    expect(calls.some((call) => call.includes('/wf-127/demos') && call.includes('room_id'))).toBe(
      false,
    )
  })

  it('builds a demo when the form is filled in', async () => {
    const calls = []
    global.fetch = stubFetch(payload(), calls)
    const user = setupUser()
    render(<Component />)
    await screen.findByRole('heading', { name: 'Interactive demo analytics' })

    await user.selectOptions(screen.getByLabelText('Room'), 'room_1')
    await user.type(screen.getByLabelText('Title'), 'Northwind sandbox')
    await user.click(screen.getByRole('button', { name: /Build the demo/ }))

    await waitFor(
      () => {
        expect(
          calls.some(
            (call) => call.includes('/api/wf-127/demos') && call.includes('room_id=room_1'),
          ),
        ).toBe(true)
      },
      { timeout: 5000 },
    )
  })

  it('refuses a click path that branches to an unknown node', async () => {
    global.fetch = stubFetch(payload())
    const user = setupUser()
    render(<Component />)
    await screen.findByRole('heading', { name: 'Interactive demo analytics' })
    const path = screen.getByLabelText('Click path')
    await user.clear(path)
    await user.type(path, 'a | A | ghost')
    expect(screen.getByText(/branches to unknown node "ghost"/)).toBeTruthy()
  })

  it('opens the analytics panel and reads the metrics and the heatmap', async () => {
    const calls = []
    global.fetch = stubFetch(payload(), calls)
    const user = setupUser()
    render(<Component />)
    await screen.findByRole('heading', { name: 'Interactive demo analytics' })
    await openAnalytics(user)

    await waitFor(
      () => {
        expect(calls.some((call) => call.includes('/demos/wf127_demo_1/analytics'))).toBe(true)
      },
      { timeout: 5000 },
    )
    expect(screen.getByText('Activity heatmap')).toBeTruthy()
    expect(screen.getByText('Stakeholders discovered')).toBeTruthy()
    expect(screen.getByText('Six-level roll-up')).toBeTruthy()
  })

  it('attributes a discovered stakeholder to the buyer who forwarded it', async () => {
    global.fetch = stubFetch(payload())
    const user = setupUser()
    render(<Component />)
    await screen.findByRole('heading', { name: 'Interactive demo analytics' })
    await openAnalytics(user)
    expect(screen.getByText('Discovered through buyer@northwind.example')).toBeTruthy()
    expect(screen.getAllByText('Discovered').length).toBeGreaterThan(0)
  })

  it('switches the drill-down level and refetches the roll-up', async () => {
    const calls = []
    global.fetch = stubFetch(payload(), calls)
    const user = setupUser()
    render(<Component />)
    await screen.findByRole('heading', { name: 'Interactive demo analytics' })
    await openAnalytics(user)

    await user.selectOptions(screen.getByLabelText('Drill-down level'), 'team')
    await waitFor(
      () => {
        expect(calls.some((call) => call.includes('/rollup') && call.includes('level=team'))).toBe(
          true,
        )
      },
      { timeout: 5000 },
    )
  })

  it('records a viewer event through the ingest', async () => {
    const calls = []
    global.fetch = stubFetch(payload(), calls)
    const user = setupUser()
    render(<Component />)
    await screen.findByRole('heading', { name: 'Interactive demo analytics' })
    await openAnalytics(user)

    await user.type(screen.getByLabelText('Viewer email'), 'cfo@northwind.example')
    await user.click(screen.getByRole('button', { name: /Record the event/ }))

    await waitFor(
      () => {
        expect(calls.some((call) => call.includes('/demos/wf127_demo_1/events'))).toBe(true)
      },
      { timeout: 5000 },
    )
  })

  it('records a forward share, which discovers the recipients', async () => {
    const calls = []
    global.fetch = stubFetch(payload(), calls)
    const user = setupUser()
    render(<Component />)
    await screen.findByRole('heading', { name: 'Interactive demo analytics' })
    await openAnalytics(user)

    await user.selectOptions(screen.getByLabelText('Kind'), 'forward')
    await user.type(screen.getByLabelText('Sender'), 'buyer@northwind.example')
    await user.type(screen.getByLabelText('Recipients'), 'cfo@northwind.example')
    await user.click(screen.getByRole('button', { name: /Record the share/ }))

    await waitFor(
      () => {
        expect(calls.some((call) => call.includes('/demos/wf127_demo_1/shares'))).toBe(true)
      },
      { timeout: 5000 },
    )
  })

  it('names the surfaces this workflow does not build', async () => {
    global.fetch = stubFetch(payload())
    render(<Component />)
    expect(await screen.findByText('Not built here')).toBeTruthy()
    expect(screen.getByText(/A screen-capture ingest/)).toBeTruthy()
    expect(screen.getByText(/A CRM push/)).toBeTruthy()
    expect(screen.getByText(/Slack, Microsoft 365 App Store/)).toBeTruthy()
  })
})

// --------------------------------------------------------------------------- #
// The primitives
// --------------------------------------------------------------------------- #

describe('the primitives', () => {
  it('prints the state name beside its tone', () => {
    render(<StateBadge state="archived" />)
    expect(screen.getByText('Archived')).toBeTruthy()
  })

  it('names a role in words rather than showing the code', () => {
    render(<RoleBadge role="discovered" />)
    expect(screen.getByText('Discovered')).toBeTruthy()
  })

  it('prints each heatmap cell count rather than shading only', () => {
    render(<HeatmapGrid heatmap={analyticsPayload().heatmap} />)
    const row = screen.getByRole('rowheader', { name: 'Friday' }).closest('tr')
    expect(within(row).getByText('3')).toBeTruthy()
    expect(screen.getByText(/Darker shading means more events/)).toBeTruthy()
  })

  it('says so rather than drawing an empty heatmap', () => {
    render(<HeatmapGrid heatmap={{ ...analyticsPayload().heatmap, cells: [], total: 0 }} />)
    expect(screen.getByText(/no heatmap to draw/)).toBeTruthy()
  })

  it('counts a section selection with its viewer count', () => {
    render(<SectionCounts sections={analyticsPayload().sections} />)
    expect(screen.getByText('Dashboard')).toBeTruthy()
    expect(screen.getAllByText(/1 selection\(s\), 1 viewer\(s\)/).length).toBeGreaterThan(0)
  })

  it('renders a stakeholder with a role, a DQL score and a qualification', () => {
    render(<StakeholderRow stakeholder={analyticsPayload().stakeholders[0]} />)
    expect(screen.getByText('Initial recipient')).toBeTruthy()
    expect(screen.getByText('DQL 84/100')).toBeTruthy()
    expect(screen.getByText('Qualified')).toBeTruthy()
  })

  it('renders a roll-up group with its rates', () => {
    render(<RollupTable rollup={rollupPayload()} />)
    expect(screen.getByText('All demos')).toBeTruthy()
    expect(screen.getByText('67%')).toBeTruthy()
    expect(screen.getByText('33%')).toBeTruthy()
  })

  it('says so rather than drawing an empty roll-up', () => {
    render(<RollupTable rollup={{ groups: [] }} />)
    expect(screen.getByText(/no group to show/)).toBeTruthy()
  })

  it('marks the notes it renders with a live region', () => {
    render(<InlineNote tone="warning">No click path.</InlineNote>)
    expect(screen.getByRole('status').textContent).toContain('No click path.')
  })
})

// --------------------------------------------------------------------------- #
// The helpers
// --------------------------------------------------------------------------- #

describe('the helpers', () => {
  it('parses the default click path into a branching tree', () => {
    const { nodes, error } = parseClickPath(DEFAULT_CLICK_PATH_TEXT)
    expect(error).toBeNull()
    expect(nodes[0]).toEqual({
      id: 'overview',
      label: 'Overview',
      kind: 'screen',
      next: ['dashboard', 'reports'],
    })
    expect(nodes.at(-1).kind).toBe('end')
  })

  it('refuses a click path with a duplicate node id', () => {
    expect(parseClickPath('a | A\na | Again').error).toMatch(/used twice/)
  })

  it('refuses an empty click path', () => {
    expect(parseClickPath('   ').error).toMatch(/at least one node/)
  })

  it('maps each state and role onto a tone the shared Badge actually has', () => {
    const tones = ['neutral', 'insert', 'update', 'delete', 'restore']
    for (const tone of Object.values(STATE_TONES)) expect(tones).toContain(tone)
    for (const tone of Object.values(ROLE_TONES)) expect(tones).toContain(tone)
    expect(STATE_TONES.published).not.toBe(STATE_TONES.archived)
  })

  it('defaults an unseen state or role to neutral rather than throwing', () => {
    expect(stateTone('something-new')).toBe('neutral')
    expect(roleTone(undefined)).toBe('neutral')
  })

  it('labels every event and level the server publishes', () => {
    for (const name of VOCABULARY.event_types) expect(EVENT_LABELS[name]).toBeTruthy()
    for (const name of VOCABULARY.levels) expect(LEVEL_LABELS[name]).toBeTruthy()
    for (const name of VOCABULARY.demo_states) expect(stateTone(name)).toBeTruthy()
    for (const name of VOCABULARY.levels) expect(roleTone(name)).toBeTruthy()
    for (const name of Object.keys(ROLE_LABELS)) expect(roleTone(name)).toBeTruthy()
  })

  it('renders a rate as a whole percentage and a dash when absent', () => {
    expect(formatRate(0.3333)).toBe('33%')
    expect(formatRate(0.6667)).toBe('67%')
    expect(formatRate(null)).toBe('--')
  })

  it('renders seconds as a compact string and a dash when absent', () => {
    expect(formatSeconds(45)).toBe('45s')
    expect(formatSeconds(150)).toBe('2m 30s')
    expect(formatSeconds(null)).toBe('--')
  })

  it('renders a timestamp as a short mono string and a dash when there is none', () => {
    expect(shortTimestamp('2026-05-01T09:00:00.000+00:00')).toBe('2026-05-01 09:00:00Z')
    expect(shortTimestamp(null)).toBe('--')
  })

  it('holds a number to the bounds the server enforces', () => {
    expect(wholeInRange('30', 1, 3650)).toBe(30)
    expect(wholeInRange('0', 1, 3650)).toBeNull()
    expect(wholeInRange('2.5', 1, 3650)).toBeNull()
    expect(wholeInRange('', 1, 3650)).toBeNull()
  })

  it('exposes every route this feature owns through its own wrapper', () => {
    expect(typeof demoApi.analytics).toBe('function')
    expect(typeof demoApi.rollup).toBe('function')
    expect(typeof demoApi.recordEvent).toBe('function')
    expect(typeof demoApi.recordShare).toBe('function')
  })
})
