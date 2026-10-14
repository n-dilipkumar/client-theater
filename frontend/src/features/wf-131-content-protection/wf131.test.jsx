/**
 * Tests for WF-131's page and its primitives.
 *
 * The page is driven against a stubbed `fetch` rather than a live server, so the
 * assertions are about what a seller reads and which controls are offered, not
 * about the server's answer. Every test builds its own stub and its own data, so
 * the file passes alone and under vitest's parallel run.
 *
 * Three of these tests are about the researched facts rather than rendering: the
 * page names the three capabilities it declines, a decision names the protection
 * that refused rather than only that something did, and an allow-list summary
 * prints the open case as a word.
 */

import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import page from './index.jsx'
import {
  ACTION_LABELS,
  REASON_LABELS,
  STATE_LABELS,
  decisionLabel,
  shortTimestamp,
  splitAllowlist,
  stateOf,
  stateTone,
} from './api.js'
import {
  ActionRow,
  AllowListPreview,
  DecisionCard,
  InlineNote,
  ProtectionBadges,
  StatusBadge,
} from './primitives.jsx'

const { Component } = page

const VOCABULARY = {
  ticket: 'WF-131',
  fields: [
    { field: 'email_protected', label: 'Email verification', source: 'WF-069', reused: true },
    { field: 'ip_allowlist', label: 'IP allow-list', source: 'WF-131', reused: false },
  ],
  actions: [
    { action: 'view', code: 8 },
    { action: 'download', code: 1010 },
    { action: 'share', code: 12 },
    { action: 'revoke', code: 13 },
  ],
  reused: { link_collection: 'wf069_gated_link' },
  plan: { requires: 'business' },
}

const HONESTY = {
  limitation:
    'The research states that no open-source project implements a full audit log with revocation semantics.',
  declined: [
    { claim: 'end-to-end encryption', why: 'No source names how it would be built.' },
    { claim: 'certifications (SOC 2 / ISO 27001)', why: 'A certification is an audit outcome.' },
    { claim: 'RBAC', why: "It is WF-077's surface." },
  ],
}

const POLICY = {
  id: 'pol_1',
  room_id: 'room_1',
  link_id: 'wf069_link_1',
  link_collection: 'wf069_gated_link',
  recipient: 'a.buyer@northwind.example',
  policy: {
    email_protected: true,
    email_authenticated: false,
    require_passcode: true,
    enable_agreement: true,
    agreement_id: 'agr_1',
    expires_at: null,
    enable_watermark: true,
    allow_download: false,
    ip_allowlist: ['203.0.113.24/32'],
  },
  revoked: false,
  revoked_at: null,
  revoked_by: null,
  updated_at: '2026-05-01T12:00:00.000Z',
  created_at: '2026-05-01T12:00:00.000Z',
}

const DECISION = {
  decision: 'deny',
  allowed: false,
  denied_by: ['ip'],
  reasons: ['ip_not_allowed'],
  message: 'This link cannot be opened from your network. Ask the sender.',
  protections: {
    ip: { allowed: false, required: true, detail: 'The client address is not in the allow-list.' },
    watermark: { allowed: true, required: true, detail: 'Each view is watermarked.' },
  },
  download: { download: false, blocked_by: ['link_allow_download'], gates: {}, reason: 'no' },
}

function payload(overrides = {}) {
  return {
    '/vocabulary': VOCABULARY,
    '/honesty': HONESTY,
    '/summary': {
      policies: 1,
      recipients: 1,
      revoked: 0,
      watermarked: 1,
      allowlisted: 1,
      actions: 3,
      denied: 1,
    },
    '/policies': { count: 1, room_id: 'room_1', policies: [POLICY] },
    pol_1: POLICY,
    '/actions': {
      link_id: 'wf069_link_1',
      count: 1,
      actions: [
        {
          id: 'act1',
          recipient: 'a.buyer@northwind.example',
          action: 'share',
          event_code: 12,
          allowed: true,
          occurred_at: '2026-05-01T12:00:00.000Z',
        },
      ],
    },
    '/bulk-alert': { alert: false, threshold: 5, window_minutes: 60, recipients: [], note: 'not a block' },
    '/evaluate': DECISION,
    '/records/room': { records: [{ id: 'room_1', data: { name: 'Northwind Traders' } }] },
    ...overrides,
  }
}

