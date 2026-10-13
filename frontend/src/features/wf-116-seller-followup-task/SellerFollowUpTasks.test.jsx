/**
 * Tests for WF-116's page and its pure helpers.
 *
 * The page is driven against a stubbed `fetch` rather than a live server, so the
 * assertions are about what a seller reads and which controls are offered, not
 * about the server's answer. Every test builds its own stub and its own data, so
 * the file passes alone and under vitest's parallel run.
 */

import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import page from './index.jsx'
import {
  completeBlockedReason,
  duePhrase,
  fromLocalInput,
  isReminderDue,
  priorityLabel,
  reminderBlockedReason,
  statusLabel,
  statusTone,
  toLocalInput,
  typeLabel,
} from './api.js'

const { Component } = page

const NOW = Date.UTC(2026, 5, 1, 12, 0, 0)

const VOCABULARY = {
  ticket: 'WF-116',
  statuses: ['NOT_STARTED', 'COMPLETED'],
  priorities: ['LOW', 'MEDIUM', 'HIGH'],
  task_types: ['EMAIL', 'CALL', 'TODO'],
  triggers: ['Task Added', 'Task Reminder', 'Task Completed'],
  signals: ['inbound_webhook', 'engagement_score_rule_met'],
  required_fields: ['due_at'],
  reminder_unit: 'unix_ms',
  collections: { task: 'wf116_task' },
}

const ROOMS = { count: 1, records: [{ id: 'room_1', data: { name: 'Northwind' } }] }

const SUMMARY = {
  tasks: 2,
  open: 1,
  completed: 1,
  reminders_due: 1,
  binned: 1,
  attached: 1,
  standalone: 1,
}

function taskRecord(overrides = {}) {
  return {
    id: 'task_1',
    subject: 'Chase the order form',
    body: 'No contact. This is a standalone internal task.',
    status: 'NOT_STARTED',
    priority: 'HIGH',
    task_type: 'TODO',
    due_at_ms: NOW + 2 * 24 * 60 * 60 * 1000,
    reminder_at_ms: NOW + 24 * 60 * 60 * 1000,
    reminder_fired_at_ms: null,
    completed_at_ms: null,
    assignee: 'dana',
    attachment: 'standalone',
    contact_id: null,
    standalone: true,
    attached: false,
    signal: null,
    ...overrides,
  }
}

const TASKS = {
  count: 1,
  entries: [taskRecord()],
}

const DETAIL = {
  ...taskRecord(),
  events: [
    { id: 'e1', trigger: 'Task Added', at_ms: NOW },
    { id: 'e2', trigger: 'Task Reminder', at_ms: NOW + 60 * 1000 },
  ],
  notifications: [],
}

const CONTACTS = { count: 1, entries: [{ id: 'c1', label: 'dana@northwind.example' }] }

const BIN = { count: 1, entries: [taskRecord({ id: 'task_bin', subject: 'Duplicate (binned)' })] }

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
    '/wf-116/vocabulary': VOCABULARY,
    '/records/room': ROOMS,
    '/wf-116/summary': SUMMARY,
    '/wf-116/tasks/task_1': DETAIL,
    '/wf-116/tasks': TASKS,
    '/wf-116/contacts': CONTACTS,
    '/wf-116/bin': BIN,
    ...overrides,
  }
}

// --------------------------------------------------------------------------- #
// Pure helpers
// --------------------------------------------------------------------------- #


