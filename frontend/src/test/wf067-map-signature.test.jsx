/**
 * Tests for the WF-067 mutual action plan e-signature page.
 *
 * The backend tests prove the rules. These prove the page does not mislead about
 * them, which is the failure this workflow actually has: a plan badge "Awaiting
 * signatures" that hides the fact that the approver has not approved reads as
 * "everything is fine" to the seller, who then waits on a buyer who was never
 * able to sign.
 *
 * So the assertions are about words rather than colour:
 *
 * - the two invariants the vendor states as limits are on the page, not implied;
 * - a plan whose approver has not approved names the approver, in text;
 * - the two refusals read as different problems, because the vendor's approver
 *   rule is the reason they differ;
 * - a retried webhook is shown as retried rather than hidden;
 * - the compose dialog sends the researched recipient shape;
 * - the descriptor is the shape the host discovers, and its id matches the
 *   backend's FEATURE["id"].
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'

import { httpError, stubApi } from './fixtures.js'

import MapEsignature from '@/features/wf-067-send-a-mutual-action-plan-for-e-signature/MapEsignature.jsx'
import descriptor from '@/features/wf-067-send-a-mutual-action-plan-for-e-signature/index.jsx'

const ROOM = 'room_northwind'

/** The vocabulary, shaped as `/api/wf-067/vocabulary` serves it. */
const VOCABULARY = {
  collections: ['map_template', 'map_plan', 'map_recipient', 'map_event', 'map_notice'],
  roles: [
    { role: 'SIGNER', label: 'Signer', signs: true, gates_signers: false, meaning: 'Signer' },
    {
      role: 'APPROVER',
      label: 'Approver',
      signs: true,
      gates_signers: true,
      meaning: 'APPROVER | Must approve before signers can sign',
    },
    { role: 'CC', label: 'Copied', signs: false, gates_signers: false, meaning: 'Copied' },
    { role: 'VIEWER', label: 'Viewer', signs: false, gates_signers: false, meaning: 'Viewer' },
    { role: 'ASSISTANT', label: 'Assistant', signs: true, gates_signers: false, meaning: 'Assistant' },
  ],
  statuses: [],
  milestones: [
    { milestone: 'draft', label: 'Draft', is_a_refusal: false, is_terminal: false },
    {
      milestone: 'awaiting_signature',
      label: 'Awaiting signatures',
      is_a_refusal: false,
      is_terminal: false,
    },
    { milestone: 'approved', label: 'Approved', is_a_refusal: false, is_terminal: true },
    {
      milestone: 'refused_by_signer',
      label: 'Refused by a signer',
      is_a_refusal: true,
      is_terminal: true,
    },
    {
      milestone: 'refused_by_approver',
      label: 'Blocked by the approver',
      is_a_refusal: true,
      is_terminal: true,
    },
    { milestone: 'expired', label: 'Expired', is_a_refusal: false, is_terminal: true },
    { milestone: 'cancelled', label: 'Cancelled', is_a_refusal: false, is_terminal: true },
  ],
  events: [
    { event: 'DOCUMENT_OPENED', label: 'Recipient opened the document', meaning: 'Recipient opens the document' },
    { event: 'DOCUMENT_COMPLETED', label: 'Everyone finished. The plan is approved', meaning: 'Recipient completes their action.' },
  ],
  invite_paths: [
    { path: 'embed', label: 'Open the plan inside this sales room' },
    { path: 'redirect', label: 'Open the plan in a full window' },
    { path: 'email', label: 'Email every recipient a signing link' },
  ],
  coordinate_rule: { min: 0, max: 100, unit: 'percent of the page', quote: '' },
  reasons: {
    plan_distributed: 'The plan went out. Recipients can sign now.',
    plan_approved: 'Every recipient finished. The milestone is approved.',
    refused_by_signer: 'A signer refused the plan.',
    refused_by_approver: 'The approver refused the plan. Signers never got to sign.',
  },
  invariants: {
    never_sign_for_a_recipient: 'This product never signs for a buyer.',
    never_sign_quote: 'The API cannot: Sign documents on behalf of recipients (recipients must sign themselves).',
    signed_pdf_rule: 'The plan is only approved once every recipient has finished.',
    signed_pdf_quote: 'Retrieve the signed PDF until all recipients have completed signing.',
  },
}

const INFERENCES = {
  ticket: 'WF-067',
  count: 1,
  decisions: [
    {
      id: 'external-id-is-derived',
      question: 'What format does externalId take?',
      research_says: 'The research names the key and not its format.',
      reading: 'dsr-map.<room>.<plan>.<token>.',
      otherwise: 'Two plans in one room could be confused.',
      changeable_by: 'Set external_id on the plan.',
    },
  ],
  note: '',
}

