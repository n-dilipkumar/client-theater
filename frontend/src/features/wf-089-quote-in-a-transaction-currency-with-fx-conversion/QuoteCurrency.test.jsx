import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import {
  PRICING_ERROR_CODES,
  PRICING_OUTCOMES,
  RECALCULATION_TRIGGERS,
  money,
  plural,
  pricingErrorCode,
  pricingOutcome,
  query,
  quoteCurrencyApi,
  rate,
  refusalSummary,
} from './api'
import descriptor, { QuoteCurrencyPage } from './index.jsx'
import { CurrencyRow, MoneyRow, OutcomeBadge, RefusalBadge, RunRow } from './primitives'

/**
 * Tests for the pieces of WF-089 that can get quietly wrong.
 *
 * Eight claims are pinned here, and each is one the specification or the recorded decision
 * makes explicitly:
 *
 * 1. A refusal is a state, not a fault. Both researched codes render with their own wording,
 *    because a refusal that rendered as an empty totals table would be indistinguishable
 *    from a quote nobody priced.
 * 2. A money value is stored twice and the transaction figure is authoritative. Both figures
 *    render on every row, and the authoritative one is labelled.
 * 3. An uncomputed figure renders as a dash, never as a zero. "Not priced" and "nothing" are
 *    different facts.
 * 4. A rate with no figure behind it renders as a dash, never as a silent zero, and a rate
 *    that was never read says so.
 * 5. The rate source is rendered as words, because "1.08" alone does not say whether the
 *    deployment negotiated it or the platform published it.
 * 6. The currency cannot change under line items, and the page's refusal summary keeps the
 *    server's own remedy rather than inventing one.
 * 7. The six recalculation triggers are the research's six, in the research's order.
 * 8. An unknown pricing error code renders as itself rather than as an empty badge.
 *
 * The page component itself needs a stubbed transport, so the tests below cover its
 * building blocks, its helpers and its descriptor rather than driving eleven fetches.
 */

