import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { outcomeLabel, seriesStateLabel, stateBadge } from './api'
import descriptor, { NurtureSeries } from './index.jsx'
import { BlockRow, FilterChip, Notice } from './primitives'

/**
 * Tests for the pieces of WF-105 that can get quietly wrong.
 *
 * Three claims are pinned here, and each is one the specification makes:
 *
 * 1. Re-entry and the sticky exit are different facts. An exited contact is
 *    labelled "cannot re-enter" rather than merged with "completed".
 * 2. A skip is a decision, not a failure. Each entry outcome renders as words.
 * 3. A status is never conveyed by colour alone: the words are always present.
 *
 * The page itself needs a stubbed transport, so this covers its building blocks
 * and its descriptor rather than driving six fetches.
 */

describe('the descriptor', () => {
  it('exports the shape the feature host discovers', () => {
    expect(descriptor.id).toBe('wf-105-reenter-series-and-tag')
    expect(typeof descriptor.label).toBe('string')
    expect(descriptor.Component).toBe(NurtureSeries)
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

describe('an enrollment state is a word, not a colour', () => {
  it('labels each state with the fact a reader needs', () => {
    expect(stateBadge('enrolled').label).toBe('Enrolled')
    expect(stateBadge('completed').label).toBe('Completed')
    expect(stateBadge('exited').label).toMatch(/cannot re-enter/i)
  })

  it('keeps exited and completed as different words', () => {
    expect(stateBadge('exited').label).not.toBe(stateBadge('completed').label)
  })

  it('falls back to a real label rather than rendering nothing', () => {
    expect(stateBadge('something-new').label).toBe('something-new')
    expect(stateBadge(undefined).label).toBe('Unknown')
  })
})

describe('a skip is a decision, not a failure', () => {
  it('gives every entry outcome a sentence', () => {
    expect(outcomeLabel('enrolled')).toBe('Enrolled')
    expect(outcomeLabel('reentered')).toBe('Re-entered')
    expect(outcomeLabel('skipped_exited_sticky')).toMatch(/cannot re-enter/i)
    expect(outcomeLabel('skipped_no_match')).toMatch(/did not match/i)
  })

  it('labels the re-entry toggle being off', () => {
    expect(outcomeLabel('skipped_reenter_disabled')).toMatch(/re-entry is off/i)
  })

  it('labels a series state', () => {
    expect(seriesStateLabel('live')).toBe('Live')
    expect(seriesStateLabel('draft')).toBe('Draft')
  })
})

describe('a filter is shown as words', () => {
  it('renders field is value', () => {
    render(<FilterChip filter={{ field: 'tags', value: 'ExitUpgradeSeries' }} />)
    expect(screen.getByText('tags')).toBeInTheDocument()
    expect(screen.getByText('is')).toBeInTheDocument()
    expect(screen.getByText('ExitUpgradeSeries')).toBeInTheDocument()
  })
})

describe('a path block names its kind', () => {
  it('shows the tag a tag block selects', () => {
    render(
      <ul>
        <BlockRow
          block={{ kind: 'tag', title: 'Tag the upgraded contact', tag: 'Upgraded after Series' }}
          position={2}
          last
        />
      </ul>,
    )
    expect(screen.getByText('Tag the upgraded contact')).toBeInTheDocument()
    expect(screen.getByText(/tag \/ Upgraded after Series/)).toBeInTheDocument()
    expect(screen.getByText('end of path')).toBeInTheDocument()
  })
})

describe('a notice carries its tone in words', () => {
  it('renders a text label beside the tone', () => {
    render(<Notice tone="warning">A tag block cannot create a tag.</Notice>)
    expect(screen.getByText('Warning')).toBeInTheDocument()
    expect(screen.getByText('A tag block cannot create a tag.')).toBeInTheDocument()
  })
})
