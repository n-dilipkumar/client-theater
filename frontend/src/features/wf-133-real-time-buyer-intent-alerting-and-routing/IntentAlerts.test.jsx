/**
 * The intent-alerting page (WF-133).
 *
 * `apiRequest` is never mocked. `globalThis.fetch` is replaced instead, keyed by
 * method and path, which is what the other feature tests in this product do and what
 * keeps the client under test on the same path a browser would take.
 *
 * What this file is really for is the three claims the page makes that a build
 * cannot check:
 *
 * 1. It registers. A branch once shipped a feature folder with no `index.jsx` and
 *    the build stayed green, because the host globs for that file and a folder
 *    without one is invisible rather than broken.
 * 2. It calls nothing outside its own prefix. A page that reaches into another
 *    feature's routes is exactly the coupling the feature contract forbids.
 * 3. It never implies a message was sent. This product has no outbound transport,
 *    and a page that renders an alert without its dispatch state would tell a rep
 *    something the codebase cannot support.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import IntentAlerts from './IntentAlerts'
import descriptor from './index.jsx'

const BASE = '/wf-133'
const ROOM = 'room-1'

const VOCABULARY = {
  ticket: 'WF-133',
  thresholds: [
    { kind: 'dwell', label: 'Time on one page', unit: 'seconds', threshold: 90, sourced: true },
    { kind: 'pages', label: 'Distinct pages', unit: 'pages', threshold: 4, sourced: false },
  ],
  dispatch_states: ['queued', 'held_for_integration', 'skipped', 'suppressed'],
  alert_context_fields: ['pages', 'dwell', 'stakeholder'],
  sends_mail: false,
  sends_slack: false,
  primary_channel: 'email',
}

const INFERENCES = {
  count: 1,
  inferences: [
    {
      id: 'suppression_window',
      question: 'For how long is a contacted account suppressed?',
      reading: 'Twenty-four hours, per account, per room, per recipient.',
      change: 'Set SUPPRESSION_HOURS in the vocabulary.',
      risk: 'It can swallow a genuine second wave of interest.',
      sourced: false,
    },
  ],
}

const SUMMARY = {
  room_id: ROOM,
  watchlists: 1,
  watched_accounts: 2,
  alerts: 1,
  alert_states: { open: 1 },
  tasks: 1,
  actions: 0,
}

const ALERT = {
  id: 'al_1',
  signal_id: 'sig_1',
  company_key: 'northwind-energy',
  accountable: '005-dana',
  recipients: [{ who: '005-dana', address: '', deliverable: false }],
  consulted: ['Opportunity owner'],
  dispatches: [
    {
      channel: 'email',
      state: 'queued',
      to: 'dana.kelly@acme.example',
      reason: '',
    },
    {
      channel: 'slack',
      state: 'held_for_integration',
      to: 'dana.kelly@acme.example',
      reason: 'This product has no Slack surface.',
    },
  ],
  channel_states: { email: 'queued', slack: 'held_for_integration' },
  suppressed: false,
  payload: {
    pages: { visited: ['/overview', '/security', '/pricing', '/case-studies'], count: 4 },
    dwell: { longest_page_seconds: 142, window_total_seconds: 400, threshold_seconds: 90 },
    stakeholder: { name: 'Dana Okafor', role: 'VP Procurement', identified: 'true' },
    triggered_by: [
      { kind: 'dwell', label: 'Time on one page', observed: 142, threshold: 90, unit: 'seconds' },
    ],
  },
  subject: 'Dana Okafor read 4 page(s) in the Northwind room, 142s on the longest',
  body: 'Which pages\n  /overview',
  state: 'open',
  opportunity_id: 'wf133-deal-northwind',
  dispatched_at: '2026-10-04T09:00:00+00:00',
}

const ALERTS = { count: 1, by_state: { open: 1 }, by_channel: { 'email:queued': 1 }, alerts: [ALERT] }
const ENGAGEMENT = {
  count: 2,
  engaged: 1,
  not_engaged: 1,
  reads: 'Engaged means the account has an alert that no rep has dismissed.',
  accounts: [
    {
      company_key: 'northwind-energy',
      company_name: 'Northwind Energy',
      engagement: 'engaged',
      engaged: true,
      pages: ['/pricing'],
      last_seen_at: '2026-10-04T07:00:00+00:00',
      accountable: '005-dana',
    },
    {
      company_key: 'meridian-foods',
      company_name: 'Meridian Foods',
      engagement: 'not_engaged',
      engaged: false,
      pages: [],
      last_seen_at: '',
      accountable: '',
    },
  ],
}

const WATCHLISTS = {
  count: 1,
  watched_accounts: 2,
  watchlists: [
    {
      id: 'wl_1',
      name: 'Strategic accounts',
      tier: 'strategic',
      accounts: ['northwind-energy', 'meridian-foods'],
      account_count: 2,
      notify: ['dana.kelly@acme.example'],
      note: 'Reviewed every Monday.',
    },
  ],
}

const RULES = {
  count: 1,
  rules: [
    {
      id: 'rl_1',
      name: 'Enterprise territory',
      kind: 'team',
      match: { team: 'enterprise-uk' },
      notify: ['dana.kelly@acme.example'],
      position: 1,
      stop: false,
      note: 'Adds to the owner rather than replacing them.',
    },
  ],
}

const DEFAULTS = {
  '/records/room': { records: [{ id: ROOM, data: { name: 'Northwind Traders' } }] },
  [`${BASE}/vocabulary`]: VOCABULARY,
  [`${BASE}/inferences`]: INFERENCES,
  [`${BASE}/summary`]: SUMMARY,
  [`${BASE}/alerts`]: ALERTS,
  [`${BASE}/engagement`]: ENGAGEMENT,
  [`${BASE}/watchlists`]: WATCHLISTS,
  [`${BASE}/rules`]: RULES,
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
  render(<IntentAlerts />)
  await screen.findByText('Intent alerts')
  await waitFor(() => expect(calls.length).toBeGreaterThan(0))
}

describe('the descriptor', () => {
  it('exports the id the backend feature claims', () => {
    expect(descriptor.id).toBe('wf-133-real-time-buyer-intent-alerting-and-routing')
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
    const { container } = render(<IntentAlerts />)
    await screen.findByText('Intent alerts')
    expect(container.textContent).not.toMatch(/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u)
  })

  it('says plainly that nothing is sent, rather than leaving it to be inferred', async () => {
    await renderPage()
    expect(screen.getByText(/does not send mail or a Slack message/i)).toBeTruthy()
  })

  it('selects the only room by itself rather than making a seller pick from one', async () => {
    await renderPage()
    await screen.findByText(ALERT.subject)
    const summaryCall = calls.find((call) => call.path === `${BASE}/summary`)
    expect(summaryCall).toBeTruthy()
    expect(summaryCall.query).toContain(`room_id=${ROOM}`)
  })

  it('asks for a room before it fetches anything per-room', async () => {
    globalThis.fetch = vi.fn(async (url, init = {}) => {
      const raw = String(url)
      const path = raw.replace('/api', '').split('?')[0]
      const method = (init.method || 'GET').toUpperCase()
      calls.push({ url: raw, path, method, query: '', body: undefined })
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
    // "Choose a room" is both the empty select option and the empty-state title, so
    // the count is asserted rather than reaching for one node.
    expect(screen.getAllByText('Choose a room').length).toBeGreaterThan(0)
    expect(calls.filter((call) => call.path === `${BASE}/summary`)).toHaveLength(0)
    expect(calls.filter((call) => call.path === `${BASE}/alerts`)).toHaveLength(0)
  })

  it('scopes every read to the room it was given', async () => {
    const user = userEvent.setup()
    await renderPage()
    await user.selectOptions(screen.getByLabelText('Room'), ROOM)
    await screen.findByText(ALERT.subject)
    for (const path of ['/summary', '/alerts', '/engagement', '/watchlists', '/rules']) {
      const call = calls.find((entry) => entry.path === `${BASE}${path}`)
      expect(call, path).toBeTruthy()
      expect(call.query, path).toContain(`room_id=${ROOM}`)
    }
  })
})

describe('the alert and the three researched facts', () => {
  async function openAlerts() {
    const user = userEvent.setup()
    await renderPage()
    await user.selectOptions(screen.getByLabelText('Room'), ROOM)
    await screen.findByText(ALERT.subject)
    return user
  }

  it('shows which pages, how long and which stakeholder', async () => {
    await openAlerts()
    expect(screen.getByText('Which pages')).toBeTruthy()
    expect(screen.getByText('/case-studies')).toBeTruthy()
    expect(screen.getByText('How long')).toBeTruthy()
    expect(screen.getByText('Which stakeholder')).toBeTruthy()
    expect(screen.getByText('Dana Okafor')).toBeTruthy()
    expect(screen.getByText('VP Procurement')).toBeTruthy()
    // Both dwell numbers are on the page, because the sourced threshold is the
    // longest read while the rep's question is usually the window total. Both stay
    // in seconds near the threshold, so "142s" reads against the sourced "90s".
    expect(screen.getByText('142s on the longest page, 6m 40s in the window')).toBeTruthy()
    expect(screen.getByText('sourced threshold is 90s')).toBeTruthy()
  })

  it('shows the threshold arithmetic rather than asserting that something fired', async () => {
    await openAlerts()
    expect(screen.getByText(/142 seconds, threshold 90/)).toBeTruthy()
  })

  it('renders every channel state with its meaning in words, never colour alone', async () => {
    await openAlerts()
    // Each state is stated twice on purpose: once for a screen reader beside the
    // badge, and once visibly beside the channel. So the count is asserted, not
    // the singular.
    expect(screen.getAllByText('A message exists in the outbox to send').length).toBeGreaterThan(0)
    expect(screen.getAllByText(/this build has no surface for it/i).length).toBeGreaterThan(0)
  })

  it('never renders a state that claims a message was sent', async () => {
    await openAlerts()
    const labels = screen.getAllByText(/^(queued|held for integration|skipped|suppressed)$/i)
    expect(labels.length).toBeGreaterThan(0)
    expect(screen.queryByText(/^sent$/i)).toBeNull()
  })

  it('says the stakeholder has no address rather than implying one exists', async () => {
    await openAlerts()
    expect(screen.getAllByText(/no address on file, by design/i).length).toBeGreaterThan(0)
  })

  it('records a rep action against the signal', async () => {
    handlers[`POST ${BASE}/signals/sig_1/actions`] = () =>
      json({ state: 'acknowledged', action: { kind: 'acknowledged' }, alert: { state: 'acknowledged' } }, 201)
    const user = await openAlerts()
    await user.type(screen.getByLabelText('Note'), 'Calling her today.')
    await user.click(screen.getByRole('button', { name: 'Acknowledge' }))
    await waitFor(() =>
      expect(calls.some((call) => call.path === `${BASE}/signals/sig_1/actions`)).toBe(true)
    )
    const sent = calls.find((call) => call.path === `${BASE}/signals/sig_1/actions`)
    expect(sent.body).toMatchObject({ kind: 'acknowledged', note: 'Calling her today.' })
  })

  it('refuses a dismissal with no note and says why in plain words', async () => {
    handlers[`POST ${BASE}/signals/sig_1/actions`] = () =>
      err({ error: 'invalid_signal_action', detail: 'a dismissed needs a note' }, 422)
    const user = await openAlerts()
    await user.click(screen.getByRole('button', { name: 'Dismiss' }))
    expect(await screen.findByText(/needs a note/i)).toBeTruthy()
  })

  it('refuses a Slack handle as a recipient and explains the limit', async () => {
    handlers[`POST ${BASE}/rules`] = () =>
      err({ error: 'invalid_routing_rule', detail: "'@dana' is not an email address" }, 422)
    const user = userEvent.setup()
    await renderPage()
    await user.selectOptions(screen.getByLabelText('Room'), ROOM)
    await user.click(screen.getByRole('button', { name: 'Routing' }))
    await user.type(screen.getByLabelText('Name'), 'Enterprise')
    await user.click(screen.getByRole('button', { name: 'Save rule' }))
    expect(await screen.findByText(/has no Slack surface, so a handle has nowhere to go/i)).toBeTruthy()
  })
})

describe('who is engaged and who is not', () => {
  it('shows a watched account that has never alerted', async () => {
    const user = userEvent.setup()
    await renderPage()
    await user.selectOptions(screen.getByLabelText('Room'), ROOM)
    await user.click(screen.getByRole('button', { name: 'Engagement' }))
    expect(await screen.findByText('Meridian Foods')).toBeTruthy()
    expect(screen.getByText('1 engaged, 1 not engaged')).toBeTruthy()
    expect(screen.getByText(/Engaged means the account has an alert/)).toBeTruthy()
  })
})

describe('the decision record', () => {
  it('serves every recorded judgement with its change and its risk', async () => {
    const user = userEvent.setup()
    await renderPage()
    await user.click(screen.getByRole('button', { name: 'Decisions' }))
    expect(await screen.findByText('suppression_window')).toBeTruthy()
    expect(screen.getByText(/Twenty-four hours, per account/)).toBeTruthy()
    expect(screen.getByText(/Set SUPPRESSION_HOURS/)).toBeTruthy()
    expect(screen.getByText('inferred')).toBeTruthy()
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
    const user = userEvent.setup()
    await renderPage()
    await user.click(screen.getByRole('button', { name: 'Watchlists' }))
    expect(await screen.findByLabelText('Name')).toBeTruthy()
    expect(screen.getByLabelText('Tier')).toBeTruthy()
    expect(screen.getByLabelText('Company keys')).toBeTruthy()
    expect(screen.getByLabelText('Notify')).toBeTruthy()
  })

  it('marks the current section rather than only colouring it', async () => {
    await renderPage()
    const alerts = screen.getByRole('button', { name: 'Alerts' })
    expect(alerts.getAttribute('aria-current')).toBe('page')
  })
})

describe('empty and error states', () => {
  it('says so when a room has no alerts rather than rendering a blank panel', async () => {
    handlers[`GET ${BASE}/alerts`] = () => json({ count: 0, by_state: {}, by_channel: {}, alerts: [] })
    const user = userEvent.setup()
    await renderPage()
    await user.selectOptions(screen.getByLabelText('Room'), ROOM)
    expect(await screen.findByText('No alerts in this room yet')).toBeTruthy()
  })

  it('offers a retry when a read fails', async () => {
    handlers[`GET ${BASE}/summary`] = () => err({ detail: 'boom' }, 500)
    const user = userEvent.setup()
    await renderPage()
    await user.selectOptions(screen.getByLabelText('Room'), ROOM)
    expect(await screen.findByText('Could not load data')).toBeTruthy()
    expect(screen.getByRole('button', { name: /retry/i })).toBeTruthy()
  })

  it('tells a seller with no rooms what to do first', async () => {
    globalThis.fetch = vi.fn(async (url) => {
      const path = String(url).replace('/api', '').split('?')[0]
      if (path === '/records/room') return json({ records: [] })
      return json(DEFAULTS[path])
    })
    render(<IntentAlerts />)
    expect(await screen.findByText('No rooms yet')).toBeTruthy()
  })
})
