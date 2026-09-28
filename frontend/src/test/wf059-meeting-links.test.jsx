import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import MeetingLinks from '@/features/wf-059-provision-a-per-booking-video-conferen/MeetingLinks.jsx'
import descriptor from '@/features/wf-059-provision-a-per-booking-video-conferen/index.jsx'
import { BOOKING_STATES, StateChip } from '@/features/wf-059-provision-a-per-booking-video-conferen/primitives.jsx'

/**
 * Tests for the WF-059 meeting-links page.
 *
 * Four things a page can get wrong that the backend tests cannot see, each of
 * which is a way a seller reads "everything is fine" when it is not:
 *
 * * **The state is never colour-only.** A booking list is exactly where six
 *   states that differ only by hue fail - for a colour-blind rep, and in a
 *   screenshot printed in black and white. The design floor requires a text
 *   label beside every icon, so every researched state is asserted to render
 *   words, not a hue.
 * * **The mandatory connection is visible before it blocks.** "Connecting Zoom
 *   on the Integrations tab is mandatory for this one to work" only teaches a
 *   seller something if the page shows a provider that is connected but cannot
 *   provision, on the same screen as a Location that needs it.
 * * **A refusal lands somewhere actionable.** The shared `apiRequest` hands the
 *   page a status and a message but not the server's machine-readable code, so
 *   the panel that shows the refusal is load-bearing and is pinned here.
 * * **A link a guest can click is rendered as a link**, and the researched
 *   Gong redirect is shown, because "a Gong link that redirects to Zoom" and
 *   "a Zoom link" are different links to the same meeting.
 *
 * Stubs are keyed on this feature's own paths under `/wf-059`, written the way
 * the feature's client writes them. An unstubbed path throws rather than
 * answering 404, so a test cannot quietly pass against the wrong response.
 */

const BASE = '/wf-059'
const ROOM_ID = 'room_northwind'
const ROOM_NAME = 'Northwind Traders'

const ok = (body, status = 200) => ({ ok: true, status, statusText: 'OK', json: async () => body })

const refused = (status, detail, error = 'refused') => ({
  ok: false,
  status,
  statusText: 'Error',
  json: async () => ({ error, detail }),
})

/** The seven researched Location options, as `/vocabulary` serves them. */
const KINDS = [
  {
    kind: 'google-meet',
    label: 'Google Meet',
    one_time: true,
    guest_supplied: false,
    provider: 'google-meet',
    connection_required: true,
    outcome: 'conference-provisioned',
    wire_location_type: 'integration',
    integration_value: 'google-meet',
    text_key: null,
    researched: 'This option generates a one-time Google Meet link to be displayed in the Location.',
  },
  {
    kind: 'zoom',
    label: 'Zoom',
    one_time: true,
    guest_supplied: false,
    provider: 'zoom',
    connection_required: true,
    outcome: 'conference-provisioned',
    wire_location_type: 'integration',
    integration_value: 'zoom',
    text_key: null,
    researched: 'This one generates a one-time Zoom link.',
  },
  {
    kind: 'gong',
    label: 'Gong',
    one_time: true,
    guest_supplied: false,
    provider: 'gong',
    connection_required: true,
    outcome: 'conference-provisioned',
    wire_location_type: 'integration',
    integration_value: 'gong',
    text_key: null,
    researched: 'This one generates a one-time Gong link; however, when clicked, Gong will redirect you to Zoom.',
  },
  {
    kind: 'conference-details',
    label: 'Conference Details',
    one_time: false,
    guest_supplied: false,
    provider: null,
    connection_required: false,
    outcome: 'static',
    wire_location_type: 'link',
    integration_value: null,
    text_key: 'conference_details',
    researched: 'This option is normally used to include links, like static Zoom ones, for those who don\'t want to use one-time links',
  },
  {
    kind: 'in-person',
    label: 'In-Person Meeting',
    one_time: false,
    guest_supplied: false,
    provider: null,
    connection_required: false,
    outcome: 'in-person',
    wire_location_type: 'address',
    integration_value: null,
    text_key: 'custom_text',
    researched: 'In-Person Meeting.',
  },
  {
    kind: 'custom',
    label: 'Custom',
    one_time: false,
    guest_supplied: false,
    provider: null,
    connection_required: false,
    outcome: 'in-person',
    wire_location_type: 'address',
    integration_value: null,
    text_key: 'custom_text',
    researched: 'Custom.',
  },
  {
    kind: 'attendee-defined',
    label: 'Ask the Guest (Provide My Own)',
    one_time: false,
    guest_supplied: true,
    provider: null,
    connection_required: false,
    outcome: 'awaiting-guest',
    wire_location_type: 'attendeeDefined',
    integration_value: null,
    text_key: 'attendee_prompt',
    researched: 'This field will enable your prospects to provide the Location themselves.',
  },
]

