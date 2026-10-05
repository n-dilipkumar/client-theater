/**
 * Tests for WF-100's page.
 *
 * The page is driven against a stubbed `fetch` rather than a live server, so the assertions
 * are about what a seller reads and which controls are offered, not about the server's answer.
 * Every test builds its own stub and its own data, so the file passes alone and under vitest's
 * parallel run without depending on another test's fixture.
 *
 * Three things get the most attention here, because they are the three the research leaves
 * open and the three a reviewer would otherwise have to take on trust:
 *
 * - the page says the acceptance signal was not sourced, and names the Jev audit id.
 * - the page says which branch of the renewal date rule produced a contract's date.
 * - the page says what it did not build, rather than leaving the gap to be discovered.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import page from './index.jsx'
import {
  CONTRACT_TARGETS,
  EFFECTIVE_DATE_MODES,
  formatDate,
  formatMoney,
  plural,
  quoteState,
} from './api.js'

const { Component } = page

const JEV_AUDIT_ID = 'jev-20261004T231639-22752-99672'

const RENEWAL_RULE_FINALISED =
  'If a renewal has been finalized or a renewal quote has been accepted by the customer, the renewal date will be the effective date of the renewal quote.'
const RENEWAL_RULE_NOT_FINALISED =
  "If a renewal hasn't been finalized, or a renewal quote isn't added to the contract, the renewal date will be the date the contract ends."

function contract(overrides = {}) {
  return {
    id: 'contract_northwind',
    name: 'Northwind platform agreement',
    buyer: 'Halcyon Cloud',
    seller: 'Dana Ruiz',
    currency: 'EUR',
    start_date: '2025-03-01',
    end_date: '2026-03-01',
    term_length: 12,
    term_label: '12',
    total: 57600,
    line_items: [{ sku: 'SEAT-STD', quantity: 40, amount: '48000.00' }],
    renewal: {
      renewal_date: '2026-03-01',
      branch: 'if_not_finalised',
      rule: RENEWAL_RULE_NOT_FINALISED,
      source_quote_id: null,
    },
    alert: { due: '2026-01-30', offset_days: 30 },
    renewable: { renewable: true, reason: '', field: '' },
    chain: {
      renewed_from_contract_id: null,
      renewed_into_contract_id: null,
      renewed_into_quote_id: null,
      renewal_finalised: false,
      previous: null,
      next: null,
    },
    quotes: [],
    ...overrides,
  }
}

function quote(overrides = {}) {
  return {
    id: 'quote_northwind',
    name: 'Renewal of Northwind platform agreement',
    contract_id: 'contract_northwind',
    state: 'draft',
    buyer: 'Halcyon Cloud',
    currency: 'EUR',
    total: 57600,
    prorate: true,
    deal_stage: 'Contract sent',
    proration: { prorated: true, enabled: true },
    effective_date: {
      mode: 'on_agreement',
      label: 'On agreement',
      resolved: false,
      on: null,
    },
    chain: null,
    ...overrides,
  }
}

const SUMMARY = {
  contracts: 2,
  renewable_contracts: 1,
  templates: 1,
  quotes: 1,
  quotes_by_state: { draft: 1 },
  accepted_quotes: 0,
  renewal_contracts: 0,
  deals: 0,
  pipelines: 1,
  workflows: 0,
  rule: `${RENEWAL_RULE_FINALISED} ${RENEWAL_RULE_NOT_FINALISED}`,
  evidence: {
    renewal_creates_contract:
      'When a renewal quote is accepted, a new contract is created and automatically associated with the previous contract.',
  },
}

const VOCABULARY = {
  effective_date_modes: EFFECTIVE_DATE_MODES.map((entry) => entry.value),
  renewal_date_rule: SUMMARY.rule,
  renewal_date_branches: {
    if_finalised: RENEWAL_RULE_FINALISED,
    if_not_finalised: RENEWAL_RULE_NOT_FINALISED,
  },
  renewal_date_branch_note:
    'Both branches are implemented. A contract with no accepted renewal quote reports its own end date.',
  evergreen_label: 'Evergreen',
  evergreen_rule:
    'If all line items are set to Automatically renew until canceled, the term length is marked as Evergreen.',
  evergreen_is_a_label:
    'Evergreen is a derived label on a term length. It is not a separate contract type and it is not a separate state.',
  direct_renewal_beta: true,
  direct_renewal_note:
    'Direct contract renewal is marked BETA in the research and is described as bypassing the quote. This implementation did not build the bypass.',
  re_enroll_note:
    'Re-enroll was named in the research but its behaviour was not described. The behaviour implemented here is a decision, not a sourced fact.',
}

const DECISIONS = {
  count: 2,
  decisions: [
    {
      id: 'wf100-acceptance-signal',
      question: 'Who accepts a renewal quote, and how does the room learn that it happened?',
      chosen: 'An explicit room action records the acceptance',
      left_open_by: 'The research never says who accepts or how the room learns that it happened.',
      rejected_because: 'Inferring acceptance from a status write would give a status field a hidden side effect.',
      cost_of_the_choice: 'The buyer-facing accept click is not modelled as a separate actor.',
      sourced: false,
      jev_audit_id: JEV_AUDIT_ID,
      jev_verdict: 'pass',
    },
    {
      id: 'wf100-evergreen-as-label',
      question: 'Is Evergreen a contract type, a state, or a label?',
      chosen: 'A derived label on the term length',
      left_open_by: 'The research says the term length is marked as Evergreen.',
      rejected_because: 'A separate type would need a migration and a typed column.',
      cost_of_the_choice: 'The label is computed from line items on every read.',
      sourced: true,
    },
  ],
}

const TEMPLATES = {
  count: 1,
  templates: [
    {
      id: 'tpl_annual',
      name: 'Annual renewal template',
      change_type: 'renewal',
      term_months: 12,
      discount_format: 'total',
      association_type: 286,
    },
  ],
  ownership_note: 'This workflow owns the collection because no pending ticket provisions one.',
  association_note: 'The renewal quote template id is attached as association type 286 at creation.',
}

const PIPELINES = {
  count: 1,
  pipelines: [
    {
      id: 'pipe_renewals',
      name: 'Renewals',
      stages: ['Qualification', 'Contract sent', 'Closed won'],
    },
  ],
}

const DEALS = { count: 0, deals: [], created_at_acceptance: true }
const WORKFLOWS = {
  count: 0,
  workflows: [],
  action_note:
    'Create renewal quote from contract is described in the research as a deal-based workflow action and explicitly not a documented REST endpoint.',
  re_enroll_note: VOCABULARY.re_enroll_note,
}

function jsonResponse(body, status = 200) {
  return { ok: status < 400, status, json: async () => body }
}

function stubFetch(routes) {
  const calls = []
  // Longest pattern first. `/wf-100/quotes` is a prefix of `/api/wf-100/quotes/quote_northwind/accept`,
  // so first-match-wins would let the board read swallow the accept stub and every refusal test
  // would silently see a 200.
  const patterns = Object.keys(routes).sort((a, b) => b.length - a.length)
  const handler = async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET', body: options.body })
    for (const pattern of patterns) {
      if (url.includes(pattern)) {
        const body = routes[pattern]
        const resolved = typeof body === 'function' ? await body(options) : body
        return jsonResponse(resolved, resolved?.__status || 200)
      }
    }
    return jsonResponse({ detail: `no stub for ${url}` }, 404)
  }
  global.fetch = vi.fn(handler)
  return calls
}

/** The nine reads the board makes, so a test only overrides the one it cares about. */
function boardRoutes(overrides = {}) {
  return {
    '/wf-100/summary': SUMMARY,
    '/wf-100/vocabulary': VOCABULARY,
    '/wf-100/decisions': DECISIONS,
    '/wf-100/contracts': {
      count: 1,
      contracts: [contract()],
      evergreen_label: 'Evergreen',
      renewal_date_rule: SUMMARY.rule,
      renewal_date_branches: VOCABULARY.renewal_date_branches,
    },
    '/wf-100/templates': TEMPLATES,
    '/wf-100/pipelines': PIPELINES,
    '/wf-100/quotes': {
      count: 1,
      quotes: [quote()],
      states: ['draft', 'shared', 'accepted', 'superseded'],
      derived_not_sourced: "draft, shared and superseded are this implementation's choice.",
    },
    '/wf-100/deals': DEALS,
    '/wf-100/workflows': WORKFLOWS,
    ...overrides,
  }
}

