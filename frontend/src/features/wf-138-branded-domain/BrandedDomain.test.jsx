import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'

import BrandedDomain from '@/features/wf-138-branded-domain/BrandedDomain.jsx'
import descriptor from '@/features/wf-138-branded-domain/index.jsx'
import { brandedDomainApi, tokenVariables } from '@/features/wf-138-branded-domain/api.js'

/**
 * Tests for WF-138's page.
 *
 * What these pin is the behaviour the researched flow requires, and the three
 * things a branded domain can quietly get wrong:
 *
 *   1. **A host state is never carried by colour alone.** A state has to be on
 *      the page as a word, so the assertion is on the words rather than on a
 *      class.
 *   2. **An override is token to token.** There is no control that accepts a
 *      colour value, and the preview shows the custom property each pair
 *      becomes.
 *   3. **White-label mode swaps an identity.** The client is presented and the
 *      owner's name is withheld, and the server's own field message appears when
 *      the switch is turned on with no client to present.
 *
 * ## Why the fetch stub here is local
 *
 * The shared `stubApi` in `src/test/fixtures.js` keys on the path alone, and
 * this page reads and writes the *same* path: `GET .../brand` loads the form and
 * `PUT .../brand` saves it, and `GET/POST/DELETE .../domains` are three methods
 * on one path. A path-keyed stub cannot make a save fail while the load
 * succeeds, which is exactly the case under test, so this file carries its own
 * method-aware stub. It is not a copy of the shared one: `"METHOD path"` keys
 * win over bare `path` keys, and an unlisted request throws.
 */

const FAILURE = Symbol('wf138 failure')
const fail = (status, body) => ({ [FAILURE]: true, status, body })

function stubRoutes(routes) {
  const calls = []
  const keys = Object.keys(routes).sort((a, b) => b.length - a.length)
  const matches = (path, candidate) =>
    path === candidate || path.startsWith(`${candidate}/`) || path.startsWith(`${candidate}?`)

  globalThis.fetch = async (url, options = {}) => {
    const path = String(url).replace('/api', '')
    const method = options.method || 'GET'
    calls.push({ path, method, body: options.body })

    const key = keys.find((candidate) => {
      const [namedMethod, namedPath] = candidate.includes(' ')
        ? candidate.split(' ')
        : [null, candidate]
      if (namedMethod && namedMethod !== method) return false
      return matches(path, namedPath)
    })
    if (!key) throw new Error(`unstubbed request: ${method} ${path}`)

    const entry = routes[key]
    const failure = entry && typeof entry === 'object' && entry[FAILURE] ? entry : null
    const status = failure ? failure.status : 200
    const body = failure ? failure.body : entry

    return {
      ok: status >= 200 && status < 300,
      status,
      statusText: failure ? 'Error' : 'OK',
      json: async () => (typeof body === 'function' ? body() : body),
    }
  }

  return calls
}

const A = 'acct_a'
const B = 'acct_b'

const CONFIG = {
  cname_target: 'cname.dsr.test',
  base_url: 'http://127.0.0.1:8000',
  deployment_host: '127.0.0.1',
  record_type: 'CNAME',
  propagation_note: 'A new CNAME can take up to 24 hours to propagate.',
  cloudflare_note: 'If this domain is proxied by Cloudflare, set the CNAME to DNS only (proxy off).',
  format_note: 'The host must be in subdomain format, for example deals.acme.com.',
  token_note: 'A tenant override names another token, never a colour value.',
  cannot_verify: [
    'TLS issuance and renewal (ACME). A browser refuses an https URL without a certificate.',
    'A reverse proxy terminating TLS and forwarding the Host header.',
  ],
}

const SUMMARY = {
  brands: 2,
  white_labelled: 1,
  claims: 2,
  verified: 1,
  unverified: 1,
  failed: 0,
  released: 0,
  ambiguous_hosts: [],
  rooms_themed: 1,
  rooms_inheriting: 1,
  token_overrides: 1,
  events: 2,
  deployment_host: '127.0.0.1',
}

