/**
 * The quoting page, driven through the states a seller actually reaches (WF-086).
 *
 * The `fetch` stub answers by path, so each test states what the server said and
 * then asserts what the page said back. That is the property worth testing here:
 * a page that renders a figure the server did not send, or hides a refusal the
 * server did send, looks identical in a screenshot and is wrong in production.
 *
 * The states covered are the ones that are easy to leave out and expensive to
 * find later: loading, a database with no deal to quote from, a catalogue that has
 * not been provisioned, a field-keyed 422, a 409 carrying a stable reason, and a
 * published quote whose line items are frozen.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import QuoteAuthor from './QuoteAuthor'

const SUMMARY = {
  quotes: 2,
  drafts: 1,
  published: 1,
  expired: 0,
  line_items: 3,
  zero_priced_lines: 0,
  total_contract_value: 210540,
  catalogue_available: true,
  templates: 1,
}

const QUOTE = {
  id: 'quote_1',
  room_id: 'room_1',
  revision: 3,
  title: 'Northwind quote',
  status: 'draft',
  account: 'Northwind Traders',
  owner: 'dana',
  currency: 'EUR',
  expires_on: '2099-06-30',
  expired: false,
  deal_amount_written: null,
  published_at: null,
  modules: [
    { key: 'header', label: 'Header', kind: 'builtin', visible: true, position: 0 },
    { key: 'line_items', label: 'Line Items', kind: 'builtin', visible: true, position: 1 },
    { key: 'totals', label: 'Totals', kind: 'builtin', visible: true, position: 2 },
  ],
  line_items: [
    {
      id: 'li_1',
      name: 'Platform, annual',
      sku: 'PLAT',
      quantity: 30,
      unit_price: 4200,
      discount_type: 'percentage',
      discount_value: 10,
      tax_rate: 10,
      price_source: 'catalog',
      tier_label: 'from 25',
      amounts: { total: 124740 },
    },
    {
      id: 'li_2',
      name: 'Support, annual',
      quantity: 1,
      unit_price: 2400,
      discount_type: 'currency',
      discount_value: 200,
      tax_rate: 10,
      price_source: 'manual',
      tier_label: null,
      amounts: { total: 2420 },
    },
  ],
  totals: {
    subtotal: 148200,
    discount: 23400,
    tax: 12480,
    total: 137280,
    future_payments: 5000,
    total_contract_value: 142280,
  },
}

const ROOMS = { records: [{ id: 'room_1', data: { name: 'Northwind Traders' } }] }

const DEALS = {
  records: [
    {
      id: 'deal_1',
      data: {
        name: 'Northwind renewal',
        account: 'Northwind Traders',
        owner: 'dana',
        currency: 'EUR',
        stage: 'negotiation',
      },
    },
  ],
}

/**
 * Answer by path, so a test states what the server said and nothing more.
 *
 * Matching is on the pathname with the query string stripped. Every list call on
 * this page carries a filter, so a suffix match against the raw URL misses every
 * one of them and the page renders "no deal to quote from" against a mirror that
 * holds one. That failure reads as a broken picker rather than as a stub that
 * answered 404.
 */
function stubFetch(routes) {
  return vi.fn(async (url, options = {}) => {
    const pathname = new URL(String(url), 'http://localhost').pathname
    const key = Object.keys(routes).find((path) => pathname.endsWith(path))
    if (key === undefined) {
      return { ok: false, status: 404, statusText: 'Not Found', json: async () => ({}) }
    }
    const entry = routes[key]
    if (entry.status && entry.status >= 400) {
      return {
        ok: false,
        status: entry.status,
        statusText: 'Error',
        json: async () => entry.body,
      }
    }
    return {
      ok: true,
      status: 200,
      statusText: 'OK',
      json: async () => (typeof entry === 'function' ? entry(options) : entry),
    }
  })
}

function baseRoutes(extra = {}) {
  return {
    '/records/room': ROOMS,
    '/records/crm_deal': DEALS,
    '/wf-086/summary': SUMMARY,
    [`/wf-086/quotes/${QUOTE.id}`]: QUOTE,
    ...extra,
  }
}

