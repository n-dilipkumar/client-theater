/**
 * WF-096 page tests.
 *
 * `apiRequest` is never mocked. `globalThis.fetch` is replaced instead, keyed by path, which
 * keeps the client under test on the path a browser would take and is what the other feature
 * tests in this product do.
 *
 * Every test builds its own stub and its own fixture, so this file passes alone and under
 * vitest's parallel run without depending on another test's data.
 *
 * The states below are the ones this page must never get wrong. Each is either a rule the
 * research states outright or a reading that would be actively misleading:
 *
 *   * loading, error and empty, because a payments board that goes blank reads as "there is
 *     nothing here", which for a page about money is the one reading that must be impossible;
 *   * acceptance and payment as two separate acts, because a declined charge must never undo an
 *     acceptance the buyer already gave, and a page that wires them into one button cannot
 *     express that;
 *   * a declined charge, which is a 200 carrying `outcome: 'declined'` and not an HTTP error, so
 *     it must read on the page as an outcome rather than as a broken request;
 *   * the strict `$0.50` minimum and the three-tax-ID cap, stated on the page beside the rule
 *     they govern, because a number a seller cannot see is a number they cannot check;
 *   * the irreversibility of an accepted quote, because "Quotes can't be voided or deleted after
 *     they have been accepted" is a researched rule and the controls must follow it;
 *   * the *Do not specify* recipient, because the research says a contact "doesn't need to be
 *     added to the quote" and a form demanding one would refuse a purchase-order acceptance.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import descriptor, { AcceptAndPayPage } from './index.jsx'
import { FACTS, RAIL, amountDue, paymentState, paymentTone, plural, railStep } from './api'
import {
  AcceptanceRail,
  ChargeRow,
  EvidenceNote,
  InvoiceRow,
  MinimumChargePanel,
  PaymentBadge,
  TaxIdPanel,
  evidenceFor,
} from './primitives'

const ROOM = 'room_a'

/** The minimum charge's own sentence, so a page that drops it fails rather than reads plainer. */
const MINIMUM_CHARGE_SENTENCE =
  'when using HubSpot payments or Stripe as your payment processing option, the total amount due must be more than $0.50, or the equivalent minimum of the settlement currency.'

/** The cap's sentence. */
const TAX_ID_SENTENCE = 'Buyers can add up to three tax IDs to the quote'

/** The whole-number rule, which publish enforces. */
const WHOLE_NUMBER_SENTENCE =
  'if the quote offers online payments or the Enable billing switch is on, line item quantities must be whole numbers.'

const SUMMARY = {
  quotes: 2,
  setups: 2,
  accepted: 1,
  awaiting_acceptance: 1,
  charges: 2,
  charges_recorded: 1,
  charges_declined: 1,
  charged_total: 1500,
  pending_payment: 0,
  invoices: 4,
  invoices_scheduled: 3,
  subscriptions: 1,
  tax_ids: 2,
  amount_due_total: 1500.2,
  by_payment_type: { HUBSPOT: 2, BYO_STRIPE: 0 },
  minimum_charge_usd: 0.5,
  tax_id_limit: 3,
  minimum_charge_quote: MINIMUM_CHARGE_SENTENCE,
  first_invoice_quote:
    'The first invoice is also generated and sent to the buyer immediately after quote acceptance, regardless of its scheduled invoice date or due date.',
  not_owned: {
    quote_authoring: 'WF-086 authors the quote and its line items; this workflow reads them.',
    e_signature: "WF-095 collects acceptance by e-signature; this workflow is the clickwrap path.",
  },
}

const VOCABULARY = {
  tax_id_limit: 3,
  minimum_charge_usd: 0.5,
  payment_methods: ['ACH', 'CREDIT_OR_DEBIT_CARD', 'SEPA', 'BACS', 'PADS'],
  evidence: {
    minimum_charge: MINIMUM_CHARGE_SENTENCE,
    tax_id: TAX_ID_SENTENCE,
    whole_number_quantity: WHOLE_NUMBER_SENTENCE,
    online_payment_acceptance:
      "online payments require an acceptance method of E-signature or Accept without signature. Print and sign isn't a valid acceptance method with online payments.",
  },
  not_owned: SUMMARY.not_owned,
}

const DECISIONS = {
  count: 1,
  decisions: [
    {
      question: 'How should a charge below the minimum be answered?',
      chosen: 'recorded_declined_outcome',
      rejected_because: 'A processor decline is a definite result, not a malformed request.',
      left_open_by: 'The research states the constraint and not whether a decline is an error.',
      audit_id: 'jev-20261005T121102-5456-62628',
    },
  ],
}

/** A quote with one line, priced above the minimum. */
function quote(overrides = {}) {
  return {
    id: 'q_paid',
    room_id: ROOM,
    revision: 1,
    title: 'Northwind platform renewal',
    status: 'ACCEPTED',
    hs_status: 'ACCEPTED',
    currency: 'USD',
    accepted: true,
    quote_id: 'q_paid',
    company_name: 'Northwind Logistics',
    created_at: '2026-01-05T09:00:00Z',
    updated_at: '2026-01-06T09:00:00Z',
    amount_due: { subtotal: 1500, discount: 0, tax: 0, total: 1500, line_count: 1 },
    line_items: [
      {
        id: 'line_1',
        name: 'Platform seats',
        quantity: 25,
        unit_price: 60,
        billing_frequency: 'monthly',
      },
    ],
    acceptance: { id: 'acc_1', accepted_by: 'Ada Byron', at: '2026-01-06T09:00:00Z' },
    ...overrides,
  }
}

