import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import WhiteLabel from '../pages/WhiteLabel.jsx'
import PublicRoom from '../pages/PublicRoom.jsx'
import { CONFIG, ROOMS, httpError, report, stubApi, whiteLabel } from './fixtures.js'

/**
 * Tests for the white-label pages.
 *
 * These pin the behaviour the research specifies, plus the two things a UI can
 * quietly get wrong: that a domain's status is never communicated by colour
 * alone, and that the non-removable link secret is never offered as editable.
 */

const baseRoutes = {
  '/records/room': ROOMS,
  '/white-label/config': CONFIG,
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
  stubApi({ '/white-label/resolve': body })
  return render(<PublicRoom path={path} />)
}

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
    const { user } = await renderPage({ ...baseRoutes, '/rooms/room_acme/white-label': whiteLabel() })
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
    const { user } = await renderPage({ ...baseRoutes, '/rooms/room_acme/white-label': whiteLabel() })
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
    const { user } = await renderPage({ ...baseRoutes, '/rooms/room_acme/white-label': whiteLabel() })
    await chooseRoom(user)

    expect(screen.getByText('Verified')).toBeInTheDocument()
  })

  it('distinguishes a domain still waiting on DNS from a failed check', async () => {
    const { user } = await renderPage({
      ...baseRoutes,
      '/rooms/room_acme/white-label': whiteLabel({
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
      '/rooms/room_acme/white-label': whiteLabel({ domain_status: 'unverified' }),
    })
    await chooseRoom(user)

    expect(screen.getByRole('button', { name: /Re-check/ })).toBeInTheDocument()
  })

  it('reports every failed check with the reason it failed', async () => {
    const { user } = await renderPage({
      ...baseRoutes,
      '/white-label/verify': report({ ready: false, status: 'failed' }),
    })

    await verifyDomain(user)

    expect(screen.getByText('Check failed')).toBeInTheDocument()
    // The whole point of a separate verify step is seeing what is wrong.
    expect(screen.getByText(/resolves to cname.dsr.test/)).toBeInTheDocument()
  })

  it('does not offer to save a domain before a room is chosen', async () => {
    const { user } = await renderPage({ ...baseRoutes, '/white-label/verify': report() })
    await verifyDomain(user)

    expect(screen.queryByRole('button', { name: /Save domain/ })).toBeNull()
    expect(screen.getByText(/Choose a room above/)).toBeInTheDocument()
  })

  it('saves a verified domain to the chosen room', async () => {
    const { user, calls } = await renderPage({
      ...baseRoutes,
      '/white-label/verify': report(),
      '/rooms/room_acme/white-label/domain': whiteLabel(),
    })

    await chooseRoom(user)
    await verifyDomain(user)
    await user.click(await screen.findByRole('button', { name: /Save domain/ }))

    await waitFor(() => {
      const claim = calls.find((c) => c.path.endsWith('/white-label/domain') && c.method === 'POST')
      expect(claim).toBeDefined()
      expect(JSON.parse(claim.body)).toEqual({ domain: 'proposals.acme.com' })
    })
  })

  it('confirms before saving a domain whose checks failed, and honours cancelling', async () => {
    stubApi({ ...baseRoutes, '/white-label/verify': report({ ready: false, status: 'failed' }) })
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    const user = userEvent.setup()
    render(<WhiteLabel />)
    await screen.findByLabelText('Sales room')

    await user.selectOptions(screen.getByLabelText('Sales room'), 'room_acme')
    await verifyDomain(user)
    await user.click(await screen.findByRole('button', { name: /Save domain/ }))

    expect(confirm).toHaveBeenCalled()
    // Cancelled means nothing was written.
    expect(confirm.mock.results[0].value).toBe(false)
  })

  it('never offers the link secret as an editable field', async () => {
    // Sourced: the secret is a "non-removable identifier", so the UI must not
    // offer a control implying it can be changed.
    const { user } = await renderPage({ ...baseRoutes, '/rooms/room_acme/white-label': whiteLabel() })
    await chooseRoom(user)

    expect(screen.queryByLabelText(/link secret/i)).toBeNull()
    expect(screen.queryByRole('button', { name: /Rotate secret/i })).toBeNull()
  })

  it('offers to create the secret only when the room has none', async () => {
    const { user } = await renderPage({
      ...baseRoutes,
      '/rooms/room_acme/white-label': whiteLabel({
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
    const { user } = await renderPage({
      ...baseRoutes,
      '/rooms/room_acme/white-label': whiteLabel(),
      '/rooms/room_acme/white-label/branding': whiteLabel(),
    })
    await chooseRoom(user)

    await user.type(screen.getByLabelText('Accent colour'), '#22c55e')
    await user.click(screen.getByRole('button', { name: 'Save brand tokens' }))

    await waitFor(() => expect(screen.getByText('Brand tokens saved')).toBeInTheDocument())
  })

  it('surfaces a server-side rejection of an unsafe brand token', async () => {
    const { user } = await renderPage({
      ...baseRoutes,
      '/rooms/room_acme/white-label': whiteLabel(),
      '/rooms/room_acme/white-label/branding': httpError(422, {
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
      '/rooms/room_acme/white-label': whiteLabel({
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
      '/rooms/room_acme/white-label': whiteLabel({ domain: null, domain_status: 'unverified' }),
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
})
