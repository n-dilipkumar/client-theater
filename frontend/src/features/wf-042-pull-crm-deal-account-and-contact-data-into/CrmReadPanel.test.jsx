import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import CrmReadPanel from './CrmReadPanel'
import {
  cacheAge,
  cacheNote,
  displayValue,
  isUnlabelled,
  outcomeNote,
  planSummary,
} from './api'
import descriptor from './index.jsx'
import { PANEL_WITH_CRM, ROOM, ROOM_2, VOCABULARY, routes } from './fixtures'

/**
 * Tests for the WF-042 CRM read panel page.
 *
 * Four things this page can get quietly wrong, and each is pinned here:
 *
 * 1. **A stage shows its label, and its stored value beside it.** The research's
 *    step five is "Proposal sent", not the integer 2. A panel showing only the
 *    code is the failure the feature exists to prevent, and one showing only the
 *    label cannot tell a seller what changed.
 * 2. **A room with no CRM context is a panel with a reason, not an error.** The
 *    research's own summary of this workflow is that the room still works, so a
 *    page that showed an error here would be wrong about the product.
 * 3. **An unlabelled option says so in words.** The design floor forbids
 *    conveying state by colour, and a code the reader cannot read is exactly the
 *    case that needs a word.
 * 4. **The cache state is a sentence, not a badge.** "fresh" alone is a word a
 *    reader has to interpret; the page carries the TTL and what it means.
 *
 * Queries are scoped to a **section heading**, not to the whole document. The
 * page also prints the raw API response, so every value appears twice, and an
 * unscoped `getByText` would pass on a page that rendered the panel as nothing
 * but JSON.
 *
 * Stubs are keyed on this feature's own prefix and live in `fixtures.js`, so one
 * place decides what the API looks like.
 */

let calls = []

beforeEach(() => {
  window.localStorage.clear()
  calls = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url, options = {}) => {
      calls.push({ url: String(url), method: options.method || 'GET' })
      const entry = routes(String(url), options)
      if (entry.status && entry.status >= 400) {
        return { ok: false, status: entry.status, statusText: 'Error', json: async () => entry.body }
      }
      return { ok: true, status: 200, json: async () => entry.body }
    })
  )
})

afterEach(() => {
  vi.unstubAllGlobals()
})

/** The section a heading introduces, so a query cannot match the JSON dump. */
function section(name) {
  return within(screen.getByRole('heading', { name }).closest('section'))
}

async function renderPage() {
  const user = userEvent.setup()
  render(<CrmReadPanel />)
  await screen.findByText('CRM read panel')
  return user
}

async function renderPanel() {
  window.localStorage.setItem('wf-042:last-room', ROOM.id)
  const user = await renderPage()
  await screen.findByRole('heading', { name: 'Deal panel' })
  return user
}

describe('the descriptor', () => {
  it('exports the shape the feature host discovers', () => {
    expect(descriptor.id).toBe('wf-042-pull-crm-deal-account-and-contact-data-into')
    expect(descriptor.label).toBe('CRM read panel')
    expect(descriptor.Component).toBe(CrmReadPanel)
    expect(typeof descriptor.iconPath).toBe('string')
    expect(descriptor.iconPath.length).toBeGreaterThan(0)
  })

  it('names its id after the ticket, so the two halves are findable by one name', () => {
    // The backend module is deliberately not imported here: it is Python, and a
    // browser bundle has no business reaching across into it. What is asserted
    // is the id shape, which is what a reader searches on.
    expect(descriptor.id).toMatch(/^wf-042-[a-z0-9-]+$/)
  })
})