/** A quote published but never accepted, so the board has a second rail position. */
function awaitingQuote() {
  return quote({
    id: 'q_waiting',
    title: 'Orbis sample order',
    status: 'published',
    hs_status: '',
    accepted: false,
    acceptance: null,
    amount_due: { subtotal: 1200, discount: 0, tax: 0, total: 1200, line_count: 1 },
    line_items: [{ id: 'line_2', name: 'Licence', quantity: 1, unit_price: 1200, billing_frequency: 'one_time' }],
  })
}

const SETUP = {
  id: 'setup_1',
  quote_id: 'q_paid',
  acceptance_method: 'clickwrap',
  acceptance_method_label: 'Accept without signature',
  allowed_payment_methods: ['CREDIT_OR_DEBIT_CARD', 'ACH'],
  payment_type: 'HUBSPOT',
  payment_status: 'PENDING',
  hs_payment_status: 'PENDING',
  billing_enabled: true,
  payment_enabled: true,
  collection_process: 'AUTO_PAYMENTS',
  net_payment_terms: 'NET_30',
  effective_date: '2026-01-06',
  created_at: '2026-01-05T09:00:00Z',
}

/** The same setup, for a quote published but not yet accepted. */
function setupFor(quoteId) {
  return { ...SETUP, id: `setup_${quoteId}`, quote_id: quoteId }
}

const CHARGES = [
  {
    id: 'chg_1',
    quote_id: 'q_paid',
    outcome: 'recorded',
    amount: 1500,
    currency: 'USD',
    payment_method: 'CREDIT_OR_DEBIT_CARD',
    payment_type: 'HUBSPOT',
    initiated_at: '2026-01-06T09:05:00Z',
    settled_at: '2026-01-06T09:06:00Z',
  },
  {
    id: 'chg_2',
    quote_id: 'q_waiting',
    outcome: 'declined',
    amount: 0.2,
    currency: 'USD',
    payment_method: 'CREDIT_OR_DEBIT_CARD',
    payment_type: 'HUBSPOT',
    reason: 'amount_due_below_minimum',
    detail: 'The total amount due must be more than $0.50.',
    initiated_at: '2026-01-06T09:07:00Z',
    settled_at: null,
  },
]

const INVOICES = [
  {
    id: 'inv_1',
    quote_id: 'q_paid',
    number: 'INV-0001',
    kind: 'first',
    status: 'sent',
    amount: 1500,
    currency: 'USD',
    invoice_date: '2026-01-06',
    sent_at: '2026-01-06T09:00:00Z',
    billing_frequency: 'one_time',
  },
  {
    id: 'inv_2',
    quote_id: 'q_paid',
    number: 'INV-0002',
    kind: 'scheduled',
    status: 'scheduled',
    amount: 1500,
    currency: 'USD',
    invoice_date: '2026-02-06',
    send_on: '2026-01-27',
    billing_frequency: 'monthly',
  },
]

function view(overrides = {}) {
  return {
    setup: SETUP,
    quote: quote(),
    charges: [CHARGES[0]],
    invoices: INVOICES,
    subscriptions: [{ id: 'sub_1', quote_id: 'q_paid', status: 'active' }],
    tax_ids: [
      { id: 'tx_1', quote_id: 'q_paid', value: 'US-001' },
      { id: 'tx_2', quote_id: 'q_paid', value: 'US-002' },
    ],
    activity: [
      { id: 'act_1', activity: 'quote_accepted', label: 'Quote accepted', at: '2026-01-06T09:00:00Z' },
    ],
    ...overrides,
  }
}

function jsonResponse(body, status = 200) {
  return {
    ok: status < 400,
    status,
    json: async () => body,
  }
}

/**
 * A `fetch` stub keyed by a substring of the path.
 *
 * The longest matching pattern wins, not the first. `/wf-096/quotes` is a substring of
 * `/wf-096/quotes/q_paid` and of `/wf-096/quotes/q_waiting/void`, so a first-match stub would
 * answer every per-quote request with the board's list and the page would render a list where a
 * quote belongs. That is not a stub detail: it is the same collision a real router avoids by
 * longest-prefix match.
 *
 * A `function` value is called per request, so a test can hand back one body first and another
 * after a write, which is how the board's own reload is exercised. `{ __status, body }` answers
 * with that status, for a refusal.
 */
function stubFetch(routes) {
  const calls = []
  const patterns = Object.keys(routes).sort((a, b) => b.length - a.length)
  const handler = async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET', body: options.body })
    for (const pattern of patterns) {
      if (url.includes(pattern)) {
        const raw = routes[pattern]
        const value = typeof raw === 'function' ? await raw() : raw
        if (value && value.__status) return jsonResponse(value.body, value.__status)
        return jsonResponse(value)
      }
    }
    return jsonResponse({ detail: `no stub for ${url}` }, 404)
  }
  global.fetch = vi.fn(handler)
  return calls
}

/** The four reads the page issues on mount, and nothing else. */
function baseRoutes(overrides = {}) {
  return {
    '/wf-096/summary': SUMMARY,
    '/wf-096/quotes': {
      count: 2,
      quotes: [
        view(),
        // Published for payments but not yet accepted, which is the state the accept
        // control exists for.
        view({
          quote: awaitingQuote(),
          setup: setupFor('q_waiting'),
          charges: [],
          invoices: [],
          tax_ids: [],
        }),
      ],
    },
    '/wf-096/vocabulary': VOCABULARY,
    '/wf-096/decisions': DECISIONS,
    ...overrides,
  }
}