/**
 * A fetch that answers everything from `routes`, except the one path a test is
 * about.
 *
 * The point of one object rather than two stubs is that a second stub that
 * "forwards to the default" quietly loses the create route unless the caller
 * remembers to include it. It was forgotten in three tests, and each one failed
 * with "no create quote button", which reads as a page bug rather than as a stub
 * answering 404 on the create.
 */
function stubWithOverride(routes, pathFragment, response) {
  const inner = stubFetch(routes)
  return vi.fn(async (url, options) => {
    if (String(url).includes(pathFragment)) return response
    return inner(url, options)
  })
}

const REJECTED_422 = {
  ok: false,
  status: 422,
  statusText: 'Error',
  json: async () => ({
    error: 'quote_invalid',
    detail: 'That line item is not valid.',
    errors: { name: 'Enter a name for the line.' },
  }),
}

/**
 * How long an assertion waits, and why it is not the default.
 *
 * Opening the editor is two sequential round trips: the create POST resolves, then
 * the editor fetches the quote it was just handed. Testing Library's default is
 * 1000ms, which is enough on an idle machine and not enough on a busy one. This
 * file failed one of these waits in a full run while passing alone, and a test
 * that only passes in one order is a defect in the test.
 *
 * The allowance is applied through one helper rather than per assertion, because
 * the alternative is a file where most waits are 1000ms and one is 5000ms, and the
 * odd one out is the one that gets deleted the next time it flakes.
 */
const WAIT = { timeout: 5000 }

/**
 * The per-test budget, and why it is above vitest's default.
 *
 * Opening the editor is two sequential round trips: the create POST resolves, then
 * the editor fetches the quote it was just handed. Three tests in this file blew
 * vitest's 5000ms default in a full run while the same file passed alone, because
 * several agents share the cores. `userEvent.setup({ delay: null })` was tried as
 * the fix and made it worse: it dispatches without waiting, so React state
 * updates from the async click handler land outside act and the page never
 * settles. The budget is raised instead, and it is raised for every test in the
 * file rather than only the slow ones, because a file where most tests have the
 * default and three do not is a file where the next flaky test is "fixed" by
 * being given its own number.
 *
 * A ceiling nobody reaches costs nothing. `it` is wrapped once, here, rather than
 * repeated at twenty call sites.
 */
const BUDGET = 20000
const itEditor = (name, fn) => it(name, fn, BUDGET)

/** Click Create quote and wait until the editor is on screen. */
/**
 * Click Create quote and wait until the editor is on screen.
 *
 * The wait for the picker to settle before the click is load-bearing, and it was
 * found by a failure rather than by reading the docs. userEvent's default inserts
 * a real delay before it dispatches. In that gap the page's `deals` fetch
 * resolved, React re-rendered, and the button node the test had already resolved
 * was replaced. The click landed on a detached element, `onClick` never ran, and
 * no POST to /wf-086/quotes was ever made. The symptom was "Platform, annual"
 * never appearing, which reads as a page that never opens the editor.
 *
 * It passed alone and failed in a full run because whether the re-render lands in
 * the gap depends on how loaded the machine is. Nothing in the page is wrong; the
 * test was holding a node reference across an await that let React replace it.
 */
async function openEditor(user) {
  await screen.findByDisplayValue('Northwind renewal', undefined, WAIT)
  await user.click(await screen.findByRole('button', { name: /create quote/i }, WAIT))
  return screen.findByText('Platform, annual', undefined, WAIT)
}

const REJECTED_409_EXPIRED = {
  ok: false,
  status: 409,
  statusText: 'Error',
  json: async () => ({
    error: 'quote_conflict',
    reason: 'quote_expired',
    detail: 'This quote is past its expiration date.',
  }),
}

/**
 * The routes every test gets, including the create call.
 *
 * The create route belongs here rather than in each test. A test that stubs a
 * second time over the one `beforeEach` installed left the create POST answering
 * 404 in a full run while the same test passed alone, and the symptom was the
 * editor simply never opening. One stub per test, installed once, is both the
 * simpler arrangement and the one that behaves the same in both runs.
 */
