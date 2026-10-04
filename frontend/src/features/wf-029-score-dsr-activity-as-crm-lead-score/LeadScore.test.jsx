/**
 * The page, tested against real API payloads.
 *
 * These tests exist for the parts of the page where a mistake would be
 * *believable* rather than visibly broken: a signed score rendered as a bare number,
 * a saved criterion that looks armed when its Dock property was never provisioned, a
 * third-party contact property that reads as though it were written, a missing
 * filter rendered as though the server had accepted one, and a recorded CRM request
 * that reads as a completed one.
 *
 * Every assertion is about something the research fixes, not about markup.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import descriptor from './index'
import { signedScore } from './api'

const VOCABULARY = {
  score_property: 'HubSpot Score',
  actionability_note:
    'Nothing happens on the seller screen. The scoring rule is continuous, so a score that moved is a record of what the buyer activity did, not a task for anyone.',
  execution_note:
    'Recorded, not executed. The research names the HubSpot CRM API as the write side and this build holds no CRM credential.',
  baseline_quote:
    "For activities like Clicks, Downloads, Interactions and Views, we recommend using the 'Occurred' filter as a baseline.",
  integration: { default: 'hubspot', required_scopes: ['crm.objects.contacts.read', 'crm.objects.contacts.write'] },
  buckets: [
    { name: 'positive', sign: 1, label: 'Positive score - matching activity adds to the score.' },
    { name: 'negative', sign: -1, label: 'Negative score - matching activity subtracts.' },
  ],
  families: [
    { name: 'views', group: 'analytics_events', label: 'Views - Occurred only.', refinements: ['occurred'] },
    {
      name: 'clicks',
      group: 'analytics_events',
      label: 'Clicks - Occurred, then the link name.',
      refinements: ['occurred', 'link_name'],
    },
    {
      name: 'downloads',
      group: 'analytics_events',
      label: 'Downloads - Occurred, then the file name.',
      refinements: ['occurred', 'file_name'],
    },
    {
      name: 'interactions',
      group: 'analytics_events',
      label: 'Interactions - Occurred, then the link name.',
      refinements: ['occurred', 'link_name'],
    },
    {
      name: 'map_activity',
      group: 'map_activity',
      label: 'MAP activity - Occurred, then the task name.',
      refinements: ['occurred', 'task_name'],
    },
  ],
  refinements: [
    { name: 'occurred', label: 'Occurred - a UTC date.' },
    { name: 'link_name', label: 'Link name - the URL the contact clicked.' },
    { name: 'file_name', label: 'File name - the file the contact downloaded.' },
    { name: 'task_name', label: 'Task name - the plan task the contact completed.' },
  ],
}

const ROOMS = {
  records: [
    { id: 'room_1', data: { name: 'Northwind Traders', account: 'Northwind Traders' } },
    { id: 'room_2', data: { name: 'Contoso Health', account: 'Contoso Health' } },
  ],
}

const INTEGRATIONS = {
  count: 1,
  required_scopes: ['crm.objects.contacts.read', 'crm.objects.contacts.write'],
  integrations: [
    {
      id: 'int_1',
      vendor: 'hubspot',
      label: 'HubSpot portal',
      enabled: true,
      portal_id: 'hs_demo_portal',
      scopes: ['crm.objects.contacts.read', 'crm.objects.contacts.write'],
      missing_scopes: [],
      writable: true,
      minimum_score: null,
      connected_rooms: ['room_1'],
    },
  ],
}

const PROPERTIES = {
  count: 4,
  families: ['views', 'clicks', 'downloads', 'interactions', 'map_activity'],
  provisioned: ['views', 'clicks', 'downloads', 'interactions'],
  awaiting_provisioning: ['map_activity'],
  properties: [
    { id: 'p1', family: 'views', name: 'dsr_views', object: 'contact', state: 'provisioned' },
    { id: 'p2', family: 'clicks', name: 'dsr_clicks', object: 'contact', state: 'provisioned' },
    { id: 'p3', family: 'downloads', name: 'dsr_downloads', object: 'contact', state: 'provisioned' },
    {
      id: 'p4',
      family: 'interactions',
      name: 'dsr_interactions',
      object: 'contact',
      state: 'provisioned',
    },
  ],
  provisioning_note:
    'These properties come from provisioning the sales-room engagement object into the CRM.',
}

const CRITERIA = {
  count: 3,
  armed: 2,
  criteria_by_bucket: { positive: 2, negative: 1 },
  provisioned_families: ['views', 'clicks', 'downloads', 'interactions'],
  criteria: [
    {
      id: 'c1',
      family: 'downloads',
      label: 'Read the pricing pack',
      refinements: { occurred: { from: '2026-09-01' }, file_name: 'Pricing One-Pager' },
      bucket: 'positive',
      score: 20,
      score_property: 'HubSpot Score',
      property_resolved: true,
      enabled: true,
      armed: true,
      provisioned: true,
      lint: [],
      sign: 1,
    },
    {
      id: 'c2',
      family: 'map_activity',
      label: 'Signed up for a free account',
      refinements: { occurred: '2026-10-01' },
      bucket: 'positive',
      score: 50,
      score_property: 'HubSpot Score',
      property_resolved: true,
      enabled: true,
      armed: false,
      provisioned: false,
      lint: [],
      sign: 1,
    },
    {
      id: 'c3',
      family: 'interactions',
      label: 'Scored on a third-party property',
      refinements: { occurred: '2026-10-01' },
      bucket: 'negative',
      score: 15,
      score_property: 'Showing SMB Intent',
      property_resolved: false,
      enabled: true,
      armed: true,
      provisioned: true,
      lint: [
        {
          code: 'unresolved_property',
          severity: 'warning',
          field: 'score_property',
          message: 'This build does not provision that contact property.',
        },
      ],
      sign: -1,
    },
  ],
}

const SUMMARY = {
  room_id: 'room_1',
  criteria_count: 3,
  criteria_by_bucket: { positive: 2, negative: 1 },
  criteria_by_family: { downloads: 1, map_activity: 1, interactions: 1 },
  contacts_scored: 2,
  runs: 4,
  runs_that_moved: 3,
  top_score: 30,
  average_score: 10,
  contacts_above_zero: 1,
  contacts_below_zero: 1,
  provisioned_families: ['views', 'clicks', 'downloads', 'interactions'],
  awaiting_provisioning: ['map_activity'],
  integration_state: 'writable',
  missing_scopes: [],
  deal_connected: true,
  crm_plan: { write_contact: 'PATCH /crm/v3/objects/contacts/{contactId}' },
  execution_note: VOCABULARY.execution_note,
  states: [
    {
      code: 'property_not_provisioned',
      count: 1,
      meaning: 'Dock activity properties still to provision into the CRM.',
    },
    { code: 'no_criteria_saved', count: 0, meaning: 'No criterion saved, so no score can move.' },
    { code: 'negative_scores', count: 1, meaning: 'Contacts whose score is below zero.' },
    {
      code: 'runs_that_moved_nothing',
      count: 1,
      meaning: 'Runs that fired and left the number alone.',
    },
  ],
}

const SCORES = {
  count: 2,
  total: 2,
  room_id: 'room_1',
  scores: [
    {
      contact: 'priya.raman@northwind.example',
      account: 'Northwind Traders',
      score: 30,
      raw_score: 30,
      event_count: 3,
      matched_criteria: 1,
      criterion_count: 3,
      contributions: [
        {
          criterion_id: 'c1',
          label: 'Read the pricing pack',
          family: 'downloads',
          bucket: 'positive',
          points: 20,
          events: 1,
          score_property: 'HubSpot Score',
          property_resolved: true,
        },
      ],
      floor_applied: false,
    },
    {
      contact: 'dana.kelly@northwind.example',
      account: 'Northwind Traders',
      score: -10,
      raw_score: -10,
      event_count: 1,
      matched_criteria: 1,
      criterion_count: 3,
      contributions: [
        {
          criterion_id: 'c3',
          label: 'Scored on a third-party property',
          family: 'interactions',
          bucket: 'negative',
          points: -10,
          events: 1,
          score_property: 'Showing SMB Intent',
          property_resolved: false,
        },
      ],
      floor_applied: false,
    },
  ],
}

const HISTORY = {
  count: 2,
  room_id: 'room_1',
  moved_only: false,
  runs: [
    {
      run_id: 'r2',
      contact: 'priya.raman@northwind.example',
      driver: 'activity_event',
      from_score: 10,
      to_score: 30,
      delta: 20,
      changed: true,
      matched_criteria: 1,
      criterion_count: 3,
      event_count: 3,
      miss_tally: 'family_mismatch x2',
      findings: [
        {
          code: 'property_not_provisioned',
          severity: 'warning',
          message: 'No Dock activity property is provisioned for map_activity.',
        },
      ],
    },
    {
      run_id: 'r1',
      contact: 'dana.kelly@northwind.example',
      driver: 'activity_event',
      from_score: 0,
      to_score: -10,
      delta: -10,
      changed: true,
      matched_criteria: 1,
      criterion_count: 3,
      event_count: 1,
      miss_tally: 'no_events x2',
      findings: [],
    },
  ],
}

const INFERENCES = {
  sourced_quote: 'Click Add criteria for either positive or negative scores.',
  count: 2,
  inferences: [
    {
      id: 'score-is-recomputed-from-the-whole-history',
      topic: 'whether a score accumulates per event or is recomputed from history',
      basis: 'Sourced, and pulling two ways.',
      value: { model: 'recompute_from_history' },
      why: 'Re-evaluates is the operative word, and it is the one the automation note uses.',
      change_it: 'contributions_for() and recompute().',
      blast_radius: 'Whether a replayed webhook moves a score.',
      jev_audit: 'jev-20261004T024258-6152-78859',
    },
    {
      id: 'not-built',
      topic: 'what this build deliberately does not do',
      basis: 'The research names the write side.',
      value: { outbound_crm_calls: 'not built' },
      why: 'A function that opens a socket to an API it has no credentials for is not a feature.',
      change_it: 'Nothing to change.',
      blast_radius: 'What a run says it did.',
    },
  ],
}

const RUN = {
  run_id: 'r3',
  driver: 'manual',
  contact: 'priya.raman@northwind.example',
  room_id: 'room_1',
  score: 30,
  raw_score: 30,
  from_score: 30,
  delta: 0,
  changed: false,
  floor_applied: false,
  event_count: 3,
  contributions: SCORES.scores[0].contributions,
  criteria: [
    {
      criterion_id: 'c1',
      label: 'Read the pricing pack',
      family: 'downloads',
      bucket: 'positive',
      score: 20,
      matched_count: 1,
      miss_count: 2,
      points: 20,
      reason: 'matched',
      detail: "1 of the contact's 3 event(s) matched, worth 20 point(s).",
    },
    {
      criterion_id: 'c2',
      label: 'Signed up for a free account',
      family: 'map_activity',
      bucket: 'positive',
      score: 50,
      matched_count: 0,
      miss_count: 3,
      points: 0,
      reason: 'family_mismatch',
      detail: "none of the contact's 3 event(s) matched. The reasons are family_mismatch x3",
    },
  ],
  findings: [
    {
      code: 'property_not_provisioned',
      severity: 'warning',
      message: 'No Dock activity property is provisioned for map_activity.',
    },
  ],
  crm_plan: {
    executed: false,
    requests: [
      {
        purpose: 'write_contact',
        endpoint: 'PATCH /crm/v3/objects/contacts/{contactId}',
        method: 'PATCH',
        path: '/crm/v3/objects/contacts/priya.raman@northwind.example',
      },
      {
        purpose: 'batch_upsert',
        endpoint: 'POST /crm/v3/objects/contacts/batch/upsert',
        method: 'POST',
        path: '/crm/v3/objects/contacts/batch/upsert',
      },
      {
        purpose: 'association_labels',
        endpoint: 'POST /crm/v4/associations/{fromObjectType}/{toObjectType}/labels',
        method: 'POST',
        path: '/crm/v4/associations/contacts/deals/labels',
      },
    ],
    dropped_properties: [
      {
        property: 'lifecyclestage',
        accepted: false,
        comparison: "'lead' is at or behind 'opportunity', and the value can only be set forward",
      },
    ],
  },
  batch_plan: { count: 1, executed: false },
  payload_plan: { endpoint: 'POST /crm/v3/properties', unresolved_properties: [] },
}

/** Routes keyed by the exact path the page requests. */
function routes(overrides = {}) {
  return {
    '/wf-029/vocabulary': VOCABULARY,
    '/wf-029/inferences': INFERENCES,
    '/wf-029/integrations': INTEGRATIONS,
    '/wf-029/properties': PROPERTIES,
    '/wf-029/criteria': CRITERIA,
    '/wf-029/rooms/room_1/summary': SUMMARY,
    '/wf-029/rooms/room_1/scores': SCORES,
    '/wf-029/rooms/room_1/history': HISTORY,
    '/wf-029/rooms/room_1/score': RUN,
    '/records/room': ROOMS,
    ...overrides,
  }
}

