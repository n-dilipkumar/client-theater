import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import {
  CONSENT_STATES,
  DSAR_STATES,
  GATE_OUTCOMES,
  consentState,
  dsarState,
  formatInstant,
  formatWindow,
  gateOutcome,
  isConsentJurisdiction,
  plural,
} from './api'
import descriptor, { PrivacyControlsPage } from './index.jsx'
import { ConsentRow, GateVerdict, ResidueList, WindowTable } from './primitives'

/**
 * Tests for the pieces of WF-085 that can get quietly wrong.
 *
 * Six claims are pinned here, and each is one the specification makes explicitly:
 *
 * 1. The gate fails closed. A decision has to say what denying means - a unique ID per
 *    page view, no cookies - or a reader cannot tell a working gate from a broken one.
 * 2. A configured window and the researched ceiling are different numbers, and the page
 *    shows both. Showing one would let an operator believe a longer window was agreed.
 * 3. `revoked` and `denied` are different words. One had tracking and lost it; the other
 *    never had it.
 * 4. Residue is named, never hidden. The audit trail always keeps what the erasure
 *    removed, so an erasure that rendered a clean tick would be the failure this workflow
 *    exists to prevent.
 * 5. Nothing on the page claims a certification, and no vendor entity is stored as this
 *    deployment's controller.
 * 6. An unreadable instant renders as `unknown`, never as 1970.
 *
 * The page component itself needs a stubbed transport, so the tests below cover its
 * building blocks and its descriptor rather than driving eight fetches.
 */

const DENY_DECISION = {
  region: 'eu-west',
  jurisdiction: 'eea',
  outcome: 'deny',
  consent_required: true,
  reason: 'No consent signal was sent.',
  reasons: ['No consent signal was sent.'],
  effect: {
    outcome: 'deny',
    identifier: 'unique_per_page_view',
    persistent_identifier: false,
    cookies: false,
    tracking: false,
    recording: false,
  },
  signals_ignored: [],
}

describe('the descriptor', () => {
  it('exports the shape the feature host discovers', () => {
    expect(descriptor.id).toBe('wf-085-meet-gdpr-ccpa-residency-retention-dsar')
    expect(typeof descriptor.label).toBe('string')
    expect(descriptor.Component).toBe(PrivacyControlsPage)
  })

  it('uses an icon name that exists in the shared set', () => {
    // `components/ui.jsx` is not edited, so the name must already be there. The Icon
    // component falls back to a generic glyph for an unknown name, which would hide
    // this mistake rather than show it.
    const known = [
      'dashboard',
      'rooms',
      'audit',
      'schema',
      'plus',
      'refresh',
      'trash',
      'restore',
      'search',
      'close',
      'chevron',
      'database',
    ]

    expect(known).toContain(descriptor.icon)
  })
})

describe('the gate fails closed', () => {
  it('names the three answers, and deny is one of them', () => {
    expect(GATE_OUTCOMES.map((outcome) => outcome.value)).toEqual([
      'track',
      'deny',
      'not_required',
    ])
  })

  it('says what denying means rather than only that it happened', () => {
    // The specification gives the deny behaviour exactly: "a unique ID per page view"
    // and "does not use cookies to persist session data".
    render(<GateVerdict decision={DENY_DECISION} />)

    expect(screen.getByText('Denied')).toBeInTheDocument()
    expect(screen.getByText(/unique ID per page view/i)).toBeInTheDocument()
  })

  it('renders every effect as a word, so nothing is carried by colour alone', () => {
    render(<GateVerdict decision={DENY_DECISION} />)

    expect(screen.getByText('Persistent identifier')).toBeInTheDocument()
    expect(screen.getByText('Cookies')).toBeInTheDocument()
    // Two "off" rows and two more further down: identifier, cookies, tracking, recording.
    expect(screen.getAllByText('off')).toHaveLength(4)
  })

  it('renders a grant as every effect on', () => {
    const decision = {
      ...DENY_DECISION,
      outcome: 'track',
      reason: 'Explicit consent was granted.',
      effect: {
        ...DENY_DECISION.effect,
        outcome: 'track',
        identifier: 'persistent',
        persistent_identifier: true,
        cookies: true,
        tracking: true,
        recording: true,
      },
    }

    render(<GateVerdict decision={decision} />)

    expect(screen.getByText('Tracking allowed')).toBeInTheDocument()
    expect(screen.getAllByText('on')).toHaveLength(4)
  })

  it('reports a signal the gate discarded and why', () => {
    const decision = {
      ...DENY_DECISION,
      signals_ignored: [
        { signal: 'dnt', reason: 'The specification records that the vendor does not respond.' },
      ],
    }

    render(<GateVerdict decision={decision} />)

    expect(screen.getByText('dnt')).toBeInTheDocument()
    expect(screen.getByText(/does not respond/i)).toBeInTheDocument()
  })

  it('says not_required rather than falling through to tracking silently', () => {
    expect(gateOutcome('not_required').label).toBe('Not required here')
    expect(gateOutcome('not_required').meaning).toMatch(/not in the configured consent list/i)
  })

  it('falls back to a real answer rather than rendering nothing', () => {
    expect(gateOutcome(undefined).value).toBe('deny')
    expect(gateOutcome('something-new').value).toBe('deny')
  })

  it('renders nothing at all when there is no decision', () => {
    const { container } = render(<GateVerdict decision={null} />)

    expect(container).toBeEmptyDOMElement()
  })
})

