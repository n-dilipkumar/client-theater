/**
 * Tests for WF-094's page.
 *
 * The page is driven against a stubbed `fetch` rather than a live server, so the
 * assertions are about what a seller reads and which controls are offered, not
 * about the server's answer. Every test builds its own stub and its own data, so
 * the file passes alone and under vitest's parallel run without depending on
 * another test's fixture.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import page from './index.jsx'
import { exceedsAttachmentCap, megabytes, statusLabel, statusTone } from './api.js'
import { CopyField, FrozenAmount, StatusBadge } from './primitives.jsx'

const { Component } = page

const CAP = 20 * 1024 * 1024

const DRAFT = {
  id: 'quote_draft',
  room_id: 'room_1',
  data: {
    title: 'Northwind renewal',
    status: 'DRAFT',
    quote_number: 'Q-2026-014',
    line_items: [{ quantity: 2, price: 1200 }],
  },
  derived: {
    status: 'DRAFT',
    known_status: true,
    locked: false,
    frozen_amount: null,
    shareable: false,
    sent: false,
    can_publish: true,
    can_unlock: false,
    unlock_targets: ['DRAFT', 'PENDING_APPROVAL', 'REJECTED'],
  },
}

const PUBLISHED = {
  id: 'quote_published',
  room_id: 'room_1',
  data: {
    title: 'Contoso onboarding',
    status: 'PUBLISHED',
    quote_number: 'Q-2026-015',
    hs_locked: true,
    hs_quote_amount: 8400,
    locked_amount: 8400,
    hs_domain: 'billing.northwind.example',
    hs_slug: 'q-2026-015',
    hs_quote_link: 'https://billing.northwind.example/q-2026-015',
  },
  derived: {
    status: 'PUBLISHED',
    known_status: true,
    locked: true,
    frozen_amount: 8400,
    shareable: true,
    sent: false,
    can_publish: false,
    can_unlock: true,
    unlock_targets: ['DRAFT', 'PENDING_APPROVAL', 'REJECTED'],
  },
}

const VOCABULARY = {
  ticket: 'WF-094',
  statuses: ['DRAFT', 'PENDING_APPROVAL', 'REJECTED', 'PUBLISHED', 'SHARED'],
  publishable_from: ['DRAFT', 'PENDING_APPROVAL', 'REJECTED', 'SHARED'],
  unlock_targets: ['DRAFT', 'PENDING_APPROVAL', 'REJECTED'],
  activities: ['Quote published', 'Quote sent'],
  languages: ['en', 'de'],
  locales: ['en-GB', 'de-DE'],
  timezones: ['UTC'],
  limits: { cc: 9, email_attachment_cap_bytes: CAP },
}

function jsonResponse(body, status = 200) {
  return {
    ok: status < 400,
    status,
    json: async () => body,
  }
}

function stubFetch(routes) {
  const calls = []
  const handler = async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET', body: options.body })
    for (const [pattern, body] of Object.entries(routes)) {
      if (url.includes(pattern)) return jsonResponse(typeof body === 'function' ? body() : body)
    }
    return jsonResponse({ detail: `no stub for ${url}` }, 404)
  }
  global.fetch = vi.fn(handler)
  return calls
}

beforeEach(() => {
  vi.restoreAllMocks()
})

/**
 * The keys `derived` actually carries, spelled the way the server spells them.
 *
 * These are snake_case because the API is. An earlier version of this page read
 * `canPublish` and `canUnlock` and rendered every quote with no controls on it
 * and no test failed, because a page that offers nothing is not a crash. Naming
 * the contract here is what makes a rename of the server's keys a test failure.
 */
const DERIVED_KEYS = [
  'status',
  'known_status',
  'locked',
  'frozen_amount',
  'shareable',
  'sent',
  'unlock_targets',
  'can_publish',
  'can_unlock',
]

describe('the descriptor', () => {
  it('exports the four fields the host discovers', () => {
    expect(page.id).toBe('wf-094-publish-quote')
    expect(typeof page.label).toBe('string')
    expect(page.icon).toBeTruthy()
    expect(typeof Component).toBe('function')
  })
})

describe('the derived contract', () => {
  it('uses exactly the keys the server sends', () => {
    // Both fixtures are built from the same list as the server's `derived`
    // block, so this fails the moment either side renames a key.
    expect(Object.keys(DRAFT.derived).sort()).toEqual([...DERIVED_KEYS].sort())
    expect(Object.keys(PUBLISHED.derived).sort()).toEqual([...DERIVED_KEYS].sort())
  })
})

