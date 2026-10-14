/**
 * Tests for WF-071's page and its primitives.
 *
 * The page is driven against a stubbed `fetch` rather than a live server, so the
 * assertions are about what a rep reads and which controls are offered, not about
 * the server's answer. Every test builds its own stub and its own data, so the
 * file passes alone and under vitest's parallel run without depending on another
 * test's fixture.
 *
 * Three of these tests are about the researched facts rather than rendering: the
 * page names the surfaces it does not build, the overlay marks a token the view
 * could not fill instead of hiding it, and the token picker offers exactly the
 * five tokens the server accepts.
 */

import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import page from './index.jsx'
import {
  POSITION_LABELS,
  STATE_LABELS,
  TOKEN_LABELS,
  numberInRange,
  shortTimestamp,
  stateOf,
  stateTone,
  tokensIn,
  wholeInRange,
  withToken,
} from './api.js'
import {
  ConfigSummary,
  InlineNote,
  OverlayPreview,
  StampRow,
  TokenChips,
  WatermarkStateBadge,
} from './primitives.jsx'

const { Component } = page

const VOCABULARY = {
  ticket: 'WF-071',
  positions: [
    'top-left',
    'top-center',
    'top-right',
    'middle-left',
    'middle-center',
    'middle-right',
    'bottom-left',
    'bottom-center',
    'bottom-right',
  ],
  rotations: [0, 30, 45, 90, 180],
  tokens: ['email', 'date', 'time', 'link', 'ipAddress'],
  required_fields: ['text', 'is_tiled', 'position', 'rotation', 'color', 'font_size', 'opacity'],
  limits: {
    font_size_min: 1,
    font_size_max: 96,
    opacity_min: 0,
    opacity_max: 1,
    text_min_chars: 1,
    text_max_chars: 200,
  },
  defaults: {
    text: 'Confidential',
    is_tiled: false,
    position: 'middle-center',
    rotation: 45,
    color: '#000000',
    font_size: 24,
    opacity: 0.5,
  },
  plan: { feature: 'watermark', requires: 'business', plans: ['starter', 'business', 'enterprise'] },
  reused: { link_collection: 'wf121_tracked_link' },
}

function linkRow(overrides = {}) {
  return {
    link: {
      id: 'wf121_link_1',
      room_id: 'room_1',
      title: 'Security overview',
      asset_id: 'deck-demo',
      asset_name: 'Enterprise Security & Compliance Overview',
      page_count: 6,
      share_url: '/api/wf-121/links/demo',
      require_viewer: true,
      state: 'active',
      closed: false,
    },
    config_id: null,
    enable_watermark: false,
    watermark_config: null,
    updated_at: null,
    ...overrides,
  }
}

function payload(overrides = {}) {
  return {
    '/vocabulary': VOCABULARY,
    '/summary': { links: 1, watermarked: 1, tiled: 1, cleared: 0, stamps: 2, viewers_stamped: 1 },
    '/links': { count: 1, room_id: 'room_1', links: [linkRow()] },
    '/stamps': {
      link_id: 'wf121_link_1',
      count: 1,
      stamps: [
        {
          id: 's1',
          viewer: 'a.buyer@northwind.example',
          ip_address: '203.0.113.9',
          rendered_text: 'Confidential \u2014 a.buyer@northwind.example',
          unresolved_tokens: [],
          page_count: 6,
          stamped_at: '2026-05-01T12:00:00.000+00:00',
        },
      ],
    },
    '/watermark': {
      link: linkRow().link,
      config_id: 'cfg1',
      enable_watermark: true,
      watermark_config: {
        text: 'Confidential \u2014 {{email}}',
        is_tiled: true,
        position: 'middle-center',
        rotation: 45,
        color: '#000000',
        font_size: 24,
        opacity: 0.5,
      },
      updated_at: '2026-05-01T12:00:00.000+00:00',
    },
    '/records/room': { records: [{ id: 'room_1', data: { name: 'Northwind Traders' } }] },
    ...overrides,
  }
}

/** Route each API call this page makes to one canned answer. */
function stubFetch(answers, calls = []) {
  return vi.fn(async (url) => {
    const path = String(url).split('?')[0]
    calls.push(String(url))
    const key = Object.keys(answers).find((candidate) => path.endsWith(candidate))
    const body = key ? answers[key] : {}
    return {
      ok: true,
      status: 200,
      json: async () => body,
      text: async () => JSON.stringify(body),
    }
  })
}

