import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import descriptor, {
  ConsentRecording as Page,
} from '@/features/wf-060-auto-join-and-record-with-consent/index.jsx'
import {
  ConsentChip,
  RecordedAfterDeclineNote,
  RecordingChip,
} from '@/features/wf-060-auto-join-and-record-with-consent/primitives.jsx'
import { httpError, stubApi } from './fixtures.js'

/**
 * Tests for WF-060's page.
 *
 * The backend rules are pinned in `backend/tests/test_wf060.py`. What is pinned here is
 * the four things a UI can quietly get wrong on a consent-gated recording workflow, and
 * none of the four is visible in a screenshot:
 *
 *   1. **No status is carried by colour alone.** Every state renders a mark, the server's
 *      own state name, and a written sentence. Three redundant channels, so a reader who
 *      cannot see one of them still has the other two.
 *   2. **A booking recorded after a refusal says so in words.** This is the derived
 *      open point - the research does not say what the gate does when enforcement is off -
 *      and the page has to state the consequence rather than leave a reviewer to work it
 *      out from two chips side by side.
 *   3. **The refused step is reported with the step that is available instead.** The 409
 *      carries `state`, `step` and `allowed`, and a page that only shows the sentence
 *      makes the operator guess what to press next.
 *   4. **A booking that contradicts itself is called out, not listed.** A record whose
 *      state and whose recording outcome disagree has a compliance record nobody can
 *      trust, and it belongs at the top of the board.
 *
 * Every stub is keyed on this feature's own paths under `/wf-060`.
 */

const BASE = '/wf-060'

const ROOMS = {
  count: 1,
  records: [{ id: 'room_a', collection: 'room', data: { name: 'Northwind' } }],
}

const SUMMARY = {
  room_id: null,
  profiles: 2,
  profiles_with_consent_page: 2,
  profiles_enforcing: 1,
  users: 2,
  bookings: 3,
  by_state: {
    scheduled: 0,
    awaiting_consent: 1,
    consented: 0,
    declined: 0,
    joined_without_consent: 0,
    recording: 0,
    recorded: 2,
    cancelled: 0,
  },
  by_consent_state: {
    not_required: 0,
    pending: 1,
    granted: 1,
    declined: 1,
    joined_without_consent: 0,
  },
  recordings_blocked: 1,
  recordings_cancelled: 0,
  recordings_complete: 2,
  inconsistent: [],
  resolution_key: 'organizer_email',
  providers: ['google_meet', 'microsoft_teams', 'webex', 'zoom'],
  precall_window_minutes: [10, 20],
  prompt_mode: 'first_guest_with_audio',
}

function booking(bookingId, overrides = {}) {
  return {
    id: `rec_${bookingId}`,
    collection: 'wf060_consent_recording',
    room_id: 'room_a',
    data: {
      booking_id: bookingId,
      room_id: 'room_a',
      organizer_email: 'dana@northwind.example',
      profile_id: 'prof_1',
      profile_resolution: 'assigned',
      profile_resolution_key: 'organizer_email',
      title: `Meeting ${bookingId}`,
      provider: 'zoom',
      external_invitees: ['analyst@contoso.example'],
      internal_invitees: ['buyer@northwind.example'],
      bot_invited: ['coordinator@gong.io'],
      audio_prompt_suppressed: true,
      state: 'awaiting_consent',
      consent_state: 'pending',
      recording_state: 'blocked',
      enforced: true,
      terminal: false,
      consent_link: { meeting_id: `mtg-${bookingId}`, link_state: 'active' },
      outbound_request: {
        method: 'POST',
        endpoint: '/v2/meetings',
        scope: 'api:meetings:user:create',
        body: { organizerEmail: 'dana@northwind.example', externalId: bookingId },
      },
      ...overrides,
    },
  }
}

const BOOKINGS = {
  count: 2,
  bookings: [
    booking('b-waiting'),
    booking('b-done', {
      state: 'recorded',
      consent_state: 'granted',
      recording_state: 'complete',
      terminal: true,
    }),
  ],
}

