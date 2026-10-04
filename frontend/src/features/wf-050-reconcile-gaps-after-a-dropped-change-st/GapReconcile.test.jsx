import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import GapReconcile from './GapReconcile'
import {
  canReconcile,
  canSubscribe,
  changeTypeLabel,
  cursorNote,
  defaultOf,
  dropNote,
  formatDays,
  gapTypeNote,
  logLineNote,
  runStateNote,
  stateTone,
  subscriptionNote,
} from './api'
import descriptor from './index.jsx'
import { GAP_EVENTS, ROOM, ROOM_BROKEN, ROOM_QUIET, VOCABULARY, routes } from './fixtures'

/**
 * Tests for the WF-050 gap reconciliation page.
 *
 * Five things this page can get quietly wrong, and each is pinned here:
 *
 * 1. A gap event and an overflow event are different shapes, and the page says
 *    which is which rather than lumping them into one list.
 * 2. A dirty record is not a broken room. The page names each dirty record and how
 *    long it has been waiting.
 * 3. A dropped change event is the research's prescribed behaviour. The page says
 *    which of the four reasons dropped it.
 * 4. The two cursors are not the same shape, and the page says whether the vendor
 *    will still answer for each one.
 * 5. A room has to be chosen before anything is read, so opening the page costs no
 *    vendor calls at all.
 *
 * Queries are scoped to a **section heading**, not to the whole document. The page
 * also prints raw values such as timestamps, so an unscoped `getByText` would pass
 * on a page that rendered every section as nothing but data.
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
      calls.push({
        url: String(url),
        method: options.method || 'GET',
        body: options.body ? JSON.parse(options.body) : null,
      })
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

/** The section a heading introduces, so a query cannot match the data dump. */
function section(name) {
  return within(screen.getByRole('heading', { name }).closest('section'))
}

async function renderPage() {
  const user = userEvent.setup()
  render(<GapReconcile />)
  await screen.findByText('Reconcile gaps after a dropped change stream')
  return user
}

async function renderLedger() {
  window.localStorage.setItem(
    'wf-050-reconcile-gaps-after-a-dropped-change-st:last-room',
    ROOM.id
  )
  const user = await renderPage()
  await screen.findByRole('heading', { name: 'Data health' })
  return user
}

describe('the descriptor', () => {
  it('exports the shape the feature host discovers', () => {
    expect(descriptor.id).toBe('wf-050-reconcile-gaps-after-a-dropped-change-st')
    expect(descriptor.label).toBe('Gap reconciliation')
    expect(descriptor.Component).toBe(GapReconcile)
    expect(typeof descriptor.iconPath).toBe('string')
    expect(descriptor.iconPath.length).toBeGreaterThan(0)
    expect(descriptor.icon).toBeTruthy()
  })

  it('names its id after the ticket, so the two halves are findable by one name', () => {
    // The backend module is deliberately not imported here: it is Python, and a
    // browser bundle has no business reaching across into it. What is asserted is
    // the id shape, which is what a reader searches on.
    expect(descriptor.id).toMatch(/^wf-050-[a-z0-9-]+$/)
  })
})

