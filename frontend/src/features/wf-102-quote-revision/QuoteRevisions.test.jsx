/**
 * Tests for WF-102's page and its pure helpers.
 *
 * The page is driven against a stubbed `fetch` rather than a live server, so the
 * assertions are about what a seller reads and which controls are offered, not
 * about the server's answer. Every test builds its own stub and its own data, so
 * the file passes alone and under vitest's parallel run without depending on
 * another test's fixture.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import page from './index.jsx'
import {
  canRevise,
  isProjectQuote,
  pricingNote,
  recallBlockedReason,
  reviseBlockedReason,
  statusLabel,
} from './api.js'

const { Component } = page

const VOCABULARY = {
  ticket: 'WF-102',
  statuses: ['draft', 'published', 'revised', 'lost', 'canceled'],
  close_reasons: ['lost', 'canceled', 'revised'],
  quote_creation_methods: { '776160000': 'Unknown', '776160001': 'Revision' },
  activities: ['quote_close', 'quote_recalled', 'quote_republished'],
  collections: { quote: 'wf086_quote', activity: 'wf102_quote_activity' },
  evidence: {
    revise_creates_a_new_quote:
      'If you revise it, a new quote is created, set to Draft status, and you can start over.',
  },
}

const ROOMS = { count: 1, records: [{ id: 'room_1', data: { name: 'Northwind' } }] }

const SUMMARY = {
  quotes: 2,
  by_status: { draft: 1, revised: 1 },
  revised: 1,
  activities: 2,
  artefacts: 0,
}

function quoteRecord(overrides = {}) {
  return {
    id: 'quote_1',
    data: { title: 'Northwind annual quote', status: 'draft' },
    ...overrides,
  }
}

const QUOTES = { count: 1, records: [quoteRecord()] }

const DETAIL = {
  id: 'quote_1',
  title: 'Northwind annual quote',
  status: 'draft',
  status_label: 'Draft',
  revision_number: 1,
  owner: 'dana',
  currency: 'EUR',
  open: true,
  closed: false,
  pricing: { mode: 'live', adjusts_to_price_list: true },
  totals: { total: 198.0 },
  line_items: [
    {
      id: 'line_1',
      name: 'Platform licence',
      quantity: 2,
      unit_price: 100.0,
      price_source: 'manual',
      amounts: { total: 198.0 },
    },
  ],
}

const LINEAGE = {
  count: 2,
  revisions: [
    { id: 'quote_0', revision_number: 0, status: 'revised', closed_on: '2026-05-01' },
    { id: 'quote_1', revision_number: 1, status: 'draft', closed_on: null },
  ],
}

const ACTIVITY = {
  count: 1,
  entries: [
    {
      id: 'a1',
      action: 'quote_close',
      label: 'Quote closed',
      reason: 'revised',
      revision_number: 0,
      at: '2026-05-01T09:30:00+00:00',
    },
  ],
}

function jsonResponse(body, status = 200) {
  return { ok: status < 400, status, json: async () => body }
}

function stubFetch(routes) {
  const calls = []
  // Longest pattern first, so `/wf-102/quotes/quote_1/lineage` is not swallowed
  // by the `/wf-102/quotes` list stub.
  const patterns = Object.keys(routes).sort((a, b) => b.length - a.length)
  global.fetch = vi.fn(async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET' })
    for (const pattern of patterns) {
      if (url.includes(pattern)) {
        const body = routes[pattern]
        const resolved = typeof body === 'function' ? await body(options) : body
        return jsonResponse(resolved, resolved?.__status || 200)
      }
    }
    return jsonResponse({ detail: `no stub for ${url}` }, 404)
  })
  return calls
}

function pageRoutes(overrides = {}) {
  return {
    '/wf-102/vocabulary': VOCABULARY,
    '/records/room': ROOMS,
    '/wf-102/summary': SUMMARY,
    '/wf-102/quotes/quote_1/lineage': LINEAGE,
    '/wf-102/quotes/quote_1/activity': ACTIVITY,
    '/wf-102/quotes/quote_1': DETAIL,
    '/wf-102/quotes': QUOTES,
    '/records/wf086_quote': QUOTES,
    ...overrides,
  }
}

// --------------------------------------------------------------------------- #
// Pure helpers
// --------------------------------------------------------------------------- #


describe('the pure helpers', () => {
  it('labels every status the research names', () => {
    expect(statusLabel('draft')).toBe('Draft')
    expect(statusLabel('published')).toBe('Active')
    expect(statusLabel('revised')).toBe('Revised')
    expect(statusLabel('lost')).toBe('Lost')
    expect(statusLabel('canceled')).toBe('Canceled')
  })

  it('offers a revise only for an open, non-project quote with lines', () => {
    expect(canRevise(DETAIL)).toBe(true)
    expect(canRevise({ ...DETAIL, open: false })).toBe(false)
    expect(canRevise({ ...DETAIL, line_items: [] })).toBe(false)
    expect(canRevise({ ...DETAIL, quote_scope: 'project_quote' })).toBe(false)
  })

  it('names why a revise is blocked', () => {
    expect(reviseBlockedReason({ ...DETAIL, open: false })).toContain('Clone it')
    expect(reviseBlockedReason({ ...DETAIL, line_items: [] })).toContain('line item')
    expect(reviseBlockedReason({ ...DETAIL, quote_scope: 'project_quote' })).toContain('project')
  })

  it('blocks a recall once the quote is paid or cannot be restored', () => {
    expect(recallBlockedReason({ status: 'published', hs_payment_status: 'paid' })).toContain(
      'approved, accepted or paid',
    )
    expect(recallBlockedReason({ status: 'published', esignature_enabled: true })).toContain(
      'e-signature',
    )
    expect(recallBlockedReason({ status: 'published' })).toBeNull()
    expect(recallBlockedReason({ status: 'draft' })).toContain('active quote')
  })

  it('describes live and frozen pricing', () => {
    expect(pricingNote(DETAIL)).toContain('current price list')
    expect(pricingNote({ pricing: { mode: 'frozen' } })).toContain('frozen')
  })

  it('recognises a project quote', () => {
    expect(isProjectQuote({ quote_scope: 'project_quote' })).toBe(true)
    expect(isProjectQuote({})).toBe(false)
  })
})

// --------------------------------------------------------------------------- #
// The page
// --------------------------------------------------------------------------- #


describe('the page', () => {
  it('renders the revision board and its counts', async () => {
    stubFetch(pageRoutes())
    render(<Component />)

    expect(await screen.findByText('Quote revisions')).toBeTruthy()
    await waitFor(() => expect(screen.getByText('Revised')).toBeTruthy())
    expect(screen.getByText('Activities')).toBeTruthy()
  })

  it('lists a quote and shows its detail once selected', async () => {
    stubFetch(pageRoutes())
    render(<Component />)

    const picker = await screen.findByLabelText('Quote')
    await userEvent.selectOptions(picker, 'quote_1')

    expect(await screen.findByText('Northwind annual quote')).toBeTruthy()
    expect(screen.getByText('Live')).toBeTruthy()
    expect(screen.getByText('2026-05-01')).toBeTruthy()
  })

  it('offers the three close reasons and the workflow actions', async () => {
    stubFetch(pageRoutes())
    render(<Component />)

    const picker = await screen.findByLabelText('Quote')
    await userEvent.selectOptions(picker, 'quote_1')

    const reasons = await screen.findByLabelText('Close reason')
    expect([...reasons.options].map((option) => option.value)).toEqual([
      'lost',
      'canceled',
      'revised',
    ])
    for (const label of ['Revise', 'Clone', 'Reprice draft', 'Recall', 'Re-publish', 'Close quote']) {
      expect(screen.getByRole('button', { name: label })).toBeTruthy()
    }
  })

  it('disables the revise control and explains why for a closed quote', async () => {
    const closed = {
      ...DETAIL,
      status: 'lost',
      open: false,
      closed: true,
      revision_number: 0,
      pricing: { mode: 'frozen', adjusts_to_price_list: false },
    }
    stubFetch(pageRoutes({ '/wf-102/quotes/quote_1': closed }))
    render(<Component />)

    const picker = await screen.findByLabelText('Quote')
    await userEvent.selectOptions(picker, 'quote_1')

    const revise = await screen.findByRole('button', { name: 'Revise' })
    expect(revise.disabled).toBe(true)
    expect(screen.getByText(/Clone it to start a new version/)).toBeTruthy()
  })

  it('shows the recall block for a paid active quote', async () => {
    const paid = {
      ...DETAIL,
      status: 'published',
      hs_payment_status: 'paid',
      pricing: { mode: 'frozen', adjusts_to_price_list: false },
    }
    stubFetch(pageRoutes({ '/wf-102/quotes/quote_1': paid }))
    render(<Component />)

    const picker = await screen.findByLabelText('Quote')
    await userEvent.selectOptions(picker, 'quote_1')

    expect(await screen.findByText(/approved, accepted or paid/)).toBeTruthy()
    const recall = screen.getByRole('button', { name: 'Recall' })
    expect(recall.disabled).toBe(true)
  })
})