describe('the page', () => {
  it('renders a published quote with its frozen total and its link', async () => {
    stubFetch({
      '/WF-094/vocabulary': VOCABULARY,
      '/WF-094/settings': { domain: 'billing.northwind.example', domain_fallback: false },
      '/WF-094/quotes': { count: 1, entries: [PUBLISHED] },
      '/records/room': { entries: [{ id: 'room_1', data: { name: 'Northwind' } }] },
    })

    render(<Component />)

    expect(await screen.findByText('Contoso onboarding')).toBeInTheDocument()
    expect(screen.getByText('8400.00')).toBeInTheDocument()
    expect(
      screen.getByDisplayValue('https://billing.northwind.example/q-2026-015'),
    ).toBeInTheDocument()
  })

  it('says a locked total is frozen, rather than leaving the number bare', async () => {
    stubFetch({
      '/WF-094/vocabulary': VOCABULARY,
      '/WF-094/settings': { domain: 'quotes.website.com', domain_fallback: false },
      '/WF-094/quotes': { count: 1, entries: [PUBLISHED] },
      '/records/room': { entries: [{ id: 'room_1', data: { name: 'Northwind' } }] },
    })

    render(<Component />)

    expect(await screen.findByText('Frozen on publish')).toBeInTheDocument()
  })

  it('offers publish and share on a draft, and no link yet', async () => {
    stubFetch({
      '/WF-094/vocabulary': VOCABULARY,
      '/WF-094/settings': { domain: 'quotes.website.com', domain_fallback: false },
      '/WF-094/quotes': { count: 1, entries: [DRAFT] },
      '/records/room': { entries: [{ id: 'room_1', data: { name: 'Northwind' } }] },
    })

    render(<Component />)

    expect(await screen.findByText('Northwind renewal')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /publish and lock/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /share only/i })).toBeInTheDocument()
    // A draft has no link, so the copy control must not be on the page.
    expect(screen.queryByRole('button', { name: /copy link/i })).not.toBeInTheDocument()
  })

  it('offers the unlock control on a published quote and not on a draft', async () => {
    stubFetch({
      '/WF-094/vocabulary': VOCABULARY,
      '/WF-094/settings': { domain: 'quotes.website.com', domain_fallback: false },
      '/WF-094/quotes': { count: 2, entries: [DRAFT, PUBLISHED] },
      '/records/room': { entries: [{ id: 'room_1', data: { name: 'Northwind' } }] },
    })

    render(<Component />)

    expect(await screen.findByText('Contoso onboarding')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /unlock the total/i })).toBeInTheDocument()
  })

  it('warns that a fallback domain is in use', async () => {
    stubFetch({
      '/WF-094/vocabulary': VOCABULARY,
      '/WF-094/settings': { domain: 'quotes.website.com', domain_fallback: true },
      '/WF-094/quotes': { count: 0, entries: [] },
      '/records/room': { entries: [{ id: 'room_1', data: { name: 'Northwind' } }] },
    })

    render(<Component />)

    expect(await screen.findByText(/no quote domain is connected/i)).toBeInTheDocument()
  })

  it('renders an empty state rather than a blank page when there are no quotes', async () => {
    stubFetch({
      '/WF-094/vocabulary': VOCABULARY,
      '/WF-094/settings': { domain: 'quotes.website.com', domain_fallback: false },
      '/WF-094/quotes': { count: 0, entries: [] },
      '/records/room': { entries: [{ id: 'room_1', data: { name: 'Northwind' } }] },
    })

    render(<Component />)

    expect(await screen.findByText(/no quotes to publish/i)).toBeInTheDocument()
  })

  it('publishes with a body that carries no link', async () => {
    const calls = stubFetch({
      '/WF-094/vocabulary': VOCABULARY,
      '/WF-094/settings': { domain: 'quotes.website.com', domain_fallback: false },
      '/WF-094/quotes': { count: 1, entries: [DRAFT] },
      '/records/room': { entries: [{ id: 'room_1', data: { name: 'Northwind' } }] },
      '/publish': PUBLISHED,
    })

    render(<Component />)
    await userEvent.click(await screen.findByRole('button', { name: /publish and lock/i }))

    await waitFor(() => {
      const publish = calls.find((call) => call.url.includes('/publish'))
      expect(publish).toBeDefined()
      const body = JSON.parse(publish.body)
      // The computed properties are not settable, so the body carries none of
      // them. A field here would be a field the server refuses.
      expect(body).not.toHaveProperty('hs_quote_link')
      expect(body).not.toHaveProperty('hs_slug')
      expect(body.shared_only).toBe(false)
    })
  })

  it('refuses an oversized PDF before the seller records the send', async () => {
    stubFetch({
      '/WF-094/vocabulary': VOCABULARY,
      '/WF-094/settings': { domain: 'quotes.website.com', domain_fallback: false },
      '/WF-094/quotes': { count: 1, entries: [PUBLISHED] },
      '/records/room': { entries: [{ id: 'room_1', data: { name: 'Northwind' } }] },
    })

    render(<Component />)
    await userEvent.click(await screen.findByRole('button', { name: /record an email/i }))

    await userEvent.type(await screen.findByLabelText(/^to$/i), 'buyer@example.com')
    await userEvent.type(screen.getByLabelText(/pdf size in bytes/i), String(CAP + 1))

    // The researched cap is silent, so the page says it before the send rather
    // than letting the seller discover it afterwards.
    expect(await screen.findByText(/above the/i)).toBeInTheDocument()
  })
})

