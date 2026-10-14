/**
 * Tests for WF-137's page, its primitives and its helpers.
 *
 * The page is driven against a stubbed `fetch` rather than a live server, so the
 * assertions are about what a seller reads and which controls are offered, not
 * about the server's answer. Every test builds its own stub and its own data, so
 * the file passes alone and under vitest's parallel run without depending on
 * another test's fixture.
 *
 * Three of these tests are about the researched decisions rather than about
 * rendering: the two snapshot triggers are distinct, an off-brand room is a list
 * of named findings, and the page names the surfaces it does not build rather
 * than omitting them.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import page from './index.jsx'
import {
  FINDING_LABELS,
  LIFECYCLE_LABELS,
  RETENTION_LABELS,
  STATE_TONES,
  TRIGGER_LABELS,
  governanceApi,
  shortTimestamp,
  stateTone,
  wholeInRange,
} from './api.js'
import {
  FindingList,
  InlineNote,
  PhaseList,
  RetentionWindow,
  StateBadge,
  VersionRow,
} from './primitives.jsx'

const { Component } = page

const setupUser = () => userEvent.setup({ delay: null })

const VOCABULARY = {
  ticket: 'WF-137',
  snapshot_triggers: ['publish', 'session', 'restore', 'branch'],
  lifecycle_states: ['sales', 'onboarding', 'onboarded', 'archived', 'deleted'],
  retention_states: ['live', 'archived', 'deleted'],
  brand_tokens: ['theme', 'accent', 'logo', 'font'],
  defaults: { archive_after_days: 180, delete_after_days: 365, first_version: 1 },
  not_implemented: [
    'A live push of refreshed content into every live room. The refresh is a read-through.',
    'An LMS embed. Mindtello, Highspot and Allego are named as integrations.',
  ],
}

const BRAND_SET = {
  name: 'Client Theater house brand',
  version: 1,
  tokens: { theme: 'light', accent: '#10506f', logo: 'client-theater.svg', font: 'Schibsted Grotesk' },
  allowed_values: { theme: ['light'], accent: ['#10506f'] },
  required_tokens: ['theme', 'accent', 'logo', 'font'],
}

function brandPayload(overrides = {}) {
  return {
    brand_set: BRAND_SET,
    count: 1,
    non_compliant: 1,
    stale: 0,
    rooms: [
      {
        room_id: 'room_1',
        room_name: 'Northwind',
        lifecycle_state: 'onboarding',
        stale: false,
        branding: { theme: 'dark', accent: '#22c55e' },
        findings: [
          { code: 'unapproved_theme', token: 'theme', value: 'dark', message: 'The theme value is not approved.' },
        ],
        compliant: false,
      },
    ],
    ...overrides,
  }
}

function roomBrandPayload() {
  return {
    room_id: 'room_1',
    room_name: 'Northwind',
    lifecycle_state: 'onboarding',
    brand_set_version: 1,
    stale: false,
    required_tokens: ['theme', 'accent', 'logo', 'font'],
    branding_source: 'room',
    branding: { theme: 'dark', accent: '#22c55e' },
    findings: [
      { code: 'unapproved_theme', token: 'theme', value: 'dark', message: 'The theme value is not approved.' },
    ],
    compliant: false,
  }
}

function historyPayload() {
  return {
    room_id: 'room_1',
    count: 2,
    head_version: 2,
    lifecycle_state: 'onboarding',
    versions: [
      {
        id: 's1',
        version: 1,
        trigger: 'publish',
        label: 'Overview deck shared',
        author: 'dana',
        at: '2026-05-01T09:00:00.000+00:00',
        sections: [],
        content: {},
        restored_from: null,
        branched_from: null,
      },
      {
        id: 's2',
        version: 2,
        trigger: 'session',
        label: 'Editing session',
        author: 'dana',
        at: '2026-05-02T09:00:00.000+00:00',
        sections: [],
        content: {},
        restored_from: null,
        branched_from: null,
      },
    ],
  }
}

function onboardingPayload() {
  return {
    room_id: 'room_1',
    lifecycle_state: 'onboarding',
    plan_key: 'house-onboarding',
    plan_name: 'House onboarding plan',
    progress: { phases: 2, phases_complete: 0, phases_active: 1, phases_pending: 1, milestones: 2, milestones_complete: 0 },
    active_phase: { phase_key: 'kickoff', name: 'Kickoff' },
    next_milestone: 'introductions',
    phases: [
      {
        id: 'p1',
        phase_key: 'kickoff',
        name: 'Kickoff',
        order: 0,
        state: 'active',
        open_milestones: ['introductions'],
        milestones: [
          { key: 'introductions', name: 'Introductions and access', state: 'pending' },
          { key: 'success_criteria', name: 'Success criteria agreed', state: 'complete' },
        ],
      },
      {
        id: 'p2',
        phase_key: 'implementation',
        name: 'Implementation',
        order: 1,
        state: 'pending',
        open_milestones: ['plan_signed'],
        milestones: [{ key: 'plan_signed', name: 'Implementation plan signed', state: 'pending' }],
      },
    ],
  }
}

function retentionPayload() {
  return {
    room_id: 'room_1',
    lifecycle_state: 'onboarding',
    has_policy: true,
    state: 'live',
    legal_basis: 'signed contract',
    archive_after_days: 180,
    delete_after_days: 365,
    lifecycle_ended_at: '2026-01-01T00:00:00.000+00:00',
    archive_at: '2026-06-30T00:00:00.000+00:00',
    delete_at: '2027-06-30T00:00:00.000+00:00',
    archive_due: false,
    delete_due: false,
    due: false,
  }
}

function payload(overrides = {}) {
  return {
    '/records/room': { records: [{ id: 'room_1', data: { name: 'Northwind' } }] },
    '/rooms/room_1/brand': roomBrandPayload(),
    '/rooms/room_1/history': historyPayload(),
    '/rooms/room_1/onboarding': onboardingPayload(),
    '/rooms/room_1/retention': retentionPayload(),
    '/vocabulary': VOCABULARY,
    '/summary': {
      rooms_tracked: 1,
      snapshots: 2,
      plans: 0,
      phases: 2,
      phases_complete: 0,
      retention_policies: 1,
      retention_due: 0,
      brand_set_version: 1,
      brand_stale_rooms: 0,
      lifecycle: { onboarding: 1 },
    },
    '/brand': brandPayload(),
    '/retention': { count: 1, due: 0, policies: [{ room_id: 'room_1' }] },
    ...overrides,
  }
}

/** Route each API call this page makes to one canned answer. */
function stubFetch(answers, calls = []) {
  return vi.fn(async (url) => {
    const path = String(url).split('?')[0]
    calls.push(String(url))
    const key = Object.keys(answers).find((candidate) => path.endsWith(candidate))
    const body = key ? answers[key] : {}
    return {
      ok: true,
      status: 200,
      json: async () => body,
      text: async () => JSON.stringify(body),
    }
  })
}

