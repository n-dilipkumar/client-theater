import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { leadQuery, refusalMessage, visitorApi } from '@/features/wf-031-identify-anonymous-web-visitors-as-com/api'
import descriptor, {
  default as descriptorAgain,
} from '@/features/wf-031-identify-anonymous-web-visitors-as-com/index.jsx'
import Page from '@/features/wf-031-identify-anonymous-web-visitors-as-com/IdentifiedCompanies'

/**
 * Tests for the WF-031 identified-companies page.
 *
 * Six things the backend tests cannot see, and each one is a way this page was
 * previously wrong:
 *
 * * **The page is discovered at all.** `lib/features.js` globs
 *   `src/features/*\/index.jsx`. A branch once shipped the component and
 *   `primitives.jsx` with no `index.jsx`, so the glob never matched and the build
 *   was green while the product carried zero occurrences of the ticket. The
 *   descriptor is pinned here so that file cannot go missing without a red test.
 * * **The descriptor's id matches the backend feature id.** `App.jsx` keys its nav
 *   on it and the backend registry reports it, so a drift shows up as a page with
 *   no server behind it.
 * * **The capture form has no field for a person.** "exclusively company-level
 *   identification rather than tracking individual users." A form that offered one
 *   would contradict the stance the server enforces, and the server would refuse
 *   it. The test asserts the rendered labels rather than the implementation.
 * * **The three condition labels are the vendor's own words.** "Exact ... Contains
 *   ... Starts with". A picker that showed `starts_with` alone would hide which
 *   condition a seller had chosen.
 * * **A page definition carrying a domain is refused with a message that says
 *   what to type instead.** The server's 422 detail is right; a page that rendered
 *   it raw would leave the seller guessing.
 * * **An empty lead list says so.** A filtered list with no rows must not render an
 *   empty panel.
 *
 * Stubs are keyed on this feature's own paths. An unstubbed path throws, so a test
 * cannot quietly pass against the wrong response.
 */

const PREFIX = '/api/wf-031'

const ok = (body, status = 200) => ({
  ok: true,
  status,
  statusText: 'OK',
  json: async () => body,
})

/**
 * A refused response.
 *
 * `ok: false` is the whole point: `apiRequest` reads it, reads the body for
 * `detail`, puts the status on the Error and throws. A stub that answered a
 * refusal with `ok: true` would let the page's error branch be asserted against a
 * success, and the test would pass while proving nothing.
 */
const err = (body, status = 422) => ({
  ok: false,
  status,
  statusText: 'Unprocessable Entity',
  json: async () => body,
})

const VOCABULARY = {
  identification_stance:
    'Albacross focuses on exclusively company-level identification rather than tracking individual users.',
  company_level_only: true,
  capture_parameters: [
    { name: 'ip_address', label: 'IP address', note: 'At least one of the two is required.' },
    { name: 'country', label: 'Country', note: 'At least one of the two is required.' },
    { name: 'network', label: 'Network', note: 'At least one of the two is required.' },
  ],
  match_conditions: [
    { name: 'exact', label: 'Exact', note: 'Character for character.' },
    { name: 'contains', label: 'Contains', note: 'Anywhere inside.' },
    { name: 'starts_with', label: 'Starts with', note: 'Begins with.' },
  ],
  company_fields: ['name', 'website', 'address', 'size', 'contacts'],
  contact_fields: ['name', 'role'],
  lead_filters: ['page', 'segment', 'tag', 'icp', 'country', 'size'],
  ranking: ['page_views', 'last_visit_at', 'company_key'],
  downstream_surfaces: [
    { surface: 'Workflows', scope: 'section 17 of the research file', note: 'Not built here.' },
    { surface: 'Auto-engage', scope: 'campaign builder', note: 'Not built here.' },
  ],
}

