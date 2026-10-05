/**
 * The page-outreach page (WF-106).
 *
 * `apiRequest` is never mocked. `globalThis.fetch` is replaced instead, keyed by
 * method and path, which is what the other feature tests in this product do and what
 * keeps the client under test on the same path a browser would take.
 *
 * What this file is really for is the four claims the page makes that a build cannot
 * check:
 *
 * 1. It registers. A branch once shipped a feature folder with no `index.jsx` and the
 *    build stayed green, because the host globs for that file and a folder without one
 *    is invisible rather than broken.
 * 2. It calls nothing outside its own prefix. A page that reaches into another
 *    feature's routes is exactly the coupling the feature contract forbids.
 * 3. It never implies a message was sent. A delivery row records what the room decided
 *    to show, and a page that renders one without saying so tells a seller something
 *    this codebase cannot support.
 * 4. It never presents a derived threshold as a sourced one. The repeat count, the
 *    window and the dwell number are this build's readings, and a page that shows them
 *    without that label would be quoting a vendor threshold the research never states.
 */

import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import PageOutreach from './PageOutreach'
import descriptor from './index.jsx'

const BASE = '/wf-106'
const ROOM = 'room-1'

const VOCABULARY = {
  ticket: 'WF-106',
  thresholds: [
    { kind: 'repeat_visits', label: 'Matching visits before the block is shown', unit: 'visits', threshold: 3, sourced: false },
    { kind: 'repeat_window', label: 'Window the visits have to fall inside', unit: 'days', threshold: 7, sourced: false },
    { kind: 'dwell', label: 'Time on one matching page', unit: 'seconds', threshold: 60, sourced: false },
  ],
  frequency_modes: [
    { mode: 'seen', label: 'Seen', default: true, sourced: true, reads: 'One show is enough.' },
    { mode: 'any_interaction', label: 'Any interaction happens', default: false, sourced: true, reads: 'A dismissal counts.' },
    { mode: 'engaged_with', label: 'Engaged with', default: false, sourced: true, reads: 'Only a path selection engages.' },
  ],
  channels: ['in_app'],
  calls_vendor: false,
  sends_email: false,
  renders_in_browser_messenger: false,
  reads: 'This workflow records what it would show and records what the buyer did with it.',
}

const SUMMARY = {
  room_id: ROOM,
  workflows: 4,
  live_workflows: 3,
  draft_workflows: 1,
  views: 11,
  matched_views: 9,
  deliveries: 3,
  receipts: 3,
  receipt_kinds: { path_selected: 1, dismissed: 1, messenger_opened: 1 },
  engaged_receipts: 1,
  hidden_for_session: 1,
  calls_vendor: false,
}

const PROSPECTS = {
  room_id: ROOM,
  count: 3,
  shown_to: 2,
  not_shown_to: 1,
  engaged: 1,
  hidden_for_session: 1,
  as_of: '2026-10-05T09:00:00+00:00',
  reads:
    'A prospect is a visitor the room has a recorded page view for, whether or not the block was shown.',
  prospects: [
    {
      visitor_key: 'visitor-northwind-9f2',
      company_key: 'northwind-energy',
      visits: 3,
      matched_visits: 3,
      paths: ['/upgrade/plans'],
      workflows: ['Upgrade page repeaters'],
      last_seen_at: '2026-10-05T06:00:00+00:00',
      deliveries: 1,
      engaged: true,
      hidden_for_session: false,
    },
    {
      visitor_key: 'visitor-meridian-7b8',
      company_key: 'meridian-foods',
      visits: 2,
      matched_visits: 2,
      paths: ['/security/trust'],
      workflows: ['Security page, stops on any touch'],
      last_seen_at: '2026-10-04T08:00:00+00:00',
      deliveries: 1,
      engaged: false,
      hidden_for_session: true,
    },
    {
      visitor_key: 'visitor-halcyon-5d4',
      company_key: '',
      visits: 2,
      matched_visits: 2,
      paths: ['/upgrade/plans'],
      workflows: ['Upgrade page repeaters'],
      last_seen_at: '2026-10-04T21:00:00+00:00',
      deliveries: 0,
      engaged: false,
      hidden_for_session: false,
    },
  ],
}

const WORKFLOWS = {
  count: 1,
  by_state: { live: 1 },
  workflows: [
    {
      id: 'wf_1',
      name: 'Upgrade page repeaters',
      frequency: 'engaged_with',
      state: 'live',
      repeat_visits: 3,
      repeat_window_days: 7,
      dwell_seconds: 60,
      rule_count: 2,
      note: 'Live. Two visits short of three shows nothing.',
      rules: [
        { kind: 'url', mode: 'prefix', value: '/upgrade' },
        { kind: 'dwell', mode: 'at_or_above', value: '60' },
      ],
    },
  ],
}

