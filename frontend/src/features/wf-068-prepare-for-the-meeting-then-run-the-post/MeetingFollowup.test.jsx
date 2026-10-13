import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import MeetingFollowup from '@/features/wf-068-prepare-for-the-meeting-then-run-the-post/MeetingFollowup.jsx'
import descriptor from '@/features/wf-068-prepare-for-the-meeting-then-run-the-post/index.jsx'
import { followupApi } from '@/features/wf-068-prepare-for-the-meeting-then-run-the-post/api.js'
import {
  ACTIVITY_WORD,
  STATUSES,
  STATUS_TONE,
  assignErrorWord,
  isDateTrigger,
  positionLabel,
  triggerLabel,
} from '@/features/wf-068-prepare-for-the-meeting-then-run-the-post/primitives.jsx'

/**
 * Tests for the WF-068 page.
 *
 * Six things the backend tests cannot see, and each one is a way this page would
 * otherwise be quietly wrong:
 *
 * * **The page is discovered at all.** `lib/features.js` globs
 *   `src/features/*\/index.jsx`. A feature that ships its page and its primitives
 *   but no descriptor builds green and appears in the product zero times.
 * * **The three statuses are named, not tinted.** The researched enum has exactly
 *   three values. A page that rendered them as three colours would tell a
 *   colour-blind reader that three different things happened and say what none of
 *   them were.
 * * **A per-prospect failure reads as a refusal, not as a failed batch.** The
 *   research is explicit that failures are per prospect. A page that rendered the
 *   split as "the batch failed" would send a seller to rebuild a request that
 *   mostly worked.
 * * **A date trigger says it is waiting.** The researched sentence is "they do not
 *   run immediately when an automation is created or edited", and a page that drew
 *   a date trigger like any other would suggest it had already fired.
 * * **The to-do position is the researched string.** `1/5`, not two numbers.
 * * **The two gates are quoted.** The vendor's own sentence is the text an operator
 *   acts on, and a paraphrase hides it.
 *
 * Stubs are keyed on this feature's own paths. An unstubbed path throws, so a test
 * cannot quietly pass against the wrong response.
 */

const ROOM = 'room_1'
const OWNER = 'dana@northwind.example'

const VOCABULARY = {
  ticket: 'WF-068',
  vendors: {
    gong: ['meeting_prep', 'engage_flows', 'crm_upload'],
    cal: ['followup_webhooks', 'recording_downloads'],
    none: ['to_do_queue', 'ask_anything', 'forecast_reminders'],
  },
  object_types: ['ACCOUNT', 'CONTACT', 'DEAL', 'LEAD'],
  triggers: [
    'crm_entity_created',
    'crm_entity_updated_with',
    'crm_entity_created_or_updated',
    'current_date_is',
    'tracker_detected',
  ],
  trigger_labels: {
    crm_entity_created: 'is created',
    current_date_is: 'current date is',
    tracker_detected: 'an AI tracker detects a concept on a call',
  },
  trigger_operators: ['equals', 'not_equals'],
  statuses: ['Running', 'Pending', 'Paused'],
  date_trigger_schedule_quote:
    'Date-based triggers run on a daily schedule and are evaluated each morning. They do not run immediately when an automation is created or edited.',
  date_trigger_evaluation_hour: 6,
  assignment: { max_prospects: 200, error_codes: ['InvalidArgument', 'InvalidState'] },
  gates: {
    engage_license:
      "The selected user must have an Engage license. If they don't, the automation won't run.",
    parent_account:
      'If the automation selects a contact without a parent account, the automation will fail to run.',
    personal_flow: 'Personal flows cannot be used with automations.',
  },
  todo_queue_quote:
    "To-dos are grouped into a queue that shows your position as you move through them. For example, 1/5 means you're working on the first of five to-dos in that queue.",
  prep: {
    sections: [
      'objectives',
      'key_topics',
      'prior_discussion_highlights',
      'action_items_for_you',
      'suggested_questions',
      'reminders',
    ],
    recent_activity_kinds: ['calls', 'emails', 'digital_interactions', 'crm_changes', 'texts'],
    push_notification_minutes: 30,
    keyboard_shortcut: 'Shift+M',
  },
  dry_run: { voices: ['neutral', 'warm', 'direct'], again_label: 'Go again' },
  cal: { transcription_formats: ['json', 'srt', 'txt', 'vtt'] },
}

