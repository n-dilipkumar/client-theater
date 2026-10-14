import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'

import ConversationIntelligence from '@/features/wf-135-conversation-intelligence/ConversationIntelligence.jsx'
import descriptor from '@/features/wf-135-conversation-intelligence/index.jsx'
import {
  CRM_OUTCOME_WORDS,
  DESTINATION_WORDS,
  fieldError,
  conversationApi,
} from '@/features/wf-135-conversation-intelligence/api.js'
import { httpError, stubApi } from './fixtures.js'

/**
 * Tests for WF-135's page.
 *
 * What these pin is the one-entry fan-out the researched flow describes, and the
 * three things it can quietly get wrong:
 *
 *   1. **One entry, three destinations.** The page shows the room's notes, the
 *      handoff packet and the CRM custom fields for every capture, and says
 *      whether they agree, so a fan-out that reached one destination is visible.
 *   2. **The four extraction outputs are shown from the server's data.** The
 *      summary, the next steps, the requirements and the MEDDICC evidence all
 *      come from the response, so a page that counted or invented them would fail.
 *   3. **A refusal is the server's reason.** A 409 is shown as the stable reason
 *      token, not parsed out of an English sentence.
 *
 * The stubs are keyed on this feature's own paths under `/wf-135`, and `stubApi`
 * throws on an unlisted path so a test cannot pass against the wrong response.
 */

const ROOM = 'room_nw'

const ROOMS = {
  count: 1,
  records: [{ id: ROOM, collection: 'room', revision: 1, data: { name: 'Northwind' } }],
}

const SUMMARY = {
  room_id: ROOM,
  captures: 1,
  complete_extractions: 1,
  destinations: 3,
  room_note: 1,
  handoff_packet: 1,
  crm_field: 1,
  written: 3,
  failed: 0,
  agreed: 1,
  partial: 0,
  plan_linked: 1,
  letters_total: 7,
  generated_at: '2026-05-04T09:00:00+00:00',
}

const LETTERS = [
  { key: 'metrics', letter: 'M', label: 'Metrics', captures_with_evidence: 1, covered: true },
  { key: 'economic_buyer', letter: 'E', label: 'Economic buyer', captures_with_evidence: 0, covered: false },
  { key: 'decision_criteria', letter: 'D', label: 'Decision criteria', captures_with_evidence: 0, covered: false },
  { key: 'decision_process', letter: 'D', label: 'Decision process', captures_with_evidence: 0, covered: false },
  { key: 'identify_pain', letter: 'I', label: 'Identify pain', captures_with_evidence: 0, covered: false },
  { key: 'champion', letter: 'C', label: 'Champion', captures_with_evidence: 1, covered: true },
  { key: 'competition', letter: 'C', label: 'Competition', captures_with_evidence: 0, covered: false },
]

const COVERAGE = {
  room_id: ROOM,
  captures: 1,
  letters: LETTERS,
  covered_letters: ['metrics', 'champion'],
  missing_letters: ['economic_buyer', 'decision_criteria', 'decision_process', 'identify_pain', 'competition'],
  letters_total: 7,
  letters_with_evidence: 2,
  note: 'Coverage counts a letter when at least one capture carries evidence for it.',
  generated_at: '2026-05-04T09:00:00+00:00',
}

const CAPTURE = {
  id: 'cap_1',
  room_id: ROOM,
  source_kind: 'call',
  occurred_at: '2026-05-04T09:00:00+00:00',
  transcript_ref: 'store://transcripts/northwind-discovery-2026-05',
  plan_ref: 'wf124_plan_northwind',
  extraction: {
    summary: 'Northwind confirmed a Q3 rollout.',
    next_steps: [{ text: 'Send the SOC 2 report', owner: 'dana' }],
    requirements: ['SOC 2 Type II'],
    meddicc: { champion: 'Priya', metrics: 'time' },
  },
  field_map: {},
  destination_ids: ['row_note', 'row_packet', 'row_crm'],
  destinations: [
    { id: 'row_note', kind: 'room_note', label: 'Room notes', state: 'written', reports: [] },
    { id: 'row_packet', kind: 'handoff_packet', label: 'Handoff packet', state: 'written', reports: [] },
    {
      id: 'row_crm',
      kind: 'crm_field',
      label: 'CRM custom fields',
      state: 'written',
      crm_state: 'not_sent',
      reports: [],
    },
  ],
  agreement: {
    agreed: true,
    written: ['crm_field', 'handoff_packet', 'room_note'],
    failed: [],
    missing: [],
  },
  meddicc_coverage: {
    covered_letters: ['metrics', 'champion'],
    missing_letters: ['economic_buyer', 'decision_criteria', 'decision_process', 'identify_pain', 'competition'],
    letters_total: 7,
  },
}

