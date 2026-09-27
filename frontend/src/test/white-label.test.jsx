import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import PublicRoom from '@/features/wf-017-white-label/PublicRoom.jsx'
import WhiteLabel from '@/features/wf-017-white-label/WhiteLabel.jsx'
import descriptor, {
  mountShareLinkIfRequested,
  shareLinkPath,
} from '@/features/wf-017-white-label/index.jsx'
import { BASE, CONFIG, ROOMS, httpError, report, stubApi, whiteLabel } from './fixtures.js'

/**
 * Tests for the white-label pages.
 *
 * These pin the behaviour the research specifies, plus the two things a UI can
 * quietly get wrong: that a domain's status is never communicated by colour
 * alone, and that the non-removable link secret is never offered as editable.
 *
 * Every stub is keyed on this feature's own paths under `/wf-017-white-label`.
 * That prefix is the port's one structural change to the HTTP surface, and a
 * stub keyed on the branch's old `/white-label/...` would fail on a fetch error
 * rather than on the behaviour under test -- so the prefix is written once, in
 * `fixtures.js`, and never spelled out here.
 */

const baseRoutes = {
  '/records/room': ROOMS,
  [`${BASE}/config`]: CONFIG,
}

/** Render and wait for the page to finish its initial load.
 *
 *  Every interaction below depends on the room list and the deployment config
 *  having arrived, so the wait happens here once rather than as a race in each
 *  test. */
async function renderPage(routes = baseRoutes) {
  const calls = stubApi(routes)
  const user = userEvent.setup()
  render(<WhiteLabel />)
  await screen.findByLabelText('Sales room')
  return { user, calls }
}

/** Choose a room and wait for its white-label state to load. */
async function chooseRoom(user, roomId = 'room_acme') {
  await user.selectOptions(screen.getByLabelText('Sales room'), roomId)
  await waitFor(() => expect(screen.getByText('Buyer link')).toBeInTheDocument())
}

/** Type a domain and run verification, waiting for the check results. */
async function verifyDomain(user, domain = 'proposals.acme.com') {
  await user.type(screen.getByLabelText('Custom domain'), domain)
  await user.click(screen.getByRole('button', { name: /Verify domain/ }))
  await screen.findByRole('list', { name: 'Domain verification checks' })
}

function renderPublicRoom(path = '/r/Proposal-Name-aB3xY9zK1q', body) {
  stubApi({ [`${BASE}/resolve`]: body })
  return render(<PublicRoom path={path} />)
}

describe('registration', () => {
  it('exports a descriptor the host can discover', () => {
    // The glob in lib/features.js only keeps a module whose *default* export has
    // a Component and an id, so a descriptor without them is a silent no-op.
    expect(descriptor.id).toBe('wf-017-white-label')
    expect(descriptor.label).toBe('White-label')
    expect(typeof descriptor.Component).toBe('function')
  })

  it('carries its own glyph rather than an edit to the shared icon map', () => {
    expect(descriptor.iconPath).toBeTruthy()
    // `icon` still names something in PATHS, because Icon falls back to it and
    // a bare `iconPath` with no fallback would draw nothing on an old build.
    expect(descriptor.icon).toBeTruthy()
  })
})

describe('shareLinkPath', () => {
  it('recognises the researched path shape', () => {
    expect(shareLinkPath('/r/Proposal-Name-aB3xY9zK1q')).toBe('/r/Proposal-Name-aB3xY9zK1q')
    expect(shareLinkPath('/r/Northwind-Traders-aB3xY9zK1q/')).toBe('/r/Northwind-Traders-aB3xY9zK1q/')
  })

  it('leaves every other path to the application', () => {
    for (const path of ['/', '/rooms', '/r/', '/r', '/r/one/two', '/api/health', '', null, 42]) {
      expect(shareLinkPath(path)).toBeNull()
    }
  })
})

