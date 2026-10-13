/**
 * Tests for WF-118's page.
 *
 * The page is driven against a stubbed `fetch`, so the assertions are about what
 * a seller reads and which controls are offered, not about a live server. Every
 * test builds its own stub and its own data, so the file passes alone and under
 * vitest's parallel run without depending on another test's fixture.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import page from './index.jsx'
import {
  activationForm,
  activationPayload,
  activationSummary,
  eligibilityText,
  statusLabel,
  stopAfterLabel,
  tierLabel,
} from './api.js'

const { Component } = page

const VOCABULARY = {
  ticket: 'WF-118',
  repeat_units: ['day', 'hour'],
  throttle_units: ['minute', 'hour', 'day'],
  min_stop_after: 2,
  delay_max_seconds: 60,
  decisions: [
    {
      id: 'programmatic_launch',
      decision: 'How a programmatic launch interacts with the throttle.',
      selected: 'bypasses_gate',
      alternative_rejected: 'Subjecting it to the gate.',
      verdict: 'pass',
      audit_id: 'jev-20261013T194510-856-10906',
    },
  ],
  sources: ['https://support.pendo.io/hc/en-us/articles/360031864452-Order-and-throttle-your-guides'],
}

const SUMMARY = {
  total: 2,
  unthrottled: 1,
  repeating: 1,
  applications: 1,
  sessions: 1,
  events: 2,
  by_status: { public: 2, staging: 0 },
}

const WELCOME = {
  key: 'welcome_tour',
  name: 'Welcome tour',
  status: 'public',
  activation: {
    repeat: { enabled: true, every: 1, unit: 'day' },
    stop_after: { mode: 'count', count: 3 },
    ignore_throttling: false,
    delay_seconds: 0,
  },
  stop_after_label: 'After 3 dismissals',
}

const CRITICAL = {
  key: 'critical_notice',
  name: 'Critical notice',
  status: 'public',
  activation: {
    repeat: { enabled: false, every: 1, unit: 'day' },
    stop_after: { mode: 'never', count: 2 },
    ignore_throttling: true,
    delay_seconds: 0,
  },
  stop_after_label: 'Never',
}

const RANKING = [
  { ...CRITICAL, tier: 0, position: 0, eligible: true, eligible_at: null, active: false, suppressed: false },
  { ...WELCOME, tier: 1, position: 1, eligible: true, eligible_at: null, active: false, suppressed: false },
]

const ORDER = {
  id: 'order_1',
  app_id: 'demo-app',
  guide_keys: ['critical_notice', 'welcome_tour'],
  throttling: { enabled: true, count: 1, unit: 'day' },
  configured: true,
}

const SESSION = {
  app_id: 'demo-app',
  user_id: 'dana',
  configured: true,
  active_guide_key: null,
  last_dismissed_at: null,
  guides: {},
}

function jsonResponse(body, status = 200) {
  return { ok: status < 400, status, json: async () => body }
}

function stubFetch() {
  const calls = []
  const handler = async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET', body: options.body })
    if (url.includes('/wf-118/vocabulary')) return jsonResponse(VOCABULARY)
    if (url.includes('/wf-118/summary')) return jsonResponse(SUMMARY)
    if (url.includes('/wf-118/guides') && (options.method || 'GET') === 'GET') {
      return jsonResponse({ count: 2, guides: [CRITICAL, WELCOME] })
    }
    if (url.includes('/ranking')) return jsonResponse({ ranking: RANKING, order: ORDER, at: null })
    if (url.includes('/order')) return jsonResponse(ORDER)
    if (url.includes('/sessions/')) return jsonResponse(SESSION)
    if (url.includes('/events')) return jsonResponse({ event: 'dismissed', key: 'welcome_tour' }, 201)
    return jsonResponse({ detail: `no stub for ${url}` }, 404)
  }
  global.fetch = vi.fn(handler)
  return calls
}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe('the descriptor', () => {
  it('exports the four fields the host discovers', () => {
    expect(page.id).toBe('wf-118-guide-prompts')
    expect(typeof page.label).toBe('string')
    expect(page.icon).toBeTruthy()
    expect(typeof page.iconPath).toBe('string')
    expect(typeof Component).toBe('function')
  })
})

describe('the helpers', () => {
  it('names the two tiers', () => {
    expect(tierLabel(0)).toBe('Unthrottled')
    expect(tierLabel(1)).toBe('Throttled')
  })

  it('summarises an activation in one line', () => {
    const summary = activationSummary(WELCOME.activation)
    expect(summary).toMatch(/repeats every 1 day/i)
    expect(summary).toMatch(/stops after 3 dismissals/i)
    expect(activationSummary(CRITICAL.activation)).toMatch(/ignores throttling/i)
  })

  it('reads the stop label and the status label without re-deriving them', () => {
    expect(stopAfterLabel({ stop_after: { mode: 'never' } })).toBe('Never')
    expect(stopAfterLabel({ stop_after: { mode: 'count', count: 5 } })).toBe('After 5 dismissals')
    expect(statusLabel('public')).toBe('Public')
    expect(statusLabel('staging')).toBe('Staging')
  })

  it('explains why a guide is or is not a candidate', () => {
    expect(eligibilityText({ eligible: true })).toBe('Eligible now')
    expect(eligibilityText({ suppressed: true, suppress_reason: 'stop_after reached' })).toMatch(/suppressed/i)
    expect(eligibilityText({ eligible: false, eligible_at: '2026-03-02T09:00:00+00:00' })).toMatch(/eligible 2026/i)
    expect(eligibilityText(null)).toBe('Unknown')
  })

  it('round-trips the activation form and its payload', () => {
    const form = activationForm(WELCOME.activation)
    expect(form.repeatEnabled).toBe(true)
    expect(form.repeatEvery).toBe(1)
    expect(activationPayload(form).stop_after).toEqual({ mode: 'count', count: 3 })
    expect(activationPayload({ ...form, ignoreThrottling: true }).ignore_throttling).toBe(true)
  })
})

describe('the page', () => {
  it('renders the summary and the two-tier order list', async () => {
    stubFetch()
    render(<Component />)

    expect(await screen.findByText('Welcome tour')).toBeInTheDocument()
    expect(screen.getByText('Critical notice')).toBeInTheDocument()
    expect(screen.getAllByText('Unthrottled').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Throttled').length).toBeGreaterThan(0)
    expect(screen.getByText('Ignore throttling')).toBeInTheDocument()
  })

  it('shows an empty state rather than a blank list', async () => {
    global.fetch = vi.fn(async (url) => {
      if (url.includes('/wf-118/vocabulary')) return jsonResponse(VOCABULARY)
      if (url.includes('/wf-118/summary')) return jsonResponse({ ...SUMMARY, total: 0 })
      if (url.includes('/wf-118/guides')) return jsonResponse({ count: 0, guides: [] })
      if (url.includes('/ranking')) return jsonResponse({ ranking: [], order: ORDER })
      if (url.includes('/order')) return jsonResponse({ ...ORDER, guide_keys: [] })
      if (url.includes('/sessions/')) return jsonResponse(SESSION)
      return jsonResponse({}, 200)
    })
    render(<Component />)

    expect(await screen.findByText(/no guides yet/i)).toBeInTheDocument()
  })

  it('moves a guide with a row arrow', async () => {
    const calls = stubFetch()
    render(<Component />)
    await screen.findByText('Welcome tour')

    const arrows = screen.getAllByRole('button', { name: /^Top:/i })
    await userEvent.click(arrows[0])

    await waitFor(() => {
      const move = calls.find((call) => call.url.includes('/order/move'))
      expect(move).toBeDefined()
      expect(JSON.parse(move.body).move).toBe('top')
    })
  })

  it('records a dismissal for the selected guide', async () => {
    const calls = stubFetch()
    render(<Component />)
    await screen.findByText('Welcome tour')

    await userEvent.click(screen.getByRole('button', { name: 'Welcome tour' }))
    await userEvent.click(screen.getByRole('button', { name: /^Dismiss$/i }))

    await waitFor(() => {
      const post = calls.find((call) => call.url.includes('/events') && call.method === 'POST')
      expect(post).toBeDefined()
      expect(JSON.parse(post.body)).toMatchObject({ event: 'dismissed', key: 'welcome_tour' })
    })
  })

  it('creates a guide from the new-guide form', async () => {
    const calls = stubFetch()
    render(<Component />)
    await screen.findByText('Welcome tour')

    await userEvent.type(screen.getByLabelText(/^key$/i), 'brandnewkey')
    await userEvent.type(screen.getByLabelText(/^name$/i), 'Brand new guide')
    await userEvent.click(screen.getByRole('button', { name: /create guide/i }))

    await waitFor(() => {
      const post = calls.find(
        (call) => call.method === 'POST' && call.url.endsWith('/wf-118/guides'),
      )
      expect(post).toBeDefined()
      expect(JSON.parse(post.body)).toMatchObject({ key: 'brandnewkey', status: 'public' })
    })
  })

  it('lists the recorded decisions with their audit ids', async () => {
    stubFetch()
    render(<Component />)

    expect(await screen.findByText(/bypasses_gate/i)).toBeInTheDocument()
    expect(screen.getByText('jev-20261013T194510-856-10906')).toBeInTheDocument()
  })
})