const STATES = Object.keys(SUMMARY.by_state)

const VOCABULARY = {
  providers: {
    zoom: 'Zoom',
    google_meet: 'Google Meet',
    microsoft_teams: 'Microsoft Teams',
    webex: 'Webex',
  },
  link_kinds: ['dynamic_link', 'static_link', 'host_decides'],
  states: STATES,
  consent_states: ['not_required', 'pending', 'granted', 'declined', 'joined_without_consent'],
  recording_states: ['blocked', 'armed', 'in_progress', 'complete', 'cancelled'],
  steps: [
    'grant_consent',
    'decline_consent',
    'join_without_consent',
    'start_recording',
    'finish_recording',
    'cancel_recording',
  ],
  prompt_modes: ['first_guest_with_audio', 'every_guest'],
  default_prompt_mode: 'first_guest_with_audio',
  precall_window_minutes: [10, 20],
  profile_resolution_key: 'organizer_email',
  recording_bot_email: 'coordinator@gong.io',
  switches: {
    consent_page_enabled: 'consent_page_enabled',
    enforce_consent_page: 'enforce_consent_page',
    allow_join_without_consent: 'allow_join_without_consent',
    precall_email_enabled: 'precall_email_enabled',
    audio_prompt_enabled: 'audio_prompt_enabled',
  },
}

const DECISIONS = {
  count: 2,
  decisions: [
    {
      id: 'wf060-enforcement-off-makes-the-page-advisory',
      question: "What does the consent gate do when 'Enforce use of consent page' is off?",
      decision: 'The page is still issued and a decision is still recorded.',
      evidence: 'The research states what enforcement achieves.',
      rejected: 'Refuse to record whenever the page is off or enforcement is off.',
      cost_of_the_rejected_reading: 'Clearing one checkbox would stop every recording.',
      residual_risk: 'The call is recorded after a participant said no.',
      surface: 'decisions.initial',
    },
    {
      id: 'wf060-audio-prompt-fires-once-per-call',
      question: 'When does the audio prompt fire?',
      decision: 'On the first guest with audio on.',
      evidence: 'The research offers both and chooses neither.',
      rejected: 'Fire on every guest.',
      cost_of_the_rejected_reading: 'A legal notice becomes an argument.',
      residual_risk: 'A late joiner does not hear the prompt.',
      surface: 'profiles.validate_audio_prompt',
    },
  ],
}

function baseRoutes(overrides = {}) {
  return {
    '/records/room': ROOMS,
    [`${BASE}/summary`]: SUMMARY,
    [`${BASE}/vocabulary`]: VOCABULARY,
    [`${BASE}/bookings`]: BOOKINGS,
    [`${BASE}/decisions`]: DECISIONS,
    [`${BASE}/profiles`]: { count: 0, profiles: [] },
    [`${BASE}/users`]: { count: 0, users: [] },
    ...overrides,
  }
}

async function renderBoard(routes = baseRoutes()) {
  const calls = stubApi(routes)
  render(<Page />)
  await screen.findByRole('heading', { name: 'Consent recording', level: 1 })
  await screen.findByText('b-waiting')
  return calls
}

describe('the descriptor', () => {
  it('exports the id the backend names, so the two halves are findable by one name', () => {
    expect(descriptor.id).toBe('wf-060-auto-join-and-record-with-consent')
    expect(descriptor.label).toBeTruthy()
    expect(typeof descriptor.Component).toBe('function')
  })
})

