import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'

import NextStepsBoard from '@/features/wf-136-inroom-booking/NextStepsBoard.jsx'
import descriptor from '@/features/wf-136-inroom-booking/index.jsx'
import { bookingApi, refusalText, STATE_TONES, STATE_WORDS } from '@/features/wf-136-inroom-booking/api.js'
import { httpError, stubApi } from './fixtures.js'

/**
 * Tests for WF-136's page.
 *
 * What these pin is the behaviour the researched specification requires, and the
 * three things a room-integration page can quietly get wrong:
 *
 *   1. **A state carries a word.** Every proposal badge renders its word as text,
 *      so nothing is carried by colour alone.
 *   2. **The buyer really books through the API.** The accept call is asserted on
 *      the request the page made, not on what it drew, because a page that only
 *      looked booked would pass a screenshot review and fail in the network tab.
 *   3. **The panel merges two inputs.** The open MAP tasks come from WF-124, and
 *      the panel is a server read; the page never counts proposals itself.
 *
 * The stubs are keyed on this feature's own paths under `/wf-136`, and `stubApi`
 * throws on an unlisted path so a test cannot pass against the wrong response.
 */

const ROOM = 'room_a'
const START = '2026-05-12T09:00:00Z'

const ROOMS = {
  count: 1,
  records: [{ id: ROOM, collection: 'room', revision: 1, data: { name: 'Northwind' } }],
}

const PROPOSAL = {
  id: 'inroom_booking_proposal_one',
  room_id: ROOM,
  summary: 'Book the technical deep dive',
  state: 'proposed',
  effective_state: 'proposed',
  answerable: true,
  proposed_at: '2026-05-04T09:00:00Z',
  expires_at: '2026-05-11T09:00:00Z',
  task_id: 'wf124_task_one',
}

const SUMMARY = {
  room_id: ROOM,
  proposals: 1,
  by_state: { proposed: 1 },
  answerable: 1,
  booked: 0,
  meetings: 0,
  meetings_linked: 0,
  meetings_unlinked: 0,
  idle_minutes: 15,
  generated_at: '2026-05-04T09:00:00Z',
}

const NEXT_STEPS = {
  room_id: ROOM,
  proposals: [PROPOSAL],
  map_tasks: [
    {
      id: 'wf124_task_one',
      plan_id: 'wf124_plan_one',
      title: 'Run the technical deep dive',
      owner: 'Priya',
      owner_side: 'seller',
      status: 'todo',
      due_date: null,
      visibility: 'external',
    },
  ],
  count: 2,
  generated_at: '2026-05-04T09:00:00Z',
}

const SLOTS = {
  room_id: ROOM,
  proposal_id: PROPOSAL.id,
  available_count: 1,
  slot_count: 1,
  slots: [{ start: START, end: '2026-05-12T09:30:00Z', available: true, reason: null }],
}

function baseRoutes(overrides = {}) {
  return {
    '/records/room': ROOMS,
    [`/wf-136/rooms/${ROOM}/proposals`]: { room_id: ROOM, count: 1, proposals: [PROPOSAL] },
    [`/wf-136/rooms/${ROOM}/meetings`]: { room_id: ROOM, count: 0, meetings: [] },
    [`/wf-136/rooms/${ROOM}/next-steps`]: NEXT_STEPS,
    '/wf-136/summary': SUMMARY,
    [`/wf-136/proposals/${PROPOSAL.id}/slots`]: SLOTS,
    ...overrides,
  }
}

beforeEach(() => {
  globalThis.fetch = undefined
})

describe('WF-136 descriptor', () => {
  it('exports the shape the host discovers', () => {
    expect(descriptor.id).toBe('wf-136-inroom-booking')
    expect(descriptor.label).toBeTruthy()
    expect(descriptor.Component).toBeTruthy()
    // The glyph is not in the shared PATHS map, so it travels as a path and
    // `components/ui.jsx` stays unedited.
    expect(descriptor.iconPath).toBeTruthy()
  })
})

