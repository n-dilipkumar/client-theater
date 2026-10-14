/**
 * Tests for WF-123's page and its pure helpers.
 *
 * The page is driven against a stubbed `fetch` rather than a live server, so the
 * assertions are about what a seller reads and which controls are offered, not
 * about the server's answer. Every test builds its own stub and its own data, so
 * the file passes alone and under vitest's parallel run.
 *
 * The buyer's view is asserted at the data level as well as the DOM: the one rule
 * the research states is that the qualification summary is visible to the seller
 * only, so `buyerViewHasSellerOnly` has to be false for the payload the buyer
 * route returns.
 */

import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import page from './index.jsx'
import {
  answerLabel,
  bandBreakdown,
  bandLabel,
  bandTone,
  buyerViewHasSellerOnly,
  dimensionLabel,
  formatDateTime,
  routeLabel,
  scorePercent,
  stripSellerOnly,
} from './api.js'

const { Component } = page

const ROOMS = {
  count: 1,
  records: [{ id: 'room_1', data: { name: 'Northwind Traders' } }],
}

const BUYER_FORM = {
  id: 'form_1',
  room_id: 'room_1',
  name: 'Northwind qualification',
  intro: 'Four questions so we can prepare the right next step.',
  visibility: 'buyer',
  questions: [
    {
      id: 'budget',
      label: 'Budget',
      question: 'Is there a budget for this, and has it been approved?',
      required: true,
      options: [
        { value: 'funded', label: 'Funded and approved' },
        { value: 'no_budget', label: 'No budget yet' },
      ],
    },
    {
      id: 'timeline',
      label: 'Timeline',
      question: 'When do you need this in place?',
      required: true,
      options: [
        { value: 'now', label: 'As soon as possible' },
        { value: 'later', label: 'No date yet' },
      ],
    },
  ],
}

const FORMS = {
  count: 2,
  entries: [
    {
      id: 'form_1',
      name: 'Northwind qualification',
      status: 'live',
      dimensions: ['budget', 'timeline', 'authority', 'procurement_path'],
      respondents: 1,
      live: true,
    },
    {
      id: 'form_2',
      name: 'Contoso security review',
      status: 'draft',
      dimensions: ['budget'],
      respondents: 0,
      live: false,
    },
  ],
}

const SUBMISSIONS = {
  count: 1,
  seller_only: true,
  entries: [
    {
      id: 'sub_1',
      form_name: 'Northwind qualification',
      status: 'submitted',
      band: 'qualified',
      score: 12,
      max_score: 12,
      route_target: 'account_executive',
      submitted_at: '2026-06-01T12:00:00.000Z',
    },
  ],
}

const DETAIL = {
  ...SUBMISSIONS.entries[0],
  answers: {
    budget: 'funded',
    timeline: 'now',
    authority: 'decision_maker',
    procurement_path: 'known_process',
  },
  events: [
    { id: 'e1', event: 'answers_submitted', at: '2026-06-01T12:00:00.000Z' },
    { id: 'e2', event: 'qualification_scored', at: '2026-06-01T12:00:00.000Z' },
    { id: 'e3', event: 'routed_on_band', at: '2026-06-01T12:00:00.000Z' },
  ],
}

const SUMMARY = {
  room_id: 'room_1',
  seller_only: true,
  forms: 2,
  submissions: 1,
  in_progress: 0,
  qualified: 1,
  by_band: { qualified: 1, developing: 0, unqualified: 0 },
  routing: { account_executive: 1 },
}

function jsonResponse(body, status = 200) {
  return { ok: status < 400, status, json: async () => body }
}

function stubFetch(routes) {
  const calls = []
  const patterns = Object.keys(routes).sort((a, b) => b.length - a.length)
  global.fetch = vi.fn(async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET', body: options.body })
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
    '/records/room': ROOMS,
    '/wf-123/rooms/room_1/form': BUYER_FORM,
    '/wf-123/rooms/room_1/qualification': SUMMARY,
    '/wf-123/submissions/sub_1': DETAIL,
    '/wf-123/submissions': SUBMISSIONS,
    '/wf-123/forms': FORMS,
    ...overrides,
  }
}

// --------------------------------------------------------------------------- #
// Pure helpers
// --------------------------------------------------------------------------- #


