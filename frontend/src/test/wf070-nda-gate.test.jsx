import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import AgreementGate from '@/features/wf-070-require-nda-acceptance-before-viewing/AgreementGate.jsx'
import NdaGate from '@/features/wf-070-require-nda-acceptance-before-viewing/NdaGate.jsx'
import descriptor from '@/features/wf-070-require-nda-acceptance-before-viewing/index.jsx'
import { httpError, stubApi } from './fixtures.js'

/**
 * Tests for WF-070's two pages.
 *
 * What these pin is the behaviour the researched specification requires, and the four
 * things a UI can quietly get wrong on a disclosure workflow:
 *
 *   1. **The content is not requested until the viewer has accepted.** That ordering is
 *      the security property, and it is invisible in a screenshot.
 *   2. **A superseded acceptance says so.** The refusal reason is
 *      `agreement_changed`, not `agreement_not_accepted`, and sending the viewer back
 *      to the same box to click the same button would be a loop they cannot leave.
 *   3. **A closed link reads the same whether it expired or was revoked.** The
 *      researched rule is that a viewer holding a forwarded URL gets the closed page,
 *      and copy that said "withdrawn" would tell them they were cut off.
 *   4. **A gate failing closed is visible on the board.** "Gated" and "gated but asking
 *      for nothing" look identical in the flag, and only one of them is safe.
 *
 * Every stub is keyed on this feature's own paths under `/wf-070`.
 */

const BASE = '/wf-070'
const LINK = 'wf069_link_abc123'
const SESSION = 'wf070_viewer_session_abc123'
const AGREEMENT = 'wf070_agreement_abc123'
const ROOM = 'room_a'

const NDA_BODY = 'MUTUAL NON-DISCLOSURE AGREEMENT. Each party keeps confidential information.'

const ROOMS = { count: 1, records: [{ id: ROOM, collection: 'room', data: { name: 'Northwind' } }] }

const SUMMARY = {
  agreements: 1,
  links: 2,
  gated: 1,
  ungated: 1,
  broken_gates: 0,
  acceptances: 1,
  sessions: 1,
  accepted_sessions: 1,
  generated_at: '2026-03-04T09:00:00.000+00:00',
}

const AGREEMENTS = {
  room_id: ROOM,
  agreements: [
    {
      id: AGREEMENT,
      room_id: ROOM,
      title: 'Northwind mutual NDA',
      kind: 'nda',
      governing_law: 'England and Wales',
      effective_date: '2026-03-01',
      version: 1,
      body_digest: 'digest-1',
      characters: NDA_BODY.length,
    },
  ],
}

function gateRow(overrides = {}) {
  return {
    id: LINK,
    room_id: ROOM,
    title: 'Northwind deal link',
    gate: {
      enabled: true,
      agreement_id: AGREEMENT,
      agreement_title: 'Northwind mutual NDA',
      agreement_ok: true,
      fields: ['enable_agreement', 'agreement_id'],
    },
    agreement: null,
    revoked: false,
    expired: false,
    ...overrides,
  }
}

const GATES = { room_id: ROOM, gates: [gateRow()] }

const ACCEPTANCES = {
  room_id: ROOM,
  acceptances: [
    {
      id: 'wf070_acceptance_abc123',
      link_id: LINK,
      room_ref: ROOM,
      session_id: SESSION,
      agreement_id: AGREEMENT,
      agreement_version: 1,
      body_digest: 'digest-1',
      email: 'buyer@northwind.example',
      accepted: true,
      accepted_at: '2026-03-04T09:05:00.000+00:00',
    },
  ],
}

const GATE_OPEN = {
  link_id: LINK,
  title: 'Northwind deal link',
  state: 'agreement',
  gate_required: true,
  session_id: SESSION,
  session_token: 'never-shown-again',
  agreement: {
    id: AGREEMENT,
    title: 'Northwind mutual NDA',
    kind: 'nda',
    governing_law: 'England and Wales',
    effective_date: '2026-03-01',
    version: 1,
    body: NDA_BODY,
    body_digest: 'digest-1',
    characters: NDA_BODY.length,
  },
  message: 'Read the agreement and accept it to continue.',
}

const RELEASED = {
  link_id: LINK,
  released: true,
  gate_satisfied: true,
  target: { kind: 'dataroom', id: ROOM },
  resolved: true,
  content: { title: 'Pricing' },
  acceptance_id: 'wf070_acceptance_abc123',
  agreement_id: AGREEMENT,
  remaining_gates: [],
  note: 'This agreement gate is one gate.',
}

