import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'

import FieldMapping from './FieldMapping'
import descriptor from './index.jsx'
import {
  MAPPING,
  MAPPING_WITHOUT_METADATA,
  NO_METADATA,
  REPORT_WITH_FINDINGS,
  httpError,
  routes,
  stubApi,
} from './fixtures'

/**
 * Find a `<pre>` whose text contains a fragment.
 *
 * The rendered payload is one text node, so an exact-string matcher cannot see
 * inside it. This is a function matcher rather than a `data-testid`: the DOM a
 * reader inspects is the DOM under test, and a test id added only for a test is a
 * second naming of the same element.
 */
const inPre = (fragment) => (content, element) => element.tagName === 'PRE' && content.includes(fragment)

/**
 * Tests for the WF-035 field-mapping page.
 *
 * Four things a page like this can get quietly wrong, and each is pinned here:
 *
 * 1. **A finding's message is rendered, not just its badge.** A row that says
 *    `unsupported_option` and nothing else is a row an admin has to leave the app to
 *    act on, and the server already wrote the sentence for them.
 * 2. **A refused activation button is disabled and says why.** The researched gate
 *    is "before any data is written"; a button that 422s is a worse gate than one
 *    that explains itself.
 * 3. **An un-sourced request plan is labelled as one.** A plan that looks like a
 *    sourced one invites someone to send it, and this workflow's Dataverse plan is
 *    explicitly not cited.
 * 4. **A 409 on the property read is shown with the endpoints to fix it**, because
 *    "no metadata" is the first thing a new connection hits.
 *
 * Stubs are keyed on this feature's own prefix, written once in `fixtures.js`.
 */

/** Render, choose the connection, then choose its mapping. */
async function renderPage(overrides = {}) {
  const calls = stubApi(routes(overrides))
  const user = userEvent.setup()
  render(<FieldMapping />)
  await screen.findByText('HubSpot — Northwind portal')
  await user.click(screen.getByText('HubSpot — Northwind portal'))
  // The grid belongs to a mapping, not to a connection, so the page waits for the
  // second click rather than guessing which mapping a reader wanted.
  await user.click(await screen.findByText('contacts'))
  return { user, calls }
}

afterEach(() => {
  delete globalThis.fetch
})

describe('registration', () => {
  it('exports a descriptor the host can discover', () => {
    // The glob in lib/features.js only keeps a module whose *default* export has a
    // Component and an id, so a descriptor without them is a silent no-op.
    expect(descriptor.id).toBe('wf-035-map-sales-room-fields-onto-crm-fields-')
    expect(descriptor.label).toBe('Field mapping')
    expect(typeof descriptor.Component).toBe('function')
  })

  it('carries its own glyph rather than editing the shared icon map', () => {
    expect(descriptor.iconPath).toMatch(/^M4 6h5/)
  })
})

describe('the header', () => {
  it('names the workflow and its researched gate', async () => {
    await renderPage()
    expect(await screen.findByRole('heading', { name: 'Field mapping' })).toBeInTheDocument()
    expect(screen.getByText(/before any data is written/)).toBeInTheDocument()
  })

  it('shows the summary tiles the server computed', async () => {
    await renderPage()
    expect(await screen.findByText('Key ceiling')).toBeInTheDocument()
    expect(screen.getByText('1 sync key pinned')).toBeInTheDocument()
  })

  it('warns when a mapping has no property metadata recorded', async () => {
    stubApi(
      routes({
        '/wf-035/summary': {
          ...routes()['/wf-035/summary'],
          objects_without_metadata: [
            { mapping_id: 'map_1', connection_id: 'conn_1', crm_object: 'contact', provider: 'dataverse' },
          ],
        },
      }),
    )
    render(<FieldMapping />)
    expect(await screen.findByText('1 mapping(s) have no property metadata')).toBeInTheDocument()
  })
})

describe('the grid', () => {
  it('renders a clean row with its direction and transform', async () => {
    await renderPage()
    const grid = await screen.findByRole('table')
    expect(within(grid).getByText('primary_contact_email')).toBeInTheDocument()
    expect(within(grid).getByText('email.normalize')).toBeInTheDocument()
  })

  it('renders a finding message, not only its badge', async () => {
    await renderPage({
      [`${'/wf-035/connections/conn_1/mappings/map_1/validation'}`]: {
        validation: REPORT_WITH_FINDINGS,
        validated: true,
        can_activate: false,
        stale: false,
        reason: '',
      },
    })
    const grid = await screen.findByRole('table')
    // The badge alone is a handle; the sentence is what an admin can act on.
    expect(within(grid).getByText('unsupported_option')).toBeInTheDocument()
    expect(
      within(grid).getByText(/not internal option values of 'lifecyclestage'/),
    ).toBeInTheDocument()
  })

  it('offers the CRM property names, read from the metadata route', async () => {
    const { user } = await renderPage()
    await user.click(await screen.findByRole('button', { name: 'Add row' }))
    const picker = screen.getByLabelText('CRM property')
    await user.click(picker)
    expect(screen.getByRole('option', { name: /lifecyclestage \(enumeration\)/ })).toBeInTheDocument()
  })

  it('shows a picklist row the internal option values it must target', async () => {
    const { user } = await renderPage({
      '/wf-035/connections/conn_1/mappings/map_1/validation': {
        validation: REPORT_WITH_FINDINGS,
        validated: true,
        can_activate: false,
        stale: false,
        reason: '',
      },
    })
    const grid = await screen.findByRole('table')
    expect(within(grid).getByText('internal: lead, customer')).toBeInTheDocument()
  })
})