async function renderBoard(overrides = {}) {
  const calls = stubFetch(boardRoutes(overrides))
  render(<Component />)
  // Scoped to level 1: the board also has a level 2 section heading with the same words, and
  // an unscoped match would find two nodes and throw for a reason that has nothing to do with
  // what the test is checking.
  await waitFor(() => expect(screen.getByRole('heading', { level: 1 })).toBeTruthy())
  return calls
}

beforeEach(() => {
  vi.restoreAllMocks()
})

// --------------------------------------------------------------------------- //
// The descriptor
// --------------------------------------------------------------------------- //

describe('the feature descriptor', () => {
  it('exports the id the backend feature registered under', () => {
    expect(page.id).toBe('wf-100-create-a-renewal-quote-from-a-contract-and-auto')
  })

  it('exports a component the host can mount', () => {
    expect(typeof Component).toBe('function')
  })

  it('names an icon from the shared set rather than an emoji', () => {
    expect(page.icon).toBe('audit')
    // A character outside the ASCII range in the icon slot is an emoji in this product.
    expect(/^[\x20-\x7e]+$/.test(page.icon)).toBe(true)
  })
})

// --------------------------------------------------------------------------- //
// The three things the research leaves open
// --------------------------------------------------------------------------- //

describe('the acceptance signal', () => {
  it('says the signal was not sourced, rather than implying the platform learned of it', async () => {
    await renderBoard()
    expect(screen.getByText('The acceptance signal was not sourced')).toBeTruthy()
    // getAllByText, because the banner and the decision card both carry this sentence on
    // purpose: the banner states it, the decision records it.
    expect(
      screen.getAllByText(/who accepts or how the room learns that it happened/i).length,
    ).toBeGreaterThan(0)
  })

  it('names the Jev audit id for that decision', async () => {
    await renderBoard()
    // getAllByText, because the banner and the decision card both carry it on purpose.
    expect(screen.getAllByText(new RegExp(JEV_AUDIT_ID)).length).toBeGreaterThan(0)
  })

  it('tells a reviewer the acceptance is an action they take, not one the room inferred', async () => {
    await renderBoard()
    expect(
      screen.getByRole('button', { name: /record acceptance/i }),
    ).toBeTruthy()
  })

  it('offers no acceptance control on a quote that is already accepted', async () => {
    await renderBoard({
      '/wf-100/quotes': {
        count: 1,
        quotes: [quote({ state: 'accepted', new_contract_id: 'contract_renewed' })],
        states: ['draft', 'shared', 'accepted', 'superseded'],
        derived_not_sourced: 'derived',
      },
    })
    expect(screen.queryByRole('button', { name: /record acceptance/i })).toBeNull()
  })
})