describe('the board', () => {
  it('states the gate in the page heading, in its own words', async () => {
    await renderBoard()
    expect(
      screen.getByText(/stays blocked until somebody consents/i),
    ).toBeInTheDocument()
  })

  it('reports the measured counts rather than the ones it hoped for', async () => {
    await renderBoard()
    const blocked = screen.getByText('Recordings blocked').closest('div')
    expect(within(blocked.parentElement).getByText('1')).toBeInTheDocument()
    const complete = screen.getByText('Recordings complete').closest('div')
    expect(within(complete.parentElement).getByText('2')).toBeInTheDocument()
  })

  it('shows both axes of the state machine on every booking', async () => {
    await renderBoard()
    const card = screen.getByText('b-waiting').closest('div.rounded-sm')
    expect(within(card).getByText('blocked')).toBeInTheDocument()
    expect(within(card).getByText('Waiting on a consent decision')).toBeInTheDocument()
    expect(within(card).getByText('pending')).toBeInTheDocument()
    expect(within(card).getByText('No answer yet')).toBeInTheDocument()
  })

  it('says the key the consent profile resolves on', async () => {
    await renderBoard()
    // One per booking card, because the key is a property of every booking rather than
    // of the board. Both are the same key, and that is the point.
    expect(screen.getAllByText('organizer_email').length).toBeGreaterThanOrEqual(2)
  })

  it('offers an empty state rather than a bare list when there is nothing yet', async () => {
    stubApi({
      '/records/room': ROOMS,
      [`${BASE}/summary`]: { ...SUMMARY, bookings: 0 },
      [`${BASE}/vocabulary`]: VOCABULARY,
      [`${BASE}/bookings`]: { count: 0, bookings: [] },
    })
    render(<Page />)
    expect(await screen.findByText(/No bookings are being recorded yet/i)).toBeInTheDocument()
  })

  it('surfaces a record that contradicts itself, above the board', async () => {
    stubApi(
      baseRoutes({
        [`${BASE}/summary`]: {
          ...SUMMARY,
          inconsistent: ['b-bad: terminal state recorded means recording complete'],
        },
      }),
    )
    render(<Page />)
    expect(await screen.findByText(/contradict themselves/i)).toBeInTheDocument()
    expect(
      screen.getByText('b-bad: terminal state recorded means recording complete'),
    ).toBeInTheDocument()
  })

  it('reports a failed load with a retry rather than an empty page', async () => {
    stubApi({
      '/records/room': ROOMS,
      [`${BASE}/summary`]: httpError(500, { detail: 'boom' }),
    })
    render(<Page />)
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument()
  })
})

describe('the recorded-after-a-refusal callout', () => {
  it('names the configuration that produced it', async () => {
    stubApi(
      baseRoutes({
        [`${BASE}/bookings`]: {
          count: 1,
          bookings: [
            booking('b-advisory', {
              state: 'recorded',
              consent_state: 'declined',
              recording_state: 'complete',
              terminal: true,
              enforced: false,
            }),
          ],
        },
      }),
    )
    render(<Page />)
    const note = await screen.findByText(/recorded after a participant refused consent/i)
    expect(note).toBeInTheDocument()
    expect(screen.getByText(/Enforce use of consent page/)).toBeInTheDocument()
    expect(screen.getByText('enforcement is off')).toBeInTheDocument()
  })

  it('does not appear on a booking whose profile enforces consent', async () => {
    stubApi(
      baseRoutes({
        [`${BASE}/bookings`]: {
          count: 1,
          bookings: [
            booking('b-declined-enforced', {
              state: 'cancelled',
              consent_state: 'declined',
              recording_state: 'cancelled',
              terminal: true,
              enforced: true,
            }),
          ],
        },
      }),
    )
    render(<Page />)
    await screen.findByText('b-declined-enforced')
    expect(screen.queryByText(/recorded after a participant refused consent/i)).toBeNull()
  })

  it('renders nothing at all for an enforcing profile', () => {
    const { container } = render(<RecordedAfterDeclineNote enforced />)
    expect(container).toBeEmptyDOMElement()
  })
})

