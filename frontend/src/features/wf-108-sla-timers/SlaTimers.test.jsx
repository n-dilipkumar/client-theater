import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { badgeLabel, metricStateLabel, ratePercent, timerStateLabel } from './api'
import descriptor, { SlaTimers } from './index.jsx'
import { SlaBadge, formatSeconds } from './primitives'

/**
 * Tests for the pieces of WF-108 that can get quietly wrong.
 *
 * Two claims are pinned here, and each is one the specification makes:
 *
 * 1. A status is never conveyed by colour alone: every badge renders its words.
 * 2. A rate with no decided metric reads as `n/a` rather than as a misleading `0%`.
 *
 * The page itself needs a stubbed transport, so this covers its building blocks and its
 * descriptor rather than driving five fetches.
 */

describe('the descriptor', () => {
  it('exports the shape the feature host discovers', () => {
    expect(descriptor.id).toBe('wf-108-sla-timers')
    expect(typeof descriptor.label).toBe('string')
    expect(descriptor.Component).toBe(SlaTimers)
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
  it('labels each of the three badges with the researched sentence', () => {
    expect(badgeLabel('grey')).toMatch(/more than 5 minutes/i)
    expect(badgeLabel('orange')).toMatch(/less than 5 minutes/i)
    expect(badgeLabel('red')).toMatch(/overdue/i)
  })

  it('renders the words inside the badge, so colour is never the only signal', () => {
    render(<SlaBadge badge="red">Overdue</SlaBadge>)
    expect(screen.getByText('Overdue')).toBeInTheDocument()
  })

  it('falls back to a real label rather than rendering nothing', () => {
    expect(badgeLabel(undefined)).toBe('No timer')
    expect(timerStateLabel('breached')).toBe('Breached')
    expect(timerStateLabel('something-new')).toBe('something-new')
    expect(metricStateLabel('not_applicable')).toBe('Not applicable')
  })
})

describe('a rate reads honestly', () => {
  it('renders a fraction as a whole percentage', () => {
    expect(ratePercent(0.5)).toBe('50%')
    expect(ratePercent(0.3333)).toBe('33%')
  })

  it('reads n/a rather than 0% when nothing has been decided', () => {
    expect(ratePercent(0, false)).toBe('n/a')
    expect(ratePercent(undefined, true)).toBe('n/a')
  })
})

describe('a countdown is a moment a person reads', () => {
  it('formats hours, minutes and seconds', () => {
    expect(formatSeconds(3661)).toBe('1h 01m')
    expect(formatSeconds(125)).toBe('2m 05s')
    expect(formatSeconds(45)).toBe('45s')
  })

  it('renders not applicable rather than a false zero', () => {
    expect(formatSeconds(null)).toBe('not applicable')
  })
})