describe('the vocabulary helpers', () => {
  it('prefers the label over the stored value', () => {
    expect(displayValue({ value: 2, label: 'Proposal sent' })).toBe('Proposal sent')
  })

  it('falls back to the stored value when nothing labelled it', () => {
    expect(displayValue({ value: 2, label: '' })).toBe('2')
    expect(displayValue({ value: null, label: '' })).toBe('-')
    expect(displayValue(null)).toBe('-')
  })

  it('marks an unlabelled value so state is never colour alone', () => {
    expect(isUnlabelled({ value: 2, label: '' })).toBe(true)
    expect(isUnlabelled({ value: 2, label: 'Proposal sent' })).toBe(false)
    expect(isUnlabelled({ value: null, label: '' })).toBe(false)
  })

  it('writes a sentence for every cache state the server can report', () => {
    for (const state of VOCABULARY.cache_states) {
      expect(cacheNote(state).length).toBeGreaterThan(0)
    }
  })

  it('writes a sentence for every read outcome', () => {
    for (const outcome of VOCABULARY.read_outcomes) {
      expect(outcomeNote(outcome).length).toBeGreaterThan(0)
    }
  })

  it('summarises a read plan the way each vendor spells it', () => {
    expect(planSummary({ query: { q: 'SELECT Id FROM Opportunity' } })).toBe(
      'SELECT Id FROM Opportunity'
    )
    expect(planSummary({ query: { $select: 'id,name', $top: '200' } })).toContain('$select=id,name')
    expect(planSummary({ method: 'POST', path: '/crm/v3/objects/deals/search', body: {} })).toBe(
      'POST /crm/v3/objects/deals/search'
    )
  })

  it('describes a cache age in words rather than in seconds', () => {
    expect(cacheAge({ age_seconds: 12 })).toBe('12s ago')
    expect(cacheAge({ age_seconds: 300 })).toBe('5m ago')
    expect(cacheAge({ age_seconds: 7200 })).toBe('2h ago')
    expect(cacheAge({ age_seconds: null })).toBe('')
  })
})