beforeEach(() => {
  vi.stubGlobal('fetch', stubFetch(baseRoutes({ '/wf-086/quotes': { quote: QUOTE } })))
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('the state the page opens in', () => {
  itEditor('says there is a product library only when the server says there is one', async () => {
    render(<QuoteAuthor />)
    await screen.findByText('Author a quote from a deal')
    expect(await screen.findByText('Ready')).toBeTruthy()
    expect(screen.queryByText('No product library yet')).toBeNull()
  })

  itEditor('explains an absent catalogue rather than showing a price from nowhere', async () => {
    // WF-087 has not shipped, so this is the state a fresh database is in. A page
    // that hid it would leave a seller wondering why nothing tiers.
    vi.stubGlobal(
      'fetch',
      stubFetch(baseRoutes({ '/wf-086/summary': { ...SUMMARY, catalogue_available: false } })),
    )
    render(<QuoteAuthor />)
    expect(await screen.findByText('No product library yet')).toBeTruthy()
    expect(screen.getByText('Not provisioned')).toBeTruthy()
  })

  itEditor('offers the deal to quote from, prefilled from the mirror', async () => {
    render(<QuoteAuthor />)
    await screen.findByText('Author a quote from a deal')
    expect(await screen.findByDisplayValue('Northwind renewal')).toBeTruthy()
    // The account appears twice: in the picker's preview and, once a room is
    // chosen, nowhere else on this page. getAllByText rather than getByText, so
    // the assertion says "it is on screen" instead of breaking if the preview
    // grows a column.
    expect(screen.getAllByText('Northwind Traders').length).toBeGreaterThan(0)
    expect(screen.getByText('negotiation')).toBeTruthy()
    expect(screen.getByText('dana')).toBeTruthy()
  })

  itEditor('explains an empty mirror instead of showing an unusable picker', async () => {
    vi.stubGlobal('fetch', stubFetch(baseRoutes({ '/records/crm_deal': { records: [] } })))
    render(<QuoteAuthor />)
    expect(await screen.findByText('No deal to quote from')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /create quote/i })).toBeNull()
  })
})

describe('authoring a quote', () => {
  itEditor('creates the quote and opens the editor on it', async () => {
    const user = userEvent.setup()
    render(<QuoteAuthor />)
    await openEditor(user)

    expect(screen.getByDisplayValue('Northwind quote')).toBeTruthy()
    // The call the page made, read back off the stub beforeEach installed.
    const call = globalThis.fetch.mock.calls.find(([url]) =>
      new URL(String(url), 'http://localhost').pathname.endsWith('/wf-086/quotes'),
    )
    expect(call).toBeTruthy()
    expect(call[0]).toContain('/wf-086/quotes?room_id=room_1')
    expect(JSON.parse(call[1].body)).toEqual({ deal_id: 'deal_1' })
  })

  itEditor('shows every line, and says each has its own record id', async () => {
    const user = userEvent.setup()
    render(<QuoteAuthor />)
    await openEditor(user)

    expect(screen.getByText('Platform, annual')).toBeTruthy()
    expect(screen.getByText('Support, annual')).toBeTruthy()
    expect(screen.getByText(/each with its own record id/)).toBeTruthy()
  })

  itEditor('shows where each unit price came from, in words rather than colour', async () => {
    // A price with no visible provenance is a price nobody can check.
    const user = userEvent.setup()
    render(<QuoteAuthor />)
    await openEditor(user)

    expect(screen.getByText('From the catalogue, from 25')).toBeTruthy()
    expect(screen.getByText('Typed by hand')).toBeTruthy()
  })

  itEditor('shows the contract value first, because that is the figure the publish writes', async () => {
    const user = userEvent.setup()
    vi.stubGlobal('fetch', stubFetch(baseRoutes({ '/wf-086/quotes': { quote: QUOTE } })))
    render(<QuoteAuthor />)
    await openEditor(user)

    const panel = (await screen.findByText('Total contract value')).closest('div')
    expect(panel.textContent).toContain('142,280.00 EUR')
    expect(screen.getByText(/only/, { selector: 'p' })).toBeTruthy()
  })

  itEditor('prints a 422 message beside the field it names', async () => {
    // The server sends a field-keyed map so each message lands next to its input.
    const user = userEvent.setup()
    vi.stubGlobal(
      'fetch',
      stubWithOverride(
        baseRoutes({ '/wf-086/quotes': { quote: QUOTE } }),
        '/line-items',
        REJECTED_422,
      ),
    )

    render(<QuoteAuthor />)
    await openEditor(user)
    await user.click(screen.getByRole('button', { name: /add line item/i }))

    expect(await screen.findByText('Enter a name for the line.')).toBeTruthy()
  })

  itEditor('turns a 409 reason token into the sentence the page owns', async () => {
    // The server sends a stable token. The page branches on it and shows its own
    // words, so a server reword cannot leave a stale sentence here.
    const user = userEvent.setup()
    vi.stubGlobal(
      'fetch',
      stubWithOverride(
        baseRoutes({ '/wf-086/quotes': { quote: QUOTE } }),
        '/publish',
        REJECTED_409_EXPIRED,
      ),
    )

    render(<QuoteAuthor />)
    await openEditor(user)
    await user.click(screen.getByRole('button', { name: /publish quote/i }))

    expect(await screen.findByText(/Extend the date before publishing/)).toBeTruthy()
    expect(screen.getByText('That change was refused')).toBeTruthy()
  })
})

