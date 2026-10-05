/**
 * The price book rules page, tested against real API payloads.
 *
 * These tests exist for the parts of the page where a mistake would be *believable*
 * rather than visibly broken. That is most of this page, because the researched rules
 * are all about what does NOT happen:
 *
 * a deal matching two rules rendered as priced, when the sourced answer is that nothing
 * is written and a person chooses;
 * the update trigger rendered as an error, when "Price books are auto-assigned only when a
 * deal is created" is the rule working;
 * **Change price book** offered without saying it removes the previous book's line items,
 * which the source states outright;
 * a reason conveyed by colour alone, which the accessibility floor bans;
 * a mode quoted on the page that disagrees with the mode the server reports.
 *
 * Every assertion is about something the research fixes, not about markup.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import descriptor from './index'
import { PriceBookRulesPage } from './index'
import { priceBookApi, listDeals, listQuotes, listRooms, modeName } from './api'
import { BookStateBadge, ReasonBadge } from './primitives'

// The three core-collection reads are module-level named exports, not methods on the
// api object, and an ESM named export is not replaceable by `vi.spyOn`. Wrapping them
// in `vi.fn()` while keeping every other export real is what makes them stubbable
// without a second module and without the test reaching past the API it is testing.
vi.mock('./api', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    listRooms: vi.fn(),
    listDeals: vi.fn(),
    listQuotes: vi.fn(),
  }
})

const VOCABULARY = {
  collections: { price_book_rules: 'wf088_price_book_rule' },
  filters: {
    objects: [
      { object: 'deal', label: 'Deal properties.' },
      { object: 'company', label: 'Company properties.' },
    ],
    // The labels are the server's own, and a `<select>` is driven by its option *text*,
    // so this fixture carries `is any of` rather than the code `in`. Selecting a code
    // that no option carries fails, which is a property of the control and not a bug in
    // the page, so the fixture spells the label.
    operators: [
      { operator: 'is', label: 'is' },
      { operator: 'is_not', label: 'is not' },
      { operator: 'gt', label: 'is greater than' },
      { operator: 'gte', label: 'is at least' },
      { operator: 'in', label: 'is any of' },
      { operator: 'not_in', label: 'is none of' },
    ],
    match_modes: [
      { mode: 'all', label: 'Every filter must be met.' },
      { mode: 'any', label: 'Any one filter is enough.' },
    ],
    default_match_mode: 'all',
    group_quote: 'configure deal-property filters (`and` / `or` groups)',
  },
  switches: {
    enabled: 'enabled',
    auto_assign: 'auto_assign',
    auto_assigned_toggle_quote: 'toggle **Auto-assigned** on',
  },
  modes: [
    { mode: 'no_assignment_rules', label: 'No assignment rules. Manual only.' },
    { mode: 'assignment_rules_without_auto_assignment', label: 'Test first.' },
    { mode: 'assignment_rules_with_auto_assignment', label: 'With auto-assignment.' },
  ],
  price_book: {
    field: 'price_book',
    change_quote: 'Change price book',
    line_items_removed_quote:
      'If the price book is changed, any line items associated with the previous price book will be removed.',
  },
  quote_inheritance: {
    quote:
      "Quotes inherit the price book from the associated deal. Users can't select a price book when creating a quote; they must select it on the deal.",
  },
  assignments: [
    {
      reason: 'assigned',
      label: 'One rule matched exactly, so its price book was written onto the deal.',
      writes: true,
    },
    {
      reason: 'needs_choice',
      label: 'More than one price book matched, so nothing was written and the deal owner chooses.',
      writes: false,
    },
    {
      reason: 'auto_assign_runs_on_create_only',
      label: 'Price books are auto-assigned only when a deal is created.',
      writes: false,
    },
  ],
  triggers: [
    { trigger: 'create', label: 'The deal row was created.' },
    { trigger: 'update', label: 'Something changed. Assigns nothing.' },
  ],
  book_states: [
    { state: 'assigned', label: 'A price book is on the deal.' },
    { state: 'needs_choice', label: 'More than one matched. A person chooses.' },
    { state: 'unassigned', label: 'No price book is on the deal.' },
  ],
}

const INFERENCES = {
  count: 8,
  decisions: [],
  multiple_matches: {
    code: 'needs_choice',
    sourced: [
      'If multiple price books match, the deal owner can choose which matching price book to use.',
      "If multiple price levels are returned, the price level field isn't populated and the user must specify a price level.",
    ],
    why: 'Both sentences say a person chooses and neither writes a book.',
    rejected: 'An invented tie-break would price a buyer at a price nobody picked.',
    jev_audit_id: 'jev-20261005T121136-18784-96276',
    jev_confidence: 1,
  },
  override_removes_lines: {
    quote:
      'If the price book is changed, any line items associated with the previous price book will be removed.',
    why: 'A line that names the previous book, and a line that names no book at all.',
    rejected: 'Keeping them would quote a mix of two books prices.',
    jev_audit_id: 'jev-20261005T121136-18784-96564',
    jev_confidence: 1,
  },
}

const SUMMARY = {
  rules: 2,
  rules_enabled: 1,
  rules_auto_assigning: 1,
  assignments: 3,
  written: 1,
  awaiting_a_choice: 1,
  mode: 'assignment_rules_with_auto_assignment',
  catalogue_found: false,
  catalogue_note: 'A price book is a reference by id or name, so a rule works before the catalogue exists.',
  by_outcome: { assigned: 1, needs_choice: 1, matched_rule_without_auto_assign: 1 },
}

const ENTERPRISE = { id: 'pb-ent', name: 'Enterprise list 2026' }
const STANDARD = { id: 'pb-std', name: 'Standard list' }
const PARTNER = { id: 'pb-prt', name: 'Preferred partner list' }

const DEAL_ONE = {
  id: 'd1',
  data: { name: 'Northwind expansion', segment: 'enterprise', amount: 12000 },
}

const CONDITION_ASSIGNED = {
  deal_id: 'd1',
  outcome: 'assigned',
  explanation: 'One rule matched exactly, so its price book was written onto the deal.',
  writes: true,
  state: 'assigned',
  mode: 'assignment_rules_with_auto_assignment',
  create_only_quote:
    "Price books are auto-assigned only when a deal is created. After a price book is auto-assigned, HubSpot won't run auto-assignment again if the deal or associated company properties used in the filter are updated.",
  candidates: [
    { rule_id: 'r1', rule_label: 'Enterprise deals', price_book: ENTERPRISE, matched_filters: [] },
  ],
  rules: [
    {
      rule_id: 'r1',
      rule_label: 'Enterprise deals',
      matched: true,
      enabled: true,
      auto_assign: true,
      price_book: ENTERPRISE,
      filters: [
        {
          object: 'deal',
          property: 'segment',
          operator: 'is',
          expected: 'enterprise',
          actual: 'enterprise',
          matched: true,
          present: true,
        },
      ],
    },
  ],
  inactive_rules: [],
  test_first_rules: [],
  line_items: [{ _id: 'li1', sku: 'SEAT-STD', quantity: 2 }],
  // What an override would remove right now. A deal that already carries a price book
  // and has a line on it has something to lose, so the realistic fixture is not an empty
  // list. `line_items_removed_on_change` is the server's own answer, computed by the same
  // `lines_for_book` the override calls, which is why it is not derived from `line_items`
  // here: a line naming a different book survives and must not be counted.
  line_items_removed_on_change: [{ _id: 'li1', sku: 'SEAT-STD', quantity: 2 }],
}

const CONDITION_CHOICE = {
  ...CONDITION_ASSIGNED,
  outcome: 'needs_choice',
  explanation: 'More than one price book matched, so nothing was written and the deal owner chooses.',
  writes: false,
  state: 'needs_choice',
  candidates: [
    { rule_id: 'r1', rule_label: 'Mid standard', price_book: STANDARD, matched_filters: [] },
    { rule_id: 'r2', rule_label: 'Mid partner', price_book: PARTNER, matched_filters: [] },
  ],
  rules: [
    {
      rule_id: 'r1',
      rule_label: 'Mid standard',
      matched: true,
      enabled: true,
      auto_assign: true,
      price_book: STANDARD,
      filters: [
        {
          object: 'deal',
          property: 'segment',
          operator: 'is',
          expected: 'mid',
          actual: 'mid',
          matched: true,
          present: true,
        },
      ],
    },
    {
      rule_id: 'r2',
      rule_label: 'Mid partner',
      matched: true,
      enabled: true,
      auto_assign: true,
      price_book: PARTNER,
      filters: [
        {
          object: 'deal',
          property: 'amount',
          operator: 'gte',
          expected: 20000,
          actual: 45000,
          matched: true,
          present: true,
        },
      ],
    },
  ],
  line_items: [
    { _id: 'li1', sku: 'SEAT-STD', quantity: 2 },
    { _id: 'li2', sku: 'SEAT-PREMIUM', quantity: 1 },
  ],
}

const CARD_UNASSIGNED = {
  deal_id: 'd1',
  price_book: null,
  price_book_label: 'None',
  deal_field: null,
  authority: 'none',
  state: 'unassigned',
  candidates: [],
  needs_a_choice: false,
}

const CARD_ASSIGNED = {
  deal_id: 'd1',
  price_book: { ...ENTERPRISE, rule_label: 'Enterprise deals' },
  price_book_label: 'Enterprise list 2026',
  deal_field: 'price_book',
  authority: 'price_book_field',
  state: 'assigned',
  candidates: CONDITION_ASSIGNED.candidates,
  needs_a_choice: false,
}

const CARD_CHOICE = {
  deal_id: 'd1',
  price_book: null,
  price_book_label: 'None',
  deal_field: null,
  authority: 'none',
  state: 'needs_choice',
  candidates: CONDITION_CHOICE.candidates,
  needs_a_choice: true,
}

const ASSIGNED_ROW = {
  id: 'a1',
  deal_id: 'd1',
  outcome: 'assigned',
  written: true,
  price_book_label: 'Enterprise list 2026',
  rule_label: 'Enterprise deals',
  trigger: 'create',
  at: '2026-10-05T09:00:00.000+00:00',
  line_items_removed_count: 0,
  explanation: 'One rule matched exactly, so its price book was written onto the deal.',
}

const CHOICE_ROW = {
  id: 'a2',
  deal_id: 'd1',
  outcome: 'needs_choice',
  written: false,
  price_book_label: 'None',
  rule_label: null,
  trigger: 'create',
  at: '2026-10-05T09:00:01.000+00:00',
  line_items_removed_count: 0,
  explanation: 'More than one price book matched, so nothing was written.',
}

const QUOTE = { id: 'q1', data: { name: 'Northwind quote', deal: 'd1' } }

/**
 * Stub every call the page makes.
 *
 * `overrides` is merged into the defaults *before* the spies are installed, not after.
 * Applying them afterwards was a silent no-op: the spy had already been wired to the
 * default implementation, so a test that overrode `conditions` rendered the default
 * payload and failed on a string the page had legitimately not rendered. Every
 * override in this file depends on that ordering, so it is stated here rather than
 * discovered by the next test to be written.
 */
