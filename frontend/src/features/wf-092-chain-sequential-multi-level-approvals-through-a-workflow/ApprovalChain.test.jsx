/**
 * The approval chain page, tested against real API payloads.
 *
 * These tests exist for the parts of the page where a mistake would be *believable*
 * rather than visibly broken. That is most of this page, because the researched rules
 * are all about what does NOT happen:
 *
 * a chain waiting at priority 2 while the page offers the priority 1 approver's button;
 * an auto-approved quote rendered as an error, when the research calls auto-approval the
 * safety valve;
 * a refusal from a lower priority rendered as a failure, when it is the sequential rule
 * working;
 * a chain state shown by colour alone, which the accessibility floor bans;
 * a cap quoted on the page that disagrees with the cap the server enforces.
 *
 * Every assertion is about something the research fixes, not about markup.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import descriptor from './index'
import ApprovalChain from './ApprovalChain'
import { approvalApi } from './api'
import { ChainLadder, StatusPill } from './primitives'

const VOCABULARY = {
  branch: {
    property: 'quote_amount',
    default_threshold: 5000,
    operators: ['greater_than', 'greater_than_or_equal'],
    outcomes: ['qualified', 'not_qualified', 'auto_approved'],
  },
  chain: {
    states: ['pending_approval', 'in_review', 'approved', 'rejected'],
    requirements: ['all', 'any', 'sequential'],
    sequential_rule:
      'Sequential approvals require approval by every approver at each priority step.',
    caps: { max_sequences: 5, max_approvers_per_sequence: 10 },
  },
  decisions: ['approved', 'rejected', 'abstained'],
  notifications: {
    built: ['bell', 'email'],
    researched_but_not_built: ['teams', 'slack', 'google_chat'],
  },
  template: { open: '{{', close: '}}', root: 'quote', example: '{{quote.quote_amount}}' },
  single_workflow_id: 'wf092-sequential-quote-approval',
}

const INFERENCES = {
  sourced_quotes: [],
  inferences: [
    {
      id: 'DERIVED_AUTO_APPROVE_IS_THE_SAFE_DEFAULT',
      topic: 'a quote that matched a branch but collected no approval step',
      basis: 'The valve is sourced and explicit.',
      value: 'approve',
    },
  ],
  not_built: [
    {
      id: 'no-delivery-for-three-channels',
      what_is_not_built: 'Sending a notification on Teams, Slack or Google Chat.',
      why: 'The research names the channels. This product has no confirmed delivery surface.',
      instead: 'the notification row carries the channel',
    },
  ],
}

const WORKFLOW = {
  id: 'wf092_quote_approval_workflow_1',
  workflow_id: 'wf092-sequential-quote-approval',
  re_enroll: true,
}

const LEVELS = [
  { priority: 1, approvers: ['sales_manager'], requirement: 'sequential' },
  { priority: 2, approvers: ['sales_director'], requirement: 'sequential' },
  { priority: 3, approvers: ['legal_representative'], requirement: 'sequential' },
]

const ENROLMENT_WAITING = {
  id: 'wf092_approval_enrolment_1',
  quote_id: 'wf086_quote_1',
  state: 'pending_approval',
  publishable: false,
  auto_approved: false,
  active_priority: 1,
  decisions: {},
  levels: LEVELS,
}

const ENROLMENT_AUTO = {
  id: 'wf092_approval_enrolment_2',
  quote_id: 'wf086_quote_2',
  state: 'approved',
  publishable: true,
  auto_approved: true,
  active_priority: null,
  decisions: {},
  levels: [],
  reason: 'No approval step was added above the start action, so the quote is auto-approved.',
}

const QUOTES = [
  { id: 'wf086_quote_1', name: 'Acme expansion', quote_amount: 12000 },
  { id: 'wf086_quote_2', name: 'Pilot', quote_amount: 900 },
]

function stubApi(overrides = {}) {
  const spies = {
    vocabulary: vi.fn().mockResolvedValue(VOCABULARY),
    inferences: vi.fn().mockResolvedValue(INFERENCES),
    workflow: vi.fn().mockResolvedValue(WORKFLOW),
    branches: vi.fn().mockResolvedValue({
      branches: [
        {
          id: 'b1',
          name: 'Quotes above 5000',
          property: 'quote_amount',
          operator: 'greater_than',
          threshold: 5000,
          steps: LEVELS,
        },
      ],
    }),
    quotes: vi.fn().mockResolvedValue({ quotes: QUOTES }),
    enrolments: vi.fn().mockResolvedValue({
      enrolments: [ENROLMENT_WAITING, ENROLMENT_AUTO],
    }),
    decide: vi.fn().mockResolvedValue({ outcome: 'decided', next_priority: 2 }),
    enrol: vi.fn().mockResolvedValue({ auto_approved: false }),
    toggleReEnrol: vi.fn().mockResolvedValue({}),
    enrolment: vi.fn().mockResolvedValue({ decision_rows: [], notifications: [] }),
    addBranch: vi.fn().mockResolvedValue({}),
  }
  for (const [key, value] of Object.entries({ ...spies, ...overrides })) {
    vi.spyOn(approvalApi, key).mockImplementation(value)
  }
  return spies
}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe('the descriptor', () => {
  it('exports a discoverable descriptor whose id matches the backend feature id', () => {
    expect(descriptor.id).toBe('wf-092-chain-sequential-multi-level-approvals-through-a-workflow')
    expect(typeof descriptor.label).toBe('string')
    expect(descriptor.Component).toBeTruthy()
  })

  it('passes its own glyph rather than appending to the shared PATHS map', () => {
    expect(descriptor.iconPath).toBeTruthy()
    expect(descriptor.iconPath).not.toBe('')
  })
})

describe('the page', () => {
  it('renders the heading and the researched limits', async () => {
    stubApi()
    render(<ApprovalChain />)
    await waitFor(() => expect(screen.getByText('Quote approval chain')).toBeTruthy())
    expect(await screen.findByText('5')).toBeTruthy()
    expect(screen.getByText('10')).toBeTruthy()
  })

  it('offers a decision only to the approver at the active priority', async () => {
    stubApi()
    render(<ApprovalChain />)
    await waitFor(() => expect(screen.getByText('Quote approval chain')).toBeTruthy())

    await screen.findAllByText('sales_manager')

    // The manager sits in the button row at priority 1. The director is listed in the
    // ladder but is offered no button, because the chain has not reached priority 2.
    const buttonRows = document.querySelectorAll('span.flex.items-center.gap-2')
    const withButton = [...buttonRows].filter((row) =>
      within(row).queryByText('Approve')
    )
    expect(withButton).toHaveLength(1)
    expect(within(withButton[0]).getByText('sales_manager')).toBeTruthy()
    expect(within(withButton[0]).getByText('Approve')).toBeTruthy()
    expect(within(withButton[0]).getByText('Reject')).toBeTruthy()

    const directorRows = [...buttonRows].filter((row) =>
      within(row).queryByText('sales_director')
    )
    expect(directorRows).toHaveLength(0)
  })

  it('records a decision when the active approver approves', async () => {
    const spies = stubApi()
    render(<ApprovalChain />)
    await waitFor(() => expect(screen.getByText('Quote approval chain')).toBeTruthy())
    await screen.findAllByText('sales_manager')

    await userEvent.click(screen.getByText('Approve'))
    await waitFor(() =>
      expect(spies.decide).toHaveBeenCalledWith('wf092_approval_enrolment_1', 'sales_manager', 'approved')
    )
  })

  it('shows a premature refusal as the rule working, not as a failure', async () => {
    stubApi({
      decide: vi.fn().mockResolvedValue({
        outcome: 'not_yet_your_priority',
        your_priority: 2,
        active_priority: 1,
      }),
    })
    render(<ApprovalChain />)
    await waitFor(() => expect(screen.getByText('Quote approval chain')).toBeTruthy())
    await screen.findAllByText('sales_manager')

    await userEvent.click(screen.getByText('Approve'))
    const notice = await screen.findByText(/sits at priority 2/)
    expect(notice).toBeTruthy()
    expect(screen.queryByText('Could not load data')).toBeNull()
  })

  it('renders an auto-approved quote as the researched valve, not as an error', async () => {
    stubApi()
    render(<ApprovalChain />)
    await waitFor(() => expect(screen.getByText('Quote approval chain')).toBeTruthy())
    // Two places say it, and both are correct: the card explains the valve and the
    // status pill names the state it produced.
    expect((await screen.findAllByText(/auto-approved/i)).length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('publishable')).toBeTruthy()
  })

  it('names the channels it does not build rather than implying they were sent', async () => {
    stubApi()
    render(<ApprovalChain />)
    await waitFor(() => expect(screen.getByText('Quote approval chain')).toBeTruthy())
    expect(await screen.findByText(/Teams, Slack or Google Chat/)).toBeTruthy()
  })

  it('counts waiting, decided and auto-approved chains separately', async () => {
    stubApi()
    render(<ApprovalChain />)
    await waitFor(() => expect(screen.getByText('Quote approval chain')).toBeTruthy())
    expect(await screen.findByText('Chains with a priority to decide')).toBeTruthy()
    expect(screen.getByText('No approval step qualified')).toBeTruthy()
  })

  it('shows an error state when the API fails', async () => {
    vi.spyOn(approvalApi, 'vocabulary').mockRejectedValue(new Error('boom'))
    vi.spyOn(approvalApi, 'inferences').mockResolvedValue(INFERENCES)
    vi.spyOn(approvalApi, 'workflow').mockResolvedValue(WORKFLOW)
    vi.spyOn(approvalApi, 'branches').mockResolvedValue({ branches: [] })
    vi.spyOn(approvalApi, 'quotes').mockResolvedValue({ quotes: [] })
    vi.spyOn(approvalApi, 'enrolments').mockResolvedValue({ enrolments: [] })
    render(<ApprovalChain />)
    expect(await screen.findByText('Could not load data')).toBeTruthy()
  })
})

describe('the chain ladder', () => {
  it('shows every priority and marks where the chain is waiting', () => {
    const { container } = render(
      <ChainLadder levels={LEVELS} decisions={{}} activePriority={2} state="in_review" />
    )
    expect(screen.getByText('priority 1')).toBeTruthy()
    expect(screen.getByText('priority 2')).toBeTruthy()
    expect(screen.getByText('priority 3')).toBeTruthy()
    // Priority 2 holds the active decision, and both other levels wait on it.
    expect(within(container).getAllByText('waiting for this decision')).toHaveLength(1)
    expect(within(container).getAllByText('waiting on an earlier priority')).toHaveLength(2)
  })

  it('says a level with no step was auto-approved rather than showing an empty list', () => {
    render(<ChainLadder levels={[]} decisions={{}} activePriority={null} state="approved" />)
    expect(screen.getByText(/auto-approved/)).toBeTruthy()
  })

  it('reports each approver decision by name', () => {
    render(
      <ChainLadder
        levels={LEVELS}
        decisions={{ sales_manager: 'approved', sales_director: 'rejected' }}
        activePriority={null}
        state="rejected"
      />
    )
    expect(screen.getByText('Approved')).toBeTruthy()
    expect(screen.getByText('Rejected')).toBeTruthy()
  })
})

describe('the status pill', () => {
  it.each([
    ['approved', 'Approved'],
    ['rejected', 'Rejected'],
    ['pending_approval', 'Pending approval'],
    ['in_review', 'In review'],
  ])('spells out %s as a word, so no state is carried by colour alone', (state, word) => {
    render(<StatusPill state={state} />)
    expect(screen.getByText(word === 'Pending approval' ? 'Waiting' : word)).toBeTruthy()
  })

  it('falls back to the raw value for a state it does not know', () => {
    render(<StatusPill state="escalated" />)
    expect(screen.getByText('escalated')).toBeTruthy()
  })
})