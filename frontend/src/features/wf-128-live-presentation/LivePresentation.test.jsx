/**
 * Tests for WF-128's page and its pure helpers.
 *
 * The page is driven against a stubbed `fetch` rather than a live server, so the
 * assertions are about what a seller reads and which controls are offered, not
 * about the server's answer. Every test builds its own stub and its own data, so
 * the file passes alone and under vitest's parallel run.
 */

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import {
  attendanceLabel,
  conflictText,
  eventLabel,
  formatDateTime,
  formatSeconds,
  participantRoleLabel,
  participantStatusLabel,
  questionLabel,
  recordingLabel,
  sessionStateLabel,
  sessionStateTone,
} from './api.js'
import page from './index.jsx'

const { Component } = page

const ROOM = 'room_1'

const VOCABULARY = {
  ticket: 'WF-128',
  transport: 'poll',
  transport_note:
    'No realtime transport exists in this build. A follower reads the presenter cursor by polling.',
  stack_choice: {
    sfu: { name: 'LiveKit', licence: 'Apache-2.0', reason: 'Apache-2.0 with egress.' },
    deck: { name: 'reveal.js', licence: 'MIT', reason: 'MIT and fragment-based.' },
  },
  unimplemented_automations: [
    'Auto-translate captions',
    'Auto-clip highlights',
    'Attendance written to CRM',
  ],
  sourced_gaps: ['No realtime transport exists in this product, so this build runs no SFU.'],
}

const ROOMS = { count: 1, records: [{ id: ROOM, data: { name: 'Northwind' } }] }

const SUMMARY = {
  sessions: 1,
  live: 1,
  ended: 0,
  attended: 1,
  open_questions: 1,
  transport: 'poll',
}

const AGENDA = [
  { index: 0, kind: 'page', ref: 'p1', label: 'Welcome and agenda' },
  { index: 1, kind: 'page', ref: 'p2', label: 'How the platform fits' },
  { index: 2, kind: 'page', ref: 'p3', label: 'Security and rollout' },
]

const SESSION = {
  id: 'wf128_sess_1',
  room_id: ROOM,
  title: 'Live walkthrough',
  state: 'live',
  state_label: 'Live',
  presenter: 'rep@northwind.example',
  agenda: AGENDA,
  step_count: 3,
  position: 1,
  revealed: 1,
  current_step: AGENDA[1],
  transport: 'poll',
  transport_note:
    'No realtime transport exists in this build. A follower reads the presenter cursor by polling.',
  streamed: false,
  created_at_ms: 1770000000000,
}

const SESSIONS = { count: 1, entries: [SESSION] }

const PARTICIPANTS = {
  count: 3,
  entries: [
    {
      id: 'wf128_p1',
      email: 'rep@northwind.example',
      role: 'presenter',
      status: 'invited',
      join_url: '/join/wf128/tok-presenter',
    },
    {
      id: 'wf128_p2',
      email: 'a.buyer@northwind.example',
      role: 'follower',
      status: 'joined',
      join_url: '/join/wf128/tok-a',
    },
    {
      id: 'wf128_p3',
      email: 'b.buyer@northwind.example',
      role: 'follower',
      status: 'invited',
      join_url: '/join/wf128/tok-b',
    },
  ],
}

const QUESTIONS = {
  count: 1,
  entries: [
    {
      id: 'wf128_q1',
      asker: 'a.buyer@northwind.example',
      text: 'How does SSO map onto our groups?',
      status: 'open',
      answer: null,
      asked_at_ms: 1770000000000,
    },
  ],
}

const EVENTS = {
  count: 1,
  entries: [
    {
      id: 'wf128_e1',
      kind: 'step_revealed',
      detail: 'Step 2 of 3.',
      at_ms: 1770000000000,
    },
  ],
}

const ATTENDANCE = {
  count: 3,
  entries: [
    {
      id: 'wf128_a1',
      email: 'rep@northwind.example',
      state: 'invited_only',
      attended: false,
      seconds: 0,
      written_to: 'room',
    },
    {
      id: 'wf128_a2',
      email: 'a.buyer@northwind.example',
      state: 'attended',
      attended: true,
      seconds: 1800,
      written_to: 'room',
    },
  ],
}

const RECORDINGS = {
  count: 1,
  entries: [
    {
      id: 'wf128_r1',
      status: 'published',
      replay_ref: 'artefact://wf128/wf128_sess_1',
      playback_url: 'https://replay.example.invalid/wf128/wf128_sess_1.m3u8',
      hosted: false,
      hosting_note: 'No media is hosted: the reference and the replay URL are stand-ins.',
      artefact_store: 'object storage (S3-compatible)',
    },
  ],
}

function jsonResponse(body, status = 200) {
  return { ok: status < 400, status, json: async () => body }
}

function stubFetch(routes) {
  const calls = []
  const patterns = Object.keys(routes).sort((a, b) => b.length - a.length)
  global.fetch = vi.fn(async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET' })
    for (const pattern of patterns) {
      if (url.includes(pattern)) {
        const body = routes[pattern]
        const resolved = typeof body === 'function' ? await body(options) : body
        return jsonResponse(resolved, resolved?.__status || 200)
      }
    }
    return jsonResponse({ detail: `no stub for ${url}` }, 404)
  })
  return calls
}