const PROVIDERS = {
  picker_count: 3,
  enum_count: 29,
  in_cal_enum_count: 27,
  escape_hatch: { location_type: 'link', why: 'static links, for those who don\'t want to use one-time links' },
  providers: [
    { provider: 'google-meet', label: 'Google Meet', on_picker: true, in_cal_integration_enum: true, enum_position: 1, gong_redirects_to_zoom: false },
    { provider: 'zoom', label: 'Zoom', on_picker: true, in_cal_integration_enum: true, enum_position: 2, gong_redirects_to_zoom: false },
    { provider: 'gong', label: 'Gong', on_picker: true, in_cal_integration_enum: false, enum_position: null, gong_redirects_to_zoom: true },
  ],
  other_cal_integrations: [{ provider: 'jitsi' }, { provider: 'huddle' }],
}

const VOCABULARY = {
  location_kinds: KINDS.map((k) => k.kind),
  cal_integration_enum_count: 29,
  location_types: ['address', 'attendeeAddress', 'attendeeDefined', 'attendeePhone', 'integration', 'link', 'phone', 'organizersDefaultApp'],
  dynamic_tags: ['CP.Meeting.RescheduleUrl', 'CP.Meeting.CancelUrl'],
  location_catalogue: { count: 7, default: 'google-meet', kinds: KINDS },
  providers: PROVIDERS,
  provisioning: { retry_attempts: 3 },
}

/** Zoom ready, Gong ready, Meet connected but revoked. */
const CONNECTIONS = {
  count: 3,
  ready: 2,
  not_ready: 1,
  connections: [
    { id: 'conn_zoom', provider: 'zoom', host: 'dana@northwind.example', state: 'connected', token_present: true, readiness: { ready: true, missing: [] } },
    { id: 'conn_gong', provider: 'gong', host: 'dana@northwind.example', state: 'connected', token_present: true, readiness: { ready: true, missing: [] } },
    {
      id: 'conn_meet',
      provider: 'google-meet',
      host: 'sam@contoso.example',
      state: 'revoked',
      token_present: false,
      readiness: { ready: false, missing: ['token', 'state:revoked'] },
    },
  ],
}

const LOCATIONS = {
  count: 3,
  one_time: 3,
  defaults: 1,
  with_gaps: 0,
  meeting_locations: [
    { id: 'loc_zoom', kind: 'zoom', name: 'Zoom (one-time link)', is_default: true, mints_conference: true, provider: 'zoom', gaps: [], wire_location: { type: 'integration', integration: 'zoom' }, kind_errors: [] },
    { id: 'loc_meet', kind: 'google-meet', name: 'Google Meet (one-time link)', is_default: false, mints_conference: true, provider: 'google-meet', gaps: [], wire_location: { type: 'integration', integration: 'google-meet' }, kind_errors: [] },
    { id: 'loc_static', kind: 'conference-details', name: 'Conference Details (static Zoom room)', is_default: false, mints_conference: false, provider: null, gaps: [], wire_location: { type: 'link', link: 'https://example.zoom.us/j/9876543210' }, kind_errors: [] },
  ],
}

const SUMMARY = {
  room_id: ROOM_ID,
  count: 4,
  one_time_linked: 2,
  missing_links: 0,
  provider_failures: 1,
  still_retryable: 1,
  awaiting_guest: 1,
  static: 0,
  in_person: 0,
  provision_failed: 0,
  locations: 3,
  locations_without_a_connection: 1,
  stranded_locations: [{ location_id: 'loc_meet', name: 'Google Meet (one-time link)', kind: 'google-meet', missing: ['token', 'state:revoked'] }],
  providers_connected: ['gong', 'zoom'],
  providers_required: ['google-meet', 'zoom', 'gong'],
  automation_note: 'Automatic on booking.',
}