async function openRoom(user) {
  await screen.findByRole('heading', { name: 'Governance and version control' })
  await user.selectOptions(screen.getByLabelText('Room'), 'room_1')
  await screen.findByRole('heading', { name: 'Version history' })
}

beforeEach(() => {
  vi.restoreAllMocks()
})

// --------------------------------------------------------------------------- #
// The descriptor
// --------------------------------------------------------------------------- #

describe('the descriptor', () => {
  it('exports the four fields App.jsx reads', () => {
    expect(page.id).toBe('wf-137-governance')
    expect(page.label).toBe('Governance')
    expect(typeof page.icon).toBe('string')
    expect(typeof page.Component).toBe('function')
    expect(page.order).toBe(1370)
  })
})

// --------------------------------------------------------------------------- #
// The page
// --------------------------------------------------------------------------- #

describe('the page', () => {
  it('names the workflow it is about', async () => {
    global.fetch = stubFetch(payload())
    render(<Component />)
    expect(await screen.findByRole('heading', { name: 'Governance and version control' })).toBeTruthy()
  })

  it("calls this feature's own routes, plus the core room collection", async () => {
    const calls = []
    global.fetch = stubFetch(payload(), calls)
    render(<Component />)
    await screen.findByRole('heading', { name: 'Governance and version control' })
    const apiCalls = calls.filter((call) => call.startsWith('/api/'))
    const own = apiCalls.filter((call) => call.includes('/api/wf-137/'))
    const core = apiCalls.filter((call) => !call.includes('/api/wf-137/'))
    expect(own.length).toBeGreaterThan(0)
    expect(core.every((call) => call.startsWith('/api/records/room'))).toBe(true)
  })

  it('shows the four headline figures', async () => {
    global.fetch = stubFetch(payload())
    render(<Component />)
    await screen.findByRole('heading', { name: 'Governance and version control' })
    expect(screen.getByText('Governed rooms')).toBeTruthy()
    expect(screen.getByText('Snapshots')).toBeTruthy()
    expect(screen.getByText('Brand set')).toBeTruthy()
    expect(screen.getByText('Retention due')).toBeTruthy()
  })

  it('lists each governed room with its brand compliance', async () => {
    global.fetch = stubFetch(payload())
    render(<Component />)
    await screen.findByRole('heading', { name: 'Governance and version control' })
    expect(screen.getByText('1 finding(s)')).toBeTruthy()
    expect(screen.getAllByText('Onboarding portal').length).toBeGreaterThan(0)
  })

  it('shows an empty state rather than a blank panel when no room is selected', async () => {
    global.fetch = stubFetch(payload())
    render(<Component />)
    expect(await screen.findByText('No room selected')).toBeTruthy()
  })

  it('renders the version history with both triggers after a room is picked', async () => {
    global.fetch = stubFetch(payload())
    const user = setupUser()
    render(<Component />)
    await openRoom(user)
    expect(screen.getByText('Publish')).toBeTruthy()
    expect(screen.getByText('Editing session')).toBeTruthy()
    expect(screen.getAllByText('Head').length).toBeGreaterThan(0)
  })

  it('renders the phased onboarding plan with its milestones', async () => {
    global.fetch = stubFetch(payload())
    const user = setupUser()
    render(<Component />)
    await openRoom(user)
    expect(screen.getByText('Kickoff')).toBeTruthy()
    expect(screen.getByText('Implementation')).toBeTruthy()
    expect(screen.getByText('Introductions and access')).toBeTruthy()
  })

  it('applies the brand to the selected room', async () => {
    const calls = []
    global.fetch = stubFetch(payload(), calls)
    const user = setupUser()
    render(<Component />)
    await openRoom(user)
    await user.click(screen.getByRole('button', { name: 'Apply brand' }))
    await waitFor(
      () => {
        expect(calls.some((call) => call.includes('/api/wf-137/rooms/room_1/brand/apply'))).toBe(true)
      },
      { timeout: 5000 },
    )
  })

  it('publishes a version through the snapshots route', async () => {
    const calls = []
    global.fetch = stubFetch(payload(), calls)
    const user = setupUser()
    render(<Component />)
    await openRoom(user)
    await user.click(screen.getByRole('button', { name: 'Publish a version' }))
    await waitFor(
      () => {
        expect(calls.some((call) => call.includes('/api/wf-137/rooms/room_1/snapshots'))).toBe(true)
      },
      { timeout: 5000 },
    )
  })

  it('checkpoints an editing session with the session trigger', async () => {
    const calls = []
    global.fetch = stubFetch(payload(), calls)
    const user = setupUser()
    render(<Component />)
    await openRoom(user)
    await user.click(screen.getByRole('button', { name: 'Checkpoint a session' }))
    await waitFor(
      () => {
        expect(
          calls.some(
            (call) => call.includes('/snapshots') && call.includes('trigger=session'),
          ),
        ).toBe(true)
      },
      { timeout: 5000 },
    )
  })

  it('restores a version through its own route', async () => {
    const calls = []
    global.fetch = stubFetch(payload(), calls)
    const user = setupUser()
    render(<Component />)
    await openRoom(user)
    await user.click(screen.getAllByRole('button', { name: 'Restore' })[0])
    await waitFor(
      () => {
        expect(calls.some((call) => call.includes('/snapshots/1/restore'))).toBe(true)
      },
      { timeout: 5000 },
    )
  })

  it('offers the repurpose control only while the room is still selling', async () => {
    const answers = payload()
    answers['/rooms/room_1/onboarding'] = {
      ...onboardingPayload(),
      lifecycle_state: 'sales',
      phases: [],
    }
    global.fetch = stubFetch(answers)
    const user = setupUser()
    render(<Component />)
    await openRoom(user)
    const repurpose = screen.getByRole('button', { name: 'Repurpose at closed-won' })
    expect(repurpose).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Advance the phase' })).toBeDisabled()
  })

  it('closes a milestone through the milestones route', async () => {
    const calls = []
    global.fetch = stubFetch(payload(), calls)
    const user = setupUser()
    render(<Component />)
    await openRoom(user)
    await user.click(screen.getAllByRole('button', { name: 'Mark complete' })[0])
    await waitFor(
      () => {
        expect(calls.some((call) => call.includes('/onboarding/milestones/complete'))).toBe(true)
      },
      { timeout: 5000 },
    )
  })

  it('names the surfaces this workflow does not build', async () => {
    global.fetch = stubFetch(payload())
    render(<Component />)
    expect(await screen.findByText('Not built here')).toBeTruthy()
    expect(screen.getByText(/A live push of refreshed content/)).toBeTruthy()
    expect(screen.getByText(/An LMS embed/)).toBeTruthy()
  })
})