function pageRoutes(overrides = {}) {
  return {
    '/records/room': ROOMS,
    '/wf-128/vocabulary': VOCABULARY,
    '/wf-128/summary': SUMMARY,
    '/wf-128/sessions/wf128_sess_1/participants': PARTICIPANTS,
    '/wf-128/sessions/wf128_sess_1/questions': QUESTIONS,
    '/wf-128/sessions/wf128_sess_1/events': EVENTS,
    '/wf-128/sessions/wf128_sess_1/attendance': ATTENDANCE,
    '/wf-128/sessions/wf128_sess_1/recordings': RECORDINGS,
    '/wf-128/sessions/wf128_sess_1': SESSION,
    '/wf-128/sessions': SESSIONS,
    ...overrides,
  }
}

async function openSession() {
  render(<Component />)
  await screen.findByText('Live presentation')
  await userEvent.click(await screen.findByRole('button', { name: /Live walkthrough/ }))
  await screen.findByRole('heading', { name: 'Reveal order' })
}

// --------------------------------------------------------------------------- #
// Pure helpers
// --------------------------------------------------------------------------- #

describe('the pure helpers', () => {
  it('labels every session state, role, status, question, attendance and recording', () => {
    expect(sessionStateLabel('live')).toBe('Live')
    expect(sessionStateTone('ended')).toBe('update')
    expect(participantRoleLabel('presenter')).toBe('Presenter')
    expect(participantStatusLabel('joined')).toBe('Following')
    expect(questionLabel('open')).toBe('Open')
    expect(attendanceLabel('invited_only')).toBe('Invited, did not join')
    expect(recordingLabel('published')).toBe('Published as a replay')
    expect(eventLabel('step_revealed')).toBe('Step revealed')
  })

  it('formats a timestamp and a second count, or a dash', () => {
    expect(formatDateTime(null)).toBe('-')
    expect(formatDateTime(1770000000000)).not.toBe('-')
    expect(formatSeconds(1800)).toBe('30m 00s')
    expect(formatSeconds(null)).toBe('-')
  })

  it('turns a conflict token into a sentence', () => {
    expect(conflictText('reveal_out_of_order')).toContain('before the step before it')
    expect(conflictText('second_presenter_refused')).toContain('one presenter')
    expect(conflictText('something_new')).toBe('That change was refused.')
  })
})

// --------------------------------------------------------------------------- #
// The page
// --------------------------------------------------------------------------- #

describe('the page', () => {
  it('renders the heading and asks for a room before showing sessions', async () => {
    stubFetch(pageRoutes())
    render(<Component />)

    expect(await screen.findByText('Live presentation')).toBeTruthy()
    expect(await screen.findByLabelText('Room')).toBeTruthy()
    expect(await screen.findByRole('heading', { name: 'Sessions' })).toBeTruthy()
  })

  it('states that no realtime transport exists', async () => {
    stubFetch(pageRoutes())
    await openSession()

    expect(screen.getByText('Live transport is a stand-in')).toBeTruthy()
    expect(screen.getAllByText(/No realtime transport exists/).length).toBeGreaterThan(0)
  })

  it('draws the reveal order and marks the current step', async () => {
    stubFetch(pageRoutes())
    await openSession()

    expect(screen.getByText('Now presenting')).toBeTruthy()
    expect(screen.getByText('Already shown')).toBeTruthy()
    expect(screen.getByText('Not yet revealed')).toBeTruthy()
    expect(screen.getByText('Security and rollout')).toBeTruthy()
  })

  it('reveals the next step through the reveal route', async () => {
    const calls = stubFetch(pageRoutes())
    await openSession()

    await userEvent.click(screen.getByRole('button', { name: 'Reveal next step' }))

    expect(
      calls.some(
        (call) =>
          call.method === 'POST' &&
          call.url.includes('/wf-128/sessions/wf128_sess_1/reveal'),
      ),
    ).toBe(true)
  })

  it('simulates a follower joining through the sync route', async () => {
    const calls = stubFetch(pageRoutes())
    await openSession()

    const joiner = screen.getAllByRole('button', { name: 'Simulate this follower joining' })[0]
    await userEvent.click(joiner)

    expect(
      calls.some(
        (call) =>
          call.method === 'POST' && call.url.includes('/wf-128/sessions/wf128_sess_1/sync'),
      ),
    ).toBe(true)
  })

  it('ends the session through the end route', async () => {
    const calls = stubFetch(pageRoutes())
    await openSession()

    await userEvent.click(screen.getByRole('button', { name: 'End and publish replay' }))

    expect(
      calls.some(
        (call) => call.method === 'POST' && call.url.includes('/wf-128/sessions/wf128_sess_1/end'),
      ),
    ).toBe(true)
  })

  it('shows the replay as a stand-in reference', async () => {
    stubFetch(pageRoutes())
    await openSession()

    expect(screen.getByText('Published as a replay')).toBeTruthy()
    expect(screen.getByText(/replay.example.invalid/)).toBeTruthy()
    expect(screen.getByText('Stand-in')).toBeTruthy()
  })

  it('creates a session from the room with the form', async () => {
    const calls = stubFetch(pageRoutes())
    render(<Component />)
    await screen.findByLabelText('Room')

    await userEvent.type(await screen.findByLabelText('Presenter'), 'rep@northwind.example')
    await userEvent.click(await screen.findByRole('button', { name: 'Create session' }))

    expect(
      calls.some((call) => call.method === 'POST' && call.url.endsWith('/wf-128/sessions')),
    ).toBe(true)
  })
})
