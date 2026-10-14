/**
 * Tests for WF-119's page.
 *
 * The page is driven against a stubbed `fetch`, so the assertions are about what
 * a reviewer reads and which controls are offered, not about a live server.
 * Every test builds its own stub and its own data, so the file passes alone and
 * under vitest's parallel run without depending on another test's fixture.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import page from './index.jsx'
import {
  contactLabel,
  extrasLabel,
  gatePayload,
  hasLapsed,
  pauseEnvelope,
  remainingLabel,
  resumeBody,
  stateLabel,
  stateTone,
  windowLabel,
} from './api.js'

const { Component } = page

const VOCABULARY = {
  ticket: 'WF-119',
  prefix: '/api/wf-119',
  envelope: { extras_keys: ['locationId', 'contactId', 'workflowId'], branch_key: 'branchId' },
  hold_window: { default_seconds: 259200, min_seconds: 300, max_seconds: 2592000 },
  resume: {
    mode: 'async',
    rejected_mode: 'sync',
    audit_id: 'jev-20261014T105812-18180-92928',
    success_url_outcome: 'approved',
    failed_url_outcome: 'refused',
  },
}

const DECISIONS = {
  resume_mode: {
    mode: 'async',
    rejected: 'sync',
    audit_id: 'jev-20261014T105812-18180-92928',
    confidence: 1,
  },
  count: 1,
  decisions: [
    {
      id: 'RESUME_MODE_IS_ASYNC',
      question: 'The spec gives two resume modes and does not choose.',
      choice: 'The resume webhook carries the branch id in its body.',
      rejected: 'Sync: resuming from the pause response.',
      audit_id: 'jev-20261014T105812-18180-92928',
      verdict: "pass; chose 'async'",
    },
  ],
  not_built: [{ item: 'A mandatory Test Code step', why: 'It is not a rule about this repository.' }],
}

const SUMMARY = {
  gates: 1,
  pause_enabled: 1,
  holds: 3,
  by_state: { held: 1, resumed: 1, expired: 1 },
  expired_lazily: 1,
  resumes: 1,
  resume_mode: 'async',
}

const GATE = {
  key: 'send_proposal',
  name: 'Approve before the proposal is sent',
  workflow_id: 'wf-demo',
  action_key: 'pause_for_approval',
  pause_execution: true,
  hold_seconds: 259200,
  branches: { approved: 'branch_yes', refused: 'branch_no' },
  action_url: '/api/wf-119/gates/send_proposal/pause',
  resume_urls: {
    approved: '/api/wf-119/hooks/tok/approved',
    refused: '/api/wf-119/hooks/tok/refused',
  },
}

const HELD = {
  id: 'wf119_hold_1',
  gate_key: 'send_proposal',
  extras: { locationId: 'loc_northwind', contactId: 'con_waiting', workflowId: 'wf-demo' },
  state: 'held',
  outcome: null,
  branch: null,
  expired: false,
  can_resume: true,
  expires_at: '2026-10-17T09:00:00.000+00:00',
  expires_in_seconds: 259200,
}

const APPROVED = {
  ...HELD,
  id: 'wf119_hold_2',
  extras: { ...HELD.extras, contactId: 'con_approved' },
  state: 'resumed',
  outcome: 'approved',
  branch: 'branch_yes',
  can_resume: false,
}

const EXPIRED = {
  ...HELD,
  id: 'wf119_hold_3',
  extras: { ...HELD.extras, contactId: 'con_lapsed' },
  state: 'held',
  expired: true,
  can_resume: false,
  expires_in_seconds: -3600,
}

function jsonResponse(body, status = 200) {
  return { ok: status < 400, status, json: async () => body }
}

function stubFetch({ holds = [HELD, APPROVED, EXPIRED] } = {}) {
  const calls = []
  const handler = async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET', body: options.body })
    if (url.includes('/wf-119/vocabulary')) return jsonResponse(VOCABULARY)
    if (url.includes('/wf-119/decisions')) return jsonResponse(DECISIONS)
    if (url.includes('/wf-119/summary')) return jsonResponse(SUMMARY)
    if (url.includes('/wf-119/gates') && (options.method || 'GET') === 'GET') {
      return jsonResponse({ count: 1, gates: [GATE] })
    }
    if (url.includes('/wf-119/holds')) return jsonResponse({ count: holds.length, holds })
    if (url.includes('/wf-119/sweep')) return jsonResponse({ expired: 1, holds: [] })
    if (url.includes('/resume')) return jsonResponse({ outcome: 'approved', replay: false })
    if (url.includes('/pause')) return jsonResponse({ held: true, hold: HELD })
    return jsonResponse({ error: 'no stub', detail: `no stub for ${url}` }, 404)
  }
  global.fetch = vi.fn(handler)
  return calls
}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe('the descriptor', () => {
  it('exports the four fields the host discovers', () => {
    expect(page.id).toBe('wf-119-approval-gate')
    expect(page.label).toBe('Approval gate')
    expect(page.icon).toBeTruthy()
    expect(typeof page.iconPath).toBe('string')
    expect(typeof page.order).toBe('number')
    expect(typeof Component).toBe('function')
  })
})

describe('the helpers', () => {
  it('reads a hold as held, approved, refused or expired', () => {
    expect(stateLabel(HELD)).toBe('Held')
    expect(stateLabel(APPROVED)).toBe('Approved')
    expect(stateLabel({ ...APPROVED, outcome: 'refused' })).toBe('Refused')
    expect(stateLabel(EXPIRED)).toBe('Expired')
  })

  it('reads expiry before the stored state, because the server derives it lazily', () => {
    expect(hasLapsed(EXPIRED)).toBe(true)
    expect(hasLapsed(HELD)).toBe(false)
    expect(stateLabel({ ...EXPIRED, state: 'resumed', expired: true })).toBe('Expired')
  })

  it('gives each state a distinct tone so colour and word never disagree', () => {
    expect(stateTone(HELD)).toBe('update')
    expect(stateTone(APPROVED)).toBe('insert')
    expect(stateTone({ ...APPROVED, outcome: 'refused' })).toBe('delete')
    expect(stateTone(EXPIRED)).toBe('restore')
  })

  it('reads the derived hold window in whole days and hours', () => {
    expect(windowLabel(259200)).toBe('3 days')
    expect(windowLabel(86400)).toBe('1 day')
    expect(windowLabel(7200)).toBe('2 hours')
    expect(windowLabel(90)).toBe('90 seconds')
    expect(windowLabel(undefined)).toBe('—')
  })

  it('reads the server countdown without inventing one', () => {
    expect(remainingLabel(HELD)).toBe('in 3 d')
    expect(remainingLabel(EXPIRED)).toBe('Lapsed')
    expect(remainingLabel(null)).toBe('—')
  })

  it('names the contact and the whole correlation key', () => {
    expect(contactLabel(HELD.extras)).toBe('con_waiting')
    expect(contactLabel(undefined)).toBe('—')
    expect(extrasLabel(HELD.extras)).toBe('loc_northwind / con_waiting / wf-demo')
  })

  it('builds a resume body from the gates own branch, never an invented one', () => {
    expect(resumeBody('approved', GATE)).toEqual({
      outcome: 'approved',
      branchId: 'branch_yes',
      approver: undefined,
    })
    expect(resumeBody('refused', GATE, 'ravi')).toEqual({
      outcome: 'refused',
      branchId: 'branch_no',
      approver: 'ravi',
    })
    expect(resumeBody('approved', undefined).branchId).toBe('')
  })

  it('builds the documented action envelope', () => {
    const envelope = pauseEnvelope({
      locationId: 'loc',
      contactId: 'con_1',
      workflowId: 'wf-demo',
      metaKey: 'proposal-approval',
      version: '2',
    })
    expect(envelope.extras).toEqual({ locationId: 'loc', contactId: 'con_1', workflowId: 'wf-demo' })
    expect(envelope.meta).toEqual({ key: 'proposal-approval', version: 2 })
    expect(envelope.data).toEqual({})
  })

  it('builds the gate payload with both branches', () => {
    const payload = gatePayload({
      key: 'send_proposal',
      name: 'Approve',
      workflowId: 'wf-demo',
      actionKey: 'pause',
      pauseExecution: true,
      approvedBranch: 'branch_yes',
      refusedBranch: 'branch_no',
    })
    expect(payload).toEqual({
      key: 'send_proposal',
      workflow_id: 'wf-demo',
      action_key: 'pause',
      name: 'Approve',
      pause_execution: true,
      branches: { approved: 'branch_yes', refused: 'branch_no' },
    })
  })
})

describe('the page', () => {
  it('renders the summary, the gate and the two resume URLs', async () => {
    stubFetch()
    render(<Component />)

    expect(await screen.findByText('Approve before the proposal is sent')).toBeInTheDocument()
    expect(screen.getByText('Waiting on a human')).toBeInTheDocument()
    expect(screen.getByText('1 lapsed with no decision')).toBeInTheDocument()
    expect(screen.getByText('Success execution')).toBeInTheDocument()
    expect(screen.getByText('Failed execution')).toBeInTheDocument()
    expect(screen.getByText('/api/wf-119/hooks/tok/approved')).toBeInTheDocument()
    expect(screen.getByText('Pause on')).toBeInTheDocument()
  })

  it('labels every hold state in words as well as a tone', async () => {
    stubFetch()
    render(<Component />)

    expect(await screen.findByText('con_waiting')).toBeInTheDocument()
    expect(screen.getByText('con_approved')).toBeInTheDocument()
    expect(screen.getByText('con_lapsed')).toBeInTheDocument()
    expect(screen.getAllByText('Held').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Approved').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Expired').length).toBeGreaterThan(0)
    expect(screen.getByText('Lapsed with no decision')).toBeInTheDocument()
    expect(screen.getByText('Approved on branch_yes')).toBeInTheDocument()
  })

  it('offers Approve and Refuse only while a hold can still be resumed', async () => {
    stubFetch()
    render(<Component />)
    await screen.findByText('con_waiting')

    const approve = screen.getAllByRole('button', { name: /^Approve:/i })
    expect(approve).toHaveLength(3)
    expect(approve[0]).toBeEnabled()
    expect(approve[1]).toBeDisabled()
    expect(approve[2]).toBeDisabled()
  })

  it('resumes a hold through the in-app route with the gates branch', async () => {
    const calls = stubFetch()
    render(<Component />)
    await screen.findByText('con_waiting')

    await userEvent.click(screen.getAllByRole('button', { name: /^Approve:/i })[0])

    await waitFor(() => {
      const post = calls.find((call) => call.url.endsWith('/holds/wf119_hold_1/resume'))
      expect(post).toBeDefined()
      expect(JSON.parse(post.body)).toEqual({
        outcome: 'approved',
        branchId: 'branch_yes',
        approver: undefined,
      })
    })
  })

  it('refuses a hold on the refused branch', async () => {
    const calls = stubFetch()
    render(<Component />)
    await screen.findByText('con_waiting')

    await userEvent.click(screen.getAllByRole('button', { name: /^Refuse:/i })[0])

    await waitFor(() => {
      const post = calls.find((call) => call.url.endsWith('/holds/wf119_hold_1/resume'))
      expect(JSON.parse(post.body)).toEqual({
        outcome: 'refused',
        branchId: 'branch_no',
        approver: undefined,
      })
    })
  })

  it('sweeps lapsed holds from the page', async () => {
    const calls = stubFetch()
    render(<Component />)
    await screen.findByText('con_waiting')

    await userEvent.click(screen.getByRole('button', { name: /expire lapsed holds/i }))

    await waitFor(() => {
      expect(calls.some((call) => call.url.endsWith('/wf-119/sweep'))).toBe(true)
    })
  })

  it('shows an empty state rather than a blank list', async () => {
    stubFetch({ holds: [] })
    render(<Component />)

    expect(await screen.findByText(/no contact is held/i)).toBeInTheDocument()
  })

  it('creates a gate from the form', async () => {
    const calls = stubFetch()
    render(<Component />)
    await screen.findByText('con_waiting')

    await userEvent.type(screen.getByLabelText(/gate key/i), 'send_invoice')
    await userEvent.click(screen.getByRole('button', { name: /^create gate$/i }))

    await waitFor(() => {
      const post = calls.find(
        (call) => call.method === 'POST' && call.url.endsWith('/wf-119/gates'),
      )
      expect(post).toBeDefined()
      expect(JSON.parse(post.body)).toMatchObject({
        key: 'send_invoice',
        pause_execution: true,
        branches: { approved: 'branch_proposal_approved', refused: 'branch_proposal_refused' },
      })
    })
  })

  it('holds a contact with the documented envelope', async () => {
    const calls = stubFetch()
    render(<Component />)
    await screen.findByText('con_waiting')

    await userEvent.clear(screen.getByLabelText(/contact id/i))
    await userEvent.type(screen.getByLabelText(/contact id/i), 'con_new')
    await userEvent.click(screen.getByRole('button', { name: /hold this contact/i }))

    await waitFor(() => {
      const post = calls.find((call) => call.url.endsWith('/gates/send_proposal/pause'))
      expect(post).toBeDefined()
      expect(JSON.parse(post.body).extras.contactId).toBe('con_new')
      expect(JSON.parse(post.body).meta).toEqual({ key: 'proposal-approval', version: 1 })
    })
  })

  it('lists the resume-mode decision with its Jev audit id', async () => {
    stubFetch()
    render(<Component />)

    expect(await screen.findByText('Resume mode: async')).toBeInTheDocument()
    expect(screen.getByText('RESUME_MODE_IS_ASYNC')).toBeInTheDocument()
    expect(screen.getAllByText(/jev-20261014T105812-18180-92928/).length).toBeGreaterThan(0)
    expect(screen.getByText(/A mandatory Test Code step/)).toBeInTheDocument()
  })

  it('shows an error state when the API refuses', async () => {
    global.fetch = vi.fn(async () => jsonResponse({ detail: 'gate_not_found' }, 404))
    render(<Component />)

    expect(await screen.findByText(/could not load data/i)).toBeInTheDocument()
  })
})