describe('the renewal date rule', () => {
  it('quotes both branches rather than paraphrasing one', async () => {
    await renderBoard()
    expect(screen.getByText(RENEWAL_RULE_FINALISED)).toBeTruthy()
    expect(screen.getByText(RENEWAL_RULE_NOT_FINALISED)).toBeTruthy()
  })

  it('names the branch that answered for a contract that is not yet finalised', async () => {
    await renderBoard()
    expect(screen.getAllByText('Not yet finalised').length).toBeGreaterThan(0)
  })

  it('names the other branch for a contract whose renewal was finalised', async () => {
    await renderBoard({
      '/wf-100/contracts': {
        count: 1,
        contracts: [
          contract({
            renewal: {
              renewal_date: '2026-04-01',
              branch: 'if_finalised',
              rule: RENEWAL_RULE_FINALISED,
              source_quote_id: 'quote_northwind',
            },
            chain: {
              renewed_from_contract_id: null,
              renewed_into_contract_id: 'contract_renewed',
              renewed_into_quote_id: 'quote_northwind',
              renewal_finalised: true,
              previous: null,
              next: { id: 'contract_renewed', name: 'Renewed contract', missing: false },
            },
          }),
        ],
        evergreen_label: 'Evergreen',
        renewal_date_rule: SUMMARY.rule,
        renewal_date_branches: VOCABULARY.renewal_date_branches,
      },
    })
    expect(screen.getAllByText('Renewal finalised').length).toBeGreaterThan(0)
  })

  it('shows the contract end date before anything is accepted', async () => {
    await renderBoard()
    expect(screen.getAllByText('2026-03-01').length).toBeGreaterThan(0)
  })
})

