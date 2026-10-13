/**
 * Tests for WF-099's page.
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
import { blockedReason, canAccept, flowLabel, flowTone } from './api.js'

const { Component } = page

const VOCABULARY = {
  ticket: 'WF-099',
  flows: ['contract_and_order', 'contract_only', 'none'],
  effective_start_date_modes: ['ON_AGREEMENT', 'CUSTOM', 'DELAYED_BY_DAYS', 'DELAYED_BY_MONTHS'],
  legacy_rule: 'A quote is legacy only when it carries an explicit marker.',
  collections: { contract: 'contract' },
}

const SETTINGS_ON = {
  create_contracts_from_accepted_quotes: true,
  connected_cpq: true,
  direct_contracts_enabled: false,
  renewal_alert_mode: 'off',
  explicit: true,
}

const CONTRACT = {
  id: 'ct_1',
  room_id: 'room_1',
  data: {
    name: 'Northwind annual licence',
    contract_flow: 'contract_and_order',
    start_date: '2026-04-01',
    end_date: '2027-04-01',
    term_length: 12,
    currency: 'EUR',
    billing_flow: 'none',
    totals: { total: 17050.0 },
    order_id: 'ord_1',
    line_items: [
      { name: 'Platform licence', sku: 'SKU-01', quantity: 1, amount: 12000.0 },
      { name: 'Onboarding', sku: 'SKU-02', quantity: 2, amount: 2500.0 },
    ],
  },
  derived: { term_label: '12 months', has_order: true, order_id: 'ord_1', renewable: true },
}

const READY_QUOTE = {
  quote_id: 'q_1',
  title: 'Northwind annual licence',
  status: 'published',
  published: true,
  legacy: false,
  accepted: false,
  existing_contract_id: null,
  flow: 'contract_and_order',
  plan: { flow: 'contract_and_order', create_contract: true, create_order: true, reason: '' },
  effective_start: { mode: 'ON_AGREEMENT', start_date: '2026-04-01' },
  effective_start_date_mode: 'ON_AGREEMENT',
  billing_flow: 'none',
}

const LEGACY_QUOTE = {
  ...READY_QUOTE,
  quote_id: 'q_legacy',
  legacy: true,
  plan: { flow: 'none', create_contract: false, create_order: false, reason: '' },
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
    '/wf-099/vocabulary': VOCABULARY,
    '/wf-099/settings': SETTINGS_ON,
    '/wf-099/contracts': { count: 1, entries: [CONTRACT] },
    '/wf-099/summary': { contracts: 1, with_order: 1, acceptances: 1, legacy_acceptances: 0 },
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
    expect(page.id).toBe('wf-099-auto-create-a-contract-from-an-accepted-quote')
    expect(typeof page.label).toBe('string')
    expect(page.icon).toBeTruthy()
    expect(typeof Component).toBe('function')
  })
})

describe('the helpers', () => {
  it('names a flow in words and a tone from one place', () => {
    expect(flowLabel('contract_and_order')).toBe('Contract and order')
    expect(flowTone('contract_and_order')).toBe('insert')
    expect(flowLabel('unknown_flow')).toBe('unknown_flow')
  })

  it('decides readiness from the server answer', () => {
    expect(canAccept(READY_QUOTE)).toBe(true)
    expect(canAccept(LEGACY_QUOTE)).toBe(false)
    expect(canAccept({ ...READY_QUOTE, existing_contract_id: 'ct_1' })).toBe(false)
    expect(canAccept(null)).toBe(false)
  })

  it('explains why a quote is blocked', () => {
    expect(blockedReason(null)).toMatch(/select a quote/i)
    expect(blockedReason(LEGACY_QUOTE)).toMatch(/legacy/i)
    expect(blockedReason(READY_QUOTE)).toBeNull()
  })
})

describe('the page', () => {
  it('renders a contract with its inherited line items', async () => {
    stubFetch(baseRoutes())
    render(<Component />)

    expect((await screen.findAllByText('Northwind annual licence')).length).toBeGreaterThan(0)
    expect(screen.getByText('SKU-01')).toBeInTheDocument()
    expect(screen.getByText('SKU-02')).toBeInTheDocument()
    expect(screen.getByText(/contract and order/i)).toBeInTheDocument()
  })

  it('renders an empty state rather than a blank page', async () => {
    stubFetch(baseRoutes({ '/wf-099/contracts': { count: 0, entries: [] } }))
    render(<Component />)

    expect(await screen.findByText(/no contracts yet/i)).toBeInTheDocument()
  })

  it('turns the setting on with a body the server accepts', async () => {
    const calls = stubFetch(
      baseRoutes({ '/wf-099/settings': { ...SETTINGS_ON, create_contracts_from_accepted_quotes: false } }),
    )
    render(<Component />)

    const toggle = await screen.findByRole('button', {
      name: /create contracts from accepted quotes/i,
    })
    await userEvent.click(toggle)

    await waitFor(() => {
      const post = calls.find((call) => call.method === 'POST' && call.url.includes('/wf-099/settings'))
      expect(post).toBeDefined()
      expect(JSON.parse(post.body).create_contracts_from_accepted_quotes).toBe(true)
    })
  })

  it('records acceptance through the acceptance route, not a status write', async () => {
    const calls = stubFetch(baseRoutes({ '/wf-099/quotes/q_1': READY_QUOTE }))
    render(<Component />)

    await userEvent.selectOptions(await screen.findByLabelText(/^quote$/i), 'q_1')
    expect(await screen.findByRole('button', { name: /record acceptance/i })).toBeEnabled()

    await userEvent.click(screen.getByRole('button', { name: /record acceptance/i }))

    await waitFor(() => {
      const post = calls.find((call) => call.url.includes('/acceptance'))
      expect(post).toBeDefined()
      expect(post.url).toContain('/wf-099/quotes/q_1/acceptance')
    })
  })

  it('warns that a legacy quote creates no contract and disables acceptance', async () => {
    stubFetch(
      baseRoutes({
        '/wf-099/quotes/q_1': LEGACY_QUOTE,
        '/records/wf086_quote': { records: [{ id: 'q_1', data: { title: 'Adventure Works historic' } }] },
      }),
    )
    render(<Component />)

    await userEvent.selectOptions(await screen.findByLabelText(/^quote$/i), 'q_1')

    expect(await screen.findByText(/accepted legacy quotes do not create contracts/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /record acceptance/i })).toBeDisabled()
  })

  it('creates a standalone contract with the form values', async () => {
    const calls = stubFetch(
      baseRoutes({ '/wf-099/settings': { ...SETTINGS_ON, direct_contracts_enabled: true } }),
    )
    render(<Component />)

    await userEvent.type(await screen.findByLabelText(/^name$/i), 'Wingtip standalone')
    await userEvent.click(screen.getByRole('button', { name: /create standalone contract/i }))

    await waitFor(() => {
      const post = calls.find((call) => call.method === 'POST' && call.url.includes('/wf-099/contracts'))
      expect(post).toBeDefined()
      expect(JSON.parse(post.body).name).toBe('Wingtip standalone')
    })
  })

  it('says the standalone path needs the beta when it is off', async () => {
    stubFetch(baseRoutes())
    render(<Component />)

    expect(await screen.findByText(/turn on the direct create/i)).toBeInTheDocument()
  })
})
