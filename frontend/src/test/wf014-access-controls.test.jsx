import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import descriptor from '@/features/wf-014-access-controls/index.jsx'
import AccessControlsPage from '@/features/wf-014-access-controls/AccessControlsPage.jsx'
import { BuyerBanner } from '@/features/wf-014-access-controls/primitives.jsx'

/**
 * Tests for the WF-014 access-controls page.
 *
 * The backend tests prove the rules. These prove the page does not mislead about
 * them, which is the failure mode this workflow actually has: a page badged Live
 * that is closed to every buyer reads as "everything is fine" when it is not.
 *
 * So the assertions are about words rather than colour:
 *
 * * a closed link says *why* it is closed, in text, next to the badge;
 * * a link that is Live but out of views does not read as open;
 * * the buyer banner announces itself to assistive tech and distinguishes a
 *   warning that sits beside the content from an error that replaces it;
 * * the descriptor is the shape the host discovers, and its id matches the
 *   backend's `FEATURE["id"]`.
 */

/** One page's window, as `/api/wf-014/pages` returns it. */
function aWindow(overrides = {}) {
  const window = {
    page_id: 'page_1',
    room_id: 'room_1',
    published: true,
    stored_status: 'published',
    access_status: 'live',
    accessible: true,
    closed_by: [],
    expiry: {
      enabled: false,
      days: null,
      starts_at: null,
      expires_at: null,
      days_remaining: null,
      warning_days: 7,
      expiring_soon: false,
      clock: 'publish',
    },
    view_limit: {
      enabled: false,
      max_views: null,
      views: 0,
      views_remaining: null,
      capped: false,
      counted_by: 'count_where',
    },
    manual_status: null,
    ...overrides,
  }
  return window
}

function aBuyerView(overrides = {}) {
  return {
    page_id: 'page_1',
    room_id: 'room_1',
    state: 'open',
    show_content: true,
    message: '',
    message_placement: 'bottom-left',
    warning: false,
    error: false,
    days_remaining: null,
    expires_at: null,
    views_remaining: null,
    ...overrides,
  }
}

let calls = []
let windows = []
let buyerView = aBuyerView()
let summary = {}

/**
 * Route a request to its fixture.
 *
 * Matched with `includes` rather than `startsWith`, because the shared
 * `apiRequest` prefixes every path with `/api`, so the page never sees a URL
 * beginning `/wf-014`. A mock that matched the unprefixed path would silently
 * return `{}` for everything and every assertion would pass against an empty
 * page - which is how a test file stops testing anything.
 */
function respond(path) {
  if (path.includes('/wf-014/summary')) return summary
  if (path.includes('/buyer')) return buyerView
  if (path.includes('/wf-014/pages')) return { count: windows.length, windows }
  return {}
}

beforeEach(() => {
  calls = []
  windows = [aWindow()]
  summary = {
    total: 1,
    by_status: { live: 1, expiring_soon: 0, declined: 0, view_limit: 0, draft: 0 },
    closed: 0,
    open: 1,
    with_expiry: 0,
    with_view_limit: 0,
  }
  buyerView = aBuyerView()

  vi.stubGlobal(
    'fetch',
    vi.fn(async (url, options = {}) => {
      calls.push({ url: String(url), method: options.method || 'GET', body: options.body })
      const body = respond(String(url))
      return {
        ok: true,
        status: 200,
        statusText: 'OK',
        json: async () => body,
      }
    }),
  )
})

describe('the descriptor the host discovers', () => {
  it('is the shape lib/features.js looks for, with an id matching the backend', () => {
    // Must equal FEATURE["id"] in backend/dsr/features/wf014_access_controls.py.
    expect(descriptor.id).toBe('wf-014-access-controls')
    expect(typeof descriptor.label).toBe('string')
    expect(descriptor.label.length).toBeGreaterThan(0)
    expect(descriptor.Component).toBeTruthy()
  })

  it('carries its own glyph rather than adding one to the shared PATHS map', () => {
    expect(descriptor.iconPath).toBeTruthy()
    expect(descriptor.iconPath).not.toContain('PATHS')
  })
})

describe('the summary tiles', () => {
  it('reports closed links beside the live ones', async () => {
    summary = { ...summary, total: 2, open: 1, closed: 1, by_status: { ...summary.by_status, live: 1 } }
    windows = [aWindow(), aWindow({ page_id: 'page_2', access_status: 'view_limit', accessible: false, closed_by: ['view_limit'] })]

    render(<AccessControlsPage />)

    await waitFor(() => expect(screen.getByText('Closed')).toBeTruthy())
    expect(screen.getByText('Open')).toBeTruthy()
  })
})