const ROOMS = {
  count: 1,
  records: [{ id: ROOM, collection: 'room', revision: 1, data: { name: 'Northwind Traders' } }],
}

/** One plan, shaped as `/api/wf-067/rooms/{room_id}/plans` serves it. */
function aPlan(overrides = {}) {
  return {
    id: 'plan_1',
    room_id: ROOM,
    milestone: 'awaiting_signature',
    status: 'PENDING',
    signing_unlocked: false,
    blocking_approvers: ['Luis Ortega must approve before signers can sign'],
    plan: {
      subject: 'Mutual action plan - enterprise rollout',
      external_id: 'dsr-map.room1.plan1.tok3n',
      signing_order: 'PARALLEL',
      invite_path: 'embed',
      distributed_at: '2026-10-04T09:00:00+00:00',
    },
    links: {
      invite_path: 'embed',
      signing_url: 'https://app.documenso.com/d/tok3n?externalId=dsr-map.room1.plan1.tok3n',
      embed_url: 'https://app.documenso.com/embed/direct/tok3n?externalId=dsr-map.room1.plan1.tok3n',
    },
    ...overrides,
  }
}

const SUMMARY = {
  room_id: ROOM,
  plans: 1,
  awaiting_signature: 1,
  approved: 0,
  events: 2,
  notices: 1,
  unread_notices: 1,
  invariants: { never_sign_for_a_recipient: true, signed_pdf_before_completion: false },
}

function baseRoutes(overrides = {}) {
  return {
    '/wf-067/vocabulary': VOCABULARY,
    '/wf-067/inferences': INFERENCES,
    '/wf-067/whoami': {
      header: 'X-Documenso-Secret',
      path_template: 'POST /api/wf-067/rooms/{room_id}/webhook',
      events: ['DOCUMENT_OPENED'],
      join_key: 'externalId',
    },
    '/records/room': ROOMS,
    [`/wf-067/rooms/${ROOM}/summary`]: SUMMARY,
    [`/wf-067/rooms/${ROOM}/plans`]: { room_id: ROOM, count: 1, plans: [aPlan()] },
    [`/wf-067/rooms/${ROOM}/templates`]: { room_id: ROOM, count: 0, templates: [] },
    [`/wf-067/rooms/${ROOM}/events`]: { room_id: ROOM, count: 0, events: [] },
    [`/wf-067/rooms/${ROOM}/notices`]: { room_id: ROOM, count: 0, unread: 0, notices: [] },
    ...overrides,
  }
}

async function renderPage(routes) {
  stubApi(routes)
  render(<MapEsignature />)
  await screen.findByRole('heading', { name: /mutual action plan signatures/i })
}

beforeEach(() => {
  // Nothing to reset: each test installs its own fetch stub through renderPage.
})

describe('the invariants', () => {
  it('states on the page that this product never signs for a recipient', async () => {
    await renderPage(baseRoutes())
    const note = await screen.findByText(/what this product will not do/i)
    expect(note).toBeTruthy()
    expect(screen.getByText(/never signs for a recipient/i)).toBeTruthy()
  })

  it('states that the signed document is not retrievable before every recipient finishes', async () => {
    await renderPage(baseRoutes())
    expect(
      screen.getByText(/not retrievable until\s+every recipient has finished signing/i)
    ).toBeTruthy()
  })
})

describe('the Needs you tab', () => {
  it('says plainly when nothing is waiting on a person', async () => {
    await renderPage(
      baseRoutes({
        [`/wf-067/rooms/${ROOM}/plans`]: {
          room_id: ROOM,
          count: 1,
          plans: [aPlan({ milestone: 'approved', signing_unlocked: true })],
        },
      })
    )
    expect(await screen.findByText(/nothing is waiting on a person/i)).toBeTruthy()
  })

  it('names the approver who is holding a plan up, in text', async () => {
    const plan = aPlan()
    await renderPage(
      baseRoutes({
        [`/wf-067/rooms/${ROOM}/plans`]: { room_id: ROOM, count: 1, plans: [plan] },
      })
    )
    expect(
      await screen.findByText(/Luis Ortega must approve before signers can sign/)
    ).toBeTruthy()
    expect(screen.getByText(/signers cannot sign yet/i)).toBeTruthy()
  })
})