async function mount(overrides = {}) {
  const table = routes(overrides)
  const calls = []
  const apiRequest = vi.fn(async (path, options = {}) => {
    calls.push({ path, options })
    // Matched on the path without its query string, so a table key can be written
    // the way a reader thinks of the route rather than the way the client builds it.
    const key = Object.keys(table).find((entry) => path === entry || path.startsWith(`${entry}?`))
    if (!key) throw new Error(`unmocked route: ${path}`)
    return table[key]
  })
  vi.doMock('@/lib/api', () => ({ apiRequest }))
  const module = await import('./LeadScore')
  render(<module.default />)
  await screen.findByRole('heading', { name: 'Lead score' })
  return { calls, apiRequest }
}

describe('WF-029 lead score page', () => {
  beforeEach(() => {
    vi.resetModules()
    vi.clearAllMocks()
  })

  it('exports a descriptor the host can discover', () => {
    expect(descriptor.id).toBe('wf-029-score-dsr-activity-as-crm-lead-score')
    expect(descriptor.label).toBe('Lead score')
    expect(descriptor.Component).toBeTruthy()
    expect(descriptor.iconPath).toBeTruthy()
    expect(typeof descriptor.order).toBe('number')
  })

  it('states the researched sentence about the seller screen', async () => {
    await mount()
    expect(await screen.findByText(VOCABULARY.actionability_note)).toBeTruthy()
  })

  it('names the score property the research names', async () => {
    await mount()
    expect(await screen.findAllByText('HubSpot Score')).not.toHaveLength(0)
    expect(screen.getByText(/DSR engagement is scored against the/)).toBeTruthy()
  })

  it('renders a signed score with its sign in words, not only in tone', async () => {
    await mount()
    const scores = await screen.findByRole('region', { name: 'Contact scores' })
    // The section exists before its data arrives, so the assertion waits for a row
    // rather than for the region. Each contact row nests a list of contributions, so
    // the rows are found by the contact they name rather than by position.
    await within(scores).findByText('priya.raman@northwind.example')
    const rows = within(scores).getAllByRole('listitem')
    const rowFor = (contact) =>
      rows.find((row) => within(row).queryByText(contact) !== null)

    const priya = rowFor('priya.raman@northwind.example')
    expect(within(priya).getByText('+30')).toBeTruthy()
    expect(within(priya).getAllByText(/above zero/).length).toBeGreaterThan(0)

    const dana = rowFor('dana.kelly@northwind.example')
    expect(within(dana).getByText('-10')).toBeTruthy()
    expect(within(dana).getAllByText(/below zero/).length).toBeGreaterThan(0)
  })

  it('shows a criterion on an unprovisioned Dock property as not armed, and says why', async () => {
    await mount()
    const panel = await screen.findByRole('region', { name: /Steps 2 to 5/ })
    await within(panel).findByText('Signed up for a free account')
    expect(within(panel).getByText('not armed')).toBeTruthy()
    expect(within(panel).getByText(/has nothing to score against and matches nothing/)).toBeTruthy()
  })

  it('shows a third-party contact property as not written by this build', async () => {
    await mount()
    const panel = await screen.findByRole('region', { name: /Steps 2 to 5/ })
    expect(
      within(panel).getAllByText(/does not provision|no property of that name is written/i)
        .length,
    ).toBeGreaterThan(0)
    expect(within(panel).getByText('unresolved_property')).toBeTruthy()
  })

  it('offers only the two buckets and only the five Dock properties', async () => {
    await mount()
    const panel = await screen.findByRole('region', { name: /Steps 2 to 5/ })
    const buckets = within(panel).getByLabelText('Score bucket')
    expect(within(buckets).getAllByRole('option').map((o) => o.value)).toEqual([
      'positive',
      'negative',
    ])
    const families = within(panel).getByLabelText('Dock property to score against')
    expect(within(families).getAllByRole('option').map((o) => o.value)).toEqual([
      'views',
      'clicks',
      'downloads',
      'interactions',
      'map_activity',
    ])
  })

  it('says a score is a magnitude and the bucket carries the sign', async () => {
    await mount()
    const panel = await screen.findByRole('region', { name: /Steps 2 to 5/ })
    expect(within(panel).getAllByText(/bucket carries the sign/).length).toBeGreaterThan(0)
  })

  it('offers the second filter the property publishes and disables it when there is none', async () => {
    const user = userEvent.setup()
    await mount()
    const panel = await screen.findByRole('region', { name: /Steps 2 to 5/ })

    // Downloads publishes occurred and file_name, so the file-name control is live.
    expect(within(panel).getByLabelText(/File name/)).not.toBeDisabled()

    // Views publishes only occurred, so the second control is disabled rather than
    // offering a filter the server refuses.
    await user.selectOptions(within(panel).getByLabelText('Dock property to score against'), 'views')
    await waitFor(() => {
      expect(within(panel).getByText('No second filter for this property')).toBeTruthy()
    })
    expect(within(panel).getByLabelText(/No second filter for this property/)).toBeDisabled()
  })

  it('sends a criterion in the names the research uses', async () => {
    const user = userEvent.setup()
    const { apiRequest } = await mount()
    const panel = await screen.findByRole('region', { name: /Steps 2 to 5/ })

    await user.selectOptions(within(panel).getByLabelText('Dock property to score against'), 'downloads')
    await user.selectOptions(within(panel).getByLabelText('Score bucket'), 'negative')
    await user.clear(within(panel).getByLabelText('Score value'))
    await user.type(within(panel).getByLabelText('Score value'), '7')
    await user.type(within(panel).getByLabelText('Label'), 'Bounced')
    await user.type(within(panel).getByLabelText(/Occurred/), '2026-10-01')
    await user.type(within(panel).getByLabelText(/File name/), 'Careers Page')
    await user.click(within(panel).getByRole('button', { name: 'Save criterion' }))

    await waitFor(() => {
      // The page also GETs /criteria on load, so the POST is picked out by method.
      const call = apiRequest.mock.calls.find(
        ([path, options]) => path === '/wf-029/criteria' && options?.method === 'POST',
      )
      expect(call).toBeTruthy()
      expect(JSON.parse(call[1].body)).toEqual({
        family: 'downloads',
        bucket: 'negative',
        score: 7,
        label: 'Bounced',
        refinements: { occurred: '2026-10-01', file_name: 'Careers Page' },
      })
    })
  })

  it('reports a saved score without claiming the CRM was written to', async () => {
    const user = userEvent.setup()
    await mount()
    const scores = await screen.findByRole('region', { name: 'Contact scores' })
    await user.type(within(scores).getByLabelText('Contact'), 'priya.raman@northwind.example')
    await user.click(within(scores).getByRole('button', { name: 'Re-evaluate now' }))

    const plan = await within(scores).findByText('What would be written')
    expect(plan).toBeTruthy()
    expect(within(scores).getAllByText('not executed').length).toBe(3)
    expect(
      within(scores).getByText('/crm/v3/objects/contacts/priya.raman@northwind.example'),
    ).toBeTruthy()
    expect(within(scores).getByText(VOCABULARY.execution_note)).toBeTruthy()
  })

  it('reports a dropped lifecyclestage with the stage comparison', async () => {
    const user = userEvent.setup()
    await mount()
    const scores = await screen.findByRole('region', { name: 'Contact scores' })
    await user.type(within(scores).getByLabelText('Contact'), 'priya.raman@northwind.example')
    await user.click(within(scores).getByRole('button', { name: 'Re-evaluate now' }))

    await within(scores).findByText('What would be written')
    expect(within(scores).getByText('not written')).toBeTruthy()
    expect(
      within(scores).getByText(/only be set forward/),
    ).toBeTruthy()
  })

  it('says why a criterion that matched nothing did not match', async () => {
    const user = userEvent.setup()
    await mount()
    const scores = await screen.findByRole('region', { name: 'Contact scores' })
    await user.type(within(scores).getByLabelText('Contact'), 'priya.raman@northwind.example')
    await user.click(within(scores).getByRole('button', { name: 'Re-evaluate now' }))

    const heading = await within(scores).findByText('Why each criterion did or did not score')
    expect(heading).toBeTruthy()
    expect(within(scores).getByText('family_mismatch')).toBeTruthy()
    expect(within(scores).getByText(/worth 20 point\(s\)/)).toBeTruthy()
  })

  it('offers both run views because the rule is continuous', async () => {
    const user = userEvent.setup()
    await mount()
    const panel = await screen.findByRole('region', { name: 'Scoring runs' })
    const picker = within(panel).getByLabelText('Show')
    expect(within(picker).getAllByRole('option').map((o) => o.value)).toEqual(['all', 'moved'])

    await user.selectOptions(picker, 'moved')
    await waitFor(() => {
      expect(within(panel).getAllByText('moved').length).toBeGreaterThan(0)
    })
  })

  it('shows a run that moved nothing as a run rather than hiding it', async () => {
    await mount({
      '/wf-029/rooms/room_1/history': {
        ...HISTORY,
        count: 3,
        runs: [
          HISTORY.runs[0],
          HISTORY.runs[1],
          { ...HISTORY.runs[1], run_id: 'r0', changed: false, delta: 0, to_score: 0, from_score: 0 },
        ],
      },
    })
    const panel = await screen.findByRole('region', { name: 'Scoring runs' })
    expect(within(panel).getAllByText('no change').length).toBe(1)
    expect(within(panel).getAllByText('moved').length).toBe(2)
    expect(within(panel).getAllByText(/Miss tally:/).length).toBe(3)
    expect(within(panel).getAllByText('family_mismatch x2').length).toBe(1)
  })

  it('lists the states that are not successes with their counts', async () => {
    await mount()
    const panel = await screen.findByRole('region', { name: 'This room' })
    expect(within(panel).getByText('property_not_provisioned')).toBeTruthy()
    expect(within(panel).getByText('negative_scores')).toBeTruthy()
    expect(within(panel).getByText('runs_that_moved_nothing')).toBeTruthy()
    expect(within(panel).getByText(/Dock activity properties still to provision/)).toBeTruthy()
  })

  it('publishes every inference with how to change it and the Jev audit id', async () => {
    await mount()
    const panel = await screen.findByRole('region', { name: /What this build decided/ })
    expect(within(panel).getByText('decided by Jev')).toBeTruthy()
    expect(within(panel).getByText('audit jev-20261004T024258-6152-78859')).toBeTruthy()
    expect(within(panel).getByText(/contributions_for\(\) and recompute\(\)/)).toBeTruthy()
  })

  it('shows an empty state rather than a blank table when nothing has scored', async () => {
    await mount({
      '/wf-029/criteria': { ...CRITERIA, count: 0, armed: 0, criteria: [] },
      '/wf-029/rooms/room_1/scores': { count: 0, total: 0, scores: [] },
    })
    expect(await screen.findByText('No criterion saved yet')).toBeTruthy()
    expect(screen.getByText('No contact scored yet')).toBeTruthy()
  })

  it('shows an empty state when no CRM organisation is registered', async () => {
    await mount({
      '/wf-029/integrations': { count: 0, required_scopes: [], integrations: [] },
    })
    expect(await screen.findByText('No CRM organisation registered')).toBeTruthy()
    expect(screen.getByText(/no contact property to write/)).toBeTruthy()
  })

  it('names a missing scope on the row rather than only as a refusal', async () => {
    await mount({
      '/wf-029/integrations': {
        count: 1,
        required_scopes: ['crm.objects.contacts.read', 'crm.objects.contacts.write'],
        integrations: [
          {
            ...INTEGRATIONS.integrations[0],
            scopes: ['crm.objects.contacts.read'],
            missing_scopes: ['crm.objects.contacts.write'],
            writable: false,
          },
        ],
      },
    })
    const panel = await screen.findByRole('region', { name: 'Step 1: the CRM integration' })
    expect(within(panel).getByText('missing')).toBeTruthy()
    expect(within(panel).getByText('crm.objects.contacts.write')).toBeTruthy()
    expect(within(panel).getByText('not writable')).toBeTruthy()
  })

  it('tells a person which properties are still to provision', async () => {
    await mount()
    const panel = await screen.findByRole('region', {
      name: 'Step 1b: the Dock activity properties',
    })
    expect(within(panel).getAllByText('not provisioned').length).toBe(1)
    expect(within(panel).getByText(/Awaiting provisioning/)).toBeTruthy()
    expect(within(panel).getByText(PROPERTIES.provisioning_note)).toBeTruthy()
  })
})

describe('the signed score helper', () => {
  it('reads the sign from the served bucket rather than from this file', () => {
    expect(signedScore(VOCABULARY, 'positive', 20)).toBe(20)
    expect(signedScore(VOCABULARY, 'negative', 10)).toBe(-10)
    expect(signedScore(VOCABULARY, 'sideways', 10)).toBe(0)
    expect(signedScore(undefined, 'positive', 5)).toBe(0)
  })
})