/**
 * A board whose one quote was never published for payments, which is the only state the
 * publish control exists for.
 *
 * Kept separate from {@link baseRoutes} rather than folded into it, because a quote with no
 * setup and a quote awaiting acceptance are different states and a test that stubs both at once
 * cannot say which one it is exercising.
 */
function unpublishedRoutes(overrides = {}) {
  const draft = quote({
    id: 'q_draft',
    title: 'Halcyon part-day engagement',
    accepted: false,
    acceptance: null,
    status: 'DRAFT',
    hs_status: '',
    amount_due: { subtotal: 900, discount: 0, tax: 0, total: 900, line_count: 1 },
    line_items: [{ id: 'line_3', name: 'Consulting days', quantity: 1, unit_price: 900 }],
  })
  return {
    '/wf-096/summary': { ...SUMMARY, quotes: 1, setups: 1, accepted: 0, awaiting_acceptance: 1, pending_payment: 0 },
    '/wf-096/quotes': {
      count: 1,
      quotes: [
        view({
          quote: draft,
          setup: null,
          charges: [],
          invoices: [],
          subscriptions: [],
          tax_ids: [],
          activity: [],
        }),
      ],
    },
    '/wf-096/vocabulary': VOCABULARY,
    '/wf-096/decisions': DECISIONS,
    ...overrides,
  }
}

beforeEach(() => {
  vi.restoreAllMocks()
})

// --------------------------------------------------------------------------- //
// The descriptor
// --------------------------------------------------------------------------- //

describe('the descriptor', () => {
  it('exports the four fields the host discovers', () => {
    expect(descriptor.id).toBe('wf-096-accept-a-quote-without-a-signature-and-take')
    expect(descriptor.label).toBe('Accept and pay')
    expect(descriptor.icon).toBeTruthy()
    expect(descriptor.Component).toBe(AcceptAndPayPage)
  })

  it('names an icon rather than an emoji, because the floor forbids emoji as icons', () => {
    // A short alphabetic token is a name the Icon component resolves; an emoji is not.
    expect(descriptor.icon).toMatch(/^[a-z][a-z0-9-]*$/)
  })
})

// --------------------------------------------------------------------------- //
// The helpers, which the page's states are read through
// --------------------------------------------------------------------------- //

describe('the acceptance-to-payment rail', () => {
  it('has three steps in the order the research puts them', () => {
    expect(RAIL.map((step) => step.key)).toEqual(['published', 'accepted', 'paid'])
    expect(RAIL.map((step) => step.label)).toEqual([
      'Published for payments',
      'Accepted without signature',
      'Payment taken',
    ])
  })

  it('reads a quote with no setup as not on the rail at all', () => {
    expect(railStep({ setup: null })).toBe(-1)
    expect(railStep(null)).toBe(-1)
  })

  it('stops at published, then accepted, then paid', () => {
    const published = { setup: SETUP, quote: { accepted: false }, charges: [] }
    const accepted = { setup: SETUP, quote: { accepted: true }, charges: [] }
    const paid = { setup: SETUP, quote: { accepted: true }, charges: [CHARGES[0]] }
    const declined = { setup: SETUP, quote: { accepted: true }, charges: [CHARGES[1]] }

    expect(railStep(published)).toBe(0)
    expect(railStep(accepted)).toBe(1)
    expect(railStep(paid)).toBe(2)
    // A declined charge is not payment, so the rail stays where the buyer actually is.
    expect(railStep(declined)).toBe(1)
  })
})

describe('the payment state', () => {
  it('names each state in words, so no state is carried by colour alone', () => {
    expect(paymentState({ setup: null })).toBe('Not published for payments')
    expect(paymentState({ setup: SETUP, quote: { accepted: false }, charges: [] })).toBe(
      'Awaiting acceptance',
    )
    expect(paymentState({ setup: SETUP, quote: { accepted: true }, charges: [] })).toBe(
      'Awaiting payment',
    )
    expect(paymentState({ setup: SETUP, quote: { accepted: true }, charges: [CHARGES[1]] })).toBe(
      'Payment declined',
    )
    expect(paymentState({ setup: SETUP, quote: { accepted: true }, charges: [CHARGES[0]] })).toBe(
      'Paid',
    )
  })

  it('gives every named state a tone, and an unknown state a neutral one', () => {
    for (const detail of [
      { setup: null },
      { setup: SETUP, quote: { accepted: false }, charges: [] },
      { setup: SETUP, quote: { accepted: true }, charges: [CHARGES[1]] },
      { setup: SETUP, quote: { accepted: true }, charges: [CHARGES[0]] },
    ]) {
      expect(paymentTone(detail)).toBeTruthy()
    }
    expect(paymentTone({ setup: SETUP, quote: { accepted: false }, charges: [] })).toBe('info')
    expect(paymentTone({ setup: SETUP, quote: { accepted: true }, charges: [CHARGES[0]] })).toBe(
      'success',
    )
    expect(paymentTone({ setup: SETUP, quote: { accepted: true }, charges: [CHARGES[1]] })).toBe(
      'danger',
    )
  })

  it('defaults an absent amount due, so a caller can always read the total', () => {
    expect(amountDue(null).total).toBe(0)
    expect(amountDue({}).line_count).toBe(0)
    expect(amountDue({ amount_due: { total: 1500 } }).total).toBe(1500)
  })

  it('pluralises, because a count beside a noun reads wrong at one', () => {
    expect(plural(1, 'invoice')).toBe('1 invoice')
    expect(plural(2, 'invoice')).toBe('2 invoices')
    expect(plural(2, 'subscription')).toBe('2 subscriptions')
  })
})