describe('the state chips', () => {
  it('never carries its meaning in colour alone', () => {
    const { rerender } = render(<RecordingChip state="blocked" />)
    // The mark, the server's own state name, and a written sentence.
    expect(screen.getByText('blocked')).toBeInTheDocument()
    expect(screen.getByText('Waiting on a consent decision')).toBeInTheDocument()
    expect(document.querySelector('svg')).not.toBeNull()

    rerender(<RecordingChip state="complete" />)
    expect(screen.getByText('Recording complete')).toBeInTheDocument()

    rerender(<RecordingChip state="cancelled" />)
    expect(screen.getByText('Not recorded')).toBeInTheDocument()
  })

  it('says plainly that nobody was asked, rather than not required', () => {
    render(<ConsentChip state="not_required" enforced={false} />)
    // The difference between the two matters: one is a page nobody was shown, the other
    // is a page whose answer did not count.
    expect(screen.getByText('Nobody was asked')).toBeInTheDocument()
  })

  it('marks a decline made under an unenforced profile as such', () => {
    render(<ConsentChip state="declined" enforced={false} />)
    expect(screen.getByText('enforcement is off')).toBeInTheDocument()
  })

  it('says nothing about enforcement on a granted consent', () => {
    render(<ConsentChip state="granted" enforced />)
    expect(screen.getByText('Consent given')).toBeInTheDocument()
    expect(screen.queryByText('enforcement is off')).toBeNull()
  })
})

describe('a refused step', () => {
  it('reports the step that is available instead of only that this one is not', async () => {
    const calls = await renderBoard(
      baseRoutes({
        [`${BASE}/bookings/b-waiting/recording/start`]: httpError(409, {
          error: 'illegal_transition',
          detail: "cannot start_recording from state 'awaiting_consent'",
          state: 'awaiting_consent',
          step: 'start_recording',
          allowed: ['grant_consent', 'decline_consent', 'join_without_consent'],
        }),
      }),
    )

    const card = screen.getByText('b-waiting').closest('div.rounded-sm')
    await userEvent.click(within(card).getByRole('button', { name: /recording started/i }))

    expect(await screen.findByText('That step was refused')).toBeInTheDocument()
    expect(screen.getByText(/cannot start_recording/)).toBeInTheDocument()
    // The alternatives, so the operator knows what to press instead of being told only
    // what does not work.
    expect(screen.getByText(/grant_consent, decline_consent, join_without_consent/)).toBeInTheDocument()

    const pressed = calls.filter((call) => call.method === 'POST')
    expect(pressed.map((call) => call.path)).toContain(
      `${BASE}/bookings/b-waiting/recording/start`,
    )
  })

  it('sends the researched decision names rather than a status a page made up', async () => {
    const calls = await renderBoard()
    const card = screen.getByText('b-waiting').closest('div.rounded-sm')

    await userEvent.click(within(card).getByRole('button', { name: /consent given/i }))
    await waitFor(() => {
      expect(calls.some((call) => call.path.includes('/consent?decision=granted'))).toBe(true)
    })

    await userEvent.click(within(card).getByRole('button', { name: /consent refused/i }))
    await waitFor(() => {
      expect(calls.some((call) => call.path.includes('/consent?decision=declined'))).toBe(true)
    })
  })
})

describe('the decision record', () => {
  it('serves what the workflow decided and what it rejected', async () => {
    stubApi(baseRoutes())
    render(<Page />)
    await screen.findByText('b-waiting')
    await userEvent.click(screen.getByRole('button', { name: 'Decisions' }))

    expect(
      await screen.findByText(/must derive the missing steps and record the derivation/i),
    ).toBeInTheDocument()
    expect(
      screen.getByText("What does the consent gate do when 'Enforce use of consent page' is off?"),
    ).toBeInTheDocument()
  })

  it('keeps the evidence and the rejected reading behind a control', async () => {
    stubApi(baseRoutes())
    render(<Page />)
    await screen.findByText('b-waiting')
    await userEvent.click(screen.getByRole('button', { name: 'Decisions' }))

    expect(screen.queryByText(/clearing one checkbox would stop every recording/i)).toBeNull()
    await userEvent.click(
      screen.getAllByRole('button', { name: /show the reasoning/i })[0],
    )
    expect(
      await screen.findByText(/clearing one checkbox would stop every recording/i),
    ).toBeInTheDocument()
    // Expanding the first card turns its control into "Hide", so only the second card's
    // "show the reasoning" button is left. Each card keeps its own cost behind its own
    // control, so expanding one does not answer a question about the other.
    expect(screen.queryByText(/a legal notice becomes an argument/i)).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: /show the reasoning/i }))
    expect(screen.getByText(/a legal notice becomes an argument/i)).toBeInTheDocument()
  })
})

