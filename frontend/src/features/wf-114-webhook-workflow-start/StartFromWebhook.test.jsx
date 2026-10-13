import { webcrypto } from 'node:crypto'

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import StartFromWebhook from '@/features/wf-114-webhook-workflow-start/StartFromWebhook.jsx'
import descriptor from '@/features/wf-114-webhook-workflow-start/index.jsx'
import {
  bindingLines,
  canSendFromPage,
  invocationNote,
  invocationTone,
  invocationWord,
  methodsLine,
  shortHash,
  signedHeaders,
  signingRuleLine,
  triggerSavedTone,
  triggerSavedWord,
  workflowTone,
  workflowWord,
} from '@/features/wf-114-webhook-workflow-start/api.js'

/**
 * Tests for the WF-114 start-from-webhook page.
 *
 * Five things the backend tests cannot see, and each is a way this page would
 * otherwise be quietly wrong:
 *
 * * **The page is discovered at all.** `lib/features.js` globs
 *   `src/features/*\/index.jsx`. A feature that ships its page and primitives but
 *   no descriptor builds green and appears in the product zero times.
 * * **An unmapped delivery reads differently from a rejected one.** Both are
 *   non-successes, and the fix is different for each.
 * * **No status is conveyed by colour alone.** Every state carries its own word.
 * * **The page cannot sign without the trigger's signing value**, and a delivery
 *   that is not signed is refused at the far end.
 * * **The signature the page computes is the one the server verifies.** The
 *   vector below is HMAC-SHA256("secret", '1.{"a":1}') computed outside this
 *   file, so a change to the signed input is caught here rather than by an
 *   external service whose deliveries start failing.
 *
 * The fetch stub is keyed on this feature's own paths. An unstubbed path throws,
 * so a test cannot quietly pass against the wrong response.
 */

// jsdom's `crypto` has no `subtle` in every version this project runs on. The
// floor in src/test/setup.js restores globals per test and re-snapshots in
// beforeAll, so a value installed at module load survives the whole file.
if (!globalThis.crypto?.subtle) {
  Object.defineProperty(globalThis, 'crypto', { value: webcrypto, configurable: true })
}

const ROOM = 'room_1'

const VOCABULARY = {
  ticket: 'WF-114',
  vocabulary: {
    methods: ['POST', 'GET', 'PUT'],
    method_payload_sources: [
      { method: 'POST', source: 'body', accepts_body: true, note: 'The documented example arrives this way.' },
      { method: 'GET', source: 'query', accepts_body: false, note: 'HTTP defines no request body for GET.' },
      { method: 'PUT', source: 'body', accepts_body: true, note: 'Named beside POST, same shape.' },
    ],
    states: ['accepted', 'duplicate', 'unmapped', 'rejected'],
    operators: [{ id: 'equals', label: 'equals', takes_value: true }],
    variable_types: [{ id: 'text', label: 'Text' }],
    documented_payload: { customer_id: 'CUST_001' },
  },
  signing: {
    signature_header: 'X-DSR-Signature',
    timestamp_header: 'X-DSR-Timestamp',
    rule: "HMAC-SHA256(secret, '{timestamp}.{raw_body}'), hex encoded",
    signed_input: 'POST and PUT sign the exact bytes of the request body.',
    encoding: 'hex',
    timestamp_unit: 'unix seconds',
    window_seconds: 300,
    required: 'always',
    required_when: 'every delivery, because this build fails closed',
    reused_from: 'WF-113 dsr.webhook_ingest.signing',
  },
  inferences: 1,
}

const INFERENCES = {
  count: 1,
  decisions: [
    {
      id: 'every-generated-url-is-signed',
      topic: 'how a delivery proves it came from the external service',
      chosen: 'require the shared signature on every delivery, always',
      options: ['require the shared signature on every delivery, always'],
      rejected_because: 'A minted URL that anyone can call is a published write endpoint.',
      change_it: 'backend/dsr/webhook_workflow_start/signing.py:verify',
    },
  ],
}

const WORKFLOWS = {
  room_ref: ROOM,
  count: 1,
  workflows: [
    {
      id: 'webhook_workflow_1',
      name: 'Northwind payments',
      status: 'published',
      action_count: 2,
      actions: ['Update the lead status'],
    },
  ],
}

const SUMMARY = {
  workflows: 1,
  published: 1,
  triggers: 1,
  saved_triggers: 1,
  invocations: 4,
  runs: 1,
  notes: ['The webhook URL is unique for each trigger, not for each workflow.'],
}

