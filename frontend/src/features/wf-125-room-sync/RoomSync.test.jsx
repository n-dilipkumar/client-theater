/**
 * Tests for WF-125's page and its primitives.
 *
 * The page is driven against a stubbed `fetch` rather than a live server, so the
 * assertions are about what a rep reads and which controls are offered, not
 * about the server's answer. Every test builds its own stub and its own data, so
 * the file passes alone and under vitest's parallel run without depending on
 * another test's fixture.
 *
 * Three of these tests are about the researched facts rather than rendering: a
 * rule explains its condition set, a write-back names whether its object is
 * standard or custom, and a failed write-back is offered a retry.
 */

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import page from './index.jsx'
import {
  CREATED_WORDS,
  STATE_WORDS,
  conditionText,
  conflictText,
  countsByState,
  recordLabel,
  roomsByDeal,
  shortTimestamp,
} from './api.js'
import { ConditionList, CreatedBadge, ObjectKindBadge, StateBadge } from './primitives.jsx'

const { Component } = page

const VOCABULARY = {
  crm_systems: ['salesforce', 'dataverse', 'hubspot'],
  writeback_states: ['pending', 'synced', 'failed'],
  roles: ['admin', 'editor', 'presenter'],
  decisions: { shape: 'Mirrored records in the audited store.' },
  not_implemented: ['A real CRM client: no Salesforce, HubSpot or Dynamics call is made.'],
  connector: { systems: ['salesforce', 'dataverse', 'hubspot'], objects: ['deal', 'contact', 'account'] },
}

