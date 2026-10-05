import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import {
  ACCEPTANCE_METHODS,
  ACTIVITY_LABELS,
  SIGNING_ORDER,
  STATUS_LABELS,
  acceptanceFacts,
  documentSize,
  formatInstant,
  plural,
  signingStep,
} from './api'
import descriptor, { AcceptancePage } from './index.jsx'
import { EventRow, EvidenceNote, QuotaPanel, SignerRow, SigningRail, StatusBadge } from './primitives'

/**
 * Tests for the pieces of WF-095 that can get quietly wrong.
 *
 * Nine claims are pinned here, and each is one the specification or the recorded decision
 * makes explicitly:
 *
 * 1. The signing chain is the research's four statuses in the research's order, and a rail
 *    step reads "Done", "Now" or "Waiting" so the state is never carried by colour alone.
 * 2. A status badge always carries the status word, because colour alone is not a state.
 * 3. A signer row says who still owes a signature, in words, and shows the signing order the
 *    research fixes: the buyer signs first and the countersigner second.
 * 4. An event that writes no activity row says so, rather than leaving a blank that reads as
 *    a missing value.
 * 5. The quota panel says "none stated" when the research stated no ceiling, rather than
 *    rendering an empty or invented number.
 * 6. A size with no document behind it renders as a dash, never as a zero.
 * 7. An unparsable instant renders as a dash, never as "Invalid Date".
 * 8. The four activity labels are the research's four, in the research's wording.
 * 9. The descriptor is the shape the feature host discovers, and its icon name already exists
 *    in the shared set because that file is not edited.
 *
 * The page component itself needs a stubbed transport, so the tests below cover its building
 * blocks, its helpers and its descriptor rather than driving eleven fetches.
 */