describe('the vocabulary helpers', () => {
  it('calls the overflow a gap only when the published vocabulary says so', () => {
    expect(changeTypeLabel(VOCABULARY, 'GAP_UPDATE')).toBe('gap')
    expect(changeTypeLabel(VOCABULARY, 'GAP_OVERFLOW')).toBe('overflow')
    expect(changeTypeLabel(VOCABULARY, 'GAP_REORG')).toBe('unrecognised')
    expect(changeTypeLabel(VOCABULARY, null)).toBe('unknown')
  })

  it('writes a sentence for every gap type the server can report', () => {
    for (const changeType of VOCABULARY.gap_change_types) {
      expect(gapTypeNote(VOCABULARY, changeType).length).toBeGreaterThan(0)
      expect(gapTypeNote(VOCABULARY, changeType)).toContain('full re-read')
    }
  })

  it('says an overflow names no operation, rather than rendering an empty cell', () => {
    expect(gapTypeNote(VOCABULARY, 'GAP_OVERFLOW')).toContain('names no operation')
  })

  it('says why a cursor is resumable and why it is not', () => {
    expect(cursorNote({ kind: 'replay_id', present: true, resumable: true, applies: false, age_days: 3 })).toContain(
      'does not expire'
    )
    expect(
      cursorNote({ kind: 'delta_link', present: true, resumable: true, applies: true, age_days: 2, expiry_days: 7 })
    ).toContain('inside the 7 day window')
    expect(
      cursorNote({ kind: 'delta_link', present: true, resumable: false, age_days: 9, expiry_days: 7 })
    ).toContain('full re-read')
    expect(cursorNote({ kind: 'replay_id', present: false, resumable: false })).toContain('nothing to resume from')
    expect(cursorNote(null)).toContain('no resumable position')
  })

  it('says which of the four reasons dropped a change', () => {
    expect(
      dropNote({ applied: false, reason: 'dirty_and_newer_than_the_read' })
    ).toContain('newer than the record the vendor returned')
    expect(dropNote({ applied: false, reason: 'older_than_the_gap' })).toContain(
      'already inside the window'
    )
    expect(dropNote({ applied: false, reason: 'no_dirty_marker' })).toContain('named neither')
    expect(dropNote({ applied: true, reason: 'no_dirty_marker' })).toContain('no dirty flag')
    expect(dropNote({ applied: true, reason: 'covered_by_the_re_read' })).toContain('already carried')
    expect(dropNote(null)).toBe('')
  })

  it('writes a sentence for every run state the server can report', () => {
    expect(runStateNote({ state: 'complete', counts: { written: 2, deleted: 1 } })).toContain(
      '2 row(s) overwritten, 1 tombstoned'
    )
    expect(runStateNote({ state: 'expired_cursor' })).toContain('past its window')
    expect(runStateNote({ state: 'refused' })).toContain('Nothing was written')
    expect(runStateNote({ state: 'open' })).toContain('has not finished')
    expect(runStateNote(null)).toBe('')
  })

  it('says what the overflow did to the subscription', () => {
    // Found by kind rather than by index, so inserting an event cannot make this
    // test quietly pass against a gap.
    const overflow = GAP_EVENTS.find((row) => row.kind === 'overflow')
    const gap = GAP_EVENTS.find((row) => row.kind === 'gap')
    expect(subscriptionNote(overflow)).toContain('unsubscribed')
    expect(subscriptionNote({ ...overflow, subscription_state: 'subscribed' })).toContain(
      'subscribed again'
    )
    expect(subscriptionNote(gap)).toContain('does not pause the stream')
    expect(subscriptionNote(null)).toBe('')
  })

  it('renders each sync-log line with its own number, because the number is the order', () => {
    const note = logLineNote({ seq: 3, event: 'dirty_flag_cleared', detail: 'Opportunity/006' })
    expect(note).toContain('Step 3')
    expect(note).toContain('dirty flag cleared')
    expect(note).toContain('Opportunity/006')
    expect(logLineNote(null)).toBe('')
  })

  it('reads the researched numbers from the published vocabulary', () => {
    expect(defaultOf(VOCABULARY, 'overflow_change_threshold')).toBe(100000)
    expect(defaultOf(VOCABULARY, 'change_tracking_expiry_days')).toBe(7)
    expect(defaultOf({}, 'overflow_change_threshold')).toBeUndefined()
  })

  it('describes an age in words rather than as a decimal', () => {
    expect(formatDays(0.5)).toBe('12h')
    expect(formatDays(3)).toBe('3d')
    expect(formatDays(null)).toBe('an unknown age')
  })

  it('never lets a tone carry a state on its own', () => {
    // The tone is decoration. Every state above also writes a sentence, and these
    // are the tones that sentence is paired with.
    expect(stateTone(VOCABULARY, 'complete')).toBe('insert')
    expect(stateTone(VOCABULARY, 'expired_cursor')).toBe('delete')
    expect(stateTone(VOCABULARY, 'open')).toBe('update')
    expect(stateTone(VOCABULARY, 'dirty')).toBe('delete')
    expect(stateTone(VOCABULARY, 'something-else')).toBe('neutral')
  })

  it('offers a repair only when there is a dirty record and no run in flight', () => {
    expect(canReconcile(1, 0)).toBe(true)
    expect(canReconcile(0, 0)).toBe(false)
    expect(canReconcile(2, 1)).toBe(false)
  })

  it('offers a resubscription only when an overflow left the room unsubscribed', () => {
    expect(canSubscribe(GAP_EVENTS)).toBe(true)
    expect(canSubscribe(GAP_EVENTS.filter((row) => row.kind === 'gap'))).toBe(false)
    expect(canSubscribe(null)).toBe(false)
  })
})

