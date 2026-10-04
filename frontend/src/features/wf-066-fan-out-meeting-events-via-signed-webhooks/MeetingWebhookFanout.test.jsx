import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import MeetingWebhookFanout from '@/features/wf-066-fan-out-meeting-events-via-signed-webhooks/MeetingWebhookFanout.jsx'
import descriptor from '@/features/wf-066-fan-out-meeting-events-via-signed-webhooks/index.jsx'
import {
  deliveryNote,
  signingRuleLine,
  subscriptionTone,
  subscriptionWord,
  urlIsAcceptable,
} from '@/features/wf-066-fan-out-meeting-events-via-signed-webhooks/api.js'
import { MachineValue, StatusBadge } from '@/features/wf-066-fan-out-meeting-events-via-signed-webhooks/primitives.jsx'

/**
 * Tests for the WF-066 meeting webhook page.
 *
 * Six things the backend tests cannot see, and each one is a way this page would
 * otherwise be quietly wrong:
 *
 * * **The page is discovered at all.** `lib/features.js` globs
 *   `src/features/*\/index.jsx`. A feature that ships its page and its primitives
 *   but no descriptor builds green and appears in the product zero times.
 * * **The signing rule renders as the exact string, not a summary.** The research
 *   records a re-serialised payload as the cause of a signature mismatch, so a
 *   page that paraphrases the rule sends a team to debug their own code.
 * * **"Reached nobody" and "reached them and they refused it" read differently.**
 *   Both would render as "failed", and the reader would not know whether to enable
 *   a row or to fix a subscriber.
 * * **No status is conveyed by colour alone.** Every state carries its own word.
 * * **The replay window is published as the consumer's.** A page that implied the
 *   sender enforced it would send someone looking for a control that does not exist.
 * * **The event-type picker offers exactly the three the server published**, each
 *   with the payload value it fires, so the page cannot drift from the validator.
 *
 * Stubs are keyed on this feature's own paths. An unstubbed path throws, so a test
 * cannot quietly pass against the wrong response.
 */

const ROOM = 'room_1'
const SUB = 'meeting_webhook_subscription_aaaa'
const EVENT = 'meeting_webhook_event_bbbb'

const VOCABULARY = {
  event_types: [
    { id: 'new_meeting', label: 'For New Meeting', payload_type: 'Created' },
    { id: 'meeting_update', label: 'For Meeting Update', payload_type: 'Updated' },
    { id: 'canceled_meeting', label: 'For Canceled Meeting', payload_type: 'Deleted' },
  ],
  payload_types: ['Created', 'Updated', 'Deleted'],
  statuses: ['enabled', 'disabled'],
  headers: {
    signature: 'X-Chili-Signature',
    timestamp: 'X-Chili-Timestamp',
    signature_encoding: 'hex',
    timestamp_unit: 'unix seconds',
  },
  signing_rule: "HMAC-SHA256(secret, '{timestamp}.{raw_body}'), hex encoded",
  replay_window_seconds: 300,
  replay_window_owner: 'the consumer',
  fan_out: 'unbounded',
}

const INFERENCES = {
  count: 2,
  inferences: [
    {
      id: 'one-flat-envelope-for-the-three-chili-types',
      topic: 'the shape of the payload this sender emits',
      basis: 'the research quotes two envelopes and warns against mixing them',
      change_it: 'dsr/meeting_webhook_fanout/payloads.py:build_payload',
    },
    {
      id: 'replay-protection-belongs-to-the-consumer',
      topic: 'whether the sender refuses a delivery on age',
      basis: 'Replay protection is left to the consumer (MAX_AGE_SECONDS = 300)',
      change_it: 'dsr/meeting_webhook_fanout/signing.py:verify',
    },
  ],
}