describe('the descriptor', () => {
  it('exports the shape the feature host discovers', () => {
    expect(descriptor.id).toBe('wf-095-collect-acceptance-by-e-signature-countersignature')
    expect(typeof descriptor.label).toBe('string')
    expect(descriptor.Component).toBe(AcceptancePage)
  })

  it('uses an icon name that exists in the shared set', () => {
    // `components/ui.jsx` is not edited, so the name must already be there. The Icon component
    // falls back to a generic glyph for an unknown name, which would hide this mistake.
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

describe('the signing chain', () => {
  it('is the research four statuses in the research order', () => {
    expect(SIGNING_ORDER).toEqual([
      'pending_signature',
      'viewed_pending_signature',
      'pending_countersignature',
      'accepted',
    ])
    expect(Object.keys(STATUS_LABELS)).toEqual(SIGNING_ORDER)
  })

  it('uses the research own status wording', () => {
    expect(STATUS_LABELS.pending_signature).toBe('Pending signature')
    expect(STATUS_LABELS.viewed_pending_signature).toBe('Viewed - pending signature')
    expect(STATUS_LABELS.pending_countersignature).toBe('Pending countersignature')
    expect(STATUS_LABELS.accepted).toBe('Accepted')
  })

  it('reports the step index of a status, and -1 for an unknown one', () => {
    expect(signingStep('pending_signature')).toBe(0)
    expect(signingStep('accepted')).toBe(3)
    expect(signingStep('never_heard_of_it')).toBe(-1)
  })
})

describe('the signing rail', () => {
  it('marks every step with a word as well as with its position', () => {
    render(<SigningRail status="pending_countersignature" />)

    // Two steps are behind the current one, so two read "Done" and one reads "Now".
    expect(screen.getAllByText('Done')).toHaveLength(2)
    expect(screen.getByText('Now')).toBeDefined()
    expect(screen.getAllByText('Waiting')).toHaveLength(1)
  })

  it('marks the current step with aria-current so assistive technology reads it', () => {
    const { container } = render(<SigningRail status="viewed_pending_signature" />)
    const current = container.querySelector('[aria-current="step"]')
    expect(current).not.toBeNull()
    expect(current.textContent).toContain('Viewed - pending signature')
  })

  it('renders every status on the chain, so a rail never shows a partial journey', () => {
    render(<SigningRail status="accepted" />)
    for (const label of Object.values(STATUS_LABELS)) {
      expect(screen.getByText(label)).toBeDefined()
    }
  })

  it('lights nothing for an unknown status rather than guessing a step', () => {
    render(<SigningRail status="not_a_status" />)
    expect(screen.queryByText('Now')).toBeNull()
    expect(screen.queryByText('Done')).toBeNull()
  })
})

describe('the status badge', () => {
  it('always carries the status word, so colour is never the only carrier', () => {
    for (const status of SIGNING_ORDER) {
      const { unmount } = render(<StatusBadge status={status} />)
      expect(screen.getByText(STATUS_LABELS[status])).toBeDefined()
      unmount()
    }
  })

  it('renders an unknown status as itself rather than as an empty badge', () => {
    render(<StatusBadge status="mystery_state" />)
    expect(screen.getByText('mystery_state')).toBeDefined()
  })
})

describe('a signer row', () => {
  const buyer = {
    id: 'sig_1',
    name: 'Ada Byron',
    email: 'ada@northwind.example',
    role: 'buyer',
    role_label: 'Buyer contact',
    signing_order: 1,
    signed: false,
    signed_at: null,
    verification_required: true,
    verified: false,
  }

  it('says the party still owes a signature, in words', () => {
    render(<SignerRow signer={buyer} onSign={() => {}} onReassign={() => {}} busy={false} />)
    expect(screen.getByText('Not signed')).toBeDefined()
    expect(screen.getByText('Verify email')).toBeDefined()
  })

  it('shows the signing order the research fixes', () => {
    const { unmount } = render(
      <SignerRow signer={buyer} onSign={() => {}} onReassign={() => {}} busy={false} />,
    )
    expect(screen.getByText(/Signs first/)).toBeDefined()
    unmount()

    render(
      <SignerRow
        signer={{ ...buyer, role: 'countersigner', signing_order: 2 }}
        onSign={() => {}}
        onReassign={() => {}}
        busy={false}
      />,
    )
    expect(screen.getByText(/Signs second/)).toBeDefined()
  })

  it('labels a countersigner as a countersigner, not as a buyer contact', () => {
    render(
      <SignerRow
        signer={{ ...buyer, role: 'countersigner', role_label: 'Countersigner', signing_order: 2 }}
        onSign={() => {}}
        onReassign={() => {}}
        busy={false}
      />,
    )
    expect(screen.getByText('Countersigner')).toBeDefined()
    expect(screen.queryByText('Buyer contact')).toBeNull()
  })

  it('says a signed party is signed and offers no further signature', () => {
    render(
      <SignerRow
        signer={{ ...buyer, signed: true, signed_at: '2026-10-05T12:00:00+00:00' }}
        onSign={() => {}}
        onReassign={() => {}}
        busy={false}
      />,
    )
    // "Signed" appears on the state badge and on the disabled button, and both must say it.
    expect(screen.getAllByText('Signed').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByRole('button', { name: 'Signed' }).disabled).toBe(true)
  })

  it('reports a verified buyer as verified rather than as needing an email', () => {
    render(
      <SignerRow
        signer={{ ...buyer, verified: true }}
        onSign={() => {}}
        onReassign={() => {}}
        busy={false}
      />,
    )
    expect(screen.getByText('Verified')).toBeDefined()
  })
})

describe('an event row', () => {
  it('names the activity the research names', () => {
    render(
      <EventRow
        event={{
          id: 'evt_1',
          event: 'buyer_signed',
          activity: 'quote_buyer_signed',
          status_before: 'viewed_pending_signature',
          status_after: 'pending_countersignature',
          at: '2026-10-05T12:00:00+00:00',
          detail: {},
        }}
      />,
    )
    expect(screen.getByText('Quote buyer signed')).toBeDefined()
    expect(screen.getByText(/moved to Pending countersignature/)).toBeDefined()
  })

  it('says so in words when an event wrote no activity row', () => {
    render(
      <EventRow
        event={{
          id: 'evt_2',
          event: 'viewed',
          activity: null,
          status_before: 'pending_signature',
          status_after: 'viewed_pending_signature',
          at: '2026-10-05T12:00:00+00:00',
          detail: {},
        }}
      />,
    )
    expect(screen.getByText('No activity row')).toBeDefined()
  })

  it('says an event left the status alone rather than implying a move', () => {
    render(
      <EventRow
        event={{
          id: 'evt_3',
          event: 'attempt_failed',
          activity: 'signing_attempt_failed',
          status_before: 'pending_signature',
          status_after: 'pending_signature',
          at: '2026-10-05T12:00:00+00:00',
          detail: { reason: 'verification_not_requested' },
        }}
      />,
    )
    expect(screen.getByText('Signing attempt failed')).toBeDefined()
    expect(screen.getByText(/Stayed at Pending signature/)).toBeDefined()
    expect(screen.getByText('verification_not_requested')).toBeDefined()
  })
})

describe('the four activity labels', () => {
  it('are the research four, in the research wording', () => {
    expect(Object.values(ACTIVITY_LABELS)).toEqual([
      'Quote buyer signed',
      'Quote countersigned',
      'Quote reassigned',
      'Signing attempt failed',
    ])
  })
})

describe('the three acceptance methods', () => {
  it('are the research three, each with the label the seller sidebar shows', () => {
    expect(ACCEPTANCE_METHODS.map((choice) => choice.value)).toEqual([
      'esignature',
      'clickwrap',
      'print_and_sign',
    ])
    expect(ACCEPTANCE_METHODS[0].label).toBe('E-signature')
    expect(ACCEPTANCE_METHODS[1].label).toBe('Accept without signature')
    expect(ACCEPTANCE_METHODS[2].label).toBe('Print and sign')
  })
})

describe('the researched facts', () => {
  it('carry the evidence sentence beside every number the page shows', () => {
    expect(acceptanceFacts.verificationWindowMinutes).toBe(60)
    expect(acceptanceFacts.verificationWindowQuote).toContain('one hour')
    expect(acceptanceFacts.pdfSizeCapMb).toBe(40)
    expect(acceptanceFacts.pdfSizeCapQuote).toContain('40 MB')
  })

  it('name the contract as a downstream consumer rather than as this workflow', () => {
    expect(acceptanceFacts.contractIsDownstreamQuote).toContain('WF-099')
  })

  it('name the identity duty as the integrator', () => {
    expect(acceptanceFacts.authenticationOwnerQuote).toContain('your responsibility')
  })
})

describe('the quota panel', () => {
  it('says none stated when the research stated no ceiling', () => {
    render(
      <QuotaPanel
        quota={{
          used: 2,
          envelopes_charged: 2,
          month: '2026-10',
          limit: null,
          reset_day: 1,
          quota_unspecified_quote: 'The research states no number.',
          counts_envelope_not_signer_quote: 'three signatures, one usage',
          consumed_on_enable_quote: 'as soon as the option is turned on',
        }}
      />,
    )
    expect(screen.getByText('none stated')).toBeDefined()
    expect(screen.getByText(/2 envelopes charged in 2026-10/)).toBeDefined()
    expect(screen.getByText('The research states no number.')).toBeDefined()
  })

  it('renders a stated ceiling as a number when one exists', () => {
    render(
      <QuotaPanel
        quota={{
          used: 10,
          envelopes_charged: 10,
          month: '2026-10',
          limit: 25,
          reset_day: 1,
          quota_unspecified_quote: '',
          counts_envelope_not_signer_quote: '',
          consumed_on_enable_quote: '',
        }}
      />,
    )
    expect(screen.getByText('25')).toBeDefined()
  })

  it('renders nothing rather than a broken panel when the quota is absent', () => {
    const { container } = render(<QuotaPanel quota={null} />)
    expect(container.textContent).toBe('')
  })
})

describe('an evidence note', () => {
  it('sets the sentence as a blockquote so it reads as evidence, not as a heading', () => {
    const { container } = render(<EvidenceNote quote="The evidence sentence." />)
    expect(container.querySelector('blockquote')?.textContent).toBe('The evidence sentence.')
    expect(screen.getByText('Evidence')).toBeDefined()
  })

  it('renders nothing rather than an empty figure when there is no quote', () => {
    const { container } = render(<EvidenceNote quote={null} />)
    expect(container.firstChild).toBeNull()
  })
})

describe('documentSize', () => {
  it('renders a byte count as MB to one decimal place', () => {
    expect(documentSize(1024 * 1024)).toBe('1.0 MB')
    expect(documentSize(1_800_000)).toBe('1.7 MB')
  })

  it('renders a dash for an absent size, never a zero', () => {
    expect(documentSize(null)).toBe('--')
    expect(documentSize(undefined)).toBe('--')
  })
})

describe('formatInstant', () => {
  it('renders an ISO instant rather than a raw machine value', () => {
    expect(formatInstant('2026-10-05T12:00:00+00:00')).not.toBe('--')
  })

  it('renders a dash for an absent or unparsable instant, never Invalid Date', () => {
    expect(formatInstant(null)).toBe('--')
    expect(formatInstant('')).toBe('--')
    expect(formatInstant('not a date')).toBe('--')
  })
})

describe('plural', () => {
  it('agrees with its count', () => {
    expect(plural(1, 'envelope')).toBe('1 envelope')
    expect(plural(0, 'envelope')).toBe('0 envelopes')
    expect(plural(3, 'signer')).toBe('3 signers')
  })

  it('takes an irregular plural rather than adding an s', () => {
    expect(plural(2, 'party still owes', 'parties still owe')).toBe('2 parties still owe')
  })
})