const INFERENCES = {
  count: 2,
  inferences: [
    {
      id: 'what_identifies_a_company',
      question: 'What turns an anonymous request into a company?',
      reading: 'The network, and an exact address already on file before the network.',
      why: 'Both are network-level facts.',
      change: 'find_company',
      risk: 'Two companies behind one network are one record.',
    },
    {
      id: 'ranking_keys',
      question: 'What ranks the lead list?',
      reading: 'Page views, then last visit, then company key.',
      why: 'The research names no key.',
      change: 'build_rows',
      risk: 'A seller wanting recency first gets the opposite order.',
    },
  ],
}

const INSTALLATIONS = {
  count: 1,
  installations: [
    { id: 'inst_1', client_id: 'cli-acme', site: 'https://acme.example', installed_at: null },
  ],
}

const PAGES = {
  count: 3,
  client_id: 'cli-acme',
  pages: [
    {
      id: 'pg_article',
      revision: 1,
      client_id: 'cli-acme',
      name: 'Read the conversion article',
      path: '/newsroom/converting-the-unconverted-article',
      condition: 'starts_with',
      condition_label: 'Starts with',
    },
    {
      id: 'pg_price',
      revision: 1,
      client_id: 'cli-acme',
      name: 'Asked for a price',
      path: '/pricing',
      condition: 'exact',
      condition_label: 'Exact',
    },
    {
      id: 'pg_tree',
      revision: 1,
      client_id: 'cli-acme',
      name: 'Read anything under pricing',
      path: '/pricing',
      condition: 'contains',
      condition_label: 'Contains',
    },
  ],
}

const COMPANY = (overrides = {}) => ({
  id: 'rec_1',
  revision: 1,
  company_key: '203.0.113.0-24-9e6315',
  name: 'Northwind Traders',
  website: 'https://northwind.example',
  address: '4 Shipley Lane, Manchester',
  size: '1000+',
  contacts: [{ name: 'Dana Kelly', role: 'CRO' }],
  segment: 'enterprise',
  tags: ['in-market', 'tail-lights'],
  countries: ['GB'],
  page_views: 3,
  distinct_paths: 2,
  contact_count: 1,
  first_seen_at: '2026-09-01T09:00:00+00:00',
  last_visit_at: '2026-10-01T09:00:00+00:00',
  known_ips: ['203.0.113.11'],
  known_networks: ['203.0.113.0/24'],
  identified_from: 'capture',
  matched_pages: ['pg_article', 'pg_price'],
  visits_href: '/companies/203.0.113.0-24-9e6315/visits',
  downstream: [{ surface: 'Workflows' }, { surface: 'Auto-engage' }],
  ...overrides,
})

const LEADS = (overrides = {}) => ({
  filters: {
    pages: [],
    segment: '',
    tags: [],
    icp: '',
    country: '',
    size: '',
    limit: 50,
  },
  summary: {
    companies: 1,
    page_views: 3,
    with_contact_candidates: 1,
    countries: ['GB'],
    sizes: ['1000+'],
    researched_fields: ['name', 'website', 'address', 'size', 'contacts'],
  },
  pages: PAGES.pages,
  companies: [COMPANY()],
  ...overrides,
})

const EMPTY_LEADS = LEADS({ summary: { companies: 0, page_views: 0, with_contact_candidates: 0, countries: [], sizes: [] }, companies: [] })

const CAPTURED = {
  company_key: '203.0.113.0-24-9e6315',
  company_created: true,
  identified: 'company',
  company_level_only: true,
  visit_id: 'vis_1',
  path: '/newsroom/converting-the-unconverted-article',
  captured_at: '2026-10-04T09:00:00+00:00',
  client_id: 'cli-acme',
  company: COMPANY({ page_views: 1, matched_pages: [], contacts: [] }),
  matched_pages: [PAGES.pages[0]],
  matched_page_ids: ['pg_article'],
  matches: true,
}