const VOCABULARY = {
  tenant_tokens: [
    'primary',
    'accent',
    'accent-bright',
    'accent-soft',
    'muted',
    'border-subtle',
    'info',
    'success',
    'warning',
    'destructive',
  ],
  product_tokens: ['background', 'surface', 'foreground', 'muted-foreground'],
  claim_states: ['unverified', 'verified', 'failed', 'released'],
  claim_expectations: {
    unverified: 'Not serving yet. A CNAME may take up to 24 hours to propagate.',
    verified: 'Serving: this brand answers on this host.',
    failed: 'Not serving. The last check failed.',
    released: 'Released. The host is free for another brand.',
  },
  event_kinds: [],
  decisions: {},
  unverifiable_here: [],
}

function brand(overrides = {}) {
  return {
    account_id: A,
    name: 'Northwind Studio',
    account_ref: 'Northwind Traders',
    owner_name: 'Northwind Studio',
    client_name: null,
    white_label: false,
    brand_revision: 2,
    presented: {
      presented: 'Northwind Studio',
      role: 'owner',
      white_labelled: false,
      withheld: null,
    },
    tokens: { accent: 'accent-bright' },
    css_variables: { '--color-accent': 'var(--color-accent-bright)' },
    hosts: [],
    cannot_verify: [],
    ...overrides,
  }
}

const LIVE_HOST = {
  id: 'c1',
  host: 'deals.acme.example',
  brand_id: A,
  state: 'verified',
  serving: true,
  expectation: VOCABULARY.claim_expectations.verified,
  cname_target: 'cname.dsr.test',
  observed: ['cname.dsr.test'],
}

const AWAITING_HOST = {
  id: 'c2',
  host: 'rooms.acme.example',
  brand_id: A,
  state: 'unverified',
  serving: false,
  expectation: VOCABULARY.claim_expectations.unverified,
  cname_target: 'cname.dsr.test',
  observed: [],
}

const ROOMS = {
  count: 2,
  records: [
    { id: 'room_1', collection: 'room', revision: 1, data: { name: 'Northwind Traders' } },
    { id: 'room_2', collection: 'room', revision: 1, data: { name: 'Contoso Health' } },
  ],
}

const THEMES = {
  account_id: A,
  rooms: [
    {
      theme_id: 'theme_1',
      room_ref: 'room_1',
      brand_id: A,
      tokens: { accent: 'accent-bright' },
      override: {},
      css_variables: { '--color-accent': 'var(--color-accent-bright)' },
      brand_revision: 2,
      inherited: true,
    },
  ],
}

const EVENTS = {
  account_id: A,
  events: [
    { id: 'e1', kind: 'brand_created', at: '2026-10-01T09:00:00.000+00:00', detail: 'Northwind', host: null },
    { id: 'e2', kind: 'domain_claimed', at: '2026-10-01T09:05:00.000+00:00', detail: 'verified', host: 'deals.acme.example' },
  ],
}

const REPORT = {
  host: 'deals.acme.example',
  cname_target: 'cname.dsr.test',
  ready: true,
  status: 'verified',
  brand_id: A,
  observed: ['cname.dsr.test'],
  checks: [
    { name: 'cname', label: 'CNAME points at cname.dsr.test', ok: true, detail: 'deals.acme.example resolves to cname.dsr.test' },
    { name: 'format', label: 'Subdomain format', ok: true, detail: 'The host is a subdomain and is well formed.' },
    { name: 'available', label: 'Not held by another brand', ok: true, detail: 'No other brand holds this host.' },
  ],
  cannot_verify: [],
}

function baseRoutes(overrides = {}) {
  return {
    '/records/room': ROOMS,
    '/wf-138/config': CONFIG,
    '/wf-138/summary': SUMMARY,
    '/wf-138/vocabulary': VOCABULARY,
    '/wf-138/accounts': { count: 2, accounts: [brand(), brand({ account_id: B, name: 'Fabrikam Advisory' })] },
    [`/wf-138/accounts/${A}/brand`]: brand(),
    [`/wf-138/accounts/${A}/domains`]: {
      account_id: A,
      state_expectations: VOCABULARY.claim_expectations,
      hosts: [LIVE_HOST, AWAITING_HOST],
    },
    [`/wf-138/accounts/${A}/rooms`]: THEMES,
    [`/wf-138/accounts/${A}/events`]: EVENTS,
    ...overrides,
  }
}