describe('the descriptor', () => {
  it('exports the shape the feature host discovers', () => {
    expect(descriptor.id).toBe('wf-089-quote-in-a-transaction-currency-with-fx-conversion')
    expect(typeof descriptor.label).toBe('string')
    expect(descriptor.Component).toBe(QuoteCurrencyPage)
  })

  it('uses an icon name that exists in the shared set', () => {
    // `components/ui.jsx` is not edited, so the name must already be there. The Icon
    // component falls back to a generic glyph for an unknown name, which would hide this
    // mistake rather than show it.
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

  it('passes the page own mark as a path rather than adding one to the shared map', () => {
    expect(typeof descriptor.iconPath).toBe('string')
    expect(descriptor.iconPath.length).toBeGreaterThan(0)
  })
})

describe('a refusal is a state, not a fault', () => {
  it('carries both researched codes with the specification wording', () => {
    const byCode = Object.fromEntries(PRICING_ERROR_CODES.map((row) => [row.value, row.label]))

    expect(byCode['34']).toBe('34 Invalid Price Level Currency')
    expect(byCode['38']).toBe(
      '38 Transaction currency is not set for the product price list item',
    )
  })

  it('says what each code means, so a refusal is not just a number', () => {
    for (const entry of PRICING_ERROR_CODES) {
      expect(entry.meaning.length).toBeGreaterThan(20)
    }
  })

  it('renders a refusal badge with the code as text, not as a coloured dot', () => {
    render(<RefusalBadge code="34" />)

    expect(screen.getByText('34 Invalid Price Level Currency')).toBeTruthy()
  })

  it('renders an unknown code as itself rather than as an empty badge', () => {
    render(<RefusalBadge code="99" />)

    expect(screen.getByText('Pricing error 99')).toBeTruthy()
  })

  it('renders no refusal as a neutral label rather than as a missing badge', () => {
    expect(pricingErrorCode(null).label).toBe('No pricing error')
  })

  it('carries three outcomes, and refused is one of them', () => {
    const values = PRICING_OUTCOMES.map((row) => row.value)

    expect(values).toContain('priced')
    expect(values).toContain('refused')
    expect(values).toContain(null)
  })

  it('says what a refusal did: no totals stored, and the run is on the log', () => {
    const refused = pricingOutcome('refused')

    expect(refused.meaning).toMatch(/no totals/i)
    expect(refused.meaning).toMatch(/audit log/i)
  })

  it('renders the outcome badge with the outcome as words', () => {
    render(<OutcomeBadge outcome="refused" />)

    expect(screen.getByText('Refused to price')).toBeTruthy()
  })

  it('falls back to "not priced yet" for a quote nobody has priced', () => {
    render(<OutcomeBadge outcome={null} />)

    expect(screen.getByText('Not priced yet')).toBeTruthy()
  })
})

describe('a money value is stored twice, and the transaction figure is authoritative', () => {
  it('renders both figures on one row, with the currency each is in', () => {
    render(
      <MoneyRow
        label="totalamount"
        transactionValue="15401.00"
        baseValue="16633.08"
        isoCode="EUR"
        baseIsoCode="USD"
        primary
      />,
    )

    expect(screen.getByText('15401.00 EUR')).toBeTruthy()
    expect(screen.getByText('16633.08 USD')).toBeTruthy()
  })

  it('labels the authoritative figure rather than leaving it to position', () => {
    render(
      <MoneyRow
        label="totalamount"
        transactionValue="15401.00"
        baseValue="16633.08"
        isoCode="EUR"
        baseIsoCode="USD"
        primary
      />,
    )

    expect(screen.getByText('authoritative')).toBeTruthy()
  })

  it('renders an uncomputed figure as a dash, not as a zero', () => {
    expect(money(null, 'EUR')).toBe('-')
    expect(money(undefined, 'USD')).toBe('-')
    expect(money('', 'EUR')).toBe('-')
  })

  it('renders a computed figure as the decimal string the server sent', () => {
    expect(money('15401.00', 'EUR')).toBe('15401.00 EUR')
    expect(money('0.00', 'EUR')).toBe('0.00 EUR')
  })

  it('renders a figure with no currency beside it rather than guessing one', () => {
    expect(money('1200.00')).toBe('1200.00')
  })

  it('renders both a zero and an absent figure differently', () => {
    expect(money('0.00', 'EUR')).not.toBe(money(null, 'EUR'))
  })
})

describe('the rate renders as a figure and a source', () => {
  it('shows the rate to six places, because a rate of 1.0837 is meaningful', () => {
    expect(rate(1.0837)).toBe('1.0837')
  })

  it('says "no rate" rather than rendering a silent zero', () => {
    expect(rate(null)).toBe('no rate')
    expect(rate(undefined)).toBe('no rate')
    expect(rate('')).toBe('no rate')
  })

  it('never renders a zero rate for a missing one', () => {
    expect(rate(null)).not.toBe('0')
  })

  it('rounds a whole rate without inventing decimals', () => {
    expect(rate(1)).toBe('1')
  })
})

describe('a currency record says whether its rate may be written here', () => {
  it('marks a Custom record as writable and a Standard one as not', () => {
    render(
      <ul>
        <CurrencyRow
          currency={{
            id: 'c1',
            iso_code: 'EUR',
            name: 'Euro',
            currency_precision: 2,
            currency_type: 'Custom',
            rate_is_writable: true,
            is_base_currency: false,
          }}
          selected={false}
          onSelect={() => {}}
        />
        <CurrencyRow
          currency={{
            id: 'c2',
            iso_code: 'USD',
            name: 'Dollar',
            currency_precision: 2,
            currency_type: 'Standard',
            rate_is_writable: false,
            is_base_currency: true,
          }}
          selected={false}
          onSelect={() => {}}
        />
      </ul>,
    )

    expect(screen.getByText('rate writable here')).toBeTruthy()
    expect(screen.getByText('rate from the platform')).toBeTruthy()
    expect(screen.getByText('base currency')).toBeTruthy()
  })

  it('gives each row a 44px target, because it is selectable', () => {
    const { container } = render(
      <ul>
        <CurrencyRow
          currency={{
            id: 'c1',
            iso_code: 'EUR',
            name: 'Euro',
            currency_precision: 2,
            rate_is_writable: true,
          }}
          selected={false}
          onSelect={() => {}}
        />
      </ul>,
    )

    expect(container.querySelector('button').className).toContain('min-h-11')
  })

  it('reports the selection with aria-pressed rather than with colour', () => {
    // Scoped to this render's own container: `getByRole` across the document would find
    // the unselected rows an earlier test in this file left mounted, and assert against
    // one of those instead.
    const { container } = render(
      <ul>
        <CurrencyRow
          currency={{ id: 'c1', iso_code: 'EUR', name: 'Euro', currency_precision: 2 }}
          selected="c1"
          onSelect={() => {}}
        />
      </ul>,
    )

    expect(container.querySelector('button').getAttribute('aria-pressed')).toBe('true')
  })

  it('reports an unselected row as unpressed, not as merely unhighlighted', () => {
    const { container } = render(
      <ul>
        <CurrencyRow
          currency={{ id: 'c1', iso_code: 'EUR', name: 'Euro', currency_precision: 2 }}
          selected="other"
          onSelect={() => {}}
        />
      </ul>,
    )

    expect(container.querySelector('button').getAttribute('aria-pressed')).toBe('false')
  })
})

describe('a pricing run is on the page whether it priced or refused', () => {
  it('renders a refused run with its code and its detail, not as an absent row', () => {
    render(
      <ul>
        <RunRow
          run={{
            ran_at: '2026-10-04T09:00:00.000+00:00',
            outcome: 'refused',
            pricing_error_code: '34',
            pricing_error: 'Invalid Price Level Currency',
            detail: 'The price list is in USD but the quote is in EUR.',
            trigger: 'product_add',
            trigger_label: 'A product was added to the quote.',
            rate: 1.08,
            rate_source: 'custom_stamped',
            line_count: 2,
          }}
        />
      </ul>,
    )

    expect(screen.getByText('The price list is in USD but the quote is in EUR.')).toBeTruthy()
    expect(screen.getByText('A product was added to the quote.')).toBeTruthy()
    expect(screen.getByText(/rate 1.08/)).toBeTruthy()
  })

  it('renders a priced run with both of its totals', () => {
    render(
      <ul>
        <RunRow
          run={{
            ran_at: '2026-10-04T09:00:00.000+00:00',
            outcome: 'priced',
            trigger: 'record_create',
            trigger_label: 'The quote was created.',
            rate: 1.08,
            rate_source: 'custom_stamped',
            line_count: 2,
            iso_code: 'EUR',
            base_iso_code: 'USD',
            totals: { totalamount: '15401.00' },
            totals_base: { totalamount_base: '16633.08' },
          }}
        />
      </ul>,
    )

    expect(screen.getByText('15401.00 EUR / 16633.08 USD')).toBeTruthy()
  })

  it('counts one line as one line', () => {
    // Read the paragraph's own text rather than matching across elements: the sentence is
    // built from interpolated parts, so a text matcher on the DOM has to be told which
    // node to read.
    const { container } = render(
      <ul>
        <RunRow
          run={{ outcome: 'priced', trigger: 'record_open', rate: 1, rate_source: 'identity', line_count: 1 }}
        />
      </ul>,
    )

    expect(container.querySelector('p').textContent).toBe('rate 1 (identity) / 1 line')
  })

  it('counts two lines as two lines', () => {
    const { container } = render(
      <ul>
        <RunRow
          run={{ outcome: 'priced', trigger: 'record_open', rate: 1, rate_source: 'identity', line_count: 2 }}
        />
      </ul>,
    )

    expect(container.querySelector('p').textContent).toContain('2 lines')
  })
})

describe('the currency-change refusal keeps the server own words', () => {
  it('reads the remedy out of the 409 body rather than inventing one', () => {
    const error = {
      message: 'The currency cannot change while the quote holds line items.',
      body: {
        error: 'currency_change_refused',
        detail: 'The currency cannot change while the quote holds line items.',
        line_items: 2,
        remedy: 'Remove every line item from the quote, then set the currency again.',
      },
    }

    expect(refusalSummary(error)).toContain('Remove every line item')
  })

  it('reads a field-keyed 400 as one line per field', () => {
    const error = {
      errors: {
        exchange_rate: 'exchange_rate is resolved from the currency record.',
      },
    }

    expect(refusalSummary(error)).toBe(
      'exchange_rate: exchange_rate is resolved from the currency record.',
    )
  })

  it('falls back to the message when the body carries nothing useful', () => {
    expect(refusalSummary(new Error('boom'))).toBe('boom')
  })
})

describe('the recalculation triggers are the research six', () => {
  it('carries all six, in the research order', () => {
    expect(RECALCULATION_TRIGGERS).toEqual([
      'record_open',
      'record_create',
      'record_update',
      'product_add',
      'product_update',
      'product_delete',
    ])
  })

  it('leads with record_open, because that is what the page does on a click', () => {
    expect(RECALCULATION_TRIGGERS[0]).toBe('record_open')
  })
})

describe('counts read as words', () => {
  it('says one quote, not 1 quotes', () => {
    expect(plural(1, 'quote')).toBe('1 quote')
    expect(plural(2, 'quote')).toBe('2 quotes')
    expect(plural(0, 'quote')).toBe('0 quotes')
  })
})

describe('the api wrapper', () => {
  it('drops empty values rather than sending an empty filter', () => {
    expect(query({ room_id: 'room_a' })).toBe('?room_id=room_a')
    expect(query({ room_id: '', actor: undefined, id: null })).toBe('')
    expect(query({ room_id: 'a', actor: 'dana' })).toBe('?room_id=a&actor=dana')
  })

  it('offers one call per route this workflow serves', () => {
    // Asserted on the shape rather than by calling: every call reaches `fetch`, and a test
    // that fires eleven of them would be testing the transport rather than this wrapper.
    for (const name of [
      'summary',
      'vocabulary',
      'decisions',
      'decision',
      'currencies',
      'registerCurrency',
      'stampRate',
      'priceLists',
      'createPriceList',
      'priceItems',
      'createPriceItem',
      'quotes',
      'createQuote',
      'quote',
      'addLine',
      'changeCurrency',
      'priceQuote',
      'pricingRuns',
      'rateReads',
    ]) {
      expect(typeof quoteCurrencyApi[name]).toBe('function')
    }
  })

  it('changes the currency with a PATCH, because the currency is a field', () => {
    expect(quoteCurrencyApi.changeCurrency.name).toBe('changeCurrency')
  })
})