/** Every route the page needs, stubbed with an answer the test can vary. */
function stubBase(overrides = {}) {
  const routes = {
    [`${PREFIX}/vocabulary`]: ok(VOCABULARY),
    [`${PREFIX}/inferences`]: ok(INFERENCES),
    [`${PREFIX}/installations`]: ok(INSTALLATIONS),
    [`${PREFIX}/pages`]: ok(PAGES),
    [`${PREFIX}/icps`]: ok({ count: 1, profiles: [{ id: 'icp_1', name: 'Large', sizes: ['1000+'], countries: [] }] }),
    [`${PREFIX}/companies`]: ok(LEADS()),
    ...overrides,
  }
  global.fetch = vi.fn(async (url, options = {}) => {
    const text = String(url)
    // Longest prefix first: `/companies/x` would otherwise shadow
    // `/companies/x/visits`, and the drill-down would silently render a company
    // record where a page-visit roll-up belongs.
    const entry = Object.keys(routes)
      .filter((path) => text.startsWith(path))
      .sort((left, right) => right.length - left.length)[0]
    if (!entry) throw new Error(`unstubbed route: ${url}`)
    const answer = routes[entry]
    return typeof answer === 'function' ? answer(url, options) : answer
  })
  return routes
}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe('the descriptor', () => {
  it('is a default export with the contract fields', () => {
    expect(descriptor).toEqual(descriptorAgain)
    expect(descriptor.id).toBe('wf-031-identify-anonymous-web-visitors-as-com')
    expect(typeof descriptor.label).toBe('string')
    expect(typeof descriptor.icon).toBe('string')
    expect(typeof descriptor.Component).toBe('function')
  })

  it('matches the backend feature id exactly', () => {
    // The two halves of the feature are findable by one name, and App.jsx keys
    // its nav on this id while the backend registry reports it.
    expect(descriptor.id).toBe('wf-031-identify-anonymous-web-visitors-as-com')
  })

  it('passes a glyph path so the shared PATHS map is not edited', () => {
    expect(descriptor.iconPath).toMatch(/^M/)
    expect(descriptor.iconPath.length).toBeGreaterThan(20)
    expect(descriptor.iconPath).not.toContain('<')
  })

  it('orders itself beside the other buyer-intent pages', () => {
    expect(descriptor.order).toBeGreaterThan(300)
    expect(descriptor.order).toBeLessThan(340)
  })
})

describe('the api module', () => {
  it('repeats a list filter rather than joining it', () => {
    const query = leadQuery({ page: ['pg_a', 'pg_b'], tag: ['x'], segment: '', icp: 'icp_1', country: '', size: '', limit: 50 })
    const search = new URLSearchParams()
    for (const key of ['page', 'tag']) {
      for (const value of query[key]) search.append(key, value)
    }
    expect(search.getAll('page')).toEqual(['pg_a', 'pg_b'])
    expect(query.segment).toBe('')
  })

  it('explains the domain refusal in the words of the research', () => {
    const error = Object.assign(
      new Error("'https://acme.example/pricing' carries a domain. Enter the path without the domain"),
      { status: 422 },
    )
    expect(refusalMessage(error)).toContain('without the domain')
  })

  it('explains a personal-data refusal', () => {
    const error = Object.assign(
      new Error("'visitor_id' identifies an individual. This workflow identifies companies only"),
      { status: 422 },
    )
    expect(refusalMessage(error)).toContain('companies, not people')
  })

  it('explains an uninstalled client', () => {
    const error = Object.assign(
      new Error("no tracking snippet is installed under client id 'cli-x'"),
      { status: 404 },
    )
    expect(refusalMessage(error)).toContain('Install the tracking snippet')
  })

  it('falls back to the server detail for anything else', () => {
    expect(refusalMessage(new Error('boom'))).toBe('boom')
  })

  it('has a method for every call the page makes', () => {
    for (const name of [
      'vocabulary',
      'inferences',
      'installations',
      'install',
      'capture',
      'pages',
      'definePage',
      'amendPage',
      'dropPage',
      'profiles',
      'saveProfile',
      'dropProfile',
      'companies',
      'company',
      'addCompany',
      'amendCompany',
      'visits',
      'companyPages',
    ]) {
      expect(typeof visitorApi[name]).toBe('function')
    }
  })
})