const SUBSCRIPTIONS = {
  room_id: ROOM,
  count: 3,
  enabled: 1,
  retired: 1,
  by_event_type: { new_meeting: 2, meeting_update: 1, canceled_meeting: 1 },
  subscription_limit: null,
  subscription_limit_note:
    'The research states the fan-out is unbounded: "You are not limited by the number of webhooks you have".',
  subscriptions: [
    {
      id: SUB,
      url: 'https://hooks.northwind.example/meetings/created',
      event_type: 'new_meeting',
      payload_type: 'Created',
      status: 'enabled',
      retired: false,
    },
    {
      id: 'meeting_webhook_subscription_cccc',
      url: 'http://warehouse.internal/meetings/all',
      event_type: 'meeting_update',
      payload_type: 'Updated',
      status: 'disabled',
      retired: false,
    },
    {
      id: 'meeting_webhook_subscription_dddd',
      url: 'https://hooks.tailspin.example/meetings/created',
      event_type: 'canceled_meeting',
      payload_type: 'Deleted',
      status: 'enabled',
      retired: true,
    },
  ],
}

const SUMMARY = {
  room_id: ROOM,
  subscriptions: 2,
  enabled: 1,
  retired: 1,
  events: 2,
  deliveries: 4,
  by_outcome: { delivered: 2, failed: 1, skipped: 1 },
  by_event_type: { new_meeting: 1, canceled_meeting: 1 },
  has_secret: true,
  notes: [
    'The fan-out is unbounded: "You are not limited by the number of webhooks you have".',
    'Replay protection belongs to the consumer. The room ships the timestamp header.',
    'One attempt per event. There is no retry ladder. Redelivery is a route a person calls.',
  ],
}

const EVENTS = {
  room_id: ROOM,
  count: 2,
  by_event_type: { new_meeting: 1, canceled_meeting: 1 },
  events: [
    {
      id: EVENT,
      event_type: 'new_meeting',
      payload_type: 'Created',
      meeting_id: 'm-0001',
      timestamp: '1790162400',
      missing_fields: [],
    },
    {
      id: 'meeting_webhook_event_eeee',
      event_type: 'canceled_meeting',
      payload_type: 'Deleted',
      meeting_id: 'm-0003',
      timestamp: '1790162700',
      missing_fields: ['assigneeIdChili', 'bookerName'],
    },
  ],
}

const DELIVERIES = {
  room_id: ROOM,
  count: 3,
  by_outcome: { delivered: 1, failed: 1, skipped: 1 },
  deliveries: [
    {
      id: 'meeting_webhook_delivery_1',
      url: 'https://hooks.northwind.example/meetings/created',
      event_type: 'new_meeting',
      outcome: 'delivered',
      status: 202,
      duration_ms: 41.2,
      response_excerpt: 'accepted',
    },
    {
      id: 'meeting_webhook_delivery_2',
      url: 'https://hooks.northwind.example/meetings/updated',
      event_type: 'meeting_update',
      outcome: 'failed',
      status: 500,
      error: 'HTTP 500',
      response_excerpt: 'upstream exploded',
    },
    {
      id: 'meeting_webhook_delivery_3',
      url: null,
      event_type: 'new_meeting',
      outcome: 'skipped',
      reason: 'no_enabled_subscriptions',
    },
  ],
}

const SAMPLE = {
  room_id: ROOM,
  event_type: 'new_meeting',
  payload_type: 'Created',
  timestamp: '1790162400',
  deployment_mode: 'self_hosted',
  raw_body: '{"meetingIdChili": "m-0001", "type": "Created"}',
  signing_input: '1790162400.{"meetingIdChili": "m-0001", "type": "Created"}',
  signature: '5e98bf40c632f33b41bc9c44ad3d575f1855a21b928e1618d44afc311763f201',
  signature_header: 'X-Chili-Signature',
  timestamp_header: 'X-Chili-Timestamp',
  secret_is_set: true,
  replay_window_seconds: 300,
  replay_window_owner: 'the consumer, not the sender',
  verify_snippet:
    "expected = hmac.new(secret, f'{timestamp}.{raw_body}'.encode(), hashlib.sha256).hexdigest()",
}