describe('mountShareLinkIfRequested', () => {
  const originalPath = window.location.pathname

  function goto(path) {
    window.history.pushState({}, '', path)
  }

  beforeEach(() => {
    // Stand in for the `#root` element `index.html` provides. Without it the
    // shim has nothing to hide, and the property that matters most -- that the
    // application's own root survives untouched -- cannot be asserted at all.
    const root = document.createElement('div')
    root.id = 'root'
    document.body.appendChild(root)
  })

  afterEach(() => {
    document.getElementById('wf-017-share-link')?.remove()
    document.getElementById('root')?.remove()
    window.history.pushState({}, '', originalPath)
  })

  it('does nothing on an ordinary admin visit', () => {
    // The single most important property of the shim: a feature must not take
    // the dashboard away from the people who use it every day.
    goto('/')
    expect(mountShareLinkIfRequested()).toBe(false)
    expect(document.getElementById('wf-017-share-link')).toBeNull()
    expect(document.getElementById('root').style.display).toBe('')
  })

  it('renders the room and hides the application root on a share link', async () => {
    stubApi({
      [`${BASE}/resolve`]: {
        room_id: 'room_acme',
        name: 'Proposal Name',
        served_on_custom_domain: true,
        white_label: whiteLabel(),
      },
    })
    goto('/r/Proposal-Name-aB3xY9zK1q')

    // act() because the shim creates its own root and renders into it; the
    // render is what the next assertion is about.
    await act(async () => {
      expect(mountShareLinkIfRequested()).toBe(true)
    })

    expect(await screen.findByRole('heading', { name: 'Proposal Name' })).toBeInTheDocument()
    expect(document.getElementById('root').style.display).toBe('none')
    // The application's own root is still there, just hidden. The shim hides;
    // it never unmounts or replaces it.
    expect(document.getElementById('root').isConnected).toBe(true)
  })

  it('follows the browser Back button between two share links', async () => {
    // The shim renders a component rather than a fixed `<PublicRoom path={...} />`
    // precisely so this works. A buyer who opens link A, link B, and presses Back
    // must see A again; capturing the path once at mount left them looking at B,
    // and no test could see it because every shim test drove the mount by hand
    // rather than navigating.
    // The stub body is called with no arguments, so which room comes back is
    // driven by a variable the test moves, not by the request.
    let current = { room_id: 'room_a', name: 'Room A', white_label: whiteLabel() }
    stubApi({ [`${BASE}/resolve`]: () => current })

    goto('/r/Room-A-demoLnkN2d')
    act(() => {
      expect(mountShareLinkIfRequested()).toBe(true)
    })
    expect(await screen.findByRole('heading', { name: 'Room A' })).toBeInTheDocument()

    // A client-side navigation, which is what a buyer following a link does.
    current = { room_id: 'room_b', name: 'Room B', white_label: whiteLabel({ room_id: 'room_b' }) }
    window.history.pushState({}, '', '/r/Room-B-demoLnkC3e')
    act(() => {
      window.dispatchEvent(new PopStateEvent('popstate'))
    })
    expect(await screen.findByRole('heading', { name: 'Room B' })).toBeInTheDocument()

    // And back again.
    current = { room_id: 'room_a', name: 'Room A', white_label: whiteLabel() }
    window.history.pushState({}, '', '/r/Room-A-demoLnkN2d')
    act(() => {
      window.dispatchEvent(new PopStateEvent('popstate'))
    })
    expect(await screen.findByRole('heading', { name: 'Room A' })).toBeInTheDocument()
  })

  it('does not mount a second room if it runs twice', async () => {
    stubApi({
      [`${BASE}/resolve`]: { room_id: 'room_acme', name: 'Proposal Name', white_label: whiteLabel() },
    })
    goto('/r/Proposal-Name-aB3xY9zK1q')

    act(() => {
      expect(mountShareLinkIfRequested()).toBe(true)
      expect(mountShareLinkIfRequested()).toBe(true)
    })
    expect(document.querySelectorAll('#wf-017-share-link')).toHaveLength(1)

    // Let the first root's fetch settle before the test ends, so its state
    // update is not reported as an unwrapped update after the fact.
    await screen.findByRole('heading', { name: 'Proposal Name' })
  })

  it('gives the application back when the path stops matching', async () => {
    stubApi({
      [`${BASE}/resolve`]: { room_id: 'room_acme', name: 'Proposal Name', white_label: whiteLabel() },
    })
    goto('/r/Proposal-Name-aB3xY9zK1q')
    act(() => {
      mountShareLinkIfRequested()
    })
    expect(document.getElementById('wf-017-share-link')).not.toBeNull()
    await screen.findByRole('heading', { name: 'Proposal Name' })

    goto('/')
    act(() => {
      expect(mountShareLinkIfRequested()).toBe(false)
    })
    expect(document.getElementById('wf-017-share-link')).toBeNull()
    expect(document.getElementById('root').style.display).toBe('')
  })
})

