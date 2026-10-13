import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import WebhookFanout from '@/features/wf-113-webhook-fanout/WebhookFanout.jsx'
import descriptor from '@/features/wf-113-webhook-fanout/index.jsx'
import {
  automationTone,
  automationWord,
  canSendSample,
  deliveryNote,
  deliveryTone,
  deliveryWord,
  shortHash,
  signingRuleLine,
  triggerTone,
  triggerWord,
} from '@/features/wf-113-webhook-fanout/api.js'

/**
 * Tests for the WF-113 webhook ingest page.
 *
 * Four things the backend tests cannot see, and each is a way this page would
 * otherwise be quietly wrong:
 *
 * * **The page is discovered at all.** `lib/features.js` globs
 *   `src/features/*\/index.jsx`. A feature that ships its page and primitives but
 *   no descriptor builds green and appears in the product zero times.
 * * **"Skipped because the Slack block has no channel" reads differently from
 *   "the event had no name".** Both would render as a single word, and the
 *   reader would not know which fix to make.
 * * **No status is conveyed by colour alone.** Every state carries its own word.
 * * **A secret-bearing automation cannot be sent a sample from the page.** The
 *   button is disabled, because signing is the external service's job.
 *
 * The fetch stub is keyed on this feature's own paths. An unstubbed path throws,
 * so a test cannot quietly pass against the wrong response.
 */

const ROOM = 'room_1'

const VOCABULARY = {
  ticket: 'WF-113',
  vocabulary: {
    batch_limit: 1000,
    actions: [
      { key: 'send_event', label: 'Send Event', optional: false },
      { key: 'batch_update', label: 'Batch Update', optional: false },
      { key: 'slack', label: 'Slack', optional: true },
    ],
    rate_limits: [
      { surface: 'pipelines', label: 'Track / Pipelines', limit: 3000, window_seconds: 3, soft: true },
      { surface: 'app_api', label: 'App API', limit: 10, window_seconds: 1, soft: false },
    ],
  },
  signing: {
    signature_header: 'X-DSR-Signature',
    timestamp_header: 'X-DSR-Timestamp',
    rule: "HMAC-SHA256(secret, '{timestamp}.{raw_body}'), hex encoded",
    encoding: 'hex',
    timestamp_unit: 'unix seconds',
    window_seconds: 300,
    required_when: 'the automation carries a secret',
    reused_from: 'WF-066 dsr.meeting_webhook_fanout.signing',
  },
  inferences: 1,
}

const INFERENCES = {
  count: 1,
  decisions: [
    {
      id: 'customer-data-resolves-to-the-empty-string',
      topic: 'what a mapping that references the person renders',
      chosen: 'render the empty string and do not error',
      options: ['render the empty string and do not error'],
      rejected_because: 'the research states the empty string as the behaviour',
      change_it: 'backend/dsr/webhook_ingest/rules.py:resolve_expression',
    },
  ],
}

const AUTOMATIONS = {
  room_ref: ROOM,
  count: 1,
  automations: [
    {
      id: 'webhook_automation_1',
      name: 'Order events',
      status: 'started',
      action_count: 4,
      url: '/api/wf-113/hooks/tok_1',
      token: 'tok_1',
      has_secret: false,
    },
  ],
}

const SUMMARY = {
  automations: 1,
  started: 1,
  triggers: 2,
  duplicates: 1,
  rejected: 0,
  events: 3,
  deliveries: 4,
  by_outcome: { delivered: 2, skipped: 2 },
  notes: ['The webhook is the subject. A mapping that references the person renders an empty string.'],
}

const TRIGGERS = {
  room_ref: ROOM,
  count: 2,
  by_state: { accepted: 1, duplicate: 1 },
  triggers: [
    {
      id: 'webhook_trigger_1',
      state: 'accepted',
      shape: 'object',
      fingerprint: 'abcdef0123456789',
      identifiers: { id: 'ORD-1' },
      received_at: '2026-03-04T12:00:00.000+00:00',
    },
    {
      id: 'webhook_trigger_2',
      state: 'duplicate',
      shape: 'object',
      fingerprint: 'abcdef0123456789',
      identifiers: { id: 'ORD-1' },
      received_at: '2026-03-04T12:00:30.000+00:00',
    },
  ],
}

const EVENTS = {
  room_ref: ROOM,
  count: 1,
  by_name: { 'order:placed': 1 },
  events: [
    { id: 'webhook_event_1', name: 'order:placed', action: 'send_event', data: { order_id: 'ORD-1' } },
  ],
}

const DELIVERIES = {
  room_ref: ROOM,
  count: 2,
  by_outcome: { delivered: 1, skipped: 1 },
  deliveries: [
    { id: 'webhook_fanout_delivery_1', action: 'send_event', outcome: 'delivered', count: 1, reason: null },
    { id: 'webhook_fanout_delivery_2', action: 'slack', outcome: 'skipped', count: null, reason: 'no_channel' },
  ],
}

function jsonResponse(payload, status = 200) {
  return { ok: status < 400, status, json: async () => payload }
}