describe('the consent profiles panel', () => {
  const PROFILES = {
    count: 1,
    profiles: [
      {
        id: 'prof_1',
        collection: 'wf060_consent_profile',
        room_id: 'room_a',
        data: {
          name: 'Standard recording consent',
          description: 'This call will be recorded for note taking.',
          consent_page_enabled: true,
          enforce_consent_page: true,
          allow_join_without_consent: true,
          precall_email_enabled: true,
          audio_prompt_enabled: true,
          providers: { zoom: 'dynamic_link' },
          default_provider: 'zoom',
          locales: ['en'],
          audio_prompt: { mode: 'first_guest_with_audio' },
          is_default: true,
        },
      },
    ],
  }

  it('names the researched prompt mode rather than paraphrasing it', async () => {
    stubApi(baseRoutes({ [`${BASE}/profiles`]: PROFILES }))
    render(<Page />)
    await screen.findByText('b-waiting')
    await userEvent.click(screen.getByRole('button', { name: 'Consent profiles' }))

    expect(await screen.findByText('first_guest_with_audio')).toBeInTheDocument()
    expect(screen.getByText('default')).toBeInTheDocument()
    expect(screen.getByText('enforcing')).toBeInTheDocument()
  })

  it('labels every switch in the words the backend sent', async () => {
    stubApi(baseRoutes({ [`${BASE}/profiles`]: PROFILES }))
    render(<Page />)
    await screen.findByText('b-waiting')
    await userEvent.click(screen.getByRole('button', { name: 'Consent profiles' }))

    await userEvent.click(await screen.findByRole('button', { name: /edit the switches/i }))
    expect(screen.getByLabelText('enforce_consent_page')).toBeInTheDocument()
    expect(screen.getByLabelText('allow_join_without_consent')).toBeInTheDocument()
    // The hint states the consequence, so the switch can be read without knowing the
    // research.
    expect(screen.getByText(/the recording is then cancelled/i)).toBeInTheDocument()
  })

  it('sends only the fields the operator changed', async () => {
    const calls = stubApi(baseRoutes({ [`${BASE}/profiles`]: PROFILES }))
    render(<Page />)
    await screen.findByText('b-waiting')
    await userEvent.click(screen.getByRole('button', { name: 'Consent profiles' }))

    await userEvent.click(await screen.findByRole('button', { name: /edit the switches/i }))
    await userEvent.click(screen.getByLabelText('enforce_consent_page'))
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => {
      const saved = calls.find((call) => call.method === 'PATCH')
      expect(saved).toBeTruthy()
      expect(JSON.parse(saved.body).enforce_consent_page).toBe(false)
    })
  })

  it('puts a refused field message beside the field that caused it', async () => {
    stubApi(
      baseRoutes({
        [`${BASE}/profiles`]: PROFILES,
        [`${BASE}/profiles/prof_1`]: httpError(400, {
          error: 'consent_profile_invalid',
          detail: '1 field is not acceptable: providers',
          errors: { providers: 'at least one provider is required when the consent page is on' },
        }),
      }),
    )
    render(<Page />)
    await screen.findByText('b-waiting')
    await userEvent.click(screen.getByRole('button', { name: 'Consent profiles' }))

    await userEvent.click(await screen.findByRole('button', { name: /edit the switches/i }))
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))

    expect(
      await screen.findByText(/at least one provider is required when the consent page is on/i),
    ).toBeInTheDocument()
  })

  it('says why a booking cannot be resolved when the directory is empty', async () => {
    stubApi(baseRoutes({ [`${BASE}/profiles`]: PROFILES }))
    render(<Page />)
    await screen.findByText('b-waiting')
    await userEvent.click(screen.getByRole('button', { name: 'Consent profiles' }))

    expect(await screen.findByText(/No directory users/i)).toBeInTheDocument()
    expect(screen.getByText(/resolves a profile by the organiser's email/i)).toBeInTheDocument()
  })
})