const BOOKINGS = {
  room_id: ROOM_ID,
  count: 4,
  with_links: 2,
  without_links: 2,
  provider_failures: 1,
  bookings: [
    {
      id: 'bk_1',
      booking_uid: 'bk_zoom_priya',
      attendee_name: 'Priya',
      attendee_email: 'priya@northwind.example',
      location_kind: 'zoom',
      location_provider: 'zoom',
      state: 'provisioned',
      conference_id: 'conf_a1',
      meetingLocation: 'https://northwind.zoom.us/j/7612223710',
      provision_attempts: 0,
      create_request: { method: 'POST', url: null, query: {}, body: { location: { type: 'integration', integration: 'zoom' } } },
    },
    {
      id: 'bk_2',
      booking_uid: 'bk_zoom_omar',
      attendee_name: 'Omar',
      location_kind: 'gong',
      location_provider: 'gong',
      state: 'swapped',
      conference_id: 'conf_a2',
      meetingLocation: 'https://northwind.gong.io/g/3179493643',
      redirects_to: 'https://northwind.zoom.us/j/3179493643',
      previous_location: { location_provider: 'zoom', meetingLocation: 'https://northwind.zoom.us/j/1112223333' },
      location_change_reason: 'meeting-moved-tool',
      provision_attempts: 0,
      create_request: { method: 'POST', body: { location: { type: 'integration', integration: 'gong' } } },
    },
    {
      id: 'bk_3',
      booking_uid: 'bk_attendee_defined_mei',
      attendee_name: 'Mei',
      location_kind: 'attendee-defined',
      location_provider: 'attendee-defined',
      state: 'awaiting-guest',
      provision_attempts: 0,
    },
    {
      id: 'bk_4',
      booking_uid: 'bk_zoom_rafa',
      attendee_name: 'Rafa',
      location_kind: 'zoom',
      location_provider: 'zoom',
      state: 'provisioned',
      conference_id: 'conf_a4',
      meetingLocation: 'https://northwind.zoom.us/j/0987325320',
      provision_attempts: 1,
      provision_state: 'retrying',
      create_request: { method: 'POST' },
    },
  ],
}

const ROOMS = {
  count: 1,
  records: [{ id: ROOM_ID, collection: 'room', data: { name: ROOM_NAME } }],
}

function stubApi(overrides = {}) {
  const routes = {
    '/records/room': ROOMS,
    [`${BASE}/vocabulary`]: VOCABULARY,
    [`${BASE}/connections`]: CONNECTIONS,
    [`${BASE}/meeting-locations`]: LOCATIONS,
    [`${BASE}/rooms/${ROOM_ID}/summary`]: SUMMARY,
    [`${BASE}/rooms/${ROOM_ID}/bookings`]: BOOKINGS,
    ...overrides,
  }
  const keys = Object.keys(routes).sort((a, b) => b.length - a.length)
  const matches = (path, candidate) =>
    path === candidate || path.startsWith(`${candidate}/`) || path.startsWith(`${candidate}?`)

  const calls = []

  globalThis.fetch = vi.fn(async (url, init = {}) => {
    const raw = String(url)
    const path = raw.replace('/api', '').split('?')[0]
    const query = raw.includes('?') ? raw.slice(raw.indexOf('?') + 1) : ''
    const method = (init.method || 'GET').toUpperCase()
    const body = init.body ? JSON.parse(init.body) : undefined
    calls.push({ path, method, query, body })

    const key = keys.find((candidate) => matches(path, candidate))
    if (!key) throw new Error(`unstubbed request: ${path}`)

    const value = routes[key]
    if (typeof value !== 'function') return ok(value, method === 'POST' ? 201 : 200)

    const answer = value({ path, method, query, body, calls })
    return answer && typeof answer.ok === 'boolean' ? answer : ok(answer, 200)
  })

  return { calls }
}

const lastCallTo = (calls, path) => calls.filter((call) => call.path === path).at(-1)

/** Render, pick the room, and wait for the bookings to arrive. */
async function renderPage(routes) {
  const { calls } = stubApi(routes)
  const user = userEvent.setup()
  render(<MeetingLinks />)
  await screen.findByText(/Cal documents 29 integrations/)
  await user.selectOptions(screen.getByLabelText('Room'), ROOM_ID)
  await screen.findByText('bk_zoom_priya')
  return { user, calls }
}

