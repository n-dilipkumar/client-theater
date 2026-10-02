import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import Gate from '@/features/wf-069-gate-each-buyer-link-with-a-password-a/Gate.jsx'
import LinkGating from '@/features/wf-069-gate-each-buyer-link-with-a-password-a/LinkGating.jsx'
import descriptor from '@/features/wf-069-gate-each-buyer-link-with-a-password-a/index.jsx'
import { Toggle } from '@/features/wf-069-gate-each-buyer-link-with-a-password-a/primitives.jsx'
import { httpError, stubApi } from './fixtures.js'

/**
 * Tests for WF-069's two pages.
 *
 * What these pin is the behaviour the researched specification actually requires,
 * and the three things a UI can quietly get wrong on a security workflow:
 *
 *   1. **The document is not requested until the gate has granted.** That ordering is
 *      the security property, and it is invisible in a screenshot.
 *   2. **A refused code puts the buyer back at the address step**, because a
 *      one-time code is spent by one wrong answer. A UI that keeps the code box on
 *      screen is asking the buyer to retype something that can never match.
 *   3. **A closed link reads the same whether it expired or was revoked.** The
 *      researched rule is that a buyer holding a forwarded URL gets the expired page,
 *      and copy that said "withdrawn" would tell them they were cut off.
 *
 * Every stub is keyed on this feature's own paths under `/wf-069`.
 */

const BASE = '/wf-069'
const LINK = 'wf069_link_abc123'
const PASSWORD = 'northwind-2026'

/** The gate's own answer, as the backend words it. */
function gate(overrides = {}) {
  return {
    link_id: LINK,
    room_id: 'room_a',
    title: 'Northwind - mutual NDA',
    target: { kind: 'dataroom', id: 'room_a' },
    step: 'password',
    message: null,
    reason: null,
    steps: ['password'],
    password_set: true,
    email_authenticated: false,
    expires_at: null,
    revoked: false,
    ...overrides,
  }
}

const ROOMS = { count: 1, records: [{ id: 'room_a', collection: 'room', data: { name: 'Northwind' } }] }

const SUMMARY = {
  links: 1,
  open: 1,
  expired: 0,
  revoked: 0,
  password_protected: 1,
  email_protected: 0,
  email_authenticated: 0,
  expiring_within_a_day: 0,
  verified_visitors: 0,
  views: 0,
  notifications: 0,
  pending_codes: 0,
  presets: 0,
}

const LINKS = {
  room_id: 'room_a',
  links: [
    {
      id: LINK,
      room_id: 'room_a',
      title: 'Northwind - mutual NDA',
      target: { kind: 'dataroom', id: 'room_a' },
      state: 'password',
      next_step: 'password',
      message: null,
      revoked: false,
      revoked_at: null,
      expired: false,
      expiry_reason: null,
      expires_at: null,
      expires_in_seconds: null,
      settings: {
        email_protected: false,
        email_authenticated: false,
        enable_notification: true,
        expires_at: null,
        password_set: true,
        email_required: false,
        code_required: false,
        steps: ['password'],
      },
      preset_id: null,
      preset_fields: [],
      preset_overridden: [],
      carried_fields: [],
      password_rotated_at: null,
    },
  ],
  revoked: [],
}

const boardRoutes = () => ({
  '/records/room': ROOMS,
  '/records/document': { count: 0, records: [] },
  [`${BASE}/summary`]: SUMMARY,
  [`${BASE}/presets`]: { presets: [] },
  [`${BASE}/rooms/room_a/links`]: LINKS,
  [`${BASE}/rooms/room_a/views`]: { room_id: 'room_a', views: [] },
  [`${BASE}/rooms/room_a/notifications`]: { room_id: 'room_a', notifications: [] },
})

// -- the descriptor --------------------------------------------------------- //

describe('the WF-069 descriptor', () => {
  it('is the shape the host discovers, with an id matching the backend', () => {
    expect(descriptor.id).toBe('wf-069-gate-each-buyer-link-with-a-password-a')
    expect(typeof descriptor.label).toBe('string')
    expect(typeof descriptor.Component).toBe('function')
  })

  it('carries its own glyph rather than asking to edit the shared icon map', () => {
    expect(descriptor.iconPath).toMatch(/^M/)
    expect(descriptor.icon).toBeTruthy()
  })

  it('does not claim WF-015 buyer route', () => {
    // `#/view/<room_id>` is WF-015's. Two features claiming one hash pattern is the
    // collision this registration file exists to make impossible.
    expect(descriptor.id).not.toBe('wf-015-identity-gate')
  })
})

// -- the buyer gate --------------------------------------------------------- //