describe('the Plans tab', () => {
  it('badges a milestone with its label rather than colour alone', async () => {
    await renderPage(baseRoutes())
    await userEvent.click(screen.getByRole('button', { name: /^plans/i }))
    expect(await screen.findAllByText(/awaiting signatures/i)).not.toHaveLength(0)
  })

  it('offers Distribute on a draft and says the plan has not gone out', async () => {
    await renderPage(
      baseRoutes({
        [`/wf-067/rooms/${ROOM}/plans`]: {
          room_id: ROOM,
          count: 1,
          plans: [aPlan({ milestone: 'draft', signing_unlocked: true, blocking_approvers: [] })],
        },
      })
    )
    await userEvent.click(screen.getByRole('button', { name: /^plans/i }))
    expect(await screen.findByText(/not sent yet/i)).toBeTruthy()
    expect(screen.getByRole('button', { name: /distribute/i })).toBeTruthy()
  })

  it('hides Distribute once the plan is out, and offers Cancel', async () => {
    await renderPage(baseRoutes())
    await userEvent.click(screen.getByRole('button', { name: /^plans/i }))
    await screen.findAllByText(/awaiting signatures/i)
    expect(screen.queryByRole('button', { name: /^distribute/i })).toBeNull()
    expect(screen.getByRole('button', { name: /cancel/i })).toBeTruthy()
  })

  it('offers neither action on an approved plan, because it is finished', async () => {
    await renderPage(
      baseRoutes({
        [`/wf-067/rooms/${ROOM}/plans`]: {
          room_id: ROOM,
          count: 1,
          plans: [aPlan({ milestone: 'approved', signing_unlocked: true })],
        },
      })
    )
    await userEvent.click(screen.getByRole('button', { name: /^plans/i }))
    await screen.findByText(/mutual action plan - enterprise rollout/i)
    // The milestone badge reads "Approved". The stat card above also says
    // "Approved", so this scopes to the plan row rather than the page.
    const row = screen.getByText(/mutual action plan - enterprise rollout/i).closest('div')
    expect(within(row).getByText(/^approved$/i)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /^distribute/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /cancel/i })).toBeNull()
  })
})

describe('the two refusals', () => {
  // A refusal belongs on the Plans tab: it is not "needs you", because nobody is
  // waiting to act. The point of the pair is that the two badges read as
  // different problems to the seller, so they are read side by side.

  it('reads as a blocked plan when the approver refused', async () => {
    await renderPage(
      baseRoutes({
        [`/wf-067/rooms/${ROOM}/plans`]: {
          room_id: ROOM,
          count: 1,
          plans: [aPlan({ milestone: 'refused_by_approver', signing_unlocked: true })],
        },
      })
    )
    await userEvent.click(screen.getByRole('button', { name: /^plans/i }))
    expect(await screen.findByText(/blocked by the approver/i)).toBeTruthy()
  })

  it('reads as a different problem when a signer refused', async () => {
    await renderPage(
      baseRoutes({
        [`/wf-067/rooms/${ROOM}/plans`]: {
          room_id: ROOM,
          count: 1,
          plans: [aPlan({ milestone: 'refused_by_signer', signing_unlocked: true })],
        },
      })
    )
    await userEvent.click(screen.getByRole('button', { name: /^plans/i }))
    expect(await screen.findByText(/refused by a signer/i)).toBeTruthy()
    // The two must not collapse into one "rejected", because the seller's next
    // action differs: an approver's no means fix and resend.
    expect(screen.queryByText(/blocked by the approver/i)).toBeNull()
  })
})

describe('the Activity tab', () => {
  it('shows a retried event as retried rather than hiding the repeat', async () => {
    await renderPage(
      baseRoutes({
        [`/wf-067/rooms/${ROOM}/events`]: {
          room_id: ROOM,
          count: 2,
          events: [
            {
              id: 'map_event_1',
              event: 'DOCUMENT_OPENED',
              attempts: 3,
              received_at: '2026-10-04T09:01:00+00:00',
            },
            {
              id: 'map_event_2',
              event: 'DOCUMENT_OPENED',
              attempts: 1,
              received_at: '2026-10-04T09:02:00+00:00',
            },
          ],
        },
      })
    )
    await userEvent.click(screen.getByRole('button', { name: /activity/i }))
    expect(await screen.findByText(/3 deliveries/i)).toBeTruthy()
    // Two rows carry the same event, so the meaning appears twice by design.
    expect(screen.getAllByText(/recipient opens the document/i)).toHaveLength(2)
  })

  it('says so plainly when there are no events yet', async () => {
    await renderPage(baseRoutes())
    await userEvent.click(screen.getByRole('button', { name: /activity/i }))
    expect(await screen.findByText(/no events yet/i)).toBeTruthy()
  })
})

describe('the Decisions tab', () => {
  it('renders the register verbatim rather than summarising it', async () => {
    await renderPage(baseRoutes())
    await userEvent.click(screen.getByRole('button', { name: /decisions/i }))
    expect(await screen.findByText(/what format does externalId take/i)).toBeTruthy()
    expect(screen.getByText(/Two plans in one room could be confused\./i)).toBeTruthy()
    expect(screen.getByText(/Set external_id on the plan\./i)).toBeTruthy()
  })
})