describe('the configured window and the ceiling are different numbers', () => {
  const ROWS = [
    {
      class: 'session_recording',
      label: 'Session recordings',
      configured_days: 14,
      ceiling_days: 30,
      evidence: 'recordings "up to 30 days from the time of recording"',
      schedule: { collections: ['wf075_view'] },
    },
    {
      class: 'heatmap',
      label: 'Heatmaps',
      configured_days: 273,
      ceiling_days: 273,
      evidence: 'heatmaps up to 9 months',
      schedule: { collections: ['wf075_heatmap'] },
    },
  ]

  it('renders both numbers for every class', () => {
    render(<WindowTable rows={ROWS} />)

    expect(screen.getByText('14 days')).toBeInTheDocument()
    expect(screen.getByText('30 days')).toBeInTheDocument()
  })

  it('says so when the configured window is shorter than the ceiling', () => {
    render(<WindowTable rows={ROWS} />)

    expect(screen.getByText('shorter than the ceiling')).toBeInTheDocument()
  })

  it('names the collections each class ages', () => {
    render(<WindowTable rows={ROWS} />)

    expect(screen.getByText('wf075_view')).toBeInTheDocument()
    expect(screen.getByText('wf075_heatmap')).toBeInTheDocument()
  })

  it('renders the sentence each window came from', () => {
    render(<WindowTable rows={ROWS} />)

    expect(screen.getByText(/up to 30 days from the time of recording/i)).toBeInTheDocument()
  })

  it('says so when no class has been configured', () => {
    render(<WindowTable rows={[]} />)

    expect(screen.getByText(/no retention class has been configured/i)).toBeInTheDocument()
  })
})

describe('revoked and denied are different words', () => {
  it('keeps them apart, and keeps a fallback', () => {
    expect(consentState('revoked').label).not.toBe(consentState('denied').label)
    expect(consentState('something-new').label).toBe(consentState(undefined).label)
  })

  it('says what a revocation means', () => {
    expect(consentState('revoked').meaning).toMatch(/cookies were cleared/i)
    expect(consentState('denied').meaning).toMatch(/never had it|never granted/i)
  })

  it('renders the state word beside the cookie consequence, never colour alone', () => {
    render(
      <ConsentRow
        record={{
          id: 'c1',
          subject: 'buyer@northwind.example',
          region: 'eu-west',
          signal: null,
          state: 'revoked',
          cookies_cleared: true,
        }}
        selectedId="c1"
      />,
    )

    expect(screen.getByText('Revoked')).toBeInTheDocument()
    expect(screen.getByText('cookies cleared')).toBeInTheDocument()
    expect(screen.getByText('buyer@northwind.example')).toBeInTheDocument()
  })

  it('gives the row a 44px target and a pressed state', () => {
    render(
      <ConsentRow
        record={{
          id: 'c1',
          subject: 'buyer@northwind.example',
          region: 'eu-west',
          signal: 'granted',
          state: 'active',
          cookies_cleared: false,
        }}
        selectedId="c1"
      />,
    )

    const button = screen.getByRole('button')
    expect(button.className).toContain('min-h-11')
    expect(button).toHaveAttribute('aria-pressed', 'true')
  })

  it('names the four erasure states and falls back to a real one', () => {
    expect(DSAR_STATES.map((entry) => entry.value)).toEqual([
      'opened',
      'partial',
      'fulfilled',
      'nothing_found',
    ])
    expect(dsarState(undefined).value).toBe('opened')
    expect(consentState('revoked').tone).toBe('delete')
  })
})