const ROUTES = {
  '/api/records/room?limit=100': { records: [{ id: ROOM, data: { name: 'Northwind' } }] },
  '/api/wf-066/vocabulary': VOCABULARY,
  '/api/wf-066/inferences': INFERENCES,
  [`/api/wf-066/rooms/${ROOM}/subscriptions`]: SUBSCRIPTIONS,
  [`/api/wf-066/rooms/${ROOM}/summary`]: SUMMARY,
  '/api/wf-066/rooms/room_1/events?limit=25': EVENTS,
  '/api/wf-066/rooms/room_1/deliveries?limit=60': DELIVERIES,
  [`/api/wf-066/rooms/${ROOM}/sample`]: SAMPLE,
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
  render(<MeetingWebhookFanout />)
  await screen.findByText('Meeting webhooks')
  await waitFor(() => expect(screen.getByText('Subscriber URLs')).toBeInTheDocument())
}

describe('WF-066 meeting webhook page', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('is discovered with the descriptor the backend feature id matches', () => {
    expect(descriptor.id).toBe('wf-066-fan-out-meeting-events-via-signed-webhooks')
    expect(descriptor.label).toBe('Meeting webhooks')
    expect(descriptor.Component).toBe(MeetingWebhookFanout)
    // The glyph is a path rather than a shared PATHS name, because
    // components/ui.jsx is shared and this feature may not edit it.
    expect(descriptor.iconPath).toMatch(/^M\d/)
  })

  it('offers exactly the three event types the server published, with what each fires', async () => {
    await renderPage(stubFetch())
    const picker = await screen.findByLabelText('Event type')
    const options = within(picker).getAllByRole('option')
    expect(options.map((option) => option.textContent)).toEqual([
      'For New Meeting (Created)',
      'For Meeting Update (Updated)',
      'For Canceled Meeting (Deleted)',
    ])
  })

  it('says a new row lands disabled and quotes why the fan-out has no cap', async () => {
    await renderPage(stubFetch())
    expect(screen.getByText(/lands/i)).toBeInTheDocument()
    // Matched with getAllBy: the page states the unbounded fan-out twice, once in
    // the summary notes and once under the table. Both are wanted, and a test that
    // demanded exactly one would fail the moment a reviewer added a second - which
    // is the wrong thing for this assertion to catch.
    expect(
      screen.getAllByText(/not limited by the number of webhooks you have/i).length,
    ).toBeGreaterThan(0)
  })

  it('never conveys a subscription status by colour alone', async () => {
    await renderPage(stubFetch())
    expect(screen.getAllByText('enabled').length).toBeGreaterThan(0)
    expect(screen.getAllByText('disabled').length).toBeGreaterThan(0)
    expect(screen.getByText('retired')).toBeInTheDocument()
  })

  it('renders the signing rule as the exact string a subscriber has to compute', async () => {
    await renderPage(stubFetch())
    expect(await screen.findByText(/HMAC-SHA256\(secret/)).toBeInTheDocument()
    // The exact signing input, because the research records a re-serialised body
    // as the cause of a signature mismatch. A summary would not be checkable.
    expect(screen.getByText(SAMPLE.signing_input)).toBeInTheDocument()
    expect(screen.getByText(SAMPLE.raw_body)).toBeInTheDocument()
    expect(screen.getByText(SAMPLE.signature)).toBeInTheDocument()
  })

  it('names the header the signature travels in', async () => {
    await renderPage(stubFetch())
    expect(await screen.findByText('Signature header')).toBeInTheDocument()
    // getAllBy: the section heading count also names the signature header, and both
    // are wanted. The assertion is that the name appears, not where.
    expect(screen.getAllByText('X-Chili-Signature').length).toBeGreaterThan(0)
    expect(screen.getAllByText('X-Chili-Timestamp').length).toBeGreaterThan(0)
  })

  it('publishes the replay window as the consumers, not the rooms', async () => {
    await renderPage(stubFetch())
    expect(await screen.findByText(/300 s, applied by the consumer/)).toBeInTheDocument()
    expect(screen.getByText(/Replay protection belongs to the consumer/)).toBeInTheDocument()
  })

  it('distinguishes reached-and-refused from never-reached', async () => {
    await renderPage(stubFetch())
    // "Reached them and they answered 500" and "no enabled subscription wanted it"
    // are different problems. A page that rendered both as "failed" would hide the
    // one the reader came for.
    expect(await screen.findByText(/answered 500/)).toBeInTheDocument()
    expect(screen.getByText(/No enabled subscription wanted this event type/)).toBeInTheDocument()
  })

  it('says which fields the room left out of a payload rather than sending nulls', async () => {
    await renderPage(stubFetch())
    expect(await screen.findByText('assigneeIdChili, bookerName')).toBeInTheDocument()
  })

  it('posts to the create route with the URL and the event type the picker named', async () => {
    const fetchImpl = stubFetch({
      [`/api/wf-066/rooms/${ROOM}/subscriptions`]: {
        id: 'meeting_webhook_subscription_ffff',
        url: 'https://hooks.new.example/created',
        event_type: 'meeting_update',
        status: 'disabled',
      },
    })
    await renderPage(fetchImpl)
    await userEvent.type(await screen.findByLabelText('Subscriber URL'), 'https://hooks.new.example/created')
    await userEvent.selectOptions(screen.getByLabelText('Event type'), 'meeting_update')
    await userEvent.click(screen.getByRole('button', { name: /create/i }))

    await waitFor(() => {
      const posted = fetchImpl.mock.calls.filter((call) => String(call[1]?.method) === 'POST')
      const create = posted.find((call) =>
        String(call[0]).includes(`/rooms/${ROOM}/subscriptions`),
      )
      expect(create).toBeDefined()
      expect(JSON.parse(create[1].body)).toEqual({
        url: 'https://hooks.new.example/created',
        event_type: 'meeting_update',
      })
    })
  })

  it('refuses a URL this deployment will not accept before the server does', async () => {
    await renderPage(stubFetch())
    // A websocket URL: it parses, so the browser check gets as far as the scheme,
    // and the scheme is the thing the deployment rules on.
    await userEvent.type(await screen.findByLabelText('Subscriber URL'), 'ws://hooks.example/created')
    await userEvent.click(screen.getByRole('button', { name: /create/i }))
    // The message names the reason rather than a status code, because the reader
    // typed the URL and the URL is what is wrong.
    expect(await screen.findByRole('alert')).toHaveTextContent(/Only http and https are accepted/)
  })

  it('refuses a URL the browser cannot parse at all', async () => {
    expect(urlIsAcceptable('hooks.example/created', 'self_hosted')).toEqual({
      ok: false,
      why: expect.stringContaining('not a URL'),
    })
  })

  it('refuses a localhost subscriber URL on a room on the SaaS rules', () => {
    expect(urlIsAcceptable('https://localhost/hook', 'saas')).toEqual({
      ok: false,
      why: expect.stringContaining('localhost'),
    })
    expect(urlIsAcceptable('https://hooks.example/hook', 'saas').ok).toBe(true)
  })

  it('accepts a private plain-http address on a self-hosted room', () => {
    // The research says self-hosted accepts both, and this room is self-hosted.
    expect(urlIsAcceptable('http://warehouse.internal/meetings', 'self_hosted').ok).toBe(true)
    expect(urlIsAcceptable('http://127.0.0.1/hook', 'self_hosted').ok).toBe(true)
  })

  it('sends the enable and delete controls to their own routes', async () => {
    const fetchImpl = stubFetch({
      [`/api/wf-066/rooms/${ROOM}/subscriptions/${SUB}`]: { id: SUB, status: 'disabled' },
      [`/api/wf-066/rooms/${ROOM}/events/${EVENT}/redeliver`]: { delivered: 1, failed: 0 },
    })
    await renderPage(fetchImpl)

    // The table has one button per live row, so the controls are found by their
    // position rather than by their label: a table with two live rows has two
    // Delete buttons and a getBy would be ambiguous by design.
    const table = (await screen.findByText('Subscriber URLs')).closest('section')
    await userEvent.click(within(table).getByRole('button', { name: /^disable$/i }))
    await waitFor(() => {
      const patched = fetchImpl.mock.calls.filter((call) => String(call[1]?.method) === 'PATCH')
      expect(patched.some((call) => String(call[0]).includes(SUB))).toBe(true)
    })

    await userEvent.click(within(table).getAllByRole('button', { name: /^delete$/i })[0])
    await waitFor(() => {
      const deleted = fetchImpl.mock.calls.filter((call) => String(call[1]?.method) === 'DELETE')
      expect(deleted.some((call) => String(call[0]).includes('/subscriptions/'))).toBe(true)
    })

    // The event table has one redelivery control per event, so the first row's is
    // taken by position. The assertion is which route it reached, which is the
    // only thing a reader of this table cares about.
    const events = (await screen.findByText('Signed events')).closest('section')
    await userEvent.click(within(events).getAllByRole('button', { name: /send again/i })[0])
    await waitFor(() => {
      const posted = fetchImpl.mock.calls.filter((call) => String(call[1]?.method) === 'POST')
      expect(posted.some((call) => String(call[0]).includes(`/events/${EVENT}/redeliver`))).toBe(
        true,
      )
    })
  })

  it('says a retired row keeps its history rather than inviting a second delete', async () => {
    await renderPage(stubFetch())
    expect(await screen.findByText(/History resolves/)).toBeInTheDocument()
  })

  it('says so when a room has no subscriber URLs yet', async () => {
    await renderPage(
      stubFetch({
        [`/api/wf-066/rooms/${ROOM}/subscriptions`]: {
          ...SUBSCRIPTIONS,
          count: 0,
          enabled: 0,
          retired: 0,
          subscriptions: [],
        },
        '/api/wf-066/rooms/room_1/events?limit=25': { count: 0, events: [] },
        '/api/wf-066/rooms/room_1/deliveries?limit=60': { count: 0, deliveries: [] },
      }),
    )
    expect(await screen.findByText(/no subscriber URLs yet/i)).toBeInTheDocument()
  })

  it('renders every judgement call with its basis and how to change it', async () => {
    await renderPage(stubFetch())
    expect(
      await screen.findByText('the shape of the payload this sender emits'),
    ).toBeInTheDocument()
    expect(screen.getByText(/Replay protection is left to the consumer/)).toBeInTheDocument()
    expect(screen.getByText(/dsr\/scheduling_meetings\/payloads.py:build_payload/)).toBeInTheDocument()
  })
})