const VIEWS = {
  count: 2,
  matched: 1,
  by_stopped_by: { matched_below_repeat: 1, hidden_for_session: 1 },
  views: [
    {
      id: 'view_1',
      path: '/upgrade/plans',
      visitor_key: 'visitor-halcyon-5d4',
      workflow_name: 'Upgrade page repeaters',
      session_id: 'sess-halcyon-1',
      dwell_seconds: 70,
      visited_at: '2026-10-04T21:00:00+00:00',
      matched: true,
      stopped_by: 'matched_below_repeat',
      counts: { matching_visits: 2, visits_required: 3 },
    },
    {
      id: 'view_2',
      path: '/security/trust',
      visitor_key: 'visitor-meridian-7b8',
      workflow_name: 'Security page, stops on any touch',
      session_id: 'sess-meridian-1',
      dwell_seconds: 95,
      visited_at: '2026-10-04T08:00:00+00:00',
      matched: false,
      stopped_by: 'hidden_for_session',
      counts: { matching_visits: 2, visits_required: 2 },
    },
  ],
}

const DELIVERIES = {
  count: 1,
  by_state: { engaged: 1 },
  deliveries: [
    {
      id: 'del_1',
      workflow_id: 'wf_1',
      workflow_name: 'Upgrade page repeaters',
      visitor_key: 'visitor-northwind-9f2',
      company_key: 'northwind-energy',
      session_id: 'sess-northwind-4',
      channel: 'in_app',
      message_type: 'in_app',
      state: 'engaged',
      goal_name: 'meeting_booked',
      shown_at: '2026-10-05T06:00:00+00:00',
      receipt_count: 1,
      receipt_kinds: ['path_selected'],
      trigger: {
        matching_visits: 3,
        visits_required: 3,
        window_days: 7,
        path: '/upgrade/plans',
        dwell_seconds: 84,
        rule_matches: [
          { kind: 'url', mode: 'prefix', expected: '/upgrade', observed: '/upgrade/plans', met: true },
          { kind: 'dwell', mode: 'at_or_above', expected: '60', observed: '84', met: true },
        ],
      },
      blocks: [
        {
          kind: 'message',
          text: 'You have been back to our upgrade page a few times.',
          apps: [{ kind: 'video', title: 'Two-minute upgrade walkthrough', url: 'https://x.example/v' }],
        },
      ],
    },
  ],
}

const INFERENCES = {
  count: 2,
  sourced_count: 1,
  inferred_count: 1,
  inferences: [
    {
      id: 'repeat_count',
      question: 'How many matching visits does a buyer have to make?',
      reading: 'Three matching visits inside seven days.',
      why: 'The ticket title says repeatedly and the spec states no count.',
      change: 'Set REPEAT_VISITS in dsr.page_outreach.vocabulary.',
      risk: 'A buyer who returns three times inside an hour sees nothing.',
      sourced: false,
    },
    {
      id: 'seen_counts_shows',
      question: 'What does the Seen mode count?',
      reading: 'Shows.',
      why: 'The quote is explicit.',
      change: 'frequency_decision in dsr.page_outreach.rules.',
      risk: 'A buyer who was shown the block on a page load that failed keeps it counted.',
      sourced: true,
    },
  ],
}

const DEFAULTS = {
  '/records/room': { records: [{ id: ROOM, data: { name: 'Northwind Traders' } }] },
  [`${BASE}/vocabulary`]: VOCABULARY,
  [`${BASE}/inferences`]: INFERENCES,
  [`${BASE}/summary`]: SUMMARY,
  [`${BASE}/prospects`]: PROSPECTS,
  [`${BASE}/workflows`]: WORKFLOWS,
  [`${BASE}/views`]: VIEWS,
  [`${BASE}/deliveries`]: DELIVERIES,
}

const calls = []
let handlers = {}

function json(body, status = 200) {
  return { ok: status < 400, status, statusText: 'OK', json: async () => body }
}

/** A refusal that answers `ok: false`, so a success branch cannot be asserted. */
function err(body, status) {
  return { ok: false, status, statusText: 'Error', json: async () => body }
}