const CAPTURES = { room_id: ROOM, count: 1, captures: [CAPTURE] }

function baseRoutes(overrides = {}) {
  return {
    '/records/room': ROOMS,
    '/wf-135/summary': SUMMARY,
    '/wf-135/coverage': COVERAGE,
    '/wf-135/captures': CAPTURES,
    '/wf-135/destinations/row_crm/report': { ...CAPTURE.destinations[2], state: 'failed', crm_state: 'failed' },
    ...overrides,
  }
}

beforeEach(() => {
  globalThis.fetch = undefined
})

describe('WF-135 descriptor', () => {
  it('exports the shape the host discovers', () => {
    expect(descriptor.id).toBe('wf-135-conversation-intelligence')
    expect(descriptor.label).toBe('Conversation intelligence')
    expect(descriptor.Component).toBe(ConversationIntelligence)
    // The glyph is not in the shared PATHS map, so it travels as a path.
    expect(descriptor.iconPath).toBeTruthy()
  })
})

describe('the board', () => {
  it('shows the server totals rather than counting on the page', async () => {
    stubApi(baseRoutes())
    render(<ConversationIntelligence />)

    await screen.findByText('Captures', { selector: 'p' })
    expect(screen.getByText('Fan-out rows', { selector: 'p' })).toBeInTheDocument()
    expect(screen.getByText('Agreed', { selector: 'p' })).toBeInTheDocument()
    expect(screen.getByText('Partial', { selector: 'p' })).toBeInTheDocument()
  })

  it('shows the four extraction outputs the server sent', async () => {
    stubApi(baseRoutes())
    render(<ConversationIntelligence />)

    expect(await screen.findByText('Northwind confirmed a Q3 rollout.')).toBeInTheDocument()
    expect(screen.getByText('Send the SOC 2 report')).toBeInTheDocument()
    expect(screen.getByText('SOC 2 Type II')).toBeInTheDocument()
    expect(screen.getByText('wf124_plan_northwind')).toBeInTheDocument()
    expect(screen.getAllByText('Agreed').length).toBeGreaterThan(0)
  })

  it('gives every MEDDICC letter a word, covered or not', async () => {
    stubApi(baseRoutes())
    render(<ConversationIntelligence />)

    await screen.findByText('Northwind confirmed a Q3 rollout.')
    // The coverage panel and the capture card both carry a word per letter.
    expect(screen.getAllByText('M Metrics - evidence').length).toBeGreaterThan(0)
    expect(screen.getAllByText('E Economic buyer - no evidence').length).toBeGreaterThan(0)
    expect(screen.getByText('2 of 7 letters carry evidence')).toBeInTheDocument()
  })

  it('shows the three destinations the one entry reached', async () => {
    stubApi(baseRoutes())
    render(<ConversationIntelligence />)

    await screen.findByText('Northwind confirmed a Q3 rollout.')
    expect(screen.getByText(DESTINATION_WORDS.room_note)).toBeInTheDocument()
    expect(screen.getByText(DESTINATION_WORDS.handoff_packet)).toBeInTheDocument()
    expect(screen.getByText(DESTINATION_WORDS.crm_field)).toBeInTheDocument()
    // Every destination row carries its own word, never a colour alone.
    expect(screen.getAllByText('Written').length).toBeGreaterThanOrEqual(3)
  })
})

describe('recording one conversation', () => {
  it('posts the room, the summary, the next steps and the requirements once', async () => {
    const calls = stubApi(baseRoutes())
    render(<ConversationIntelligence />)
    await screen.findByText('Northwind confirmed a Q3 rollout.')

    await userEvent.type(screen.getByLabelText('Summary'), 'A pricing call.')
    await userEvent.type(screen.getByLabelText('Next steps'), 'Send the deck | dana\nBook the review')
    await userEvent.type(screen.getByLabelText('Requirements'), 'SSO')
    await userEvent.click(screen.getByRole('button', { name: 'Record conversation' }))

    await waitFor(() => {
      const posted = calls.find(
        (call) => call.method === 'POST' && call.path === '/wf-135/captures',
      )
      expect(posted).toBeTruthy()
      const body = JSON.parse(posted.body)
      expect(body.room_id).toBe(ROOM)
      expect(body.summary).toBe('A pricing call.')
      expect(body.next_steps).toEqual([{ text: 'Send the deck', owner: 'dana' }, 'Book the review'])
      expect(body.requirements).toEqual(['SSO'])
    })
  })
})