const TRIGGERS = {
  room_ref: ROOM,
  count: 1,
  triggers: [
    {
      id: 'webhook_workflow_trigger_1',
      name: 'Inbound Webhook',
      saved: true,
      methods: ['POST', 'GET', 'PUT'],
      mapping_count: 4,
      url: '/api/wf-114/hooks/tok_1',
      token: 'tok_1',
      has_signing_value: true,
    },
  ],
}

const INVOCATIONS = {
  room_ref: ROOM,
  count: 4,
  by_state: { accepted: 1, duplicate: 1, unmapped: 1, rejected: 1 },
  invocations: [
    {
      id: 'inv_1',
      state: 'accepted',
      mode: 'live',
      reason: null,
      bindings: [{ variable: 'customer_ref', value: 'CUST_001' }],
      fingerprint: 'abcdef0123456789',
      received_at: '2026-03-04T12:00:00.000+00:00',
    },
    {
      id: 'inv_2',
      state: 'unmapped',
      mode: 'live',
      reason: 'no_mapping_matched',
      bindings: [],
      fingerprint: 'abcdef0123456789',
      received_at: '2026-03-04T12:01:00.000+00:00',
    },
    {
      id: 'inv_3',
      state: 'rejected',
      mode: 'live',
      reason: 'signature_missing',
      bindings: [],
      fingerprint: null,
      received_at: '2026-03-04T12:02:00.000+00:00',
    },
    {
      id: 'inv_4',
      state: 'duplicate',
      mode: 'live',
      reason: null,
      bindings: [],
      fingerprint: 'abcdef0123456789',
      received_at: '2026-03-04T12:03:00.000+00:00',
    },
  ],
}

const RUNS = {
  room_ref: ROOM,
  count: 1,
  runs: [
    {
      id: 'run_1',
      status: 'started',
      mode: 'live',
      bindings: [{ variable: 'customer_ref', value: 'CUST_001' }],
      planned_actions: ['Update the lead status'],
      payload_shape: 'object',
      started_at: '2026-03-04T12:00:00.000+00:00',
    },
  ],
}

function jsonResponse(payload, status = 200) {
  return { ok: status < 400, status, json: async () => payload }
}

function installFetch(overrides = {}) {
  const routes = {
    '/api/records/room?limit=100': { records: [{ id: ROOM, data: { name: 'Northwind Traders' } }] },
    '/api/wf-114/vocabulary': VOCABULARY,
    '/api/wf-114/inferences': INFERENCES,
    [`/api/wf-114/workflows?room_id=${ROOM}`]: WORKFLOWS,
    [`/api/wf-114/summary?room_id=${ROOM}`]: SUMMARY,
    [`/api/wf-114/triggers?room_id=${ROOM}`]: TRIGGERS,
    [`/api/wf-114/invocations?room_id=${ROOM}`]: INVOCATIONS,
    [`/api/wf-114/runs?room_id=${ROOM}`]: RUNS,
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
    expect(descriptor.id).toBe('wf-114-webhook-workflow-start')
    expect(descriptor.label).toBe('Start from webhook')
    expect(typeof descriptor.Component).toBe('function')
    expect(descriptor.iconPath).toBeTruthy()
  })
})