function installFetch(overrides = {}) {
  const routes = {
    '/api/records/room?limit=100': { records: [{ id: ROOM, data: { name: 'Northwind Traders' } }] },
    '/api/wf-113/vocabulary': VOCABULARY,
    '/api/wf-113/inferences': INFERENCES,
    [`/api/wf-113/automations?room_id=${ROOM}`]: AUTOMATIONS,
    [`/api/wf-113/summary?room_id=${ROOM}`]: SUMMARY,
    [`/api/wf-113/triggers?room_id=${ROOM}`]: TRIGGERS,
    [`/api/wf-113/events?room_id=${ROOM}`]: EVENTS,
    [`/api/wf-113/deliveries?room_id=${ROOM}`]: DELIVERIES,
    ...overrides,
  }
  const fetchMock = vi.fn(async (url) => {
    const key = String(url)
    if (!(key in routes)) throw new Error(`unstubbed fetch: ${key}`)
    const value = routes[key]
    return value instanceof Object && 'status' in value && 'json' in value
      ? value
      : jsonResponse(value)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

beforeEach(() => {
  installFetch()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('the descriptor', () => {
  it('is the discovered shape and matches the backend id', () => {
    expect(descriptor.id).toBe('wf-113-webhook-fanout')
    expect(descriptor.label).toBe('Webhook ingest')
    expect(typeof descriptor.Component).toBe('function')
    expect(descriptor.iconPath).toBeTruthy()
  })
})

describe('the page', () => {
  it('renders the generated URL and every log', async () => {
    render(<WebhookFanout />)
    expect(await screen.findByRole('heading', { name: 'Webhook ingest' })).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('/api/wf-113/hooks/tok_1')).toBeInTheDocument())
    expect(screen.getByText('order:placed')).toBeInTheDocument()
    expect(screen.getByText('duplicate')).toBeInTheDocument()
    expect(screen.getByText('started')).toBeInTheDocument()
  })

  it('explains a skipped delivery in a sentence a reader can act on', async () => {
    render(<WebhookFanout />)
    const note = await screen.findByText(/skipped because/)
    expect(note.textContent).toContain('no channel to post to')
  })

  it('states the webhook is the subject from the summary notes', async () => {
    render(<WebhookFanout />)
    const matches = await screen.findAllByText(/The webhook is the subject/)
    expect(matches.length).toBeGreaterThan(0)
  })

  it('creates an automation through the feature route', async () => {
    const created = {
      id: 'webhook_automation_2',
      name: 'Order events',
      status: 'draft',
      action_count: 4,
      url: '/api/wf-113/hooks/tok_2',
      token: 'tok_2',
      has_secret: false,
    }
    const fetchMock = installFetch({
      [`/api/wf-113/automations?room_id=${ROOM}`]: AUTOMATIONS,
    })
    fetchMock.mockImplementation(async (url, options = {}) => {
      const key = String(url)
      if (key.startsWith('/api/wf-113/automations') && options.method === 'POST') {
        return jsonResponse(created, 201)
      }
      if (key === `/api/wf-113/automations?room_id=${ROOM}`) return jsonResponse(AUTOMATIONS)
      if (key === '/api/records/room?limit=100')
        return jsonResponse({ records: [{ id: ROOM, data: { name: 'Northwind Traders' } }] })
      if (key === '/api/wf-113/vocabulary') return jsonResponse(VOCABULARY)
      if (key === '/api/wf-113/inferences') return jsonResponse(INFERENCES)
      if (key === `/api/wf-113/summary?room_id=${ROOM}`) return jsonResponse(SUMMARY)
      if (key === `/api/wf-113/triggers?room_id=${ROOM}`) return jsonResponse(TRIGGERS)
      if (key === `/api/wf-113/events?room_id=${ROOM}`) return jsonResponse(EVENTS)
      if (key === `/api/wf-113/deliveries?room_id=${ROOM}`) return jsonResponse(DELIVERIES)
      throw new Error(`unstubbed fetch: ${key}`)
    })

    render(<WebhookFanout />)
    await screen.findByRole('heading', { name: 'Webhook ingest' })
    await userEvent.click(await screen.findByRole('button', { name: /Give me a webhook URL/ }))
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining('/api/wf-113/automations?room_id=room_1'),
        expect.objectContaining({ method: 'POST' }),
      ),
    )
  })
})

describe('the helpers', () => {
  it('names every automation and trigger state as a word', () => {
    expect(automationWord({ status: 'started' })).toBe('started')
    expect(automationWord({ status: 'draft' })).toBe('draft')
    expect(automationTone({ status: 'started' })).toBe('insert')
    expect(triggerWord({ state: 'duplicate' })).toBe('duplicate')
    expect(triggerWord({ state: 'rejected' })).toBe('rejected')
    expect(triggerWord({ state: 'accepted' })).toBe('accepted')
    expect(triggerTone({ state: 'rejected' })).toBe('delete')
  })

  it('distinguishes a skipped reason from a failed one', () => {
    expect(deliveryNote({ action: 'slack', outcome: 'skipped', reason: 'no_channel' })).toContain(
      'no channel',
    )
    expect(deliveryNote({ action: 'send_event', outcome: 'failed', reason: 'event_name_missing' })).toContain(
      'no name',
    )
    expect(deliveryTone({ outcome: 'delivered' })).toBe('insert')
    expect(deliveryWord({ outcome: 'skipped' })).toBe('skipped')
  })

  it('refuses to send a sample to a secret-bearing automation', () => {
    expect(canSendSample({ token: 't', has_secret: false })).toBe(true)
    expect(canSendSample({ token: 't', has_secret: true })).toBe(false)
    expect(canSendSample({ token: null, has_secret: false })).toBe(false)
  })

  it('shortens a fingerprint and renders the signing rule', () => {
    expect(shortHash('abcdef0123456789')).toBe('abcdef012345...')
    expect(signingRuleLine(VOCABULARY)).toContain('X-DSR-Signature')
    expect(signingRuleLine(VOCABULARY)).toContain('X-DSR-Timestamp')
  })
})
