/**
 * The Slack-notification page (WF-112).
 *
 * `apiRequest` is never mocked. `globalThis.fetch` is replaced instead, keyed by
 * method and path, which is what the other feature tests in this product do and what
 * keeps the client under test on the same path a browser would take.
 *
 * What this file is really for is the three claims the page makes that a build cannot
 * check:
 *
 * 1. It registers. A branch once shipped a feature folder with no `index.jsx` and the
 *    build stayed green, because the host globs for that file and a folder without one
 *    is invisible rather than broken.
 * 2. It calls nothing outside its own prefix. A page that reaches into another
 *    feature's routes is exactly the coupling the feature contract forbids.
 * 3. It never implies a message was sent. This product has no Slack transport, and a
 *    page that renders a decision without saying so would tell a seller something the
 *    codebase cannot support.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import SlackSignalNotify from './SlackSignalNotify'
import descriptor from './index.jsx'

const BASE = '/wf-112'
const ROOM = 'room-1'

const VOCABULARY = {
  required_scope: 'bot',
  refusal_codes: ['missing_bot_scope', 'channel_required', 'bot_not_in_channel', 'rate_limited'],
  researched_refusals: ['missing_bot_scope', 'channel_required', 'bot_not_in_channel'],
  dispatch_states: ['recorded', 'refused'],
  sends_slack: false,
  delivery: 'not_sent',
  attribution_excluded: true,
}

const SUMMARY = {
  room_id: ROOM,
  connected: true,
  workspace: 'acme',
  channel: 'sales-signals',
  automations: 1,
  live_automations: 1,
  dispatches: 2,
  dispatch_states: { recorded: 1, refused: 1 },
  refusals: { bot_not_in_channel: 1 },
  sends_slack: false,
}

const CONNECTION = {
  room_id: ROOM,
  connected: true,
  connection: {
    id: 'conn_1',
    workspace: 'acme',
    channel: 'sales-signals',
    channel_kind: 'public',
    scopes: ['bot', 'chat:write'],
    bot_in_channel: true,
    state: 'connected',
  },
}

const AUTOMATIONS = {
  count: 1,
  live: 1,
  automations: [
    {
      id: 'blk_1',
      name: 'High-intent buyer signal',
      state: 'live',
      trigger: 'signal',
      block_kind: 'slack_message',
      channel: 'sales-signals',
      message: 'New signal for {{customer.username}}.',
    },
  ],
}

const DISPATCHES = {
  count: 2,
  by_state: { recorded: 1, refused: 1 },
  by_refusal: { bot_not_in_channel: 1 },
  dispatches: [
    {
      id: 'disp_1',
      kind: 'live',
      state: 'recorded',
      channel: 'sales-signals',
      message: { text: 'New signal for Dana.' },
      signal: { type: 'pricing_page_view' },
      delivery: 'not_sent',
    },
    {
      id: 'disp_2',
      kind: 'live',
      state: 'refused',
      refusal: 'bot_not_in_channel',
      reason: 'the bot is not in the private channel deal-desk',
      channel: 'deal-desk',
      retry_after_seconds: 0,
    },
  ],
}

const DEFAULTS = {
  '/records/room': { records: [{ id: ROOM, data: { name: 'Northwind Traders' } }] },
  [`${BASE}/vocabulary`]: VOCABULARY,
  [`${BASE}/summary`]: SUMMARY,
  [`${BASE}/connection`]: CONNECTION,
  [`${BASE}/automations`]: AUTOMATIONS,
  [`${BASE}/dispatches`]: DISPATCHES,
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
    calls.push({ url: raw, path, method })
    const handler = handlers[`${method} ${path}`]
    if (handler) return handler()
    const fallback = DEFAULTS[path]
    if (fallback === undefined) throw new Error(`unstubbed request: ${method} ${path}`)
    return json(fallback)
  })
})

async function renderPage() {
  render(<SlackSignalNotify />)
  await screen.findByRole('heading', { name: 'Slack signals' })
  await screen.findByText('Northwind Traders')
  await waitFor(() => expect(calls.some((call) => call.path === `${BASE}/summary`)).toBe(true))
}

describe('the descriptor', () => {
  it('exports the id the backend feature claims', () => {
    expect(descriptor.id).toBe('wf-112-slack-signal-notify')
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
    const { container } = render(<SlackSignalNotify />)
    await screen.findByRole('heading', { name: 'Slack signals' })
    expect(container.textContent).not.toMatch(/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u)
  })

  it('says plainly that nothing is sent, rather than leaving it to be inferred', async () => {
    await renderPage()
    expect(screen.getByText(/does not send a Slack message/i)).toBeTruthy()
  })

  it('shows the connected workspace and channel', async () => {
    await renderPage()
    expect((await screen.findAllByText('acme')).length).toBeGreaterThan(0)
    expect(screen.getAllByText('#sales-signals').length).toBeGreaterThan(0)
  })

  it('renders a recorded decision as what would be posted, not as sent', async () => {
    await renderPage()
    expect(await screen.findByText('New signal for Dana.')).toBeTruthy()
    expect(screen.getByText(/would post to #sales-signals/)).toBeTruthy()
  })

  it('renders a refused decision with the fix a seller can apply', async () => {
    await renderPage()
    expect(await screen.findByText(/Invite it with \/invite/)).toBeTruthy()
  })

  it('records a test send and reports the decision', async () => {
    handlers[`POST ${BASE}/automations/blk_1/test`] = () =>
      json({
        id: 'disp_test',
        kind: 'test',
        state: 'recorded',
        channel: 'sales-signals',
        message: { text: 'New signal for Dana.' },
      })
    await renderPage()
    await userEvent.type(screen.getByLabelText(/Test buyer name/i), 'Dana')
    await userEvent.click(screen.getByRole('button', { name: /Send test/i }))
    expect(
      await screen.findByText(/Recorded: this message would post to #sales-signals/i),
    ).toBeTruthy()
    const posted = calls.find((call) => call.method === 'POST' && call.path.endsWith('/test'))
    expect(posted).toBeTruthy()
  })

  it('reports a refusal from the send-test form with the fix, not a bare status', async () => {
    handlers[`POST ${BASE}/automations/blk_1/test`] = () =>
      json({ error: 'channel_required', detail: 'a Slack connection needs a channel' }, 422)
    await renderPage()
    await userEvent.click(screen.getByRole('button', { name: /Send test/i }))
    expect(await screen.findByText(/connect time/)).toBeTruthy()
  })
})