describe('StatusBadge', () => {
  it('names the status in words, not only in colour', () => {
    render(<StatusBadge quote={PUBLISHED} />)
    expect(screen.getByText('Published, total locked')).toBeInTheDocument()
    expect(screen.getByText('Total locked')).toBeInTheDocument()
  })

  it('says a shared quote keeps an editable total', () => {
    render(<StatusBadge quote={{ data: { status: 'SHARED' } }} />)
    expect(screen.getByText('Shared, total editable')).toBeInTheDocument()
  })

  it('falls back to the raw id for a status it does not know', () => {
    render(<StatusBadge quote={{ data: { status: 'ARCHIVED' } }} />)
    expect(screen.getByText('ARCHIVED')).toBeInTheDocument()
  })
})

describe('CopyField', () => {
  /**
   * jsdom exposes `navigator.clipboard` as a read-only getter, so a plain
   * assignment is ignored and the real (absent) clipboard is called instead.
   * Defining the property is what makes the stub actually take.
   */
  function stubClipboard(writeText) {
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
      writable: true,
    })
  }

  it('copies the link and confirms it happened', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    stubClipboard(writeText)

    render(<CopyField label="Public link" value="https://example.com/q-1" />)
    await userEvent.click(screen.getByRole('button', { name: /copy link/i }))

    expect(writeText).toHaveBeenCalledWith('https://example.com/q-1')
    expect(await screen.findByText(/the buyer can open this link/i)).toBeInTheDocument()
  })

  it('says the field is still selectable when the clipboard is refused', async () => {
    const writeText = vi.fn().mockRejectedValue(new Error('denied'))
    stubClipboard(writeText)

    render(<CopyField label="Public link" value="https://example.com/q-1" />)
    await userEvent.click(screen.getByRole('button', { name: /copy link/i }))

    expect(await screen.findByText(/select the link and copy it by hand/i)).toBeInTheDocument()
  })
})

describe('FrozenAmount', () => {
  it('says the amount is not computed before the first publish', () => {
    render(<FrozenAmount amount={null} locked={false} />)
    expect(screen.getByText(/not computed yet/i)).toBeInTheDocument()
  })
})

describe('the helpers', () => {
  it('reads the status label and tone from one place', () => {
    expect(statusLabel('PUBLISHED')).toBe('Published, total locked')
    expect(statusTone('PUBLISHED')).toBe('insert')
    expect(statusLabel('NOT_A_STATUS')).toBe('NOT_A_STATUS')
  })

  it('treats a PDF at the cap as attachable and one over it as dropped', () => {
    expect(exceedsAttachmentCap(CAP, CAP)).toBe(false)
    expect(exceedsAttachmentCap(CAP + 1, CAP)).toBe(true)
    expect(exceedsAttachmentCap('', CAP)).toBe(false)
  })

  it('reports megabytes with one decimal', () => {
    expect(megabytes(CAP + 1)).toBe('20.0')
  })
})