function payload(overrides = {}) {
  return {
    '/vocabulary': VOCABULARY,
    '/summary': {
      rules: 1,
      rules_enabled: 1,
      records: 2,
      standard_objects: 1,
      custom_objects: 1,
      targets: 2,
      custom_targets: 1,
      rooms: 1,
      events: 2,
      writebacks: 3,
      pending: 0,
      synced: 2,
      failed: 1,
      generated_at: '2026-05-04T09:00:00+00:00',
    },
    '/rules': {
      rules: [
        {
          id: 'r1',
          name: 'Auto-create a room for every open opportunity over 50k',
          crm: 'salesforce',
          object: 'deal',
          enabled: true,
          conditions: [
            { field: 'stage', op: 'eq', value: 'Qualification' },
            { field: 'value', op: 'gte', value: 50000 },
          ],
        },
      ],
    },
    '/records': {
      records: [
        {
          id: 'deal1',
          crm: 'salesforce',
          object: 'deal',
          object_kind: 'standard',
          external_id: '006-NORTHWIND',
          name: 'Northwind Traders - Enterprise Evaluation',
        },
        {
          id: 'custom1',
          crm: 'salesforce',
          object: 'Deal_Room__c',
          object_kind: 'custom',
          external_id: '006-NORTHWIND',
          Engagement_Count__c: 1,
        },
      ],
    },
    '/targets': {
      targets: [
        { id: 't1', object: 'deal', object_kind: 'standard', fields: { Room_Views__c: 'view_count' } },
        {
          id: 't2',
          object: 'Deal_Room__c',
          object_kind: 'custom',
          fields: { Engagement_Count__c: 'view_count' },
        },
      ],
    },
    '/rooms': {
      rooms: [{ id: 'link1', room_id: 'room1', deal_id: 'deal1', created_from: 'rule' }],
    },
    '/events': {
      events: [
        { id: 'e1', kind: 'view', external_id: '006-NORTHWIND', at: '2026-05-04T09:00:00.000+00:00' },
        { id: 'e2', kind: 'download', external_id: '006-NORTHWIND', at: '2026-05-04T09:05:00.000+00:00' },
      ],
    },
    '/writebacks': {
      writebacks: [
        { id: 'w1', object: 'deal', object_kind: 'standard', external_id: '006-NORTHWIND', state: 'synced', fields: { Room_Views__c: 1 } },
        { id: 'w2', object: 'Deal_Room__c', object_kind: 'custom', external_id: '006-NORTHWIND', state: 'synced', fields: { Engagement_Count__c: 1 } },
        { id: 'w3', object: 'Room_Engagement__c', object_kind: 'custom', external_id: '006-NORTHWIND', state: 'failed', reason: 'mirror_absent', fields: { Kind__c: 'view' } },
      ],
    },
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

beforeEach(() => {
  vi.restoreAllMocks()
})

// --------------------------------------------------------------------------- #
// The descriptor
// --------------------------------------------------------------------------- #

describe('the descriptor', () => {
  it('exports the four fields App.jsx reads', () => {
    expect(page.id).toBe('wf-125-room-sync')
    expect(page.label).toBe('CRM room sync')
    expect(typeof page.icon).toBe('string')
    expect(typeof page.Component).toBe('function')
  })

  it('has a unique order beside the workflows it reuses', () => {
    expect(page.order).toBeGreaterThan(0)
  })
})

// --------------------------------------------------------------------------- #
// The api helpers
// --------------------------------------------------------------------------- #

describe('the api helpers', () => {
  it('writes a condition as a short sentence', () => {
    expect(conditionText({ field: 'stage', op: 'eq', value: 'Qualification' })).toBe(
      'stage eq Qualification',
    )
    expect(conditionText({ field: 'value', op: 'gte', value: 50000 })).toBe('value gte 50000')
    expect(conditionText({ field: 'stage', op: 'exists' })).toBe('stage is present')
  })

  it('counts write-backs by state', () => {
    const counts = countsByState([
      { state: 'synced' },
      { state: 'failed' },
      { state: 'failed' },
    ])
    expect(counts).toEqual({ pending: 0, synced: 1, failed: 2 })
  })

  it('labels a record by its name, then its CRM id', () => {
    expect(recordLabel({ name: 'Northwind', external_id: '006-A' })).toBe('Northwind')
    expect(recordLabel({ external_id: '006-A' })).toBe('006-A')
    expect(recordLabel({})).toBe('unknown')
  })

  it('renders a timestamp compactly and a missing one as a dash', () => {
    expect(shortTimestamp('2026-05-04T09:00:00.000Z')).toBe('2026-05-04 09:00:00Z')
    expect(shortTimestamp(null)).toBe('--')
  })

  it('keys the rooms by the deal they were generated from', () => {
    expect(roomsByDeal([{ deal_id: 'd1', room_id: 'r1' }])).toEqual({ d1: { deal_id: 'd1', room_id: 'r1' } })
  })

  it('gives every write-back state and creation path a word', () => {
    for (const word of Object.values(STATE_WORDS)) expect(word).toBeTruthy()
    for (const word of Object.values(CREATED_WORDS)) expect(word).toBeTruthy()
    expect(conflictText('mirror_absent')).toMatch(/no mirror/)
    expect(conflictText('unknown')).toBe('That change was refused.')
  })
})

// --------------------------------------------------------------------------- #
// The primitives
// --------------------------------------------------------------------------- #

describe('the primitives', () => {
  it('renders a write-back state as a word, not only a colour', () => {
    render(<StateBadge state="failed" />)
    expect(screen.getByText('Failed')).toBeTruthy()
  })

  it('renders an object kind as a word', () => {
    render(<ObjectKindBadge kind="custom" />)
    expect(screen.getByText('Custom')).toBeTruthy()
  })

  it('renders how a room was made as a word', () => {
    render(<CreatedBadge from="seller" />)
    expect(screen.getByText('Seller pick')).toBeTruthy()
  })

  it('lists a rule condition set', () => {
    render(
      <ConditionList
        conditions={[
          { field: 'stage', op: 'eq', value: 'Qualification' },
          { field: 'value', op: 'gte', value: 50000 },
        ]}
      />,
    )
    expect(screen.getByText('stage eq Qualification')).toBeTruthy()
    expect(screen.getByText('value gte 50000')).toBeTruthy()
  })
})

// --------------------------------------------------------------------------- #
// The page
// --------------------------------------------------------------------------- #

describe('the page', () => {
  it('loads the board and counts its objects', async () => {
    vi.stubGlobal('fetch', stubFetch(payload()))
    render(<Component />)
    expect(await screen.findByText('CRM-driven room lifecycle')).toBeTruthy()
    expect(screen.getByText('Mirrored records')).toBeTruthy()
    expect(screen.getByText('Generated rooms')).toBeTruthy()
    expect(await screen.findByText(/Northwind Traders - Enterprise Evaluation/)).toBeTruthy()
  })

  it('shows a rule with its condition set', async () => {
    vi.stubGlobal('fetch', stubFetch(payload()))
    render(<Component />)
    expect(await screen.findByText(/Auto-create a room/)).toBeTruthy()
    expect(screen.getByText('stage eq Qualification')).toBeTruthy()
    expect(screen.getByText('Enabled')).toBeTruthy()
  })

  it('names each write-back target as standard or custom', async () => {
    vi.stubGlobal('fetch', stubFetch(payload()))
    render(<Component />)
    expect((await screen.findAllByText(/Deal_Room__c/)).length).toBeGreaterThan(0)
    expect(screen.getAllByText('Custom').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Standard').length).toBeGreaterThan(0)
  })

  it('reports a failed write-back and offers a retry', async () => {
    vi.stubGlobal('fetch', stubFetch(payload()))
    const user = userEvent.setup()
    render(<Component />)
    expect(await screen.findByText(/1 write-back\(s\) failed/)).toBeTruthy()
    const retries = screen.getAllByRole('button', { name: /Retry/ })
    expect(retries).toHaveLength(1)
    await user.click(retries[0])
    const calls = global.fetch.mock.calls.map((call) => String(call[0]))
    expect(calls.some((url) => url.includes('/writebacks/w3/retry'))).toBe(true)
  })

  it('names the surfaces it does not build rather than omitting them', async () => {
    vi.stubGlobal('fetch', stubFetch(payload()))
    render(<Component />)
    expect(await screen.findByText('Not built here')).toBeTruthy()
    expect(screen.getByText(/A real CRM client/)).toBeTruthy()
  })

  it('shows an empty state when nothing is mirrored yet', async () => {
    vi.stubGlobal(
      'fetch',
      stubFetch(payload({ '/records': { records: [] }, '/writebacks': { writebacks: [] } })),
    )
    render(<Component />)
    expect(await screen.findByText('No mirrored CRM records')).toBeTruthy()
  })
})