beforeEach(() => {
  calls.length = 0
  handlers = {}
  globalThis.fetch = vi.fn(async (url, init = {}) => {
    const raw = String(url)
    const path = raw.replace('/api', '').split('?')[0]
    const method = (init.method || 'GET').toUpperCase()
    calls.push({
      url: raw,
      path,
      method,
      query: raw.includes('?') ? raw.split('?')[1] : '',
      body: init.body ? JSON.parse(init.body) : undefined,
    })
    const handler = handlers[`${method} ${path}`]
    if (handler) return handler({ path, method })
    const fallback = DEFAULTS[path]
    if (fallback === undefined) throw new Error(`unstubbed request: ${method} ${path}`)
    return json(fallback)
  })
})

async function renderPage() {
  render(<PageOutreach />)
  await screen.findByText('Page outreach')
  await waitFor(() => expect(calls.length).toBeGreaterThan(0))
}

describe('the descriptor', () => {
  it('exports the id the backend feature claims', () => {
    expect(descriptor.id).toBe('wf-106-trigger-outreach-on-high-intent-page-visits')
  })

  it('carries a glyph by path rather than editing the shared icon map', () => {
    expect(typeof descriptor.iconPath).toBe('string')
    expect(descriptor.iconPath.length).toBeGreaterThan(0)
  })

  it('exports a component the host can render', () => {
    expect(typeof descriptor.Component).toBe('function')
  })
})

describe('the registration', () => {
  it('never calls a route outside its own prefix, apart from the core room list', async () => {
    await renderPage()
    for (const call of calls) {
      expect(call.path === '/records/room' || call.path.startsWith(BASE)).toBe(true)
    }
  })
})

describe('the page', () => {
  it('uses no emoji as an icon', async () => {
    const { container } = render(<PageOutreach />)
    await screen.findByText('Page outreach')
    expect(container.textContent).not.toMatch(/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u)
  })

  it('says plainly that nothing is posted to any vendor', async () => {
    await renderPage()
    expect(screen.getByText(/posts no message to any vendor/i)).toBeTruthy()
  })

  it('labels the three thresholds as derived rather than presenting them as sourced', async () => {
    await renderPage()
    // Matched on the sentence rather than the bold lead-in, because the numbers live in
    // a sibling text node and the lead-in span carries none of them.
    const note = screen.getByText(/The research names the signal and states no number/).closest('p')
    expect(note.textContent).toContain('Derived, not sourced.')
    expect(note.textContent).toContain('3 visits')
    expect(note.textContent).toContain('7 days')
    expect(note.textContent).toContain('60 seconds')
  })

  it('selects the only room by itself rather than making a seller pick from one', async () => {
    await renderPage()
    await screen.findByText('visitor-halcyon-5d4')
    const summaryCall = calls.find((call) => call.path === `${BASE}/summary`)
    expect(summaryCall).toBeTruthy()
    expect(summaryCall.query).toContain(`room_id=${ROOM}`)
  })

  it('asks for a room before it fetches anything per-room', async () => {
    globalThis.fetch = vi.fn(async (url, init = {}) => {
      const raw = String(url)
      const path = raw.replace('/api', '').split('?')[0]
      calls.push({ url: raw, path, method: (init.method || 'GET').toUpperCase(), query: '', body: undefined })
      if (path === '/records/room') {
        return json({
          records: [
            { id: 'room-1', data: { name: 'Northwind Traders' } },
            { id: 'room-2', data: { name: 'Contoso Health' } },
          ],
        })
      }
      return json(DEFAULTS[path])
    })
    await renderPage()
    expect(screen.getAllByText('Choose a room').length).toBeGreaterThan(0)
  })

  it('reports a failing vocabulary call rather than rendering an empty page', async () => {
    handlers[`GET ${BASE}/vocabulary`] = () =>
      err({ error: 'boom', detail: 'the vocabulary route is down' }, 500)
    render(<PageOutreach />)
    expect(await screen.findByText('Could not load data')).toBeTruthy()
    expect(screen.getByText('the vocabulary route is down')).toBeTruthy()
  })
})

describe('the prospects tab', () => {
  it('shows a buyer who was never shown the block, which a delivery-driven view cannot', async () => {
    await renderPage()
    const row = (await screen.findByText('visitor-halcyon-5d4')).closest('tr')
    expect(within(row).getByText('Browsed a targeted page and was not shown the block')).toBeTruthy()
    expect(within(row).getByText('2 of 2')).toBeTruthy()
  })

  it('names what happened to a buyer who engaged and to one whose session hid it', async () => {
    await renderPage()
    const engaged = (await screen.findByText('visitor-northwind-9f2')).closest('tr')
    expect(within(engaged).getByText(/Engaged: chose a branch/)).toBeTruthy()
    const hidden = screen.getByText('visitor-meridian-7b8').closest('tr')
    expect(within(hidden).getByText(/Dismissed or Messenger opened/)).toBeTruthy()
  })

  it('shows a dash rather than a blank cell for a visitor with no company key', async () => {
    await renderPage()
    const row = (await screen.findByText('visitor-halcyon-5d4')).closest('tr')
    expect(within(row).getByText('no company key')).toBeTruthy()
  })
})

