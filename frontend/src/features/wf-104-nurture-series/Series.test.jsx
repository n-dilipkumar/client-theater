/**
 * WF-104 page tests.
 *
 * `apiRequest` is never mocked. `globalThis.fetch` is replaced instead, keyed by path, which is
 * what the other feature tests in this product do and what keeps the client under test on the
 * same path a browser would take.
 *
 * Every state below is one the design floor requires or the research forces: loading, error and
 * empty, because a board that goes blank reads as "there is nothing here"; the two honest header
 * statements, because both are consequences of a decision rather than facts about a running
 * system; the four block kinds, because the research fixes that set; the audience preview count,
 * because that is the entry gate made visible; and a run's dispatch and fallback, because the
 * offline branch is the whole point of the workflow.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import Series from './Series'
import descriptor from './index.jsx'

const ROOM = 'room_a'
const PREFIX = '/wf-104'

const VOCABULARY = {
  block_kinds: ['rule', 'content', 'wait', 'tag'],
  block_labels: { rule: 'Rule block', content: 'Content block', wait: 'Wait block', tag: 'Tag block' },
  channels: ['in_app', 'email', 'mobile_push', 'sms'],
  channel_labels: { in_app: 'In-app post', email: 'Email', mobile_push: 'Mobile push', sms: 'SMS' },
  content_type_labels: { post: 'Post', email: 'Email' },
  run_states: ['running', 'waiting', 'finished', 'exited'],
  run_state_labels: { running: 'Running', waiting: 'Waiting', finished: 'Finished', exited: 'Left the path' },
  poll: { interval_seconds: 1800, quote: 'Series checks every 30 minutes whether a customer matches each rule, message, wait, or tag block.' },
}

const SERIES = {
  id: 'ser_1',
  name: 'Enterprise evaluation nurture',
  state: 'draft',
  runs: 1,
  blocks: [
    { id: 'who_enters', kind: 'rule', role: 'entry', filters: [{ field: 'plan', op: 'eq', value: 'enterprise' }], match: 'all' },
    {
      id: 'intro_post',
      kind: 'content',
      channel: 'in_app',
      content_type: 'post',
      goal: 'Get a reply',
      send_window_seconds: 2592000,
      fallback_enabled: true,
      fallback_channel: 'email',
    },
    { id: 'give_it_a_day', kind: 'wait', duration_seconds: 86400 },
    { id: 'engaged_tag', kind: 'tag', tag: 'nurtured' },
  ],
}

const RUN = {
  id: 'run_1',
  contact_id: 'buyer_online',
  state: 'waiting',
  cursor: 2,
  next_block: { kind: 'wait', id: 'give_it_a_day' },
  tags: [],
  waiting_kind: 'wait',
  resume_at: '2026-03-03T09:00:00Z',
  dispatches: [{ id: 'd1', channel: 'in_app', state: 'delivered', is_fallback: false }],
  engagement: [],
  activity: [{ id: 'a1', code: 'run_entered', at: '2026-03-02T09:00:00Z' }],
}

const DEFAULTS = {
  '/records/room': { records: [{ id: ROOM, data: { name: 'Northwind Traders' } }] },
  [`${PREFIX}/vocabulary`]: VOCABULARY,
  [`${PREFIX}/inferences`]: {
    ticket: 'WF-104',
    count: 1,
    decisions: [
      {
        id: 'audience-mechanism',
        question: 'How does the domain module obtain the contacts it filters?',
        chosen: 'The audience is caller-supplied.',
        rejected: 'A private audience collection.',
        consequence: 'Decoupled from the CRM.',
        jev_audit: 'jev-20261013T150622-7908-82240',
        confidence: 0.86,
      },
    ],
    jev_audits: { 'audience-mechanism': 'jev-20261013T150622-7908-82240' },
    unsourced: ['audience-mechanism'],
  },
  [`${PREFIX}/rooms/${ROOM}/summary`]: { series_by_state: { draft: 1, live: 0 }, runs: 1, due: 0, dispatches: 1, fallbacks: 0 },
  [`${PREFIX}/rooms/${ROOM}/series`]: { count: 1, series: [SERIES] },
  [`${PREFIX}/rooms/${ROOM}/runs`]: { count: 1, runs: [RUN] },
  [`${PREFIX}/rooms/${ROOM}/runs/run_1`]: RUN,
}

const calls = []
let handlers = {}
let failures = new Set()

function json(body, status = 200) {
  return { ok: status < 400, status, statusText: 'OK', json: async () => body }
}

beforeEach(() => {
  calls.length = 0
  handlers = {}
  failures = new Set()
  globalThis.fetch = vi.fn(async (url, init = {}) => {
    const raw = String(url)
    const path = raw.replace('/api', '').split('?')[0]
    const method = (init.method || 'GET').toUpperCase()
    calls.push({ url: raw, path, method, body: init.body ? JSON.parse(init.body) : undefined })
    if (failures.has(`${method} ${path}`)) {
      return json({ error: 'boom', detail: 'The service is unavailable.' }, 500)
    }
    const handler = handlers[`${method} ${path}`]
    if (handler) return handler({ path, method })
    const fallback = DEFAULTS[path]
    if (fallback === undefined) throw new Error(`unstubbed request: ${method} ${path}`)
    return json(fallback)
  })
})

async function renderBoard() {
  const user = userEvent.setup({ delay: null })
  render(<Series />)
  await screen.findByRole('heading', { name: /Nurture series/ })
  const picker = await screen.findByLabelText('Room')
  await user.selectOptions(picker, ROOM)
  await waitFor(() => expect(screen.queryByText('Pick a room')).toBeNull())
  return { user }
}

describe('the descriptor', () => {
  it('carries the id the backend feature exports, so the two halves are findable', () => {
    expect(descriptor.id).toBe('wf-104-nurture-series')
    expect(descriptor.label).toBe('Nurture series')
    expect(typeof descriptor.Component).toBe('function')
  })

  it('passes a glyph path rather than a name, because the shared PATHS map is not ours', () => {
    expect(typeof descriptor.iconPath).toBe('string')
    expect(descriptor.iconPath.length).toBeGreaterThan(10)
  })
})

describe('the loading state', () => {
  it('says what is loading rather than rendering a blank board', () => {
    render(<Series />)
    expect(screen.getByRole('status')).toHaveTextContent('Loading nurture series')
  })
})

describe('the room gate', () => {
  it('asks for a room before it asks for any series', async () => {
    render(<Series />)
    expect(await screen.findByText('Pick a room')).toBeTruthy()
    expect(screen.queryByText('No series yet')).toBeNull()
  })

  it('calls no room-scoped route until a room is chosen', async () => {
    render(<Series />)
    await screen.findByRole('heading', { name: /Nurture series/ })
    const scoped = calls.filter(
      (call) => call.path.startsWith(`${PREFIX}/rooms/`) || call.path.startsWith('/records/room')
    )
    expect(scoped.map((call) => call.path)).toEqual(['/records/room'])
  })
})

describe('the board', () => {
  it('renders the series, its blocks and the two honest statements', async () => {
    await renderBoard()
    expect(await screen.findByText('Enterprise evaluation nurture')).toBeTruthy()
    expect(screen.getByText('Rule block')).toBeTruthy()
    expect(screen.getByText('Content block')).toBeTruthy()
    expect(screen.getByText('Wait block')).toBeTruthy()
    expect(screen.getByText('Tag block')).toBeTruthy()
    expect(screen.getByText(/Series checks every 30 minutes/)).toBeTruthy()
    expect(screen.getByText(/Nothing leaves this product/)).toBeTruthy()
  })

  it('shows the send window and the fallback channel on the content block', async () => {
    await renderBoard()
    expect(await screen.findByText(/send window 30d, fallback Email/)).toBeTruthy()
  })

  it('sets a draft series live', async () => {
    const { user } = await renderBoard()
    handlers[`POST ${PREFIX}/rooms/${ROOM}/series/ser_1/go-live`] = () => json({ id: 'ser_1', state: 'live' })
    await user.click(await screen.findByRole('button', { name: /Set live/ }))
    await waitFor(() =>
      expect(calls.some((call) => call.method === 'POST' && call.path.endsWith('/go-live'))).toBe(true)
    )
  })

  it('creates a series with all four block kinds', async () => {
    const { user } = await renderBoard()
    handlers[`POST ${PREFIX}/rooms/${ROOM}/series`] = () => json({ id: 'ser_2', state: 'draft' }, 201)
    await user.click(await screen.findByRole('button', { name: /New series/ }))
    await waitFor(() => expect(calls.some((call) => call.method === 'POST' && call.path.endsWith('/series'))).toBe(true))
    const created = calls.find((call) => call.method === 'POST' && call.path.endsWith('/series'))
    expect(created.body.blocks.map((block) => block.kind)).toEqual(['rule', 'content', 'wait', 'rule', 'tag'])
  })

  it('runs the poll with an audience, because this feature stores no contacts', async () => {
    const { user } = await renderBoard()
    handlers[`POST ${PREFIX}/rooms/${ROOM}/evaluate`] = () => json({ fired_count: 1, skipped: [] })
    await user.click(await screen.findByRole('button', { name: /Run poll/ }))
    await waitFor(() => expect(calls.some((call) => call.path.endsWith('/evaluate'))).toBe(true))
    const poll = calls.find((call) => call.path.endsWith('/evaluate'))
    expect(Array.isArray(poll.body.audience)).toBe(true)
    expect(poll.body.audience.length).toBeGreaterThan(0)
  })

  it('shows the audience preview count and why a buyer is excluded', async () => {
    const { user } = await renderBoard()
    handlers[`POST ${PREFIX}/rooms/${ROOM}/series/ser_1/preview`] = () =>
      json({
        entered: 2,
        audience_size: 3,
        contacts: [
          { contact_id: 'buyer_online', entered: true, reasons: [] },
          { contact_id: 'buyer_starter', entered: false, reasons: ['plan eq starter'] },
        ],
      })
    await user.click(await screen.findByRole('button', { name: /Preview audience/ }))
    expect(await screen.findByText(/2 of 3 will enter/)).toBeTruthy()
    expect(screen.getByText(/excluded \(plan eq starter\)/)).toBeTruthy()
  })

  it('shows a run and its dispatch when one is selected', async () => {
    const { user } = await renderBoard()
    await user.click(await screen.findByRole('button', { name: /Show run/ }))
    expect((await screen.findAllByText('Dispatches')).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/delivered/).length).toBeGreaterThan(0)
    expect(screen.getByText('run_entered')).toBeTruthy()
  })
})

describe('the error state', () => {
  it('surfaces a failed vocabulary load rather than a blank page', async () => {
    failures.add(`GET ${PREFIX}/vocabulary`)
    render(<Series />)
    expect(await screen.findByText('Could not load data')).toBeTruthy()
  })
})