describe('the page', () => {
  it('renders the workflow, its trigger URL and every delivery state', async () => {
    render(<StartFromWebhook />)
    expect(await screen.findByRole('heading', { name: 'Start from webhook' })).toBeInTheDocument()
    await waitFor(() =>
      expect(screen.getByText('/api/wf-114/hooks/tok_1')).toBeInTheDocument(),
    )
    expect(screen.getByText('Northwind payments')).toBeInTheDocument()
    expect(screen.getByText('published')).toBeInTheDocument()
    expect(screen.getByText('unmapped')).toBeInTheDocument()
    expect(screen.getByText('rejected')).toBeInTheDocument()
    expect(screen.getByText('duplicate')).toBeInTheDocument()
  })

  it('explains an unmapped delivery in a sentence a reader can act on', async () => {
    render(<StartFromWebhook />)
    const note = await screen.findByText(/no mapping matched/)
    expect(note).toBeInTheDocument()
  })

  it('shows the variables a run bound and the actions it planned', async () => {
    render(<StartFromWebhook />)
    await waitFor(() =>
      expect(screen.getByText(/customer_ref = "CUST_001"/)).toBeInTheDocument(),
    )
    expect(screen.getByText('Update the lead status')).toBeInTheDocument()
  })

  it('generates a trigger and shows its URL and signing value once', async () => {
    const created = {
      id: 'webhook_workflow_trigger_2',
      name: 'Inbound Webhook',
      saved: false,
      methods: ['POST', 'GET', 'PUT'],
      mapping_count: 4,
      url: '/api/wf-114/hooks/tok_2',
      token: 'tok_2',
      signing_value: 'fresh-signing-value',
      signature_header: 'X-DSR-Signature',
      timestamp_header: 'X-DSR-Timestamp',
      has_signing_value: true,
    }
    const fetchMock = installFetch()
    fetchMock.mockImplementation(async (url, options = {}) => {
      const key = String(url)
      if (key === '/api/wf-114/workflows/webhook_workflow_1/triggers') {
        expect(options.method).toBe('POST')
        return jsonResponse(created, 201)
      }
      if (key in {}) return jsonResponse({})
      if (key === '/api/records/room?limit=100')
        return jsonResponse({ records: [{ id: ROOM, data: { name: 'Northwind Traders' } }] })
      if (key === '/api/wf-114/vocabulary') return jsonResponse(VOCABULARY)
      if (key === '/api/wf-114/inferences') return jsonResponse(INFERENCES)
      if (key === `/api/wf-114/workflows?room_id=${ROOM}`) return jsonResponse(WORKFLOWS)
      if (key === `/api/wf-114/summary?room_id=${ROOM}`) return jsonResponse(SUMMARY)
      if (key === `/api/wf-114/triggers?room_id=${ROOM}`) return jsonResponse(TRIGGERS)
      if (key === `/api/wf-114/invocations?room_id=${ROOM}`) return jsonResponse(INVOCATIONS)
      if (key === `/api/wf-114/runs?room_id=${ROOM}`) return jsonResponse(RUNS)
      throw new Error(`unstubbed fetch: ${key}`)
    })

    render(<StartFromWebhook />)
    await screen.findByRole('heading', { name: 'Start from webhook' })
    await userEvent.click(
      await screen.findByRole('button', { name: /Trigger for Northwind payments/ }),
    )
    await waitFor(() =>
      expect(screen.getByText('/api/wf-114/hooks/tok_2')).toBeInTheDocument(),
    )
    expect(screen.getByText('fresh-signing-value')).toBeInTheDocument()
    expect(screen.getByText('X-DSR-Signature')).toBeInTheDocument()
  })

  it('sends a test request through this feature own hook route', async () => {
    const created = {
      id: 'webhook_workflow_trigger_3',
      name: 'Inbound Webhook',
      saved: true,
      methods: ['POST', 'GET', 'PUT'],
      mapping_count: 4,
      url: '/api/wf-114/hooks/tok_3',
      token: 'tok_3',
      signing_value: 'fresh-signing-value',
      signature_header: 'X-DSR-Signature',
      timestamp_header: 'X-DSR-Timestamp',
      has_signing_value: true,
    }
    const report = {
      state: 'accepted',
      mode: 'test',
      started: false,
      message: 'Test request validated.',
      bindings: [],
    }
    const fetchMock = installFetch()
    fetchMock.mockImplementation(async (url, options = {}) => {
      const key = String(url)
      if (key.startsWith('/api/wf-114/hooks/tok_3')) {
        expect(options.method).toBe('POST')
        expect(options.headers['X-DSR-Signature']).toMatch(/^[0-9a-f]{64}$/)
        expect(options.headers['X-DSR-Timestamp']).toMatch(/^\d+$/)
        return jsonResponse(report, 202)
      }
      if (key === '/api/wf-114/workflows/webhook_workflow_1/triggers')
        return jsonResponse(created, 201)
      if (key === '/api/records/room?limit=100')
        return jsonResponse({ records: [{ id: ROOM, data: { name: 'Northwind Traders' } }] })
      if (key === '/api/wf-114/vocabulary') return jsonResponse(VOCABULARY)
      if (key === '/api/wf-114/inferences') return jsonResponse(INFERENCES)
      if (key === `/api/wf-114/workflows?room_id=${ROOM}`) return jsonResponse(WORKFLOWS)
      if (key === `/api/wf-114/summary?room_id=${ROOM}`) return jsonResponse(SUMMARY)
      if (key === `/api/wf-114/triggers?room_id=${ROOM}`) return jsonResponse(TRIGGERS)
      if (key === `/api/wf-114/invocations?room_id=${ROOM}`) return jsonResponse(INVOCATIONS)
      if (key === `/api/wf-114/runs?room_id=${ROOM}`) return jsonResponse(RUNS)
      throw new Error(`unstubbed fetch: ${key}`)
    })

    render(<StartFromWebhook />)
    await screen.findByRole('heading', { name: 'Start from webhook' })
    await userEvent.click(
      await screen.findByRole('button', { name: /Trigger for Northwind payments/ }),
    )
    await screen.findByText('/api/wf-114/hooks/tok_3')
    await userEvent.click(await screen.findByRole('button', { name: /Send a test request/ }))
    await waitFor(() => expect(screen.getByText('Test request validated.')).toBeInTheDocument())
  })

  it('states that the URL is unique per trigger', async () => {
    render(<StartFromWebhook />)
    const matches = await screen.findAllByText(/unique for each trigger/)
    expect(matches.length).toBeGreaterThan(0)
  })
})