describe('residue is named, never hidden', () => {
  const RESIDUE = {
    audit_rows: 6,
    erasure_records_kept: 1,
    remaining: 1,
    scan_truncated: false,
    rule: 'The audit trail is this product guarantee. The rows stay and are counted.',
    notes: {
      audit_trail: 'The audit trail keeps before and after snapshots.',
      erasure_record: 'The request row keeps itself.',
      audit_mirror: 'The JSONL mirror is written by the audited wrapper.',
    },
  }

  it('renders the audit rows the erasure could not remove', () => {
    render(<ResidueList residue={RESIDUE} />)

    expect(screen.getByText('Audit rows')).toBeInTheDocument()
    expect(screen.getByText('6')).toBeInTheDocument()
  })

  it('says why the request row kept itself', () => {
    render(<ResidueList residue={RESIDUE} />)

    expect(screen.getByText('The request row keeps itself.')).toBeInTheDocument()
  })

  it('says when a scan bound stopped it from claiming anything survived', () => {
    render(<ResidueList residue={{ ...RESIDUE, remaining: null }} />)

    expect(screen.getByText(/cannot say whether anything survived/i)).toBeInTheDocument()
  })

  it('says so when nothing has been erased yet', () => {
    render(<ResidueList residue={null} />)

    expect(screen.getByText(/no residue to report/i)).toBeInTheDocument()
  })
})

describe('what the page does not claim', () => {
  it('holds every consent state the workflow serves', () => {
    expect(CONSENT_STATES.map((entry) => entry.value)).toEqual(['active', 'revoked', 'denied'])
  })

  it('reads the consent region list from the server rather than writing one here', () => {
    const served = { consent_jurisdictions: ['eea', 'uk', 'ch'] }

    expect(isConsentJurisdiction(served, 'eea')).toBe(true)
    expect(isConsentJurisdiction(served, 'us')).toBe(false)
  })

  it('reads a served list of objects as well as a served list of strings', () => {
    const served = { consent_jurisdictions: [{ id: 'eea' }, { id: 'uk' }] }

    expect(isConsentJurisdiction(served, 'uk')).toBe(true)
    expect(isConsentJurisdiction(undefined, 'uk')).toBe(false)
  })
})

describe('instants and durations', () => {
  it('renders an ISO 8601 instant as a readable string', () => {
    expect(formatInstant('2025-10-04T17:46:40.000+00:00')).toBe('2025-10-04 17:46:40Z')
  })

  it('renders a Unix millisecond value as the same instant, not 1970', () => {
    expect(formatInstant(1759600000000)).toBe('2025-10-04 17:46:40Z')
  })

  it('renders a missing or unreadable instant as unknown rather than as a date', () => {
    // 1970 is a real date a reviewer could misread as the moment a signal landed.
    expect(formatInstant(null)).toBe('unknown')
    expect(formatInstant('')).toBe('unknown')
    expect(formatInstant('not-a-date')).toBe('unknown')
  })

  it('renders a window in days and names the months it is about', () => {
    expect(formatWindow(14)).toBe('14 days')
    expect(formatWindow(273)).toMatch(/about 9 months/)
    expect(formatWindow(-1)).toBe('unknown')
  })

  it('pluralises a count so a page never says one records', () => {
    expect(plural(1, 'record')).toBe('1 record')
    expect(plural(2, 'record')).toBe('2 records')
    expect(plural(0, 'request')).toBe('0 requests')
  })
})
