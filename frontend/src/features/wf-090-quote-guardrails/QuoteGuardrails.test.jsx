/**
 * The quote guardrails page, tested against real API payloads.
 *
 * These tests are for the places where a mistake would be believable rather than
 * visibly broken, which is most of a rules page, because almost every rule in the
 * specification is about what does NOT happen:
 *
 * a rule whose property the quote lacks rendered as a block, when the third state is the
 * whole reason the feature is trustworthy;
 * a blocked publish rendered as a crash, when it is the researched gate working;
 * an unreadable definition failing silently on save, when the parser's reason is the only
 * actionable thing on screen;
 * the five quoted examples not offered, so an author has to memorise a DSL.
 *
 * Every assertion is about something the evidence fixes, not about markup.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import descriptor from './index'
import QuoteGuardrails from './QuoteGuardrails'
import { guardrailApi } from './api'

const SOLD_TOGETHER =
  'SOLD_TOGETHER FROM line_item WHERE [hs_product_id] IN ("ENT-LICENSE", "SUPPORT-ADDON", "TRAINING")'
const DEEP_DISCOUNT = 'SUM([discount]) FROM line_item > 60'

const VOCABULARY = {
  rule: {
    fields: ['name', 'rule_definition', 'outcome', 'message', 'status'],
    outcomes: [
      { outcome: 'show_warning', label: 'Show warning', sentence: 'Warn.' },
      { outcome: 'block_publish', label: 'Block publish', sentence: 'Block.' },
    ],
    statuses: ['enabled', 'disabled'],
  },
  grammar: {
    scopes: [{ scope: 'quote', cardinality: 'one' }],
    aggregates: ['SUM', 'MIN', 'MAX', 'AVG', 'COUNT'],
    quantifiers: ['SOLD_TOGETHER', 'INCOMPATIBLE'],
    examples: [SOLD_TOGETHER, DEEP_DISCOUNT],
  },
  verdicts: ['violation', 'clear', 'unverifiable', 'skipped'],
  reason_codes: [{ code: 'guardrail_block_publish', text: 'Stopped.' }],
  limitations: [
    { code: 'guardrail_quote_discount_unavailable', text: 'quote-level discount properties are not available' },
    { code: 'guardrail_arithmetic_unsupported', text: 'Arithmetic inside aggregate functions is not supported.' },
  ],
}

const RULES = {
  count: 1,
  rules: [
    {
      id: 'wf090_quote_rule_1',
      data: {
        name: 'Deep line discount',
        rule_definition: DEEP_DISCOUNT,
        outcome: 'block_publish',
        message: 'The total line discount is above 60 percent.',
        status: 'enabled',
        enabled: true,
      },
    },
  ],
}

const BLOCKING_VERDICT = {
  rule_id: 'wf090_quote_rule_1',
  rule_name: 'Deep line discount',
  outcome: 'block_publish',
  message: 'The total line discount is above 60 percent.',
  verdict: 'violation',
  reason_code: 'guardrail_block_publish',
  reason: 'Too deep (SUM of discount over 2 line_item record(s) is 70.0, and the rule requires > 60.)',
}

const BLOCKING_EVALUATION = {
  quote_id: 'wf086_quote_1',
  rules_evaluated: 1,
  verdicts: [BLOCKING_VERDICT],
  violations: [BLOCKING_VERDICT],
  blocking: [BLOCKING_VERDICT],
  warnings: [],
  unverifiable: [],
  skipped: [],
  blocked: true,
  publishable: false,
  reason_code: 'guardrail_block_publish',
  reason: 'A quote rule the administrator set to Block publish is violated.',
}

const UNVERIFIABLE_EVALUATION = {
  ...BLOCKING_EVALUATION,
  verdicts: [
    {
      ...BLOCKING_VERDICT,
      verdict: 'unverifiable',
      reason_code: 'guardrail_unverifiable',
      reason: 'The quote does not carry the property this rule reads.',
    },
  ],
  violations: [],
  blocking: [],
  unverifiable: [
    {
      ...BLOCKING_VERDICT,
      verdict: 'unverifiable',
      reason_code: 'guardrail_unverifiable',
      reason: 'The quote does not carry the property this rule reads.',
    },
  ],
  blocked: false,
  publishable: true,
  reason_code: 'guardrail_unverifiable',
}

const QUOTES = {
  count: 1,
  records: [
    { id: 'wf086_quote_1', data: { name: 'Northwind renewal', hs_quote_amount: 48000 } },
  ],
}

const SUMMARY = {
  rules: { total: 1, enabled: 1, disabled: 0, block_publish: 1, show_warning: 0 },
  evaluations: { total: 0, blocked: 0, publishable: 0 },
  publish_attempts: { total: 7, allowed: 4, blocked: 3 },
}

function stubApi(overrides = {}) {
  const spies = {
    vocabulary: vi.fn().mockResolvedValue(VOCABULARY),
    inferences: vi.fn().mockResolvedValue({ count: 0, decisions: { inferences: [], not_built: [] } }),
    rules: vi.fn().mockResolvedValue(RULES),
    summary: vi.fn().mockResolvedValue(SUMMARY),
    listQuotes: vi.fn().mockResolvedValue(QUOTES),
    listLineItems: vi.fn().mockResolvedValue({ count: 0, records: [] }),
    createRule: vi.fn().mockResolvedValue({ id: 'wf090_quote_rule_2' }),
    patchRule: vi.fn().mockResolvedValue({ id: 'wf090_quote_rule_1' }),
    deleteRule: vi.fn().mockResolvedValue({ id: 'wf090_quote_rule_1' }),
    validate: vi.fn().mockResolvedValue({ valid: true, normalised: DEEP_DISCOUNT }),
    evaluate: vi.fn().mockResolvedValue(BLOCKING_EVALUATION),
    publish: vi.fn().mockResolvedValue({ allowed: true, warnings: [] }),
  }
  for (const [key, value] of Object.entries({ ...spies, ...overrides })) {
    vi.spyOn(guardrailApi, key).mockImplementation(value)
  }
  return spies
}

async function renderPage() {
  render(<QuoteGuardrails />)
  await waitFor(() => expect(screen.getByText('Quote guardrails')).toBeTruthy())
  await screen.findAllByText('Deep line discount')
}

/**
 * Fill a field the way a person would, without `userEvent.type`'s keyboard syntax.
 *
 * `userEvent.type` reads `{`, `[` and `]` as key descriptors, so a DSL definition typed
 * through it arrives mangled. A real paste does not: `fireEvent.change` sets the value and
 * fires the same `onChange` the typing would, which is the contract under test.
 */