describe('the pure helpers', () => {
  it('labels every closed value the research names', () => {
    expect(bandLabel('qualified')).toBe('Qualified')
    expect(bandLabel('developing')).toBe('Developing')
    expect(bandLabel('unqualified')).toBe('Unqualified')
    expect(bandTone('qualified')).toBe('insert')
    expect(dimensionLabel('procurement_path')).toBe('Procurement path')
    expect(routeLabel('account_executive')).toBe('Account executive')
  })

  it('turns a score into a share of its ceiling', () => {
    expect(scorePercent(12, 12)).toBe(100)
    expect(scorePercent(9, 12)).toBe(75)
    expect(scorePercent(0, 12)).toBe(0)
    expect(scorePercent(5, 0)).toBe(0)
    expect(scorePercent(null, 12)).toBe(0)
  })

  it('formats a timestamp or a dash', () => {
    expect(formatDateTime('')).toBe('-')
    expect(formatDateTime(null)).toBe('-')
    expect(formatDateTime('not a date')).toBe('-')
    expect(formatDateTime('2026-06-01T12:00:00.000Z')).not.toBe('-')
  })

  it('reads the label for an answered option from the form itself', () => {
    const question = { options: [{ value: 'funded', label: 'Funded and approved' }] }
    expect(answerLabel(question, 'funded')).toBe('Funded and approved')
    expect(answerLabel(question, 'unknown')).toBe('unknown')
    expect(answerLabel(undefined, 'funded')).toBe('funded')
  })

  it('reports a seller-only field and strips it', () => {
    expect(buyerViewHasSellerOnly({ id: 'x', answers: {} })).toBe(false)
    expect(buyerViewHasSellerOnly({ id: 'x', score: 12 })).toBe(true)
    expect(buyerViewHasSellerOnly({ id: 'x', band: 'qualified' })).toBe(true)
    expect(buyerViewHasSellerOnly(null)).toBe(false)

    const stripped = stripSellerOnly({ id: 'x', score: 12, band: 'qualified', answers: {} })
    expect(stripped).toEqual({ id: 'x', answers: {} })
    expect(buyerViewHasSellerOnly(stripped)).toBe(false)
  })

  it('breaks a summary into the research band order', () => {
    const bands = bandBreakdown({ by_band: { qualified: 2, unqualified: 1 } })
    expect(bands.map((entry) => entry.band)).toEqual(['qualified', 'developing', 'unqualified'])
    expect(bands[0].count).toBe(2)
    expect(bands[1].count).toBe(0)
  })
})

// --------------------------------------------------------------------------- #
// The buyer's view, at the data level
// --------------------------------------------------------------------------- #


describe("the buyer's view", () => {
  it('never carries a score or a band', () => {
    expect(buyerViewHasSellerOnly(BUYER_FORM)).toBe(false)
    expect('score' in BUYER_FORM).toBe(false)
    expect('band' in BUYER_FORM).toBe(false)
  })
})

// --------------------------------------------------------------------------- #
// The page
// --------------------------------------------------------------------------- #


describe('the page', () => {
  it('renders the console, the counts and the buyer view', async () => {
    stubFetch(pageRoutes())
    render(<Component />)

    expect(await screen.findByText('Qualification capture')).toBeTruthy()
    expect(await screen.findByLabelText('Room')).toBeTruthy()
    expect(screen.getAllByText('Submissions').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Qualified').length).toBeGreaterThan(0)
    expect(await screen.findByLabelText('Form name')).toBeTruthy()

    // The buyer's questions, their options, and the seller-only note.
    expect(await screen.findByText('Buyer view')).toBeTruthy()
    expect(
      screen.getByText('Is there a budget for this, and has it been approved?'),
    ).toBeTruthy()
    expect(screen.getByText('Funded and approved')).toBeTruthy()
    expect(
      screen.getByText(
        /The score, the band and the routing decision are visible to the seller only/,
      ),
    ).toBeTruthy()
  })

  it('lists a submission and shows its score, band and lifecycle once selected', async () => {
    stubFetch(pageRoutes())
    render(<Component />)

    const row = await screen.findByRole('button', { name: /12\/12/ })
    await userEvent.click(row)

    expect(await screen.findByText('Answers')).toBeTruthy()
    expect(screen.getByText('Lifecycle')).toBeTruthy()
    expect(screen.getByText('answers_submitted')).toBeTruthy()
    expect(screen.getByText('Routed to')).toBeTruthy()
  })

  it('offers a publish control only for a draft form and posts it', async () => {
    const calls = stubFetch(pageRoutes())
    render(<Component />)

    const publish = await screen.findByRole('button', { name: 'Publish' })
    await userEvent.click(publish)

    expect(
      calls.some(
        (call) => call.method === 'POST' && call.url.includes('/wf-123/forms/form_2/publish'),
      ),
    ).toBe(true)
  })

  it('creates a form with the chosen dimensions', async () => {
    const calls = stubFetch(pageRoutes())
    render(<Component />)

    await userEvent.type(await screen.findByLabelText('Form name'), 'Fabrikam renewal')
    fireEvent.change(screen.getByLabelText('Intro'), { target: { value: 'A short one.' } })
    await userEvent.click(screen.getByRole('button', { name: 'Create form' }))

    const post = calls.find((call) => call.method === 'POST' && call.url.includes('/wf-123/forms'))
    expect(post).toBeTruthy()
    const body = JSON.parse(post.body)
    expect(body.name).toBe('Fabrikam renewal')
    expect(body.dimensions).toEqual(['budget', 'timeline', 'authority', 'procurement_path'])
    expect(body.room_id).toBe('room_1')
  })

  it('says so when the room has no live form', async () => {
    stubFetch(
      pageRoutes({
        '/wf-123/rooms/room_1/form': { __status: 404, detail: 'no live form' },
      }),
    )
    render(<Component />)

    expect(await screen.findByText(/No live form is embedded/)).toBeTruthy()
  })
})