describe('a published quote', () => {
  const PUBLISHED = {
    ...QUOTE,
    status: 'published',
    published_at: '2026-05-04T09:00:00+00:00',
    deal_amount_written: 142280,
  }

  itEditor('says it is published and will not change', async () => {
    const user = userEvent.setup()
    vi.stubGlobal(
      'fetch',
      stubFetch(baseRoutes({ [`/wf-086/quotes/${QUOTE.id}`]: PUBLISHED, '/wf-086/quotes': { quote: PUBLISHED } })),
    )
    render(<QuoteAuthor />)
    await openEditor(user)

    expect(await screen.findAllByText('Published')).not.toHaveLength(0)
    expect(screen.getByText(/Its line items are frozen/)).toBeTruthy()
    expect(screen.getByRole('button', { name: /^Published$/ })).toBeDisabled()
  })

  itEditor('reports the figure that reached the deal, so the write-back is checkable', async () => {
    const user = userEvent.setup()
    vi.stubGlobal(
      'fetch',
      stubFetch(baseRoutes({ [`/wf-086/quotes/${QUOTE.id}`]: PUBLISHED, '/wf-086/quotes': { quote: PUBLISHED } })),
    )
    render(<QuoteAuthor />)
    await openEditor(user)

    expect(await screen.findByText('Wrote 142,280.00 EUR to the deal amount.')).toBeTruthy()
  })

  itEditor('offers no way to add a line, and disables the ones already there', async () => {
    // The add form is gone entirely on a published quote. The remove buttons stay
    // on screen but disabled: a control that vanishes on a state change is harder
    // to find than one that is visibly unavailable.
    const user = userEvent.setup()
    vi.stubGlobal(
      'fetch',
      stubFetch(baseRoutes({ [`/wf-086/quotes/${QUOTE.id}`]: PUBLISHED, '/wf-086/quotes': { quote: PUBLISHED } })),
    )
    render(<QuoteAuthor />)
    await openEditor(user)

    expect(screen.queryByRole('button', { name: /add line item/i })).toBeNull()
    expect(screen.getByRole('button', { name: /remove platform, annual/i })).toBeDisabled()
  })
})

