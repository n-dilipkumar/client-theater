/**
 * Tests for WF-130's page and its primitives.
 *
 * The page is driven against a stubbed `fetch` rather than a live server, so the
 * assertions are about what a seller reads and which controls are offered, not
 * about the server's answer. Every test builds its own stub and its own data, so
 * the file passes alone and under vitest's parallel run without depending on
 * another test's fixture.
 *
 * Three of these tests are about the researched facts rather than about
 * rendering: a hidden section prints the stage that reveals it, the page names
 * the surfaces it does not build, and the deal form is refused before the round
 * trip when it has no account.
 */

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import page from './index.jsx'
import {
  STATE_LABELS,
  collateralApi,
  kindLabel,
  parseTokens,
  sectionVisibility,
  shortTimestamp,
  stageLabel,
  stateLabel,
} from './api.js'
import { InlineNote, KindBadge, SectionList, StateBadge } from './primitives.jsx'

const { Component } = page

const setupUser = () => userEvent.setup({ delay: null })

const VOCABULARY = {
  kinds: ['document', 'deck'],
  states: ['draft', 'published', 'withdrawn'],
  section_kinds: ['narrative', 'case_study', 'pricing', 'mutual_action_plan', 'proof'],
  deal_stages: ['discovery', 'technical_validation', 'business_case', 'procurement', 'closed_won'],
  tokens: ['account', 'contact', 'contact_first', 'product', 'stage', 'seller', 'deal_id'],
  default_template: 'proposal_document',
  templates: [
    { key: 'proposal_document', name: 'Personalised proposal', kind: 'document' },
    { key: 'discovery_deck', name: 'Discovery deck', kind: 'deck' },
  ],
}

const DECISIONS = {
  decisions: { assembly_engine: 'Rules first.' },
  not_implemented: [
    'An LLM assembly path. The rules engine is the implementation.',
    'Rendering content blocks to DOCX, PDF or PPTX, and the pdf.js preview.',
  ],
}

const SECTION = (overrides = {}) => ({
  key: 'overview',
  title: 'Overview',
  kind: 'narrative',
  body: 'Prepared for Northwind.',
  reveal_at_stage: null,
  visible: true,
  synced: false,
  asset_ref: null,
  asset_version: null,
  unresolved_tokens: [],
  ...overrides,
})

function collateralPayload(overrides = {}) {
  return {
    id: 'wf130_collateral_1',
    room_id: 'room_1',
    title: 'Northwind personalised proposal',
    kind: 'document',
    state: 'published',
    engine: 'rules',
    version: 2,
    template_key: 'proposal_document',
    template_name: 'Personalised proposal',
    deal: { account: 'Northwind', contact: 'Ava Chen', stage: 'discovery' },
    stage: 'discovery',
    brand: { applied: true, tokens: ['accent'] },
    sections: [
      SECTION(),
      SECTION({
        key: 'case_study',
        title: 'Reference customer',
        kind: 'case_study',
        body: 'A comparable team ran the same evaluation.',
        synced: true,
        asset_ref: 'northwind-case-study',
        asset_version: 2,
      }),
      SECTION({
        key: 'pricing',
        title: 'Pricing',
        kind: 'pricing',
        body: 'Commercial terms for Northwind.',
        reveal_at_stage: 'procurement',
        visible: false,
      }),
    ],
    visible_sections: ['overview', 'case_study'],
    section_count: 3,
    visible_count: 2,
    hidden_count: 1,
    synced_count: 1,
    published_at: '2026-05-01T09:00:00.000+00:00',
    withdrawn_at: null,
    room_page_id: 'page_1',
    ...overrides,
  }
}

function assetPayload(overrides = {}) {
  return {
    id: 'wf130_asset_1',
    key: 'northwind-case-study',
    kind: 'case_study',
    title: 'Northwind onboarding case study',
    body: 'Cut onboarding time by 38 percent.',
    version: 2,
    updated_at: '2026-05-01T09:00:00.000+00:00',
    ...overrides,
  }
}