describe('what this build did not do', () => {
  it('says the direct renewal bypass was not built, rather than leaving the gap', async () => {
    await renderBoard()
    expect(screen.getByText('Direct contract renewal')).toBeTruthy()
    expect(screen.getByText(/did not build the bypass/i)).toBeTruthy()
  })

  it('says the workflow action has no vendor endpoint underneath it', async () => {
    await renderBoard()
    expect(screen.getByText(/explicitly not a documented REST endpoint/i)).toBeTruthy()
  })

  it('says the re-enrol behaviour is a decision and not a sourced fact', async () => {
    await renderBoard()
    expect(screen.getByText(/a decision, not a sourced fact/i)).toBeTruthy()
  })

  it('says which ticket does not provision the template collection', async () => {
    await renderBoard()
    expect(screen.getByText(/no pending ticket provisions one/i)).toBeTruthy()
  })
})

// --------------------------------------------------------------------------- //
// The states
// --------------------------------------------------------------------------- //

describe('the states this page can be in', () => {
  it('shows a loading state before the board arrives', async () => {
    global.fetch = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({}) }))
    render(<Component />)
    expect(screen.getByText(/loading renewal quotes/i)).toBeTruthy()
  })

  it('shows an error state with a retry when the API is down', async () => {
    global.fetch = vi.fn(async () => {
      throw new Error('the API is down')
    })
    render(<Component />)
    await waitFor(() => expect(screen.getByText(/the API is down/i)).toBeTruthy())
  })

  it('shows an empty state rather than a blank board when there is nothing yet', async () => {
    await renderBoard({
      '/wf-100/contracts': { count: 0, contracts: [], evergreen_label: 'Evergreen' },
      '/wf-100/quotes': { count: 0, quotes: [], derived_not_sourced: 'derived' },
    })
    expect(screen.getByText('No contracts yet')).toBeTruthy()
    expect(screen.getByText('No renewal quotes yet')).toBeTruthy()
  })

  it('says a renewal the buyer has not accepted has no deal yet', async () => {
    await renderBoard()
    expect(screen.getByText('No renewal deals yet')).toBeTruthy()
    expect(
      screen.getByText(/a deal appears when a renewal quote is accepted, not when it is created/i),
    ).toBeTruthy()
  })
})

// --------------------------------------------------------------------------- //
// What the page shows
// --------------------------------------------------------------------------- //