describe('the researched gate', () => {
  it('leaves Activate disabled while a validation carries errors', async () => {
    await renderPage({
      '/wf-035/connections/conn_1/mappings/map_1/validation': {
        validation: REPORT_WITH_FINDINGS,
        validated: true,
        can_activate: false,
        stale: false,
        reason: '',
      },
    })
    const activate = await screen.findByRole('button', { name: /Activate/ })
    expect(activate).toBeDisabled()
  })

  it('explains why Activate is disabled, rather than leaving a dead button', async () => {
    await renderPage({
      '/wf-035/connections/conn_1/mappings/map_1/validation': {
        validation: REPORT_WITH_FINDINGS,
        validated: true,
        can_activate: false,
        stale: false,
        reason: '',
      },
    })
    const activate = await screen.findByRole('button', { name: /Activate/ })
    expect(activate).toHaveAttribute(
      'title',
      'Activation needs a stored validation with no error findings',
    )
  })

  it('enables Activate once the stored validation is clean', async () => {
    await renderPage()
    expect(await screen.findByRole('button', { name: /Activate/ })).toBeEnabled()
  })

  it('shows the blocking flags by name', async () => {
    await renderPage({
      '/wf-035/connections/conn_1/mappings/map_1/validation': {
        validation: REPORT_WITH_FINDINGS,
        validated: true,
        can_activate: false,
        stale: false,
        reason: '',
      },
    })
    expect(
      await screen.findByText(/blocking: unknown_property, unsupported_option/),
    ).toBeInTheDocument()
  })
})

describe('the sync key', () => {
  it('shows the pinned property and how many unique keys are left', async () => {
    await renderPage()
    expect(await screen.findByRole('heading', { name: 'Sync key' })).toBeInTheDocument()
    expect(screen.getByText(/9 of 10 unique keys in use, 0 left after this one/)).toBeInTheDocument()
  })

  it('labels a sourced create request as sourced', async () => {
    await renderPage()
    expect(await screen.findByText('Sourced create request')).toBeInTheDocument()
    expect(screen.getByText('POST /crm/properties/2026-09/contacts')).toBeInTheDocument()
  })

  it('shows hasUniqueValue set to true, which is the researched instruction', async () => {
    await renderPage()
    await screen.findByText('Sourced create request')
    expect(screen.getByText(/"hasUniqueValue": true/)).toBeInTheDocument()
  })

  it('labels an un-sourced plan as one and quotes why', async () => {
    await renderPage({
      '/wf-035/connections/conn_1/mappings/map_1/sync-key': {
        mapping_id: 'map_1',
        pinned: true,
        sync_key: { properties: ['dsr_row_id'], unique: true, usage: { used: 1, needed: 1, limit: 10, remaining: 8 } },
        limit: 10,
        plan: {
          provider: 'dataverse',
          sourced: false,
          gap: 'WF-035 cites the reads, not the key-creation surface: that is WF-036.',
          crm_object: 'account',
          note: 'Alternate keys identify rows by business columns.',
          steps: [{ step: 1, name: 'EntityKeyMetadata', method: 'POST', url: '/api/data/v9.2/EntityKeyMetadata', body: {}, sourced: false }],
        },
        usage: { used: 1, needed: 1, limit: 10, remaining: 8 },
      },
    })
    expect(await screen.findByText('Un-sourced request plan')).toBeInTheDocument()
    expect(screen.getByText(/that is WF-036/)).toBeInTheDocument()
  })

  it('shows the research gap verbatim when no request can be built', async () => {
    await renderPage({
      '/wf-035/connections/conn_1/mappings/map_1/sync-key': {
        mapping_id: 'map_1',
        pinned: true,
        sync_key: { properties: ['DSR_Row_Id__c'], unique: true },
        limit: 10,
        plan: null,
        plan_error: 'this workflow will not emit a Salesforce external-ID field request',
        plan_gap: "Salesforce's Object Reference and Metadata API field pages are client-rendered and unreadable via the read path.",
        usage: {},
      },
    })
    expect(await screen.findByText('No create request for this provider')).toBeInTheDocument()
    expect(screen.getByText(/client-rendered and unreadable/)).toBeInTheDocument()
  })
})

