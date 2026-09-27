import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import RoomTemplates from '@/features/wf-001/room-templates/RoomTemplates.jsx'
import descriptor from '@/features/wf-001/room-templates/index.jsx'
import shim from '@/features/wf-001-room-templates/index.jsx'

/**
 * Tests for the WF-001 create-room wizard.
 *
 * Three things a page can get wrong that the backend tests cannot see:
 *
 * * **The wizard gates.** Step 3 must not be reachable without an account and a
 *   template, because those two are the workflow's own load-bearing bindings.
 * * **A refusal lands the operator somewhere they can act.** The shared
 *   `apiRequest` hands the page a status and an operator-facing message but not
 *   the server's machine-readable code, so the status-to-step mapping in
 *   `RoomTemplates.jsx` is load-bearing and is pinned here.
 * * **The Friendly URL preview is advisory.** It must show what the server will
 *   do, including the truncation, without promising to be authoritative.
 *
 * Stubs are keyed on this feature's own paths under `/wf-001`. The port moved the
 * branch's core-shaped `/api/rooms` and `/api/accounts` onto the router prefix, so
 * a stub keyed on the old path would fail on a fetch error rather than on the
 * behaviour under test. The prefix is written once, in `BASE`.
 */

const BASE = '/wf-001'

const TEMPLATES = {
  count: 2,
  templates: [
    {
      template_id: 'tpl_standard',
      template_version_id: 'tpl_standard_v1',
      name: 'Standard Digital Sales Room',
      description: 'The pre-configured layout.',
      sections: ['overview'],
      source: 'shipped',
    },
    {
      template_id: 'tpl_technical_review',
      template_version_id: 'tpl_technical_review_v1',
      name: 'Technical Review',
      description: 'Security and architecture material.',
      source: 'shipped',
    },
  ],
}

const ACCOUNTS = {
  count: 1,
  total: 1,
  accounts: [
    {
      id: 'account_northwind',
      collection: 'account',
      created_at: '2026-09-01T00:00:00.000+00:00',
      data: { name: 'Northwind Traders', domain: 'northwind.example', tier: 'enterprise' },
    },
  ],
}

const ROOMS = {
  rooms: [
    {
      id: 'room_existing',
      collection: 'room',
      created_at: '2026-09-20T00:00:00.000+00:00',
      data: {
        name: 'Contoso Health - Security Review',
        status: 'active',
        account_name: 'Contoso Health',
        account_id: 'account_contoso',
        friendly_url: 'contoso-security',
        template_id: 'tpl_standard',
        template_version_id: 'tpl_standard_v1',
        site_id: 'site_existing',
      },
    },
  ],
  count: 1,
  total: 1,
  status: 'active',
  limit: 100,
  offset: 0,
}

const CREATED = {
  id: 'room_new',
  collection: 'room',
  data: {
    name: 'Acme Evaluation',
    status: 'active',
    friendly_url: 'acme-evaluation',
    site_id: 'site_new',
    account_name: 'Northwind Traders',
  },
  site: { id: 'site_new', collection: 'site', data: { friendly_url: 'acme-evaluation' } },
}

const ROOM_NAME = 'Contoso Health - Security Review'

/** A response the shared `apiRequest` accepts. */
const ok = (body, status = 200) => ({ ok: true, status, statusText: 'OK', json: async () => body })

const refused = (status, detail) => ({
  ok: false,
  status,
  statusText: 'Error',
  json: async () => ({ error: 'refused', detail }),
})

/**
 * Answer every path this page reads, and let a test override one.
 *
 * `/api` is stripped so route keys can be written the way the feature's own
 * client writes them, without repeating the shared base. An unstubbed path
 * throws rather than answering 404, so a test cannot quietly pass against the
 * wrong response - a request to `/wf-002/rooms` fails loudly here.
 *
 * A route value may be a body or a function. A function's result is normalised
 * into a response if it is not already one, so a test can say `() => ROOMS` and
 * mean "answer this with that body" rather than having to spell out `ok` and
 * `json` - and a test that returns a refusal still gets the `!response.ok`
 * branch the real client would take.
 */