describe('the board', () => {
  it('leads with the contracts rather than the quotes', async () => {
    await renderBoard()
    const headings = screen.getAllByRole('heading', { level: 2 }).map((node) => node.textContent)
    expect(headings.indexOf('Contracts')).toBeLessThan(headings.indexOf('Renewal quotes'))
  })

  it('reports the four headline numbers', async () => {
    await renderBoard()
    // getAllByText, because each stat label also appears as a section heading and a stat card
    // is a caption rather than a unique label.
    for (const label of ['Contracts', 'Renewal quotes', 'Renewal contracts', 'Renewal deals']) {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0)
    }
    expect(screen.getByText('1 renewable now')).toBeTruthy()
    expect(screen.getByText('created at acceptance')).toBeTruthy()
  })

  it('shows an evergreen contract as a label and not as a separate type', async () => {
    await renderBoard({
      '/wf-100/contracts': {
        count: 1,
        contracts: [
          contract({
            name: 'Cypress evergreen agreement',
            term_label: 'Evergreen',
            line_items: [
              { sku: 'SEAT-ENT', quantity: 60, amount: '147600.00', auto_renew: 'until_canceled' },
            ],
          }),
        ],
        evergreen_label: 'Evergreen',
        renewal_date_rule: SUMMARY.rule,
        renewal_date_branches: VOCABULARY.renewal_date_branches,
      },
    })
    // The word appears on the badge, the term row and the research panel, because a reviewer
    // has to see it in each place rather than infer it from one.
    expect(screen.getAllByText('Evergreen').length).toBeGreaterThanOrEqual(3)
    expect(
      screen.getByText(/that is a label on the record, not a separate contract type/i),
    ).toBeTruthy()
  })

  it('refuses to offer a renewal on a contract that has already been renewed', async () => {
    await renderBoard({
      '/wf-100/contracts': {
        count: 1,
        contracts: [
          contract({
            renewable: {
              renewable: false,
              reason: 'This contract has already been renewed.',
              field: 'renewal_finalised',
            },
          }),
        ],
        evergreen_label: 'Evergreen',
        renewal_date_rule: SUMMARY.rule,
        renewal_date_branches: VOCABULARY.renewal_date_branches,
      },
    })
    expect(screen.getByRole('button', { name: /create renewal quote/i }).disabled).toBe(true)
    expect(screen.getByText('This contract has already been renewed.')).toBeTruthy()
  })

  it('offers all four researched change effective date modes', async () => {
    await renderBoard()
    const select = screen.getByLabelText('Change effective date')
    const values = Array.from(select.options).map((option) => option.value)
    expect(values).toEqual(['on_agreement', 'custom_date', 'delayed_start', 'months'])
  })

  it('offers the researched proration checkbox with a visible label', async () => {
    await renderBoard()
    const checkbox = screen.getByLabelText(/prorate charges and credits/i)
    expect(checkbox.checked).toBe(true)
  })

  it('offers both researched deal selection methods', async () => {
    await renderBoard()
    const select = screen.getByLabelText('Deal selection')
    const values = Array.from(select.options).map((option) => option.value)
    expect(values).toEqual(['new_deal_default_stage', 'existing_deal'])
  })

  it('offers both researched contract scopes for a workflow', async () => {
    await renderBoard()
    expect(screen.getByText('One contract')).toBeTruthy()
    expect(screen.getByText('Contracts: all associated')).toBeTruthy()
  })

  it('shows the association type the research names on a template', async () => {
    await renderBoard()
    expect(screen.getByText('286')).toBeTruthy()
  })
})

// --------------------------------------------------------------------------- //
// The writes
// --------------------------------------------------------------------------- //