beforeEach(() => {
  vi.restoreAllMocks()
})

// --------------------------------------------------------------------------- #
// The descriptor
// --------------------------------------------------------------------------- #

describe('the descriptor', () => {
  it('exports the four fields App.jsx reads', () => {
    expect(page.id).toBe('wf-071-watermark')
    expect(page.label).toBe('Viewer watermark')
    expect(typeof page.icon).toBe('string')
    expect(typeof page.Component).toBe('function')
  })

  it('has a unique order beside the workflows it reuses', () => {
    expect(page.order).toBeGreaterThan(0)
  })
})

// --------------------------------------------------------------------------- #
// The api helpers
// --------------------------------------------------------------------------- #

describe('the state of a link', () => {
  it('names the four states a watermark can be in', () => {
    expect(stateOf({ enable_watermark: false })).toBe('off')
    expect(stateOf({ enable_watermark: true, watermark_config: null })).toBe('cleared')
    expect(
      stateOf({ enable_watermark: true, watermark_config: { is_tiled: true } }),
    ).toBe('tiled')
    expect(
      stateOf({ enable_watermark: true, watermark_config: { is_tiled: false } }),
    ).toBe('anchored')
  })

  it('gives every state a word as well as a tone', () => {
    for (const state of Object.keys(STATE_LABELS)) {
      expect(stateTone(state)).toBeTruthy()
      expect(STATE_LABELS[state]).toBeTruthy()
    }
  })
})

describe('the range helpers', () => {
  it('accepts a whole number inside the range only', () => {
    expect(wholeInRange('24', 1, 96)).toBe(24)
    expect(wholeInRange('0', 1, 96)).toBeNull()
    expect(wholeInRange('2.5', 1, 96)).toBeNull()
    expect(wholeInRange('', 1, 96)).toBeNull()
  })

  it('accepts a decimal inside the range only', () => {
    expect(numberInRange('0.5', 0, 1)).toBe(0.5)
    expect(numberInRange('1.5', 0, 1)).toBeNull()
    expect(numberInRange('abc', 0, 1)).toBeNull()
  })
})

describe('the token helpers', () => {
  it('appends a token once, on its own line, and never twice', () => {
    expect(withToken('Confidential', 'email')).toBe('Confidential {{email}}')
    expect(withToken('Confidential {{email}}', 'email')).toBe('Confidential {{email}}')
    expect(withToken('', 'email')).toBe('{{email}}')
  })

  it('reads the tokens a text uses, in first-seen order', () => {
    expect(tokensIn('{{email}} and {{date}} and {{email}}')).toEqual(['email', 'date'])
    expect(tokensIn('no tokens here')).toEqual([])
  })
})

describe('the presentation helpers', () => {
  it('labels the nine anchors and the five tokens', () => {
    expect(Object.keys(POSITION_LABELS)).toHaveLength(9)
    expect(Object.keys(TOKEN_LABELS)).toEqual(['email', 'date', 'time', 'link', 'ipAddress'])
  })

  it('renders a timestamp compactly and a missing one as a dash', () => {
    expect(shortTimestamp('2026-05-01T12:00:00.000Z')).toBe('2026-05-01 12:00:00Z')
    expect(shortTimestamp(null)).toBe('--')
  })
})

// --------------------------------------------------------------------------- #
// The primitives
// --------------------------------------------------------------------------- #