describe('the panel', () => {
  it('shows the five fields the user flow names', async () => {
    await renderPanel()
    const panel = section('Deal panel')
    expect(panel.getByText('Northwind rollout')).toBeTruthy()
    expect(panel.getByText('48000')).toBeTruthy()
    expect(panel.getByText('Dana Okafor')).toBeTruthy()
    expect(panel.getByText('VP Operations')).toBeTruthy()
    expect(panel.getByText('Northwind Traders')).toBeTruthy()
    expect(panel.getByText('Manufacturing')).toBeTruthy()
  })

  it('shows the stage label and the stored value beside it', async () => {
    await renderPanel()
    const panel = section('Deal panel')
    expect(panel.getByText('In negotiation')).toBeTruthy()
    expect(panel.getByText('Negotiation')).toBeTruthy()
  })

  it('says the room has no CRM context instead of showing an error', async () => {
    window.localStorage.setItem('wf-042:last-room', ROOM_2.id)
    await renderPage()
    await screen.findByText('No CRM context for this buyer')
    expect(screen.getByText(/without CRM context/)).toBeTruthy()
  })

  it('offers to register a buyer from the no-context state', async () => {
    window.localStorage.setItem('wf-042:last-room', ROOM_2.id)
    const user = await renderPage()
    await user.click(await screen.findByText('Register this buyer'))
    expect(await screen.findByText('Register a buyer identity')).toBeTruthy()
    expect(screen.getByLabelText('Buyer email')).toBeTruthy()
    expect(screen.getByLabelText('Owner id')).toBeTruthy()
  })

  it('names the cache state in words, not only in a badge', async () => {
    await renderPanel()
    const shown = section('Read-through cache')
    expect(shown.getByText('Within its TTL. No vendor call was made.')).toBeTruthy()
    expect(shown.getByText('fresh')).toBeTruthy()
    expect(shown.getByText('300s')).toBeTruthy()
    expect(shown.getByText('4s ago')).toBeTruthy()
  })

  it('says which source supplied the display labels', async () => {
    await renderPanel()
    expect(section('Deal panel').getByText('labels from room option sets')).toBeTruthy()
  })

  it('shows the read set the field map produced', async () => {
    await renderPanel()
    const readSet = section('Read set, derived from the field map')
    expect(readSet.getByText('StageName')).toBeTruthy()
    // The column a deployment added to its write map reaches this table, which
    // is the research's extensibility claim rendered rather than asserted.
    expect(readSet.getByText('Region__c')).toBeTruthy()
    expect(readSet.getByText('deal_region')).toBeTruthy()
  })

  it('shows the exact query each read issued', async () => {
    await renderPanel()
    const log = section('Read query log')
    expect(log.getByText(/SELECT Id, Name, StageName/)).toBeTruthy()
    expect(log.getByText('Opportunity')).toBeTruthy()
  })

  it('marks an option the vendor could not label and the room has no map for', async () => {
    await renderPanel()
    expect(section('Deal panel').getAllByText('unlabelled').length).toBeGreaterThan(0)
  })

  it('reports every vendor limit it publishes', async () => {
    await renderPanel()
    const published = section('Vocabulary')
    expect(published.getByText('salesforce')).toBeTruthy()
    expect(published.getByText('dataverse')).toBeTruthy()
    expect(published.getByText('hubspot')).toBeTruthy()
    expect(published.getByText('2000')).toBeTruthy()
    expect(published.getByText('5000')).toBeTruthy()
    expect(published.getByText('3000')).toBeTruthy()
  })

  it('says which vendor can annotate a label and which cannot', async () => {
    await renderPanel()
    const published = section('Vocabulary')
    expect(published.getByText('annotates labels')).toBeTruthy()
    expect(published.getAllByText('no label API').length).toBe(2)
  })

  it('shows an empty state rather than a blank grid when nothing has been read', async () => {
    window.localStorage.setItem('wf-042:last-room', 'room-quiet')
    await renderPage()
    await screen.findByRole('heading', { name: 'Read query log' })
    expect(
      within(screen.getByRole('heading', { name: 'Read query log' }).closest('section')).getByText(
        'No read has been issued for this room yet.'
      )
    ).toBeTruthy()
  })

  it('asks for a room before it reads anything', async () => {
    await renderPage()
    expect(
      await screen.findByText(/The deal panel is scoped to a room/)
    ).toBeTruthy()
    expect(calls.filter((call) => call.url.includes('/panel'))).toHaveLength(0)
  })

  it('forces a read on demand and reloads afterwards', async () => {
    const user = await renderPanel()
    await user.click(screen.getByText('Force read'))
    await waitFor(() =>
      expect(
        calls.some((call) => call.url.includes('/panel/pull') && call.method === 'POST')
      ).toBe(true)
    )
    expect(calls.filter((call) => call.url.includes('/panel?')).length).toBeGreaterThan(1)
  })

  it('disables the forced read when there is no identity to read for', async () => {
    window.localStorage.setItem('wf-042:last-room', ROOM_2.id)
    await renderPage()
    await screen.findByText('No CRM context for this buyer')
    expect(screen.getByText('Force read').closest('button')?.disabled).toBe(true)
  })

  it('reports an API failure with a retry rather than an empty page', async () => {
    window.localStorage.setItem('wf-042:last-room', 'room-broken')
    await renderPage()
    expect(await screen.findByText('Could not load data')).toBeTruthy()
    expect(screen.getByText('Retry')).toBeTruthy()
  })

  it('counts the room queries it issued and reads from the cache', async () => {
    await renderPanel()
    // The panel endpoint is asked with refresh=true on load, which is the
    // scheduler's door. A page that only ever read from cache would never
    // produce the first of these.
    expect(calls.some((call) => call.url.includes('refresh=true'))).toBe(true)
    expect(calls.some((call) => call.url.includes('/cache'))).toBe(true)
    expect(calls.some((call) => call.url.includes('/queries'))).toBe(true)
  })
})

describe('the fixture contract', () => {
  it('describes a panel the server can actually produce', () => {
    // The fixture is the researched shape verbatim. A drift between it and the
    // server is a drift between this page and the API, and this asserts the
    // fields the page reads are the fields the fixture carries.
    for (const key of ['deal', 'contact', 'account']) {
      expect(PANEL_WITH_CRM.panel[key]).toHaveProperty('found')
      expect(PANEL_WITH_CRM.panel[key]).toHaveProperty('external_id')
    }
    expect(PANEL_WITH_CRM.panel.deal.stage).toHaveProperty('value')
    expect(PANEL_WITH_CRM.panel.deal.stage).toHaveProperty('label')
    expect(PANEL_WITH_CRM.cache).toHaveProperty('state')
    expect(PANEL_WITH_CRM).toHaveProperty('outcome')
  })
})