async function renderPage(routes = baseRoutes(), { withForm = true } = {}) {
  const calls = stubRoutes(routes)
  const user = userEvent.setup()
  const view = render(<BrandedDomain />)
  // Step 2 always renders; the account select only exists once a brand does.
  await screen.findByRole('heading', { name: /2\. Choose an account/ })
  if (withForm) await screen.findByLabelText('Owner name')
  return { user, calls, view }
}

beforeEach(() => {
  globalThis.fetch = undefined
})

describe('WF-138 descriptor', () => {
  it('exports the shape the host discovers', () => {
    expect(descriptor.id).toBe('wf-138-branded-domain')
    expect(descriptor.label).toBe('Branded domain')
    expect(descriptor.Component).toBe(BrandedDomain)
    // The glyph is not in the shared PATHS map, so it travels as a path and
    // components/ui.jsx stays unedited.
    expect(descriptor.iconPath).toBeTruthy()
    expect(descriptor.icon).toBeTruthy()
  })
})

describe('the deployment and its limits', () => {
  it('leads with the CNAME record the deployment is configured for', async () => {
    await renderPage()
    expect(screen.getAllByText('cname.dsr.test').length).toBeGreaterThan(0)
    expect(screen.getAllByText('CNAME').length).toBeGreaterThan(0)
    expect(screen.getAllByText(/24 hours/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/DNS only/).length).toBeGreaterThan(0)
  })

  it('states plainly what a verified host cannot prove', async () => {
    await renderPage()
    expect(
      screen.getByRole('heading', { name: /What this deployment cannot verify/ }),
    ).toBeInTheDocument()
    expect(screen.getAllByText(/TLS issuance and renewal/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/reverse proxy terminating TLS/).length).toBeGreaterThan(0)
  })

  it('numbers the steps in the researched order', async () => {
    await renderPage()
    const headings = screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent)
    expect(headings[0]).toMatch(/1\. Point a CNAME/)
    expect(headings[1]).toMatch(/2\. Choose an account/)
    expect(headings[2]).toMatch(/3\. Brand identity/)
    expect(headings[3]).toMatch(/4\. Brand tokens/)
    expect(headings[4]).toMatch(/5\. Hosts/)
    expect(headings[5]).toMatch(/6\. Rooms/)
  })

  it('shows the board numbers from the server rather than counting on the page', async () => {
    await renderPage()
    expect(screen.getByText('Brands', { selector: 'p' })).toBeInTheDocument()
    expect(screen.getByText('Hosts live', { selector: 'p' })).toBeInTheDocument()
    expect(screen.getByText('1 white-labelled')).toBeInTheDocument()
    expect(screen.getByText('1 awaiting DNS')).toBeInTheDocument()
  })
})