// --------------------------------------------------------------------------- #
// The primitives
// --------------------------------------------------------------------------- #

describe('the primitives', () => {
  it('prints the lifecycle name beside its tone', () => {
    render(<StateBadge state="onboarding" />)
    expect(screen.getByText('Onboarding portal')).toBeTruthy()
  })

  it('prints a retention state from the retention vocabulary', () => {
    render(<StateBadge state="archived" kind="retention" />)
    expect(screen.getByText('Archived')).toBeTruthy()
  })

  it('lists every off-brand finding with its token', () => {
    render(<FindingList findings={roomBrandPayload().findings} />)
    expect(screen.getByText('Unapproved theme')).toBeTruthy()
    expect(screen.getByText('theme')).toBeTruthy()
  })

  it('says so rather than drawing an empty finding list', () => {
    render(<FindingList findings={[]} />)
    expect(screen.getByText(/This room is on brand/)).toBeTruthy()
  })

  it('renders a version with its trigger and author', () => {
    render(
      <VersionRow
        version={historyPayload().versions[1]}
        head={2}
        busy={false}
        onPreview={() => {}}
        onRestore={() => {}}
        onBranch={() => {}}
      />,
    )
    expect(screen.getByText('v2')).toBeTruthy()
    expect(screen.getByText('Editing session')).toBeTruthy()
    expect(screen.getByText(/dana/)).toBeTruthy()
  })

  it('renders each phase with its milestone count', () => {
    render(<PhaseList plan={onboardingPayload()} onComplete={() => {}} busy={false} />)
    expect(screen.getByText('Kickoff')).toBeTruthy()
    expect(screen.getByText('1/2 milestone(s)')).toBeTruthy()
  })

  it('says so rather than drawing an empty plan', () => {
    render(<PhaseList plan={{ phases: [] }} onComplete={() => {}} busy={false} />)
    expect(screen.getByText(/no onboarding plan yet/)).toBeTruthy()
  })

  it('renders both retention dates and the due state', () => {
    render(<RetentionWindow retention={retentionPayload()} />)
    expect(screen.getByText('2026-06-30 00:00:00Z')).toBeTruthy()
    expect(screen.getByText('After 180 day(s)')).toBeTruthy()
    expect(screen.getByText(/Basis: signed contract/)).toBeTruthy()
  })

  it('marks the notes it renders with a live region', () => {
    render(<InlineNote tone="warning">No retention policy.</InlineNote>)
    expect(screen.getByRole('status').textContent).toContain('No retention policy.')
  })
})

