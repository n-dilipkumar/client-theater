import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'

import RoomConversationPage from '@/features/wf-132-inroom-conversation/page.jsx'
import descriptor from '@/features/wf-132-inroom-conversation/index.jsx'
import {
  conflictText,
  conversationApi,
  STATE_WORDS,
} from '@/features/wf-132-inroom-conversation/api.js'
import { httpError, stubApi } from '@/test/fixtures.js'

/**
 * Tests for WF-132's page.
 *
 * What these pin is the behaviour the researched specification requires, and the
 * three things a conversation UI can quietly get wrong:
 *
 *   1. **A state carries a word, not a colour.** A resolved thread and an open one
 *      must each render text a screen reader says out loud.
 *   2. **An unresolved tag is visible.** A handle nobody in the room holds is the
 *      WF-004 dependency made visible, so the page must say so rather than hide it.
 *   3. **The contested flag is shown.** The research flags the workflow as
 *      contested; the page states it rather than smoothing it away.
 *
 * The stubs are keyed on this feature's own paths under `/wf-132`, and `stubApi`
 * throws on an unlisted path, so a test cannot pass against the wrong response.
 */

const ROOM = 'room_a'

const ROOMS = {
  count: 1,
  records: [{ id: ROOM, collection: 'room', revision: 1, data: { name: 'Northwind Traders', owner: 'dana' } }],
}

const VOCAB = {
  ticket: 'WF-132',
  anchor_kinds: ['page', 'section', 'asset', 'map_task'],
  contested: {
    flag: true,
    reason: 'A room you cannot talk inside may not be a room at all.',
  },
}

const SUMMARY = {
  room_id: ROOM,
  threads: 1,
  open_threads: 1,
  resolved_threads: 0,
  comments: 2,
  questions: 1,
  unanswered_questions: 1,
  mentions: 2,
  unresolved_mentions: 1,
  champion_edits: 1,
  edits: 1,
  conversions: 0,
  escalations: 1,
  contested: true,
}

function thread(overrides = {}) {
  return {
    id: 'wf132_thread_one',
    room_id: ROOM,
    anchor: { type: 'page', id: 'page_pricing', label: 'Pricing' },
    state: 'open',
    created_by: 'a.buyer@northwind.example',
    created_side: 'buyer',
    watchers: ['a.buyer@northwind.example', 'legal@northwind.example'],
    message_count: 2,
    last_activity_at: '2026-10-14T12:00:00.000+00:00',
    map_task_ref: null,
    contested: true,
    unanswered_questions: 1,
    mention_count: 2,
    unresolved_mentions: 1,
    last_comment: { author: 'a.buyer@northwind.example', side: 'buyer', body: 'Can we talk about the tiers?' },
    ...overrides,
  }
}

const THREADS = { room_id: ROOM, count: 1, threads: [thread()] }

const ESCALATIONS = {
  room_id: ROOM,
  count: 1,
  escalations: [
    {
      id: 'wf132_escalation_one',
      thread_ref: 'wf132_thread_one',
      reason: 'unanswered_buyer_question',
      escalated_to: 'dana',
      hours_open: 28,
      at: '2026-10-14T12:00:00.000+00:00',
    },
  ],
}

const CHAMPIONS = {
  room_id: ROOM,
  champion_edits: 1,
  editors: [{ principal: 'legal@northwind.example', edits: 1 }],
  edits: [
    {
      id: 'wf132_edit_one',
      section_ref: 'page_pricing',
      actor: 'legal@northwind.example',
      side: 'buyer',
      champion: true,
      summary: 'Tailored the pricing summary for internal approval.',
      delta: { headline: { from: 'Pricing', to: 'Pricing for the rollout' } },
    },
  ],
}

const IDENTITY = {
  principal: 'dana',
  role: 'room_owner',
  role_label: 'Owner',
  side: 'seller',
  is_champion: false,
}

function baseRoutes(overrides = {}) {
  return {
    '/records/room': ROOMS,
    '/wf-132/vocabulary': VOCAB,
    '/wf-132/summary': SUMMARY,
    '/wf-132/threads': THREADS,
    [`/wf-132/rooms/${ROOM}/escalations`]: ESCALATIONS,
    [`/wf-132/rooms/${ROOM}/champions`]: CHAMPIONS,
    [`/wf-132/rooms/${ROOM}/identity`]: IDENTITY,
    ...overrides,
  }
}