describe('the next-steps board', () => {
  it('shows each proposal with a word for its state, never colour alone', async () => {
    stubApi(baseRoutes())
    render(<NextStepsBoard />)

    expect(await screen.findByText('Book the technical deep dive')).toBeInTheDocument()
    expect(screen.getAllByText(STATE_WORDS.proposed).length).toBeGreaterThan(0)
    // No status is conveyed only by a tone.
    expect(STATE_TONES.proposed).toBe('info')
    expect(STATE_WORDS.expired).toBe('Lapsed')
  })

  it('lists the open plan tasks it did not compute itself', async () => {
    stubApi(baseRoutes())
    render(<NextStepsBoard />)

    // The task appears in the panel's list and, as an option, in the propose form.
    expect(await screen.findAllByText('Run the technical deep dive')).not.toHaveLength(0)
    expect(screen.getByText('Open plan tasks', { selector: 'p' })).toBeInTheDocument()
  })

  it('books the slot the buyer picks, through the accept route', async () => {
    const calls = stubApi(
      baseRoutes({
        [`/wf-136/proposals/${PROPOSAL.id}/accept`]: {
          proposal: { ...PROPOSAL, state: 'booked', effective_state: 'booked' },
          meeting: { id: 'm1', state: 'linked' },
        },
      }),
    )
    render(<NextStepsBoard />)
    await screen.findByText('Book the technical deep dive')

    await userEvent.click(screen.getByRole('button', { name: 'Show times' }))
    await userEvent.click(await screen.findByRole('button', { name: `Book ${START}` }))
    await userEvent.type(screen.getByLabelText('Who is being booked'), 'Ines Duarte')
    await userEvent.type(screen.getByLabelText('Their email'), 'ines@northwind.example')
    await userEvent.click(screen.getByRole('button', { name: 'Accept and book this time' }))

    await waitFor(() => {
      const posted = calls.find(
        (call) => call.method === 'POST' && call.path.endsWith('/accept'),
      )
      expect(posted).toBeTruthy()
      const body = JSON.parse(posted.body)
      expect(body.start).toBe(START)
      expect(body.attendee).toEqual({ name: 'Ines Duarte', email: 'ines@northwind.example' })
    })
  })

  it('declines a proposal through the decline route', async () => {
    const calls = stubApi(
      baseRoutes({
        [`/wf-136/proposals/${PROPOSAL.id}/decline`]: { ...PROPOSAL, state: 'declined' },
      }),
    )
    render(<NextStepsBoard />)
    await screen.findByText('Book the technical deep dive')

    await userEvent.click(screen.getByRole('button', { name: 'Decline' }))

    await waitFor(() => {
      expect(
        calls.some((call) => call.method === 'POST' && call.path.endsWith('/decline')),
      ).toBe(true)
    })
  })

  it('turns a refusal token into a sentence the buyer can act on', async () => {
    stubApi(
      baseRoutes({
        [`/wf-136/proposals/${PROPOSAL.id}/accept`]: httpError(409, {
          error: 'slot_not_offered',
          reason: 'slot_not_offered',
          detail: 'that time has gone',
        }),
      }),
    )
    render(<NextStepsBoard />)
    await screen.findByText('Book the technical deep dive')

    await userEvent.click(screen.getByRole('button', { name: 'Show times' }))
    await userEvent.click(await screen.findByRole('button', { name: `Book ${START}` }))
    await userEvent.type(screen.getByLabelText('Who is being booked'), 'Ines')
    await userEvent.type(screen.getByLabelText('Their email'), 'ines@example.com')
    await userEvent.click(screen.getByRole('button', { name: 'Accept and book this time' }))

    expect(await screen.findByText(refusalText('slot_not_offered'))).toBeInTheDocument()
  })

  it('offers a retry when the board cannot load', async () => {
    stubApi(baseRoutes({ '/wf-136/summary': httpError(500, { detail: 'the board did not load' }) }))
    render(<NextStepsBoard />)

    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Retry/ })).toBeInTheDocument()
  })

  it('tells the seller to seed the demo when there are no rooms at all', async () => {
    stubApi({ '/records/room': { count: 0, records: [] } })
    render(<NextStepsBoard />)

    expect(await screen.findByText('No rooms yet')).toBeInTheDocument()
  })
})

describe('the meetings panel', () => {
  const MEETING = {
    id: 'inroom_booking_meeting_one',
    room_id: ROOM,
    summary: 'Book the security review',
    state: 'linked',
    conference_url: 'https://meet.example/x',
    conference_note: null,
    booking_uid: 'bkg_1',
    start: START,
    recording_url: null,
  }

  it('shows the join link and attaches a recording by URL', async () => {
    const calls = stubApi(
      baseRoutes({
        [`/wf-136/rooms/${ROOM}/meetings`]: { room_id: ROOM, count: 1, meetings: [MEETING] },
        [`/wf-136/meetings/${MEETING.id}/recording`]: {
          ...MEETING,
          recording_url: 'https://record.example/x',
        },
      }),
    )
    render(<NextStepsBoard />)
    await screen.findByText('Book the security review')

    expect(screen.getByRole('link', { name: 'Join link' })).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('Recording link'), 'https://record.example/x')
    await userEvent.click(screen.getByRole('button', { name: 'Attach' }))

    await waitFor(() => {
      const posted = calls.find((call) => call.method === 'POST' && call.path.endsWith('/recording'))
      expect(posted).toBeTruthy()
      expect(JSON.parse(posted.body).url).toBe('https://record.example/x')
    })
  })

  it('says when a booking has no join link rather than hiding it', async () => {
    stubApi(
      baseRoutes({
        [`/wf-136/rooms/${ROOM}/meetings`]: {
          room_id: ROOM,
          count: 1,
          meetings: [{ ...MEETING, state: 'unlinked', conference_url: null, conference_note: 'no provider' }],
        },
      }),
    )
    render(<NextStepsBoard />)
    await screen.findByText('Book the security review')

    expect(screen.getAllByText('No join link yet').length).toBeGreaterThan(0)
    expect(screen.queryByRole('link', { name: 'Join link' })).not.toBeInTheDocument()
  })

  it('exposes the API wrapper the page books through', () => {
    expect(typeof bookingApi.accept).toBe('function')
    expect(typeof bookingApi.decline).toBe('function')
    expect(typeof bookingApi.attachRecording).toBe('function')
  })
})
