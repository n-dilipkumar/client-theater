/**
 * Tests for WF-126's page and its pure helpers.
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
  conflictText,
  eventLabel,
  formatDateTime,
  kindLabel,
  percentText,
  placementLabel,
  sourceLabel,
  stateLabel,
  stateTone,
} from './api.js'
import page from './index.jsx'

const { Component } = page

const ROOM = 'room_1'

const VOCABULARY = {
  ticket: 'WF-126',
  kinds: ['personal_intro', 'walkthrough'],
  default_kind: 'personal_intro',
  capture_sources: ['camera', 'screen', 'camera_and_screen'],
  default_source: 'camera_and_screen',
  events: ['start', 'progress', 'complete', 'replay'],
  low_completion_percent: 50,
  complete_percent: 95,
  implemented_automations: [
    'Auto-transcode and publish on save',
    'Notify the buyer when a new video lands',
    'Alert the rep on low completion',
  ],
  unimplemented_automations: ['Auto-embed into new rooms from a template'],
  sourced_gaps: ['The research marks native video hosting as unsourced, so this build hosts no media.'],
  user_flow: [
    { step: '1', text: 'A rep records a short personal intro from inside the room.' },
    { step: '2', text: 'The video is placed on the room hero and personalised per buyer.' },
  ],
}

const ROOMS = { count: 1, records: [{ id: ROOM, data: { name: 'Northwind' } }] }

const SUMMARY = {
  recordings: 1,
  live_placements: 1,
  viewers: 2,
  completed: 1,
  low_completion_alerts: 1,
  average_watched_percent: 55,
}

const RECIPIENTS = {
  count: 2,
  entries: [
    { email: 'a.buyer@northwind.example', role: 'viewer', source: 'grant' },
    { email: 'b.buyer@northwind.example', role: 'viewer', source: 'grant' },
  ],
}

function recording(overrides = {}) {
  return {
    id: 'wf126_rec_1',
    room_id: ROOM,
    title: 'A short hello',
    kind: 'personal_intro',
    kind_label: 'Personal intro',
    source: 'camera',
    source_label: 'Camera and microphone',
    duration_seconds: 95,
    note: 'Before our call',
    status: 'placed',
    media_ref: 'media://wf126/wf126_rec_1',
    stream_ref: 'https://media.example.invalid/wf126/wf126_rec_1.m3u8',
    hosted: false,
    transcode: { state: 'ready', stand_in: true },
    ...overrides,
  }
}

const RECORDINGS = { count: 1, entries: [recording()] }
const DETAIL = recording()

const HERO = {
  room_id: ROOM,
  surface: 'hero',
  message: 'Before our call',
  hosted: false,
  hosting_note: 'The player points at a stand-in URL.',
  recording: recording(),
  placement: {
    id: 'wf126_place_1',
    recording_id: 'wf126_rec_1',
    surface: 'hero',
    status: 'live',
    personalised_for: ['a.buyer@northwind.example', 'b.buyer@northwind.example'],
    note: 'Before our call',
    player: { url: 'https://media.example.invalid/wf126/wf126_rec_1.m3u8' },
  },
}

const HERO_FOR_BUYER = {
  ...HERO,
  viewer: 'a.buyer@northwind.example',
  personalised: true,
  engagement: { max_percent: 15, completed: false, state: 'started' },
}

const PLACEMENTS = {
  count: 1,
  entries: [
    {
      id: 'wf126_place_1',
      recording_id: 'wf126_rec_1',
      status: 'live',
      personalised_for: ['a.buyer@northwind.example'],
      placed_at_ms: 1770000000000,
    },
  ],
}

const EVENTS = {
  count: 1,
  entries: [
    {
      id: 'wf126_ev_1',
      recording_id: 'wf126_rec_1',
      viewer: 'a.buyer@northwind.example',
      event: 'progress',
      event_label: 'Progress',
      watched_percent: 15,
      at_ms: 1770000000000,
    },
  ],
}

const ENGAGEMENT = {
  count: 1,
  entries: [
    {
      id: 'wf126_eng_1',
      recording_id: 'wf126_rec_1',
      viewer: 'a.buyer@northwind.example',
      state: 'started',
      state_label: 'Started, not finished',
      events: 2,
      max_percent: 15,
      completed: false,
      replays: 0,
    },
  ],
}

const ALERTS = {
  count: 1,
  entries: [
    {
      id: 'wf126_alert_1',
      recording_id: 'wf126_rec_1',
      viewer: 'a.buyer@northwind.example',
      status: 'open',
      message: 'a.buyer@northwind.example watched 15 percent of this video.',
      max_percent: 15,
    },
  ],
}

const NOTIFICATIONS = {
  count: 1,
  entries: [
    {
      id: 'wf126_note_1',
      recipient: 'a.buyer@northwind.example',
      message: 'A short hello is ready for you in the room.',
      at_ms: 1770000000000,
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
    '/wf-126/vocabulary': VOCABULARY,
    '/records/room': ROOMS,
    '/wf-126/summary': SUMMARY,
    '/wf-126/recipients': RECIPIENTS,
    '/wf-126/rooms/room_1/hero': HERO,
    '/wf-126/rooms/room_1/placements': PLACEMENTS,
    '/wf-126/rooms/room_1/events': EVENTS,
    '/wf-126/rooms/room_1/engagement': ENGAGEMENT,
    '/wf-126/rooms/room_1/alerts': ALERTS,
    '/wf-126/rooms/room_1/notifications': NOTIFICATIONS,
    '/wf-126/recordings/wf126_rec_1': DETAIL,
    '/wf-126/recordings': RECORDINGS,
    ...overrides,
  }
}

async function openRoom() {
  render(<Component />)
  await screen.findByText('Personalised video')
  await userEvent.selectOptions(await screen.findByLabelText('Room'), ROOM)
}

// --------------------------------------------------------------------------- #
// Pure helpers
// --------------------------------------------------------------------------- #

describe('the pure helpers', () => {
  it('labels every kind, source, event, state and placement', () => {
    expect(kindLabel('walkthrough')).toBe('Walkthrough')
    expect(sourceLabel('screen')).toBe('Screen share')
    expect(eventLabel('replay')).toBe('Replayed')
    expect(stateLabel('started')).toBe('Started, not finished')
    expect(stateTone('completed')).toBe('insert')
    expect(placementLabel('superseded')).toBe('Replaced by a newer video')
  })

  it('formats a percentage and a timestamp or a dash', () => {
    expect(percentText(15.4)).toBe('15%')
    expect(percentText(null)).toBe('-')
    expect(formatDateTime(null)).toBe('-')
    expect(formatDateTime(1770000000000)).not.toBe('-')
  })

  it('turns a conflict token into a sentence', () => {
    expect(conflictText('recording_belongs_to_another_room')).toContain('another room')
    expect(conflictText('something_new')).toBe('That change was refused.')
  })
})

// --------------------------------------------------------------------------- #
// The page
// --------------------------------------------------------------------------- #

describe('the page', () => {
  it('renders the heading and asks for a room before showing anything', async () => {
    stubFetch(pageRoutes())
    render(<Component />)

    expect(await screen.findByText('Personalised video')).toBeTruthy()
    expect(await screen.findByLabelText('Room')).toBeTruthy()
    expect(screen.getByText('Pick a room above to see its video, its buyers and their playback.')).toBeTruthy()
  })

  it('shows the counts, the hero and the recordings once a room is chosen', async () => {
    stubFetch(pageRoutes())
    await openRoom()

    expect(await screen.findByRole('heading', { name: 'Recordings' })).toBeTruthy()
    expect(screen.getByText('Low completion')).toBeTruthy()
    expect(screen.getByText('On the hero now')).toBeTruthy()
    expect(await screen.findByRole('button', { name: /A short hello/ })).toBeTruthy()
  })

  it('places a selected recording on the hero', async () => {
    const calls = stubFetch(pageRoutes())
    await openRoom()

    await userEvent.click(await screen.findByRole('button', { name: /A short hello/ }))
    await userEvent.click(await screen.findByRole('button', { name: 'Place on the hero' }))

    expect(
      calls.some(
        (call) =>
          call.method === 'POST' &&
          call.url.includes('/wf-126/rooms/room_1/recordings/wf126_rec_1/place'),
      ),
    ).toBe(true)
  })

  it('previews the hero as a chosen buyer', async () => {
    stubFetch(pageRoutes({ '/wf-126/rooms/room_1/hero': HERO_FOR_BUYER }))
    await openRoom()

    await userEvent.selectOptions(
      await screen.findByLabelText('Read as'),
      'a.buyer@northwind.example',
    )

    expect(await screen.findByText(/watched 15%/)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Record event' })).toBeTruthy()
  })

  it('records a playback event for the buyer', async () => {
    const calls = stubFetch(pageRoutes({ '/wf-126/rooms/room_1/hero': HERO_FOR_BUYER }))
    await openRoom()

    await userEvent.selectOptions(
      await screen.findByLabelText('Read as'),
      'a.buyer@northwind.example',
    )
    await userEvent.click(await screen.findByRole('button', { name: 'Record event' }))

    expect(
      calls.some(
        (call) =>
          call.method === 'POST' &&
          call.url.includes('/wf-126/recordings/wf126_rec_1/playback'),
      ),
    ).toBe(true)
  })

  it('shows the low-completion alert with its reason', async () => {
    stubFetch(pageRoutes())
    await openRoom()

    expect(await screen.findByText(/watched 15 percent/)).toBeTruthy()
    expect(screen.getByText('Low-completion alerts')).toBeTruthy()
  })

  it('names the automations it owns and the one it does not', async () => {
    stubFetch(pageRoutes())
    render(<Component />)
    await screen.findByText('Personalised video')

    expect(screen.getByText('Automations this build owns')).toBeTruthy()
    expect(screen.getByText('Auto-embed into new rooms from a template')).toBeTruthy()
  })
})