describe('WhiteLabel', () => {
  it('leads with the CNAME target the deployment is configured for', async () => {
    await renderPage()
    // The target appears twice on purpose: as the deployment's current badge and
    // as the value in the DNS record the operator has to create.
    expect(screen.getAllByText('cname.dsr.test')).toHaveLength(2)
    expect(screen.getByText(/24 hours/)).toBeInTheDocument()
    expect(screen.getByText(/DNS only/)).toBeInTheDocument()
  })

  it('numbers the steps in the researched order', async () => {
    await renderPage()
    const headings = screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent)
    expect(headings[0]).toMatch(/1\. Point a CNAME/)
    expect(headings[1]).toMatch(/2\. Choose a room/)
    expect(headings[2]).toMatch(/3\. Verify and save a domain/)
  })

  it('shows the buyer link on the custom host and the default-host link beside it', async () => {
    const { user } = await renderPage({
      ...baseRoutes,
      [`${BASE}/rooms/room_acme/white-label`]: whiteLabel(),
    })
    await chooseRoom(user)

    expect(
      screen.getByText('https://proposals.acme.com/r/Proposal-Name-aB3xY9zK1q'),
    ).toBeInTheDocument()
    expect(
      screen.getByText('http://127.0.0.1:8000/r/Proposal-Name-aB3xY9zK1q'),
    ).toBeInTheDocument()
  })

  it('labels the collaborator link as internal rather than as a buyer link', async () => {
    // Sourced: collaborator URLs are internal only and bypass the custom domain.
    const { user } = await renderPage({
      ...baseRoutes,
      [`${BASE}/rooms/room_acme/white-label`]: whiteLabel(),
    })
    await chooseRoom(user)

    expect(screen.getByText('Internal collaborator link')).toBeInTheDocument()
    const copyButtons = screen
      .getAllByRole('button')
      .map((b) => b.getAttribute('aria-label'))
      .filter(Boolean)
    expect(copyButtons.some((label) => label.includes('/collab/') && label.includes('buyer'))).toBe(false)
    expect(copyButtons.some((label) => label.includes('/collab/'))).toBe(true)
  })

  it('communicates verification status with text, not colour alone', async () => {
    const { user } = await renderPage({
      ...baseRoutes,
      [`${BASE}/rooms/room_acme/white-label`]: whiteLabel(),
    })
    await chooseRoom(user)

    expect(screen.getByText('Verified')).toBeInTheDocument()
  })

  it('distinguishes a domain still waiting on DNS from a failed check', async () => {
    const { user } = await renderPage({
      ...baseRoutes,
      [`${BASE}/rooms/room_acme/white-label`]: whiteLabel({
        domain: 'proposals.acme.com',
        domain_status: 'unverified',
      }),
    })
    await chooseRoom(user)

    expect(screen.getByText('Awaiting DNS')).toBeInTheDocument()
    expect(screen.queryByText('Check failed')).toBeNull()
  })

  it('offers a re-check while a domain is waiting on propagation', async () => {
    const { user } = await renderPage({
      ...baseRoutes,
      [`${BASE}/rooms/room_acme/white-label`]: whiteLabel({ domain_status: 'unverified' }),
    })
    await chooseRoom(user)

    expect(screen.getByRole('button', { name: /Re-check/ })).toBeInTheDocument()
  })

  it('reports every failed check with the reason it failed', async () => {
    const { user } = await renderPage({
      ...baseRoutes,
      [`${BASE}/verify`]: report({ ready: false, status: 'failed' }),
    })

    await verifyDomain(user)

    expect(screen.getByText('Check failed')).toBeInTheDocument()
    // The whole point of a separate verify step is seeing what is wrong.
    expect(screen.getByText(/resolves to cname.dsr.test/)).toBeInTheDocument()
  })

  it('does not offer to save a domain before a room is chosen', async () => {
    const { user } = await renderPage({ ...baseRoutes, [`${BASE}/verify`]: report() })
    await verifyDomain(user)

    expect(screen.queryByRole('button', { name: /Save domain/ })).toBeNull()
    expect(screen.getByText(/Choose a room above/)).toBeInTheDocument()
  })

  it('saves a verified domain to the chosen room', async () => {
    const { user, calls } = await renderPage({
      ...baseRoutes,
      [`${BASE}/verify`]: report(),
      [`${BASE}/rooms/room_acme/white-label/domain`]: whiteLabel(),
    })

    await chooseRoom(user)
    await verifyDomain(user)
    await user.click(await screen.findByRole('button', { name: /Save domain/ }))

    await waitFor(() => {
      const claim = calls.find((c) => c.path.includes('/white-label/domain') && c.method === 'POST')
      expect(claim).toBeDefined()
      expect(JSON.parse(claim.body)).toEqual({ domain: 'proposals.acme.com' })
      // The researched verify-then-save order: a clean check is saved without
      // the operator having to override anything.
      expect(claim.path).not.toContain('force=true')
    })
  })

  it('confirms before saving a domain whose checks failed, and writes nothing when cancelled', async () => {
    const calls = stubApi({
      ...baseRoutes,
      [`${BASE}/verify`]: report({ ready: false, status: 'failed' }),
    })
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    const user = userEvent.setup()
    render(<WhiteLabel />)
    await screen.findByLabelText('Sales room')

    await user.selectOptions(screen.getByLabelText('Sales room'), 'room_acme')
    await verifyDomain(user)
    await user.click(await screen.findByRole('button', { name: /Save domain/ }))

    expect(confirm).toHaveBeenCalled()
    // The assertion that matters: cancelling must write nothing. Reading back
    // the mock's own `mockReturnValue(false)` proves nothing -- it is true by
    // construction -- so the claim is checked against the requests actually made.
    expect(
      calls.filter((c) => c.path.includes('/white-label/domain') && c.method === 'POST'),
    ).toHaveLength(0)
  })

  it('forces the save only when the operator confirms it', async () => {
    const { user, calls } = await renderPage({
      ...baseRoutes,
      [`${BASE}/verify`]: report({ ready: false, status: 'failed' }),
      [`${BASE}/rooms/room_acme/white-label/domain`]: whiteLabel(),
    })
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    await chooseRoom(user)
    await verifyDomain(user)
    await user.click(await screen.findByRole('button', { name: /Save domain/ }))

    await waitFor(() => {
      const claim = calls.find((c) => c.path.includes('/white-label/domain') && c.method === 'POST')
      // `force` is a query parameter, so the recorded path carries it after the
      // route; the point of the assertion is that the flag is actually sent.
      expect(claim?.path).toContain('force=true')
    })
  })

  it('refuses to save a domain it has not verified', async () => {
    // A report describes one specific host. If the operator edits the field after
    // verifying, the report says nothing about what is about to be saved -- and
    // the consequence is not cosmetic: without this guard a *failed* check on
    // a.acme.com would let b.acme.com be saved with `force=false` and no
    // confirmation at all, which is exactly the warning the forced save exists
    // to provide.
    const { user, calls } = await renderPage({
      ...baseRoutes,
      // The report has to be about `a.acme.com`, because the guard under test
      // compares the report's domain with the field's. A report about a
      // different host would be rejected for the wrong reason.
      [`${BASE}/verify`]: report({ ready: false, status: 'failed', domain: 'a.acme.com' }),
      [`${BASE}/rooms/room_acme/white-label/domain`]: whiteLabel(),
    })

    await chooseRoom(user)
    await verifyDomain(user, 'a.acme.com')
    expect(screen.getByRole('button', { name: /Save domain/ })).toBeInTheDocument()

    // Append to the field so it no longer matches the report.
    await user.type(screen.getByLabelText('Custom domain'), 'b.acme.com')

    // The control disappears rather than offering to save an unverified host.
    await waitFor(() => expect(screen.queryByRole('button', { name: /Save domain/ })).toBeNull())
    expect(
      calls.filter((c) => c.path.includes('/white-label/domain') && c.method === 'POST'),
    ).toHaveLength(0)
  })

  it('does not carry half-typed brand tokens over to another room', async () => {
    // The brand form is the one piece of state that is not obviously scoped to
    // the selected room, so it is the one that leaks: type a colour for room A,
    // switch to room B, and the old code wrote A's colour onto B.
    const { user, calls } = await renderPage({
      ...baseRoutes,
      [`${BASE}/rooms/room_acme/white-label`]: whiteLabel(),
      // A room with no domain at all, so the empty-state line is a reliable
      // signal that the second room has finished loading.
      [`${BASE}/rooms/room_northwind/white-label`]: whiteLabel({
        room_id: 'room_northwind',
        domain: null,
        domain_status: 'unverified',
      }),
      [`${BASE}/rooms/room_acme/white-label/branding`]: whiteLabel(),
    })

    await chooseRoom(user, 'room_acme')
    await user.type(screen.getByLabelText('Accent colour'), '#22c55e')

    await user.selectOptions(screen.getByLabelText('Sales room'), 'room_northwind')
    await waitFor(() =>
      expect(screen.getByText('No custom domain set. Links use the default host.')).toBeInTheDocument(),
    )
    // The form is cleared, so the new room's form shows its own (empty) state.
    expect(screen.getByLabelText('Accent colour')).toHaveValue('')

    calls.length = 0
    await user.click(screen.getByRole('button', { name: 'Save brand tokens' }))
    // "Nothing to save" is the correct outcome; a POST carrying #22c55e for
    // room_northwind is the bug.
    expect(await screen.findByText('Nothing to save')).toBeInTheDocument()
    expect(calls.filter((c) => c.path.includes('/branding'))).toHaveLength(0)
  })

  it('seeds the brand form from what is stored, so the operator can see it', async () => {
    // Blank-only fields would make it impossible to tell "unset" from
    // "unknown", and would invite retyping a value that is already correct.
    const { user } = await renderPage({
      ...baseRoutes,
      [`${BASE}/rooms/room_acme/white-label`]: whiteLabel({
        branding: { primary: '#0f172a', accent: '#22c55e' },
      }),
    })
    await chooseRoom(user)

    expect(screen.getByLabelText('Primary colour')).toHaveValue('#0f172a')
    expect(screen.getByLabelText('Accent colour')).toHaveValue('#22c55e')
    // A stored value that is not a string renders as an empty field rather than
    // as `String(value)`, which for an object would be "[object Object]" and
    // would then be sent back as a brand token.
  })

  it('never offers the link secret as an editable field', async () => {
    // Sourced: the secret is a "non-removable identifier", so the UI must not
    // offer a control implying it can be changed.
    const { user } = await renderPage({
      ...baseRoutes,
      [`${BASE}/rooms/room_acme/white-label`]: whiteLabel(),
    })
    await chooseRoom(user)

    expect(screen.queryByLabelText(/link secret/i)).toBeNull()
    expect(screen.queryByRole('button', { name: /Rotate secret/i })).toBeNull()
  })

  it('offers to create the secret only when the room has none', async () => {
    const { user } = await renderPage({
      ...baseRoutes,
      [`${BASE}/rooms/room_acme/white-label`]: whiteLabel({
        has_link_secret: false,
        share_url: undefined,
        default_host_share_url: undefined,
        path: undefined,
        slug: undefined,
        collaborator_url: undefined,
      }),
    })
    await user.selectOptions(screen.getByLabelText('Sales room'), 'room_acme')

    expect(await screen.findByRole('button', { name: /Create the link secret/ })).toBeInTheDocument()
  })

  it('sends only the brand fields the operator actually filled in', async () => {
    const { user, calls } = await renderPage({
      ...baseRoutes,
      [`${BASE}/rooms/room_acme/white-label`]: whiteLabel(),
      [`${BASE}/rooms/room_acme/white-label/branding`]: whiteLabel(),
    })
    await chooseRoom(user)

    await user.type(screen.getByLabelText('Accent colour'), '#22c55e')
    await user.click(screen.getByRole('button', { name: 'Save brand tokens' }))

    await screen.findByText('Brand tokens saved')
    // A field left blank must not be sent as "", or it would wipe a token that
    // is already stored. Only the field that was typed appears in the payload.
    const save = calls.find((c) => c.path.includes('/white-label/branding'))
    expect(JSON.parse(save.body)).toEqual({ accent: '#22c55e' })
  })

  it('surfaces a server-side rejection of an unsafe brand token', async () => {
    const { user } = await renderPage({
      ...baseRoutes,
      [`${BASE}/rooms/room_acme/white-label`]: whiteLabel(),
      [`${BASE}/rooms/room_acme/white-label/branding`]: httpError(422, {
        detail: 'branding.accent is not a valid accent token',
      }),
    })
    await chooseRoom(user)

    await user.type(screen.getByLabelText('Accent colour'), 'url(https://evil.test)')
    await user.click(screen.getByRole('button', { name: 'Save brand tokens' }))

    expect(await screen.findByText(/not a valid accent token/)).toBeInTheDocument()
  })

  it('explains that a retired domain did not break shared links', async () => {
    const { user } = await renderPage({
      ...baseRoutes,
      [`${BASE}/rooms/room_acme/white-label`]: whiteLabel({
        domain_history: [{ domain: 'old.acme.com', retired_at: '2026-09-01T00:00:00.000+00:00' }],
      }),
    })
    await chooseRoom(user)

    expect(screen.getByText('old.acme.com')).toBeInTheDocument()
    expect(screen.getByText(/never breaks a shared link/)).toBeInTheDocument()
  })

  it('tells the operator when there is nothing to white-label', async () => {
    stubApi({ ...baseRoutes, '/records/room': { count: 0, records: [] } })
    render(<WhiteLabel />)

    expect(await screen.findByText('No rooms to white-label yet')).toBeInTheDocument()
  })

  it('offers a release control only once a domain is set', async () => {
    const { user } = await renderPage({
      ...baseRoutes,
      [`${BASE}/rooms/room_acme/white-label`]: whiteLabel({ domain: null, domain_status: 'unverified' }),
    })
    await user.selectOptions(screen.getByLabelText('Sales room'), 'room_acme')

    await waitFor(() =>
      expect(screen.getByText('No custom domain set. Links use the default host.')).toBeInTheDocument(),
    )
    expect(screen.queryByRole('button', { name: /Release/ })).toBeNull()
  })
})