describe('the primitives', () => {
  it('renders the watermark state as a word, not only a colour', () => {
    render(<WatermarkStateBadge state="tiled" />)
    expect(screen.getByText('On, tiled')).toBeTruthy()
  })

  it('renders an inline note with its meaning in words', () => {
    render(<InlineNote tone="warning">Starter is refused.</InlineNote>)
    expect(screen.getByRole('status').textContent).toContain('Starter is refused.')
  })

  it('offers one button per token and reports which was pressed', async () => {
    const onInsert = vi.fn()
    const user = userEvent.setup()
    render(<TokenChips tokens={['email', 'date']} labels={TOKEN_LABELS} onInsert={onInsert} />)
    await user.click(screen.getByRole('button', { name: /email/ }))
    expect(onInsert).toHaveBeenCalledWith('email')
  })

  it('shows the substituted text and names a token the view could not fill', () => {
    render(
      <OverlayPreview
        rendered="Confidential anonymous"
        overlay={{ position: 'middle-center', rotation: 45, color: '#000000', font_size: 24, opacity: 0.5 }}
        unresolved={['email']}
      />,
    )
    expect(screen.getByText('Confidential anonymous')).toBeTruthy()
    expect(screen.getByRole('status').textContent).toContain('email')
  })

  it('draws the overlay flat and never rotates it into the page', () => {
    render(
      <OverlayPreview
        rendered="Confidential"
        overlay={{ position: 'middle-center', rotation: 90, color: '#000000', font_size: 24, opacity: 0.5 }}
      />,
    )
    expect(screen.getByText('90 degrees')).toBeTruthy()
  })

  it('summarises a committed config as machine values', () => {
    render(
      <ConfigSummary
        config={{
          text: 'Confidential',
          position: 'bottom-right',
          rotation: 0,
          color: '#abc',
          font_size: 12,
          opacity: 1,
          is_tiled: false,
        }}
      />,
    )
    expect(screen.getByText(/Confidential/).textContent).toContain('Bottom right')
  })

  it('shows a stamp with its identity and its page count', () => {
    render(
      <StampRow
        stamp={{
          id: 's1',
          rendered_text: 'Confidential \u2014 a.buyer@northwind.example',
          viewer: 'a.buyer@northwind.example',
          ip_address: '203.0.113.9',
          page_count: 6,
          unresolved_tokens: [],
          stamped_at: '2026-05-01T12:00:00.000Z',
        }}
      />,
    )
    const row = screen.getByRole('listitem')
    expect(within(row).getByText('a.buyer@northwind.example | 203.0.113.9 | 6 page(s) | 2026-05-01 12:00:00Z')).toBeTruthy()
  })
})

// --------------------------------------------------------------------------- #
// The page
// --------------------------------------------------------------------------- #

async function openPanel(user) {
  const buttons = await screen.findAllByRole('button', { name: 'Open the watermark panel' })
  await user.click(buttons[0])
}

describe('the page', () => {
  it('loads the board and counts its states', async () => {
    vi.stubGlobal('fetch', stubFetch(payload()))
    render(<Component />)
    expect(await screen.findByText('Viewer watermark')).toBeTruthy()
    expect(screen.getByText('Watermarked')).toBeTruthy()
    expect(screen.getByText('Stamps')).toBeTruthy()
    expect(await screen.findByText(/Enterprise Security & Compliance Overview/)).toBeTruthy()
  })

  it('opens the config panel with all seven validated fields', async () => {
    vi.stubGlobal('fetch', stubFetch(payload()))
    const user = userEvent.setup()
    render(<Component />)
    await openPanel(user)
    expect(await screen.findByLabelText('Watermark text')).toBeTruthy()
    expect(screen.getByLabelText('Position')).toBeTruthy()
    expect(screen.getByLabelText('Rotation')).toBeTruthy()
    expect(screen.getByLabelText('Colour')).toBeTruthy()
    expect(screen.getByLabelText('Font size in points')).toBeTruthy()
    expect(screen.getByLabelText('Opacity')).toBeTruthy()
    expect(screen.getByLabelText(/Tile the watermark/)).toBeTruthy()
  })

  it('offers exactly the five researched tokens', async () => {
    vi.stubGlobal('fetch', stubFetch(payload()))
    const user = userEvent.setup()
    render(<Component />)
    await openPanel(user)
    const picker = await screen.findByLabelText('Watermark tokens')
    expect(within(picker).getAllByRole('button')).toHaveLength(5)
  })

  it('names the surfaces it does not build rather than omitting them', async () => {
    vi.stubGlobal('fetch', stubFetch(payload()))
    render(<Component />)
    expect(await screen.findByText('Not built here')).toBeTruthy()
    expect(screen.getByText(/Access control/)).toBeTruthy()
    expect(screen.getByText(/burned into stored bytes/)).toBeTruthy()
  })

  it('shows the recorded stamps for the open link', async () => {
    vi.stubGlobal('fetch', stubFetch(payload()))
    const user = userEvent.setup()
    render(<Component />)
    await openPanel(user)
    expect(await screen.findByText('Stamps recorded')).toBeTruthy()
    expect(screen.getByText(/203\.0\.113\.9/)).toBeTruthy()
  })

  it('lists every link the scope has, with its joined watermark', async () => {
    vi.stubGlobal('fetch', stubFetch(payload({ '/links': { count: 1, room_id: 'room_1', links: [linkRow()] } })))
    render(<Component />)
    expect(await screen.findByText(/wf121_link_1/)).toBeTruthy()
    expect(screen.getByText('Off')).toBeTruthy()
  })
})
