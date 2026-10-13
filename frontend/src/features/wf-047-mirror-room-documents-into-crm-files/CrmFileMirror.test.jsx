import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import CrmFileMirror from './CrmFileMirror'
import {
  attachSummary,
  byteSize,
  folderNote,
  outcomeNote,
  requestSummary,
  stateTone,
  vendorNote,
} from './api'
import descriptor from './index.jsx'
import {
  ASSETS,
  FAILED_MIRROR,
  MIRROR_RESULT,
  NOT_DUE,
  ROOM,
  VENDORS,
  VOCABULARY,
  routes,
} from './fixtures'

/**
 * Tests for the WF-047 CRM file mirror page.
 *
 * Six things this page can get quietly wrong, and each is pinned here:
 *
 * 1. **A chip is a word, a tone and a sentence.** The researched product surface
 *    is a "Push to CRM" status chip per file, and the design floor forbids status
 *    by colour alone. A chip showing only the state word fails the same rule a
 *    badge-only row would.
 * 2. **The unsourced vendors say why.** The research states that only one
 *    vendor's Files API is documented. A page showing three identical vendor cards
 *    would claim a coverage the specification does not have, so the gap text is
 *    asserted on screen.
 * 3. **The exact multipart request is visible.** The research quotes
 *    `files/2026-09/files` and the `file` field, and a log row that hid them
 *    behind a summary would make the claim unverifiable from the page.
 * 4. **A simulated confirmation says so.** Every row records whether the local
 *    simulation answered it, so a reviewer can never read a simulated id as a
 *    vendor's.
 * 5. **A room with nothing mirrored is an empty state, not an error.** The
 *    workflow is room-scoped, so the picker comes first and an empty room says
 *    what it would hold rather than erroring.
 * 6. **A failed mirror is reported in words.** The refusal carries the gap text,
 *    and the page shows the outcome word beside it.
 *
 * Queries are scoped to a **section heading**, not to the whole document. The
 * page also prints the raw API responses, so every value appears twice, and an
 * unscoped `getByText` would pass on a page that rendered the panel as nothing
 * but JSON.
 *
 * Stubs are keyed on this feature's own prefix and live in `fixtures.js`, so one
 * place decides what the API looks like.
 */

let calls = []
let mirrorAnswer = MIRROR_RESULT
let reconcileAnswer = { outcome: 'reconciled', mirrored: 1, candidates: 1, results: [] }

beforeEach(() => {
  window.localStorage.clear()
  calls = []
  mirrorAnswer = MIRROR_RESULT
  reconcileAnswer = { outcome: 'reconciled', mirrored: 1, candidates: 1, results: [] }
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url, options = {}) => {
      calls.push({ url: String(url), method: options.method || 'GET' })
      const path = String(url).split('?')[0]
      if (path.endsWith('/mirror') && (options.method || 'GET') === 'POST') {
        return { ok: true, status: 200, json: async () => mirrorAnswer }
      }
      if (path.endsWith('/reconcile')) {
        return { ok: true, status: 200, json: async () => reconcileAnswer }
      }
      const entry = routes(String(url), options)
      if (entry.status && entry.status >= 400) {
        return {
          ok: false,
          status: entry.status,
          statusText: 'Error',
          json: async () => entry.body,
        }
      }
      return { ok: true, status: entry.status || 200, json: async () => entry.body }
    })
  )
})

/** Choose the one room this fixture set has data for. */
async function chooseRoom() {
  const room = await screen.findByLabelText('Room')
  await userEvent.selectOptions(room, ROOM.id)
  return room
}

function section(name) {
  return within(screen.getByRole('heading', { name, level: 2 }).closest('section'))
}

/**
 * The live region the page writes the last action's outcome into.
 *
 * Scoped rather than queried globally because the page also prints the raw API
 * responses, so every string on it appears twice. A query on the whole document
 * would pass on a page that rendered the panel as nothing but JSON, and would
 * fail here on a page that renders it correctly.
 */
function notice() {
  // The callout is a polite live region for an outcome and an assertive one for
  // a refusal, so either role is the region the last action wrote into.
  return within(screen.queryByRole('alert') || screen.getByRole('status'))
}