describe('PublicRoom', () => {
  const resolved = (overrides = {}) => ({
    room_id: 'room_acme',
    name: 'Proposal Name',
    served_on_custom_domain: true,
    white_label: whiteLabel(),
    ...overrides,
  })

  it('resolves the path and shows the room', async () => {
    renderPublicRoom('/r/Proposal-Name-aB3xY9zK1q', resolved())
    expect(await screen.findByRole('heading', { name: 'Proposal Name' })).toBeInTheDocument()
  })

  it('sends the host it was served on, so the server can tell the two apart', async () => {
    // Without this the "Link verified" badge on the default host would be a
    // guess, and the researched guarantee that one link works on both hosts
    // would be unobservable.
    const calls = stubApi({ [`${BASE}/resolve`]: resolved() })
    render(<PublicRoom path="/r/Proposal-Name-aB3xY9zK1q" />)
    await screen.findByRole('heading', { name: 'Proposal Name' })

    expect(calls[0].path).toContain('host=')
    expect(calls[0].path).toContain(encodeURIComponent(window.location.host))
  })

  it('shows the custom domain when the link was served on it', async () => {
    renderPublicRoom('/r/Proposal-Name-aB3xY9zK1q', resolved())
    expect(await screen.findByText('proposals.acme.com')).toBeInTheDocument()
  })

  it('still shows the room when the link was served on the default host', async () => {
    // The researched guarantee: a link shared before the domain was configured
    // keeps working. The badge changes, the room does not disappear.
    renderPublicRoom('/r/Proposal-Name-aB3xY9zK1q', resolved({ served_on_custom_domain: false }))
    expect(await screen.findByRole('heading', { name: 'Proposal Name' })).toBeInTheDocument()
    expect(screen.getByText('Link verified')).toBeInTheDocument()
  })

  it('applies brand tokens as scoped custom properties, not a stylesheet', async () => {
    const { container } = renderPublicRoom(
      '/r/Proposal-Name-aB3xY9zK1q',
      resolved({ white_label: whiteLabel({ branding: { primary: '#0f172a', accent: '#22c55e' } }) }),
    )
    await screen.findByRole('heading', { name: 'Proposal Name' })

    const wrapper = container.firstChild
    expect(wrapper.style.getPropertyValue('--wl-primary')).toBe('#0f172a')
    expect(wrapper.style.getPropertyValue('--wl-accent')).toBe('#22c55e')
    // Nothing is injected into the document head.
    expect(document.head.querySelector('style')).toBeNull()
  })

  it('ignores a brand token that is not a string', async () => {
    const { container } = renderPublicRoom(
      '/r/Proposal-Name-aB3xY9zK1q',
      resolved({
        white_label: whiteLabel({ branding: { accent: { nested: true }, primary: '#0f172a' } }),
      }),
    )
    await screen.findByRole('heading', { name: 'Proposal Name' })

    expect(container.firstChild.style.getPropertyValue('--wl-accent')).toBe('')
    expect(container.firstChild.style.getPropertyValue('--wl-primary')).toBe('#0f172a')
  })

  it('explains a link the server could not resolve', async () => {
    renderPublicRoom(
      '/r/Proposal-Name-aB3xY9zK1q',
      httpError(404, { detail: 'no room matches that link secret' }),
    )

    expect(await screen.findByText('no room matches that link secret')).toBeInTheDocument()
    expect(screen.getByText(/Ask for the link again/)).toBeInTheDocument()
  })

  it('does not crash on a 200 that resolves to no room', async () => {
    // The API is schema-flexible, so a response can be well-formed and still
    // carry nothing to render. That must be a message, not a blank page.
    renderPublicRoom('/r/Proposal-Name-aB3xY9zK1q', { room_id: 'room_acme' })

    expect(await screen.findByText('This link did not resolve to a room.')).toBeInTheDocument()
  })

  it('does not show the word "null" to a buyer when the response is empty', async () => {
    // `ErrorNote` stringifies whatever it is handed, so rendering it with a null
    // error printed the literal word "null" under a heading that said the load
    // had failed. The error branch is only for a real error.
    renderPublicRoom('/r/Proposal-Name-aB3xY9zK1q', { room_id: 'room_acme' })

    expect(await screen.findByText('This link did not resolve to a room.')).toBeInTheDocument()
    expect(screen.queryByText('Could not load data')).toBeNull()
    expect(document.body.textContent).not.toContain('null')
  })
})