describe('the lead list', () => {
  it('renders the researched fields for a company', async () => {
    stubBase()
    render(<Page />)
    expect(await screen.findByText('Northwind Traders')).toBeInTheDocument()
    expect(screen.getByText('1000+')).toBeInTheDocument()
    expect(screen.getByText('203.0.113.0-24-9e6315')).toBeInTheDocument()
  })

  it('names the ranking it applied, because the research names none', async () => {
    stubBase()
    render(<Page />)
    await screen.findByText('Northwind Traders')
    expect(screen.getByText(/ranked by page_views, last_visit_at, company_key/)).toBeInTheDocument()
  })

  it('offers the three conditions with the vendor labels on every page', async () => {
    stubBase()
    render(<Page />)
    await screen.findByText('Northwind Traders')
    expect(screen.getByText('Starts with on /newsroom/converting-the-unconverted-article')).toBeInTheDocument()
    expect(screen.getByText('Exact on /pricing')).toBeInTheDocument()
    expect(screen.getByText('Contains on /pricing')).toBeInTheDocument()
  })

  it('sends the Pages filter as repeated parameters', async () => {
    const user = userEvent.setup()
    stubBase()
    render(<Page />)
    await screen.findByText('Northwind Traders')
    await user.click(screen.getByLabelText(/Read the conversion article/))
    await waitFor(() => {
      const called = global.fetch.mock.calls.filter(([url]) => String(url).startsWith(`${PREFIX}/companies`))
      expect(called.some(([url]) => String(url).includes('page=pg_article'))).toBe(true)
    })
  })

  it('sends the segment and the size as filters', async () => {
    const user = userEvent.setup()
    stubBase()
    render(<Page />)
    await screen.findByText('Northwind Traders')
    await user.type(screen.getByLabelText('Segment'), 'enterprise')
    await user.type(screen.getByLabelText('Size'), '1000+')
    await waitFor(() => {
      const urls = global.fetch.mock.calls
        .map(([url]) => String(url))
        .filter((url) => url.startsWith(`${PREFIX}/companies`))
      expect(urls.some((url) => url.includes('segment=enterprise') && url.includes('size=1000%2B'))).toBe(true)
    })
  })

  it('says so when the filter matches nothing', async () => {
    stubBase({ [`${PREFIX}/companies`]: ok(EMPTY_LEADS) })
    render(<Page />)
    expect(await screen.findByText('No company matches this filter')).toBeInTheDocument()
  })

  it('shows which pages each company satisfied', async () => {
    stubBase()
    render(<Page />)
    await screen.findByText('Northwind Traders')
    const table = screen.getByRole('table')
    expect(within(table).getByText('Read the conversion article')).toBeInTheDocument()
    expect(within(table).getByText('Asked for a price')).toBeInTheDocument()
  })
})