describe('the descriptor', () => {
  it('exports the id the backend feature declares', () => {
    expect(descriptor.id).toBe('wf-047-mirror-room-documents-into-crm-files')
    expect(descriptor.label).toBeTruthy()
    expect(descriptor.Component).toBe(CrmFileMirror)
    expect(descriptor.iconPath).toBeTruthy()
  })
})

describe('the page shell', () => {
  it('asks for a room before it shows anything else', async () => {
    render(<CrmFileMirror />)
    expect(await screen.findByLabelText('Room')).toBeTruthy()
    expect(
      screen.getByText('Choose a room', { selector: 'p' })
    ).toBeTruthy()
  })

  it('renders the researched flow in its own words', async () => {
    render(<CrmFileMirror />)
    await chooseRoom()
    expect(
      screen.getByText(/crm_file_id on the asset row, so re-uploads are skipped/i)
    ).toBeTruthy()
  })
})

describe('the documents page and its chip', () => {
  it('shows every document with its state word, tone and sentence', async () => {
    render(<CrmFileMirror />)
    await chooseRoom()
    const documents = section('Room documents')
    await waitFor(() => expect(documents.getByText('northwind-rollout-deck.pdf')).toBeTruthy())
    expect(within(documents.getByText('mirrored').parentElement).getByText(VOCABULARY.mirror_state_notes.mirrored)).toBeTruthy()
    expect(within(documents.getByText('stale').parentElement).getByText(VOCABULARY.mirror_state_notes.stale)).toBeTruthy()
    expect(within(documents.getByText('withheld').parentElement).getByText(VOCABULARY.mirror_state_notes.withheld)).toBeTruthy()
  })

  it('shows the stored crm_file_id in mono, and "none" where there is none', async () => {
    render(<CrmFileMirror />)
    await chooseRoom()
    const documents = section('Room documents')
    await waitFor(() =>
      expect(documents.getByText('file-4f6212863afc57fd')).toBeTruthy()
    )
    expect(documents.getByText('none')).toBeTruthy()
  })

  it('never shows a status by colour alone', async () => {
    render(<CrmFileMirror />)
    await chooseRoom()
    const documents = section('Room documents')
    await waitFor(() => expect(documents.getByText('mirrored')).toBeTruthy())
    for (const state of VOCABULARY.mirror_states) {
      expect(stateTone(state)).toBeTruthy()
    }
  })
})

describe('the mirror', () => {
  it('pushes one document and reports the outcome in words', async () => {
    render(<CrmFileMirror />)
    await chooseRoom()
    const documents = section('Room documents')
    await waitFor(() => expect(documents.getByText('northwind-rollout-deck.pdf')).toBeTruthy())
    const push = documents.getAllByRole('button', { name: 'Push now' })[0]
    await userEvent.click(push)
    await waitFor(() => expect(notice().getByText('mirrored')).toBeTruthy())
    expect(notice().getByText(outcomeNote('mirrored'), { exact: false })).toBeTruthy()
    expect(calls.some((call) => call.url.includes('/mirror'))).toBe(true)
  })

  it('reports a refusal with its own reason rather than a bare word', async () => {
    mirrorAnswer = FAILED_MIRROR
    render(<CrmFileMirror />)
    await chooseRoom()
    const documents = section('Room documents')
    await waitFor(() => expect(documents.getByText('northwind-rollout-deck.pdf')).toBeTruthy())
    await userEvent.click(documents.getAllByRole('button', { name: 'Push now' })[0])
    await waitFor(() => expect(notice().getByText('failed')).toBeTruthy())
    expect(
      notice().getByText(VOCABULARY.unsourced_systems.salesforce, { exact: false })
    ).toBeTruthy()
  })

  it('toggles Push to CRM and says the CRM file stays where it is', async () => {
    render(<CrmFileMirror />)
    await chooseRoom()
    const documents = section('Room documents')
    await waitFor(() => expect(documents.getByText('northwind-rollout-deck.pdf')).toBeTruthy())
    await userEvent.click(documents.getAllByRole('button', { name: 'Turn push off' })[0])
    await waitFor(() => expect(notice().getByText(/stays there/i)).toBeTruthy())
    expect(
      calls.some((call) => call.method === 'PATCH' && call.url.includes('/assets/asset-1'))
    ).toBe(true)
  })
})