const SUMMARY = {
  prep_pages: 1,
  dry_runs: 1,
  automations: 2,
  automations_awaiting_morning_run: 1,
  flows: 1,
  instances: 2,
  instances_by_status: { Running: 1, Pending: 1, Paused: 0 },
  todos: 5,
  todos_open: 4,
  assignment_failures: 1,
  schedule_quote: VOCABULARY.date_trigger_schedule_quote,
  statuses: STATUSES,
}

const PREP_PAGE = {
  meeting_id: 'm-1',
  title: 'Northwind renewal',
  starts_at: '2026-10-14T14:00:00+00:00',
  objective: 'Agree the renewal timeline',
  summary: {
    objectives: ['Agree the renewal timeline'],
    key_topics: ['Renewal pricing'],
    prior_discussion_highlights: [],
    action_items_for_you: ['Send the questionnaire'],
    suggested_questions: ['Who signs off?'],
    reminders: [],
  },
  sections: VOCABULARY.prep.sections,
  summary_source: 'assembled_from_store',
  participants: [{ email: 'buyer@northwind.example', name: 'Priya Raman' }],
  keyboard_shortcut: 'Shift+M',
  mobile: false,
  recent_activity: {
    calls: [{ kind: 'calls', summary: 'Discovery call, 32 minutes' }],
    emails: [],
    digital_interactions: [],
    crm_changes: [],
    texts: [],
  },
}

const AUTOMATIONS = {
  count: 2,
  automations: [
    {
      id: 'a-1',
      data: {
        name: 'Enrol a contact created in the CRM',
        enabled: true,
        assignee_email: OWNER,
        trigger: { kind: 'crm_entity_created', object_type: 'CONTACT' },
        awaiting_morning_run: false,
      },
    },
    {
      id: 'a-2',
      data: {
        name: 'Renewal reminder 30 days before close',
        enabled: true,
        assignee_email: OWNER,
        trigger: { kind: 'current_date_is', object_type: 'DEAL', offset_days: -30 },
        awaiting_morning_run: true,
        last_evaluated_at: null,
      },
    },
  ],
}

const QUEUE = {
  owner_email: OWNER,
  queue_count: 1,
  note: VOCABULARY.todo_queue_quote,
  queues: [
    {
      queue_id: 'fi_1',
      flow_reference: 'f-1',
      count: 2,
      completed: 0,
      todos: [
        {
          todo_id: 't-1',
          queue_id: 'fi_1',
          position: 1,
          position_label: '1/2',
          subject: 'Recap',
          due_on: '2026-10-09',
          flow_reference: 'f-1',
          completed_at: null,
        },
        {
          todo_id: 't-2',
          queue_id: 'fi_1',
          position: 2,
          position_label: '2/2',
          subject: 'Confirm next step',
          due_on: '2026-10-11',
          flow_reference: 'f-1',
          completed_at: null,
        },
      ],
    },
  ],
}

const INSTANCES = {
  count: 2,
  statuses: STATUSES,
  instances: [
    {
      id: 'i-1',
      data: { flow_instance_id: 'fi_1', crm_prospect_id: 'con-1', flow_id: 'f-1' },
      view: {
        status: 'Pending',
        allowed_transitions: ['Running', 'Paused'],
        touches: 2,
        touches_delivered: 0,
        terminal: false,
      },
    },
  ],
}

const SPLIT = {
  prospectsAssigned: [
    {
      crmProspectId: 'con-1',
      flowInstanceId: 'fi_1',
      flowInstanceStatus: 'Running',
      exclusive: true,
      workspaceId: 'ws_abc',
    },
  ],
  prospectsNotAssigned: [
    {
      crmProspectId: 'con-2',
      errorCode: 'InvalidArgument',
      detail: 'The selected contact has no parent account.',
    },
  ],
  requested: 2,
  assigned: 1,
  failed: 1,
}

function jsonResponse(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  }
}

/** Stub only this feature's own paths. Anything else throws, so a test cannot
 *  quietly pass against the wrong response.
 *
 *  The key is the path with the `/api` origin, the `/wf-068` prefix and any query
 *  string removed, so a route that grows a query parameter does not need the stub
 *  renamed. */