describe('the company drill-down', () => {
  async function open() {
    const user = userEvent.setup()
    stubBase({
      [`${PREFIX}/companies/203.0.113.0-24-9e6315`]: ok(COMPANY()),
      [`${PREFIX}/companies/203.0.113.0-24-9e6315/visits`]: ok({
        total: 3,
        top_paths: [['/pricing', 2], ['/security', 1]],
        visits: [],
      }),
      [`${PREFIX}/companies/203.0.113.0-24-9e6315/pages`]: ok({ count: 2, pages: PAGES.pages }),
    })
    render(<Page />)
    await screen.findByText('Northwind Traders')
    await user.click(screen.getByRole('button', { name: 'Open' }))
    await screen.findByText('4 Shipley Lane, Manchester')
    return user
  }

  it('shows the five researched fields', async () => {
    await open()
    expect(screen.getByText('4 Shipley Lane, Manchester')).toBeInTheDocument()
    expect(screen.getByText('https://northwind.example')).toBeInTheDocument()
    // The size appears in the lead row and twice in the drill-down, so assert the
    // count rather than reaching for one node: three places and no more.
    expect(screen.getAllByText('1000+')).toHaveLength(3)
    // The address field is pre-filled with the researched address, which is the
    // clearest single statement that the five fields are editable here.
    expect(screen.getByLabelText('Address')).toHaveValue('4 Shipley Lane, Manchester')
    expect(screen.getByLabelText('Name')).toHaveValue('Northwind Traders')
  })

  it('shows the contacts with their role, not as a person record', async () => {
    await open()
    const contacts = within(screen.getByRole('list', { name: 'Employees and contacts' }))
    expect(contacts.getByText('Dana Kelly')).toBeInTheDocument()
    expect(contacts.getByText(/CRO/)).toBeInTheDocument()
    // The whole point of the researched stance: a contact is a name and a role
    // attached to a company, and there is no address anywhere on the row.
    expect(contacts.queryByText(/@/)).toBeNull()
  })

  it('shows the pages the company satisfied with their condition', async () => {
    await open()
    expect(screen.getByText('Read the conversion article (Starts with)')).toBeInTheDocument()
  })

  it('shows the most read paths', async () => {
    await open()
    expect(screen.getByText('/security')).toBeInTheDocument()
  })

  it('saves the detail and says so', async () => {
    const user = await open()
    await user.click(screen.getByLabelText('Address'))
    await user.clear(screen.getByLabelText('Address'))
    await user.type(screen.getByLabelText('Address'), '5 Bridge Street, Leeds')
    await user.click(screen.getByRole('button', { name: 'Save detail' }))
    expect(await screen.findByText(/Saved\./)).toBeInTheDocument()
    const patched = global.fetch.mock.calls.find(([, options]) => options?.method === 'PATCH')
    expect(patched).toBeTruthy()
    expect(JSON.parse(patched[1].body).address).toBe('5 Bridge Street, Leeds')
  })

  it('shows the refusal message when the server refuses a field', async () => {
    const user = await open()
    global.fetch = vi.fn(async (url, options = {}) => {
      if (options?.method === 'PATCH') {
        return err({ error: 'invalid_company_detail', detail: 'name cannot be empty.' }, 422)
      }
      return stubBaseFactory(url)
    })
    await user.click(screen.getByRole('button', { name: 'Save detail' }))
    expect(await screen.findByText(/cannot be empty/)).toBeInTheDocument()
  })
})

describe('the Pages tab', () => {
  it('adds a page with a path and a condition', async () => {
    const user = userEvent.setup()
    let posted = null
    stubBase({
      [`${PREFIX}/pages`]: (url, options) => {
        if (options?.method === 'POST') {
          posted = JSON.parse(options.body)
          return ok({ id: 'pg_new', ...posted }, 201)
        }
        return ok(PAGES)
      },
    })
    render(<Page />)
    await screen.findByText('Northwind Traders')
    await user.click(screen.getByRole('button', { name: 'Pages' }))
    await user.type(screen.getByLabelText('Name'), 'Read the security pack')
    await user.type(screen.getByLabelText('URL path'), '/security')
    await user.selectOptions(screen.getByLabelText('Match condition'), 'starts_with')
    await user.click(screen.getByRole('button', { name: 'Add page' }))
    await waitFor(() => expect(posted).toBeTruthy())
    expect(posted).toEqual({
      name: 'Read the security pack',
      path: '/security',
      condition: 'starts_with',
    })
  })

  it('explains a domain in a path instead of showing the raw refusal', async () => {
    const user = userEvent.setup()
    stubBase({
      [`${PREFIX}/pages`]: (url, options) => {
        if (options?.method === 'POST') {
          return err(
            {
              error: 'path_carries_a_domain',
              detail:
                "'https://acme.example/pricing' carries a domain. Enter the path without the domain",
            },
            422,
          )
        }
        return ok(PAGES)
      },
    })
    render(<Page />)
    await screen.findByText('Northwind Traders')
    await user.click(screen.getByRole('button', { name: 'Pages' }))
    await user.type(screen.getByLabelText('Name'), 'Priced up')
    await user.type(screen.getByLabelText('URL path'), 'https://acme.example/pricing')
    await user.click(screen.getByRole('button', { name: 'Add page' }))
    expect(await screen.findByText(/without the domain/)).toBeInTheDocument()
  })

  it('says so when the Pages list is empty', async () => {
    const user = userEvent.setup()
    stubBase({ [`${PREFIX}/pages`]: ok({ count: 0, pages: [] }) })
    render(<Page />)
    await screen.findByText('Northwind Traders')
    await user.click(screen.getByRole('button', { name: 'Pages' }))
    expect(await screen.findByText('No intent pages yet')).toBeInTheDocument()
  })

  it('changes a condition in place, because step four is a choice a seller revisits', async () => {
    const user = userEvent.setup()
    let patched = null
    stubBase({
      [`${PREFIX}/pages`]: (url, options) => {
        if (options?.method === 'PATCH') {
          patched = JSON.parse(options.body)
          return ok({ ...PAGES.pages[1], ...patched })
        }
        return ok(PAGES)
      },
    })
    render(<Page />)
    await screen.findByText('Northwind Traders')
    await user.click(screen.getByRole('button', { name: 'Pages' }))
    await user.selectOptions(
      screen.getByLabelText('Match condition for Asked for a price'),
      'contains',
    )
    await waitFor(() => expect(patched).toEqual({ condition: 'contains' }))
  })
})

