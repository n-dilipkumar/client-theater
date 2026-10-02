/**
 * WF-076: revoke access early, and keep the row that proves it.
 *
 * Two things are worth pinning from the frontend side.
 *
 * **The retained row is shown, not just claimed.** A revoke's whole point is
 * that the URL stops resolving *and* the record survives, so these tests assert
 * the retained row and its audit trail appear together with the cut. A page
 * that reported only "revoked" would satisfy the guarantee's first half and
 * quietly drop the second.
 *
 * **The destructive controls are gated the way the backend gates them.** The
 * backend refuses a group delete and a purge unless `confirm` echoes the id
 * being destroyed, so a page that could send either without that id would render
 * a button that cannot work - and a confirmation dialog whose yes button
 * hard-codes `true` would gate nothing.
 *
 * Every stub is keyed on this feature's own `/wf-076` prefix, so a request
 * outside it throws rather than being answered by a lookalike route.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import RevocationPage from '@/features/wf-076-revoke-access-early-and-keep-the-audit/RevocationPage.jsx'
import descriptor from '@/features/wf-076-revoke-access-early-and-keep-the-audit/index.jsx'
import { revocationApi } from '@/features/wf-076-revoke-access-early-and-keep-the-audit/api.js'
import { httpError, stubApi } from './fixtures.js'
import {
  BASE,
  GROUP,
  LINKS,
  REVOKE_RESULT,
  ROOM_ID,
  resolve,
  routes,
} from './wf076-fixtures.js'

/** Render the page with a stubbed API, and wait until its link list has arrived.
 *
 *  Waiting for the tab strip alone is not enough: the tabs render before the
 *  room-scoped reads resolve, so a test that then looks for a Revoke button races
 *  the fetch and fails on a page that is about to be correct. Every assertion
 *  below is about content inside the list, so this waits for the list.
 *
 *  The stub that does it is keyed on this test's own prefix; a room with no links
 *  is asserted separately through its own empty-state test. */
async function open(overrides = {}, { waitForLinks = true } = {}) {
  const calls = stubApi(routes(overrides))
  const user = userEvent.setup()
  render(<RevocationPage />)
  await screen.findByRole('tab', { name: /Share links/ })
  if (waitForLinks) await screen.findAllByRole('button', { name: /^Revoke$/ })
  return { user, calls }
}

/** Switch to another section of the page. */
function section(user, name) {
  return user.click(screen.getByRole('tab', { name }))
}

describe('registration', () => {
  it('exports a descriptor the host can discover', () => {
    // The glob in lib/features.js only keeps a module whose *default* export has
    // a Component and an id, so a descriptor without them is a silent no-op.
    expect(descriptor.id).toBe('wf-076-revoke-access-early-and-keep-the-audit')
    expect(descriptor.label).toBe('Revoke access')
    expect(typeof descriptor.Component).toBe('function')
  })

  it('passes a nav glyph as a path, because PATHS is a shared file', () => {
    expect(typeof descriptor.iconPath).toBe('string')
    expect(descriptor.iconPath.length).toBeGreaterThan(10)
  })
})

describe('the guarantee, stated on the page', () => {
  it('quotes the researched guarantee rather than paraphrasing it', async () => {
    await open()
    expect(await screen.findByText(/Revocation is request-time, not scheduled/)).toBeTruthy()
  })

  it('counts revoked links beside live ones, so the evidence is not implied away', async () => {
    await open()
    await screen.findByText('Live links')
    expect(screen.getByText('1 revoked, rows kept')).toBeTruthy()
  })

  it('shows the grace period as zero minutes', async () => {
    await open()
    await screen.findByText('Grace period')
    expect(screen.getByText('0 min')).toBeTruthy()
  })

  it('says cached copies are not recalled', async () => {
    await open()
    expect(await screen.findByText(/no cached-copy recall/i)).toBeTruthy()
  })
})