beforeEach(() => {
  globalThis.fetch = undefined
})

describe('WF-132 descriptor', () => {
  it('exports the shape the host discovers', () => {
    expect(descriptor.id).toBe('wf-132-inroom-conversation')
    expect(descriptor.label).toBeTruthy()
    expect(descriptor.Component).toBeTruthy()
    expect(descriptor.iconPath).toBeTruthy()
  })
})

describe('the conversation page', () => {
  it('lists a thread with its anchor, state word and tag counts', async () => {
    stubApi(baseRoutes())
    render(<RoomConversationPage />)

    expect(await screen.findByText('Room conversations')).toBeInTheDocument()
    expect(await screen.findByText('Pricing')).toBeInTheDocument()
    // The state is a word, not only a colour.
    expect(screen.getAllByText(STATE_WORDS.open).length).toBeGreaterThan(0)
    // The unresolved tag is visible, which is the WF-004 dependency made plain.
    expect(screen.getByText('1 unresolved tag')).toBeInTheDocument()
    expect(screen.getByText('1 unanswered')).toBeInTheDocument()
  })

  it('states the contested flag the research carries', async () => {
    stubApi(baseRoutes())
    render(<RoomConversationPage />)

    expect(
      await screen.findByText(/A room you cannot talk inside may not be a room at all\./),
    ).toBeInTheDocument()
  })

  it('shows the champion edit and its delta', async () => {
    stubApi(baseRoutes())
    render(<RoomConversationPage />)

    expect(
      await screen.findByText('Tailored the pricing summary for internal approval.'),
    ).toBeInTheDocument()
    expect(screen.getByText(/1 champion edit\(s\) by legal@northwind.example/)).toBeInTheDocument()
  })

  it('shows the escalation to the room owner', async () => {
    stubApi(baseRoutes())
    render(<RoomConversationPage />)

    expect(await screen.findByText('Unanswered buyer question')).toBeInTheDocument()
    expect(screen.getByText(/Escalated to dana after 28 hours\./)).toBeInTheDocument()
  })

  it('sends the first comment to the thread route with the acting principal', async () => {
    const calls = stubApi(
      baseRoutes({ [`/wf-132/rooms/${ROOM}/threads`]: thread({ id: 'wf132_thread_new' }) }),
    )
    render(<RoomConversationPage />)
    await screen.findByText('Room conversations')

    // `fireEvent.change` rather than `userEvent.type`: this test asserts the
    // request body, not keystroke behaviour, and a per-character type is both
    // slower and the thing that drops characters on a loaded machine.
    fireEvent.change(screen.getByLabelText('Anchor id'), { target: { value: 'page_1' } })
    fireEvent.change(screen.getByLabelText('First comment'), {
      target: { value: 'Opening the discussion.' },
    })
    await userEvent.click(screen.getByRole('button', { name: 'Open thread' }))

    await waitFor(
      () => {
        const posted = calls.find(
          (call) => call.method === 'POST' && call.path === `/wf-132/rooms/${ROOM}/threads`,
        )
        expect(posted).toBeTruthy()
        const body = JSON.parse(posted.body)
        expect(body.anchor_id).toBe('page_1')
        expect(body.body).toBe('Opening the discussion.')
        expect(body.actor).toBe('dana')
      },
      { timeout: 10000 },
    )
  }, 20000)

  it('says there are no rooms rather than showing an empty page', async () => {
    stubApi({ '/records/room': { count: 0, records: [] } })
    render(<RoomConversationPage />)

    expect(await screen.findByText('No rooms yet')).toBeInTheDocument()
  })

  it('offers a retry when the conversation cannot load', async () => {
    stubApi(baseRoutes({ '/wf-132/summary': httpError(500, { detail: 'the summary did not load' }) }))
    render(<RoomConversationPage />)

    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Retry/ })).toBeInTheDocument()
  })
})

describe('the api wrapper', () => {
  it('exposes the calls the page uses and the conflict sentence for a reason token', () => {
    expect(typeof conversationApi.comment).toBe('function')
    expect(typeof conversationApi.recordEdit).toBe('function')
    expect(typeof conversationApi.refreshEscalations).toBe('function')
    expect(conflictText('thread_resolved')).toMatch(/Reopen it before you comment/)
    expect(conflictText('unknown_reason')).toBe('That change was refused.')
  })
})