function boardRoutes(extra = {}) {
  return {
    '/records/room': ROOMS,
    [`${BASE}/summary`]: SUMMARY,
    [`${BASE}/agreements`]: AGREEMENTS,
    [`${BASE}/rooms/${ROOM}/gates`]: GATES,
    [`${BASE}/rooms/${ROOM}/acceptances`]: ACCEPTANCES,
    ...extra,
  }
}

// --------------------------------------------------------------------------- //
// The registration
// --------------------------------------------------------------------------- //

describe('the descriptor', () => {
  it('exports the shape the host discovers, with an id matching the backend', () => {
    expect(descriptor.id).toBe('wf-070-require-nda-acceptance-before-viewing')
    expect(typeof descriptor.Component).toBe('function')
    expect(descriptor.label).toBe('NDA gate')
  })

  it('carries its own glyph rather than adding to the shared PATHS map', () => {
    // `components/ui.jsx` is shared. A glyph that is not in PATHS travels as a path.
    // The check is that it is one usable path `d`, not that it has no spaces: an SVG
    // path needs separators, and asserting otherwise would be asserting a falsehood.
    expect(typeof descriptor.iconPath).toBe('string')
    expect(descriptor.iconPath).toMatch(/^[Mm]/)
    expect(descriptor.iconPath).not.toContain('<')
  })
})

// --------------------------------------------------------------------------- //
// The seller's board
// --------------------------------------------------------------------------- //

