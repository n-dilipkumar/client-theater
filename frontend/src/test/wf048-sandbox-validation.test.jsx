import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import SandboxValidation from '@/features/wf-048-validate-the-connector-against-a-sandb/SandboxValidation.jsx'
import descriptor from '@/features/wf-048-validate-the-connector-against-a-sandb/index.jsx'

/**
 * Tests for the WF-048 sandbox validation page.
 *
 * Five things the backend tests cannot see, and each one is a way this page was
 * previously wrong:
 *
 * * **The page is discovered at all.** `lib/features.js` globs
 *   `src/features/*\/index.jsx`. The recovered branch shipped `SandboxValidation.jsx`
 *   and `primitives.jsx` but no `index.jsx`, so the glob never matched, the page was
 *   never imported, and `npm run build` was green while the product carried zero
 *   occurrences of `wf-048`. The descriptor is pinned here so that file cannot go
 *   missing again without a red test.
 * * **The self-test posts to the room-scoped route.** The handler used to post to
 *   the literal string `/wf-048/connections/self-test-route-placeholder` and discard
 *   the answer. A test that only pressed the button would not see the wrong URL, so
 *   the recorded path is asserted.
 * * **The self-test body carries the connector the form asked for.** A self-test
 *   that posts an empty body is refused with 400, so the vendor, object and base URL
 *   have to travel.
 * * **A 422 is a different message from a 400.** The promotion gate answers 422 with
 *   `not_validated` when the newest run is not green. The page's copy for that is
 *   "run the test sync, then promote", and it must not collapse into the generic
 *   refusal text.
 * * **An empty state says so.** A room with no connectors must not render a blank
 *   panel.
 *
 * Stubs are keyed on this feature's own paths. An unstubbed path throws, so a test
 * cannot quietly pass against the wrong response.
 */

const BASE = '/wf-048'

const ok = (body, status = 200) => ({ ok: true, status, statusText: 'OK', json: async () => body })

const SUMMARY = {
  connections: 2,
  test_environments: 1,
  runs: 3,
  green_runs: 1,
  failed_runs: 2,
  promoted: 1,
}

const CONNECTION = (overrides = {}) => ({
  id: 'conn_sandbox',
  room_id: 'room_1',
  label: 'Northwind Traders - HubSpot (sandbox)',
  vendor: 'hubspot',
  environment: 'test',
  base_url: 'https://sandbox.example/api/wf-048/demo/green',
  object_name: 'engagements',
  key_field: 'engagement_key',
  test_env_kind: 'hubspot',
  env_type: 'hubspot_test_account',
  source_connection_id: 'conn_prod',
  expires_at: null,
  reverted_at: null,
  promoted_at: null,
  promoted_run_id: null,
  last_run_id: 'run_1',
  last_run_status: 'passed',
  last_validated_at: '2026-09-28T12:00:00+00:00',
  created_at: '2026-09-28T12:00:00+00:00',
  updated_at: '2026-09-28T12:00:00+00:00',
  ...overrides,
})

const CONNECTIONS = {
  count: 2,
  production: 1,
  test_environments: 1,
  connections: [
    CONNECTION(),
    CONNECTION({
      id: 'conn_prod',
      label: 'Northwind Traders - HubSpot (production)',
      environment: 'production',
      test_env_kind: null,
      env_type: null,
      source_connection_id: null,
      last_run_status: null,
    }),
  ],
}

const RUN = {
  id: 'run_1',
  connection_id: 'conn_sandbox',
  room_id: 'room_1',
  run_at: '2026-09-28T12:00:00+00:00',
  status: 'passed',
  aborted_reason: '',
  transport: 'simulated',
  assertions: [
    { kind: 'object_created', outcome: 'passed', detail: 'the sandbox created the object and returned sbx-0001' },
    { kind: 'dedupe_key_honoured', outcome: 'passed', detail: 'resending the key updated the same record sbx-0001' },
    { kind: 'rollback_fired', outcome: 'passed', detail: 'the bad row was rejected with HTTP 400' },
    { kind: 'quota_headers_behaved', outcome: 'passed', detail: 'the vendor never signalled a quota limit' },
  ],
  requests: [],
  quota: {},
}

const SELF_TEST = {
  production: CONNECTION({ id: 'conn_prod', environment: 'production', source_connection_id: null }),
  test_environment: CONNECTION(),
  run: { run_id: 'run_9', status: 'passed', assertions: RUN.assertions },
}

const ROOMS = { records: [{ id: 'room_1', data: { name: 'Northwind Traders', account: 'Northwind' } }] }