function stubApi(overrides = {}) {
  const routes = {
    [`${BASE}/room-templates`]: TEMPLATES,
    [`${BASE}/accounts`]: ACCOUNTS,
    [`${BASE}/rooms`]: ROOMS,
    ...overrides,
  }
  // Longest key first, and whole-segment only: a bare `startsWith` would let
  // `/wf-001/room-templates` answer a request for `/wf-001/room-templates-old`.
  const keys = Object.keys(routes).sort((a, b) => b.length - a.length)
  const matches = (path, candidate) =>
    path === candidate || path.startsWith(`${candidate}/`) || path.startsWith(`${candidate}?`)

  const calls = []

  globalThis.fetch = vi.fn(async (url, init = {}) => {
    const raw = String(url)
    const path = raw.replace('/api', '').split('?')[0]
    const query = raw.includes('?') ? raw.slice(raw.indexOf('?') + 1) : ''
    const method = (init.method || 'GET').toUpperCase()
    const body = init.body ? JSON.parse(init.body) : undefined
    calls.push({ path, method, query, body })

    const key = keys.find((candidate) => matches(path, candidate))
    if (!key) throw new Error(`unstubbed request: ${path}`)

    const value = routes[key]
    if (typeof value !== 'function') return ok(value, method === 'POST' ? 201 : 200)

    const answer = value({ path, method, query, body, calls })
    return answer && typeof answer.ok === 'boolean' ? answer : ok(answer, 200)
  })

  return { calls }
}

/** A rooms route that answers reads with `ROOMS` and writes with `write`. */
const roomsWith = (write) => ({ method }) => (method === 'POST' ? write() : ok(ROOMS))

/** The last request the page made to a path. */
const lastCallTo = (calls, path) => calls.filter((call) => call.path === path).at(-1)

/** Render and wait for the list to arrive, so no test races the first load. */
async function renderPage(routes) {
  const { calls } = stubApi(routes)
  const user = userEvent.setup()
  render(<RoomTemplates />)
  await screen.findByText(ROOM_NAME)
  return { user, calls }
}

/** Open the wizard and wait for step 1. */
async function openWizard(user) {
  await user.click(screen.getByRole('button', { name: 'New room' }))
  await screen.findByRole('radio', { name: /Northwind Traders/ })
}

/** Walk steps 1 and 2 and land on the name step. */
async function reachDetails(user) {
  await user.click(await screen.findByRole('radio', { name: /Northwind Traders/ }))
  await user.click(screen.getByRole('button', { name: /Next/ }))
  await screen.findByRole('radio', { name: /Standard Digital Sales Room/ })
  await user.click(screen.getByRole('button', { name: /Next/ }))
  await screen.findByLabelText('Room name')
}

/** Fill step 3 in and save, which is the only call the page makes that writes. */
async function saveRoom(user, name = 'Acme Evaluation') {
  await user.type(screen.getByLabelText('Room name'), name)
  await user.click(screen.getByRole('button', { name: /Save room/ }))
}