beforeEach(() => {
  vi.restoreAllMocks()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('the descriptor', () => {
  it('is a default export the host can discover, with a unique id', () => {
    expect(descriptor.id).toBe('wf-059-provision-a-per-booking-video-conferen')
    expect(descriptor.label).toBe('Meeting links')
    expect(typeof descriptor.Component).toBe('function')
    expect(descriptor.iconPath).toBeTruthy()
  })

  it('carries an iconPath rather than editing the shared PATHS map', () => {
    // A feature that added its glyph to ui.jsx would be a shared-file collision.
    expect(descriptor.iconPath).toMatch(/^M/)
  })
})

describe('the state chip', () => {
  it('names every researched state in words, not only in colour', () => {
    for (const [state, spec] of Object.entries(BOOKING_STATES)) {
      render(<StateChip state={state} />)
      expect(screen.getByText(spec.label)).toBeInTheDocument()
    }
  })

  it('has a label for every state the API can report', () => {
    const reported = new Set(BOOKINGS.bookings.map((b) => b.state))
    for (const state of reported) {
      expect(BOOKING_STATES[state]).toBeDefined()
    }
  })

  it('falls back to a named chip for a state it has not seen', () => {
    render(<StateChip state="something-new" />)
    expect(screen.getByText('Not provisioned')).toBeInTheDocument()
  })
})

describe('the page', () => {
  it('says the vocabulary it rendered came from the server', async () => {
    await renderPage()
    expect(screen.getByText(/7 researched Location options/)).toBeInTheDocument()
    expect(screen.getByText(/3 of them mint a one-time link/)).toBeInTheDocument()
  })

  it('asks for no room until one is chosen, because the page is room-scoped', async () => {
    stubApi()
    render(<MeetingLinks />)
    await screen.findByText(/Cal documents 29 integrations/)
    expect(screen.getByText('Choose a room')).toBeInTheDocument()
  })

  it('lists the researched options with the sentence each one comes from', async () => {
    await renderPage()
    // Scoped to the picker, because "Google Meet" is also a provider name in
    // the Integrations panel above it - a bare query would match both and the
    // test would be asserting against whichever rendered first.
    const picker = within(screen.getByRole('heading', { name: 'Meeting Type Location' }).closest('div.glass'))
    expect(picker.getByText('Google Meet')).toBeInTheDocument()
    expect(
      picker.getByText(/This option generates a one-time Google Meet link/),
    ).toBeInTheDocument()
    expect(
      picker.getByText(/This option is normally used to include links, like static Zoom ones/),
    ).toBeInTheDocument()
    expect(picker.getByText('Ask the Guest (Provide My Own)')).toBeInTheDocument()
  })

  it('shows a provider that is connected but cannot provision, because that is the gate', async () => {
    await renderPage()
    // Zoom and Gong are ready; Meet is connected with a revoked credential.
    expect(screen.getAllByText('Ready to provision')).toHaveLength(2)
    expect(screen.getAllByText('Cannot provision').length).toBeGreaterThan(0)
    expect(screen.getByText(/mandatory for this one to work/)).toBeInTheDocument()
  })

  it('names a Location option that cannot provision, and what it is missing', async () => {
    await renderPage()
    expect(
      screen.getByText(/Google Meet \(one-time link\) \(google-meet\) — missing token, state:revoked/),
    ).toBeInTheDocument()
  })

  it('publishes that Gong is not one of the Cal integrations rather than hiding it', async () => {
    await renderPage()
    expect(screen.getAllByText('not in Cal\'s enum').length).toBeGreaterThan(0)
    expect(screen.getAllByText('redirects to Zoom').length).toBeGreaterThan(0)
  })

  it('shows every researched booking state in words', async () => {
    await renderPage()
    // Two of the four demo bookings are "Link ready", so the count is the
    // assertion - a getByText here would fail on ambiguity rather than on a
    // missing chip, which is a different bug and should read differently.
    expect(screen.getAllByText('Link ready')).toHaveLength(2)
    expect(screen.getByText('Moved to a new tool')).toBeInTheDocument()
    expect(screen.getByText('Waiting on the guest')).toBeInTheDocument()
    expect(screen.getByText('1 failed attempt')).toBeInTheDocument()
  })

  it('renders the join link as a link a rep can click', async () => {
    await renderPage()
    const link = screen.getByRole('link', { name: /northwind\.zoom\.us\/j\/7612223710/ })
    expect(link).toHaveAttribute('href', 'https://northwind.zoom.us/j/7612223710')
    expect(link).toHaveAttribute('rel', expect.stringContaining('noopener'))
  })

  it('says where a Gong link actually lands, because it redirects', async () => {
    await renderPage()
    expect(screen.getByText(/Gong redirects to Zoom/)).toBeInTheDocument()
    expect(screen.getByText('https://northwind.zoom.us/j/3179493643')).toBeInTheDocument()
  })

  it('says which tool a moved meeting came from', async () => {
    await renderPage()
    expect(screen.getByText(/Moved from zoom/)).toBeInTheDocument()
  })

  it('counts the numbers a rep reads first', async () => {
    await renderPage()
    expect(screen.getByText('Fresh per-booking links')).toBeInTheDocument()
    expect(screen.getByText('No link yet')).toBeInTheDocument()
    expect(screen.getByText('Provider failures')).toBeInTheDocument()
    expect(screen.getByText('1 still retryable')).toBeInTheDocument()
  })

  it('offers to record the guest\'s own location for an Ask the Guest booking', async () => {
    const { user, calls } = await renderPage()
    await user.click(screen.getAllByRole('button', { name: 'Move or retry' })[2])
    const field = await screen.findByLabelText('Where will the guest meet?')
    await user.type(field, 'https://guest.example/room/7')
    await user.click(screen.getByRole('button', { name: /Record the guest/ }))
    await waitFor(() => {
      const call = lastCallTo(calls, `${BASE}/rooms/${ROOM_ID}/bookings/bk_attendee_defined_mei/provision`)
      expect(call?.body).toMatchObject({ guest_location: 'https://guest.example/room/7' })
    })
  })

  it('quotes Cal when the swap is offered, so the notification is not a surprise', async () => {
    const { user } = await renderPage()
    await user.click(screen.getAllByRole('button', { name: 'Move or retry' })[0])
    expect(await screen.findByText(/Attendees are notified of the location change by email/)).toBeInTheDocument()
  })

  it('says the outbound request is built and not sent', async () => {
    const { user } = await renderPage()
    await user.click(screen.getAllByRole('button', { name: 'Move or retry' })[0])
    expect(await screen.findByText(/Built and stored, not sent/)).toBeInTheDocument()
  })

  it('warns that a no-op swap is refused, in the operator\'s words', async () => {
    const { user } = await renderPage()
    await user.click(screen.getAllByRole('button', { name: 'Move or retry' })[0])
    expect(
      await screen.findByText(/would announce that nothing changed/),
    ).toBeInTheDocument()
  })

  it('sends the researched swap endpoint and the researched reason', async () => {
    const { user, calls } = await renderPage()
    await user.click(screen.getAllByRole('button', { name: 'Move or retry' })[0])
    await user.selectOptions(await screen.findByLabelText('Move this meeting to'), 'gong')
    await user.click(screen.getByRole('button', { name: /Move and email the attendees/ }))
    await waitFor(() => {
      const call = lastCallTo(calls, `${BASE}/rooms/${ROOM_ID}/bookings/bk_zoom_priya/location`)
      expect(call?.method).toBe('POST')
      expect(call?.body).toMatchObject({ kind: 'gong', reason: 'meeting-moved-tool' })
    })
  })

  it('shows a server refusal in a panel a rep can read and act on', async () => {
    const { user, calls } = await renderPage({
      [`${BASE}/rooms/${ROOM_ID}/bookings/bk_zoom_priya/location`]: ({ method }) =>
        method === 'POST'
          ? refused(409, 'booking bk_zoom_priya is already at zoom', 'location_unchanged')
          : ok(BOOKINGS),
    })
    await user.click(screen.getAllByRole('button', { name: 'Move or retry' })[0])
    await user.click(screen.getByRole('button', { name: /Move and email the attendees/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/already at zoom/)
    // And the list is unchanged, because the refusal wrote nothing.
    expect(lastCallTo(calls, `${BASE}/rooms/${ROOM_ID}/bookings`)?.method).toBe('GET')
  })

  it('reads the core rooms route through the shared client, not a second one', async () => {
    const { calls } = await renderPage()
    expect(calls.some((call) => call.path === '/records/room')).toBe(true)
  })

  it('never calls a route outside its own prefix except the core rooms list', async () => {
    const { calls } = await renderPage()
    for (const call of calls) {
      if (call.path.startsWith(BASE)) continue
      expect(call.path).toBe('/records/room')
    }
  })
})
