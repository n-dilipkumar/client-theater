/**
 * Tests for WF-117's page and its pure helpers.
 *
 * The page is driven against a stubbed `fetch` rather than a live server, so the
 * assertions are about what a seller reads and which controls are offered, not
 * about the server's answer. Every test builds its own stub and its own data, so
 * the file passes alone and under vitest's parallel run.
 */

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import {
  cadencePhrase,
  filterPhrase,
  formatDateTime,
  idlePhrase,
  outcomeLabel,
  reasonLabel,
  reasonTone,
  stepLabel,
} from './api.js'
import page from './index.jsx'

const { Component } = page

const NOW = Date.UTC(2026, 5, 1, 12, 0, 0)

const VOCABULARY = {
  ticket: 'WF-117',
  stale_references: ['last_activity', 'stage_entry', 'latest_of_both'],
  default_stale_reference: 'latest_of_both',
  default_threshold_days: 7,
  default_cadence: [
    { after_days: 7, action: 'nudge' },
    { after_days: 21, action: 'escalate' },
  ],
  reason_labels: { matched: 'Matched' },
  collections: { sweep: 'wf117_sweep' },
  user_flow: [
    { step: '1', text: 'Create a workflow and choose the trigger Stale Opportunities.' },
    { step: '2', text: 'Define the inactivity/stale rule and add filters.' },
  ],
}

const ROOMS = { count: 1, records: [{ id: 'room_1', data: { name: 'Northwind' } }] }

const SUMMARY = { opportunities: 6, published: 1, runs: 2, escalated_total: 2, notifications: 2 }

function sweep(overrides = {}) {
  return {
    id: 'wf117_sweep_1',
    name: 'Stale opportunity sweep',
    published: true,
    threshold_days: 7,
    inactivity_basis: 'latest_of_both',
    refire_control: 'marker_on_opportunity',
    cadence: [
      { after_days: 7, action: 'nudge' },
      { after_days: 21, action: 'escalate' },
    ],
    cadence_steps: 2,
    filters: { stages: ['Closed Won'], stage_mode: 'exclude', value_min: 10000, value_max: 500000 },
    ...overrides,
  }
}

const SWEEPS = { count: 1, entries: [sweep()] }
const DETAIL = sweep()
const RUNS = {
  count: 2,
  entries: [
    { id: 'run_2', at: '2026-06-01T13:00:00Z', scanned: 6, escalated: 0, failed: 0, by_reason: { already_escalated: 2 } },
    { id: 'run_1', at: '2026-06-01T12:00:00Z', scanned: 6, escalated: 2, failed: 0, by_reason: { matched: 2 } },
  ],
}
const RUN = {
  id: 'run_1',
  scanned: 6,
  matched: 2,
  escalated: 2,
  skipped: 4,
  failed: 0,
  by_reason: { matched: 2 },
  results: [
    { opportunity_id: 'opp_1', name: 'Northwind', reason: 'matched', outcome: 'escalated', idle_days: 25, step: 'escalate', task_id: 'wf116_task_1' },
    { opportunity_id: 'opp_2', name: 'Fabrikam', reason: 'below_threshold', outcome: 'skipped', idle_days: 2 },
  ],
}
const EVALUATION = {
  sweep_id: 'wf117_sweep_1',
  scanned: 6,
  matched: 2,
  decisions: [
    { opportunity_id: 'opp_1', name: 'Northwind', stage: 'Qualification', reason: 'matched', matched: true, idle_days: 25, step: { action: 'escalate' } },
    { opportunity_id: 'opp_2', name: 'Fabrikam', stage: 'Negotiation', reason: 'below_threshold', matched: false, idle_days: 2 },
  ],
}
const ESCALATIONS = { count: 1, entries: [{ id: 'e1', step: 'escalate', at_ms: NOW, opportunity_id: 'opp_1' }] }
const NOTIFICATIONS = {
  count: 1,
  entries: [{ id: 'n1', message: '[escalate] Northwind has been idle.', team: 'sales', assignee: 'dana', at_ms: NOW }],
}

function jsonResponse(body, status = 200) {
  return { ok: status < 400, status, json: async () => body }
}

function stubFetch(routes) {
  const calls = []
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
    '/wf-117/vocabulary': VOCABULARY,
    '/records/room': ROOMS,
    '/wf-117/summary': SUMMARY,
    '/wf-117/sweeps/wf117_sweep_1/evaluate': EVALUATION,
    '/wf-117/sweeps/wf117_sweep_1/run': RUN,
    '/wf-117/sweeps/wf117_sweep_1': DETAIL,
    '/wf-117/sweeps': SWEEPS,
    '/wf-117/runs': RUNS,
    '/wf-117/escalations': ESCALATIONS,
    '/wf-117/notifications': NOTIFICATIONS,
    ...overrides,
  }
}