function stubApi(overrides = {}) {
  const defaults = {
    vocabulary: vi.fn().mockResolvedValue(VOCABULARY),
    inferences: vi.fn().mockResolvedValue(INFERENCES),
    summary: vi.fn().mockResolvedValue(SUMMARY),
    // One active auto-assigning rule. The page's two explanations for "this rule matched
    // but assigned nothing" are mutually exclusive by construction: the Inactive
    // switch and the Auto-assigned switch are read independently, and a rule is one or
    // the other here. Listing both in the default fixture put both sentences on screen
    // at once, which is not a state this product creates and which made a test that
    // asserted on one of them ambiguous.
    rules: vi.fn().mockResolvedValue({
      count: 1,
      rules: [
        {
          id: 'r1',
          data: {
            key: 'enterprise',
            label: 'Enterprise deals',
            price_book: ENTERPRISE,
            enabled: true,
            auto_assign: true,
            filters: [{ object: 'deal', property: 'segment', operator: 'is', value: 'enterprise' }],
          },
        },
      ],
    }),
    assignments: vi.fn().mockResolvedValue({ assignments: [CHOICE_ROW, ASSIGNED_ROW] }),
    conditions: vi.fn().mockResolvedValue(CONDITION_ASSIGNED),
    priceBook: vi.fn().mockResolvedValue(CARD_ASSIGNED),
    quotePriceBook: vi.fn().mockResolvedValue({
      price_book: ENTERPRISE,
      price_book_label: 'Enterprise list 2026',
      authority: 'deal',
      deal_id: 'd1',
      deal_field: 'price_book',
      settable_here: false,
      evidence: VOCABULARY.quote_inheritance.quote,
      reason: 'The price book is on the associated deal, and this quote inherits it.',
    }),
    assign: vi.fn().mockResolvedValue({
      assigned: true,
      written: true,
      outcome: 'assigned',
      explanation: 'One rule matched exactly, so its price book was written onto the deal.',
      price_book: ENTERPRISE,
      price_book_label: 'Enterprise list 2026',
      trigger: 'create',
      state: 'assigned',
      create_only_quote: CONDITION_ASSIGNED.create_only_quote,
    }),
    changePriceBook: vi.fn().mockResolvedValue({
      changed: true,
      outcome: 'changed_by_hand',
      price_book: PARTNER,
      price_book_label: 'Preferred partner list',
      line_items_removed: ['li1', 'li2'],
      line_items_removed_count: 2,
      line_items_removed_quote: VOCABULARY.price_book.line_items_removed_quote,
    }),
    createRule: vi.fn().mockResolvedValue({ data: { label: 'Enterprise deals' } }),
    patchRule: vi.fn().mockResolvedValue({ data: { label: 'Enterprise deals' } }),
    removeRule: vi.fn().mockResolvedValue({}),
    // Plain objects rather than promises: `apiRequest` resolves, so a mocked function
    // has to return a promise, but these three are installed with `mockResolvedValue`
    // below and are therefore read as values here. Keeping them values means a test can
    // override `deals` with an object literal and get the same shape as the default.
    rooms: { records: [{ id: 'room_a', data: { name: 'Northwind' } }] },
    deals: { records: [DEAL_ONE] },
    quotes: { records: [QUOTE] },
  }
  // Merged here, before a single spy exists, so an override replaces the default
  // implementation rather than being handed back to a caller who never installed it.
  const spies = { ...defaults, ...overrides }

  for (const name of Object.keys(spies)) {
    if (name === 'rooms' || name === 'deals' || name === 'quotes') continue
    vi.spyOn(priceBookApi, name).mockImplementation(spies[name])
  }

  // The three core-collection reads are module-level named exports rather than methods
  // on the api object. `vi.spyOn` on an ESM module namespace is not supported, which
  // is why the `vi.mock` at the top of this file wraps them in `vi.fn()`: without that
  // this stub would install nothing and every test would read the real transport.
  vi.mocked(listRooms).mockResolvedValue(spies.rooms)
  vi.mocked(listDeals).mockResolvedValue(spies.deals)
  vi.mocked(listQuotes).mockResolvedValue(spies.quotes)

  return spies
}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe('the descriptor', () => {
  it('exports a discoverable descriptor whose id matches the backend feature id', () => {
    expect(descriptor.id).toBe('wf-088-auto-assign-the-correct-price-book-or-price')
    expect(typeof descriptor.label).toBe('string')
    expect(descriptor.Component).toBeTruthy()
  })

  it('passes its own glyph rather than appending to the shared PATHS map', () => {
    expect(descriptor.iconPath).toBeTruthy()
    expect(descriptor.iconPath).not.toBe('')
  })
})

