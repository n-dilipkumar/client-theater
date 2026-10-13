/**
 * Tests for WF-120's page and its pure helpers.
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
  backfillLabel,
  cadencePhrase,
  filterPhrase,
  formatDateTime,
  modeLabel,
  outcomeLabel,
  parseFilter,
  reasonLabel,
  statusLabel,
  statusTone,
} from './api.js'
import page from './index.jsx'

const { Component } = page

const NOW = Date.UTC(2026, 5, 1, 12, 0, 0)

const VOCABULARY = {
  ticket: 'WF-120',
  crm_objects: ['Contact', 'Company', 'Deal'],
  default_object: 'Contact',
  reenroll_modes: ['single', 'after_completion'],
  default_reenroll_mode: 'single',
  backfill_choices: ['existing_and_future', 'future_only'],
  default_backfill: 'existing_and_future',
  default_interval_minutes: 60,
  default_delay_minutes: 0,
  reason_labels: { matched: 'Enrolled', re_enrolled: 'Re-enrolled' },
  collections: { schedule: 'wf120_schedule' },
  user_flow: [
    { step: '1', text: 'Create a workflow and choose the Based on a schedule trigger.' },
    { step: '2', text: 'Turn Re-enroll on and set the enrollment filter.' },
  ],
}

const ROOMS = { count: 1, records: [{ id: 'room_1', data: { name: 'Northwind' } }] }

const SUMMARY = {
  records: 6,
  published: 1,
  enrolled_total: 3,
  queued: 1,
  completed: 2,
  unenrolled: 0,
}

function schedule(overrides = {}) {
  return {
    id: 'wf120_schedule_1',
    name: 'Scheduled enrollment demo',
    published: true,
    object: 'Contact',
    filters: [{ field: 'stage', op: 'eq', value: 'Qualification' }],
    filter_count: 1,
    unenroll_filter: [],
    unenroll_count: 0,
    delay_minutes: 0,
    interval_minutes: 60,
    re_enroll: 'single',
    backfill: 'existing_and_future',
    actions: ['Create task', 'Send internal notification'],
    action_count: 2,
    next_run_at_ms: NOW,
    ...overrides,
  }
}

const SCHEDULES = { count: 1, entries: [schedule()] }
const DETAIL = schedule()
const EVALUATION = {
  schedule_id: 'wf120_schedule_1',
  scanned: 2,
  matched: 1,
  re_enrollments: 0,
  queued: 0,
  queued_due: 0,
  decisions: [
    {
      record_id: 'rec_1',
      name: 'Northwind - Ada',
      object: 'Contact',
      matched: true,
      reason: 'matched',
      prior_status: null,
    },
    {
      record_id: 'rec_2',
      name: 'Northwind - Grace',
      object: 'Contact',
      matched: false,
      reason: 'no_filter_match',
      prior_status: null,
    },
  ],
}
const RUN = {
  id: 'wf120_run_1',
  kind: 'schedule',
  scanned: 2,
  enrolled: 1,
  re_enrolled: 0,
  released: 1,
  skipped: 1,
  failed: 0,
  by_reason: { matched: 1 },
  results: [
    {
      record_id: 'rec_1',
      name: 'Northwind - Ada',
      reason: 'matched',
      outcome: 'enrolled',
      task_id: 'wf116_task_1',
    },
    { record_id: 'rec_2', name: 'Northwind - Grace', reason: 'no_filter_match', outcome: 'skipped' },
  ],
}
const RUNS = {
  count: 1,
  entries: [
    {
      id: 'wf120_run_1',
      kind: 'schedule',
      at: '2026-06-01T12:00:00Z',
      scanned: 2,
      enrolled: 1,
      re_enrolled: 0,
      released: 1,
      failed: 0,
      by_reason: { matched: 1 },
    },
  ],
}
const ENROLLMENTS = {
  count: 1,
  entries: [
    {
      id: 'wf120_enrollment_1',
      record_id: 'rec_1',
      object: 'Contact',
      status: 'completed',
      re_enrollment: false,
      delay_minutes: 0,
      enrolled_at_ms: NOW,
      release_at_ms: NOW,
    },
  ],
}
const EVENTS = {
  count: 1,
  entries: [{ id: 'e1', kind: 'matched', record_id: 'rec_1', at_ms: NOW }],
}
const NOTIFICATIONS = {
  count: 1,
  entries: [
    {
      id: 'n1',
      message: '[Scheduled enrollment demo] Northwind - Ada completed the scheduled enrollment.',
      assignee: 'dana',
      at_ms: NOW,
    },
  ],
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
    '/wf-120/vocabulary': VOCABULARY,
    '/records/room': ROOMS,
    '/wf-120/summary': SUMMARY,
    '/wf-120/schedules/wf120_schedule_1/evaluate': EVALUATION,
    '/wf-120/schedules/wf120_schedule_1/run': RUN,
    '/wf-120/schedules/wf120_schedule_1': DETAIL,
    '/wf-120/schedules': SCHEDULES,
    '/wf-120/runs': RUNS,
    '/wf-120/enrollments': ENROLLMENTS,
    '/wf-120/events': EVENTS,
    '/wf-120/notifications': NOTIFICATIONS,
    ...overrides,
  }
}

// --------------------------------------------------------------------------- #
// Pure helpers
// --------------------------------------------------------------------------- #

describe('the pure helpers', () => {
  it('reads a filter written as lines', () => {
    expect(parseFilter('stage=Qualification')).toEqual([
      { field: 'stage', op: 'eq', value: 'Qualification' },
    ])
    expect(parseFilter('value>=1000')).toEqual([{ field: 'value', op: 'gte', value: '1000' }])
    expect(parseFilter('owner=a|b')).toEqual([{ field: 'owner', op: 'in', value: ['a', 'b'] }])
    expect(parseFilter('nonsense line')).toEqual([])
  })

  it('labels every state, mode, outcome and reason', () => {
    expect(statusLabel('completed')).toBe('Completed')
    expect(statusTone('unenrolled')).toBe('delete')
    expect(modeLabel('after_completion')).toBe('Re-enroll after completion')
    expect(backfillLabel('future_only')).toBe('Only records that match after turning it on')
    expect(outcomeLabel('released')).toBe('Actions fired')
    expect(reasonLabel('re_enrolled')).toBe('Re-enrolled')
  })

  it('describes a filter and a cadence', () => {
    expect(filterPhrase(schedule())).toContain('stage = Qualification')
    expect(filterPhrase({ filters: [] })).toBe('Every record of the object')
    expect(cadencePhrase(schedule())).toContain('Runs every 60 minutes')
    expect(cadencePhrase(schedule({ delay_minutes: 30 }))).toContain('queued for 30 minutes')
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

    expect(await screen.findByText('Scheduled enrollment')).toBeTruthy()
    expect(screen.getByText('Published schedules')).toBeTruthy()
    expect(screen.getByText('Queued')).toBeTruthy()
    expect(await screen.findByLabelText('Schedule name')).toBeTruthy()
    expect(screen.getByLabelText('Enrollment filter')).toBeTruthy()
    expect(screen.getByLabelText('Re-enroll')).toBeTruthy()
  })

  it('lists a schedule and shows its settings once selected', async () => {
    stubFetch(pageRoutes())
    render(<Component />)

    const row = await screen.findByRole('button', { name: /Scheduled enrollment demo/ })
    await userEvent.click(row)

    expect(await screen.findByText('Next run')).toBeTruthy()
    expect(screen.getByText('Queue delay')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Run schedule' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Preview' })).toBeTruthy()
  })

  it('previews the records the schedule would enrol', async () => {
    stubFetch(pageRoutes())
    render(<Component />)

    await userEvent.click(await screen.findByRole('button', { name: /Scheduled enrollment demo/ }))
    await userEvent.click(await screen.findByRole('button', { name: 'Preview' }))

    expect(await screen.findByText(/2 scanned · 1 to enrol/)).toBeTruthy()
    expect(screen.getByText('Did not meet the filter')).toBeTruthy()
  })

  it('runs the schedule and shows the run results', async () => {
    const calls = stubFetch(pageRoutes())
    render(<Component />)

    await userEvent.click(await screen.findByRole('button', { name: /Scheduled enrollment demo/ }))
    await userEvent.click(await screen.findByRole('button', { name: 'Run schedule' }))

    expect(
      calls.some(
        (call) =>
          call.method === 'POST' && call.url.includes('/wf-120/schedules/wf120_schedule_1/run'),
      ),
    ).toBe(true)
    expect(await screen.findByText(/task wf116_task_1/)).toBeTruthy()
  })

  it('posts a new schedule from the form', async () => {
    const calls = stubFetch(pageRoutes())
    render(<Component />)

    await screen.findByText('Scheduled enrollment')
    await userEvent.click(screen.getByRole('button', { name: 'Create schedule' }))

    expect(calls.some((call) => call.method === 'POST' && call.url.includes('/wf-120/schedules'))).toBe(
      true,
    )
  })

  it('shows the enrollment, the workflow log and the researched user flow', async () => {
    stubFetch(pageRoutes())
    render(<Component />)

    expect(
      await screen.findByText(
        '[Scheduled enrollment demo] Northwind - Ada completed the scheduled enrollment.',
      ),
    ).toBeTruthy()
    expect(screen.getByText('Workflow log')).toBeTruthy()
    expect(screen.getByText('The researched user flow')).toBeTruthy()
  })
})
