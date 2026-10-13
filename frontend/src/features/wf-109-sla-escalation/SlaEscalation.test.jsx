import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { actionLabel, alertLabel, formatSeconds, levelLabel, triggerLabel } from './api'
import descriptor, { SlaEscalation } from './index.jsx'
import { AlertResult, LevelBadge } from './primitives'

/**
 * Tests for the pieces of WF-109 that can get quietly wrong.
 *
 * Three claims are pinned here, and each is one the specification makes:
 *
 * 1. A status is never conveyed by colour alone: every chip renders its words.
 * 2. A failed alert is a word and a status code, because the spec reports `success`,
 *    `http_status` and `error_type`.
 * 3. A countdown is a moment a person reads, and an absent target reads as words rather
 *    than a false zero.
 *
 * The page itself needs a stubbed transport, so this covers its building blocks and its
 * descriptor rather than driving five fetches.
 */

describe('the descriptor', () => {
  it('exports the shape the feature host discovers', () => {
    expect(descriptor.id).toBe('wf-109-sla-escalation')
    expect(typeof descriptor.label).toBe('string')
    expect(descriptor.Component).toBe(SlaEscalation)
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

describe('a status is a word, not a colour', () => {
  it('labels each level', () => {
    expect(levelLabel('breach')).toBe('Breached')
    expect(levelLabel('pre_warning')).toBe('Pre-warning')
    expect(levelLabel(undefined)).toBe('Not escalated')
  })

  it('renders the words inside the level chip', () => {
    render(<LevelBadge level="breach">Breached</LevelBadge>)
    expect(screen.getByText('Breached')).toBeInTheDocument()
  })

  it('labels each trigger from the spec sentence', () => {
    expect(triggerLabel('before_breach')).toBe('Before SLA breach')
    expect(triggerLabel('on_breach')).toBe('When SLA breaches')
  })

  it('renders an alert failure as its words and its status', () => {
    render(
      <AlertResult alert={{ success: false, http_status: 400, error_type: 'request_configuration_error' }} />,
    )
    expect(screen.getByText(/Failed 400 request_configuration_error/)).toBeInTheDocument()
  })

  it('renders a successful alert as sent', () => {
    render(<AlertResult alert={{ success: true, http_status: 200 }} />)
    expect(screen.getByText('200 sent')).toBeInTheDocument()
  })
})

describe('an alert reads honestly', () => {
  it('states the failure reason from the error type', () => {
    expect(alertLabel({ success: false, error_type: 'request_configuration_error' })).toMatch(
      /request_configuration_error/,
    )
    expect(alertLabel({ success: true })).toBe('Alert sent')
    expect(alertLabel(null)).toBe('No alert')
  })
})

describe('a countdown is a moment a person reads', () => {
  it('formats hours, minutes and seconds', () => {
    expect(formatSeconds(3661)).toBe('1h 01m')
    expect(formatSeconds(125)).toBe('2m 05s')
    expect(formatSeconds(45)).toBe('45s')
  })

  it('renders words rather than a false zero', () => {
    expect(formatSeconds(null)).toBe('no target')
  })
})

describe('a run action reads as a sentence', () => {
  it('names the verb and the effect', () => {
    expect(actionLabel({ kind: 'tag', summary: "tagged the conversation 'sla breach'" })).toBe(
      "Tagged: tagged the conversation 'sla breach'",
    )
    expect(actionLabel({ kind: 'alert', summary: 'sent a Data Connector alert' })).toMatch(/Alerted/)
  })
})