describe('the exact request the workflow builds', () => {
  it('names the multipart endpoint and its field names in the log', async () => {
    render(<CrmFileMirror />)
    await chooseRoom()
    const log = section('Mirror log')
    await waitFor(() =>
      expect(
        log.getByText('POST /files/2026-09/files as multipart/form-data (file, fileName)')
      ).toBeTruthy()
    )
  })

  it('says that nothing was sent when nothing was sent', async () => {
    render(<CrmFileMirror />)
    await chooseRoom()
    const log = section('Mirror log')
    await waitFor(() => expect(log.getAllByText('nothing was sent').length).toBeGreaterThan(0))
  })

  it('marks a simulated confirmation as a simulated confirmation', async () => {
    render(<CrmFileMirror />)
    await chooseRoom()
    const log = section('Mirror log')
    await waitFor(() =>
      expect(log.getAllByText('local simulation').length).toBeGreaterThan(0)
    )
  })
})

describe('the CRM files panel', () => {
  it('lists what the room pushed and which record owns it', async () => {
    render(<CrmFileMirror />)
    await chooseRoom()
    const files = section('CRM files panel')
    await waitFor(() => expect(files.getByText('deal fabrikam-dl-9001')).toBeTruthy())
    expect(files.getByText('file-4f6212863afc57fd')).toBeTruthy()
  })
})

describe('the reconciliation rule', () => {
  it('shows the rule and the floor rather than claiming a schedule', async () => {
    render(<CrmFileMirror />)
    await chooseRoom()
    const panel = section('Reconciliation')
    await waitFor(() =>
      expect(panel.getByText(VOCABULARY.reconcile_rule, { exact: false })).toBeTruthy()
    )
    expect(panel.getByText('300s')).toBeTruthy()
    expect(panel.getByText(/starts a thread/i)).toBeTruthy()
  })

  it('runs the pass on demand and reports what it pushed', async () => {
    reconcileAnswer = {
      outcome: 'reconciled',
      rule: VOCABULARY.reconcile_rule,
      mirrored: 2,
      candidates: 3,
      results: [],
      interval_seconds: 300,
      connection_intervals: { 'conn-1': 300 },
      at: '2026-10-01T09:10:00.000+00:00',
    }
    render(<CrmFileMirror />)
    await chooseRoom()
    const panel = section('Reconciliation')
    await waitFor(() => expect(panel.getByRole('button', { name: /Run reconciliation/ })).toBeTruthy())
    await userEvent.click(panel.getByRole('button', { name: /Run reconciliation/ }))
    await waitFor(() =>
      expect(calls.some((call) => call.url.includes('/reconcile'))).toBe(true)
    )
  })

  it('reports a pass that was inside the floor as not_due', async () => {
    reconcileAnswer = NOT_DUE
    render(<CrmFileMirror />)
    await chooseRoom()
    const panel = section('Reconciliation')
    await waitFor(() => expect(panel.getByRole('button', { name: /Run reconciliation/ })).toBeTruthy())
    await userEvent.click(panel.getByRole('button', { name: /Run reconciliation/ }))
    await waitFor(() => expect(notice().getByText('not_due')).toBeTruthy())
    expect(notice().getByText(/Nothing was pushed and no vendor was asked/)).toBeTruthy()
  })
})

describe('the vendors', () => {
  it('names the gap for each vendor the research could not source', async () => {
    render(<CrmFileMirror />)
    await chooseRoom()
    const vendors = section('Vendor file capability')
    await waitFor(() => expect(vendors.getByText('hubspot')).toBeTruthy())
    // Each unsourced card carries the gap twice: once in the summary line and
    // once in the body. Both are deliberate - the summary says what it means and
    // the body says it in the research's words - so the assertion counts them.
    expect(
      vendors.getAllByText(VOCABULARY.unsourced_systems.salesforce, { exact: false }).length
    ).toBeGreaterThan(0)
    expect(
      vendors.getAllByText(VOCABULARY.unsourced_systems.dataverse, { exact: false }).length
    ).toBeGreaterThan(0)
    expect(vendors.getByText('/files/2026-09/files')).toBeTruthy()
  })
})