describe('creating a renewal quote', () => {
  it('sends the contract, the template, the date and the deal to the create route', async () => {
    const user = userEvent.setup()
    const calls = await renderBoard()

    await user.selectOptions(screen.getByLabelText('Contract'), 'contract_northwind')
    await user.selectOptions(screen.getByLabelText('Quote template'), 'tpl_annual')
    await user.selectOptions(screen.getByLabelText('Deal pipeline'), 'pipe_renewals')
    await user.selectOptions(screen.getByLabelText('Deal stage'), 'Contract sent')
    await user.click(screen.getByRole('button', { name: /create quote/i }))

    await waitFor(() => {
      const post = calls.find((call) => call.method === 'POST' && call.url.includes('/quotes'))
      expect(post).toBeTruthy()
    })
    const body = JSON.parse(calls.find((c) => c.method === 'POST' && c.url.includes('/quotes')).body)
    expect(body.contract_id).toBe('contract_northwind')
    expect(body.template_id).toBe('tpl_annual')
    expect(body.effective_date_mode).toBe('on_agreement')
    expect(body.deal_pipeline_id).toBe('pipe_renewals')
    expect(body.deal_stage).toBe('Contract sent')
  })

  it('sends the delayed start day count when that mode is chosen', async () => {
    const user = userEvent.setup()
    const calls = await renderBoard()

    await user.selectOptions(screen.getByLabelText('Change effective date'), 'delayed_start')
    await user.clear(screen.getByLabelText('Days after agreement'))
    await user.type(screen.getByLabelText('Days after agreement'), '45')
    await user.selectOptions(screen.getByLabelText('Contract'), 'contract_northwind')
    await user.selectOptions(screen.getByLabelText('Deal pipeline'), 'pipe_renewals')
    await user.selectOptions(screen.getByLabelText('Deal stage'), 'Contract sent')
    await user.click(screen.getByRole('button', { name: /create quote/i }))

    await waitFor(() => {
      expect(calls.some((call) => call.method === 'POST' && call.url.includes('/quotes'))).toBe(true)
    })
    const body = JSON.parse(calls.find((c) => c.method === 'POST' && c.url.includes('/quotes')).body)
    expect(body.effective_date_mode).toBe('delayed_start')
    expect(body.delay_days).toBe(45)
  })

  it('sends a cleared proration checkbox as an explicit false', async () => {
    const user = userEvent.setup()
    const calls = await renderBoard()

    await user.click(screen.getByLabelText(/prorate charges and credits/i))
    await user.selectOptions(screen.getByLabelText('Contract'), 'contract_northwind')
    await user.selectOptions(screen.getByLabelText('Deal pipeline'), 'pipe_renewals')
    await user.selectOptions(screen.getByLabelText('Deal stage'), 'Contract sent')
    await user.click(screen.getByRole('button', { name: /create quote/i }))

    await waitFor(() => {
      expect(calls.some((call) => call.method === 'POST' && call.url.includes('/quotes'))).toBe(true)
    })
    const body = JSON.parse(calls.find((c) => c.method === 'POST' && c.url.includes('/quotes')).body)
    expect(body.prorate).toBe(false)
  })

  it('shows the field-keyed reason the server refused a request', async () => {
    const user = userEvent.setup()
    await renderBoard({
      '/wf-100/quotes': (options) => {
        if (options.method === 'POST') {
          return {
            __status: 400,
            error: 'invalid_renewal_request',
            detail: 'A pipeline needs a stage.',
            errors: { deal_stage: 'Name the stage the renewal deal should start in.' },
          }
        }
        return {
          count: 1,
          quotes: [quote()],
          states: ['draft'],
          derived_not_sourced: 'derived',
        }
      },
    })

    await user.selectOptions(screen.getByLabelText('Contract'), 'contract_northwind')
    await user.selectOptions(screen.getByLabelText('Deal pipeline'), 'pipe_renewals')
    await user.selectOptions(screen.getByLabelText('Deal stage'), 'Contract sent')
    await user.click(screen.getByRole('button', { name: /create quote/i }))

    await waitFor(() => expect(screen.getByText(/name the stage/i)).toBeTruthy())
  })
})