describe('the research numbers the page mirrors', () => {
  it('carries the strict minimum, the cap and the ten-day lead the research states', () => {
    expect(FACTS.minimumChargeUsd).toBe(0.5)
    expect(FACTS.taxIdLimit).toBe(3)
    expect(FACTS.invoiceLeadDays).toBe(10)
    expect(FACTS.anonymousBuyer).toBe('Buyer (no contact specified)')
  })

  it('offers exactly the five researched payment methods', () => {
    expect(FACTS.paymentMethods.map((method) => method.value)).toEqual([
      'CREDIT_OR_DEBIT_CARD',
      'ACH',
      'SEPA',
      'BACS',
      'PADS',
    ])
  })

  it('offers exactly the four researched effective-date modes', () => {
    expect(FACTS.effectiveDateModes.map((mode) => mode.value)).toEqual([
      'on_agreement',
      'custom_date',
      'delayed_days',
      'delayed_months',
    ])
  })
})

// --------------------------------------------------------------------------- //
// The primitives
// --------------------------------------------------------------------------- //

describe('the acceptance rail', () => {
  it('writes Done, Now and Waiting, so no step is marked by position or colour alone', () => {
    render(<AcceptanceRail detail={{ setup: SETUP, quote: { accepted: false }, charges: [] }} />)

    expect(screen.getByText('Now')).toBeDefined()
    expect(screen.getAllByText('Waiting')).toHaveLength(2)
    expect(screen.queryByText('Done')).toBeNull()
  })

  it('marks the current step with aria-current so assistive technology reads it', () => {
    const { container } = render(
      <AcceptanceRail detail={{ setup: SETUP, quote: { accepted: true }, charges: [CHARGES[0]] }} />,
    )
    const current = container.querySelector('[aria-current="step"]')
    expect(current).not.toBeNull()
    expect(current.textContent).toContain('Payment taken')
  })

  it('lights nothing for a quote that was never published, rather than guessing a step', () => {
    render(<AcceptanceRail detail={{ setup: null, quote: { accepted: false }, charges: [] }} />)
    expect(screen.queryByText('Now')).toBeNull()
    expect(screen.queryByText('Done')).toBeNull()
  })
})

describe('the payment badge', () => {
  it('always carries the state word', () => {
    for (const state of [
      'Paid',
      'Payment declined',
      'Awaiting payment',
      'Awaiting acceptance',
      'Not published for payments',
    ]) {
      const { unmount } = render(<PaymentBadge state={state} />)
      expect(screen.getByText(state)).toBeDefined()
      unmount()
    }
  })
})

describe('an invoice row', () => {
  it('marks the first invoice as sent, because the research says it goes immediately', () => {
    render(
      <ul>
        <InvoiceRow invoice={INVOICES[0]} />
      </ul>,
    )
    expect(screen.getByText('First invoice')).toBeDefined()
    expect(screen.getByText(/Sent /)).toBeDefined()
  })

  it('marks a later invoice as scheduled, and shows its ten-day-later send date', () => {
    render(
      <ul>
        <InvoiceRow invoice={INVOICES[1]} />
      </ul>,
    )
    expect(screen.getByText('Scheduled')).toBeDefined()
    // The invoice date is 2026-02-06 and the send date is ten days before it.
    expect(screen.getByText(/Sends /)).toBeDefined()
  })
})

describe('a charge row', () => {
  it('renders a recorded charge with its outcome in words', () => {
    render(
      <ul>
        <ChargeRow charge={CHARGES[0]} />
      </ul>,
    )
    expect(screen.getByText('Charge recorded')).toBeDefined()
    expect(screen.getByText('CREDIT_OR_DEBIT_CARD')).toBeDefined()
    expect(screen.getByText('HUBSPOT')).toBeDefined()
  })

  it('renders a declined charge as a declined outcome with its reason, not as an error', () => {
    render(
      <ul>
        <ChargeRow charge={CHARGES[1]} />
      </ul>,
    )
    expect(screen.getByText('Declined')).toBeDefined()
    expect(screen.getByText('The total amount due must be more than $0.50.')).toBeDefined()
    // A decline carries no settlement instant, so the row does not claim one.
    expect(screen.queryByText(/settled /)).toBeNull()
  })
})

describe('the rule panels', () => {
  it('states the strict minimum beside the number it governs', () => {
    render(<MinimumChargePanel summary={SUMMARY} evidence={MINIMUM_CHARGE_SENTENCE} />)
    expect(screen.getByText('$0.50')).toBeDefined()
    expect(screen.getByText(/recorded as declined, not refused/)).toBeDefined()
    expect(screen.getByText(MINIMUM_CHARGE_SENTENCE)).toBeDefined()
  })

  it('falls back to the research minimum when the server has not answered', () => {
    render(<MinimumChargePanel summary={{}} evidence="" />)
    expect(screen.getByText('$0.50')).toBeDefined()
  })

  it('states the three-tax-ID cap and says the fourth is refused', () => {
    render(<TaxIdPanel summary={SUMMARY} evidence={TAX_ID_SENTENCE} />)
    expect(screen.getByText(TAX_ID_SENTENCE)).toBeDefined()
    expect(screen.getByText(/The fourth is refused/)).toBeDefined()
  })

  it('reads an evidence sentence from the vocabulary, and nothing before it loads', () => {
    expect(evidenceFor(VOCABULARY, 'minimum_charge')).toBe(MINIMUM_CHARGE_SENTENCE)
    expect(evidenceFor(VOCABULARY, 'whole_number_quantity')).toBe(WHOLE_NUMBER_SENTENCE)
    expect(evidenceFor({}, 'minimum_charge')).toBe('')
    expect(evidenceFor(undefined, 'tax_id')).toBe('')
  })

  it('renders nothing for an empty sentence rather than an empty block', () => {
    const { container } = render(<EvidenceNote label="The rule" quote="" />)
    expect(container.textContent).not.toContain('The rule')
  })
})