describe('the property read', () => {
  it('names the endpoint a connector should call when nothing is recorded', async () => {
    await renderPage({
      '/wf-035/connections/conn_1/mappings/map_1': MAPPING_WITHOUT_METADATA,
      '/wf-035/connections/conn_1/properties': httpError(409, NO_METADATA),
    })
    expect(
      await screen.findByText('No property metadata recorded for this object'),
    ).toBeInTheDocument()
    expect(screen.getByText('GET /crm/properties/2026-09/{object}')).toBeInTheDocument()
  })

  it('offers no property pickers while there is nothing to pick from', async () => {
    const { user } = await renderPage({
      '/wf-035/connections/conn_1/mappings/map_1': MAPPING_WITHOUT_METADATA,
      '/wf-035/connections/conn_1/properties': httpError(409, NO_METADATA),
    })
    await user.click(await screen.findByRole('button', { name: 'Add row' }))
    expect(screen.getByRole('option', { name: 'Not mapped' })).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: /lifecyclestage/ })).not.toBeInTheDocument()
  })
})

describe('the preview', () => {
  it('shows the payload a sync cycle would send and the injected key', async () => {
    const { user } = await renderPage()
    await user.click(await screen.findByRole('button', { name: 'Evaluate' }))
    await waitFor(() => expect(screen.getByText(inPre('"ada@example.com"'))).toBeInTheDocument())
    expect(screen.getByText(/Key dsr_row_id = r1 \(injected\)/)).toBeInTheDocument()
  })

  it('reports a record the evaluator refused, per row', async () => {
    const { user } = await renderPage({
      [`${'/wf-035/connections/conn_1/mappings/map_1/preview'}`]: {
        mapping_id: 'map_1',
        crm_object: 'contacts',
        out: {},
        in: {},
        sync_key: { property: 'dsr_row_id', value: 'r1', injected: true },
        trace: [
          {
            row_id: 'row_2',
            source_field: 'buyer_stage',
            direction: 'out',
            target_property: 'lifecyclestage',
            status: 'error',
            reason: 'unsupported_option',
            detail: {},
          },
        ],
        counts: { out: 0, in: 0, ok: 0, skipped: 0, error: 1 },
        writable: false,
      },
    })
    await user.click(await screen.findByRole('button', { name: 'Evaluate' }))
    expect(await screen.findByText('This record would be refused')).toBeInTheDocument()
    expect(screen.getByText(/unsupported_option/)).toBeInTheDocument()
  })

  it('refuses to evaluate a record that is not JSON rather than guessing one', async () => {
    const { user } = await renderPage()
    const textarea = await screen.findByLabelText('The sales-room record to evaluate the mapping against')
    await user.clear(textarea)
    // `{{` is user-event's escape for a literal brace, so a single `{` has to be
    // doubled here. What lands in the box is `{not json`.
    await user.type(textarea, '{{not json')
    await user.click(screen.getByRole('button', { name: 'Evaluate' }))
    expect(await screen.findByText('That record could not be evaluated')).toBeInTheDocument()
  })
})

describe('the inferences', () => {
  it('names every judgement call and what would change it', async () => {
    await renderPage()
    expect(await screen.findByText('What the research does not decide (2)')).toBeInTheDocument()
    expect(screen.getByText('Why does this workflow not fetch?')).toBeInTheDocument()
    await waitFor(() =>
      expect(screen.getByText('Swap MetadataReader behind the properties routes.')).toBeInTheDocument(),
    )
  })
})

describe('a refusal', () => {
  it('shows the server sentence rather than a status code', async () => {
    const { user } = await renderPage({
      [`${'/wf-035/connections/conn_1/mappings/map_1/activate'}`]: httpError(422, {
        error: 'mapping_not_valid',
        detail: "this mapping's latest validation carries 2 error finding(s)",
        validated: true,
        report: REPORT_WITH_FINDINGS,
      }),
    })
    await user.click(await screen.findByRole('button', { name: /Activate/ }))
    expect(
      await screen.findByText(/latest validation carries 2 error finding/),
    ).toBeInTheDocument()
  })
})

describe('the mapping itself', () => {
  it('shows the document a validation was run against, and when', async () => {
    await renderPage()
    await waitFor(() =>
      expect(screen.getByText(/Validated against 3 properties read from/)).toBeInTheDocument(),
    )
    expect(screen.getAllByText('GET /crm/properties/2026-09/{object}').length).toBeGreaterThan(0)
  })

  it('lists a declared transform as not executable yet', async () => {
    await renderPage()
    expect(await screen.findByText('Declared transforms')).toBeInTheDocument()
    expect(
      screen.getByText(/account\.hierarchy_rollup@1 — Resolve an account's ultimate parent/),
    ).toBeInTheDocument()
    expect(screen.getByText(/not executable yet/)).toBeInTheDocument()
  })

  it('never offers a mapping whose id it has not read', async () => {
    stubApi(routes())
    render(<FieldMapping />)
    await screen.findByText('HubSpot — Northwind portal')
    // No connection chosen yet, so no mapping panel and no rows.
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    expect(MAPPING.id).toBe('map_1')
  })
})