describe('the page', () => {
  it('renders the heading and the board', async () => {
    stubApi()
    render(<PriceBookRulesPage />)

    await waitFor(() => expect(screen.getByText('Auto-assign the correct price book')).toBeTruthy())
    expect(await screen.findByText('Waiting on a choice')).toBeTruthy()
    // The number a reviewer acts on, which the research's multiple-match rule creates.
    expect(screen.getByText('Deals matching more than one rule')).toBeTruthy()
  })

  it('names the mode the server reports rather than one it invents', async () => {
    stubApi()
    render(<PriceBookRulesPage />)

    // The card carries the mode's short name. Found by looking at the rendered page: the
    // card had been given the server's full sentence, "*Assignment rules with
    // auto-assignment*. Exactly one matching rule writes its price book onto the deal.",
    // which set in the display face at card size wrapped to nine lines and read as a layout
    // fault. So the assertion is on the name, and the sentence is checked to still be on the
    // page from the reading panel, which is where it belongs.
    const card = await screen.findByText('Mode')
    expect(within(card.parentElement).getByText('Auto-assignment')).toBeTruthy()

    // The sentence is still on the page, from the reviewed-rules panel that reports this
    // deal's own mode. The fixture's label is short ("With auto-assignment.") where the
    // server's is the full researched wording, so the assertion is on what the fixture
    // carries rather than on the server's longer sentence.
    const sentences = await screen.findAllByText(/With auto-assignment/)
    expect(sentences.length).toBeGreaterThan(0)
  })

  it('shortens the mode name to the clause that distinguishes the three modes', () => {
    // The leading clause is dropped, not the trailing one. Keeping it produced the string
    // "Assignment rules", which is the same text as the rule builder's own heading — so two
    // differently-meaning elements on one page read identically, and a test querying that
    // heading found two candidates and could not say which it meant.
    expect(modeName('assignment_rules_with_auto_assignment')).toBe('Auto-assignment')
    expect(modeName('assignment_rules_without_auto_assignment')).toBe('Without auto-assignment')
    expect(modeName('no_assignment_rules')).toBe('No assignment rules')
    // A mode code with no recognised clause still reads, rather than rendering an empty card.
    expect(modeName('some_future_mode')).toBe('Some future mode')
    expect(modeName(undefined)).toBe('Unknown')
  })

  it('does not reuse the rule builder heading as the mode card value', async () => {
    stubApi()
    render(<PriceBookRulesPage />)

    // Distinct strings, so a reader scanning the page can tell the mode from the section
    // that configures it, and so a query for either one is unambiguous.
    const card = await screen.findByText('Mode')
    const value = within(card.parentElement).getByText('Auto-assignment')
    expect(value).toBeTruthy()
    // The heading is still findable on its own, which is what makes them distinguishable.
    expect(screen.getByRole('heading', { name: 'Assignment rules' })).toBeTruthy()
  })

  it('says when no catalogue was found rather than implying one exists', async () => {
    stubApi()
    render(<PriceBookRulesPage />)

    // WF-087 has not shipped. A page that implied pricing was fully scoped would be
    // telling a seller their line items are scoped to a catalogue that is not there.
    expect(await screen.findByText('No product catalogue was found')).toBeTruthy()
  })

  it('renders both open readings with their evidence', async () => {
    stubApi()
    render(<PriceBookRulesPage />)

    expect(await screen.findByText('When more than one book matches')).toBeTruthy()
    expect(
      screen.getByText(/If multiple price books match, the deal owner can choose/),
    ).toBeTruthy()
    // The second sourced sentence, which is about a different vendor and agrees anyway.
    expect(screen.getByText(/price level field isn't populated/)).toBeTruthy()
  })

  it('renders the audit that chose the reading, not only the reading', async () => {
    stubApi()
    render(<PriceBookRulesPage />)

    expect(await screen.findByText(/jev-20261005T121136-18784-96276/)).toBeTruthy()
  })

  it('renders the rule builder with the researched toggles', async () => {
    stubApi()
    render(<PriceBookRulesPage />)

    expect(await screen.findByText('Assignment rules')).toBeTruthy()
    expect(screen.getByText(/toggle \*\*Auto-assigned\*\* on/)).toBeTruthy()
    expect(screen.getByText(/`and` \/ `or` groups/)).toBeTruthy()
  })

  it('offers both filter objects the research names', async () => {
    stubApi()
    render(<PriceBookRulesPage />)

    await waitFor(() => expect(screen.getByText('Assignment rules')).toBeTruthy())
    const select = screen.getByLabelText('Filtering on')
    const objects = within(select)
      .getAllByRole('option')
      .map((option) => option.textContent)
    expect(objects).toContain('deal')
    expect(objects).toContain('company')
  })

  it('saves a rule with its price book and its filters', async () => {
    const spies = stubApi()
    render(<PriceBookRulesPage />)

    await waitFor(() => expect(screen.getByText('Assignment rules')).toBeTruthy())
    await userEvent.type(screen.getByLabelText('Rule name'), 'enterprise')
    await userEvent.type(screen.getByLabelText('Price book name'), 'Enterprise list 2026')
    await userEvent.type(screen.getByLabelText('Property'), 'segment')
    await userEvent.type(screen.getByLabelText('Value'), 'enterprise')
    await userEvent.click(screen.getByText('Save the rule'))

    await waitFor(() => expect(spies.createRule).toHaveBeenCalled())
    const sent = spies.createRule.mock.calls[0][0]
    expect(sent.price_book.name).toBe('Enterprise list 2026')
    expect(sent.filters[0].property).toBe('segment')
    expect(sent.auto_assign).toBe(true)
  })

  it('splits a list operator value into a list rather than one string', async () => {
    const spies = stubApi()
    render(<PriceBookRulesPage />)

    await waitFor(() => expect(screen.getByText('Assignment rules')).toBeTruthy())
    await userEvent.type(screen.getByLabelText('Rule name'), 'tagged')
    await userEvent.type(screen.getByLabelText('Price book name'), 'Standard list')
    await userEvent.type(screen.getByLabelText('Property'), 'tags')
    await userEvent.selectOptions(screen.getByLabelText('Operator'), 'is any of')
    await userEvent.type(screen.getByLabelText('Value'), 'a, b, c')
    await userEvent.click(screen.getByText('Save the rule'))

    await waitFor(() => expect(spies.createRule).toHaveBeenCalled())
    // One comma-separated string would match nothing and read as a configured rule.
    expect(spies.createRule.mock.calls[0][0].filters[0].value).toEqual(['a', 'b', 'c'])
  })

  it('shows both switches on a configured rule, in words', async () => {
    stubApi()
    render(<PriceBookRulesPage />)

    // The label appears three times on this page: the rule row, the reviewed panel and
    // the log. A single-match query would be asserting on whichever the DOM happened to
    // order first, so the count is asserted instead.
    expect((await screen.findAllByText('Enterprise deals')).length).toBeGreaterThan(0)
    // One rule in the default fixture, so one Active button and one Auto-assigned button.
    // The labels state the current state rather than the action, so a seller can read
    // what a rule does without clicking it.
    expect(screen.getAllByText('Active')).toHaveLength(1)
    expect(screen.getAllByText('Auto-assigned on')).toHaveLength(1)
  })

  it('explains an inactive rule rather than leaving the seller to guess', async () => {
    stubApi({
      rules: vi.fn().mockResolvedValue({
        count: 1,
        rules: [
          {
            id: 'r2',
            data: {
              key: 'renewal',
              label: 'Renewal list',
              price_book: STANDARD,
              enabled: false,
              auto_assign: true,
              filters: [{ object: 'deal', property: 'segment', operator: 'is', value: 'renewal' }],
            },
          },
        ],
      }),
    })
    render(<PriceBookRulesPage />)

    expect((await screen.findAllByText('Renewal list')).length).toBeGreaterThan(0)
    expect(screen.getByText(/inactive, so this rule assigns nothing/)).toBeTruthy()
  })

  it('explains a test-first rule as the researched mode', async () => {
    stubApi({
      rules: vi.fn().mockResolvedValue({
        count: 1,
        rules: [
          {
            id: 'r2',
            data: {
              key: 'pilot',
              label: 'Pilot deals',
              price_book: STANDARD,
              enabled: true,
              auto_assign: false,
              filters: [{ object: 'deal', property: 'segment', operator: 'is', value: 'pilot' }],
            },
          },
        ],
      }),
    })
    render(<PriceBookRulesPage />)

    // A rule whose Auto-assigned switch is off still matches and is still reported; the
    // page has to say that rather than reporting a match and leaving the seller to guess
    // why no price book arrived. The fixture has to override the rules payload for this
    // one: the default lists an *inactive* rule too, and that branch is checked
    // separately, so both explanations cannot be on screen at once.
    expect(await screen.findByText(/Auto-assigned is off, so this rule is being tested/)).toBeTruthy()
  })
})

describe('the reviewed panel', () => {
  it('reports what each filter read, not only that it matched', async () => {
    stubApi()
    render(<PriceBookRulesPage />)

    expect(await screen.findByText('Review the matching deals')).toBeTruthy()
    expect(screen.getAllByText(/deal.segment/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/Compared with/).length).toBeGreaterThan(0)
  })

  it('says a rule matched but will not assign, rather than reporting a match', async () => {
    stubApi({
      conditions: vi.fn().mockResolvedValue({
        ...CONDITION_ASSIGNED,
        outcome: 'matched_rule_without_auto_assign',
        writes: false,
        rules: [
          {
            ...CONDITION_ASSIGNED.rules[0],
            auto_assign: false,
          },
        ],
      }),
    })
    render(<PriceBookRulesPage />)

    expect(
      await screen.findByText('Matched, but Auto-assigned is off, so nothing is written'),
    ).toBeTruthy()
  })

  it('says a rule matched but its price book is inactive', async () => {
    stubApi({
      conditions: vi.fn().mockResolvedValue({
        ...CONDITION_ASSIGNED,
        outcome: 'matched_rule_is_inactive',
        writes: false,
        rules: [{ ...CONDITION_ASSIGNED.rules[0], enabled: false }],
      }),
    })
    render(<PriceBookRulesPage />)

    expect(
      await screen.findByText('Matched, but the price book is inactive, so nothing is written'),
    ).toBeTruthy()
  })

  it('says a rule did not match and names the property it read', async () => {
    stubApi({
      conditions: vi.fn().mockResolvedValue({
        ...CONDITION_ASSIGNED,
        outcome: 'no_rule_matched',
        writes: false,
        rules: [
          {
            ...CONDITION_ASSIGNED.rules[0],
            matched: false,
            filters: [
              {
                object: 'deal',
                property: 'segment',
                operator: 'is',
                expected: 'enterprise',
                actual: 'pilot',
                matched: false,
                present: true,
              },
            ],
          },
        ],
      }),
    })
    render(<PriceBookRulesPage />)

    // One match badge, in the reviewed panel. The panel is rendered per deal and there
    // is one deal in the fixture, so a second badge would mean the same filter was
    // rendered twice.
    expect((await screen.findAllByText('Did not match')).length).toBe(1)
    // And what it read, which is the reason a panel exists rather than a yes or a no.
    // The filter row is a parent of the sentence that reports the read, so the assertion
    // is scoped to it rather than to the whole page, where "Read" also appears in prose.
    const rows = await screen.findAllByText(/Operator/)
    expect(rows.some((row) => row.textContent.includes('"pilot"'))).toBe(true)
  })
})

describe('the deal panel', () => {
  it('reads the card as Price book: None when nothing is assigned', async () => {
    stubApi({
      conditions: vi.fn().mockResolvedValue({
        ...CONDITION_ASSIGNED,
        outcome: 'no_rule_matched',
        writes: false,
        state: 'unassigned',
        candidates: [],
      }),
      priceBook: vi.fn().mockResolvedValue(CARD_UNASSIGNED),
    })
    render(<PriceBookRulesPage />)

    // The card reads `Price book:` and the label beside it is its own element, so the
    // assertion is on the two of them together rather than on either string alone.
    const card = (await screen.findAllByText('Price book:'))[0].parentElement
    expect(within(card).getByText('None')).toBeTruthy()
  })

  it('shows the assigned book with the field it came from', async () => {
    stubApi()
    render(<PriceBookRulesPage />)

    expect(await screen.findAllByText('Enterprise list 2026')).toBeTruthy()
    expect(screen.getAllByText(/price_book/).length).toBeGreaterThan(0)
  })

  it('says nothing was written when two rules matched, and offers both books', async () => {
    stubApi({
      conditions: vi.fn().mockResolvedValue(CONDITION_CHOICE),
      priceBook: vi.fn().mockResolvedValue(CARD_CHOICE),
    })
    render(<PriceBookRulesPage />)

    // The sourced answer for more than one match. A page that rendered this as priced
    // would be claiming a price book nobody chose. The explanation appears on the
    // panel's own notice and again on the log row, so a count is the honest assertion.
    expect(await screen.findByText('Nothing was written')).toBeTruthy()
    expect(screen.getAllByText(/More than one price book matched/).length).toBeGreaterThan(0)
    // Each book is named twice: once with the rule that offers it, and once in the log
    // row that recorded the collision. Both names are required for the owner to choose,
    // so the count rather than a single match is what the assertion means.
    expect(screen.getAllByText('Standard list').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Preferred partner list').length).toBeGreaterThan(0)
  })

  it('offers the candidates as buttons only after Change price book is clicked', async () => {
    const user = userEvent.setup()
    stubApi({
      conditions: vi.fn().mockResolvedValue(CONDITION_CHOICE),
      priceBook: vi.fn().mockResolvedValue(CARD_CHOICE),
    })
    render(<PriceBookRulesPage />)

    // The button says **Set** rather than **Change** while the card reads None, because
    // there is no previous book to change. Asserting on the label a deal in this state
    // actually shows is what keeps the two from being collapsed into one.
    await waitFor(() => expect(screen.getByText('Set price book')).toBeTruthy())
    expect(screen.queryByText('Use Standard list')).toBeNull()
    await user.click(screen.getByText('Set price book'))
    expect(await screen.findByText('Use Standard list')).toBeTruthy()
    expect(screen.getByText('Use Preferred partner list')).toBeTruthy()
  })

  it('states the destructive sentence before the override is offered', async () => {
    stubApi()
    render(<PriceBookRulesPage />)

    // The sourced sentence is destructive, and the panel has to carry it rather than
    // letting a seller lose pricing work by clicking a dropdown.
    expect(await screen.findByText('Changing the price book')).toBeTruthy()
    expect(
      screen.getByText(/any line items associated with the previous price book will be removed/),
    ).toBeTruthy()
    // And the override itself, only once the reading is on screen above it.
    expect(screen.getByText('Change price book')).toBeTruthy()
  })

  it('reports the removed line items after an override', async () => {
    const user = userEvent.setup()
    const spies = stubApi()
    render(<PriceBookRulesPage />)

    await waitFor(() => expect(screen.getByText('Change price book')).toBeTruthy())
    await user.click(screen.getByText('Change price book'))
    await user.click(await screen.findByText('Use Enterprise list 2026'))

    await waitFor(() => expect(spies.changePriceBook).toHaveBeenCalled())
    expect(await screen.findByText('This change removes line items')).toBeTruthy()
  })

  // The three below were added after review found the destructive count was only on
  // screen *after* the override had already run. The sentence was shown first; the
  // number was not, and three docstrings claimed it was.

  it('shows the destructive count before the override, not only after it', async () => {
    const user = userEvent.setup()
    const spies = stubApi()
    render(<PriceBookRulesPage />)

    await waitFor(() => expect(screen.getByText('Change price book')).toBeTruthy())

    // Nothing has been overridden yet: `changePriceBook` has not been called.
    expect(spies.changePriceBook).not.toHaveBeenCalled()
    await user.click(screen.getByText('Change price book'))

    // The count is on screen now, while the buttons that would delete are still ahead.
    const warning = await screen.findByText('This change removes line items')
    // Asserted on the notice's own text, not with a regex matcher: the count sits in its
    // own span, so "will be removed" is split across two elements and a text matcher
    // cannot see it.
    expect(warning.parentElement.textContent).toMatch(/will be removed/)
    expect(spies.changePriceBook).not.toHaveBeenCalled()
  })

  it('counts only the line items the override would really remove', async () => {
    // The deal has three lines; one names a different price book and survives the
    // override. Announcing three would frighten a seller about a line that stays, which
    // is the same class of defect as announcing the wrong reason for an unpriced quote.
    const user = userEvent.setup()
    stubApi({
      conditions: vi.fn().mockResolvedValue({
        ...CONDITION_ASSIGNED,
        line_items: [
          { _id: 'li1', sku: 'SEAT-STD' },
          { _id: 'li2', sku: 'SEAT-PREMIUM' },
          { _id: 'li3', sku: 'OTHER-BOOK', price_book: { id: 'pb-other', name: 'Other' } },
        ],
        line_items_removed_on_change: [
          { _id: 'li1', sku: 'SEAT-STD' },
          { _id: 'li2', sku: 'SEAT-PREMIUM' },
        ],
      }),
    })
    render(<PriceBookRulesPage />)

    await waitFor(() => expect(screen.getByText('Change price book')).toBeTruthy())
    await user.click(screen.getByText('Change price book'))

    const warning = await screen.findByText('This change removes line items')
    // The notice renders `2 line items will be removed.` from the server's own list.
    expect(warning.parentElement.textContent).toContain('2 line items will be removed')
    expect(warning.parentElement.textContent).not.toContain('3 line items')
  })

  it('does not promise an override with a book of your own', async () => {
    // Review found the copy said "the price book is set by a rule, or by an override
    // with a book of your own" while the page offered no such control: the researched
    // dropdown lists the workspace's price books, and WF-087's catalogue, which would
    // supply that list, has not shipped. The copy described a capability not present.
    const user = userEvent.setup()
    stubApi({
      conditions: vi.fn().mockResolvedValue({ ...CONDITION_ASSIGNED, candidates: [] }),
      priceBook: vi.fn().mockResolvedValue(CARD_UNASSIGNED),
    })
    render(<PriceBookRulesPage />)

    await waitFor(() => expect(screen.getByText('Set price book')).toBeTruthy())
    await user.click(screen.getByText('Set price book'))

    expect(await screen.findByText(/No rule matched this deal/)).toBeTruthy()
    expect(screen.queryByText(/a book of your own/)).toBeNull()
    // And it says why the list is empty rather than leaving the seller guessing.
    expect(screen.getByText(/has not shipped/)).toBeTruthy()
  })

  it('says the Dynamics half is served as vocabulary rather than built', async () => {
    // The ticket's step 5 is Dynamics and it is not implemented. The page used to
    // describe only the HubSpot half, so the divergence was findable only in a module
    // docstring. It is stated on the page now.
    stubApi()
    render(<PriceBookRulesPage />)

    expect(await screen.findByText('What is built, and what is only named')).toBeTruthy()
    expect(screen.getByText('GetDefaultPriceLevelRequest')).toBeTruthy()
    expect(screen.getByText('Territory Default Pricelist')).toBeTruthy()
    expect(screen.getByText(/served as vocabulary, not built/)).toBeTruthy()
  })

  it('scopes the deals and the quotes to the selected room', async () => {
    // Review found the room picker changed the stat cards and the rule list while the
    // deals and quotes below stayed on every room's, so the board contradicted itself.
    const user = userEvent.setup()
    stubApi({
      rooms: {
        records: [
          { id: 'room_a', data: { name: 'Northwind' } },
          { id: 'room_b', data: { name: 'Halcyon' } },
        ],
      },
    })
    render(<PriceBookRulesPage />)

    // "All rooms" is the default, so the lists are legitimately unscoped at first.
    await waitFor(() => expect(listDeals).toHaveBeenCalled())
    expect(listDeals.mock.calls.at(-1)[0]).toBe('')

    await waitFor(() => expect(screen.getByLabelText('Room')).toBeTruthy())
    await user.selectOptions(screen.getByLabelText('Room'), 'room_b')

    // Now both lists, and every other room-scoped read, are asked for the same room.
    await waitFor(() => expect(listDeals.mock.calls.at(-1)[0]).toBe('room_b'))
    expect(listQuotes.mock.calls.at(-1)[0]).toBe('room_b')
    expect(priceBookApi.rules).toHaveBeenCalledWith('room_b')
    expect(priceBookApi.summary).toHaveBeenCalledWith('room_b')
  })

  it('offers the update trigger and does not present it as an error', async () => {
    const user = userEvent.setup()
    const spies = stubApi({
      assign: vi.fn().mockResolvedValue({
        assigned: false,
        written: false,
        outcome: 'auto_assign_runs_on_create_only',
        explanation: 'Price books are auto-assigned only when a deal is created.',
        price_book: null,
        price_book_label: 'None',
        trigger: 'update',
        state: 'unassigned',
        create_only_quote: CONDITION_ASSIGNED.create_only_quote,
      }),
    })
    render(<PriceBookRulesPage />)

    await waitFor(() => expect(screen.getByText('Run the rule')).toBeTruthy())
    await user.selectOptions(screen.getByLabelText('Trigger'), 'update')
    await user.click(screen.getByText('Run the rule'))

    await waitFor(() => expect(spies.assign).toHaveBeenCalled())
    // The refusal is the researched rule working, so it reads as an explanation and
    // not as a failure the seller has to chase. It appears on the panel's own answer
    // and on the log row, so the count is what the assertion means.
    expect(
      (await screen.findAllByText(/auto_assign_runs_on_create_only/)).length,
    ).toBeGreaterThan(0)
    expect(
      screen.getAllByText(/Price books are auto-assigned only when a deal is created/).length,
    ).toBeGreaterThan(0)
  })

  it('sends the create trigger by default', async () => {
    const user = userEvent.setup()
    const spies = stubApi()
    render(<PriceBookRulesPage />)

    await waitFor(() => expect(screen.getByText('Run the rule')).toBeTruthy())
    await user.click(screen.getByText('Run the rule'))

    await waitFor(() => expect(spies.assign).toHaveBeenCalled())
    expect(spies.assign.mock.calls[0][1].trigger).toBe('create')
  })
})

describe('the log', () => {
  it('renders every decision including the ones that wrote nothing', async () => {
    stubApi()
    render(<PriceBookRulesPage />)

    expect((await screen.findAllByText('Every decision')).length).toBeGreaterThan(0)
    // Both rows are on screen: the one that wrote a book and the one that deliberately
    // did not. A log that only kept the successes is what makes "why is this deal
    // unpriced" unanswerable. Each code appears in the row's badge and in its prose, so
    // the count is what is asserted rather than a single match.
    expect(screen.getAllByText('needs_choice').length).toBeGreaterThan(0)
    expect(screen.getAllByText('assigned').length).toBeGreaterThan(0)
  })

  it('offers the published reasons as a filter', async () => {
    stubApi()
    render(<PriceBookRulesPage />)

    await waitFor(() => expect(screen.getByLabelText('Reason')).toBeTruthy())
    const reasons = within(screen.getByLabelText('Reason'))
      .getAllByRole('option')
      .map((option) => option.value)
      expect(reasons).toContain('needs_choice')
    // Served from the server's vocabulary, so a reason added there appears here.
    expect(reasons.length).toBeGreaterThan(3)
  })

  it('names the rule that priced a deal, so a past decision explains itself', async () => {
    stubApi()
    render(<PriceBookRulesPage />)

    // The log row carries the rule that wrote the book, so the decision still reads as a
    // decision after the rule itself has been edited or deleted.
    expect((await screen.findAllByText(/By rule/)).length).toBeGreaterThan(0)
  })
})

describe('the quote panel', () => {
  it('shows a quote inheriting its deal price book', async () => {
    stubApi()
    render(<PriceBookRulesPage />)

    expect(await screen.findByText('Quotes and the price book they inherit')).toBeTruthy()
    expect(screen.getAllByText('Inherited from the deal').length).toBeGreaterThan(0)
    // The sourced sentence is in the card's own subheading and again on the row, so a
    // count rather than a single match is what the assertion means.
    expect(
      screen.getAllByText(/Users can't select a price book when creating a quote/).length,
    ).toBeGreaterThan(0)
  })

  it('offers no control to set a price book on a quote', async () => {
    stubApi()
    render(<PriceBookRulesPage />)

    await waitFor(() => expect(screen.getByText('Northwind quote')).toBeTruthy())
    // The sourced sentence forbids it, so the panel has no field and no button for it.
    const panel = screen.getByText('Northwind quote').closest('div').parentElement
    expect(within(panel).queryByText('Change price book')).toBeNull()
  })
})

describe('the primitives', () => {
  it('renders a reason as words, so the colour is not the only signal', () => {
    render(
      <ReasonBadge
        reason="needs_choice"
        vocabulary={VOCABULARY}
        writes={false}
      />,
    )
    expect(screen.getByText('needs_choice')).toBeTruthy()
    expect(
      screen.getByText(/More than one price book matched, so nothing was written/),
    ).toBeTruthy()
  })

  it('renders a deal state as words', () => {
    render(<BookStateBadge state="assigned" vocabulary={VOCABULARY} />)
    expect(screen.getByText('assigned')).toBeTruthy()
    expect(screen.getByText(/A price book is on the deal/)).toBeTruthy()
  })

  it('renders a state it was not told about as the raw value rather than nothing', () => {
    render(<BookStateBadge state="some_future_state" vocabulary={VOCABULARY} />)
    // A label that renders as `undefined` is worse than one that shows what it did
    // not recognise.
    expect(screen.getByText('some_future_state')).toBeTruthy()
  })
})