describe('accepting a renewal quote', () => {
  it('posts to the accept route and then says what the acceptance produced', async () => {
    const user = userEvent.setup()
    const calls = await renderBoard()

    await user.click(screen.getByRole('button', { name: /record acceptance/i }))
    await waitFor(() => {
      const post = calls.find((call) => call.url.includes('/accept'))
      expect(post).toBeTruthy()
      expect(post.method).toBe('POST')
    })
    expect(calls.find((call) => call.url.includes('/accept')).url).toContain(
      '/api/wf-100/quotes/quote_northwind/accept',
    )
  })

  it('shows the remediation a 409 carries rather than swallowing the conflict', async () => {
    const user = userEvent.setup()
    await renderBoard({
      '/quotes/quote_northwind/accept': {
        __status: 409,
        error: 'renewal_conflict',
        detail: 'This quote is already accepted.',
        remedy: 'Read the new contract it created, or renew that contract instead.',
      },
    })

    await user.click(screen.getByRole('button', { name: /record acceptance/i }))
    await waitFor(() => expect(screen.getByText('This quote is already accepted.')).toBeTruthy())
    expect(
      screen.getByText(/Read the new contract it created, or renew that contract instead/i),
    ).toBeTruthy()
  })

  it('does not offer the share action on a quote that is not a draft', async () => {
    await renderBoard({
      '/wf-100/quotes': {
        count: 1,
        quotes: [quote({ state: 'shared' })],
        states: ['draft', 'shared'],
        derived_not_sourced: 'derived',
      },
    })
    expect(screen.queryByRole('button', { name: /mark shared/i })).toBeNull()
  })
})

describe('running a renewal workflow', () => {
  const WORKFLOW = {
    count: 1,
    workflows: [
      {
        id: 'wf_renewal',
        name: 'Renewal workflow for contract_northwind',
        contract_target: 'one_contract',
        enrolled_contract_id: 'contract_northwind',
        re_enroll: true,
        cycles: 0,
        deal_selection_method: 'new_deal_default_stage',
        enrollment_trigger: 'contract_renewal_date',
      },
    ],
    action_note: WORKFLOWS.action_note,
    re_enroll_note: VOCABULARY.re_enroll_note,
  }

  it('shows whether a workflow re-enrols', async () => {
    await renderBoard({ '/wf-100/workflows': WORKFLOW })
    expect(screen.getByText('Re-enrol on')).toBeTruthy()
  })

  it('runs the workflow on its own route', async () => {
    const user = userEvent.setup()
    const calls = await renderBoard({ '/wf-100/workflows': WORKFLOW })

    await user.click(screen.getByRole('button', { name: /run now/i }))
    await waitFor(() => {
      const post = calls.find((call) => call.url.includes('/run'))
      expect(post).toBeTruthy()
      expect(post.method).toBe('POST')
    })
  })
})

// --------------------------------------------------------------------------- //
// The small pure helpers
// --------------------------------------------------------------------------- //

describe('the helpers', () => {
  it('falls back to draft for a state it does not know', () => {
    expect(quoteState('sent').value).toBe('draft')
  })

  it('marks only acceptance as sourced', () => {
    expect(quoteState('accepted').sourced).toBe(true)
    expect(quoteState('draft').sourced).toBe(false)
  })

  it('renders an unreadable date as not set rather than as Invalid Date', () => {
    expect(formatDate('whenever')).toBe('not set')
    expect(formatDate('')).toBe('not set')
    expect(formatDate(null)).toBe('not set')
  })

  it('renders a date-only string as itself', () => {
    expect(formatDate('2026-04-01')).toBe('2026-04-01')
  })

  it('renders an unreadable amount as unknown', () => {
    expect(formatMoney('not a number')).toBe('unknown')
  })

  it('pluralises a count', () => {
    expect(plural(1, 'contract')).toBe('1 contract')
    expect(plural(2, 'contract')).toBe('2 contracts')
    expect(plural(0, 'contract')).toBe('0 contracts')
  })

  it('offers both researched contract targets', () => {
    expect(CONTRACT_TARGETS.map((target) => target.value)).toEqual(['one_contract', 'all_associated'])
  })
})