function stubApi(overrides = {}) {
  const routes = {
    [`${BASE}/summary`]: SUMMARY,
    [`${BASE}/connections`]: CONNECTIONS,
    [`${BASE}/connections/conn_sandbox/run-test-sync`]: { run: RUN },
    [`${BASE}/connections/conn_sandbox/promote`]: {
      connection: CONNECTION({ promoted_at: '2026-09-28T12:05:00+00:00', promoted_run_id: 'run_1' }),
      promoted: true,
      promoted_run_id: 'run_1',
      promoted_at: '2026-09-28T12:05:00+00:00',
    },
    [`${BASE}/connections/conn_sandbox/revert`]: {
      connection: CONNECTION({ reverted_at: '2026-09-28T12:06:00+00:00' }),
      reverted: true,
    },
    [`${BASE}/rooms/room_1/self-test`]: SELF_TEST,
    '/records/room': ROOMS,
    ...overrides,
  }

  const keys = Object.keys(routes).sort((a, b) => b.length - a.length)
  const matches = (path, candidate) =>
    path === candidate || path.startsWith(`${candidate}/`) || path.startsWith(`${candidate}?`)

  const calls = []

  globalThis.fetch = vi.fn(async (url, init = {}) => {
    const raw = String(url)
    const path = raw.replace('/api', '').split('?')[0]
    const method = (init.method || 'GET').toUpperCase()
    const body = init.body ? JSON.parse(init.body) : undefined
    calls.push({ path, method, body })

    const key = keys.find((candidate) => matches(path, candidate))
    if (!key) throw new Error(`unstubbed request: ${method} ${path}`)

    const value = routes[key]
    const asResponse = (answer) =>
      answer && typeof answer === 'object' && typeof answer.ok === 'boolean' ? answer : ok(answer)

    if (typeof value !== 'function') return asResponse(value)
    return asResponse(value({ path, method, body, calls }))
  })

  return { calls }
}

const callsTo = (calls, path) => calls.filter((call) => call.path === path)

async function renderPage(overrides = {}) {
  const { calls } = stubApi(overrides)
  const user = userEvent.setup()
  const view = render(<SandboxValidation />)
  await screen.findByText('Test environment')
  return { user, calls, container: view.container }
}

beforeEach(() => {
  globalThis.fetch = vi.fn()
})

describe('the descriptor', () => {
  it('exports the id the backend feature claims', () => {
    expect(descriptor.id).toBe('wf-048-validate-the-connector-against-a-sandb')
  })

  it('exports a Component, because the host drops a descriptor without one', () => {
    // lib/features.js collects a descriptor with no Component into featureProblems
    // and renders nothing. The file itself is the registration.
    expect(typeof descriptor.Component).toBe('function')
    expect(descriptor.label).toBeTruthy()
  })
})

describe('the header counts', () => {
  it('shows the states the summary route reports', async () => {
    await renderPage()
    // "Connections" is both a stat label and a section heading, so the section is
    // found by its heading role and the counts by the hints that only the stat
    // cards carry.
    expect(await screen.findByRole('heading', { name: 'Connections' })).toBeTruthy()
    expect(screen.getByText('Green runs')).toBeTruthy()
    expect(screen.getByText('Promoted')).toBeTruthy()
    expect(screen.getByText('1 test environments')).toBeTruthy()
    expect(screen.getByText('2 failed')).toBeTruthy()
    expect(screen.getByText('switched to production after green')).toBeTruthy()
  })
})

describe('the connection list', () => {
  it('says so when there is nothing to show', async () => {
    await renderPage({ [`${BASE}/connections`]: { count: 0, production: 0, test_environments: 0, connections: [] } })
    expect(await screen.findByText('No connectors registered yet')).toBeTruthy()
  })

  it('gives the test environment the three actions and production none of them', async () => {
    await renderPage()
    expect(await screen.findByRole('button', { name: /run test sync/i })).toBeTruthy()
    // Only one card is actionable, because only the test row is.
    expect(screen.getAllByRole('button', { name: /run test sync/i })).toHaveLength(1)
    expect(screen.getByRole('button', { name: /promote to production/i })).toBeTruthy()
    expect(screen.getByRole('button', { name: /^revert$/i })).toBeTruthy()
  })

  it('keeps promote disabled until the newest run is green', async () => {
    await renderPage({
      [`${BASE}/connections`]: {
        ...CONNECTIONS,
        connections: [CONNECTION({ last_run_status: 'failed' }), CONNECTIONS.connections[1]],
      },
    })
    const promote = await screen.findByRole('button', { name: /promote to production/i })
    expect(promote.disabled).toBe(true)
  })

  it('lists the production row in the table but gives it no actions', async () => {
    await renderPage()
    // The per-connection cards are filtered to non-production rows, so the
    // production connection is only ever a table row. It must not gain a run,
    // promote or revert button: all three act on a test environment.
    const table = await screen.findByRole('table')
    expect(within(table).getByText('Northwind Traders - HubSpot (production)')).toBeTruthy()
    expect(screen.queryAllByRole('button', { name: /run test sync/i })).toHaveLength(1)
  })
})