describe('share links', () => {
  it('offers Revoke only on a live link', async () => {
    await open()
    await screen.findAllByRole('button', { name: /^Revoke$/ })
    expect(screen.getAllByRole('button', { name: /^Revoke$/ })).toHaveLength(2)
    expect(screen.getByText('revoked')).toBeTruthy()
  })

  it('shows a revoked link as revoked rather than hiding it', async () => {
    await open()
    await screen.findByText(/Diligence embargoed pending the renewal/)
    expect(screen.getByText('Fabrikam diligence (embargoed)')).toBeTruthy()
  })

  it('shows the tombstone slug beside the original it released', async () => {
    await open()
    await screen.findByText('revoked-fabrikam-diligence-9f3a1c02 (was fabrikam-diligence)')
  })

  it('names who revoked a revoked link', async () => {
    await open()
    await screen.findByText('dana')
  })

  it('sends the reason the operator typed with the revoke', async () => {
    const { user, calls } = await open({
      [`${BASE}/rooms/${ROOM_ID}/links/revocation_link_one/revoke`]: REVOKE_RESULT,
    })
    // `type`, not `fill`: the reason field carries a placeholder, and `fill` is not
    // available on every input in this environment. Both set the value the form
    // reads; only `type` is what a person does.
    await user.type(screen.getByLabelText('Reason'), 'Deal went cold.')
    await user.click(screen.getAllByRole('button', { name: /^Revoke$/ })[0])

    await waitFor(() => {
      const sent = calls.find((c) => c.method === 'POST' && c.path.endsWith('/links/revocation_link_one/revoke'))
      expect(sent).toBeDefined()
      expect(JSON.parse(sent.body)).toEqual({ reason: 'Deal went cold.' })
    })
  })

  it('shows the retained row and its audit trail with the cut', async () => {
    const { user } = await open({
      [`${BASE}/rooms/${ROOM_ID}/links/revocation_link_one/revoke`]: REVOKE_RESULT,
    })
    await user.click(screen.getAllByRole('button', { name: /^Revoke$/ })[0])

    await screen.findByText('The row that survived')
    expect(screen.getByText('soft-deleted')).toBeTruthy()
    expect(screen.getByText('kept for audit')).toBeTruthy()
    expect(screen.getByText(/Audit trail, same transaction as the change/)).toBeTruthy()
  })

  it('names the slug the row held and the tombstone it wears', async () => {
    const { user } = await open({
      [`${BASE}/rooms/${ROOM_ID}/links/revocation_link_one/revoke`]: REVOKE_RESULT,
    })
    await user.click(screen.getAllByRole('button', { name: /^Revoke$/ })[0])

    await screen.findByText('The row that survived')
    // Scoped to the retained panel: the link list above still shows this link's
    // original slug, so an unscoped lookup finds two nodes and asserts nothing
    // useful about the row underneath.
    const panel = screen.getByText('The row that survived').closest('div').parentElement
    expect(panel.textContent).toContain('Slug it held')
    expect(panel.textContent).toContain('northwind-overview')
    expect(panel.textContent).toContain('revoked-northwind-overview-1a2b3c4d')
  })

  it('says the row is not reversible, and offers nothing that could undo it', async () => {
    const { user } = await open({
      [`${BASE}/rooms/${ROOM_ID}/links/revocation_link_one/revoke`]: REVOKE_RESULT,
    })
    await user.click(screen.getAllByRole('button', { name: /^Revoke$/ })[0])

    await screen.findByText('Reversible')
    expect(screen.queryByRole('button', { name: /restore|un-revoke|undo/i })).toBeNull()
  })

  it('re-reads a revoked row on demand, from the retained route', async () => {
    const { user, calls } = await open({
      [`${BASE}/rooms/${ROOM_ID}/revocations/revocation_link_gone`]: REVOKE_RESULT.retained,
    })
    await user.click(screen.getByRole('button', { name: /fabrikam-diligence/ }))

    await screen.findByText('The row that survived')
    expect(
      calls.some((c) => c.path === `${BASE}/rooms/${ROOM_ID}/revocations/revocation_link_gone`),
    ).toBe(true)
  })
})

describe('audiences', () => {
  it('offers removal per membership, and names the buyer', async () => {
    const { user } = await open()
    await section(user, /Audiences/)
    const removals = await screen.findAllByRole('button', { name: /^Remove$/ })
    expect(removals.length).toBeGreaterThan(0)
    expect(removals[0].closest('li')).toHaveTextContent('buyer@northwind.example')
  })

  it('shows a hidden item as hidden, in words rather than colour', async () => {
    const { user } = await open()
    await section(user, /Audiences/)
    await screen.findByText('doc_contract')
    expect(screen.getByText('view off')).toBeTruthy()
    expect(screen.getByRole('button', { name: /Show again/ })).toBeTruthy()
  })

  it('states the cascade before the button, not only after it', async () => {
    const { user } = await open()
    await section(user, /Audiences/)
    await screen.findByText(/memberships, its permissions and every link pointing at it/)
  })

  it('sends the group id as the confirmation for a group delete', async () => {
    const { user, calls } = await open({
      [`${BASE}/rooms/${ROOM_ID}/groups/${GROUP.id}/delete`]: { removed: 4 },
    })
    await section(user, /Audiences/)
    await screen.findByText('Procurement')
    const [confirm] = screen.getAllByRole('button', { name: /^Confirm$/ })
    await user.click(confirm)

    await waitFor(() => {
      const sent = calls.find((c) => c.path.endsWith(`/groups/${GROUP.id}/delete`))
      expect(sent).toBeDefined()
      expect(JSON.parse(sent.body)).toEqual({ confirm: GROUP.id })
    })
  })

  it('lists the viewers, so "the underlying viewer is kept" is checkable', async () => {
    const { user } = await open()
    await section(user, /Audiences/)
    await screen.findByText('The viewers underneath')
    expect(screen.getByText('analyst@partner.example')).toBeTruthy()
    expect(screen.getByText('0 memberships')).toBeTruthy()
  })
})