describe('the pure helpers', () => {
  it('labels every closed value the research names', () => {
    expect(statusLabel('NOT_STARTED')).toBe('Not started')
    expect(statusLabel('COMPLETED')).toBe('Completed')
    expect(typeLabel('EMAIL')).toBe('Email')
    expect(typeLabel('CALL')).toBe('Call')
    expect(typeLabel('TODO')).toBe('To-do')
    expect(priorityLabel('HIGH')).toBe('High')
    expect(statusTone('COMPLETED')).toBe('restore')
  })

  it('describes a due time against a supplied reference instant', () => {
    expect(duePhrase(taskRecord(), NOW)).toContain('2 days')
    expect(duePhrase(taskRecord({ due_at_ms: NOW - 1000 }), NOW)).toContain('Overdue')
    expect(duePhrase(taskRecord({ status: 'COMPLETED' }), NOW)).toContain('Completed')
  })

  it('reports a reminder as due only when it is open, unfired and reached', () => {
    const past = taskRecord({ reminder_at_ms: NOW - 1000 })
    expect(isReminderDue(past, NOW)).toBe(true)
    expect(isReminderDue(taskRecord(), NOW)).toBe(false)
    expect(isReminderDue(taskRecord({ reminder_fired_at_ms: NOW }), NOW)).toBe(false)
    expect(isReminderDue(taskRecord({ status: 'COMPLETED', reminder_at_ms: NOW - 1 }), NOW)).toBe(
      false,
    )
  })

  it('names why a reminder cannot fire', () => {
    expect(reminderBlockedReason(taskRecord({ status: 'COMPLETED' }))).toContain('completed')
    expect(reminderBlockedReason(taskRecord({ reminder_at_ms: null }))).toContain('no reminder time')
    expect(reminderBlockedReason(taskRecord({ reminder_fired_at_ms: NOW }))).toContain('already fired')
    expect(reminderBlockedReason(taskRecord())).toBeNull()
  })

  it('names why a task cannot be completed', () => {
    expect(completeBlockedReason(taskRecord({ status: 'COMPLETED' }))).toContain('already completed')
    expect(completeBlockedReason(taskRecord())).toBeNull()
  })

  it('round-trips a datetime-local value', () => {
    const onTheMinute = Date.UTC(2026, 5, 1, 12, 30, 0)
    expect(fromLocalInput(toLocalInput(onTheMinute))).toBe(new Date(onTheMinute).toISOString())
    expect(fromLocalInput('')).toBeUndefined()
    expect(toLocalInput(null)).toBe('')
  })
})

// --------------------------------------------------------------------------- #
// The page
// --------------------------------------------------------------------------- #


describe('the page', () => {
  it('renders the board, its counts and the create form', async () => {
    stubFetch(pageRoutes())
    render(<Component />)

    expect(await screen.findByText('Follow-up tasks')).toBeTruthy()
    expect(screen.getByText('Reminders due')).toBeTruthy()
    expect(screen.getByText('Recycling bin')).toBeTruthy()
    expect(await screen.findByLabelText('Due time')).toBeTruthy()
    expect(screen.getByLabelText('Reminder time')).toBeTruthy()
  })

  it('lists a task and shows its lifecycle triggers once selected', async () => {
    stubFetch(pageRoutes())
    render(<Component />)

    const row = await screen.findByRole('button', { name: /Chase the order form/ })
    await userEvent.click(row)

    expect(await screen.findByText('Lifecycle triggers')).toBeTruthy()
    expect(screen.getByText('Task Reminder')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Complete' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Fire reminder' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Delete' })).toBeTruthy()
  })

  it('disables the reminder control and explains why once it has fired', async () => {
    const fired = {
      ...DETAIL,
      reminder_fired_at_ms: NOW,
      events: [{ id: 'e1', trigger: 'Task Added', at_ms: NOW }],
    }
    stubFetch(pageRoutes({ '/wf-116/tasks/task_1': fired }))
    render(<Component />)

    const row = await screen.findByRole('button', { name: /Chase the order form/ })
    await userEvent.click(row)

    const fire = await screen.findByRole('button', { name: 'Fire reminder' })
    expect(fire.disabled).toBe(true)
    expect(screen.getByText(/already fired/)).toBeTruthy()
  })

  it('fills the create form and posts a task', async () => {
    const calls = stubFetch(pageRoutes())
    render(<Component />)

    await screen.findByText('Follow-up tasks')
    await userEvent.type(await screen.findByLabelText('Subject'), 'Call the buyer')
    fireEvent.change(screen.getByLabelText('Due time'), { target: { value: '2026-06-03T09:00' } })
    await userEvent.click(screen.getByRole('button', { name: 'Create task' }))

    expect(calls.some((call) => call.method === 'POST' && call.url.includes('/wf-116/tasks'))).toBe(
      true,
    )
  })

  it('offers only the closed priority and type values', async () => {
    stubFetch(pageRoutes())
    render(<Component />)

    const priority = await screen.findByLabelText('Priority')
    expect([...priority.options].map((option) => option.value)).toEqual(['LOW', 'MEDIUM', 'HIGH'])
    const type = screen.getByLabelText('Type')
    expect([...type.options].map((option) => option.value)).toEqual(['EMAIL', 'CALL', 'TODO'])
  })
})