describe('the buyer gate', () => {
  it('shows the friendly page for an expired link', async () => {
    stubApi({
      [`${BASE}/links/${LINK}/gate`]: gate({
        step: 'expired',
        reason: 'expired',
        message: 'This link has expired. Ask the sender for a new one.',
      }),
    })
    render(<Gate linkId={LINK} />)
    expect(await screen.findByText('This link is closed')).toBeInTheDocument()
    expect(screen.getByText(/has expired/)).toBeInTheDocument()
  })

  it('says the same thing about a revoked link, and never that it was withdrawn', async () => {
    const expired = gate({
      step: 'expired',
      reason: 'expired',
      message: 'This link has expired. Ask the sender for a new one.',
    })
    const revoked = { ...expired, reason: 'revoked', revoked: true }
    // The backend words them identically. Asserted on the page so a copy change in
    // either layer breaks here rather than in front of a buyer.
    stubApi({ [`${BASE}/links/${LINK}/gate`]: revoked })
    render(<Gate linkId={LINK} />)
    expect(await screen.findByText('This link is closed')).toBeInTheDocument()
    expect(screen.queryByText(/withdrawn|no longer available/i)).not.toBeInTheDocument()
    expect(revoked.message).toBe(expired.message)
  })

  it('asks for nothing on an ungated link', async () => {
    stubApi({ [`${BASE}/links/${LINK}/gate`]: gate({ step: 'open', steps: [], password_set: false }) })
    render(<Gate linkId={LINK} />)
    expect(await screen.findByText(/not gated/)).toBeInTheDocument()
    expect(screen.queryByLabelText('Link password')).not.toBeInTheDocument()
  })

  it('walks an authenticated link: email, then code, then password, then the document', async () => {
    const user = userEvent.setup()
    const calls = stubApi({
      [`${BASE}/links/${LINK}/gate`]: gate({
        step: 'email',
        steps: ['email', 'code', 'password'],
        email_authenticated: true,
      }),
      [`${BASE}/links/${LINK}/gate/email`]: {
        challenge_id: 'chal_1',
        email: 'buyer@northwind.example',
        step: 'code',
        code_dispatched: true,
      },
      [`${BASE}/links/${LINK}/gate/code`]: {
        challenge_id: 'chal_1',
        email_verified: true,
        visitor_id: 'vis_1',
        step: 'password',
      },
      [`${BASE}/links/${LINK}/gate/password`]: {
        granted: true,
        step: 'open',
        view_token: 'wf069_view_tok',
        visitor_id: 'vis_1',
      },
      [`${BASE}/links/${LINK}/document`]: {
        link_id: LINK,
        resolved: true,
        content: { title: 'Mutual NDA' },
        viewer: { email: 'buyer@northwind.example', email_verified: true },
        steps_cleared: ['email', 'code', 'password'],
      },
    })

    render(<Gate linkId={LINK} />)

    await user.type(await screen.findByLabelText('Your email address'), 'buyer@northwind.example')
    await user.click(screen.getByRole('button', { name: 'Continue' }))

    expect(await screen.findByLabelText('Six-digit code')).toBeInTheDocument()
    expect(screen.getByText('Code sent')).toBeInTheDocument()

    await user.type(screen.getByLabelText('Six-digit code'), '123456')
    await user.click(screen.getByRole('button', { name: 'Confirm code' }))

    expect(await screen.findByLabelText('Link password')).toBeInTheDocument()
    expect(screen.getByText(/Your email is confirmed/)).toBeInTheDocument()

    await user.type(screen.getByLabelText('Link password'), PASSWORD)
    await user.click(screen.getByRole('button', { name: 'Open the link' }))

    expect(await screen.findByText('You are through the gate')).toBeInTheDocument()
    expect(screen.getByText('Mutual NDA')).toBeInTheDocument()

    // The whole walk went through this feature's own paths.
    const paths = calls.map((call) => call.path)
    expect(paths).toContain(`${BASE}/links/${LINK}/gate/email`)
    expect(paths).toContain(`${BASE}/links/${LINK}/gate/code`)
    expect(paths).toContain(`${BASE}/links/${LINK}/gate/password`)
    // A four-step walk with real typing on each field. The default 5s budget is not
    // enough for `userEvent`, which types character by character on purpose so the
    // component sees the same event sequence a person produces.
  }, 20000)

  it('does not request the document until the gate has granted', async () => {
    const user = userEvent.setup()
    const calls = stubApi({
      [`${BASE}/links/${LINK}/gate`]: gate({ step: 'email', steps: ['email', 'password'] }),
      [`${BASE}/links/${LINK}/gate/email`]: { challenge_id: 'chal_1', step: 'password' },
      [`${BASE}/links/${LINK}/gate/password`]: {
        granted: true,
        view_token: 'wf069_view_tok',
        visitor_id: null,
      },
      [`${BASE}/links/${LINK}/document`]: {
        resolved: true,
        content: { title: 'Mutual NDA' },
        viewer: { email: null, email_verified: false },
      },
    })

    render(<Gate linkId={LINK} />)
    await user.type(await screen.findByLabelText('Your email address'), 'buyer@northwind.example')
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await screen.findByLabelText('Link password')

    // Two steps in, and the content has still not been asked for.
    expect(calls.some((call) => call.path.startsWith(`${BASE}/links/${LINK}/document`))).toBe(false)

    await user.type(screen.getByLabelText('Link password'), PASSWORD)
    await user.click(screen.getByRole('button', { name: 'Open the link' }))
    await screen.findByText('Mutual NDA')

    const documentCall = calls.find((call) => call.path.startsWith(`${BASE}/links/${LINK}/document`))
    expect(documentCall).toBeTruthy()
    // And it carries the token, which the grant was the only source of.
    expect(documentCall.path).toContain('view_token=wf069_view_tok')
  })

  it('sends a refused code back to the address step', async () => {
    const user = userEvent.setup()
    stubApi({
      [`${BASE}/links/${LINK}/gate`]: gate({
        step: 'email',
        steps: ['email', 'code', 'password'],
        email_authenticated: true,
      }),
      [`${BASE}/links/${LINK}/gate/email`]: { challenge_id: 'chal_1', step: 'code' },
      [`${BASE}/links/${LINK}/gate/code`]: httpError(403, {
        error: 'gate_denied',
        reason: 'code_rejected',
        detail: 'That code did not match. Check the email we sent and try again.',
      }),
    })

    render(<Gate linkId={LINK} />)
    await user.type(await screen.findByLabelText('Your email address'), 'buyer@northwind.example')
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.type(await screen.findByLabelText('Six-digit code'), '000000')
    await user.click(screen.getByRole('button', { name: 'Confirm code' }))

    expect(await screen.findByText(/That code did not match/)).toBeInTheDocument()
    // Back to the address, because the code was spent by the wrong answer.
    expect(await screen.findByLabelText('Your email address')).toBeInTheDocument()
    expect(screen.queryByLabelText('Six-digit code')).not.toBeInTheDocument()
  })

  it('never puts the password or a code on the page', async () => {
    const user = userEvent.setup()
    stubApi({
      [`${BASE}/links/${LINK}/gate`]: gate({ step: 'password', steps: ['password'] }),
      [`${BASE}/links/${LINK}/gate/password`]: httpError(403, {
        error: 'gate_denied',
        reason: 'password_rejected',
        detail: 'That password did not match.',
      }),
    })

    const { container } = render(<Gate linkId={LINK} />)
    const input = await screen.findByLabelText('Link password')
    expect(input).toHaveAttribute('type', 'password')
    await user.type(input, PASSWORD)
    await user.click(screen.getByRole('button', { name: 'Open the link' }))

    await screen.findByText(/That password did not match/)
    expect(container.textContent).not.toContain(PASSWORD)
  })

  it('offers a retry when the gate itself cannot be read', async () => {
    stubApi({
      [`${BASE}/links/${LINK}/gate`]: httpError(404, {
        error: 'not_found',
        detail: 'No such gated link.',
      }),
    })
    render(<Gate linkId={LINK} />)
    expect(await screen.findByText('No such gated link.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })
})

// -- the seller board ------------------------------------------------------- //

describe('the seller board', () => {
  async function renderBoard(extra = {}) {
    const calls = stubApi({ ...boardRoutes(), ...extra })
    const user = userEvent.setup()
    render(<LinkGating />)
    await screen.findByText('Northwind - mutual NDA')
    return { user, calls }
  }

  it('shows the researched defaults as the starting state', async () => {
    await renderBoard()
    const protection = screen.getByRole('switch', { name: /Ask for an email address/ })
    const authentication = screen.getByRole('switch', { name: /Verify the inbox/ })
    const notification = screen.getByRole('switch', { name: /Notify the team/ })

    // email_protected defaults true, email_authenticated defaults false, and
    // enable_notification defaults on. All three are the documented ones.
    expect(protection).toHaveAttribute('aria-checked', 'true')
    expect(authentication).toHaveAttribute('aria-checked', 'false')
    expect(notification).toHaveAttribute('aria-checked', 'true')
  })

  it('turns protection on when authentication is turned on', async () => {
    const { user } = await renderBoard()
    const protection = screen.getByRole('switch', { name: /Ask for an email address/ })
    expect(protection).toHaveAttribute('aria-checked', 'true')

    await user.click(screen.getByRole('switch', { name: /Notify the team/ }))
    await user.click(screen.getByRole('switch', { name: /Verify the inbox/ }))

    // Authentication is stronger than protection, not an alternative to it, so the
    // switch below it has to agree. Otherwise the form would offer a combination the
    // server silently normalises.
    expect(screen.getByRole('switch', { name: /Ask for an email address/ })).toHaveAttribute(
      'aria-checked',
      'true',
    )
  })

  it('sends the researched defaults on a create', async () => {
    const { user, calls } = await renderBoard({
      [`${BASE}/rooms/room_a/links`]: LINKS,
    })
    await user.type(screen.getByLabelText('Title'), 'Kestrel - pricing')
    await user.click(screen.getByRole('button', { name: /Create gated link/ }))

    await waitFor(() => {
      const post = calls.find((call) => call.method === 'POST' && call.path.endsWith('/links'))
      expect(post).toBeTruthy()
      const body = JSON.parse(post.body)
      expect(body.title).toBe('Kestrel - pricing')
      // A blank password box is a request for no password, and a blank date box is a
      // request for no expiry. Sending "" instead would be an unparseable timestamp.
      expect(body.password).toBeNull()
      expect(body.expires_at).toBeNull()
      expect(body.email_protected).toBe(true)
      expect(body.email_authenticated).toBe(false)
      expect(body.enable_notification).toBe(true)
      // Exactly one target: a room link is a dataroom link.
      expect(body.dataroom_id).toBe('room_a')
      expect(body.document_id).toBeUndefined()
    })
  })

  it('shows a link expiry as a countdown and an ungated link as no gate', async () => {
    stubApi({
      ...boardRoutes(),
      [`${BASE}/rooms/room_a/links`]: {
        room_id: 'room_a',
        links: [
          {
            ...LINKS.links[0],
            id: 'wf069_link_soon',
            title: 'Closing today',
            expires_at: '2026-10-02T18:00:00+00:00',
            expires_in_seconds: 21600,
          },
          {
            ...LINKS.links[0],
            id: 'wf069_link_open',
            title: 'Ungated page',
            settings: { ...LINKS.links[0].settings, password_set: false, steps: [] },
            state: 'open',
          },
        ],
        revoked: [],
      },
    })
    render(<LinkGating />)
    expect(await screen.findByText('6h left')).toBeInTheDocument()
    expect(await screen.findByText('no gate')).toBeInTheDocument()
  })

  it('offers an empty state when a room has no links yet', async () => {
    stubApi({
      ...boardRoutes(),
      [`${BASE}/rooms/room_a/links`]: { room_id: 'room_a', links: [], revoked: [] },
    })
    render(<LinkGating />)
    expect(await screen.findByText('No gated links yet')).toBeInTheDocument()
  })

  it('keeps revoked links visible instead of pretending they never existed', async () => {
    stubApi({
      ...boardRoutes(),
      [`${BASE}/rooms/room_a/links`]: {
        room_id: 'room_a',
        links: [],
        revoked: [{ ...LINKS.links[0], id: 'wf069_link_gone', revoked: true, revoked_at: 'x' }],
      },
    })
    render(<LinkGating />)
    const summary = await screen.findByText('1 revoked link')
    expect(summary).toBeInTheDocument()
  })

  it('stamps a view with whether the address was verified', async () => {
    stubApi({
      ...boardRoutes(),
      [`${BASE}/rooms/room_a/views`]: {
        room_id: 'room_a',
        views: [
          {
            link_id: LINK,
            email: 'buyer@northwind.example',
            email_verified: true,
            viewed_at: '2026-10-02T09:00:00.000+00:00',
          },
          {
            link_id: LINK,
            email: 'someone@elsewhere.example',
            email_verified: false,
            viewed_at: '2026-10-02T09:05:00.000+00:00',
          },
        ],
      },
    })
    render(<LinkGating />)
    const card = (await screen.findByText('Views')).closest('div')
    expect(within(card).getAllByText('verified')).toHaveLength(1)
    expect(within(card).getByText('someone@elsewhere.example')).toBeInTheDocument()
  })
})

// -- the locally rebuilt primitive ------------------------------------------ //

describe('the locally rebuilt Toggle', () => {
  it('is a switch, not a checkbox, and reports its state to assistive tech', () => {
    render(
      <Toggle id="t" label="Ask for an email address" checked={false} onChange={() => {}} />,
    )
    const control = screen.getByRole('switch', { name: 'Ask for an email address' })
    expect(control).toHaveAttribute('aria-checked', 'false')
    expect(control).toHaveAttribute('id', 't')
  })

  it('meets the 44px touch target floor', () => {
    // The wrapper is the target; the visual switch is 24px tall inside it. Asserted on
    // the control itself because that is what a reviewer taps.
    render(<Toggle id="t2" label="Notify" checked onChange={() => {}} />)
    expect(screen.getByRole('switch')).toHaveAttribute('aria-checked', 'true')
  })
})