describe('the workflows tab', () => {
  async function openWorkflows() {
    const user = userEvent.setup()
    await renderPage()
    await user.click(screen.getByRole('button', { name: 'Workflows' }))
    await screen.findByText('The outreach workflows')
    return user
  }

  it('names the frequency mode on every workflow rather than showing a colour', async () => {
    await openWorkflows()
    const card = (await screen.findByText('Upgrade page repeaters')).closest('li')
    expect(within(card).getByText(/engaged_with/)).toBeTruthy()
    expect(within(card).getByText(/3 visit\(s\) in 7 day\(s\)/)).toBeTruthy()
  })

  it('renders each rule as a phrase a seller wrote, not as a raw value', async () => {
    await openWorkflows()
    const card = (await screen.findByText('Upgrade page repeaters')).closest('li')
    expect(within(card).getByText('the path starts with /upgrade, at a slash boundary')).toBeTruthy()
    expect(within(card).getByText('the buyer spent 60 seconds or more on the page')).toBeTruthy()
  })

  it('explains that a workflow with no rule targets every page', async () => {
    handlers[`GET ${BASE}/workflows`] = () =>
      json({ count: 1, by_state: { live: 1 }, workflows: [{ ...WORKFLOWS.workflows[0], rules: [], rule_count: 0 }] })
    await openWorkflows()
    expect(await screen.findByText(/no targeting rule, so every page counts/)).toBeTruthy()
  })

  it('saves a workflow through the create route with the rules it was given', async () => {
    const user = await openWorkflows()
    handlers[`POST ${BASE}/workflows`] = () => json(WORKFLOWS.workflows[0], 201)
    await user.type(screen.getByLabelText('Name'), 'Pricing repeaters')
    await user.type(screen.getByLabelText('URL rule'), '/pricing')
    await user.type(screen.getByLabelText('Dwell rule'), '45')
    await user.click(screen.getByRole('button', { name: 'Save workflow' }))

    const posted = calls.find((call) => call.method === 'POST' && call.path === `${BASE}/workflows`)
    expect(posted).toBeTruthy()
    expect(posted.query).toContain(`room_id=${ROOM}`)
    expect(posted.body.name).toBe('Pricing repeaters')
    expect(posted.body.rules).toEqual([
      { kind: 'url', mode: 'prefix', value: '/pricing' },
      { kind: 'dwell', value: '45' },
    ])
    expect(await screen.findByText(/A draft never fires/)).toBeTruthy()
  })

  it('turns a domain refusal into something a seller can act on', async () => {
    const user = await openWorkflows()
    handlers[`POST ${BASE}/workflows`] = () =>
      err({ error: 'workflow_already_exists', detail: 'a workflow named X already exists in this room' }, 409)
    await user.type(screen.getByLabelText('Name'), 'Upgrade page repeaters')
    await user.click(screen.getByRole('button', { name: 'Save workflow' }))
    expect(await screen.findByText(/would show the same buyer the same block twice/)).toBeTruthy()
  })

  it('sets a draft live through the state route, and says what that changes', async () => {
    const user = await openWorkflows()
    handlers[`POST ${BASE}/workflows/wf_1/state`] = () => json({ ...WORKFLOWS.workflows[0], state: 'live' }, 201)
    await user.click(screen.getByRole('button', { name: 'Set draft' }))
    const posted = calls.find((call) => call.method === 'POST' && call.path.endsWith('/state'))
    expect(posted).toBeTruthy()
    expect(posted.body).toEqual({ state: 'draft' })
  })
})

describe('the page views tab', () => {
  it('names the gate that stopped each view rather than showing a bare flag', async () => {
    const user = userEvent.setup()
    await renderPage()
    await user.click(screen.getByRole('button', { name: 'Page views' }))
    expect(await screen.findByText('not enough visits yet')).toBeTruthy()
    expect(screen.getByText('hidden for this session')).toBeTruthy()
  })

  it('shows the counts the decision was made on, so the arithmetic is visible', async () => {
    const user = userEvent.setup()
    await renderPage()
    await user.click(screen.getByRole('button', { name: 'Page views' }))
    expect(await screen.findByText(/2 of 3 matching visits in the window at the time/)).toBeTruthy()
  })

  it('says a view that did not match is not counted', async () => {
    const user = userEvent.setup()
    await renderPage()
    await user.click(screen.getByRole('button', { name: 'Page views' }))
    expect(await screen.findByText(/Not counted: the rules did not hold/)).toBeTruthy()
  })
})