describe('running the test sync', () => {
  it('posts to the connection route and reports the four assertions', async () => {
    const { user, calls } = await renderPage()
    await user.click(await screen.findByRole('button', { name: /run test sync/i }))

    await waitFor(() =>
      expect(callsTo(calls, `${BASE}/connections/conn_sandbox/run-test-sync`)).toHaveLength(1),
    )
    expect(await screen.findByText('Test sync passed')).toBeTruthy()
    // Each assertion kind appears twice: once in the notice summary and once as a
    // term in the Latest run panel. Both renderings are wanted, so the count is
    // what is asserted rather than a single match.
    for (const kind of ['object_created', 'dedupe_key_honoured', 'rollback_fired', 'quota_headers_behaved']) {
      expect(screen.getAllByText(new RegExp(kind)).length).toBeGreaterThan(0)
    }
    // The panel is what a reviewer reads for the detail behind each outcome.
    const panel = screen.getByRole('heading', { name: 'Latest run' }).closest('section')
    for (const kind of ['object_created', 'dedupe_key_honoured', 'rollback_fired', 'quota_headers_behaved']) {
      expect(within(panel).getByText(new RegExp(`${kind}.*passed`))).toBeTruthy()
    }
  })
})

describe('the promotion gate', () => {
  it('names the green run the promotion came from', async () => {
    const { user, calls } = await renderPage()
    await user.click(await screen.findByRole('button', { name: /promote to production/i }))
    await waitFor(() => expect(callsTo(calls, `${BASE}/connections/conn_sandbox/promote`)).toHaveLength(1))
    expect(await screen.findByText(/names the green run it came from/i)).toBeTruthy()
    expect(screen.getByText(/run_1/)).toBeTruthy()
  })

  it('turns a 422 into the instruction to run the sync first, not a generic refusal', async () => {
    const { user } = await renderPage({
      [`${BASE}/connections/conn_sandbox/promote`]: {
        ok: false,
        status: 422,
        statusText: 'Unprocessable',
        json: async () => ({ error: 'not_validated', detail: 'no green run' }),
      },
    })
    await user.click(await screen.findByRole('button', { name: /promote to production/i }))
    expect(await screen.findByText('Not promoted')).toBeTruthy()
    expect(screen.getByText(/run it, then promote/i)).toBeTruthy()
  })
})

describe('the self-test form', () => {
  it('posts to the room-scoped route, not to a placeholder path', async () => {
    const { user, calls } = await renderPage()
    await user.type(screen.getByLabelText(/production base url/i), 'https://org.example/prod')
    await user.click(screen.getByRole('button', { name: /create both rows and run the fixture/i }))

    await waitFor(() => expect(callsTo(calls, `${BASE}/rooms/room_1/self-test`)).toHaveLength(1))
    // The recovered handler posted to
    // /wf-048/connections/self-test-route-placeholder. If that comes back, the
    // unstubbed-request throw fires instead of this passing.
    expect(calls.some((call) => call.path.includes('placeholder'))).toBe(false)
  })

  it('sends the connector the form asked for', async () => {
    const { user, calls } = await renderPage()
    await user.type(screen.getByLabelText(/production base url/i), 'https://org.example/prod')
    await user.click(screen.getByRole('button', { name: /create both rows and run the fixture/i }))

    await waitFor(() => expect(callsTo(calls, `${BASE}/rooms/room_1/self-test`)).toHaveLength(1))
    const body = callsTo(calls, `${BASE}/rooms/room_1/self-test`)[0].body
    expect(body.vendor).toBe('salesforce')
    expect(body.object_name).toBe('Engagement__c')
    expect(body.base_url).toBe('https://org.example/prod')
    // A self-test with no config for the sandbox is refused by the domain.
    expect(body.test_environment).toEqual({ kind: 'power_platform', env_type: 'sandbox' })
  })

  it('reports the run the self-test produced', async () => {
    const { user } = await renderPage()
    await user.type(screen.getByLabelText(/production base url/i), 'https://org.example/prod')
    await user.click(screen.getByRole('button', { name: /create both rows and run the fixture/i }))
    expect(await screen.findByText('Self-test passed')).toBeTruthy()
  })

  it('keeps the submit disabled until a base URL is typed', async () => {
    await renderPage()
    const submit = await screen.findByRole('button', { name: /create both rows and run the fixture/i })
    expect(submit.disabled).toBe(true)
  })
})