function fill(element, value) {
  fireEvent.change(element, { target: { value } })
}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe('the descriptor', () => {
  it('exports a discoverable descriptor whose id matches the backend feature id', () => {
    expect(descriptor.id).toBe('wf-090-quote-guardrails')
    expect(typeof descriptor.label).toBe('string')
    expect(descriptor.Component).toBeTruthy()
  })

  it('passes its own glyph rather than appending to the shared PATHS map', () => {
    expect(descriptor.iconPath).toBeTruthy()
    expect(descriptor.iconPath).not.toBe('')
  })
})

describe('the page', () => {
  it('renders the heading, the counts and the two documented limits', async () => {
    stubApi()
    await renderPage()
    expect(screen.getByText('3 stopped by a rule')).toBeTruthy()
    expect(screen.getByText(/quote-level discount properties are not available/)).toBeTruthy()
    expect(screen.getByText(/Arithmetic inside aggregate functions/)).toBeTruthy()
  })

  it('offers the five quoted examples and fills the definition with one', async () => {
    stubApi()
    await renderPage()
    await userEvent.click(screen.getByText(SOLD_TOGETHER))
    expect(screen.getByLabelText('Definition').value).toBe(SOLD_TOGETHER)
  })
  it('asks the parser before saving and shows the normalised definition', async () => {
    const spies = stubApi()
    await renderPage()
    fill(screen.getByLabelText('Definition'), DEEP_DISCOUNT)
    await userEvent.click(screen.getByText('Check definition'))
    await waitFor(() => expect(spies.validate).toHaveBeenCalledWith(DEEP_DISCOUNT))
    expect(await screen.findByText('Readable')).toBeTruthy()
    expect(screen.getAllByText(DEEP_DISCOUNT).length).toBeGreaterThan(0)
  })

  it('shows the parser reason when a definition is refused, and still 200', async () => {
    stubApi({
      validate: vi.fn().mockResolvedValue({
        valid: false,
        error: 'guardrail_quote_discount_unavailable',
        detail: 'quote-level discount properties are not available in the DSL.',
      }),
    })
    await renderPage()
    fill(screen.getByLabelText('Definition'), '[quote.hs_discount] > 5')
    await userEvent.click(screen.getByText('Check definition'))
    expect(await screen.findByText('The parser refused this definition')).toBeTruthy()
    expect(screen.getAllByText('guardrail_quote_discount_unavailable').length).toBeGreaterThan(0)
  })

  it('reports the server refusal when an unreadable rule is saved', async () => {
    const error = new Error('Arithmetic inside aggregate functions is not supported.')
    error.status = 422
    stubApi({ createRule: vi.fn().mockRejectedValue(error) })
    await renderPage()
    fill(screen.getByLabelText('Name'), 'Bad rule')
    fill(screen.getByLabelText('Definition'), 'SUM([a] * [b]) FROM line_item > 1')
    fill(screen.getByLabelText('Message'), 'No')
    await userEvent.click(screen.getByText('Save rule'))
    expect(await screen.findByText(/Could not save \(422\)/)).toBeTruthy()
    expect(screen.getAllByText(/Arithmetic inside aggregate functions/).length).toBeGreaterThan(0)
  })

  it('flips a rule’s status through the enable switch', async () => {
    const spies = stubApi()
    await renderPage()
    await userEvent.click(screen.getByText('Disable'))
    await waitFor(() => expect(spies.patchRule).toHaveBeenCalledWith('wf090_quote_rule_1', { status: 'disabled' }))
  })

  it('evaluates a quote without writing anything', async () => {
    const spies = stubApi()
    await renderPage()
    await userEvent.selectOptions(screen.getByLabelText('Quote'), 'wf086_quote_1')
    await userEvent.click(screen.getByText('Evaluate'))
    await waitFor(() => expect(spies.evaluate).toHaveBeenCalledWith('wf086_quote_1'))
    expect(spies.publish).not.toHaveBeenCalled()
    expect(await screen.findByText('Blocking rules')).toBeTruthy()
  })

  it('renders a blocked publish as the gate working, naming the rule', async () => {
    const refusal = new Error('The total line discount is above 60 percent.')
    refusal.status = 409
    const spies = stubApi({ publish: vi.fn().mockRejectedValue(refusal) })
    await renderPage()
    await userEvent.selectOptions(screen.getByLabelText('Quote'), 'wf086_quote_1')
    await userEvent.click(screen.getByText('Publish'))
    expect(await screen.findByText(/Publish stopped \(409\)/)).toBeTruthy()
    expect((await screen.findAllByText(/The total line discount is above 60 percent/)).length).toBeGreaterThan(0)
    // The verdicts are re-read, so the blocking rule is named rather than only a status.
    await waitFor(() => expect(spies.evaluate).toHaveBeenCalledWith('wf086_quote_1'))
    expect((await screen.findAllByText('Deep line discount')).length).toBeGreaterThan(1)
  })

  it('reports a publish that no rule blocked', async () => {
    stubApi({ publish: vi.fn().mockResolvedValue({ allowed: true, warnings: [{ rule_name: 'Large quote' }] }) })
    await renderPage()
    await userEvent.selectOptions(screen.getByLabelText('Quote'), 'wf086_quote_1')
    await userEvent.click(screen.getByText('Publish'))
    expect(await screen.findByText('Published')).toBeTruthy()
  })

  it('shows a rule the quote cannot answer as "Cannot check", never as blocked', async () => {
    stubApi({ evaluate: vi.fn().mockResolvedValue(UNVERIFIABLE_EVALUATION) })
    await renderPage()
    await userEvent.selectOptions(screen.getByLabelText('Quote'), 'wf086_quote_1')
    await userEvent.click(screen.getByText('Evaluate'))
    expect(await screen.findByText('Cannot check')).toBeTruthy()
    expect(screen.queryByText('Blocked')).toBeNull()
    expect(screen.getByText('Could not be checked')).toBeTruthy()
  })
})