describe('the signature the page computes', () => {
  it('is the exact one the server verifies', async () => {
    // HMAC-SHA256("secret", '1.{"a":1}'), computed outside this file.
    const headers = await signedHeaders('secret', '{"a":1}', {
      when: new Date(1 * 1000),
    })
    expect(headers['X-DSR-Signature']).toBe(
      '7900dcfa0dd8e32ee0ef71db511fc5d7b91ed98221ea7eb400f3072f0d7dd39d',
    )
    expect(headers['X-DSR-Timestamp']).toBe('1')
  })

  it('refuses to sign without a signing value rather than sending it unsigned', async () => {
    await expect(signedHeaders('', '{}')).rejects.toThrow(/No signing value/)
  })
})

describe('the helpers', () => {
  it('names every workflow and trigger state as a word', () => {
    expect(workflowWord({ status: 'published' })).toBe('published')
    expect(workflowWord({ status: 'draft' })).toBe('draft')
    expect(workflowTone({ status: 'published' })).toBe('insert')
    expect(triggerSavedWord({ saved: true })).toBe('saved')
    expect(triggerSavedWord({ saved: false })).toBe('not saved')
    expect(triggerSavedTone({ saved: false })).toBe('restore')
  })

  it('names every delivery state as a word with its own tone', () => {
    expect(invocationWord({ state: 'accepted' })).toBe('accepted')
    expect(invocationWord({ state: 'unmapped' })).toBe('unmapped')
    expect(invocationWord({ state: 'rejected' })).toBe('rejected')
    expect(invocationWord({ state: 'duplicate' })).toBe('duplicate')
    expect(invocationTone({ state: 'rejected' })).toBe('delete')
    expect(invocationTone({ state: 'unmapped' })).toBe('restore')
    expect(invocationTone({ state: 'duplicate' })).toBe('update')
    expect(invocationTone({ state: 'accepted' })).toBe('insert')
  })

  it('distinguishes an unmapped delivery from a refused one and from a test', () => {
    expect(invocationNote({ state: 'unmapped', reason: 'no_mapping_matched' })).toContain(
      'did not start',
    )
    expect(invocationNote({ state: 'rejected', reason: 'trigger_not_saved' })).toContain(
      'Save trigger',
    )
    expect(invocationNote({ state: 'rejected', reason: 'signature_missing' })).toContain('signed')
    expect(invocationNote({ state: 'accepted', mode: 'test', bindings: [] })).toContain(
      'started nothing',
    )
    expect(invocationNote({ state: 'accepted', mode: 'live', bindings: [1, 2] })).toContain(
      '2 variable(s)',
    )
    expect(invocationNote({ state: 'duplicate' })).toContain('sent before')
  })

  it('renders the served method table and the signing rule', () => {
    expect(methodsLine({ methods: ['POST', 'GET'] })).toBe('POST, GET')
    expect(methodsLine({})).toBe('none')
    expect(signingRuleLine(VOCABULARY)).toContain('X-DSR-Signature')
    expect(signingRuleLine(VOCABULARY)).toContain('X-DSR-Timestamp')
  })

  it('only offers a delivery when the session holds the signing value', () => {
    expect(canSendFromPage({ token: 't', signingValue: 's' })).toBe(true)
    expect(canSendFromPage({ token: 't' })).toBe(false)
    expect(canSendFromPage({ signingValue: 's' })).toBe(false)
  })

  it('shortens a fingerprint and lists the bindings', () => {
    expect(shortHash('abcdef0123456789')).toBe('abcdef012345...')
    expect(bindingLines([{ variable: 'a', value: 1 }])).toEqual(['a = 1'])
    expect(bindingLines(null)).toEqual([])
  })
})
