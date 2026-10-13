/**
 * Tests for WF-101's page.
 *
 * The page is driven against a stubbed `fetch` rather than a live server, so the
 * assertions are about what a seller reads and which controls are offered. Every
 * test builds its own stub and its own data, so the file passes alone and under
 * vitest's parallel run without depending on another test's fixture.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import page from './index.jsx'
import { blockedReason, canConvert, statusLabel, statusTone } from './api.js'

const { Component } = page

const VOCABULARY = {
  ticket: 'WF-101',
  pricing_modes: ['lock_pricing', 'use_current_pricing'],
  clone_components: ['price_list', 'currency', 'products', 'line_items', 'addresses', 'shipping', 'totals'],
  collections: { order: 'wf101_sales_order' },
  refusal_reasons: { quote_not_activated: 'Activate the quote first.' },
}

const SETTINGS = {
  enable_creation_of_orders_from_quotes: true,
  enable_sales_order_processing_integration: false,
  ispricelocked_default: false,
  explicit: true,
}

const ORDER = {
  id: 'ord_1',
  name: 'Northwind annual licence',
  status: 'open',
  locked: true,
  prices_locked: 'Yes',
  pricing_mode: 'lock_pricing',
  closed: false,
  currency: 'EUR',
  price_list: 'book_1',
  won_on: '2026-04-01',
  totals: { total: 17050.0 },
  line_items: [
    {
      id: 'ol_1',
      name: 'Platform licence',
      sku: 'SKU-01',
      quantity: 1,
      unit_price: 12000.0,
      price_overridden: false,
      amounts: { total: 12000.0 },
    },
    {
      id: 'ol_2',
      name: 'Onboarding',
      sku: 'SKU-02',
      quantity: 2,
      unit_price: 1250.0,
      price_overridden: true,
      amounts: { total: 2500.0 },
    },
  ],
}

const READY_QUOTE = {
  quote_id: 'q_1',
  title: 'Northwind annual licence',
  status: 'published',
  activated: true,
  activated_by: 'status=published',
  accepted: true,
  accepted_by: 'hs_status',
  convertible: true,
  refusal: null,
  refusal_reason: null,
  existing_order_id: null,
  price_lock: { locked: true, branch: 'integration_disabled', reason: 'Integration is off.' },
  line_item_count: 2,
}

const BLOCKED_QUOTE = {
  ...READY_QUOTE,
  quote_id: 'q_draft',
  activated: false,
  activated_by: '',
  refusal: 'quote_not_activated',
  refusal_reason: 'Activate the quote first.',
}

function jsonResponse(body, status = 200) {
  return { ok: status < 400, status, json: async () => body }
}

function stubFetch(routes) {
  const calls = []
  const handler = async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET', body: options.body })
    for (const [pattern, body] of Object.entries(routes)) {
      if (url.includes(pattern)) return jsonResponse(typeof body === 'function' ? body() : body)
    }
    return jsonResponse({ detail: `no stub for ${url}` }, 404)
  }
  global.fetch = vi.fn(handler)
  return calls
}

function baseRoutes(overrides = {}) {
  return {
    '/wf-101/vocabulary': VOCABULARY,
    '/wf-101/settings': SETTINGS,
    '/wf-101/summary': { orders: 1, locked: 1, unlocked: 0, closed: 0 },
    '/wf-101/orders': { count: 1, entries: [ORDER] },
    '/records/room': { records: [{ id: 'room_1', data: { name: 'Northwind' } }] },
    '/records/wf086_quote': { records: [{ id: 'q_1', data: { title: 'Northwind annual licence' } }] },
    ...overrides,
  }
}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe('the descriptor', () => {
  it('exports the four fields the host discovers', () => {
    expect(page.id).toBe('wf-101-quote-to-order')
    expect(typeof page.label).toBe('string')
    expect(page.icon).toBeTruthy()
    expect(typeof Component).toBe('function')
  })
})

describe('the helpers', () => {
  it('names a status in words and a tone from one place', () => {
    expect(statusLabel('fulfilled')).toBe('Fulfilled')
    expect(statusTone('cancelled')).toBe('delete')
    expect(statusLabel('unknown_status')).toBe('unknown_status')
  })

  it('decides readiness from the server answer', () => {
    expect(canConvert(READY_QUOTE)).toBe(true)
    expect(canConvert(BLOCKED_QUOTE)).toBe(false)
    expect(canConvert({ ...READY_QUOTE, existing_order_id: 'ord_1' })).toBe(false)
    expect(canConvert(null)).toBe(false)
  })

  it('explains why a quote is blocked', () => {
    expect(blockedReason(null)).toMatch(/select a quote/i)
    expect(blockedReason(BLOCKED_QUOTE)).toMatch(/activate/i)
    expect(blockedReason(READY_QUOTE)).toBeNull()
  })
})

describe('the page', () => {
  it('renders an order with its price lock and its lines', async () => {
    stubFetch(baseRoutes())
    render(<Component />)

    expect((await screen.findAllByText('Northwind annual licence')).length).toBeGreaterThan(0)
    expect(screen.getByText('SKU-01')).toBeInTheDocument()
    expect(screen.getByText(/prices locked: yes/i)).toBeInTheDocument()
    expect(screen.getByText(/open/i)).toBeInTheDocument()
  })

  it('renders an empty state rather than a blank page', async () => {
    stubFetch(baseRoutes({ '/wf-101/orders': { count: 0, entries: [] } }))
    render(<Component />)

    expect(await screen.findByText(/no orders yet/i)).toBeInTheDocument()
  })

  it('turns the creation setting on with a body the server accepts', async () => {
    const calls = stubFetch(
      baseRoutes({ '/wf-101/settings': { ...SETTINGS, enable_creation_of_orders_from_quotes: false } }),
    )
    render(<Component />)

    const toggle = await screen.findByRole('button', {
      name: /enable creation of orders from quotes/i,
    })
    await userEvent.click(toggle)

    await waitFor(() => {
      const post = calls.find((call) => call.method === 'POST' && call.url.includes('/wf-101/settings'))
      expect(post).toBeDefined()
      expect(JSON.parse(post.body).enable_creation_of_orders_from_quotes).toBe(true)
    })
  })

  it('converts a quote through the conversion route', async () => {
    const calls = stubFetch(baseRoutes({ '/wf-101/quotes/q_1/readiness': READY_QUOTE }))
    render(<Component />)

    await userEvent.selectOptions(await screen.findByLabelText(/^quote$/i), 'q_1')
    expect(await screen.findByRole('button', { name: /convert quote to order/i })).toBeEnabled()

    await userEvent.click(screen.getByRole('button', { name: /convert quote to order/i }))

    await waitFor(() => {
      const post = calls.find((call) => call.url.includes('/wf-101/quotes/q_1/order'))
      expect(post).toBeDefined()
    })
  })

  it('disables conversion for a quote that is not activated', async () => {
    stubFetch(
      baseRoutes({
        '/wf-101/quotes/q_draft/readiness': BLOCKED_QUOTE,
        '/records/wf086_quote': { records: [{ id: 'q_draft', data: { title: 'Fabrikam draft' } }] },
      }),
    )
    render(<Component />)

    await userEvent.selectOptions(await screen.findByLabelText(/^quote$/i), 'q_draft')

    expect(await screen.findByText(/activate the quote/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /convert quote to order/i })).toBeDisabled()
  })

  it('switches a locked order to current pricing', async () => {
    const calls = stubFetch(
      baseRoutes({
        '/wf-101/orders/ord_1/pricing-mode': { ...ORDER, locked: false, prices_locked: 'No' },
      }),
    )
    render(<Component />)

    const button = await screen.findByRole('button', { name: /use current pricing/i })
    await userEvent.click(button)

    await waitFor(() => {
      const post = calls.find((call) => call.url.includes('/wf-101/orders/ord_1/pricing-mode'))
      expect(post).toBeDefined()
      expect(JSON.parse(post.body).mode).toBe('use_current_pricing')
    })
  })

  it('offers no order controls once the order is closed', async () => {
    stubFetch(
      baseRoutes({
        '/wf-101/orders': {
          count: 1,
          entries: [{ ...ORDER, status: 'fulfilled', closed: true }],
        },
      }),
    )
    render(<Component />)

    expect(await screen.findByText(/the order is now read-only/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /lock pricing/i })).not.toBeInTheDocument()
  })
})