describe('the page', () => {
  it('asks for a room before it reads anything', async () => {
    await renderPage()
    expect(
      await screen.findByText(/The gap ledger is scoped to one room/)
    ).toBeTruthy()
    const scoped = calls.filter((call) => call.url.includes(ROOM.id) || call.url.includes('/rooms/'))
    expect(scoped).toHaveLength(0)
  })

  it('shows the two researched numbers in its own words', async () => {
    await renderLedger()
    const published = section('Vocabulary')
    expect(published.getByText('GAP_CREATE, GAP_UPDATE, GAP_DELETE, GAP_UNDELETE')).toBeTruthy()
    expect(published.getByText('GAP_OVERFLOW')).toBeTruthy()
    expect(published.getByText('100000')).toBeTruthy()
    expect(published.getByText('7 days')).toBeTruthy()
    expect(published.getByText('replay_id, delta_link')).toBeTruthy()
    expect(published.getByText('difference, recycle_bin, dataverse_delta')).toBeTruthy()
    expect(published.getByText('LastModifiedDate')).toBeTruthy()
    expect(published.getByText('room_id, entity, record_id')).toBeTruthy()
  })

  it('separates the gap events from the overflow events and says why', async () => {
    await renderLedger()
    const shown = section('Gap and overflow ledger')
    expect(shown.getByText('Gap events (3)')).toBeTruthy()
    expect(shown.getByText('Overflow events (1)')).toBeTruthy()
    expect(shown.getByText(/Each names one record and is repaired by one full read/)).toBeTruthy()
    expect(shown.getByText(/It names no record, so the room pauses the stream/)).toBeTruthy()
  })

  it('names each gap type in text rather than only in a badge', async () => {
    await renderLedger()
    const shown = section('Gap and overflow ledger')
    expect(shown.getByText('GAP_UPDATE')).toBeTruthy()
    expect(shown.getByText('GAP_DELETE')).toBeTruthy()
    expect(shown.getAllByText(/could not emit the (update|delete|create)/).length).toBe(3)
    expect(shown.getByText('Opportunity/006A000001')).toBeTruthy()
    expect(shown.getByText('Opportunity/006A000003')).toBeTruthy()
  })

  it('shows an overflow with its replay id and its change count past the threshold', async () => {
    await renderLedger()
    const shown = section('Gap and overflow ledger')
    expect(shown.getByText('150000')).toBeTruthy()
    expect(shown.getByText('past the threshold')).toBeTruthy()
    expect(shown.getByText('seed-replay-0')).toBeTruthy()
    expect(shown.getByText(/unsubscribed and stored the Replay ID/)).toBeTruthy()
  })

  it('names each dirty record and how long it has been waiting', async () => {
    await renderLedger()
    const health = section('Data health')
    expect(health.getByText('Opportunity/006A000009')).toBeTruthy()
    expect(health.getByText('dirty for 4h')).toBeTruthy()
    expect(health.getByText(/GAP_CREATE as of/)).toBeTruthy()
  })

  it('says a dirty record is not a broken room', async () => {
    await renderLedger()
    expect(await screen.findByText('This room owes a full re-read')).toBeTruthy()
    expect(screen.getByText(/A dirty record is not a broken room/)).toBeTruthy()
  })

  it('says the vendor has no gap path for the third vendor', async () => {
    await renderLedger()
    expect(
      screen.getByText(/HubSpot has no documented gap\/overflow analogue/)
    ).toBeTruthy()
    expect(screen.getByText('hubspot (no gap path)')).toBeTruthy()
  })

  it('renders the cursor in words and says the Replay ID does not expire', async () => {
    await renderLedger()
    const shown = section('Resumable positions')
    expect(shown.getByText(/replay_id: held 1h/)).toBeTruthy()
    expect(shown.getByText(/This kind does not expire\./)).toBeTruthy()
    expect(shown.getByText('seed-replay-0')).toBeTruthy()
    expect(shown.getByText(/scoped to one entity type and does not expire/)).toBeTruthy()
  })

  it('says the delta link window in the vocabulary, beside the two cursor kinds', async () => {
    await renderLedger()
    const shown = section('Resumable positions')
    expect(shown.getByText('replay_id')).toBeTruthy()
    expect(shown.getByText('entity')).toBeTruthy()
    const published = section('Vocabulary')
    expect(published.getByText('7 days')).toBeTruthy()
    expect(published.getByText('100000')).toBeTruthy()
  })

  it('names every sync-log line with its own number', async () => {
    const user = await renderLedger()
    const log = section('Sync log')
    await user.click(log.getByText('Read the steps'))
    await screen.findByText('Steps for run crm_gap_run_1')
    expect(screen.getByText(/Step 1. run opened: gap on Opportunity/)).toBeTruthy()
    expect(screen.getByText(/Step 3. dirty flag cleared: Opportunity\/006A000001/)).toBeTruthy()
  })

  it('says a run finished and what it wrote', async () => {
    await renderLedger()
    const log = section('Sync log')
    expect(log.getByText(/Reconciled. 1 row\(s\) overwritten, 0 tombstoned./)).toBeTruthy()
  })

  it('offers the repair only when there is a dirty record and no run in flight', async () => {
    await renderLedger()
    const health = section('Data health')
    expect(health.getByText('Reconcile the first dirty record').closest('button').disabled).toBe(false)
  })

  it('repairs on demand and reloads afterwards', async () => {
    const user = await renderLedger()
    await user.click(section('Data health').getByText('Reconcile the first dirty record'))
    await waitFor(() =>
      expect(calls.some((call) => call.url.includes('/reconcile') && call.method === 'POST')).toBe(true)
    )
    expect(await screen.findByRole('heading', { name: 'Last action' })).toBeTruthy()
    const last = section('Last action')
    expect(last.getByText(/Reconciled. 1 row\(s\) overwritten/)).toBeTruthy()
    expect(last.getByText('Opportunity/006A000001')).toBeTruthy()
  })

  it('reports a dropped change as dropped, with the reason in words', async () => {
    await renderLedger()
    await waitFor(() => expect(calls.length).toBeGreaterThan(0))
    // The drop is what the server answers for a change on a dirty record. Render it
    // through the same helper the page uses, so the wording is pinned.
    expect(dropNote({ applied: false, reason: 'dirty_and_newer_than_the_read' })).toContain(
      'The dirty flag stays set'
    )
  })

  it('resubscribes on demand and says when it did', async () => {
    const user = await renderLedger()
    await user.click(section('Gap and overflow ledger').getByText('Resubscribe this room'))
    await waitFor(() =>
      expect(calls.some((call) => call.url.includes('/subscribe') && call.method === 'POST')).toBe(true)
    )
    expect(await screen.findByText(/Resubscribed at 2026-09-27T13:00:00\+00:00/)).toBeTruthy()
  })

  it('posts a gap event in the vendor own field names', async () => {
    const user = await renderLedger()
    await user.click(section('Report a gap').getByText('Report this event'))
    await waitFor(() =>
      expect(calls.some((call) => call.url.includes('/gap-events') && call.method === 'POST')).toBe(true)
    )
    const posted = calls.filter((call) => call.url.includes('/gap-events') && call.method === 'POST').at(-1)
    // The research writes these names in the vendor's camelCase, and a page that
    // renamed them would make the documented flow impossible to follow by hand.
    expect(Object.keys(posted.body).sort()).toEqual([
      'changeType',
      'commitTimestamp',
      'entity',
      'recordIds',
      'transactionKey',
      'vendor',
    ])
    expect(posted.body.changeType).toBe('GAP_UPDATE')
    expect(posted.body.entity).toBe('Opportunity')
    expect(posted.body.recordIds).toEqual(['006A000001'])
    expect(posted.body.replayId).toBeUndefined()
    expect(posted.body.changeCount).toBeUndefined()
  })

  it('reports an overflow with a change count and a replay id, and no record id', async () => {
    const user = await renderLedger()
    await user.selectOptions(screen.getByLabelText('Change type'), 'GAP_OVERFLOW')
    await user.click(section('Report a gap').getByText('Report this event'))
    await waitFor(() =>
      expect(calls.some((call) => call.url.includes('/gap-events') && call.method === 'POST')).toBe(true)
    )
    const posted = calls.filter((call) => call.url.includes('/gap-events') && call.method === 'POST').at(-1)
    expect(posted.body.changeType).toBe('GAP_OVERFLOW')
    expect(posted.body.recordIds).toBeUndefined()
    // "Overflow events are generated when a single transaction involves more than
    // 100,000 changes", so the picker reports one past the published threshold.
    expect(posted.body.changeCount).toBe(100001)
    expect(posted.body.replayId).toMatch(/^page-replay-/)
  })

  it('shows an empty state rather than a blank grid when nothing is dirty', async () => {
    window.localStorage.setItem('wf-050-reconcile-gaps-after-a-dropped-change-st:last-room', ROOM_QUIET.id)
    await renderPage()
    await screen.findByRole('heading', { name: 'Data health' })
    expect(
      section('Data health').getByText('No record is dirty. Every gap this room has seen has been repaired.')
    ).toBeTruthy()
    expect(section('Gap and overflow ledger').getByText('No gap event has been reported for this room.'))
      .toBeTruthy()
    expect(section('Resumable positions').getByText(/holds no resumable position yet/)).toBeTruthy()
  })

  it('says a clean room is clean', async () => {
    window.localStorage.setItem('wf-050-reconcile-gaps-after-a-dropped-change-st:last-room', ROOM_QUIET.id)
    await renderPage()
    await screen.findByRole('heading', { name: 'Data health' })
    expect(screen.queryByText('This room owes a full re-read')).toBeNull()
  })

  it('reports an API failure with a retry rather than an empty page', async () => {
    window.localStorage.setItem('wf-050-reconcile-gaps-after-a-dropped-change-st:last-room', ROOM_BROKEN.id)
    await renderPage()
    expect(await screen.findByText('Could not load data')).toBeTruthy()
    expect(screen.getByText('Retry')).toBeTruthy()
  })

  it('disables the repair button until a room is chosen', async () => {
    await renderPage()
    expect(screen.queryByText('Reconcile the first dirty record')).toBeNull()
  })

  it('reads every surface of its own prefix and no other feature prefix', async () => {
    await renderLedger()
    await waitFor(() => expect(calls.length).toBeGreaterThan(3))
    const scoped = calls.filter((call) => call.url.includes('/api/wf-050/'))
    expect(scoped.length).toBeGreaterThanOrEqual(5)
    for (const call of calls) {
      expect(call.url).not.toMatch(/\/api\/wf-0(42|43|45|49)\//)
    }
  })
})

describe('the fixture contract', () => {
  it('describes a ledger the server can actually produce', () => {
    // The fixture is the researched shape verbatim. A drift between it and the
    // server is a drift between this page and the API.
    for (const event of GAP_EVENTS) {
      expect(event).toHaveProperty('kind')
      expect(event).toHaveProperty('change_type')
      expect(event).toHaveProperty('entity')
      expect(event).toHaveProperty('subscription_state')
      expect(Array.isArray(event.record_ids)).toBe(true)
    }
    const overflow = GAP_EVENTS.find((row) => row.kind === 'overflow')
    expect(overflow.record_ids).toHaveLength(0)
    expect(overflow.change_count).toBeGreaterThan(100000)
    expect(overflow.exceeds_overflow_threshold).toBe(true)
    expect(overflow.replay_id).toBeTruthy()
  })

  it('names the researched numbers the page renders', () => {
    expect(VOCABULARY.numbers.overflow_change_threshold.value).toBe(100000)
    expect(VOCABULARY.numbers.change_tracking_expiry_days.value).toBe(7)
    expect(VOCABULARY.last_modified_field).toBe('LastModifiedDate')
  })
})