/** Route each API call this page makes to one canned answer, longest key first. */
function stubFetch(answers, calls = []) {
  const keys = Object.keys(answers).sort((a, b) => b.length - a.length)
  return vi.fn(async (url) => {
    const path = String(url).split('?')[0]
    calls.push(String(url))
    const key = keys.find((candidate) => path.endsWith(candidate))
    const body = key ? answers[key] : {}
    return {
      ok: true,
      status: 200,
      json: async () => body,
      text: async () => JSON.stringify(body),
    }
  })
}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe('the descriptor', () => {
  it('exports the four fields App.jsx reads', () => {
    expect(page.id).toBe('wf-131-content-protection')
    expect(page.label).toBe('Content protection')
    expect(typeof page.icon).toBe('string')
    expect(typeof page.Component).toBe('function')
  })

  it('has its own order', () => {
    expect(page.order).toBeGreaterThan(0)
  })
})

describe('the state of a policy', () => {
  it('names the three states', () => {
    expect(stateOf({ revoked: true, policy: {} })).toBe('revoked')
    expect(stateOf({ revoked: false, policy: { email_protected: true } })).toBe('gated')
    expect(stateOf({ revoked: false, policy: {} })).toBe('open')
  })

  it('gives every state a word as well as a tone', () => {
    for (const state of Object.keys(STATE_LABELS)) {
      expect(stateTone(state)).toBeTruthy()
      expect(STATE_LABELS[state]).toBeTruthy()
    }
  })
})

describe('the helpers', () => {
  it('splits an allow-list string on commas and newlines', () => {
    expect(splitAllowlist('10.0.0.0/8, 203.0.113.9\n198.51.100.1')).toEqual([
      '10.0.0.0/8',
      '203.0.113.9',
      '198.51.100.1',
    ])
    expect(splitAllowlist('')).toEqual([])
  })

  it('names the protection that refused in a decision label', () => {
    expect(decisionLabel(DECISION)).toContain('IP allow-list')
    expect(decisionLabel({ allowed: true, denied_by: [] })).toContain('Allowed')
  })

  it('labels every logged action', () => {
    for (const action of ['view', 'download', 'share', 'revoke']) {
      expect(ACTION_LABELS[action]).toBeTruthy()
    }
  })

  it('labels every refusal reason', () => {
    for (const reason of Object.values(REASON_LABELS)) {
      expect(reason).toBeTruthy()
    }
  })

  it('renders a timestamp compactly and a missing one as a dash', () => {
    expect(shortTimestamp('2026-05-01T12:00:00.000Z')).toBe('2026-05-01 12:00:00Z')
    expect(shortTimestamp(null)).toBe('--')
  })
})

describe('the primitives', () => {
  it('renders a policy state as a word, not only a colour', () => {
    render(<StatusBadge state="revoked" />)
    expect(screen.getByText('Revoked')).toBeTruthy()
  })

  it('prints the active switches, and says so when there are none', () => {
    const { rerender } = render(<ProtectionBadges fields={{ allow_download: true }} />)
    expect(screen.getByText('allow_download')).toBeTruthy()
    rerender(<ProtectionBadges fields={{}} />)
    expect(screen.getByText(/No protection beyond/)).toBeTruthy()
  })

  it('prints the open allow-list as a word rather than blank', () => {
    const { rerender } = render(<AllowListPreview allowlist={[]} />)
    expect(screen.getByText(/Open/)).toBeTruthy()
    rerender(<AllowListPreview allowlist={['10.0.0.0/8']} />)
    expect(screen.getByText('10.0.0.0/8')).toBeTruthy()
  })

  it('names the protection that refused in a decision card', () => {
    render(<DecisionCard decision={DECISION} />)
    expect(screen.getByText('Refused')).toBeTruthy()
    expect(screen.getByText(/not in the allow-list/)).toBeTruthy()
  })

  it('shows an action with its WF-079 code', () => {
    render(
      <ul>
        <ActionRow
          action={{
            id: 'act1',
            recipient: 'a.buyer@northwind.example',
            action: 'share',
            event_code: 12,
            allowed: true,
            occurred_at: '2026-05-01T12:00:00.000Z',
          }}
        />
      </ul>,
    )
    const row = screen.getByRole('listitem')
    expect(within(row).getByText('share')).toBeTruthy()
    expect(within(row).getByText(/code 12/)).toBeTruthy()
  })

  it('renders an inline note with its meaning in words', () => {
    render(<InlineNote tone="warning">A pattern is present.</InlineNote>)
    expect(screen.getByRole('status').textContent).toContain('A pattern is present.')
  })
})