describe('the module editor', () => {
  itEditor('lists the sections with a show and hide control for each', async () => {
    const user = userEvent.setup()
    vi.stubGlobal('fetch', stubFetch(baseRoutes({ '/wf-086/quotes': { quote: QUOTE } })))
    render(<QuoteAuthor />)
    await openEditor(user)

    expect(await screen.findByText('Line Items')).toBeTruthy()
    // Three visible sections, so three Hide buttons.
    expect(screen.getAllByRole('button', { name: 'Hide' })).toHaveLength(3)
  })

  itEditor('gives every move control a name that says which section it moves', async () => {
    // An icon-only arrow is unreadable out of context, and three rows of arrows
    // give a screen reader three identical buttons.
    const user = userEvent.setup()
    vi.stubGlobal('fetch', stubFetch(baseRoutes({ '/wf-086/quotes': { quote: QUOTE } })))
    render(<QuoteAuthor />)
    await openEditor(user)

    expect(await screen.findByRole('button', { name: 'Move Header up' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Move Totals down' })).toBeTruthy()
  })

  itEditor('does not let the first section move up or the last move down', async () => {
    const user = userEvent.setup()
    vi.stubGlobal('fetch', stubFetch(baseRoutes({ '/wf-086/quotes': { quote: QUOTE } })))
    render(<QuoteAuthor />)
    await openEditor(user)
    await screen.findByText('Line Items', undefined, WAIT)

    expect(screen.getByRole('button', { name: 'Move Header up' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Move Totals down' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Move Header down' })).not.toBeDisabled()
  })

  itEditor('says a custom coded module cannot be added here, and why', async () => {
    const user = userEvent.setup()
    vi.stubGlobal('fetch', stubFetch(baseRoutes({ '/wf-086/quotes': { quote: QUOTE } })))
    render(<QuoteAuthor />)
    await openEditor(user)

    expect(await screen.findByText(/not possible through an API/)).toBeTruthy()
  })

  itEditor('sends the reordered sections in the order the seller put them in', async () => {
    const user = userEvent.setup()
    const fetchMock = stubFetch(baseRoutes({ '/wf-086/quotes': { quote: QUOTE } }))
    vi.stubGlobal('fetch', fetchMock)

    render(<QuoteAuthor />)
    await openEditor(user)
    await screen.findByText('Line Items', undefined, WAIT)
    await user.click(screen.getByRole('button', { name: 'Move Totals up' }))
    await user.click(screen.getByRole('button', { name: /save sections/i }))

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        ([url, options]) =>
          new URL(String(url), 'http://localhost').pathname.endsWith(
            `/wf-086/quotes/${QUOTE.id}`,
          ) && options?.method === 'PATCH',
      )
      expect(call).toBeTruthy()
      const sent = JSON.parse(call[1].body).modules.map((module) => module.key)
      expect(sent).toEqual(['header', 'totals', 'line_items'])
    })
  })
})

describe('an expired quote', () => {
  itEditor('warns before the seller tries to publish it', async () => {
    const user = userEvent.setup()
    const EXPIRED = { ...QUOTE, expired: true, expires_on: '2020-01-01' }
    vi.stubGlobal(
      'fetch',
      stubFetch(baseRoutes({ [`/wf-086/quotes/${QUOTE.id}`]: EXPIRED, '/wf-086/quotes': { quote: EXPIRED } })),
    )
    render(<QuoteAuthor />)
    await openEditor(user)

    expect(await screen.findByText(/A buyer cannot be held to a date that has gone/)).toBeTruthy()
  })
})

describe('a line that priced at nothing', () => {
  itEditor('says so in words rather than rendering a bare zero', async () => {
    const user = userEvent.setup()
    const UNPRICED = {
      ...QUOTE,
      line_items: [{ ...QUOTE.line_items[0], unit_price: 0, price_source: 'deal' }],
    }
    vi.stubGlobal(
      'fetch',
      stubFetch(baseRoutes({ [`/wf-086/quotes/${QUOTE.id}`]: UNPRICED, '/wf-086/quotes': { quote: UNPRICED } })),
    )
    render(<QuoteAuthor />)
    await openEditor(user)

    expect(await screen.findByText('No unit price')).toBeTruthy()
  })
})