// --------------------------------------------------------------------------- #
// Pure helpers
// --------------------------------------------------------------------------- #


describe('the pure helpers', () => {
  it('labels every reason, step and outcome', () => {
    expect(reasonLabel('matched')).toBe('Matched')
    expect(reasonLabel('already_escalated')).toBe('Already escalated')
    expect(reasonTone('matched')).toBe('insert')
    expect(reasonTone('no_activity_evidence')).toBe('neutral')
    expect(stepLabel('nudge')).toBe('Nudge')
    expect(outcomeLabel('escalated')).toBe('Escalated')
    expect(outcomeLabel('failed')).toBe('Failed')
  })

  it('describes how long an opportunity has been idle', () => {
    expect(idlePhrase({ idle_days: 25, reference_field: 'last_activity' })).toContain('Idle 25 days')
    expect(idlePhrase({ idle_days: 1, reference_field: 'stage_entry' })).toContain('1 day')
    expect(idlePhrase({ idle_days: null })).toBe('No activity evidence')
  })

  it('describes a cadence and its filters', () => {
    expect(cadencePhrase(sweep())).toContain('Nudge at 7 days')
    expect(cadencePhrase(sweep())).toContain('Escalate at 21 days')
    expect(filterPhrase(sweep())).toContain('Excluding Closed Won')
    expect(filterPhrase({ filters: {} })).toBe('No filters')
  })

  it('formats a timestamp or a dash', () => {
    expect(formatDateTime(null)).toBe('-')
    expect(formatDateTime(NOW)).not.toBe('-')
  })
})

// --------------------------------------------------------------------------- #
// The page
// --------------------------------------------------------------------------- #


describe('the page', () => {
  it('renders the board, its counts and the create form', async () => {
    stubFetch(pageRoutes())
    render(<Component />)

    expect(await screen.findByText('Stale opportunities')).toBeTruthy()
    expect(screen.getByText('Published sweeps')).toBeTruthy()
    expect(screen.getByText('Escalated')).toBeTruthy()
    expect(await screen.findByLabelText('Sweep name')).toBeTruthy()
    expect(screen.getByLabelText('Inactivity threshold (days)')).toBeTruthy()
  })

  it('lists a sweep and shows its rule once selected', async () => {
    stubFetch(pageRoutes())
    render(<Component />)

    const row = await screen.findByRole('button', { name: /Stale opportunity sweep/ })
    await userEvent.click(row)

    expect(await screen.findByText('Re-fire control')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Run sweep' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Preview' })).toBeTruthy()
  })

  it('previews the decisions the sweep would take', async () => {
    stubFetch(pageRoutes())
    render(<Component />)

    await userEvent.click(await screen.findByRole('button', { name: /Stale opportunity sweep/ }))
    await userEvent.click(await screen.findByRole('button', { name: 'Preview' }))

    expect(await screen.findByText(/6 scanned · 2 matched/)).toBeTruthy()
    expect(screen.getByText('Below the threshold')).toBeTruthy()
  })

  it('runs the sweep and shows the run results', async () => {
    const calls = stubFetch(pageRoutes())
    render(<Component />)

    await userEvent.click(await screen.findByRole('button', { name: /Stale opportunity sweep/ }))
    await userEvent.click(await screen.findByRole('button', { name: 'Run sweep' }))

    expect(
      calls.some((call) => call.method === 'POST' && call.url.includes('/wf-117/sweeps/wf117_sweep_1/run')),
    ).toBe(true)
    expect(await screen.findByText(/task wf116_task_1/)).toBeTruthy()
  })

  it('posts a new sweep from the form', async () => {
    const calls = stubFetch(pageRoutes())
    render(<Component />)

    await screen.findByText('Stale opportunities')
    await userEvent.click(screen.getByRole('button', { name: 'Create and publish' }))

    expect(calls.some((call) => call.method === 'POST' && call.url.includes('/wf-117/sweeps'))).toBe(
      true,
    )
  })

  it('shows the recorded notification and the researched user flow', async () => {
    stubFetch(pageRoutes())
    render(<Component />)

    expect(await screen.findByText('[escalate] Northwind has been idle.')).toBeTruthy()
    expect(screen.getByText('The researched user flow')).toBeTruthy()
  })
})