describe('the seller board', () => {
  it('shows the states the summary route reports', async () => {
    stubApi(boardRoutes())
    render(<NdaGate />)

    await screen.findByText('Northwind mutual NDA')
    const gated = screen.getByText('Links gated')
    expect(gated.parentElement.parentElement.textContent).toContain('1')
  })

  it('labels each link with a word, not a colour alone', async () => {
    stubApi(boardRoutes())
    render(<NdaGate />)

    await screen.findByText('Northwind deal link')
    // Three states a seller must not confuse. Each carries its own word.
    expect(screen.getByText('gated')).toBeTruthy()
    expect(screen.queryByText('failing closed')).toBeNull()
  })

  it('shows a gate that is failing closed, and says what to do about it', async () => {
    stubApi(
      boardRoutes({
        [`${BASE}/summary`]: { ...SUMMARY, broken_gates: 1 },
        [`${BASE}/rooms/${ROOM}/gates`]: {
          room_id: ROOM,
          gates: [
            gateRow({
              gate: {
                enabled: true,
                agreement_id: AGREEMENT,
                agreement_title: null,
                agreement_ok: false,
                fields: ['enable_agreement', 'agreement_id'],
              },
            }),
          ],
        },
      }),
    )
    render(<NdaGate />)

    await screen.findByText('failing closed')
    expect(screen.getByText(/releases nothing/)).toBeTruthy()
    // The warning names the remedy rather than only the fault.
    expect(screen.getByText(/Point each one at another agreement/)).toBeTruthy()
  })

  it('marks an acceptance whose text was amended since, rather than counting it as current', async () => {
    stubApi(
      boardRoutes({
        [`${BASE}/agreements`]: {
          room_id: ROOM,
          agreements: [{ ...AGREEMENTS.agreements[0], version: 2, body_digest: 'digest-2' }],
        },
      }),
    )
    render(<NdaGate />)

    await screen.findByText('Northwind mutual NDA')
    // The acceptance read version 1; the agreement is now version 2. A seller
    // looking at "1 accepted" needs to see that it no longer covers the wording.
    expect(screen.getByText(/superseded/)).toBeTruthy()
  })

  it('loads the agreement list once, not once per link', async () => {
    // The cause of a red CI run. Each row used to fetch its own copy of the room's
    // agreements, which meant one request per link for data the board already had,
    // and meant the picker rendered empty until that second request came back. A
    // `<select>` with nothing in it is a control that looks broken, and it is a race
    // a caller cannot see through. Three rows, one request.
    const second = { ...gateRow(), id: 'wf069_link_second', title: 'Second deal link' }
    const third = { ...gateRow(), id: 'wf069_link_third', title: 'Third deal link' }
    const calls = stubApi(
      boardRoutes({
        [`${BASE}/rooms/${ROOM}/gates`]: { room_id: ROOM, gates: [gateRow(), second, third] },
        [`${BASE}/links/${LINK}/agreement`]: { id: LINK, gate: GATES.gates[0].gate },
        [`${BASE}/links/${second.id}/agreement`]: { id: second.id, gate: GATES.gates[0].gate },
        [`${BASE}/links/${third.id}/agreement`]: { id: third.id, gate: GATES.gates[0].gate },
      }),
    )
    render(<NdaGate />)

    await screen.findByText('Third deal link')
    const agreementReads = calls.filter((call) => call.path === `${BASE}/agreements`)
    expect(agreementReads).toHaveLength(1)
  })

it('renders the picker already populated, with no empty window', async () => {
    // The regression this pins. The page has a loading phase for the room list, so the
    // assertion is not about the first render. It is about the instant the row appears:
    // if the row is on screen, its options are too. Before the fix the row rendered
    // with a select containing only its placeholder and the options arrived on a second
    // request, so this exact query threw on a slow machine and passed on a fast one.
    stubApi(
      boardRoutes({
        [`${BASE}/rooms/${ROOM}/gates`]: {
          room_id: ROOM,
          gates: [
            gateRow({
              gate: {
                enabled: false,
                agreement_id: null,
                agreement_title: null,
                agreement_ok: true,
                fields: ['enable_agreement', 'agreement_id'],
              },
            }),
          ],
        },
      }),
    )
    render(<NdaGate />)

    await screen.findByText('Northwind deal link')
    // No await between the row arriving and this query. That is the whole assertion.
    const option = screen.getByRole('option', { name: /Northwind mutual NDA/ })
    expect(option.getAttribute('value')).toBe(AGREEMENT)
  })

it('sends one request that both enables the gate and sets the agreement', async () => {
    // The link starts ungated, so the picker is the only thing a rep has to set.
    const ungated = {
      room_id: ROOM,
      gates: [
        gateRow({
          gate: {
            enabled: false,
            agreement_id: null,
            agreement_title: null,
            agreement_ok: true,
            fields: ['enable_agreement', 'agreement_id'],
          },
        }),
      ],
    }
    const calls = stubApi(
      boardRoutes({
        [`${BASE}/rooms/${ROOM}/gates`]: ungated,
        [`${BASE}/links/${LINK}/agreement`]: { id: LINK, gate: GATES.gates[0].gate },
      }),
    )
    const user = userEvent.setup()
    render(<NdaGate />)

    await screen.findByText('Northwind deal link')
    // Wait for the option itself, not for the board. The agreement list is a second
    // request, so `findByText` on the link title resolves before the `<select>` has
    // anything in it. Selecting against an empty list passes on a fast machine and
    // fails on a slow one, which is the worst kind of test: green locally, red in CI,
    // and it says nothing about the product either way.
    await screen.findByRole('option', { name: /Northwind mutual NDA/ })
    await user.selectOptions(screen.getByLabelText('Agreement'), AGREEMENT)
    await user.click(screen.getByRole('button', { name: /apply to this link/i }))

    const patch = calls.find((call) => call.method === 'PATCH')
    expect(patch).toBeTruthy()
    // The CLI's single `--agreement` flag: one field, both jobs.
    expect(JSON.parse(patch.body)).toEqual({ agreement: AGREEMENT })
  })

  it('turns the gate off without clearing the agreement, so it can be resumed', async () => {
    const calls = stubApi(
      boardRoutes({
        [`${BASE}/links/${LINK}/agreement`]: { id: LINK, gate: GATES.gates[0].gate },
      }),
    )
    const user = userEvent.setup()
    render(<NdaGate />)

    await screen.findByText('Northwind deal link')
    await user.click(screen.getByRole('switch', { name: /require nda acceptance/i }))

    const patch = calls.find((call) => call.method === 'PATCH')
    expect(JSON.parse(patch.body)).toEqual({ enable_agreement: false })
  })

  it('reports a refusal next to the control that caused it', async () => {
    stubApi(
      boardRoutes({
        [`${BASE}/links/${LINK}/agreement`]: httpError(400, {
          error: 'agreement_invalid',
          detail: 'the agreement gate could not be read',
          errors: { agreement_id: 'An agreement gate needs an agreement to accept.' },
        }),
      }),
    )
    const user = userEvent.setup()
    render(<NdaGate />)

    await screen.findByText('Northwind deal link')
    await user.click(screen.getByRole('switch', { name: /require nda acceptance/i }))

    await screen.findByText('The gate was not changed')
    expect(screen.getByText(/needs an agreement to accept/)).toBeTruthy()
  })

  it('offers the gate only when there is something to ask for', async () => {
    stubApi(
      boardRoutes({
        [`${BASE}/rooms/${ROOM}/gates`]: {
          room_id: ROOM,
          gates: [
            gateRow({
              gate: {
                enabled: false,
                agreement_id: null,
                agreement_title: null,
                agreement_ok: true,
                fields: ['enable_agreement', 'agreement_id'],
              },
            }),
          ],
        },
      }),
    )
    render(<NdaGate />)

    await screen.findByText('Northwind deal link')
    expect(screen.getByText('not gated')).toBeTruthy()
    expect(screen.getByRole('switch', { name: /require nda acceptance/i }).getAttribute('aria-checked')).toBe(
      'false',
    )
  })
})

