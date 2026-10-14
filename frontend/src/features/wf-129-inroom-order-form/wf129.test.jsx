/**
 * Tests for WF-129's page.
 *
 * The page is driven against a stubbed `fetch` rather than a live server, so the
 * assertions are about what a buyer or seller reads and which controls are
 * offered. Every test builds its own stub and its own data, so the file passes
 * alone and under vitest's parallel run without depending on another test's
 * fixture.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import page from './index.jsx'
import {
  canAccept,
  canEdit,
  canFile,
  canSign,
  modeLabel,
  statusLabel,
  statusTone,
} from './api.js'

const { Component } = page

const VOCABULARY = {
  ticket: 'WF-129',
  statuses: ['draft', 'accepted', 'signed', 'filed'],
  signature_modes: ['typed', 'drawn', 'typed_photo'],
  render_formats_unavailable: ['pdf', 'docx'],
  payment_in_scope: false,
  map_advanced: false,
  refusal_reasons: { form_not_editable: 'The order form is frozen.' },
}

const SUMMARY = { order_forms: 1, editable: 0, signed: 1, filed: 0 }

function line(overrides = {}) {
  return {
    id: 'line_1',
    name: 'Northwind Platform, annual',
    sku: 'NWD-PLAT-ANN',
    quantity: 10,
    unit_price: 4800.0,
    billing_frequency: 'recurring',
    amounts: { total: 48000.0 },
    ...overrides,
  }
}

function form(overrides = {}) {
  return {
    id: 'ord_1',
    title: 'Northwind order form',
    status: 'draft',
    version: 1,
    currency: 'USD',
    line_count: 1,
    buyer_editable: true,
    lines: [line()],
    revisions: [],
    totals: { total: 48000.0 },
    ...overrides,
  }
}

const CERTIFICATE = {
  signer: { name: 'Ada Byron', email: 'ada@northwind.example' },
  signature_mode: 'typed_photo',
  signed_at: '2026-10-14T09:00:00',
  document_version: 2,
  document_sha256: 'a'.repeat(64),
  verified_by_provider: false,
  statement: 'The signature was captured in the room by this product. No third-party e-signature provider verified it.',
}

function jsonResponse(body, status = 200) {
  return { ok: status < 400, status, json: async () => body }
}

function stubFetch(routes) {
  const calls = []
  global.fetch = vi.fn(async (url, options = {}) => {
    const method = options.method || 'GET'
    calls.push({ url, method, body: options.body })
    for (const route of routes) {
      if (url.includes(route.match) && (!route.method || route.method === method)) {
        const body = typeof route.body === 'function' ? route.body(url, options) : route.body
        return jsonResponse(body)
      }
    }
    return jsonResponse({ detail: `no stub for ${method} ${url}` }, 404)
  })
  return calls
}

function baseRoutes(overrides = []) {
  return [
    // Overrides come first so a test's own route wins over the default.
    ...overrides,
    { match: '/records/room', body: { records: [{ id: 'room_a', data: { name: 'Northwind' } }] } },
    {
      match: '/records/wf086_quote',
      body: { records: [{ id: 'q_1', data: { title: 'Northwind annual order' } }] },
    },
    { match: '/wf-129/vocabulary', body: VOCABULARY },
    { match: '/wf-129/summary', body: SUMMARY },
    { match: '/wf-129/order-forms', method: 'GET', body: { count: 1, order_forms: [form()] } },
    { match: '/wf-129/order-forms', method: 'POST', body: { order_form: form() } },
  ]
}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe('the descriptor', () => {
  it('exports the four fields the host discovers', () => {
    expect(page.id).toBe('wf-129-inroom-order-form')
    expect(typeof page.label).toBe('string')
    expect(page.icon).toBeTruthy()
    expect(typeof Component).toBe('function')
  })
})

describe('the helpers', () => {
  it('names a status in words and a tone from one place', () => {
    expect(statusLabel('signed')).toBe('Signed')
    expect(statusTone('draft')).toBe('update')
    expect(statusLabel('unknown_state')).toBe('unknown_state')
  })

  it('names a signature mode in words', () => {
    expect(modeLabel('typed_photo')).toBe('Typed and photographed')
    expect(modeLabel('drawn')).toBe('Drawn')
  })

  it('decides which control each state offers', () => {
    expect(canEdit(form())).toBe(true)
    expect(canAccept(form())).toBe(true)
    expect(canSign(form())).toBe(false)
    expect(canSign(form({ status: 'accepted' }))).toBe(true)
    expect(canFile(form({ status: 'signed' }))).toBe(true)
    expect(canFile(form({ status: 'filed' }))).toBe(false)
    expect(canEdit(null)).toBe(false)
  })
})

describe('the page', () => {
  it('renders an order form with its itemised lines and total', async () => {
    stubFetch(baseRoutes())
    render(<Component />)

    expect((await screen.findAllByText('Northwind order form')).length).toBeGreaterThan(0)
    expect(screen.getByText('NWD-PLAT-ANN')).toBeInTheDocument()
    expect(screen.getByText('48000.00 USD')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /accept the order form/i })).toBeEnabled()
  })

  it('renders an empty state rather than a blank page', async () => {
    stubFetch(
      baseRoutes([
        { match: '/wf-129/order-forms', method: 'GET', body: { count: 0, order_forms: [] } },
      ]),
    )
    render(<Component />)

    expect(await screen.findByText(/no order forms yet/i)).toBeInTheDocument()
  })

  it('renders a quote to render in the room and posts the quote id', async () => {
    const calls = stubFetch(baseRoutes())
    render(<Component />)

    await userEvent.selectOptions(await screen.findByLabelText(/^quote$/i), 'q_1')
    await userEvent.click(screen.getByRole('button', { name: /render in room/i }))

    await waitFor(() => {
      const post = calls.find((call) => call.method === 'POST' && call.url.includes('/wf-129/order-forms'))
      expect(post).toBeDefined()
      expect(JSON.parse(post.body).quote_id).toBe('q_1')
    })
  })

  it('accepts a draft form through the accept route', async () => {
    const calls = stubFetch(baseRoutes())
    render(<Component />)

    await userEvent.click(await screen.findByRole('button', { name: /accept the order form/i }))

    await waitFor(() => {
      const post = calls.find((call) => call.url.includes('/wf-129/order-forms/ord_1/accept'))
      expect(post).toBeDefined()
    })
  })

  it('signs an accepted form with the chosen mode and the signer', async () => {
    const calls = stubFetch(
      baseRoutes([
        {
          match: '/wf-129/order-forms',
          method: 'GET',
          body: { count: 1, order_forms: [form({ status: 'accepted' })] },
        },
        { match: '/wf-129/order-forms/ord_1/sign', method: 'POST', body: { order_form: form({ status: 'signed' }) } },
      ]),
    )
    render(<Component />)

    await userEvent.type(await screen.findByLabelText(/signer name/i), 'Ada Byron')
    await userEvent.type(screen.getByLabelText(/signer email/i), 'ada@northwind.example')
    await userEvent.type(screen.getByLabelText(/typed name/i), 'Ada Byron')
    await userEvent.selectOptions(screen.getByLabelText(/sign as/i), 'typed_photo')

    // typed_photo needs an image as well as a name.
    await userEvent.type(screen.getByLabelText(/typed name/i), 'Ada Byron')
    await userEvent.type(screen.getByLabelText(/signature image/i), 'scan.png')

    await userEvent.click(screen.getByRole('button', { name: /sign in the room/i }))

    await waitFor(() => {
      const post = calls.find((call) => call.url.includes('/wf-129/order-forms/ord_1/sign'))
      expect(post).toBeDefined()
      const body = JSON.parse(post.body)
      expect(body.mode).toBe('typed_photo')
      expect(body.image).toBe('scan.png')
      expect(body.signer.email).toBe('ada@northwind.example')
    })
  })

  it('files a signed form on the deal', async () => {
    const calls = stubFetch(
      baseRoutes([
        {
          match: '/wf-129/order-forms',
          method: 'GET',
          body: { count: 1, order_forms: [form({ status: 'signed' })] },
        },
        { match: '/wf-129/order-forms/ord_1/file', method: 'POST', body: { deal_filed: true } },
      ]),
    )
    render(<Component />)

    await userEvent.click(await screen.findByRole('button', { name: /file the order form on the deal/i }))

    await waitFor(() => {
      const post = calls.find((call) => call.url.includes('/wf-129/order-forms/ord_1/file'))
      expect(post).toBeDefined()
    })
  })

  it('shows the audit certificate and says no provider verified it', async () => {
    stubFetch(
      baseRoutes([
        {
          match: '/wf-129/order-forms',
          method: 'GET',
          body: { count: 1, order_forms: [form({ status: 'signed', certificate: CERTIFICATE })] },
        },
      ]),
    )
    render(<Component />)

    expect(await screen.findByText('Audit certificate')).toBeInTheDocument()
    expect(screen.getByText(CERTIFICATE.document_sha256)).toBeInTheDocument()
    expect(screen.getByText(/no provider verification/i)).toBeInTheDocument()
    expect(screen.getByText(/no pdf or docx rendered/i)).toBeInTheDocument()
  })
})