describe('the Capture tab', () => {
  it('offers only the three public parameters, and no field for a person', async () => {
    const user = userEvent.setup()
    stubBase()
    render(<Page />)
    await screen.findByText('Northwind Traders')
    await user.click(screen.getByRole('button', { name: 'Capture' }))
    expect(screen.getByLabelText('IP address')).toBeInTheDocument()
    expect(screen.getByLabelText('Country')).toBeInTheDocument()
    expect(screen.getByLabelText('Network')).toBeInTheDocument()
    for (const forbidden of ['Name', 'Email', 'Visitor ID', 'User ID']) {
      expect(screen.queryByLabelText(forbidden)).toBeNull()
    }
  })

  it('states the company-level stance above the form', async () => {
    const user = userEvent.setup()
    stubBase()
    render(<Page />)
    await screen.findByText('Northwind Traders')
    await user.click(screen.getByRole('button', { name: 'Capture' }))
    expect(
      screen.getByText(/exclusively company-level identification rather than tracking/),
    ).toBeInTheDocument()
  })

  it('posts a capture and shows the company it resolved to', async () => {
    const user = userEvent.setup()
    let posted = null
    stubBase({
      [`${PREFIX}/captures`]: (url, options) => {
        posted = JSON.parse(options.body)
        return ok(CAPTURED, 201)
      },
    })
    render(<Page />)
    await screen.findByText('Northwind Traders')
    await user.click(screen.getByRole('button', { name: 'Capture' }))
    await user.click(screen.getByRole('button', { name: 'Capture request' }))
    expect(await screen.findByText('Resolved to a company')).toBeInTheDocument()
    expect(posted.client_id).toBe('cli-acme')
    expect(posted.path).toBe('/newsroom/converting-the-unconverted-article')
    expect(screen.getByText('Company created')).toBeInTheDocument()
    expect(screen.getByText(/Starts with on \/newsroom/)).toBeInTheDocument()
  })

  it('shows the refusal when the capture is not accepted', async () => {
    const user = userEvent.setup()
    stubBase({
      [`${PREFIX}/captures`]: err(
      { error: 'unknown_installation', detail: "no tracking snippet is installed under client id 'cli-acme'" },
      404,
    ),
    })
    render(<Page />)
    await screen.findByText('Northwind Traders')
    await user.click(screen.getByRole('button', { name: 'Capture' }))
    await user.click(screen.getByRole('button', { name: 'Capture request' }))
    expect(await screen.findByText(/Install the tracking snippet/)).toBeInTheDocument()
  })
})