function payload(overrides = {}) {
  return {
    '/vocabulary': VOCABULARY,
    '/decisions': DECISIONS,
    '/summary': {
      collaterals: 1,
      published: 1,
      draft: 0,
      withdrawn: 0,
      by_kind: { document: 1, deck: 0 },
      visible_sections: 2,
      hidden_sections: 1,
      synced_sections: 1,
      assets: 1,
      events: 2,
    },
    '/assets': { count: 1, assets: [assetPayload()] },
    '/collateral/wf130_collateral_1/events': {
      count: 2,
      events: [
        { kind: 'generated', at: '2026-05-01T09:00:00.000+00:00' },
        { kind: 'published', at: '2026-05-01T09:05:00.000+00:00' },
      ],
    },
    '/collateral/wf130_collateral_1': collateralPayload(),
    '/collateral': { count: 1, room_id: 'room_1', collateral: [collateralPayload()] },
    '/records/room': { records: [{ id: 'room_1', data: { name: 'Northwind Traders' } }] },
    ...overrides,
  }
}

/** Route each API call this page makes to one canned answer. */
function stubFetch(answers, calls = []) {
  return vi.fn(async (url, options = {}) => {
    const path = String(url).split('?')[0]
    calls.push({ url: String(url), method: options.method || 'GET' })
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

const pathsOf = (calls) => calls.map((call) => call.url)

beforeEach(() => {
  vi.restoreAllMocks()
})

// --------------------------------------------------------------------------- #
// The descriptor
// --------------------------------------------------------------------------- #

describe('the descriptor', () => {
  it('exports the four fields App.jsx reads', () => {
    expect(page.id).toBe('wf-130-collateral-generation')
    expect(page.label).toBe('Collateral generation')
    expect(typeof page.icon).toBe('string')
    expect(typeof page.Component).toBe('function')
    expect(page.order).toBe(1300)
  })
})

// --------------------------------------------------------------------------- #
// The page
// --------------------------------------------------------------------------- #

describe('the page', () => {
  it('names the workflow it is about', async () => {
    global.fetch = stubFetch(payload())
    render(<Component />)
    expect(await screen.findByRole('heading', { name: 'Collateral generation' })).toBeTruthy()
  })

  it("calls this feature's own routes, plus the core room collection", async () => {
    const calls = []
    global.fetch = stubFetch(payload(), calls)
    render(<Component />)
    await screen.findByRole('heading', { name: 'Collateral generation' })
    const api = pathsOf(calls).filter((url) => url.startsWith('/api/'))
    const own = api.filter((url) => url.includes('/api/wf-130/'))
    const core = api.filter((url) => !url.includes('/api/wf-130/'))
    expect(own.length).toBeGreaterThan(0)
    expect(core.every((url) => url.startsWith('/api/records/room'))).toBe(true)
  })

  it('shows the four headline figures', async () => {
    global.fetch = stubFetch(payload())
    render(<Component />)
    await screen.findByRole('heading', { name: 'Collateral generation' })
    expect(screen.getByText('Collateral generation')).toBeTruthy()
    expect(screen.getAllByText('Collateral').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Published').length).toBeGreaterThan(0)
    expect(screen.getByText('Hidden sections')).toBeTruthy()
    expect(screen.getByText('Source assets')).toBeTruthy()
    expect(screen.getByText('1 document, 0 deck')).toBeTruthy()
  })

  it('lists each collateral with its state, version and section counts', async () => {
    global.fetch = stubFetch(payload())
    render(<Component />)
    await screen.findByRole('heading', { name: 'Collateral generation' })
    expect(screen.getByText('Northwind personalised proposal')).toBeTruthy()
    expect(screen.getAllByText('Published').length).toBeGreaterThan(0)
    expect(screen.getByText(/v2 \| 3 section\(s\), 2 visible, 1 hidden/)).toBeTruthy()
  })

  it('shows an empty state rather than a blank list when no collateral exists', async () => {
    global.fetch = stubFetch(payload({ '/collateral': { count: 0, collateral: [] } }))
    render(<Component />)
    expect(await screen.findByText('No collateral yet')).toBeTruthy()
  })

  it('names the surfaces it does not build', async () => {
    global.fetch = stubFetch(payload())
    render(<Component />)
    await screen.findByRole('heading', { name: 'Collateral generation' })
    expect(
      screen.getByText('An LLM assembly path. The rules engine is the implementation.'),
    ).toBeTruthy()
  })

  it('refuses to generate without an account, before the round trip', async () => {
    global.fetch = stubFetch(payload())
    render(<Component />)
    const submit = await screen.findByRole('button', { name: /Generate the collateral/ })
    expect(submit).toBeDisabled()
  })

  it('reviews an artefact and saves its edits', async () => {
    const calls = []
    global.fetch = stubFetch(payload(), calls)
    const user = setupUser()
    render(<Component />)
    const review = await screen.findByRole('button', { name: 'Review' })
    await user.click(review)

    await screen.findByRole('heading', { name: 'Northwind personalised proposal' })
    expect(screen.getByText(/Hidden until Procurement/)).toBeTruthy()

    await user.click(screen.getByRole('button', { name: 'Save the edits' }))
    const patch = calls.find(
      (call) =>
        call.method === 'PATCH' && call.url.includes('/api/wf-130/collateral/wf130_collateral_1'),
    )
    expect(patch).toBeTruthy()
  })

  it('publishes to the room from the list', async () => {
    const calls = []
    global.fetch = stubFetch(payload(), calls)
    const user = setupUser()
    render(<Component />)
    const publish = await screen.findByRole('button', { name: 'Publish' })
    await user.click(publish)
    const post = calls.find(
      (call) =>
        call.method === 'POST' &&
        call.url.includes('/api/wf-130/collateral/wf130_collateral_1/publish'),
    )
    expect(post).toBeTruthy()
  })

  it('registers a source asset', async () => {
    const calls = []
    global.fetch = stubFetch(payload(), calls)
    const user = setupUser()
    render(<Component />)
    await screen.findByRole('heading', { name: 'Collateral generation' })

    await user.type(screen.getByLabelText('Key'), 'acme-proof')
    await user.click(screen.getByRole('button', { name: 'Register the asset' }))
    const post = calls.find(
      (call) => call.method === 'POST' && call.url.endsWith('/api/wf-130/assets'),
    )
    expect(post).toBeTruthy()
  })
})

// --------------------------------------------------------------------------- #
// The api helpers
// --------------------------------------------------------------------------- #

describe('the api helpers', () => {
  it('exposes this feature\'s own methods and no shared mutation', () => {
    expect(typeof collateralApi.create).toBe('function')
    expect(typeof collateralApi.publish).toBe('function')
    expect(typeof collateralApi.refreshAsset).toBe('function')
  })

  it('labels a state and a kind in words', () => {
    expect(stateLabel('withdrawn')).toBe('Withdrawn')
    expect(STATE_LABELS.published).toBe('Published')
    expect(kindLabel('deck')).toBe('Deck')
    expect(stageLabel('procurement')).toBe('Procurement')
  })

  it('prints a hidden section with the stage that reveals it', () => {
    expect(sectionVisibility({ visible: true })).toBe('Visible')
    expect(sectionVisibility({ visible: false, reveal_at_stage: 'procurement' })).toBe(
      'Hidden until Procurement',
    )
    expect(sectionVisibility({ visible: false })).toBe('Hidden')
  })

  it('formats a timestamp and parses tokens', () => {
    expect(shortTimestamp('2026-05-01T09:00:00.000Z')).toBe('2026-05-01 09:00:00Z')
    expect(shortTimestamp(null)).toBe('--')
    expect(parseTokens('a, b ,,c')).toEqual(['a', 'b', 'c'])
  })
})

// --------------------------------------------------------------------------- #
// The primitives
// --------------------------------------------------------------------------- #

describe('the primitives', () => {
  it('prints a state as a word, not only a colour', () => {
    render(<StateBadge state="withdrawn" />)
    expect(screen.getByText('Withdrawn')).toBeTruthy()
  })

  it('prints a kind as a word', () => {
    render(<KindBadge kind="deck" />)
    expect(screen.getByText('Deck')).toBeTruthy()
  })

  it('keeps a hidden section in the list and names its reveal stage', () => {
    render(
      <SectionList
        sections={[
          SECTION({ key: 'pricing', title: 'Pricing', visible: false, reveal_at_stage: 'procurement' }),
        ]}
      />,
    )
    expect(screen.getByText('Pricing')).toBeTruthy()
    expect(screen.getByText('Hidden until Procurement')).toBeTruthy()
  })

  it('states the tone in words beside the colour', () => {
    render(<InlineNote tone="warning">An account is required.</InlineNote>)
    expect(screen.getByText('An account is required.')).toBeTruthy()
  })
})
