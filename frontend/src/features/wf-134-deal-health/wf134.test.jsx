/**
 * Tests for WF-134's page, its primitives and its words.
 *
 * The page is driven against a stubbed `fetch` rather than a live server, so the
 * assertions are about what a rep reads and which controls are offered, not about
 * the server's answer. Every test builds its own stub and its own data, so the
 * file passes alone and under vitest's parallel run without depending on another
 * test's fixture.
 *
 * Three of these tests are about the researched facts rather than rendering: the
 * page names the surfaces it does not build, every state carries a word as well
 * as a colour, and the readiness reading names its denominator.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import page from './index.jsx'
import {
  BAND_TONES,
  BAND_WORDS,
  REASON_WORDS,
  bandTone,
  bandWord,
  conflictText,
  daysSince,
  deltaSentence,
  eventWord,
  factorPercent,
  factorWord,
  outcomeWord,
  reasonWord,
  weightedContribution,
} from './api.js'
import {
  AlertRow,
  BandBadge,
  ContentRow,
  CoverageColumns,
  CueRow,
  Notice,
  ReadinessPanel,
  ReasonList,
} from './primitives.jsx'

const { Component } = page

const SCORE = {
  value: 32,
  band: 'at_risk',
  factors: {
    engagement: {
      factor: 0.63,
      events: 1,
      events_in_window: 1,
      weighted: 0.5,
      recency: 1,
      volume: 0.08,
      days_since_last: 0.1,
      window_days: 21,
    },
    content: { factor: 0, required: 1, reviewed: 0, unreviewed: ['Pilot Scope'] },
    stakeholder: {
      factor: 0,
      total: 2,
      engaged: 0,
      coverage: 0,
      target: 0.6,
      not_engaged: ['Ada Chen', 'Ben Silva'],
    },
  },
  weights: { engagement: 0.5, content: 0.3, stakeholder: 0.2 },
  reasons: ['unreviewed_key_material', 'missing_stakeholders', 'below_health_target'],
  reason_sentences: [
    'Required key material is still unreviewed.',
    'Part of the buying group never engaged.',
    'Deal health is in the at-risk band.',
  ],
  previous: null,
  delta: null,
  framework: 'bant',
  measured_at: '2026-05-04T09:00:00+00:00',
}

const VOCABULARY = {
  ticket: 'WF-134',
  event_kinds: ['view', 'dwell', 'download', 'comment', 'share', 'question', 'meeting'],
  event_weights: { view: 0.5, meeting: 2 },
  weights: { engagement: 0.5, content: 0.3, stakeholder: 0.2 },
  bands: ['healthy', 'watch', 'at_risk'],
  risk_reasons: Object.keys(REASON_WORDS),
  frameworks: ['meddicc', 'bant', 'value_selling'],
  default_framework: 'meddicc',
  outcome_states: ['acknowledged', 'actioned', 'dismissed'],
  decisions: {
    framework: 'All three frameworks carry.',
    score_formula: 'The score is a weighted sum of three factors.',
    coaching_scope: 'Coaching is in scope as deterministic, rule-based cues.',
  },
  not_implemented: [
    'Live coaching during a call: needs telephony, recording and transcription.',
    'CRM opportunity fields as a data source, and a CRM update after a score.',
    'The vendor guarantees quoted in the research about quota and forecast accuracy.',
  ],
}

const CUES = [
  {
    id: 'cue_1',
    reason: 'unreviewed_key_material',
    action: 'Walk the buyer through the key material that is still unreviewed.',
    state: 'open',
    severity: 'high',
    value: 32,
    rep: 'sam',
    open: true,
  },
  {
    id: 'cue_2',
    reason: 'missing_stakeholders',
    action: 'Bring the stakeholder who never engaged onto the next call.',
    state: 'actioned',
    severity: 'high',
    value: 32,
    rep: 'sam',
    open: false,
  },
]

function answers(overrides = {}) {
  return {
    '/wf-134/vocabulary': VOCABULARY,
    '/wf-134/summary': {
      deals: 3,
      healthy: 1,
      watch: 1,
      at_risk: 1,
      open_alerts: 1,
      open_coaching_cues: 2,
      coaching_outcomes: 2,
      readiness: 0.5,
    },
    '/wf-134/readiness': {
      rep: null,
      outcomes: 2,
      actioned: 1,
      acknowledged: 0,
      dismissed: 1,
      readiness: 0.5,
      participation: 0.5,
    },
    '/wf-134/rooms/room_1/deals': { room_id: 'room_1', deals: [{ id: 'deal_1', name: 'Contoso pilot' }] },
    '/wf-134/deals/deal_1': { id: 'deal_1', name: 'Contoso pilot', account: 'Contoso Health', stage: 'discovery', framework: 'bant' },
    '/wf-134/deals/deal_1/score': { recorded: true, score: SCORE, last_recorded: { ...SCORE, id: 'score_1' } },
    '/wf-134/deals/deal_1/coverage': {
      deal_id: 'deal_1',
      coverage: 0,
      target: 0.6,
      engaged_count: 0,
      total: 2,
      met: false,
      engaged: [],
      not_engaged: [
        { id: 's1', name: 'Ada Chen', role: 'sponsor', engagement_count: 0, engaged: false },
        { id: 's2', name: 'Ben Silva', role: 'security', engagement_count: 0, engaged: false },
      ],
    },
    '/wf-134/deals/deal_1/content': {
      deal_id: 'deal_1',
      content: [
        { id: 'asset_1', name: 'Pilot Scope', kind: 'document', required: true, reviewed: false },
      ],
    },
    '/wf-134/deals/deal_1/alerts': {
      deal_id: 'deal_1',
      alerts: [
        {
          id: 'alert_1',
          kind: 'band',
          severity: 'high',
          detail: 'Deal health is 32 and the band is at risk.',
          value: 32,
          band: 'at_risk',
          raised_at: '2026-05-04T09:00:00+00:00',
          open: true,
        },
      ],
    },
    '/wf-134/deals/deal_1/coaching': { deal_id: 'deal_1', coaching: CUES },
    '/wf-134/deals/deal_1/history': {
      deal_id: 'deal_1',
      history: [{ ...SCORE, id: 'score_1' }],
    },
    '/wf-134/deals/deal_1/events': { deal_id: 'deal_1', events: [] },
    '/records/room': { records: [{ id: 'room_1', data: { name: 'Northwind' } }] },
    ...overrides,
  }
}

/** Route each API call this page makes to one canned answer. */
function stubFetch(canned, calls = []) {
  return vi.fn(async (url, options = {}) => {
    const path = String(url).split('?')[0].replace('/api', '')
    calls.push({ path, method: options.method || 'GET' })
    const body = Object.prototype.hasOwnProperty.call(canned, path) ? canned[path] : {}
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

// --------------------------------------------------------------------------- //
// The descriptor
// --------------------------------------------------------------------------- //

describe('the descriptor', () => {
  it('exports the four fields App.jsx reads', () => {
    expect(page.id).toBe('wf-134-deal-health')
    expect(page.label).toBe('Deal health')
    expect(typeof page.icon).toBe('string')
    expect(typeof page.Component).toBe('function')
  })

  it('carries its own glyph rather than editing the shared PATHS map', () => {
    expect(typeof page.iconPath).toBe('string')
    expect(page.order).toBeGreaterThan(0)
  })
})

// --------------------------------------------------------------------------- //
// The words
// --------------------------------------------------------------------------- //

describe('the words', () => {
  it('names every band, reason and outcome in words as well as tones', () => {
    for (const band of Object.keys(BAND_WORDS)) {
      expect(bandWord(band)).toBeTruthy()
      expect(bandTone(band)).toBe(BAND_TONES[band])
    }
    expect(reasonWord('unreviewed_key_material')).toBe('Key material unreviewed')
    expect(reasonWord('something_new')).toBe('something_new')
    expect(factorWord('engagement')).toBe('Engagement')
    expect(eventWord('meeting')).toBe('Meeting')
    expect(outcomeWord('open')).toBe('Open')
    expect(outcomeWord('actioned')).toBe('Actioned')
  })

  it('explains a conflict from its stable token, and falls back for an unknown one', () => {
    expect(conflictText('cue_already_closed')).toMatch(/already has an outcome/)
    expect(conflictText('mystery')).toBe('That change was refused.')
  })

  it('states how the score moved, and says when there is nothing to compare', () => {
    expect(deltaSentence({ delta: null })).toBe('First recorded reading.')
    expect(deltaSentence({ delta: 0 })).toBe('Unchanged since the previous reading.')
    expect(deltaSentence({ delta: -18 })).toBe('Down 18 points since the previous reading.')
    expect(deltaSentence({ delta: 4 })).toBe('Up 4 points since the previous reading.')
  })

  it('reads a number of days as a phrase, including when there are none', () => {
    expect(daysSince(null)).toBe('never recorded')
    expect(daysSince(undefined)).toBe('never recorded')
    expect(daysSince(0.2)).toBe('today')
    expect(daysSince(1)).toBe('1 day ago')
    expect(daysSince(12.4)).toBe('12 days ago')
  })

  it('shows a factor as a whole percentage inside the range', () => {
    expect(factorPercent({ factor: 0.634 })).toBe(63)
    expect(factorPercent({ factor: 0 })).toBe(0)
    expect(factorPercent({ factor: 2 })).toBe(100)
    expect(factorPercent({})).toBe(0)
  })

  it('shows what each factor contributes, and nothing when it cannot be read', () => {
    expect(weightedContribution({ factor: 0.63 }, { engagement: 0.5 }, 'engagement')).toBe(32)
    expect(weightedContribution({ factor: 1 }, { content: 0.3 }, 'content')).toBe(30)
    expect(weightedContribution(undefined, undefined, 'engagement')).toBeNull()
  })
})

// --------------------------------------------------------------------------- //
// The primitives
// --------------------------------------------------------------------------- //

describe('the primitives', () => {
  it('renders a band as a word and a score, never by colour alone', () => {
    render(<BandBadge band="at_risk" value={32} />)
    expect(screen.getByText('At risk')).toBeTruthy()
    expect(screen.getByText('32 / 100')).toBeTruthy()
  })

  it('renders every active reason with its sentence', () => {
    render(<ReasonList score={SCORE} />)
    expect(screen.getByText('Key material unreviewed')).toBeTruthy()
    expect(screen.getByText('Required key material is still unreviewed.')).toBeTruthy()
    expect(screen.getByText('Buying group not covered')).toBeTruthy()
  })

  it('says plainly when no reason is active', () => {
    render(<ReasonList score={{ reasons: [], reason_sentences: [] }} />)
    expect(screen.getByText(/No risk reason is active/)).toBeTruthy()
  })

  it('renders both coverage columns, including the empty one', () => {
    render(
      <CoverageColumns
        coverage={{ engaged: [{ id: 'a', name: 'Ada', role: 'sponsor', engagement_count: 3 }], not_engaged: [] }}
      />,
    )
    expect(screen.getByText('Engaged (1)')).toBeTruthy()
    expect(screen.getByText('Not engaged (0)')).toBeTruthy()
    expect(screen.getByText('Every named stakeholder has engaged.')).toBeTruthy()
  })

  it('marks a required asset reviewed and back, with the control labelled in words', async () => {
    const onToggle = vi.fn()
    const user = userEvent.setup()
    render(
      <ContentRow
        item={{ id: 'asset_1', name: 'Pilot Scope', kind: 'document', required: true, reviewed: false }}
        onToggle={onToggle}
      />,
    )
    expect(screen.getByText('Not reviewed')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: /Mark Pilot Scope as reviewed/ }))
    expect(onToggle).toHaveBeenCalledWith(expect.objectContaining({ id: 'asset_1' }), true)
  })

  it('shows an alert as open or resolved in words, and resolves only an open one', async () => {
    const onResolve = vi.fn()
    const user = userEvent.setup()
    const base = { id: 'alert_1', kind: 'band', severity: 'high', detail: 'Deal health is 32.', value: 32, band: 'at_risk', raised_at: '2026-05-04T09:00:00Z' }
    const { rerender } = render(<AlertRow alert={{ ...base, open: true }} onResolve={onResolve} />)
    expect(screen.getByText('Open')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: /Resolve the band alert/ }))
    expect(onResolve).toHaveBeenCalled()

    rerender(<AlertRow alert={{ ...base, open: false }} onResolve={onResolve} />)
    expect(screen.getByText('Resolved')).toBeTruthy()
    expect(screen.getByRole('button', { name: /Resolve the band alert/ })).toBeDisabled()
  })

  it('offers the three outcomes on an open cue and none on a closed one', () => {
    const { rerender } = render(<CueRow cue={CUES[0]} onOutcome={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Actioned' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Acknowledged' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Dismissed' })).toBeTruthy()

    rerender(<CueRow cue={CUES[1]} onOutcome={vi.fn()} />)
    expect(screen.queryByRole('button', { name: 'Actioned' })).toBeNull()
    expect(screen.getByText('Actioned')).toBeTruthy()
  })

  it('names the denominator of the readiness reading', () => {
    render(
      <ReadinessPanel
        readiness={{ outcomes: 4, actioned: 2, acknowledged: 1, dismissed: 1, readiness: 0.5, participation: 0.75 }}
      />,
    )
    expect(screen.getByText('50% of coaching cues were actioned.')).toBeTruthy()
    expect(screen.getByText(/2 actioned, 1 acknowledged, 1 dismissed of 4 outcome\(s\)/)).toBeTruthy()
  })

  it('says there is no readiness reading rather than reporting zero', () => {
    render(<ReadinessPanel readiness={{ outcomes: 0, readiness: null }} />)
    expect(screen.getByText(/No coaching cue has been closed yet/)).toBeTruthy()
  })

  it('renders a standing message in a live region', () => {
    render(<Notice tone="warning">Starter is refused.</Notice>)
    expect(screen.getByRole('status').textContent).toContain('Starter is refused.')
  })
})

// --------------------------------------------------------------------------- //
// The page
// --------------------------------------------------------------------------- //

describe('the page', () => {
  it('loads the board and reads the score with its factors', async () => {
    vi.stubGlobal('fetch', stubFetch(answers()))
    render(<Component />)

    expect(await screen.findByText('Contoso pilot | deal health 32 of 100')).toBeTruthy()
    expect(screen.getAllByText('At risk').length).toBeGreaterThan(0)
    expect(screen.getByText('Engagement')).toBeTruthy()
    expect(screen.getByText('Key material')).toBeTruthy()
    expect(screen.getByText('Stakeholder coverage')).toBeTruthy()
    expect(screen.getByText(/0 of 1 required asset\(s\) reviewed/)).toBeTruthy()
  })

  it('maps who engaged and who did not', async () => {
    vi.stubGlobal('fetch', stubFetch(answers()))
    render(<Component />)

    expect(await screen.findByText('Ada Chen')).toBeTruthy()
    expect(screen.getByText('Ben Silva')).toBeTruthy()
    expect(screen.getByText('Engaged (0)')).toBeTruthy()
    expect(screen.getByText('Not engaged (2)')).toBeTruthy()
  })

  it('offers the coaching cue and the readiness reading', async () => {
    vi.stubGlobal('fetch', stubFetch(answers()))
    render(<Component />)

    expect(
      await screen.findByText('Walk the buyer through the key material that is still unreviewed.'),
    ).toBeTruthy()
    expect(screen.getByText('50% of coaching cues were actioned.')).toBeTruthy()
  })

  it('names the surfaces it does not build rather than omitting them', async () => {
    vi.stubGlobal('fetch', stubFetch(answers()))
    render(<Component />)

    expect(await screen.findByText('Not built here')).toBeTruthy()
    expect(screen.getByText(/Live coaching during a call/)).toBeTruthy()
    expect(screen.getByText(/no evidence for|guarantees quoted in the research/)).toBeTruthy()
  })

  it('records an engagement event and scores, which is the automation', async () => {
    const calls = []
    vi.stubGlobal('fetch', stubFetch(answers(), calls))
    const user = userEvent.setup()
    render(<Component />)

    const person = await screen.findByLabelText('Person')
    await user.type(person, 'Ada Chen')
    await user.click(screen.getByRole('button', { name: 'Record the event and score' }))

    await waitFor(() =>
      expect(
        calls.some(
          (call) => call.method === 'POST' && call.path === '/wf-134/deals/deal_1/events',
        ),
      ).toBe(true),
    )
  })

  it('closes a coaching cue with an outcome', async () => {
    const calls = []
    vi.stubGlobal('fetch', stubFetch(answers(), calls))
    const user = userEvent.setup()
    render(<Component />)

    await screen.findByText('Walk the buyer through the key material that is still unreviewed.')
    await user.click(screen.getAllByRole('button', { name: 'Actioned' })[0])

    await waitFor(() =>
      expect(
        calls.some(
          (call) => call.method === 'POST' && call.path === '/wf-134/coaching/outcome',
        ),
      ).toBe(true),
    )
  })

  it('resolves an open risk alert', async () => {
    const calls = []
    vi.stubGlobal('fetch', stubFetch(answers(), calls))
    const user = userEvent.setup()
    render(<Component />)

    await screen.findByText('Deal health is 32 and the band is at risk.')
    await user.click(screen.getByRole('button', { name: /Resolve the band alert/ }))

    await waitFor(() =>
      expect(
        calls.some((call) => call.method === 'POST' && call.path === '/wf-134/alerts/resolve'),
      ).toBe(true),
    )
  })
})