describe('the Install tab', () => {
  it('records a snippet and lists it', async () => {
    const user = userEvent.setup()
    let posted = null
    stubBase({
      [`${PREFIX}/installations`]: (url, options) => {
        if (options?.method === 'POST') {
          posted = JSON.parse(options.body)
          return ok({ installed: true, created: true, ...posted }, 201)
        }
        return ok(INSTALLATIONS)
      },
    })
    render(<Page />)
    await screen.findByText('Northwind Traders')
    await user.click(screen.getByRole('button', { name: 'Install' }))
    await user.type(screen.getByLabelText('Client ID'), 'cli-northwind')
    await user.type(screen.getByLabelText('Site'), 'https://northwind.example')
    await user.click(screen.getByRole('button', { name: 'Install snippet' }))
    await waitFor(() => expect(posted).toEqual({ client_id: 'cli-northwind', site: 'https://northwind.example' }))
    expect(await screen.findByText(/Snippet installed/)).toBeInTheDocument()
  })

  it('says so when nothing is installed', async () => {
    const user = userEvent.setup()
    stubBase({ [`${PREFIX}/installations`]: ok({ count: 0, installations: [] }) })
    render(<Page />)
    await screen.findByText('Northwind Traders')
    await user.click(screen.getByRole('button', { name: 'Install' }))
    expect(await screen.findByText('No snippet is installed')).toBeInTheDocument()
  })
})

describe('the Decisions tab', () => {
  it('names the two downstream surfaces this workflow does not build', async () => {
    const user = userEvent.setup()
    stubBase()
    render(<Page />)
    await screen.findByText('Northwind Traders')
    await user.click(screen.getByRole('button', { name: 'Decisions' }))
    expect(await screen.findByText('Where this workflow stops (2 decisions)')).toBeInTheDocument()
    expect(screen.getByText('Workflows')).toBeInTheDocument()
    expect(screen.getByText('Auto-engage')).toBeInTheDocument()
  })

  it('shows every decision with its reading, its why and what would change it', async () => {
    const user = userEvent.setup()
    stubBase()
    render(<Page />)
    await screen.findByText('Northwind Traders')
    await user.click(screen.getByRole('button', { name: 'Decisions' }))
    expect(await screen.findByText('What turns an anonymous request into a company?')).toBeInTheDocument()
    expect(screen.getByText(/change: find_company/)).toBeInTheDocument()
    expect(screen.getAllByText(/risk: /)).toHaveLength(2)
  })
})

describe('the accessibility floor', () => {
  it('marks the active tab for assistive technology', async () => {
    stubBase()
    render(<Page />)
    await screen.findByText('Northwind Traders')
    expect(screen.getByRole('button', { name: 'Lead list' })).toHaveAttribute('aria-current', 'page')
  })

  it('gives the lead table a caption', async () => {
    stubBase()
    render(<Page />)
    await screen.findByText('Northwind Traders')
    expect(screen.getByRole('table')).toHaveAccessibleName(/Ranked in-market companies/)
  })

  it('shows a retry when a call fails', async () => {
    global.fetch = vi.fn(async (url) => {
      if (String(url).includes('/vocabulary')) {
        throw new Error('network down')
      }
      throw new Error(`unstubbed route: ${url}`)
    })
    render(<Page />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load data')
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })
})

/**
 * The fallback used by the one test that swaps `fetch` mid-flight to make a
 * single call fail. It answers the read routes from the same fixtures, so the
 * assertion is about the PATCH refusal and nothing else.
 */
function stubBaseFactory(url) {
  const routes = {
    [`${PREFIX}/companies/203.0.113.0-24-9e6315/visits`]: { total: 0, top_paths: [], visits: [] },
    [`${PREFIX}/companies/203.0.113.0-24-9e6315/pages`]: { count: 0, pages: [] },
    [`${PREFIX}/companies/203.0.113.0-24-9e6315`]: COMPANY(),
    [`${PREFIX}/vocabulary`]: VOCABULARY,
    [`${PREFIX}/inferences`]: INFERENCES,
    [`${PREFIX}/installations`]: INSTALLATIONS,
    [`${PREFIX}/pages`]: PAGES,
    [`${PREFIX}/icps`]: { count: 1, profiles: [] },
    [`${PREFIX}/companies`]: LEADS(),
  }
  const text = String(url)
  const hit = Object.keys(routes)
    .filter((path) => text.startsWith(path))
    .sort((left, right) => right.length - left.length)[0]
  if (!hit) throw new Error(`unstubbed route: ${url}`)
  return ok(routes[hit])
}