describe('documents', () => {
  it('lists detached rows too, because the detach keeps its row', async () => {
    const { user } = await open()
    await section(user, /Documents/)
    await screen.findByText('Enterprise Overview Deck')
    expect(screen.getByText('attached')).toBeTruthy()
    expect(screen.getByText('detached')).toBeTruthy()
  })

  it('sends the document id on a detach', async () => {
    const { user, calls } = await open()
    await section(user, /Documents/)
    await screen.findByText('Enterprise Overview Deck')
    await user.click(screen.getByRole('button', { name: /^Detach$/ }))

    await waitFor(() => {
      expect(calls.some((c) => c.path.endsWith('/documents/doc_deck/detach'))).toBe(true)
    })
  })

  it('refuses the attach control while the room is frozen, and says why', async () => {
    // The link list is stubbed empty for the frozen room, because this test is
    // about the Documents panel and a link list of its own would only race.
    const { user } = await open(
      {
      [`${BASE}/rooms/room_contoso/state`]: {
        ...routes()[`${BASE}/rooms/${ROOM_ID}/state`],
        room_id: 'room_contoso',
        frozen: true,
        refused_actions: { attach: 'the dataroom is frozen' },
        available_actions: ['purge', 'revoke_link'],
      },
      [`${BASE}/rooms/room_contoso/links`]: { ...LINKS, room_id: 'room_contoso' },
      [`${BASE}/rooms/room_contoso/groups`]: { room_id: 'room_contoso', count: 0, groups: [] },
      [`${BASE}/rooms/room_contoso/viewers`]: { room_id: 'room_contoso', count: 0, viewers: [] },
      [`${BASE}/rooms/room_contoso/documents`]: {
        room_id: 'room_contoso',
        count: 0,
        documents: [],
      },
      [`${BASE}/rooms/room_contoso/trail`]: { room_id: 'room_contoso', count: 0, trail: [] },
      },
      { waitForLinks: false },
    )
    await user.selectOptions(screen.getByLabelText('Room'), 'room_contoso')

    await section(user, /Documents/)
    await screen.findByText(/This dataroom is frozen/)
    expect(screen.getByRole('button', { name: /Refused while frozen/ })).toBeDisabled()
    // The scope matters: revocation is not in the documented frozen list.
    expect(screen.getByText(/you can still revoke a link/)).toBeTruthy()
  })
})

describe('the slug resolver', () => {
  it('says a live URL resolves, and reports the no-grace-period rule', async () => {
    const { user } = await open({ [`${BASE}/rooms/${ROOM_ID}/resolve`]: resolve() })
    await section(user, /Slug resolver/)
    await user.type(screen.getByLabelText('Slug'), 'northwind-overview')
    await user.click(screen.getByRole('button', { name: /Resolve now/ }))

    await screen.findByText('This URL resolves right now.')
    const panel = screen.getByText('The answer').closest('div').parentElement
    expect(panel.textContent).toContain('Grace period')
    expect(panel.textContent).toContain('0 min')
  })

  it('distinguishes revoked from never issued, and names who withheld it', async () => {
    const { user } = await open({
      [`${BASE}/rooms/${ROOM_ID}/resolve`]: resolve({
        resolves: false,
        reason: 'revoked',
        withheld_by: 'revoked',
        link_id: null,
      }),
    })
    await section(user, /Slug resolver/)
    await user.type(screen.getByLabelText('Slug'), 'northwind-overview')
    await user.click(screen.getByRole('button', { name: /Resolve now/ }))

    await screen.findByText('This URL does not resolve, and the reason is on record.')
    expect(screen.getByText('revoked')).toBeTruthy()
  })

  it('says nobody withheld it, when the slug was never issued', async () => {
    const { user } = await open({
      [`${BASE}/rooms/${ROOM_ID}/resolve`]: resolve({
        slug: 'never-sent',
        resolves: false,
        reason: 'not_found',
        link_id: null,
      }),
    })
    await section(user, /Slug resolver/)
    await user.type(screen.getByLabelText('Slug'), 'never-sent')
    await user.click(screen.getByRole('button', { name: /Resolve now/ }))

    await screen.findByText(/the slug was never issued/)
  })
})