// --------------------------------------------------------------------------- #
// The helpers
// --------------------------------------------------------------------------- #

describe('the helpers', () => {
  it('maps each lifecycle, retention and trigger onto a label', () => {
    for (const name of VOCABULARY.lifecycle_states) expect(LIFECYCLE_LABELS[name]).toBeTruthy()
    for (const name of VOCABULARY.retention_states) expect(RETENTION_LABELS[name]).toBeTruthy()
    for (const name of VOCABULARY.snapshot_triggers) expect(TRIGGER_LABELS[name]).toBeTruthy()
  })

  it('maps each state onto a tone the shared Badge actually has', () => {
    const tones = ['neutral', 'insert', 'update', 'delete', 'restore']
    for (const tone of Object.values(STATE_TONES)) expect(tones).toContain(tone)
  })

  it('defaults an unseen state to neutral rather than throwing', () => {
    expect(stateTone('something-new')).toBe('neutral')
    expect(stateTone(undefined)).toBe('neutral')
  })

  it('labels every finding code the server publishes', () => {
    expect(FINDING_LABELS.unapproved_theme).toBeTruthy()
    expect(FINDING_LABELS.missing_token).toBeTruthy()
  })

  it('renders a timestamp as a short mono string and a dash when there is none', () => {
    expect(shortTimestamp('2026-05-01T09:00:00.000+00:00')).toBe('2026-05-01 09:00:00Z')
    expect(shortTimestamp(null)).toBe('--')
  })

  it('holds a number to the bounds the server enforces', () => {
    expect(wholeInRange('30', 1, 3650)).toBe(30)
    expect(wholeInRange('0', 1, 3650)).toBeNull()
    expect(wholeInRange('2.5', 1, 3650)).toBeNull()
    expect(wholeInRange('', 1, 3650)).toBeNull()
  })

  it('exposes every route this feature owns through its own wrapper', () => {
    expect(typeof governanceApi.applyBrand).toBe('function')
    expect(typeof governanceApi.snapshot).toBe('function')
    expect(typeof governanceApi.repurpose).toBe('function')
    expect(typeof governanceApi.setRetention).toBe('function')
    expect(typeof governanceApi.branch).toBe('function')
  })
})