describe('the page', () => {
  async function openMatrix(user) {
    const buttons = await screen.findAllByRole('button', { name: 'Open the matrix' })
    await user.click(buttons[0])
  }

  it('loads the board and counts its states', async () => {
    vi.stubGlobal('fetch', stubFetch(payload()))
    render(<Component />)
    expect(await screen.findByText('Content protection')).toBeTruthy()
    expect(screen.getByText('Watermarked')).toBeTruthy()
    expect(screen.getByText('Logged actions')).toBeTruthy()
    expect(await screen.findByText('a.buyer@northwind.example')).toBeTruthy()
  })

  it('opens the matrix with its switches and its allow-list', async () => {
    vi.stubGlobal('fetch', stubFetch(payload()))
    const user = userEvent.setup()
    render(<Component />)
    await openMatrix(user)
    expect(
      await screen.findByRole('checkbox', { name: /Require the link's passcode/ }),
    ).toBeTruthy()
    expect(screen.getByRole('checkbox', { name: /Stamp each view/ })).toBeTruthy()
    expect(screen.getByRole('checkbox', { name: /Permit a file fetch/ })).toBeTruthy()
    expect(screen.getByLabelText('IP allow-list')).toBeTruthy()
    expect(screen.getByLabelText('Agreement to accept')).toBeTruthy()
  })

  it('names the three capabilities it does not build', async () => {
    vi.stubGlobal('fetch', stubFetch(payload()))
    render(<Component />)
    expect(await screen.findByText('Not built here')).toBeTruthy()
    expect(screen.getByText('end-to-end encryption')).toBeTruthy()
    expect(screen.getByText(/certifications/)).toBeTruthy()
    expect(screen.getByText('RBAC')).toBeTruthy()
  })

  it('shows the recorded action log for the open policy', async () => {
    vi.stubGlobal('fetch', stubFetch(payload()))
    const user = userEvent.setup()
    render(<Component />)
    await openMatrix(user)
    expect(await screen.findByText('Action log')).toBeTruthy()
    expect(screen.getByText(/code 12/)).toBeTruthy()
  })

  it('evaluates a policy and names the protection that refused', async () => {
    vi.stubGlobal('fetch', stubFetch(payload()))
    const user = userEvent.setup()
    render(<Component />)
    await openMatrix(user)
    await user.click(await screen.findByRole('button', { name: 'Evaluate as verified' }))
    expect(await screen.findByText('Evaluation')).toBeTruthy()
    expect(screen.getByText(/Refused by: IP allow-list/)).toBeTruthy()
  })

  it('reports the bulk-download pattern without blocking', async () => {
    vi.stubGlobal(
      'fetch',
      stubFetch(
        payload({
          '/bulk-alert': {
            alert: true,
            threshold: 5,
            window_minutes: 60,
            recipients: ['a.buyer@northwind.example'],
            note: 'An alert on a pattern, not a block.',
          },
        }),
      ),
    )
    const user = userEvent.setup()
    render(<Component />)
    await openMatrix(user)
    expect(await screen.findByText('Bulk-download alert')).toBeTruthy()
    expect(screen.getByText(/A pattern is present/)).toBeTruthy()
  })
})