describe('brand token rendering', () => {
  it('applies a stored token to the scoped custom property', async () => {
    const { container } = renderPublicRoom(
      '/r/Proposal-Name-aB3xY9zK1q',
      {
        room_id: 'room_acme',
        name: 'Proposal Name',
        white_label: whiteLabel({ branding: { heading_font: 'Fira Code, monospace' } }),
      },
    )
    await screen.findByRole('heading', { name: 'Proposal Name' })

    expect(container.firstChild.style.getPropertyValue('--wl-heading-font')).toBe(
      'Fira Code, monospace',
    )
  })
})

describe('the re-check control', () => {
  it('uses the shared refresh glyph, not a substitute', async () => {
    // A regression this file could not see: the control asked this feature's own
    // glyph map for `refresh`, which is a *shared* glyph, so the map returned
    // undefined and the shared Icon silently fell back to its last-resort mark.
    // Glyph now throws on an unknown name, and this test pins the rendered path.
    const { user } = await renderPage({
      ...baseRoutes,
      [`${BASE}/rooms/room_acme/white-label`]: whiteLabel({ domain_status: 'unverified' }),
    })
    await chooseRoom(user)

    const button = screen.getByRole('button', { name: /Re-check/ })
    const path = button.querySelector('svg path')
    expect(path.getAttribute('d')).toBe(
      'M4 4v6h6M20 20v-6h-6M20 9a8 8 0 00-14.3-3M4 15a8 8 0 0014.3 3',
    )
  })
})