describe('choosing an account', () => {
  it('defaults to the first account and loads its brand', async () => {
    const { calls } = await renderPage()
    expect(calls.some((call) => call.path === `/wf-138/accounts/${A}/brand`)).toBe(true)
    expect(screen.getByLabelText('Owner name')).toHaveValue('Northwind Studio')
  })

  it('creates a brand and selects it', async () => {
    const created = brand({ account_id: 'acct_new', name: 'Contoso Health' })
    const { user, calls } = await renderPage(
      baseRoutes({ 'POST /wf-138/accounts': created }),
    )

    await user.type(screen.getByLabelText('New brand name'), 'Contoso Health')
    await user.click(screen.getByRole('button', { name: 'Create brand' }))

    await waitFor(() => {
      const posted = calls.find((call) => call.method === 'POST' && call.path === '/wf-138/accounts')
      expect(posted).toBeTruthy()
      expect(JSON.parse(posted.body)).toEqual({
        name: 'Contoso Health',
        owner_name: 'Contoso Health',
      })
    })
  })

  it('offers a way to start when there is no brand at all', async () => {
    await renderPage(baseRoutes({ '/wf-138/accounts': { count: 0, accounts: [] } }), {
      withForm: false,
    })
    expect(screen.getByText('No brands yet')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Create brand' })).toBeInTheDocument()
  })
})

describe('the owner identity and white-label mode', () => {
  it('shows who is presented and who is withheld', async () => {
    await renderPage()
    expect(screen.getByText('Presented to buyers')).toBeInTheDocument()
    expect(screen.getAllByText('Northwind Studio').length).toBeGreaterThan(0)
    expect(screen.getByText('Nothing is withheld')).toBeInTheDocument()
  })

  it('presents the client and withholds the owner when white-label mode is on', async () => {
    const white = brand({
      white_label: true,
      client_name: 'Fabrikam Logistics',
      presented: {
        presented: 'Fabrikam Logistics',
        role: 'client',
        white_labelled: true,
        withheld: 'Fabrikam Advisory',
      },
    })
    await renderPage(baseRoutes({ [`/wf-138/accounts/${A}/brand`]: white }))

    expect(screen.getAllByText('Fabrikam Logistics').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Fabrikam Advisory').length).toBeGreaterThan(0)
    expect(screen.getByText('client')).toBeInTheDocument()
  })

  it('saves the identity with the white-label switch and the client name', async () => {
    const { user, calls } = await renderPage(
      baseRoutes({ [`PUT /wf-138/accounts/${A}/brand`]: brand() }),
    )

    await user.click(screen.getByLabelText(/White-label mode/))
    await user.type(screen.getByLabelText('Client name'), 'Fabrikam Logistics')
    await user.click(screen.getByRole('button', { name: 'Save identity' }))

    await waitFor(() => {
      const saved = calls.find(
        (call) => call.method === 'PUT' && call.path === `/wf-138/accounts/${A}/brand`,
      )
      expect(saved).toBeTruthy()
      expect(JSON.parse(saved.body)).toEqual({
        owner_name: 'Northwind Studio',
        client_name: 'Fabrikam Logistics',
        white_label: true,
      })
    })
  })

  it("shows the server's reason when white-label mode has no client to present", async () => {
    const { user } = await renderPage(
      baseRoutes({
        [`PUT /wf-138/accounts/${A}/brand`]: fail(422, {
          error: 'branded_domain_invalid',
          detail: 'The brand could not be read.',
          errors: {
            client_name:
              "White-label mode swaps the owner identity, so it needs the client's name to present.",
          },
        }),
      }),
    )

    await user.click(screen.getByLabelText(/White-label mode/))
    await user.click(screen.getByRole('button', { name: 'Save identity' }))

    expect(await screen.findByText(/needs the client's name to present/)).toBeInTheDocument()
  })

  it('reports a saved identity rather than staying silent', async () => {
    const { user } = await renderPage(
      baseRoutes({ [`PUT /wf-138/accounts/${A}/brand`]: brand() }),
    )
    await user.click(screen.getByRole('button', { name: 'Save identity' }))
    expect(await screen.findByText('Saved')).toBeInTheDocument()
  })
})

describe('brand tokens', () => {
  it('offers a token name for every control, never a colour value', async () => {
    const { view } = await renderPage()
    const container = view.container

    expect(container.querySelector('#token-accent').tagName).toBe('SELECT')
    expect(container.querySelector('#token-primary').tagName).toBe('SELECT')
    expect(container.querySelector('input[type="color"]')).toBeNull()
    expect(container.querySelector('#token-accent').options[0].textContent).toBe(
      'Product default',
    )
  })

  it('sends the pair it was given and previews the custom property it becomes', async () => {
    const { user, calls } = await renderPage(
      baseRoutes({ [`PUT /wf-138/accounts/${A}/tokens`]: brand() }),
    )

    await user.selectOptions(screen.getByLabelText('primary'), 'info')
    await user.click(screen.getByRole('button', { name: 'Save tokens' }))

    await waitFor(() => {
      const saved = calls.find(
        (call) => call.method === 'PUT' && call.path === `/wf-138/accounts/${A}/tokens`,
      )
      expect(saved).toBeTruthy()
      // The stored pair survives, and the new one is added: an override is a
      // refinement, not a replacement of the whole map.
      expect(JSON.parse(saved.body)).toEqual({ accent: 'accent-bright', primary: 'info' })
    })

    expect(screen.getByText('--color-primary: var(--color-info)')).toBeInTheDocument()
  })

  it("shows the server's refusal of a colour value", async () => {
    const { user } = await renderPage(
      baseRoutes({
        [`PUT /wf-138/accounts/${A}/tokens`]: fail(422, {
          error: 'branded_domain_invalid',
          detail: 'The token map could not be read.',
          errors: {
            'tokens.accent':
              "'#22c55e' is not a token. Name another token, not a colour value.",
          },
        }),
      }),
    )

    await user.click(screen.getByRole('button', { name: 'Save tokens' }))
    expect(await screen.findByText(/not a colour value/)).toBeInTheDocument()
  })

  it('renders a token map as custom properties and nothing else', () => {
    expect(tokenVariables({ accent: 'accent-bright' })).toEqual({
      '--color-accent': 'var(--color-accent-bright)',
    })
    expect(tokenVariables({})).toEqual({})
  })
})

describe('hosts', () => {
  it('verifies a host and reports every check in words', async () => {
    const { user, calls } = await renderPage(
      baseRoutes({ [`POST /wf-138/accounts/${A}/domains/verify`]: REPORT }),
    )

    await user.type(screen.getByLabelText('Host'), 'deals.acme.example')
    await user.click(screen.getByRole('button', { name: 'Verify host' }))

    await waitFor(() => {
      const posted = calls.find((call) => call.path.endsWith('/domains/verify'))
      expect(posted).toBeTruthy()
      expect(JSON.parse(posted.body)).toEqual({ host: 'deals.acme.example' })
    })

    const list = await screen.findByRole('list', { name: 'Host verification checks' })
    expect(within(list).getAllByText('Pass')).toHaveLength(3)
    expect(within(list).getByText(/resolves to cname.dsr.test/)).toBeInTheDocument()
  })

  it('shows a failed check as a failure, not as a missing check', async () => {
    const failed = {
      ...REPORT,
      ready: false,
      status: 'failed',
      checks: [
        { name: 'cname', label: 'CNAME points at cname.dsr.test', ok: false, detail: 'pilot.acme.example does not resolve yet' },
        { name: 'format', label: 'Subdomain format', ok: true, detail: 'Well formed.' },
        { name: 'available', label: 'Not held by another brand', ok: true, detail: 'Free.' },
      ],
    }
    const { user } = await renderPage(
      baseRoutes({ [`POST /wf-138/accounts/${A}/domains/verify`]: failed }),
    )

    await user.type(screen.getByLabelText('Host'), 'pilot.acme.example')
    await user.click(screen.getByRole('button', { name: 'Verify host' }))

    const list = await screen.findByRole('list', { name: 'Host verification checks' })
    expect(within(list).getByText('Fail')).toBeInTheDocument()
    expect(screen.getAllByText('Check failed').length).toBeGreaterThan(0)
  })

  it('attaches a host, and sends force only when the operator ticked it', async () => {
    const { user, calls } = await renderPage(
      baseRoutes({ [`POST /wf-138/accounts/${A}/domains`]: { claim: LIVE_HOST, report: REPORT } }),
    )

    await user.type(screen.getByLabelText('Host'), 'deals.acme.example')
    await user.click(screen.getByRole('button', { name: 'Attach host' }))
    await waitFor(() => {
      const posted = calls.find(
        (call) => call.method === 'POST' && call.path.startsWith(`/wf-138/accounts/${A}/domains`),
      )
      expect(posted).toBeTruthy()
      expect(posted.path).not.toContain('force=true')
    })

    calls.length = 0
    await user.click(screen.getByLabelText(/Save while the CNAME is still propagating/))
    await user.click(screen.getByRole('button', { name: 'Attach host' }))
    await waitFor(() => {
      const posted = calls.find(
        (call) =>
          call.method === 'POST' &&
          call.path.startsWith(`/wf-138/accounts/${A}/domains`) &&
          !call.path.endsWith('/verify'),
      )
      expect(posted.path).toContain('force=true')
    })
  })

  it('names every host state in words, so nothing is carried by colour alone', async () => {
    await renderPage()
    expect(screen.getByText('Live')).toBeInTheDocument()
    expect(screen.getByText('Awaiting DNS')).toBeInTheDocument()
    expect(screen.getAllByText('deals.acme.example').length).toBeGreaterThan(0)
    expect(screen.getByText(VOCABULARY.claim_expectations.unverified)).toBeInTheDocument()
  })

  it('releases a host by name, so the query carries the host', async () => {
    const { user, calls } = await renderPage()
    await user.click(screen.getByRole('button', { name: 'Release deals.acme.example' }))

    await waitFor(() => {
      const deleted = calls.find((call) => call.method === 'DELETE')
      expect(deleted).toBeTruthy()
      expect(deleted.path).toContain('host=deals.acme.example')
    })
  })

  it("shows the server's reason when a host belongs to another brand", async () => {
    const { user } = await renderPage(
      baseRoutes({
        [`POST /wf-138/accounts/${A}/domains`]: fail(409, {
          error: 'brand_conflict',
          reason: 'host_taken',
          detail: 'deals.acme.example is already held by another brand',
        }),
      }),
    )

    await user.type(screen.getByLabelText('Host'), 'deals.acme.example')
    await user.click(screen.getByRole('button', { name: 'Attach host' }))

    expect(await screen.findByText('Another brand already holds that host')).toBeInTheDocument()
    expect(screen.getByText(/Release it there before claiming it here/)).toBeInTheDocument()
  })

  it('re-checks every host in one call', async () => {
    const { user, calls } = await renderPage()
    await user.click(screen.getByRole('button', { name: 'Re-check every host' }))
    await waitFor(() => {
      expect(calls.some((call) => call.path.endsWith('/domains/recheck'))).toBe(true)
    })
  })
})

describe('rooms', () => {
  it('shows each room with its brand revision and whether it inherits', async () => {
    await renderPage()
    expect(screen.getAllByText('Northwind Traders').length).toBeGreaterThan(0)
    expect(screen.getByText('revision 2')).toBeInTheDocument()
    expect(screen.getByText('inherits the brand')).toBeInTheDocument()
    expect(
      screen.getAllByText('--color-accent: var(--color-accent-bright)').length,
    ).toBeGreaterThan(0)
  })

  it('attaches a room with an accent override when one is chosen', async () => {
    const { user, calls } = await renderPage(
      baseRoutes({
        'POST /wf-138/rooms/room_2/brand': { theme: THEMES.rooms[0], brand: brand() },
      }),
    )

    await user.selectOptions(screen.getByLabelText('Room'), 'room_2')
    await user.selectOptions(screen.getByLabelText('Room override for the accent token'), 'accent-soft')
    await user.click(screen.getByRole('button', { name: 'Attach room' }))

    await waitFor(() => {
      const posted = calls.find((call) => call.method === 'POST' && call.path === '/wf-138/rooms/room_2/brand')
      expect(posted).toBeTruthy()
      expect(JSON.parse(posted.body)).toEqual({ account_id: A, tokens: { accent: 'accent-soft' } })
    })
  })

  it('applies the brand to every room through its own route', async () => {
    const { user, calls } = await renderPage(
      baseRoutes({
        [`POST /wf-138/accounts/${A}/propagate`]: {
          brand_id: A,
          brand_revision: 2,
          rooms: 1,
          updated: 1,
          rows: [],
        },
      }),
    )

    await user.click(screen.getByRole('button', { name: 'Apply the brand to every room' }))
    expect(await screen.findByText('Saved')).toBeInTheDocument()
    await waitFor(() => {
      expect(calls.some((call) => call.path.endsWith('/propagate'))).toBe(true)
    })
  })
})

describe('the change log', () => {
  it('names each brand event in words', async () => {
    await renderPage()
    expect(screen.getByText('Brand created')).toBeInTheDocument()
    expect(screen.getByText('Host attached')).toBeInTheDocument()
  })
})

describe('the empty and error states', () => {
  it('tells the operator when no room has joined the brand', async () => {
    await renderPage(baseRoutes({ [`/wf-138/accounts/${A}/rooms`]: { account_id: A, rooms: [] } }))
    expect(screen.getByText(/No room has joined this brand yet/)).toBeInTheDocument()
  })

  it('offers a retry when the deployment config cannot load', async () => {
    stubRoutes({ '/wf-138/config': fail(500, { detail: 'no config' }) })
    render(<BrandedDomain />)

    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Retry/ })).toBeInTheDocument()
  })
})

describe('the api wrapper', () => {
  it('exposes the calls the page uses', () => {
    for (const name of [
      'config',
      'accounts',
      'createAccount',
      'saveBrand',
      'saveTokens',
      'domains',
      'verifyHost',
      'attachHost',
      'releaseHost',
      'recheckHosts',
      'propagate',
      'attachRoom',
      'rooms',
      'resolve',
    ]) {
      expect(typeof brandedDomainApi[name]).toBe('function')
    }
  })
})