function stub(routes = {}) {
  const seen = []
  const handler = async (url, options = {}) => {
    const path = String(url).split('?')[0].replace(/^\/api/, '').replace(/^\/wf-068/, '')
    seen.push({ path, method: options.method || 'GET', body: options.body })
    if (path in routes) {
      const entry = routes[path]
      if (typeof entry === 'function') return entry({ path, options })
      return jsonResponse(entry)
    }
    throw new Error(`unstubbed WF-068 path: ${path}`)
  }
  global.fetch = vi.fn(handler)
  global.fetch.seen = seen
  return global.fetch
}

function baseRoutes(extra = {}) {
  return {
    '/records/room': { records: [{ id: ROOM, data: { name: 'Northwind' } }] },
    '/vocabulary': VOCABULARY,
    '/summary': SUMMARY,
    '/users': { count: 1, users: [{ id: 'u-1', data: { email: OWNER, engage_licensed: true } }] },
    '/prep': { count: 1, pages: [{ meeting_id: 'm-1' }] },
    '/prep/m-1': PREP_PAGE,
    '/automations': AUTOMATIONS,
    '/trackers': { count: 0, detections: [] },
    '/webhooks': { count: 0, webhooks: [] },
    '/decisions': {
      count: 2,
      decisions: [
        {
          id: 'wf068-own-crm-collections',
          topic: 'where the CRM records come from',
          decision: 'This workflow owns its own records.',
          rejected: 'Reading another feature.',
        },
        {
          id: 'wf068-per-prospect-failure',
          topic: 'a failure inside a batch',
          decision: 'It is returned per prospect.',
          rejected: 'Raising on the first bad prospect.',
        },
      ],
    },
    ...extra,
  }
}

async function renderPage(routes = baseRoutes()) {
  stub(routes)
  render(<MeetingFollowup />)
  await waitFor(() => expect(screen.getByRole('heading', { level: 1 })).toBeInTheDocument())
}

/** Choose the assignee once its option has arrived.
 *
 *  The picker renders before the directory has loaded, so selecting straight away
 *  targets a select that has no options yet and silently does nothing. Waiting for
 *  the option is what makes the queue render at all. */
async function chooseOwner() {
  const select = await screen.findByLabelText('Assignee')
  await screen.findByRole('option', { name: /dana@northwind\.example/ })
  await userEvent.selectOptions(select, OWNER)
  return select
}

describe('the descriptor', () => {
  it('is discovered by the host glob and carries the backend feature id', () => {
    expect(descriptor.id).toBe('wf-068-prepare-for-the-meeting-then-run-the-post')
    expect(typeof descriptor.Component).toBe('function')
    expect(descriptor.label).toBeTruthy()
  })

  it('passes the nav glyph as a path, because the shared PATHS map is not ours', () => {
    expect(descriptor.iconPath).toMatch(/^M\d/)
    expect(descriptor.icon).toBeTruthy()
  })
})

describe('the three researched statuses', () => {
  it('are exactly three, and each carries a tone as well as a word', () => {
    expect(STATUSES).toEqual(['Running', 'Pending', 'Paused'])
    for (const status of STATUSES) expect(STATUS_TONE[status]).toBeTruthy()
  })

  it('are named in the queue rather than only tinted', async () => {
    await renderPage(baseRoutes({ '/queue': QUEUE, '/instances': INSTANCES }))
    await chooseOwner()
    // Two places say "Pending": the instance badge and the board's own tally. Both
    // are wanted, so the claim is that each appears and that none of them is a bare
    // colour swatch with no word beside it.
    expect((await screen.findAllByText('Pending')).length).toBeGreaterThan(0)
    expect((await screen.findAllByText('Running')).length).toBeGreaterThan(0)
  })

  it('offers only the other two statuses as actions', async () => {
    await renderPage(baseRoutes({ '/queue': QUEUE, '/instances': INSTANCES }))
    await chooseOwner()
    expect(await screen.findByRole('button', { name: 'Make Running' })).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'Make Paused' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Make Pending' })).toBeNull()
  })
})