beforeEach(() => {
  globalThis.fetch = vi.fn()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('the descriptor', () => {
  it('exports the id the backend feature claims', () => {
    expect(descriptor.id).toBe('wf-001-room-templates')
  })

  it('is reachable through the folder the host actually globs', () => {
    // The host expands `src/features/*/index.jsx` - one folder deep - and the
    // brief puts the implementation two down. If the discovered folder ever
    // stops pointing at the same module, the nav silently loses the page, and
    // this is the test that notices.
    expect(shim).toBe(descriptor)
  })

  it('carries a glyph by path rather than editing the shared icon map', () => {
    expect(typeof descriptor.iconPath).toBe('string')
    expect(descriptor.iconPath.length).toBeGreaterThan(0)
  })
})

describe('the rooms list', () => {
  it('shows a room with the account, URL and site the workflow bound it to', async () => {
    await renderPage()
    const card = screen.getByText(ROOM_NAME).closest('li')

    expect(within(card).getByText('Contoso Health')).toBeInTheDocument()
    expect(within(card).getByText('/contoso-security')).toBeInTheDocument()
    expect(within(card).getByText('site_existing')).toBeInTheDocument()
  })

  it('defaults the status filter to Active, as the research says', async () => {
    const { calls } = await renderPage()

    expect(lastCallTo(calls, `${BASE}/rooms`).query).toContain('status=active')
    expect(screen.getByLabelText('Status')).toHaveValue('active')
  })

  it('sends the search term to the server rather than filtering the page itself', async () => {
    const { user, calls } = await renderPage()
    await user.type(screen.getByLabelText('Search rooms'), 'contoso')

    await waitFor(() => {
      expect(lastCallTo(calls, `${BASE}/rooms`).query).toContain('q=contoso')
    })
  })

  it('sends a status the operator picked', async () => {
    const { user, calls } = await renderPage()
    await user.selectOptions(screen.getByLabelText('Status'), 'archived')

    await waitFor(() => {
      expect(lastCallTo(calls, `${BASE}/rooms`).query).toContain('status=archived')
    })
  })
})

describe('wizard gating', () => {
  it('will not leave step 1 until an account is chosen', async () => {
    const { user } = await renderPage()
    await openWizard(user)

    expect(screen.getByRole('button', { name: /Next/ })).toBeDisabled()
  })

  it('says what selecting an account is for, in the researched words', async () => {
    const { user } = await renderPage()
    await openWizard(user)

    expect(
      screen.getByText(/The selected account determines the team members and contacts/),
    ).toBeInTheDocument()
  })

  it('walks account, then template, then name', async () => {
    const { user } = await renderPage()
    await openWizard(user)

    await user.click(await screen.findByRole('radio', { name: /Northwind Traders/ }))
    await user.click(screen.getByRole('button', { name: /Next/ }))
    expect(
      screen.getByText(/Use a pre-configured template to get started/),
    ).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Next/ }))
    expect(screen.getByLabelText('Room name')).toBeInTheDocument()
  })

  it('preselects the shipped standard template so the common case is one click', async () => {
    const { user } = await renderPage()
    await openWizard(user)
    await user.click(await screen.findByRole('radio', { name: /Northwind Traders/ }))
    await user.click(screen.getByRole('button', { name: /Next/ }))

    expect(screen.getByRole('radio', { name: /Standard Digital Sales Room/ })).toBeChecked()
  })

  it('marks the current step for assistive technology', async () => {
    const { user } = await renderPage()
    await openWizard(user)

    expect(document.querySelector('[aria-current="step"]')).toHaveTextContent('Account')
  })

  it('goes back a step without losing the account already chosen', async () => {
    const { user } = await renderPage()
    await openWizard(user)
    await user.click(await screen.findByRole('radio', { name: /Northwind Traders/ }))
    await user.click(screen.getByRole('button', { name: /Next/ }))
    await user.click(screen.getByRole('button', { name: 'Back' }))

    expect(await screen.findByRole('radio', { name: /Northwind Traders/ })).toBeChecked()
  })

  it('sends the account and the pinned template in one call', async () => {
    const { user, calls } = await renderPage({ [`${BASE}/rooms`]: roomsWith(() => ok(CREATED, 201)) })
    await openWizard(user)
    await reachDetails(user)
    await saveRoom(user)

    await waitFor(() => {
      const post = calls.find((call) => call.method === 'POST')
      expect(post.path).toBe(`${BASE}/rooms`)
      expect(post.body).toEqual({
        name: 'Acme Evaluation',
        account_id: 'account_northwind',
        template_id: 'tpl_standard',
      })
    })
  })

  it('sends an operator-typed friendly URL only when there is one', async () => {
    const { user, calls } = await renderPage({ [`${BASE}/rooms`]: roomsWith(() => ok(CREATED, 201)) })
    await openWizard(user)
    await reachDetails(user)
    await user.type(screen.getByLabelText('Room name'), 'Acme Evaluation')
    await user.type(screen.getByLabelText(/Friendly URL/), '/Acme Evaluation/')
    await user.click(screen.getByRole('button', { name: /Save room/ }))

    await waitFor(() => {
      expect(calls.find((call) => call.method === 'POST').body.friendly_url).toBe(
        '/Acme Evaluation/',
      )
    })
  })
})

