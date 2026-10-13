import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import {
  actionLabel,
  actionOutcomeLabel,
  shapeLabel,
  skipReasonLabel,
  stateBadge,
  triggerKindLabel,
  viaLabel,
} from './api'
import descriptor, { ConversationTicket } from './index.jsx'
import { ActionRow, Notice, Quote, TicketRow, WebhookRow } from './primitives'

/**
 * Tests for the pieces of WF-110 that can get quietly wrong.
 *
 * Four claims are pinned here, and each is one the specification makes:
 *
 * 1. `resolved` and `closed` are different words, because they are two events.
 * 2. A tracker-created ticket says so, because the trigger never fires for it.
 * 3. A skipped action is a decision, not a failure, and says why.
 * 4. A status is never conveyed by colour alone: the words are always present.
 *
 * The page itself needs a stubbed transport, so this covers its building blocks
 * and its descriptor rather than driving eight fetches.
 */

describe('the descriptor', () => {
  it('exports the shape the feature host discovers', () => {
    expect(descriptor.id).toBe('wf-110-conversation-ticket')
    expect(typeof descriptor.label).toBe('string')
    expect(descriptor.Component).toBe(ConversationTicket)
  })

  it('uses an icon name that exists in the shared set', () => {
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

  it('carries a custom nav glyph rather than editing the shared PATHS map', () => {
    expect(typeof descriptor.iconPath).toBe('string')
    expect(descriptor.iconPath.length).toBeGreaterThan(10)
  })
})

describe('resolved and closed are two different events', () => {
  it('keeps the two states as two different words', () => {
    expect(stateBadge('resolved').label).not.toBe(stateBadge('closed').label)
  })

  it('names the fact each state carries', () => {
    expect(stateBadge('resolved').label).toMatch(/state transition/i)
    expect(stateBadge('closed').label).toMatch(/separate event/i)
  })

  it('falls back to a real label rather than rendering nothing', () => {
    expect(stateBadge('something-new').label).toBe('something-new')
    expect(stateBadge(undefined).label).toBe('Unknown')
  })
})

describe('a tracker ticket says the trigger never fires', () => {
  it('labels the tracker origin with the exclusion', () => {
    expect(viaLabel('tracker')).toMatch(/will not fire/i)
  })

  it('labels the converted and API origins in words', () => {
    expect(viaLabel('conversation')).toMatch(/converted/i)
    expect(viaLabel('api')).toMatch(/REST API/i)
  })

  it('labels the tracker skip reason', () => {
    expect(skipReasonLabel('tracker_created_ticket')).toMatch(/never fire/i)
  })
})

describe('a skip is a decision, not a failure', () => {
  it('gives every action outcome a sentence', () => {
    expect(actionOutcomeLabel('ran')).toBe('Ran')
    expect(actionOutcomeLabel('skipped_not_converted')).toMatch(/not converted/i)
    expect(actionOutcomeLabel('skipped_unknown_team')).toMatch(/no owning team/i)
    expect(actionOutcomeLabel('skipped_unknown_tag')).toMatch(/tag is not in the workspace/i)
  })

  it('labels the two trigger kinds', () => {
    expect(triggerKindLabel('ticket_created')).toBe('A ticket is created')
    expect(triggerKindLabel('ticket_state_changed')).toMatch(/changes the state/i)
  })

  it('labels each action kind', () => {
    expect(actionLabel('notify_team')).toMatch(/Slack/i)
    expect(actionLabel('tag_conversation')).toMatch(/linked conversation/i)
  })
})

describe('the two payload shapes are labelled', () => {
  it('names the nested shape and the flat shape', () => {
    expect(shapeLabel('nested_under_ticket')).toMatch(/nested/i)
    expect(shapeLabel('flat_item')).toMatch(/data.item/i)
    expect(shapeLabel('nested_under_ticket')).not.toBe(shapeLabel('flat_item'))
  })
})

describe('an action row shows its outcome in words', () => {
  it('renders the action and its skipped reason', () => {
    render(
      <ul>
        <ActionRow
          action={{ kind: 'apply_sla', outcome: 'skipped_not_converted', policy: 'Escalation response' }}
          actionLabel={actionLabel}
          outcomeLabel={actionOutcomeLabel}
        />
      </ul>,
    )
    expect(screen.getByText('Apply an SLA')).toBeInTheDocument()
    expect(screen.getByText(/not converted/i)).toBeInTheDocument()
    expect(screen.getByText('Skipped')).toBeInTheDocument()
  })
})

describe('a ticket row names its converted state', () => {
  it('renders the subject, the team and the converted word', () => {
    render(
      <ul>
        <TicketRow
          ticket={{
            id: 't1',
            subject: 'Platform availability',
            ticket_type: 'escalation',
            owning_team: 'escalations',
            converted: true,
          }}
          selected={false}
          onSelect={() => {}}
        />
      </ul>,
    )
    expect(screen.getByText('Platform availability')).toBeInTheDocument()
    expect(screen.getByText(/escalation \/ escalations/)).toBeInTheDocument()
    expect(screen.getByText('Converted')).toBeInTheDocument()
  })
})

describe('a webhook row names its shape and its parsed ticket', () => {
  it('renders the topic and the parsed ticket id', () => {
    render(
      <ul>
        <WebhookRow
          webhook={{
            id: 'w1',
            parsed_ticket: { id: 't1' },
            data: { shape: 'nested_under_ticket', payload: { type: 'ticket.closed' } },
          }}
        />
      </ul>,
    )
    expect(screen.getByText('ticket.closed')).toBeInTheDocument()
    expect(screen.getByText(/ticket t1/)).toBeInTheDocument()
  })
})

describe('a notice and a quote carry their words', () => {
  it('renders a text label beside the tone', () => {
    render(<Notice tone="warning">A tracker ticket never fires the trigger.</Notice>)
    expect(screen.getByText('Warning')).toBeInTheDocument()
    expect(screen.getByText('A tracker ticket never fires the trigger.')).toBeInTheDocument()
  })

  it('renders the sourced sentence', () => {
    render(<Quote source="https://example.test">Only background actions are available.</Quote>)
    expect(screen.getByText('Only background actions are available.')).toBeInTheDocument()
    expect(screen.getByText('https://example.test')).toBeInTheDocument()
  })
})