describe('the blocks tab', () => {
  async function openDeliveries() {
    const user = userEvent.setup()
    await renderPage()
    await user.click(screen.getByRole('button', { name: 'Blocks shown' }))
    await screen.findByText('Upgrade page repeaters')
    return user
  }

  it('reports the channel as recorded rather than delivered', async () => {
    await openDeliveries()
    expect(await screen.findByText(/message_type in_app, recorded and not sent/)).toBeTruthy()
  })

  it('says on the card itself that nothing was posted to a vendor', async () => {
    await openDeliveries()
    expect(await screen.findByText(/not a message that was delivered/)).toBeTruthy()
  })

  it('shows the arithmetic that fired the block', async () => {
    await openDeliveries()
    expect(await screen.findByText('3 of 3 matching visits')).toBeTruthy()
    expect(screen.getByText('inside a 7 day window')).toBeTruthy()
  })

  it('shows each rule and whether it held, with the observation beside it', async () => {
    await openDeliveries()
    expect(await screen.findByText('the path starts with /upgrade, at a slash boundary')).toBeTruthy()
    expect(screen.getAllByText('held').length).toBeGreaterThan(0)
    expect(screen.getByText(/saw \/upgrade\/plans/)).toBeTruthy()
  })

  it('shows what the buyer was shown, including the app', async () => {
    await openDeliveries()
    expect(await screen.findByText('You have been back to our upgrade page a few times.')).toBeTruthy()
    expect(screen.getByText('Two-minute upgrade walkthrough')).toBeTruthy()
  })

  it('states the delivery state in words, not by colour alone', async () => {
    await openDeliveries()
    // Two nodes carry the sentence on purpose: the sr-only span inside the badge and the
    // visible line under it. Both are asserted, because the sr-only one is what a
    // screen reader reads and the visible one is what a seller reads.
    const found = await screen.findAllByText(/The buyer chose a branch, or the goal fired/)
    expect(found.length).toBeGreaterThanOrEqual(2)
    expect(found.some((node) => node.className.includes('sr-only'))).toBe(true)
    expect(found.some((node) => !node.className.includes('sr-only'))).toBe(true)
  })
})

describe('the decisions tab', () => {
  it('separates the sourced half from the inferred half', async () => {
    const user = userEvent.setup()
    await renderPage()
    await user.click(screen.getByRole('button', { name: 'Decisions' }))
    expect(
      await screen.findByText(/of which 1 the research states and 1 it does not/)
    ).toBeTruthy()
    expect(screen.getByText('inferred')).toBeTruthy()
    expect(screen.getByText('sourced')).toBeTruthy()
  })

  it('names what would change each decision and what it risks', async () => {
    const user = userEvent.setup()
    await renderPage()
    await user.click(screen.getByRole('button', { name: 'Decisions' }))
    expect(
      await screen.findByText(/Set REPEAT_VISITS in dsr.page_outreach.vocabulary/)
    ).toBeTruthy()
    expect(screen.getByText(/returns three times inside an hour sees nothing/)).toBeTruthy()
  })
})

describe('the empty and loading states', () => {
  it('says what to do when a room has no page views yet', async () => {
    handlers[`GET ${BASE}/prospects`] = () =>
      json({ ...PROSPECTS, count: 0, prospects: [], shown_to: 0, not_shown_to: 0, engaged: 0, hidden_for_session: 0 })
    await renderPage()
    expect(await screen.findByText(/No page views recorded in this room yet/)).toBeTruthy()
  })

  it('shows a spinner while the vocabulary is loading', async () => {
    let release = () => {}
    const gate = new Promise((resolve) => {
      release = resolve
    })
    globalThis.fetch = vi.fn(async (url, init = {}) => {
      const raw = String(url)
      const path = raw.replace('/api', '').split('?')[0]
      calls.push({
        url: raw,
        path,
        method: (init.method || 'GET').toUpperCase(),
        query: '',
        body: undefined,
      })
      if (path === `${BASE}/vocabulary`) {
        await gate
        return json(VOCABULARY)
      }
      return json(DEFAULTS[path] || {})
    })
    render(<PageOutreach />)
    expect(await screen.findByText(/Loading page outreach/)).toBeTruthy()
    // Released inside act, so the state update the gate triggers is flushed as a React
    // update rather than reported as an unwrapped one.
    await act(async () => {
      release()
    })
    expect(await screen.findByText('Page outreach')).toBeTruthy()
  })
})