describe('the to-do queue', () => {
  it('shows the researched position label as one string', async () => {
    await renderPage(baseRoutes({ '/queue': QUEUE, '/instances': INSTANCES }))
    await chooseOwner()
    expect(await screen.findByText('1/2')).toBeInTheDocument()
    expect(await screen.findByText('2/2')).toBeInTheDocument()
  })

  it('shows the flow reference beside the queue', async () => {
    await renderPage(baseRoutes({ '/queue': QUEUE, '/instances': INSTANCES }))
    await chooseOwner()
    // The queue header names it, the instance names it, and both to-dos carry it.
    expect((await screen.findAllByText('f-1')).length).toBeGreaterThanOrEqual(3)
  })

  it('falls back to a count-shaped label rather than a blank cell', () => {
    expect(positionLabel({ position_label: '1/5' })).toBe('1/5')
    expect(positionLabel({ position: 2, queue_total: 5 })).toBe('2/5')
    expect(positionLabel(null)).toBe('0/0')
  })
})

describe('a per-prospect failure', () => {
  it('reads as one refusal beside one success, not as a failed batch', async () => {
    await renderPage(
      baseRoutes({
        '/queue': { owner_email: OWNER, queue_count: 0, queues: [] },
        '/instances': { count: 0, statuses: STATUSES, instances: [] },
        '/flows/prospects/assign': SPLIT,
      }),
    )
    await userEvent.type(screen.getByLabelText('Flow id'), 'f-1')
    await userEvent.type(screen.getByLabelText('Assignee email'), OWNER)
    await userEvent.click(screen.getByRole('button', { name: 'Assign' }))

    expect(await screen.findByText(/1 assigned and 1 refused, out of 2 requested/)).toBeInTheDocument()
    expect(screen.getByText(/one bad id does not fail the batch/)).toBeInTheDocument()
    expect(await screen.findByText('InvalidArgument')).toBeInTheDocument()
  })

  it('turns each researched error code into a phrase', () => {
    expect(assignErrorWord('InvalidArgument')).toMatch(/does not exist/)
    expect(assignErrorWord('InvalidState')).toMatch(/wrong state/)
    expect(assignErrorWord('SomethingElse')).toBeTruthy()
  })

  it('shows the researched 200-prospect cap on the form', async () => {
    await renderPage()
    expect(await screen.findByText(/Up to 200 prospects in one request/)).toBeInTheDocument()
    expect(screen.getByText(/Over that is refused, not truncated/)).toBeInTheDocument()
  })
})

describe('the two researched gates', () => {
  it('are quoted from the research rather than paraphrased', async () => {
    await renderPage()
    expect(
      await screen.findByText(
        "The selected user must have an Engage license. If they don't, the automation won't run.",
      ),
    ).toBeInTheDocument()
    expect(
      screen.getByText(
        'If the automation selects a contact without a parent account, the automation will fail to run.',
      ),
    ).toBeInTheDocument()
    expect(
      screen.getByText('Personal flows cannot be used with automations.'),
    ).toBeInTheDocument()
  })
})

describe('the daily schedule', () => {
  it('says a date trigger has not run, and why', async () => {
    await renderPage()
    expect(
      await screen.findByText(
        /Waiting for the next morning run\. A date trigger does not run the moment it is saved\./,
      ),
    ).toBeInTheDocument()
  })

  it('publishes the researched schedule sentence on the form', async () => {
    await renderPage()
    expect(
      await screen.findByText(VOCABULARY.date_trigger_schedule_quote),
    ).toBeInTheDocument()
  })

  it('recognises a date trigger from its shape rather than its name', () => {
    expect(isDateTrigger({ trigger: { kind: 'current_date_is' } })).toBe(true)
    expect(isDateTrigger({ trigger: { kind: 'crm_entity_created' } })).toBe(false)
    expect(isDateTrigger({})).toBe(false)
  })

  it('uses the researched trigger wording, not a paraphrase', () => {
    expect(
      triggerLabel({ trigger: { kind: 'crm_entity_created' } }, VOCABULARY.trigger_labels),
    ).toBe('is created')
    expect(triggerLabel({ trigger: { kind: 'nonsense' } }, VOCABULARY.trigger_labels)).toBe(
      'nonsense',
    )
  })
})