// --------------------------------------------------------------------------- //
// The page
// --------------------------------------------------------------------------- //

describe('the page', () => {
  it('shows a loading state rather than an empty board, so a slow API never reads as "nothing here"', async () => {
    stubFetch({
      '/wf-096/summary': new Promise(() => {}),
      '/wf-096/quotes': new Promise(() => {}),
    })

    render(<AcceptAndPayPage />)
    expect(await screen.findByText(/Loading quote payments/)).toBeDefined()
  })

  it('reports a failed read instead of rendering an empty board', async () => {
    stubFetch({
      '/wf-096/summary': { __status: 500, body: { detail: 'the payments table is unavailable' } },
      '/wf-096/quotes': { __status: 500, body: { detail: 'the payments table is unavailable' } },
    })

    render(<AcceptAndPayPage />)
    expect(await screen.findByText(/the payments table is unavailable/)).toBeDefined()
  })

  it('shows the board numbers, both quotes, and the minimum and the cap', async () => {
    stubFetch(baseRoutes())

    render(<AcceptAndPayPage />)

    expect(await screen.findByText('Northwind platform renewal')).toBeDefined()
    expect(screen.getByText('Orbis sample order')).toBeDefined()
    expect(screen.getByText('$0.50')).toBeDefined()
    expect(screen.getByText(/The fourth is refused/)).toBeDefined()
  })

  it('says the strict-minimum rule and the whole-number rule in the researched words', async () => {
    stubFetch(baseRoutes())

    render(<AcceptAndPayPage />)

    expect(await screen.findByText(MINIMUM_CHARGE_SENTENCE)).toBeDefined()
    expect(screen.getByText(WHOLE_NUMBER_SENTENCE)).toBeDefined()
    expect(screen.getByText(/Print and sign isn't a valid acceptance method/)).toBeDefined()
  })

  it('states what this workflow does not own, including WF-095 and the Connected CPQ path', async () => {
    stubFetch(baseRoutes())

    render(<AcceptAndPayPage />)

    expect(await screen.findByText(/WF-095 collects acceptance by e-signature/)).toBeDefined()
    expect(screen.getByText(/WF-086 authors the quote/)).toBeDefined()
  })

  it('lists each decision with the alternative it rejected and its audit id', async () => {
    stubFetch(baseRoutes())

    render(<AcceptAndPayPage />)

    expect(await screen.findByText('How should a charge below the minimum be answered?')).toBeDefined()
    expect(screen.getByText('recorded_declined_outcome')).toBeDefined()
    expect(
      screen.getByText(/A processor decline is a definite result, not a malformed request\./),
    ).toBeDefined()
    expect(screen.getByText('jev-20261005T121102-5456-62628')).toBeDefined()
  })

  it('offers an empty state that says what to do, not a blank page', async () => {
    stubFetch(
      baseRoutes({
        '/wf-096/quotes': { count: 0, quotes: [] },
      }),
    )

    render(<AcceptAndPayPage />)

    expect(await screen.findByText('No quotes published for payments')).toBeDefined()
    expect(screen.getByText(/Create a demo quote and publish it/)).toBeDefined()
  })

  // -- acceptance and payment are two acts --------------------------------- //

  it('offers acceptance on a published quote, and never on an accepted one', async () => {
    stubFetch(baseRoutes())

    render(<AcceptAndPayPage />)
    await screen.findByText('Orbis sample order')

    // Exactly one quote is awaiting acceptance, so exactly one accept control exists.
    expect(screen.getAllByRole('button', { name: /Accept without a signature/i })).toHaveLength(1)
    // Neither quote is accepted-and-unpaid, so no card offers payment yet.
    expect(screen.queryByRole('button', { name: /Take payment/i })).toBeNull()
  })

  it('says an accepted quote cannot be voided or deleted, and offers neither control', async () => {
    stubFetch(baseRoutes())

    render(<AcceptAndPayPage />)
    await screen.findByText('Northwind platform renewal')

    expect(screen.getByText(/Accepted quotes cannot be voided or deleted\./)).toBeDefined()
    // The un-accepted quote still has both, so exactly one of each pair exists.
    expect(screen.getAllByRole('button', { name: /^Void$/i })).toHaveLength(1)
    expect(screen.getAllByRole('button', { name: /^Delete$/i })).toHaveLength(1)
  })

  it('keeps accept and pay as separate buttons, so a declined charge cannot undo an acceptance', async () => {
    const calls = stubFetch(
      baseRoutes({
        '/wf-096/quotes/q_paid/payment': {
          outcome: 'recorded',
          quote_id: 'q_paid',
          charge: CHARGES[0],
        },
        '/wf-096/quotes/q_paid': () => detailFor('q_paid', { charges: [] }),
      }),
    )

    render(<AcceptAndPayPage />)
    await screen.findByText('Northwind platform renewal')

    await userEvent.click(screen.getAllByRole('button', { name: /^Open$/i })[0])
    await userEvent.click(await screen.findByRole('button', { name: /Take payment/i }))

    const payDialog = await screen.findByRole('dialog', { name: /Take payment/i })
    // The submit button names the amount, so the buyer sees what is about to be charged.
    await userEvent.click(within(payDialog).getByRole('button', { name: /Charge \$1,500\.00/ }))

    expect(await screen.findByText('Charge recorded')).toBeDefined()

    // The write was to the payment route only. Nothing posted to /accept.
    const posted = calls.filter((call) => call.method === 'POST')
    expect(posted.some((call) => call.url.includes('/payment'))).toBe(true)
    expect(posted.some((call) => call.url.includes('/accept'))).toBe(false)
  })

  it('shows a declined charge as a declined outcome with its reason, not as an error', async () => {
    stubFetch(
      baseRoutes({
        '/wf-096/quotes/q_paid/payment': {
          outcome: 'declined',
          quote_id: 'q_paid',
          reason: 'amount_due_below_minimum',
          detail: 'The total amount due must be more than $0.50.',
        },
        '/wf-096/quotes/q_paid': () => detailFor('q_paid', { charges: [] }),
      }),
    )

    render(<AcceptAndPayPage />)
    await screen.findByText('Northwind platform renewal')

    await userEvent.click(screen.getAllByRole('button', { name: /^Open$/i })[0])
    await userEvent.click(await screen.findByRole('button', { name: /Take payment/i }))

    const payDialog = await screen.findByRole('dialog', { name: /Take payment/i })
    await userEvent.click(within(payDialog).getByRole('button', { name: /Charge \$1,500\.00/ }))

    expect(await screen.findByText('Charge declined')).toBeDefined()
    expect(screen.getByText(/reason: amount_due_below_minimum/)).toBeDefined()
    // And the acceptance is still there: a decline did not undo it.
    expect(screen.getByText(/Accepted quotes cannot be voided or deleted\./)).toBeDefined()
  })

  it('offers only the payment methods the quote was published with', async () => {
    stubFetch(
      baseRoutes({
        '/wf-096/quotes/q_paid': () => detailFor('q_paid', { charges: [] }),
      }),
    )

    render(<AcceptAndPayPage />)
    await screen.findByText('Northwind platform renewal')

    await userEvent.click(screen.getAllByRole('button', { name: /^Open$/i })[0])
    await userEvent.click(await screen.findByRole('button', { name: /Take payment/i }))

    const payDialog = await screen.findByRole('dialog', { name: /Take payment/i })
    // Exact, because "Store the payment method" also contains the phrase.
    const select = within(payDialog).getByLabelText('Payment method')
    // The setup allows CARD and ACH, so the other three are not offered.
    expect([...select.options].map((option) => option.value)).toEqual([
      'CREDIT_OR_DEBIT_CARD',
      'ACH',
    ])
  })

  it('states the minimum beside the charge, so a decline is never a surprise', async () => {
    stubFetch(
      baseRoutes({
        '/wf-096/quotes/q_paid': () => detailFor('q_paid', { charges: [] }),
      }),
    )

    render(<AcceptAndPayPage />)
    await screen.findByText('Northwind platform renewal')

    await userEvent.click(screen.getAllByRole('button', { name: /^Open$/i })[0])
    await userEvent.click(await screen.findByRole('button', { name: /Take payment/i }))

    const payDialog = await screen.findByRole('dialog', { name: /Take payment/i })
    expect(within(payDialog).getByText(/is recorded as/)).toBeDefined()
    expect(within(payDialog).getByText('amount_due_below_minimum')).toBeDefined()
  })

  // -- accepting without a contact ----------------------------------------- //

  it('accepts without a contact, because the research says one is not required', async () => {
    const calls = stubFetch(
      baseRoutes({
        '/wf-096/quotes/q_waiting/accept': {
          quote_id: 'q_waiting',
          acceptance: { accepted_by: FACTS.anonymousBuyer },
          invoices: [INVOICES[0]],
          subscriptions: [],
        },
        '/wf-096/quotes/q_waiting': () => detailFor('q_waiting', { quote: awaitingQuote() }, null),
      }),
    )

    render(<AcceptAndPayPage />)
    await screen.findByText('Orbis sample order')

    await userEvent.click(screen.getByRole('button', { name: /Accept without a signature/i }))
    const dialog = await screen.findByRole('dialog', { name: /Accept without a signature/i })

    // The form says a blank name is legitimate, rather than marking the field required.
    expect(within(dialog).getByText(/Leave blank to record/i)).toBeDefined()
    expect(within(dialog).getByLabelText(/Accepted by/i)).not.toBeRequired()
    await userEvent.click(within(dialog).getByRole('button', { name: /Accept the quote/i }))

    expect(await screen.findByText('Quote accepted without a signature')).toBeDefined()
    expect(screen.getByText(/Buyer \(no contact specified\)\. 1 invoice created\./)).toBeDefined()

    const acceptCall = calls.find((call) => call.method === 'POST' && call.url.includes('/accept'))
    expect(acceptCall).toBeTruthy()
    expect(JSON.parse(acceptCall.body).accepted_by).toBe('')
  })

  it('names who accepted, and counts the invoices the acceptance created', async () => {
    stubFetch(
      baseRoutes({
        '/wf-096/quotes/q_waiting/accept': {
          quote_id: 'q_waiting',
          acceptance: { accepted_by: 'Ada Byron' },
          invoices: [INVOICES[0], INVOICES[1]],
          subscriptions: [{ id: 'sub_1' }],
        },
        '/wf-096/quotes/q_waiting': () => detailFor('q_waiting', { quote: awaitingQuote() }, null),
      }),
    )

    render(<AcceptAndPayPage />)
    await screen.findByText('Orbis sample order')

    await userEvent.click(screen.getByRole('button', { name: /Accept without a signature/i }))
    const dialog = await screen.findByRole('dialog', { name: /Accept without a signature/i })
    await userEvent.type(within(dialog).getByLabelText(/Accepted by/i), 'Ada Byron')
    await userEvent.click(within(dialog).getByRole('button', { name: /Accept the quote/i }))

    expect(await screen.findByText('Quote accepted without a signature')).toBeDefined()
    expect(screen.getByText(/Ada Byron\. 2 invoices created, 1 subscription\./)).toBeDefined()
  })

  // -- publishing ----------------------------------------------------------- //

  it('defaults the publish dialog to Accept without signature, this workflow own method', async () => {
    stubFetch(unpublishedRoutes())

    render(<AcceptAndPayPage />)
    await screen.findByText('Halcyon part-day engagement')

    await userEvent.click(screen.getByRole('button', { name: /Publish for payments/i }))
    const dialog = await screen.findByRole('dialog', { name: /Publish for online payments/i })

    expect(within(dialog).getByLabelText(/Acceptance method/i)).toHaveValue('clickwrap')
    // It says where e-signature belongs, so a seller is not left to guess.
    expect(within(dialog).getByText(/belongs to WF-095/)).toBeDefined()
    // And it states the researched rejection for Print and sign.
    expect(within(dialog).getByText(/never with online payments/i)).toBeDefined()
  })

  it('sends the researched publish payload, and only the fields the chosen mode needs', async () => {
    const calls = stubFetch(
      unpublishedRoutes({
        '/wf-096/quotes/q_draft/publish': { quote_id: 'q_draft', setup: SETUP },
      }),
    )

    render(<AcceptAndPayPage />)
    await screen.findByText('Halcyon part-day engagement')

    await userEvent.click(screen.getByRole('button', { name: /Publish for payments/i }))
    const dialog = await screen.findByRole('dialog', { name: /Publish for online payments/i })

    // On agreement needs no date argument.
    await userEvent.click(within(dialog).getByRole('button', { name: /Publish for payments/i }))

    await waitFor(() => {
      const call = calls.find((entry) => entry.method === 'POST' && entry.url.includes('/publish'))
      expect(call).toBeTruthy()
      const payload = JSON.parse(call.body)
      expect(payload.acceptance_method).toBe('clickwrap')
      expect(payload.allowed_payment_methods).toEqual(['CREDIT_OR_DEBIT_CARD'])
      expect(payload.effective_date_mode).toBe('on_agreement')
      expect(payload).not.toHaveProperty('effective_date')
      expect(payload).not.toHaveProperty('effective_delay_days')
      expect(payload).not.toHaveProperty('effective_delay_months')
    })
  })

  it('adds a payment method when its box is ticked', async () => {
    const calls = stubFetch(
      unpublishedRoutes({
        '/wf-096/quotes/q_draft/publish': { quote_id: 'q_draft', setup: SETUP },
      }),
    )

    render(<AcceptAndPayPage />)
    await screen.findByText('Halcyon part-day engagement')

    await userEvent.click(screen.getByRole('button', { name: /Publish for payments/i }))
    const dialog = await screen.findByRole('dialog', { name: /Publish for online payments/i })

    await userEvent.click(within(dialog).getByRole('checkbox', { name: /^ACH$/ }))
    await userEvent.click(within(dialog).getByRole('button', { name: /Publish for payments/i }))

    await waitFor(() => {
      const call = calls.find((entry) => entry.method === 'POST' && entry.url.includes('/publish'))
      expect(JSON.parse(call.body).allowed_payment_methods).toEqual([
        'CREDIT_OR_DEBIT_CARD',
        'ACH',
      ])
    })
  })

  it('sends the delay a delayed start names, in the unit the mode asks for', async () => {
    const calls = stubFetch(
      unpublishedRoutes({
        '/wf-096/quotes/q_draft/publish': { quote_id: 'q_draft', setup: SETUP },
      }),
    )

    render(<AcceptAndPayPage />)
    await screen.findByText('Halcyon part-day engagement')

    await userEvent.click(screen.getByRole('button', { name: /Publish for payments/i }))
    const dialog = await screen.findByRole('dialog', { name: /Publish for online payments/i })

    await userEvent.selectOptions(within(dialog).getByLabelText(/Effective date/i), 'delayed_months')
    const delay = await within(dialog).findByLabelText(/Delay \(months\)/i)
    await userEvent.clear(delay)
    await userEvent.type(delay, '3')
    await userEvent.click(within(dialog).getByRole('button', { name: /Publish for payments/i }))

    await waitFor(() => {
      const call = calls.find((entry) => entry.method === 'POST' && entry.url.includes('/publish'))
      const payload = JSON.parse(call.body)
      expect(payload.effective_date_mode).toBe('delayed_months')
      expect(payload.effective_delay_months).toBe(3)
      expect(payload).not.toHaveProperty('effective_delay_days')
    })
  })

  it('shows the refusal beside the publish form, with its reason code', async () => {
    stubFetch(
      unpublishedRoutes({
        '/wf-096/quotes/q_draft/publish': {
          __status: 400,
          body: {
            error: 'fractional_quantity_with_billing',
            reason: 'fractional_quantity_with_billing',
            detail: 'A line item has a fractional quantity while billing is on.',
            errors: { quantity: 'must be a whole number' },
          },
        },
      }),
    )

    render(<AcceptAndPayPage />)
    await screen.findByText('Halcyon part-day engagement')

    await userEvent.click(screen.getByRole('button', { name: /Publish for payments/i }))
    const dialog = await screen.findByRole('dialog', { name: /Publish for online payments/i })
    await userEvent.click(within(dialog).getByRole('button', { name: /Publish for payments/i }))

    expect(
      await within(dialog).findByText('A line item has a fractional quantity while billing is on.'),
    ).toBeDefined()
    // The field-keyed message lands next to its input rather than only in a banner.
    expect(within(dialog).getByText(/must be a whole number/)).toBeDefined()
  })

  it('closes the publish dialog on Escape, so a keyboard user is never trapped', async () => {
    stubFetch(unpublishedRoutes())

    render(<AcceptAndPayPage />)
    await screen.findByText('Halcyon part-day engagement')

    // Focus is still on the button that opened the dialog, which is the case a dialog-level
    // keydown handler misses and a document-level one catches.
    await userEvent.click(screen.getByRole('button', { name: /Publish for payments/i }))
    expect(await screen.findByRole('dialog')).toBeDefined()

    await userEvent.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })

  it('closes the publish dialog on the backdrop, which is a button for that reason', async () => {
    stubFetch(unpublishedRoutes())

    render(<AcceptAndPayPage />)
    await screen.findByText('Halcyon part-day engagement')

    await userEvent.click(screen.getByRole('button', { name: /Publish for payments/i }))
    expect(await screen.findByRole('dialog')).toBeDefined()

    await userEvent.click(screen.getByRole('button', { name: 'Close the dialog' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })

  // -- tax IDs -------------------------------------------------------------- //

  it('reports the tax-ID count against the researched cap', async () => {
    stubFetch(
      baseRoutes({
        '/wf-096/quotes/q_paid': () => detailFor('q_paid', { tax_ids: [1, 2].map(() => ({ value: 'US-001' })) }),
      }),
    )

    render(<AcceptAndPayPage />)
    await screen.findByText('Northwind platform renewal')

    await userEvent.click(screen.getAllByRole('button', { name: /^Open$/i })[0])
    await userEvent.click(await screen.findByRole('button', { name: /Add tax ID/i }))

    const taxDialog = await screen.findByRole('dialog', { name: /Add a buyer tax ID/i })
    expect(within(taxDialog).getByText(/2 of 3 recorded/)).toBeDefined()
  })

  it('shows a fourth tax ID refused with its published reason code', async () => {
    stubFetch(
      baseRoutes({
        '/wf-096/quotes/q_paid/tax-ids': {
          __status: 400,
          body: {
            error: 'tax_id_limit_reached',
            reason: 'tax_id_limit_reached',
            detail: 'A quote carries at most three buyer tax IDs.',
          },
        },
        '/wf-096/quotes/q_paid': () => detailFor('q_paid', { tax_ids: [{ value: 'US-001' }] }),
      }),
    )

    render(<AcceptAndPayPage />)
    await screen.findByText('Northwind platform renewal')

    await userEvent.click(screen.getAllByRole('button', { name: /^Open$/i })[0])
    await userEvent.click(await screen.findByRole('button', { name: /Add tax ID/i }))

    const taxDialog = await screen.findByRole('dialog', { name: /Add a buyer tax ID/i })
    await userEvent.type(within(taxDialog).getByLabelText(/Tax ID/i), 'US-004')
    await userEvent.click(within(taxDialog).getByRole('button', { name: /Add tax ID/i }))

    expect(await within(taxDialog).findByText(/at most three buyer tax IDs/)).toBeDefined()
  })

  // -- void and delete ------------------------------------------------------ //

  it('reports a void the server refuses, with its reason, rather than hiding it', async () => {
    stubFetch(
      baseRoutes({
        '/wf-096/quotes/q_waiting/void': {
          __status: 409,
          body: {
            error: 'quote_is_irreversible_after_acceptance',
            reason: 'quote_is_irreversible_after_acceptance',
            detail: 'A quote cannot be voided after it has been accepted.',
          },
        },
      }),
    )

    render(<AcceptAndPayPage />)
    await screen.findByText('Orbis sample order')

    await userEvent.click(screen.getByRole('button', { name: /^Void$/i }))

    expect(await screen.findByText('Could not void the quote')).toBeDefined()
    expect(screen.getByText(/A quote cannot be voided after it has been accepted\./)).toBeDefined()
    expect(screen.getByText(/reason: quote_is_irreversible_after_acceptance/)).toBeDefined()
  })

  it('dismisses the status line on request, so the board can be read without a stale banner', async () => {
    stubFetch(
      baseRoutes({
        '/wf-096/quotes/q_waiting/void': {
          __status: 409,
          body: {
            error: 'quote_is_irreversible_after_acceptance',
            reason: 'quote_is_irreversible_after_acceptance',
            detail: 'A quote cannot be voided after it has been accepted.',
          },
        },
      }),
    )

    render(<AcceptAndPayPage />)
    await screen.findByText('Orbis sample order')

    await userEvent.click(screen.getByRole('button', { name: /^Void$/i }))
    await screen.findByText('Could not void the quote')

    await userEvent.click(screen.getByRole('button', { name: /Dismiss/i }))
    await waitFor(() => expect(screen.queryByText('Could not void the quote')).toBeNull())
  })
})

/** One quote's detail, the shape `/quotes/{id}` answers with. */
function detailFor(quoteId, overrides = {}, setup = SETUP) {
  const theQuote = overrides.quote || (quoteId === 'q_paid' ? quote() : awaitingQuote())
  return {
    quote: theQuote,
    setup,
    charges: overrides.charges || [],
    invoices: overrides.invoices || INVOICES,
    subscriptions: [{ id: 'sub_1', quote_id: quoteId, status: 'active' }],
    tax_ids: overrides.tax_ids || [{ id: 'tx_1', quote_id: quoteId, value: 'US-001' }],
    activity: [],
  }
}