describe('WF-066 page helpers', () => {
  it('reads a status as a word, so a badge is never the only signal', () => {
    expect(subscriptionWord({ status: 'enabled' })).toBe('enabled')
    expect(subscriptionWord({ status: 'disabled' })).toBe('disabled')
    expect(subscriptionWord({ status: 'enabled', retired: true })).toBe('retired')
    expect(subscriptionTone({ retired: true })).toBe('delete')
    expect(subscriptionTone({ status: 'enabled' })).toBe('insert')
    expect(subscriptionTone({ status: 'disabled' })).toBe('neutral')
  })

  it('names a delivery state in a sentence a reader can act on', () => {
    expect(deliveryNote({ outcome: 'delivered', status: 202, duration_ms: 41.2 })).toMatch(
      /answered 202 in 41 ms/,
    )
    expect(deliveryNote({ outcome: 'failed', status: 500, error: 'HTTP 500' })).toMatch(
      /answered 500/,
    )
    expect(
      deliveryNote({ outcome: 'failed', status: null, error: 'URLError: timed out' }),
    ).toMatch(/could not be reached.*timed out/)
    expect(deliveryNote({ outcome: 'skipped', reason: 'no_enabled_subscriptions' })).toMatch(
      /No enabled subscription/,
    )
    expect(deliveryNote(null)).toBe('')
  })

  it('builds the signing rule line from the served vocabulary, not a literal', () => {
    const line = signingRuleLine(VOCABULARY)
    expect(line).toContain('{timestamp}.{raw_body}')
    expect(line).toContain('X-Chili-Signature')
    expect(signingRuleLine(undefined)).toBe('')
  })

  it('renders a machine value in mono and never leaves a gap for an empty one', () => {
    render(<MachineValue>m-0001</MachineValue>)
    expect(screen.getByText('m-0001')).toHaveClass('font-mono')
    render(<MachineValue />)
    expect(screen.getByText('none')).toBeInTheDocument()
  })

  it('puts the status word inside the badge rather than beside it', () => {
    render(<StatusBadge tone="insert" word="delivered" />)
    expect(screen.getByText('delivered')).toBeInTheDocument()
  })
})