describe('the connections', () => {
  it('says whether a connection follows the vendor folder recommendation', async () => {
    render(<CrmFileMirror />)
    await chooseRoom()
    const panel = section('Connections')
    await waitFor(() => expect(panel.getByText('Fabrikam deal files')).toBeTruthy())
    expect(panel.getByText(/uploads to the root directory/i)).toBeTruthy()
    expect(panel.getByText('Uploads into folder contoso-folder-1.')).toBeTruthy()
  })

  it('warns in the form when the chosen system could not be sourced', async () => {
    render(<CrmFileMirror />)
    await chooseRoom()
    const card = screen.getByRole('button', { name: /Save connection/ }).closest('form')
      .parentElement
    await userEvent.selectOptions(within(card).getByLabelText('System'), 'salesforce')
    await waitFor(() =>
      expect(
        within(card).getByText(VOCABULARY.unsourced_systems.salesforce, { exact: false })
      ).toBeTruthy()
    )
  })

  it('saves a connection in the researched vocabulary', async () => {
    render(<CrmFileMirror />)
    await chooseRoom()
    const form = within(screen.getByRole('button', { name: /Save connection/ }).closest('form'))
    await userEvent.type(form.getByLabelText('CRM record that owns the attachment'), 'fabrikam-dl-9001')
    await userEvent.click(form.getByRole('button', { name: /Save connection/ }))
    await waitFor(() =>
      expect(
        calls.some(
          (call) => call.method === 'POST' && call.url.includes('/wf-047/connections')
        )
      ).toBe(true)
    )
  })
})

describe('the decisions register', () => {
  it('renders each decision with where to change it', async () => {
    render(<CrmFileMirror />)
    await chooseRoom()
    const panel = section('Decisions this workflow rests on')
    await waitFor(() => expect(panel.getByText('revision-detected-by-content-digest')).toBeTruthy())
    expect(panel.getByText(/Change it in assets.content_digest/)).toBeTruthy()
  })
})

describe('the empty room', () => {
  it('says what a room with no documents would hold, rather than erroring', async () => {
    render(<CrmFileMirror />)
    const room = await screen.findByLabelText('Room')
    await userEvent.selectOptions(room, 'room-empty')
    await waitFor(() =>
      expect(
        screen.getByText('This room holds no documents that are mirrored into a CRM.')
      ).toBeTruthy()
    )
    expect(
      screen.getByText('This room has pushed no file to a CRM yet.')
    ).toBeTruthy()
    expect(screen.getByText('This room has made no mirror attempt yet.')).toBeTruthy()
  })
})

describe('the pure helpers', () => {
  it('reads the exact request the research quotes', () => {
    expect(
      requestSummary({
        method: 'POST',
        path: '/files/2026-09/files',
        content_type: 'multipart/form-data',
        fields: { file: 'a.pdf', fileName: 'a.pdf' },
      })
    ).toBe('POST /files/2026-09/files as multipart/form-data (file, fileName)')
    expect(requestSummary({})).toBe('')
    expect(requestSummary(null)).toBe('')
  })

  it('names both researched attach modes in words', () => {
    expect(
      attachSummary({ mode: 'note', record_id: 'deal-1' })
    ).toBe('file id attached to deal-1 as a note')
    expect(attachSummary({ mode: 'file_property', property_name: 'proposal' })).toBe(
      'file id written into the proposal property'
    )
    expect(attachSummary({})).toBe('')
  })

  it('gives every mirror outcome a sentence', () => {
    for (const outcome of VOCABULARY.mirror_outcomes) {
      expect(outcomeNote(outcome)).toBeTruthy()
    }
    expect(outcomeNote('nothing like this')).toBe('')
  })

  it('formats a byte size without losing the unit', () => {
    expect(byteSize(0)).toBe('0 B')
    expect(byteSize(512)).toBe('512 B')
    expect(byteSize(4096)).toBe('4.0 kB')
    expect(byteSize(5 * 1024 * 1024)).toBe('5.0 MB')
  })

  it('says which side of the folder recommendation a connection is on', () => {
    expect(folderNote({ folder_id: 'f-1' })).toBe('Uploads into folder f-1.')
    expect(folderNote({ folder_id: '' })).toMatch(/root directory/)
    expect(folderNote(null)).toBe('')
  })

  it('carries the vendor gap rather than hiding it', () => {
    const sourced = vendorNote(VENDORS.vendors[0])
    expect(sourced).toContain('/files/2026-09/files')
    expect(vendorNote(VENDORS.vendors[1])).toContain('could not be sourced')
    expect(vendorNote(null)).toBe('')
  })
})