describe('the audit trail', () => {
  it('lists the rows this feature wrote, with their sources', async () => {
    const { user } = await open()
    await section(user, /Audit trail/)
    await screen.findByText(`POST ${BASE}/rooms/${ROOM_ID}/links/revocation_link_one/revoke`)
  })

  it('counts a cascade as one row, because that is how it was written', async () => {
    const { user } = await open()
    await section(user, /Audit trail/)
    await screen.findByText('4 records')
  })

  it('explains that a cascade row is the only place the before-state lives', async () => {
    const { user } = await open()
    await section(user, /Audit trail/)
    await screen.findByText(/only place the full before-state of everything it removed lives/)
  })
})

describe('what this infers', () => {
  it('lists the readings the server offers, not a copy compiled into the page', async () => {
    const { user, calls } = await open()
    await section(user, /What this infers/)

    await screen.findByText('cascade-keeps-rows')
    expect(screen.getByText('membership-gates-resolution')).toBeTruthy()
    expect(calls.some((c) => c.path === `${BASE}/inferences`)).toBe(true)
  })

  it('gives each reading a claim, a choice, a reason and a lever', async () => {
    const { user } = await open()
    await section(user, /What this infers/)
    await user.click(await screen.findByText('cascade-keeps-rows'))

    await screen.findByText('What the research says')
    expect(screen.getByText('This build chose')).toBeTruthy()
    expect(screen.getByText('How to change it')).toBeTruthy()
    expect(screen.getByText('HARD_DELETE in dsr/revocation/__init__.py')).toBeTruthy()
  })
})

describe('error handling', () => {
  it("shows the server's sentence rather than a status code", async () => {
    const { user } = await open({
      [`${BASE}/rooms/${ROOM_ID}/links/revocation_link_one/revoke`]: httpError(409, {
        error: 'conflict',
        detail: 'link was already revoked at 2026-10-01T08:00:00.000+00:00',
      }),
    })
    await user.click(screen.getAllByRole('button', { name: /^Revoke$/ })[0])
    await screen.findByText(/already revoked at/)
  })

  it('reports a failed read rather than rendering an empty page', async () => {
    stubApi(routes({ [`${BASE}/rooms/${ROOM_ID}/links`]: httpError(500, { error: 'boom' }) }))
    render(<RevocationPage />)
    await screen.findByText(/500|boom/)
  })
})

describe('the api client', () => {
  it('calls only this feature routes, plus the core rooms list', async () => {
    const { calls } = await open()
    for (const call of calls) {
      expect(
        call.path.startsWith(BASE) || call.path.startsWith('/records/room'),
        `${call.path} is outside this feature`,
      ).toBe(true)
    }
  })

  it('requires the caller to pass a confirmation for the irreversible routes', () => {
    // The backend refuses both without it, so a client that defaulted it to true
    // would make the gate meaningless. Arity is the check that catches that.
    expect(revocationApi.deleteGroup.length).toBe(3)
    expect(revocationApi.purge.length).toBe(2)
  })

  it('never offers a restore for a revoked link', () => {
    expect(Object.keys(revocationApi)).not.toContain('restoreLink')
    expect(Object.keys(revocationApi)).not.toContain('undelete')
  })
})

describe('the empty states', () => {
  it('says so when a room has no links at all', async () => {
    await open(
      {
        [`${BASE}/rooms/${ROOM_ID}/links`]: {
          room_id: ROOM_ID,
          include_revoked: true,
          count: 0,
          links: [],
        },
      },
      { waitForLinks: false },
    )
    await screen.findByText('No share links yet')
  })

  it('says so when there are no audiences', async () => {
    const { user } = await open({
      [`${BASE}/rooms/${ROOM_ID}/groups`]: { room_id: ROOM_ID, count: 0, groups: [] },
    })
    await section(user, /Audiences/)
    await screen.findByText('No audiences yet')
  })

  it('says so when the trail is empty', async () => {
    const { user } = await open({
      [`${BASE}/rooms/${ROOM_ID}/trail`]: { room_id: ROOM_ID, count: 0, trail: [] },
    })
    await section(user, /Audit trail/)
    await screen.findByText('Nothing recorded yet')
  })
})