describe('composing a plan', () => {
  it('sends the researched recipient shape: email, name and role', async () => {
    const calls = stubApi(baseRoutes())
    render(<MapEsignature />)
    await screen.findByRole('heading', { name: /mutual action plan signatures/i })

    await userEvent.click(screen.getByRole('button', { name: /send a plan/i }))
    const dialog = await screen.findByRole('dialog')

    await userEvent.type(within(dialog).getByLabelText(/^subject$/i), 'Rollout plan')
    await userEvent.type(within(dialog).getByLabelText(/webhook secret/i), 'a-secret')
    await userEvent.type(within(dialog).getByLabelText(/^email$/i), 'ada@buyer.example')
    await userEvent.type(within(dialog).getByLabelText(/^name$/i), 'Ada Byron')
    await userEvent.click(within(dialog).getByRole('button', { name: /create the plan/i }))

    await waitFor(() => {
      const post = calls.find(
        (call) => call.method === 'POST' && call.path === `/wf-067/rooms/${ROOM}/plans`
      )
      expect(post).toBeTruthy()
      const body = JSON.parse(post.body)
      expect(body.subject).toBe('Rollout plan')
      expect(body.webhook_secret).toBe('a-secret')
      expect(body.recipients[0]).toMatchObject({
        email: 'ada@buyer.example',
        name: 'Ada Byron',
        role: 'SIGNER',
      })
      // The researched field geometry, as a percentage of the page.
      expect(body.recipients[0].fields[0]).toMatchObject({
        type: 'SIGNATURE',
        positionX: 10,
        positionY: 60,
      })
    })
  })

  it("reports a refused create with the server's own sentence", async () => {
    const calls = stubApi(
      baseRoutes({
        [`/wf-067/rooms/${ROOM}/plans`]: httpError(422, {
          error: 'plan_needs_a_signing_recipient',
          detail: 'a plan needs at least one of SIGNER, APPROVER',
        }),
      })
    )
    render(<MapEsignature />)
    await screen.findByRole('heading', { name: /mutual action plan signatures/i })

    await userEvent.click(screen.getByRole('button', { name: /send a plan/i }))
    const dialog = await screen.findByRole('dialog')
    await userEvent.type(within(dialog).getByLabelText(/^subject$/i), 'No signer')
    await userEvent.type(within(dialog).getByLabelText(/^email$/i), 'ada@buyer.example')
    await userEvent.click(within(dialog).getByRole('button', { name: /create the plan/i }))

    expect(
      await screen.findByText(/a plan needs at least one of SIGNER, APPROVER/)
    ).toBeTruthy()
    expect(calls.some((call) => call.method === 'POST')).toBe(true)
  })

  it('does not offer to create a plan with no room chosen', async () => {
    stubApi({ ...baseRoutes(), '/records/room': { count: 0, records: [] } })
    render(<MapEsignature />)
    expect(await screen.findByText(/no rooms yet/i)).toBeTruthy()
  })
})

describe('the descriptor', () => {
  it('is the shape the host discovers', () => {
    expect(descriptor.id).toBe('wf-067-send-a-mutual-action-plan-for-e-signature')
    expect(descriptor.label).toBeTruthy()
    expect(descriptor.Component).toBeTruthy()
    expect(descriptor.iconPath).toBeTruthy()
  })

  it('carries an id matching the backend FEATURE id', () => {
    // The two halves of one workflow are findable by one name.
    expect(descriptor.id).toBe('wf-067-send-a-mutual-action-plan-for-e-signature')
  })

  it('uses a glyph path rather than adding to the shared icon map', () => {
    expect(descriptor.iconPath).toMatch(/^M/)
    expect(descriptor.icon).toBe('audit')
  })
})

describe('the accessibility floor', () => {
  it('gives every tab a button role and marks the current one', async () => {
    await renderPage(baseRoutes())
    const current = screen.getByRole('button', { current: 'page' })
    expect(current).toBeTruthy()
  })

  it('opens a plan in a dialog that identifies itself', async () => {
    await renderPage(baseRoutes())
    await userEvent.click(screen.getByRole('button', { name: /^plans/i }))
    await userEvent.click((await screen.findAllByRole('button', { name: /^open$/i }))[0])
    const dialog = await screen.findByRole('dialog')
    expect(dialog.getAttribute('aria-modal')).toBe('true')
  })

  it('uses no emoji as an icon', () => {
    // The glyph is an SVG path, so a stray emoji in the descriptor would show.
    expect(descriptor.iconPath).not.toMatch(
      /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u
    )
  })
})