describe('a link that is badged Live but closed', () => {
  it('does not read as open, and says it is out of views', async () => {
    windows = [
      aWindow({
        access_status: 'view_limit',
        accessible: false,
        closed_by: ['view_limit'],
        view_limit: { enabled: true, max_views: 2, views: 2, views_remaining: 0, capped: true, counted_by: 'count_where' },
      }),
    ]
    summary = { ...summary, closed: 1, open: 0, by_status: { ...summary.by_status, live: 0, view_limit: 1 } }

    render(<AccessControlsPage />)

    // The researched badge wording, as text. Selected by tag because the filter
    // row carries the same label on a button, and the badge is the `span`.
    await waitFor(() => expect(screen.getByText('View Limit', { selector: 'span' })).toBeTruthy())
    // The reason, in words. This is the sentence that stops a seller reading
    // "Live" and assuming the link works.
    expect(screen.getByText(/Closed - out of views/)).toBeTruthy()
    // And explicitly *not* the reassuring "Open to buyers".
    expect(screen.queryByText('Open to buyers')).toBeNull()
    // The count is shown as a fraction, not as a bare number.
    expect(screen.getByText('2 / 2')).toBeTruthy()
  })

  it('says both reasons when a link is expired and out of views', async () => {
    windows = [
      aWindow({
        access_status: 'declined',
        accessible: false,
        closed_by: ['expired', 'view_limit'],
      }),
    ]

    render(<AccessControlsPage />)

    await waitFor(() => expect(screen.getByText(/expired on its date/)).toBeTruthy())
    expect(screen.getByText(/out of views/)).toBeTruthy()
  })
})

describe('an open link', () => {
  it('says so in words', async () => {
    render(<AccessControlsPage />)

    await waitFor(() => expect(screen.getByText('Open to buyers')).toBeTruthy())
    // The badge, selected by tag: the filter row carries the same label.
    expect(screen.getByText('Live', { selector: 'span' })).toBeTruthy()
  })
})

describe('the buyer banner', () => {
  it('is a polite status for a warning, and does not claim to be an error', () => {
    const { container } = render(
      <BuyerBanner
        view={aBuyerView({
          state: 'expiring_soon',
          warning: true,
          message: "This room's link expires in 5 days.",
          message_placement: 'bottom-left',
        })}
        onDismiss={() => {}}
      />,
    )
    const banner = container.querySelector('[role="status"]')
    expect(banner).toBeTruthy()
    expect(banner.getAttribute('aria-live')).toBe('polite')
    expect(banner.textContent).toContain('This room is closing soon')
    expect(banner.textContent).toContain('5 days')
  })

  it('is an assertive alert for an expired link, and replaces the content', () => {
    const { container } = render(
      <BuyerBanner
        view={aBuyerView({
          state: 'expired',
          error: true,
          show_content: false,
          message: "This room's link has expired and is no longer available.",
        })}
        onDismiss={() => {}}
      />,
    )
    const banner = container.querySelector('[role="alert"]')
    expect(banner).toBeTruthy()
    expect(banner.getAttribute('aria-live')).toBe('assertive')
    expect(banner.textContent).toContain('This room is closed')
  })

  it('renders nothing for an open link, rather than an empty box', () => {
    const { container } = render(
      <BuyerBanner view={aBuyerView({ state: 'open', show_content: true, message: '' })} onDismiss={() => {}} />,
    )
    expect(container.textContent).toBe('')
  })
})

describe('the two switches', () => {
  it('are separate controls, because the constraints are independent', async () => {
    render(<AccessControlsPage />)

    await waitFor(() => expect(screen.getByText('Link expiry settings')).toBeTruthy())
    expect(screen.getByText('Restrict number of views')).toBeTruthy()
    // Two distinct labelled inputs, one per constraint.
    expect(screen.getByLabelText(/Enable link expiry/)).toBeTruthy()
    expect(screen.getByLabelText(/Close the link after this many views/)).toBeTruthy()
  })

  it('explain that a draft page keeps the setting without starting the clock', async () => {
    windows = [aWindow({ published: false, access_status: 'draft', accessible: false, closed_by: ['unpublished'], expiry: { ...aWindow().expiry, enabled: true, days: 14 } })]

    render(<AccessControlsPage />)

    await waitFor(() => expect(screen.getByText(/count has not started/)).toBeTruthy())
  })
})