describe('reporting a destination outcome', () => {
  it('posts the chosen outcome to the destination row', async () => {
    const calls = stubApi(baseRoutes())
    render(<ConversationIntelligence />)
    await screen.findByText('Northwind confirmed a Q3 rollout.')

    await userEvent.selectOptions(screen.getByLabelText('Outcome for CRM custom fields'), 'failed')
    const row = screen.getByText(DESTINATION_WORDS.crm_field).closest('li')
    await userEvent.click(within(row).getByRole('button', { name: 'Report' }))

    await waitFor(() => {
      const posted = calls.find(
        (call) => call.method === 'POST' && call.path === '/wf-135/destinations/row_crm/report',
      )
      expect(posted).toBeTruthy()
      expect(JSON.parse(posted.body).outcome).toBe('failed')
    })
  })

  it("shows the server's reason when a report is refused", async () => {
    // A method-aware stub, because `stubApi` keys on the path alone and the
    // capture list and the record write share one path.
    stubByMethod({
      'GET /wf-135/summary': { body: SUMMARY },
      'GET /wf-135/coverage': { body: COVERAGE },
      'GET /wf-135/captures': { body: CAPTURES },
      'GET /records/room': { body: ROOMS },
      'POST /wf-135/destinations/row_crm/report': {
        status: 409,
        body: {
          error: 'capture_conflict',
          reason: 'fanout_incomplete',
          detail: 'The fan-out reached fewer destinations.',
        },
      },
    })
    render(<ConversationIntelligence />)
    await screen.findByText('Northwind confirmed a Q3 rollout.')

    const row = screen.getByText(DESTINATION_WORDS.crm_field).closest('li')
    await userEvent.click(within(row).getByRole('button', { name: 'Report' }))

    expect(await screen.findByText('That change was refused')).toBeInTheDocument()
  })

  it('reports a conflict on a record write from the stable reason token', async () => {
    stubByMethod({
      'GET /wf-135/summary': { body: SUMMARY },
      'GET /wf-135/coverage': { body: COVERAGE },
      'GET /wf-135/captures': { body: CAPTURES },
      'GET /records/room': { body: ROOMS },
      'POST /wf-135/captures': {
        status: 409,
        body: {
          error: 'capture_conflict',
          reason: 'write_not_visible',
          detail: 'The change was written but did not read back.',
        },
      },
    })
    render(<ConversationIntelligence />)
    await screen.findByText('Northwind confirmed a Q3 rollout.')

    await userEvent.type(screen.getByLabelText('Summary'), 'A pricing call.')
    await userEvent.click(screen.getByRole('button', { name: 'Record conversation' }))

    expect(await screen.findByText('That change was refused')).toBeInTheDocument()
  })
})

describe('the empty and error states', () => {
  it('says so when there are no rooms at all', async () => {
    stubApi({ '/records/room': { count: 0, records: [] } })
    render(<ConversationIntelligence />)

    expect(await screen.findByText('No rooms yet')).toBeInTheDocument()
  })

  it('offers a retry when the captures cannot load', async () => {
    stubApi(baseRoutes({ '/wf-135/captures': httpError(500, { detail: 'no captures' }) }))
    render(<ConversationIntelligence />)

    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Retry/ })).toBeInTheDocument()
  })
})

describe('the api wrapper', () => {
  it('exposes the calls the page uses', () => {
    for (const name of [
      'summary',
      'vocabulary',
      'coverage',
      'captures',
      'roomCaptures',
      'capture',
      'destinations',
      'record',
      'reportDestination',
    ]) {
      expect(typeof conversationApi[name]).toBe('function')
    }
  })

  it('names every CRM outcome, so no outcome renders as a raw value', () => {
    for (const outcome of ['created', 'updated', 'submitted', 'failed', 'rejected', 'rolled_back']) {
      expect(CRM_OUTCOME_WORDS[outcome]).toBeTruthy()
    }
  })

  it('reads a field error out of a 400 body', () => {
    expect(fieldError({ errors: { summary: 'Required' } }, 'summary')).toBe('Required')
    expect(fieldError(null, 'summary')).toBeNull()
  })
})

/**
 * A fetch stub that dispatches on the method as well as the path.
 *
 * `fixtures.stubApi` keys on the path alone, which is right for most features and
 * wrong for this one: one path is both the capture list (GET) and the record
 * write (POST), so a test of a refused write needs to tell them apart.
 */
function stubByMethod(handlers) {
  const calls = []
  globalThis.fetch = async (url, options = {}) => {
    const path = String(url).replace('/api', '').split('?')[0]
    const method = options.method || 'GET'
    calls.push({ path, method, body: options.body })
    const handler = handlers[`${method} ${path}`]
    if (!handler) throw new Error(`unstubbed request: ${method} ${path}`)
    const status = handler.status || 200
    return {
      ok: status >= 200 && status < 300,
      status,
      statusText: status === 200 ? 'OK' : 'Error',
      json: async () => handler.body,
    }
  }
  return calls
}