describe('the prep page', () => {
  it('renders the six researched sections as headings', async () => {
    await renderPage()
    expect(await screen.findByText('objectives')).toBeInTheDocument()
    expect(screen.getByText('action items for you')).toBeInTheDocument()
    expect(screen.getByText('suggested questions')).toBeInTheDocument()
  })

  it('says the summary is assembled rather than generated', async () => {
    await renderPage()
    expect(
      await screen.findByText(/Summary assembled from stored records/),
    ).toBeInTheDocument()
  })

  it('publishes the keyboard shortcut the research names', async () => {
    await renderPage()
    expect(await screen.findByText('Shift+M')).toBeInTheDocument()
  })

  it('groups the activity feed under the five researched words', async () => {
    await renderPage()
    for (const word of Object.values(ACTIVITY_WORD)) {
      expect(await screen.findByText(new RegExp(`^${word}`))).toBeInTheDocument()
    }
  })

  it('says the dry run synthesised nothing', async () => {
    await renderPage()
    expect(
      await screen.findByText(/The research names no voice provider, so nothing is spoken/),
    ).toBeInTheDocument()
  })
})

describe('the vendors', () => {
  it('are attributed on the page, because the evidence mixes two of them', async () => {
    await renderPage()
    expect(
      await screen.findByText(/Gong supplies the prep page, the flows and the CRM upload/),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/A link carries a token that expires, so it is recorded here and not fetched/),
    ).toBeInTheDocument()
  })
})

describe('the derived Ask anything answer', () => {
  it('names the sections it drew on', async () => {
    await renderPage(
      baseRoutes({
        '/prep/m-1/ask': {
          data: {
            question: 'what were the objectives?',
            sources: ['objectives'],
            answer: { objectives: ['Agree the renewal timeline'] },
            derived: true,
          },
        },
      }),
    )
    await userEvent.type(screen.getByLabelText('Question about this meeting'), 'objectives')
    await userEvent.click(screen.getByRole('button', { name: 'Ask' }))
    expect(await screen.findByText(/^Answered from/)).toBeInTheDocument()
    // The page names the sections it drew on, and the section list already reads
    // "objectives", so the claim is that the answer names it too.
    expect((await screen.findAllByText('objectives')).length).toBeGreaterThan(1)
  })
})

describe('loading and error states', () => {
  it('shows a spinner, then the board', async () => {
    stub(baseRoutes())
    render(<MeetingFollowup />)
    expect(screen.getByRole('status')).toBeInTheDocument()
    await waitFor(() =>
      expect(screen.getByRole('heading', { level: 1 })).toBeInTheDocument(),
    )
  })

  it('offers a retry when the board cannot be read', async () => {
    stub(
      baseRoutes({
        '/vocabulary': () => {
          throw new Error('the vocabulary route is down')
        },
      }),
    )
    render(<MeetingFollowup />)
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })
})

describe('the API wrapper', () => {
  it('keeps the researched error body on the thrown error', async () => {
    stub({
      '/flows/prospects/assign': () =>
        jsonResponse({ error: 'assignee_not_licensed', detail: 'no Engage license' }, 403),
    })
    await expect(
      followupApi.assign({ crmProspectsIds: ['con-1'], flowId: 'f-1', flowInstanceOwnerEmail: OWNER }),
    ).rejects.toMatchObject({ status: 403, code: 'assignee_not_licensed' })
  })

  it('keeps the alternatives on an illegal transition', async () => {
    stub({
      '/instances/fi_1/status': () =>
        jsonResponse(
          { error: 'flow_instance_transition_refused', status: 'Pending', allowed: ['Running'] },
          409,
        ),
    })
    await expect(followupApi.setInstanceStatus('fi_1', 'Running')).rejects.toMatchObject({
      status: 409,
      state: 'Pending',
      allowed: ['Running'],
    })
  })

  it('keeps the field-keyed map on a validation failure', async () => {
    stub({
      '/crm/sync': () =>
        jsonResponse({ error: 'crm_record_invalid', errors: { cadence_minutes: '1 to 5' } }, 400),
    })
    await expect(followupApi.syncCrm({})).rejects.toMatchObject({
      status: 400,
      errors: { cadence_minutes: '1 to 5' },
    })
  })
})