describe('the actions a seller is offered', () => {
  it('offers Decline on an open link', async () => {
    render(<AccessControlsPage />)

    await waitFor(() => expect(screen.getByRole('button', { name: 'Decline' })).toBeTruthy())
    expect(screen.queryByRole('button', { name: 'Set live' })).toBeNull()
  })

  it('offers Set live on a hand-declined link', async () => {
    windows = [
      aWindow({
        manual_status: 'declined',
        access_status: 'declined',
        accessible: false,
        closed_by: ['declined'],
      }),
    ]

    render(<AccessControlsPage />)

    await waitFor(() => expect(screen.getByRole('button', { name: 'Set live' })).toBeTruthy())
    expect(screen.queryByRole('button', { name: 'Decline' })).toBeNull()
  })

  it('posts a view through this feature\'s own route, with the shared client', async () => {
    render(<AccessControlsPage />)

    await waitFor(() => expect(screen.getByText('Count a view')).toBeTruthy())
    await userEvent.click(screen.getByRole('button', { name: 'Count a view' }))

    await waitFor(() => {
      const post = calls.find((c) => c.method === 'POST')
      expect(post).toBeTruthy()
      expect(post.url).toContain('/api/wf-014/pages/page_1/views')
      expect(JSON.parse(post.body).viewer).toBe('alex@northwind.example')
    })
  })
})

describe('filtering by the derived badge', () => {
  it('sends the status to the server rather than filtering the page itself', async () => {
    render(<AccessControlsPage />)

    await waitFor(() => expect(screen.getByText('View Limit')).toBeTruthy())
    await userEvent.click(screen.getByRole('button', { name: 'Expiring soon' }))

    await waitFor(() => {
      expect(calls[calls.length - 1].url).toContain('status=expiring_soon')
    })
  })
})

describe('states the design floor requires', () => {
  it('shows an empty state rather than a blank page', async () => {
    windows = []
    render(<AccessControlsPage />)

    await waitFor(() => expect(screen.getByText('No pages match this filter')).toBeTruthy())
  })

  it('offers a retry when the summary cannot be loaded', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: false,
        status: 500,
        statusText: 'Server Error',
        json: async () => ({ detail: 'boom' }),
      })),
    )

    render(<AccessControlsPage />)

    await waitFor(() => expect(screen.getByText('Could not load data')).toBeTruthy())
    const retry = screen.getByText('Retry')
    expect(retry.closest('button')).toBeTruthy()
  })
})

describe('the accessibility floor', () => {
  it('gives every icon a text label beside it', async () => {
    const { container } = render(<AccessControlsPage />)

    await waitFor(() => expect(screen.getByText('Open to buyers')).toBeTruthy())
    const controls = container.querySelectorAll('button, a')
    for (const control of controls) {
      // A control is either named by text, or carries an aria-label.
      const named =
        control.textContent.trim().length > 0 || control.getAttribute('aria-label') !== null
      expect(named).toBe(true)
    }
  })

  it('gives every control a 44px minimum touch target', async () => {
    const { container } = render(<AccessControlsPage />)

    await waitFor(() => expect(screen.getByText('Open to buyers')).toBeTruthy())
    // The shared `Button` and `inputClass` carry `min-h-11`; this asserts the
    // page uses them rather than hand-writing a control that does not.
    const buttons = container.querySelectorAll('button')
    expect(buttons.length).toBeGreaterThan(0)
    for (const button of buttons) {
      const usesShared = button.className.includes('min-h-11')
      expect(usesShared).toBe(true)
    }
  })

  it('names every state in text, so no state is carried by colour alone', async () => {
    windows = [
      aWindow({
        access_status: 'expiring_soon',
        expiry: { ...aWindow().expiry, enabled: true, days: 30, expiring_soon: true },
      }),
    ]
    buyerView = aBuyerView({
      state: 'expiring_soon',
      warning: true,
      message: 'This room’s link expires in 5 days.',
    })

    const { container } = render(<AccessControlsPage />)

    await waitFor(() =>
      expect(screen.getByText('Expiring soon', { selector: 'span' })).toBeTruthy(),
    )
    // The badge is a text node carrying the researched wording, not a styled dot.
    const badge = screen.getByText('Expiring soon', { selector: 'span' })
    expect(badge.textContent).toBe('Expiring soon')
    // And the buyer is told in words what the badge alone would not say. The
    // banner and the preview line both render it, so this asserts at least one
    // rather than exactly one.
    expect(screen.getAllByText(/expires in 5 days/).length).toBeGreaterThan(0)
    expect(container.querySelectorAll('span[aria-label]')).toHaveLength(0)
  })

  it('uses no emoji as an icon', async () => {
    const { container } = render(<AccessControlsPage />)

    await waitFor(() => expect(screen.getByText('Open to buyers')).toBeTruthy())
    expect(container.textContent).not.toMatch(/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u)
  })
})