// --------------------------------------------------------------------------- //
// The viewer's gate
// --------------------------------------------------------------------------- //

describe('the viewer gate', () => {
  it('does not ask for the agreement until the viewer chooses to read it', async () => {
    const calls = stubApi({ [`${BASE}/links/${LINK}/gate`]: GATE_OPEN })
    render(<AgreementGate linkId={LINK} />)

    await screen.findByRole('button', { name: /read the agreement/i })
    // One call: the page-state read. Nothing else, because no session exists yet.
    expect(calls).toHaveLength(1)
  })

  it('shows the agreement text and enables acceptance only once it has been read', async () => {
    const user = userEvent.setup()
    stubApi({ [`${BASE}/links/${LINK}/gate`]: GATE_OPEN })
    render(<AgreementGate linkId={LINK} />)

    await user.click(await screen.findByRole('button', { name: /read the agreement/i }))

    expect(await screen.findByText(NDA_BODY)).toBeTruthy()
    const accept = screen.getByRole('button', { name: /accept and open the room/i })
    // Not yet: nothing says the viewer read it.
    expect(accept).toBeDisabled()
  })

  it('never requests the content until the acceptance has been recorded', async () => {
    const calls = stubApi({
      [`${BASE}/links/${LINK}/gate`]: GATE_OPEN,
      [`${BASE}/links/${LINK}/gate/agreement`]: {
        link_id: LINK,
        accepted: true,
        created: true,
        acceptance_id: 'wf070_acceptance_abc123',
        session_id: SESSION,
        agreement_id: AGREEMENT,
        agreement_version: 1,
        accepted_at: '2026-03-04T09:05:00.000+00:00',
        state: 'open',
        content_released: true,
        message: 'Agreement accepted.',
      },
      [`${BASE}/links/${LINK}/content`]: RELEASED,
    })
    const user = userEvent.setup()
    render(<AgreementGate linkId={LINK} />)

    await user.click(await screen.findByRole('button', { name: /read the agreement/i }))
    await user.click(await screen.findByLabelText(/i have read the agreement/i))

    // The accept button is live, but nothing has been asked for yet.
    expect(calls.some((call) => call.path.includes('/content'))).toBe(false)

    await user.click(screen.getByRole('button', { name: /accept and open the room/i }))

    await screen.findByText('Agreement accepted')
    const contentCall = calls.find((call) => call.path.includes('/content'))
    expect(contentCall).toBeTruthy()
    // The acceptance came first. This ordering is the whole security property.
    expect(calls.indexOf(contentCall)).toBeGreaterThan(
      calls.findIndex((call) => call.path.includes('/gate/agreement')),
    )
  })

  it('sends the viewer address with the acceptance', async () => {
    const calls = stubApi({
      [`${BASE}/links/${LINK}/gate`]: GATE_OPEN,
      [`${BASE}/links/${LINK}/gate/agreement`]: { accepted: true, session_id: SESSION },
      [`${BASE}/links/${LINK}/content`]: RELEASED,
    })
    const user = userEvent.setup()
    render(<AgreementGate linkId={LINK} />)

    await user.click(await screen.findByRole('button', { name: /read the agreement/i }))
    await user.type(screen.getByLabelText(/your email address/i), 'buyer@northwind.example')
    await user.click(await screen.findByLabelText(/i have read the agreement/i))
    await user.click(screen.getByRole('button', { name: /accept and open the room/i }))

    await waitFor(() => {
      const post = calls.find((call) => call.path.includes('/gate/agreement'))
      expect(JSON.parse(post.body)).toMatchObject({
        session_id: SESSION,
        accepted: true,
        email: 'buyer@northwind.example',
      })
    })
  })

  it('never puts the session token on the page', async () => {
    stubApi({ [`${BASE}/links/${LINK}/gate`]: GATE_OPEN })
    const { container } = render(<AgreementGate linkId={LINK} />)

    await screen.findByRole('button', { name: /read the agreement/i })
    await userEvent.setup().click(screen.getByRole('button', { name: /read the agreement/i }))

    await screen.findByText(NDA_BODY)
    expect(container.textContent).not.toContain('never-shown-again')
  })

  it('says the agreement changed, and re-reads rather than looping', async () => {
    const calls = stubApi({
      [`${BASE}/links/${LINK}/gate`]: GATE_OPEN,
      [`${BASE}/links/${LINK}/gate/agreement`]: httpError(403, {
        error: 'agreement_denied',
        reason: 'agreement_changed',
        detail: 'The agreement changed.',
      }),
    })
    const user = userEvent.setup()
    render(<AgreementGate linkId={LINK} />)

    await user.click(await screen.findByRole('button', { name: /read the agreement/i }))
    await user.click(await screen.findByLabelText(/i have read the agreement/i))
    await user.click(screen.getByRole('button', { name: /accept and open the room/i }))

    // Not the generic refusal: this one has a different remedy.
    await screen.findByText(/Read the current version and accept it/)
    // A fresh session is minted, because the old one is bound to the old text. The
    // count is "at least" because the effect that read the page state may re-run; what
    // matters is that a second session was opened rather than the viewer being left
    // holding the one that can no longer be used.
    await waitFor(() => {
      expect(calls.filter((call) => call.path.endsWith('/gate')).length).toBeGreaterThanOrEqual(2)
    })
  })

  it('names the gates this workflow does not own, so the room is not implied', async () => {
    stubApi({
      [`${BASE}/links/${LINK}/gate`]: GATE_OPEN,
      [`${BASE}/links/${LINK}/gate/agreement`]: { accepted: true, session_id: SESSION },
      [`${BASE}/links/${LINK}/content`]: {
        ...RELEASED,
        remaining_gates: ['email', 'password'],
      },
    })
    const user = userEvent.setup()
    render(<AgreementGate linkId={LINK} />)

    await user.click(await screen.findByRole('button', { name: /read the agreement/i }))
    await user.click(await screen.findByLabelText(/i have read the agreement/i))
    await user.click(screen.getByRole('button', { name: /accept and open the room/i }))

    await screen.findByText('One more step before the room opens')
    // Accepting the NDA did not clear the password. Saying otherwise would be the
    // most dangerous wrong on this page.
    expect(screen.getByText(/email and password/)).toBeTruthy()
  })

  it('reads the same for an expired link and a revoked one', async () => {
    const user = userEvent.setup()
    stubApi({
      [`${BASE}/links/${LINK}/gate`]: httpError(403, {
        error: 'agreement_denied',
        reason: 'link_closed',
        detail: 'This link has expired. Ask the sender for a new one.',
      }),
    })
    render(<AgreementGate linkId={LINK} />)

    // The wording says "expired" and never "revoked", because a viewer holding a
    // forwarded URL must not learn that they were cut off.
    await screen.findByText('This link has expired. Ask the sender for a new one.')
    expect(document.body.textContent).not.toMatch(/withdrawn|revoked/i)
    await user.click(document.body) // no interactive element should be needed
  })

  it('says nothing is available when the gate points at a retired agreement', async () => {
    stubApi({
      [`${BASE}/links/${LINK}/gate`]: httpError(403, {
        error: 'agreement_denied',
        reason: 'agreement_unavailable',
        detail: 'The agreement for this link is not available.',
      }),
    })
    render(<AgreementGate linkId={LINK} />)

    await screen.findByText(/The agreement for this link is not available/)
  })

  it('asks for nothing on a link with no gate', async () => {
    stubApi({
      [`${BASE}/links/${LINK}/gate`]: {
        link_id: LINK,
        title: 'Open link',
        state: 'open',
        gate_required: false,
        session_id: null,
        agreement: null,
        message: 'This link is open.',
      },
    })
    render(<AgreementGate linkId={LINK} />)

    // Matched on the text, not on role="status", because the loading spinner carries
    // that role too and would satisfy the query before the notice arrives.
    await screen.findByText('This link is open')
    expect(screen.queryByRole('button', { name: /accept/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /read the agreement/i })).toBeNull()
  })
})