describe('the friendly URL preview', () => {
  it('shows the slug the server will derive, accents folded and runs collapsed', async () => {
    const { user } = await renderPage()
    await openWizard(user)
    await reachDetails(user)
    await user.type(screen.getByLabelText('Room name'), 'Ácme   Evaluación - Phase 2')

    expect(
      screen.getByText('Leave blank to use /acme-evaluacion-phase-2. Must be unique across rooms.'),
    ).toBeInTheDocument()
  })

  it('says the server will shorten a slug past its limit, rather than promising 64+ characters', async () => {
    const { user } = await renderPage()
    await openWizard(user)
    await reachDetails(user)
    await user.type(screen.getByLabelText('Room name'), 'x'.repeat(120))

    expect(
      screen.getByText(`The server will shorten this to /${'x'.repeat(64)} and add a number if it is taken.`),
    ).toBeInTheDocument()
  })
})

describe('a refused save', () => {
  it('sends a 404 back to the account step, and says why', async () => {
    const { user } = await renderPage({
      [`${BASE}/rooms`]: roomsWith(() =>
        refused(404, 'no live account record with id account_northwind'),
      ),
    })
    await openWizard(user)
    await reachDetails(user)
    await saveRoom(user)

    await screen.findByText('no live account record with id account_northwind')
    expect(screen.getByText(/determines the team members and contacts/)).toBeInTheDocument()
  })

  it('keeps a 409 on the name step, where the URL is typed', async () => {
    const { user } = await renderPage({
      [`${BASE}/rooms`]: roomsWith(() =>
        refused(409, "friendly URL 'acme-evaluation' is already used by another room"),
      ),
    })
    await openWizard(user)
    await reachDetails(user)
    await saveRoom(user)

    await screen.findByText("friendly URL 'acme-evaluation' is already used by another room")
    expect(screen.getByLabelText('Room name')).toHaveValue('Acme Evaluation')
  })

  it('shows a 400 and keeps the room name the operator typed', async () => {
    const { user } = await renderPage({
      [`${BASE}/rooms`]: roomsWith(() => refused(400, 'a room name is required')),
    })
    await openWizard(user)
    await reachDetails(user)
    await saveRoom(user)

    await screen.findByText('a room name is required')
    expect(screen.getByLabelText('Room name')).toHaveValue('Acme Evaluation')
  })

  it('does not guess at a step for a failure the server did not describe', async () => {
    // 500 is not a refusal, so there is no step the operator can act on. The
    // wizard stays put and shows the message rather than inventing one.
    const { user } = await renderPage({ [`${BASE}/rooms`]: roomsWith(() => refused(500, 'boom')) })
    await openWizard(user)
    await reachDetails(user)
    await saveRoom(user)

    await screen.findByText('boom')
    expect(screen.getByLabelText('Room name')).toBeInTheDocument()
    expect(document.querySelector('[aria-current="step"]')).toHaveTextContent('Name and URL')
  })
})

describe('a successful save', () => {
  const created = { [`${BASE}/rooms`]: roomsWith(() => ok(CREATED, 201)) }

  it('confirms the room, its URL and its site, then refreshes the list', async () => {
    const { user, calls } = await renderPage(created)
    await openWizard(user)
    await reachDetails(user)
    await saveRoom(user)

    await screen.findByText('site site_new')
    expect(screen.getByText('/acme-evaluation')).toBeInTheDocument()
    await waitFor(() => {
      const reads = calls.filter(
        (call) => call.method === 'GET' && call.path === `${BASE}/rooms`,
      )
      expect(reads.length).toBeGreaterThan(1)
    })
  })

  it('closes the wizard and resets it, so the next room starts at step 1', async () => {
    const { user } = await renderPage(created)
    await openWizard(user)
    await reachDetails(user)
    await saveRoom(user)

    await waitFor(() => expect(screen.queryByLabelText('Room name')).not.toBeInTheDocument())

    await openWizard(user)
    expect(await screen.findByRole('radio', { name: /Northwind Traders/ })).not.toBeChecked()
    expect(screen.getByRole('button', { name: /Next/ })).toBeDisabled()
  })
})
