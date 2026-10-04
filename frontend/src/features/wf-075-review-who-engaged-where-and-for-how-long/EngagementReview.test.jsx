import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { GEOGRAPHY_SOURCE, formatDuration, formatInstant, verificationState } from './api'
import descriptor, { EngagementReviewPage } from './index.jsx'
import { CountPair, DwellBars, ProofBadge } from './primitives'

/**
 * Tests for the pieces of WF-075 that can get quietly wrong.
 *
 * Four claims are pinned here, and each is one the specification makes explicitly:
 *
 * 1. A visitor and a view are different counts. `CountPair` renders them side by side
 *    with a sentence each, so a reader cannot collapse them into one number.
 * 2. `verified` means proven, not typed. `unknown` is a third state, distinct from
 *    `unverified`, and both must render as words rather than as colour.
 * 3. No vendor is named for the geolocation. The specification marks the provider as an
 *    inference and names none, so the page may not invent one.
 * 4. An unreadable timestamp renders as "unknown", never as 1970. A millisecond value
 *    that is missing must not render as a real date a rep could misread.
 *
 * The page component itself needs a stubbed transport, so the tests below cover its
 * building blocks and its descriptor rather than driving six fetches.
 */

describe('the descriptor', () => {
  it('exports the shape the feature host discovers', () => {
    expect(descriptor.id).toBe('wf-075-review-who-engaged-where-and-for-how-long')
    expect(typeof descriptor.label).toBe('string')
    expect(descriptor.Component).toBe(EngagementReviewPage)
  })

  it('uses an icon name that exists in the shared set', () => {
    // `components/ui.jsx` is not edited, so the name must already be there. The Icon
    // component falls back to a generic glyph for an unknown name, which would hide
    // this mistake rather than show it.
    const known = ['dashboard', 'rooms', 'audit', 'schema', 'plus', 'refresh', 'trash', 'restore', 'search', 'close', 'chevron', 'database']

    expect(known).toContain(descriptor.icon)
  })
})

describe('the two counts stay apart', () => {
  it('renders both counts and says what each one counts', () => {
    render(<CountPair visitors={1} views={3} />)

    expect(screen.getByText('1')).toBeInTheDocument()
    expect(screen.getByText('3')).toBeInTheDocument()
    expect(screen.getByText(/one row per buyer email/i)).toBeInTheDocument()
    expect(screen.getByText(/one row per view/i)).toBeInTheDocument()
  })
})

describe('verified means proven, not typed', () => {
  it('renders a word for each of the three states', () => {
    const { rerender } = render(<ProofBadge value="verified" />)
    expect(screen.getByText('Verified')).toBeInTheDocument()

    rerender(<ProofBadge value="unverified" />)
    expect(screen.getByText('Not verified')).toBeInTheDocument()

    rerender(<ProofBadge value="unknown" />)
    expect(screen.getByText('No proof recorded')).toBeInTheDocument()
  })

  it('keeps not-proven and no-proof-recorded as different words', () => {
    // These are different facts about a buyer. Collapsing them is the exact confusion
    // the specification's "actually proven (not merely typed in)" warns against.
    expect(verificationState('unverified').label).not.toBe(verificationState('unknown').label)
  })

  it('falls back to a real state rather than rendering nothing', () => {
    expect(verificationState('something-new').label).toBe('No proof recorded')
    expect(verificationState(undefined).label).toBe('No proof recorded')
  })

  it('states what each word means when asked', () => {
    render(<ProofBadge value="verified" showMeaning />)

    expect(screen.getByText(/identity was proven/i)).toBeInTheDocument()
  })

  it('never conveys the state by colour alone', () => {
    // The word is always present as text, so a monochrome display and a colour-blind
    // reader both get the same answer.
    render(<ProofBadge value="unknown" />)

    expect(screen.getByText('No proof recorded')).toBeInTheDocument()
  })
})

describe('the geography is labelled as an inference', () => {
  it('names no vendor', () => {
    for (const vendor of ['maxmind', 'ipinfo', 'ipapi', 'cloudflare', 'geoip']) {
      expect(GEOGRAPHY_SOURCE.toLowerCase()).not.toContain(vendor)
    }
  })

  it('says it is inferred', () => {
    expect(GEOGRAPHY_SOURCE).toMatch(/inferred/i)
  })
})

describe('timestamps', () => {
  it('renders a millisecond value as a readable instant', () => {
    // 1759600000000 ms is 2025-10-04T17:46:40Z. The assertion states the whole string
    // rather than a date fragment so it cannot pass on the wrong day.
    expect(formatInstant(1759600000000)).toBe('2025-10-04 17:46:40Z')
  })

  it('renders a millisecond value, not a second value', () => {
    // The same instant expressed in seconds would land in 1970. A page that divided by
    // a thousand twice would show the buyer engaging in 1970, which is a real date.
    expect(formatInstant(1759600000)).toMatch(/1970-01-21/)
  })

  it('renders a missing value as unknown rather than as the epoch', () => {
    // A missing timestamp rendered as 1970 would be a real date the rep could misread
    // as the moment a buyer engaged.
    expect(formatInstant(null)).toBe('unknown')
    expect(formatInstant(undefined)).toBe('unknown')
  })

  it('renders an unreadable value as unknown', () => {
    expect(formatInstant('not-a-number')).toBe('unknown')
  })
})

describe('durations', () => {
  it('renders seconds on their own under a minute', () => {
    expect(formatDuration(45)).toBe('45s')
  })

  it('renders minutes and seconds above a minute', () => {
    expect(formatDuration(140)).toBe('2m 20s')
  })

  it('renders a missing or negative duration as zero rather than as a negative', () => {
    expect(formatDuration(null)).toBe('0s')
    expect(formatDuration(-5)).toBe('0s')
  })
})

describe('the per-page bars', () => {
  it('says so when nothing has been recorded', () => {
    render(<DwellBars rows={[]} />)

    expect(screen.getByText(/no page dwell/i)).toBeInTheDocument()
  })

  it('shows the numbers as text, so nothing is carried by the bar alone', () => {
    const rows = [
      { page_number: 1, total_duration_seconds: 90, viewers: 2 },
      { page_number: 2, total_duration_seconds: 30, viewers: 1 },
    ]

    render(<DwellBars rows={rows} />)

    const list = screen.getByRole('list')
    const items = within(list).getAllByRole('listitem')
    expect(items).toHaveLength(2)
    // Each row names its page, its seconds and its reader count as text.
    expect(within(items[0]).getByText(/Page 1/)).toBeInTheDocument()
    expect(within(items[0]).getByText(/90s/)).toBeInTheDocument()
    expect(within(items[0]).getByText(/2 readers/)).toBeInTheDocument()
  })

  it('gives each bar a label a screen reader can use', () => {
    const rows = [{ page_number: 1, total_duration_seconds: 60, viewers: 1 }]

    render(<DwellBars rows={rows} />)

    expect(screen.getByRole('img', { name: /page 1 held attention for 60 seconds/i })).toBeInTheDocument()
  })
})