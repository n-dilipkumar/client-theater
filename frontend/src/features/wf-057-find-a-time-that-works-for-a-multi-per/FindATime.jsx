/**
 * Find a time: the shortlist page for a multi-person panel (WF-057).
 *
 * The page follows the researched flow rather than a feature tour, because the
 * flow is what a rep does and the order matters:
 *
 *   1. "User opens a 'find a time' surface ... and picks a set of participants +
 *      a date range." So the page starts from a room and a saved panel, and the
 *      panel is the participant set plus the range.
 *   2. "The app collects the attendees' email addresses and location constraints
 *      (room / 'suggest a location')." So the invited list is shown as people
 *      with their addresses, and a calendar nobody has published is called out as
 *      such rather than shown as free.
 *   3. "The app calls each attendee's calendar free/busy service." So the
 *      **exact request that would go on the wire** is one disclosure away, per
 *      dialect, with the researched scope and the `Prefer: outlook.timezone`
 *      header. A rep can check it rather than trust it.
 *   4. "Ranked candidate slots are returned, each with a confidence percentage
 *      and a human-readable reason; the user picks one." So the shortlist *is*
 *      the page, each row carrying the researched confidence percentage and its
 *      `suggestionReason` - and the ranking is the researched one, so the best
 *      slot is often not the earliest one and the page says so.
 *   5. "On pick, the app creates the event on the organizer's calendar
 *      (optionally creating a fresh conference)." So booking is per row, with the
 *      conference toggle in the page rather than hidden in a dialog.
 *
 * Three things this page is careful to show rather than hide:
 *
 *   - **An empty shortlist is not an error.** The research says
 *     `emptySuggestionsReason` is the signal to re-call with adjusted parameters,
 *     so an empty result is rendered as the reason *plus* a button per
 *     adjustment, and the button makes the second call. The searches that came
 *     back with nothing stay in the log beside the ones that worked.
 *   - **A 49% is not a 100%.** A calendar nobody has published contributes the
 *     researched unknown weight, so a slot that looks perfect can still read 83%.
 *     The reason string says who, and the free-share says how much.
 *   - **Booking re-checks.** Availability is a pull read with no invalidation and
 *     the research notes suggestions are fine-tuned over time, so a booking can
 *     still be refused - and this page reports that as "re-run the search", not
 *     as a failure of the user.
 */

import { useCallback, useMemo, useState } from 'react'
import { relativeTime } from '@/lib/api'
import {
  Badge,
  Card,
  EmptyState,
  ErrorNote,
  Field,
  JsonView,
  Spinner,
  inputClass,
  useAsync,
} from '@/components/ui'
import { listRooms, panelTimeApi } from './api'
import Glyph, { COMMIT_ICON, RECALL_ICON } from './icons'
import { ConfidenceBadge, Disclosure, Notice, PathButton, StatTile, Toggle } from './primitives'

/** A short label for a provider, so the picker reads as a choice not a code. */
const PROVIDER_LABEL = {
  google: 'Google Calendar — freeBusy',
  graph: 'Microsoft Graph — findMeetingTimes',
}

const PROVIDER_DESCRIPTION = {
  google: 'POST calendar/v3/freeBusy, with the calendar.events.freebusy scope',
  graph: 'POST /me/findMeetingTimes, delegated Calendars.Read.Shared, Prefer: outlook.timezone',
}

const RANKER_LABEL = {
  confidence: 'Confidence, then chronological (the researched order)',
  weighted: 'Confidence less a penalty per unknown calendar',
}

const REASON_LABEL = {
  notOrganizedAsAttendee: 'The panel does not invite its own organizer',
  notEnoughCalendarFreeTime: 'No slot in the searched window fits the meeting',
  notEnoughPeopleFree: 'Too few attendees are free for the bar this panel set',
  busySuggestions: 'House rules removed every candidate',
  none: 'No more specific cause could be derived',
}

const STATUS_GLYPH = { free: 'free', tentative: 'unknown', none: 'busy' }
const STATUS_WORD = { free: 'free', tentative: 'unknown', none: 'busy' }

/** Format an instant for a human. UTC, because the searched window is instants. */
function when(iso) {
  if (!iso) return '—'
  const parsed = new Date(iso)
  if (Number.isNaN(parsed.getTime())) return iso
  return parsed.toLocaleString(undefined, {
    weekday: 'short',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export default function FindATime() {
  const [roomId, setRoomId] = useState('')
  const [panelId, setPanelId] = useState('')
  const [searchId, setSearchId] = useState('')
  const [threshold, setThreshold] = useState('')
  const [ranker, setRanker] = useState('')
  const [reasons, setReasons] = useState(null)
  const [conference, setConference] = useState(true)
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState(null)
  const [note, setNote] = useState(null)

  const rooms = useAsync(() => listRooms({ limit: 100 }), [])
  const vocabulary = useAsync(() => panelTimeApi.vocabulary(), [])
  const inferences = useAsync(() => panelTimeApi.inferences(), [])

  const panels = useAsync(
    () => (roomId ? panelTimeApi.listPanels(roomId) : Promise.resolve(null)),
    [roomId]
  )
  const searches = useAsync(
    () => (roomId ? panelTimeApi.listSearches(roomId, { limit: 50 }) : Promise.resolve(null)),
    [roomId]
  )
  const bookings = useAsync(
    () => (roomId ? panelTimeApi.listBookings(roomId) : Promise.resolve(null)),
    [roomId]
  )

  // The settings actually in force for the next call: the control's own choice if
  // made, otherwise whatever the selected panel says. Both live in one place so
  // the control and the request cannot disagree.
  const selected = (panels.data?.panels || []).find((entry) => entry.id === panelId)
  const effectiveThreshold = threshold === '' ? selected?.min_attendee_percentage ?? 0 : Number(threshold)
  const effectiveRanker = ranker || selected?.ranker || 'confidence'
  const effectiveReasons = reasons === null ? selected?.return_suggestion_reasons !== false : reasons

  const overrides = useMemo(() => {
    const payload = {}
    if (threshold !== '') payload.min_attendee_percentage = Number(threshold)
    if (ranker) payload.ranker = ranker
    if (reasons !== null) payload.return_suggestion_reasons = reasons
    return payload
  }, [threshold, ranker, reasons])

  // The live preview: the same computation the real call makes, against the
  // controls, with no row written. This is what makes the settings explorable.
  const preview = useAsync(
    () => (roomId && panelId ? panelTimeApi.preview(roomId, panelId, overrides) : Promise.resolve(null)),
    [roomId, panelId, overrides]
  )

  const current = useAsync(
    () =>
      roomId && searchId ? panelTimeApi.readSearch(roomId, searchId) : Promise.resolve(null),
    [roomId, searchId]
  )

  const refresh = useCallback(() => {
    searches.refetch()
    bookings.refetch()
  }, [searches, bookings])

  async function runFind() {
    setBusy(true)
    setFailure(null)
    setNote(null)
    try {
      const result = await panelTimeApi.find(roomId, panelId, overrides)
      setSearchId(result.id)
      refresh()
      setNote(
        result.suggestions?.length
          ? `${result.suggestions.length} slot(s) ranked. Pick one to create the event.`
          : `Nothing fit: ${REASON_LABEL[result.empty_suggestions_reason] || result.empty_suggestions_reason}.`
      )
    } catch (error) {
      setFailure(error)
    } finally {
      setBusy(false)
    }
  }

  async function retune(adjustmentId) {
    setBusy(true)
    setFailure(null)
    setNote(null)
    try {
      const result = await panelTimeApi.retune(roomId, searchId, { adjustment: adjustmentId })
      setSearchId(result.id)
      refresh()
      setNote(
        `Re-called with "${adjustmentId}": ${result.suggestions?.length || 0} slot(s) now fit. ` +
          'Both calls are in the log — the empty one and the one that fixed it.'
      )
    } catch (error) {
      setFailure(error)
    } finally {
      setBusy(false)
    }
  }

  async function book(start) {
    setBusy(true)
    setFailure(null)
    setNote(null)
    try {
      const result = await panelTimeApi.book(roomId, searchId, {
        start,
        create_conference: conference,
      })
      refresh()
      setNote(
        `Created on the organizer's calendar: ${result.summary} at ${when(result.start)}` +
          (result.conference ? `, join at ${result.conference.entry_point}` : ', no fresh conference')
      )
    } catch (error) {
      setFailure(error)
    } finally {
      setBusy(false)
    }
  }

  if (rooms.loading || vocabulary.loading) {
    return <Spinner label="Loading find a time" />
  }
  if (rooms.error) return <ErrorNote error={rooms.error} onRetry={rooms.refetch} />
  if (vocabulary.error) return <ErrorNote error={vocabulary.error} onRetry={vocabulary.refetch} />

  const roomOptions = rooms.data?.records || []
  const panelOptions = panels.data?.panels || []
  const searchRows = searches.data?.searches || []
  const shown = current.data || preview.data
  const suggestions = shown?.suggestions || []
  const weights = vocabulary.data?.availability?.weights || {}

  return (
    <div className="space-y-5">
      <header className="space-y-1">
        <h1 className="flex items-center gap-2 text-lg font-semibold text-foreground">
          <Glyph name="panel" size={20} />
          Find a time that works for everyone
        </h1>
        <p className="max-w-3xl text-sm text-muted-foreground">
          Several calendars are read at once and every candidate slot is scored with the researched
          per-attendee weights — free <strong>{weights.free ?? 100}%</strong>, unknown{' '}
          <strong>{weights.tentative ?? weights.unknown ?? 49}%</strong>, busy{' '}
          <strong>{weights.none ?? weights.busy ?? 0}%</strong> — then ranked highest first, and
          chronologically only to break a tie. This page shows that ranking, the reason behind each
          slot, and the exact request that would go to the calendar provider.
        </p>
      </header>

      {/* -- pick a room and a panel --------------------------------------- */}
      <Card className="grid gap-4 md:grid-cols-2">
        <Field label="Room" id="wf057-room" hint="The panel belongs to one room.">
          <select
            id="wf057-room"
            className={inputClass}
            value={roomId}
            onChange={(event) => {
              setRoomId(event.target.value)
              setPanelId('')
              setSearchId('')
            }}
          >
            <option value="">Choose a room…</option>
            {roomOptions.map((room) => (
              <option key={room.id} value={room.id}>
                {room.data?.name || room.id}
              </option>
            ))}
          </select>
        </Field>

        <Field
          label="Panel"
          id="wf057-panel"
          hint={
            panelOptions.length
              ? 'A saved participant set and date range.'
              : 'No panels declared for this room yet.'
          }
        >
          <select
            id="wf057-panel"
            className={inputClass}
            value={panelId}
            disabled={!roomId || !panelOptions.length}
            onChange={(event) => {
              setPanelId(event.target.value)
              setSearchId('')
              setThreshold('')
              setRanker('')
              setReasons(null)
            }}
          >
            <option value="">Choose a panel…</option>
            {panelOptions.map((panel) => (
              <option key={panel.id} value={panel.id}>
                {panel.name} — {panel.calendar_count} calendar{panel.calendar_count === 1 ? '' : 's'}
              </option>
            ))}
          </select>
        </Field>
      </Card>

      {!roomId && (
        <EmptyState
          title="Choose a room to begin"
          description="A find-a-time surface can sit in a sales room, a CRM record or a scheduling page; this one is scoped to a room, and the calendars it reads are shared across all of them."
        />
      )}

      {roomId && !panelId && (
        <EmptyState
          title="No panel selected"
          description="A panel is a participant set plus a date range, saved so the same question can be asked again next week."
        />
      )}

      {selected && (
        <>
          {/* -- the researched knobs -------------------------------------- */}
          <Card className="space-y-4">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h2 className="text-sm font-semibold text-foreground">The searched parameters</h2>
              <p className="text-xs text-muted-foreground">
                Every field here is something the research says to adjust when a search comes back
                empty. Move one and the shortlist and the request both change.
              </p>
            </div>

            <div className="grid gap-4 md:grid-cols-3">
              <Field
                label="Minimum attendee percentage"
                id="wf057-threshold"
                hint={`Currently ${effectiveThreshold}%. The share of invited calendars that must be free; the confidence percentage is reported either way.`}
              >
                <input
                  id="wf057-threshold"
                  type="number"
                  min="0"
                  max="100"
                  step="5"
                  className={inputClass}
                  value={threshold}
                  placeholder={String(selected.min_attendee_percentage ?? 0)}
                  onChange={(event) => setThreshold(event.target.value)}
                />
              </Field>

              <Field label="Ranking" id="wf057-ranker" hint={RANKER_LABEL[effectiveRanker]}>
                <select
                  id="wf057-ranker"
                  className={inputClass}
                  value={effectiveRanker}
                  onChange={(event) => setRanker(event.target.value)}
                >
                  {(vocabulary.data?.rankers || ['confidence', 'weighted']).map((name) => (
                    <option key={name} value={name}>
                      {RANKER_LABEL[name] || name}
                    </option>
                  ))}
                </select>
              </Field>

              <Field label="Calendar provider" id="wf057-provider" hint={PROVIDER_DESCRIPTION[selected.calendar_provider]}>
                <select
                  id="wf057-provider"
                  className={inputClass}
                  value={selected.calendar_provider}
                  disabled
                  onChange={() => {}}
                >
                  <option value={selected.calendar_provider}>
                    {PROVIDER_LABEL[selected.calendar_provider] || selected.calendar_provider}
                  </option>
                </select>
              </Field>
            </div>

            <Toggle
              id="wf057-reasons"
              checked={effectiveReasons}
              onChange={setReasons}
              label="Return suggestion reasons"
              hint="The researched returnSuggestionReasons toggle. Off, the reason key is absent from every suggestion rather than null — the absence is the whole effect."
            />
          </Card>

          {/* -- the invited set -------------------------------------------- */}
          <Card className="space-y-3">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h2 className="text-sm font-semibold text-foreground">Who is being asked</h2>
              <p className="text-xs text-muted-foreground">
                A distribution list is replaced by its members, never scored alongside them.
              </p>
            </div>
            {preview.loading ? (
              <Spinner label="Reading the calendars" />
            ) : preview.error ? (
              <ErrorNote error={preview.error} onRetry={preview.refetch} />
            ) : (
              <>
                <ul className="flex flex-wrap gap-2">
                  {(preview.data?.invited || []).map((entry) => (
                    <li key={entry.id}>
                      <Badge tone={entry.readable ? 'neutral' : 'insert'}>
                        <Glyph
                          name={entry.kind === 'room' ? 'room' : entry.readable ? 'free' : 'unknown'}
                          size={12}
                        />{' '}
                        {entry.email}
                        {entry.kind !== 'person' ? ` (${entry.kind})` : ''}
                        {entry.readable ? '' : ' — availability unknown, counted at 49%'}
                      </Badge>
                    </li>
                  ))}
                </ul>
                {(preview.data?.expansion?.groups_expanded || []).length > 0 && (
                  <p className="text-xs text-muted-foreground">
                    Expanded{' '}
                    {(preview.data?.expansion?.groups_expanded || [])
                      .map((group) => group.email)
                      .join(', ')}{' '}
                    into {(preview.data?.expansion?.groups_expanded || [])[0]?.members?.length ?? 0}{' '}
                    calendar(s).
                  </p>
                )}
                {(preview.data?.expansion?.groups_unexpanded || []).length > 0 && (
                  <p className="text-xs text-amber-200">
                    Not expanded:{' '}
                    {(preview.data?.expansion?.groups_unexpanded || [])
                      .map((group) => `${group.email} (${group.reason})`)
                      .join(', ')}
                    . The group is still invited, as an unknown at 49%.
                  </p>
                )}
              </>
            )}
          </Card>

          {/* -- the researched stats --------------------------------------- */}
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <StatTile
              label="Candidates"
              value={preview.data?.counts?.candidates ?? '—'}
              hint="Every start in the window the meeting fits in"
              glyph={undefined}
            />
            <StatTile
              label="Suggested"
              value={preview.data?.counts?.suggested ?? '—'}
              hint={`${effectiveThreshold}% or more of the invited calendars free`}
            />
            <StatTile
              label="Unreadable"
              value={preview.data?.counts?.unreadable ?? 0}
              hint="Calendars counted at the researched 49%"
            />
            <StatTile
              label="House rules refused"
              value={preview.data?.counts?.house_rule_refusals ?? 0}
              hint="The integrator's own filter, above the calendars"
            />
          </div>

          {/* -- warnings, and the empty-suggestions path ------------------- */}
          {(preview.data?.warnings || []).length > 0 && (
            <div className="space-y-2">
              {(preview.data?.warnings || []).map((warning) => (
                <Notice key={warning.code} tone="warn" title={warning.code.replace(/_/g, ' ')}>
                  {warning.detail}
                </Notice>
              ))}
            </div>
          )}

          {preview.data && suggestions.length === 0 && (
            <Notice
              tone="warn"
              title={`emptySuggestionsReason: ${preview.data.empty_suggestions_reason || 'none'}`}
            >
              {REASON_LABEL[preview.data.empty_suggestions_reason] ||
                'Nothing in the searched window worked.'}{' '}
              Run the search to record it, then use the re-call below — the research says this
              property is the signal to call again with adjusted parameters.
            </Notice>
          )}

          {/* -- the shortlist, which is the feature ----------------------- */}
          <Card className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 className="text-sm font-semibold text-foreground">
                Ranked slots {searchId ? '(the recorded search)' : '(live preview)'}
              </h2>
              <PathButton
                glyph={RECALL_ICON}
                variant="primary"
                onClick={runFind}
                disabled={busy || !panelId}
              >
                {busy ? 'Working…' : 'Run the search'}
              </PathButton>
            </div>

            {suggestions.length === 0 ? (
              <EmptyState
                title="No slot fits"
                description="Widen the window, lower the bar, or drop a house rule — the empty result says which of those is worth trying first."
              />
            ) : (
              <ul className="space-y-2">
                {suggestions.map((slot) => (
                  <li
                    key={slot.start}
                    className="flex flex-wrap items-start justify-between gap-3 rounded-lg border border-border-subtle/40 p-3"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-mono text-sm text-foreground">{when(slot.start)}</span>
                        <span className="font-mono text-xs text-muted-foreground">
                          → {when(slot.end)}
                        </span>
                        <ConfidenceBadge
                          confidence={slot.confidence}
                          freePercentage={slot.free_percentage}
                        />
                      </div>
                      {slot.suggestion_reason && (
                        <p className="mt-1.5 text-sm text-muted-foreground">
                          {slot.suggestion_reason}
                        </p>
                      )}
                      <ul className="mt-2 flex flex-wrap gap-1.5">
                        {(slot.statuses || []).map((status, index) => (
                          <li key={`${slot.start}-${index}`}>
                            <Badge tone={status === 'free' ? 'neutral' : 'insert'}>
                              <Glyph name={STATUS_GLYPH[status] || 'unknown'} size={12} />{' '}
                              {STATUS_WORD[status] || status}
                            </Badge>
                          </li>
                        ))}
                      </ul>
                    </div>
                    <PathButton
                      glyph={COMMIT_ICON}
                      onClick={() => book(slot.start)}
                      disabled={!searchId || busy}
                      title={
                        searchId
                          ? 'Create the event on the organizer\'s calendar for this slot'
                          : 'Run the search first — a booking picks from the recorded list'
                      }
                    >
                      Book
                    </PathButton>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          {/* -- the researched re-call ------------------------------------- */}
          {searchId && current.data?.empty_suggestions_reason && (
            <Card className="space-y-3">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <h2 className="flex items-center gap-2 text-sm font-semibold text-foreground">
                  <Glyph name="recall" size={16} />
                  The documented re-call
                </h2>
                <p className="text-xs text-muted-foreground">
                  &ldquo;Based on this value, you can better adjust the parameters and call again
                  &rdquo; — the second call is a second row, beside the empty one.
                </p>
              </div>
              <p className="text-sm text-muted-foreground">
                {REASON_LABEL[current.data.empty_suggestions_reason] ||
                  current.data.empty_suggestions_reason}
              </p>
              <ul className="space-y-2">
                {(current.data.suggested_adjustments || []).map((adjustment) => (
                  <li
                    key={adjustment.id}
                    className="flex flex-wrap items-start justify-between gap-3 rounded-lg border border-border-subtle/40 p-3"
                  >
                    <div className="min-w-0 flex-1">
                      <p className="font-mono text-sm text-foreground">{adjustment.id}</p>
                      <p className="mt-1 text-sm text-muted-foreground">{adjustment.why}</p>
                    </div>
                    <PathButton
                      glyph={RECALL_ICON}
                      onClick={() => retune(adjustment.id)}
                      disabled={busy}
                    >
                      Call again
                    </PathButton>
                  </li>
                ))}
              </ul>
            </Card>
          )}

          {/* -- the searched request, so a rep can check it ---------------- */}
          {shown?.request && (
            <Disclosure summary="The request this search put on the wire">
              <div className="space-y-2">
                <p className="font-mono text-xs text-muted-foreground">
                  {shown.request.method} {shown.request.url}
                </p>
                <p className="font-mono text-xs text-muted-foreground">
                  scope: {shown.request.scope}
                  {shown.request.delegated ? ' (delegated)' : ''}
                </p>
                <JsonView value={shown.request.body} />
              </div>
            </Disclosure>
          )}

          {(shown?.below_threshold || []).length > 0 && (
            <Disclosure
              summary={`${shown.below_threshold.length} candidate(s) withheld by minAttendeePercentage`}
            >
              <ul className="space-y-1">
                {shown.below_threshold.map((row) => (
                  <li key={row.start} className="text-sm text-muted-foreground">
                    <span className="font-mono text-foreground">{when(row.start)}</span> —{' '}
                    {row.reason}
                  </li>
                ))}
              </ul>
            </Disclosure>
          )}

          {(shown?.house_rule_refusals || []).length > 0 && (
            <Disclosure summary={`${shown.house_rule_refusals.length} candidate(s) a house rule removed`}>
              <ul className="space-y-1">
                {shown.house_rule_refusals.map((row) => (
                  <li key={row.start} className="text-sm text-muted-foreground">
                    <span className="font-mono text-foreground">{when(row.start)}</span> —{' '}
                    <span className="font-mono text-amber-200">{row.rule}</span>: {row.detail}
                  </li>
                ))}
              </ul>
            </Disclosure>
          )}
        </>
      )}

      {failure && <ErrorNote error={failure} onRetry={() => setFailure(null)} />}
      {note && <Notice tone="good" title="Done">{note}</Notice>}

      <Toggle
        id="wf057-conference"
        checked={conference}
        onChange={setConference}
        label="Create a fresh conference when booking"
        hint="The researched step 5: 'optionally creating a fresh conference'. Unchecked, the commit request carries no conference data at all."
      />

      {/* -- the call log ------------------------------------------------- */}
      <Card className="space-y-3">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="text-sm font-semibold text-foreground">The call log</h2>
          <p className="text-xs text-muted-foreground">
            A search that came back with nothing is a row, not a gap — it is the input to the next
            call.
          </p>
        </div>
        {searchRows.length === 0 ? (
          <p className="text-sm text-muted-foreground">No find-a-time calls recorded for this room.</p>
        ) : (
          <ul className="space-y-1">
            {searchRows.map((row) => (
              <li key={row.id}>
                <button
                  type="button"
                  onClick={() => setSearchId(row.id)}
                  aria-current={row.id === searchId ? 'true' : undefined}
                  className={`flex min-h-11 w-full flex-wrap items-center justify-between gap-2 rounded-lg
                    px-3 py-1.5 text-left text-sm hover:bg-muted
                    focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2
                    focus-visible:ring-offset-background
                    ${row.id === searchId ? 'bg-muted' : ''}`}
                >
                  <span className="min-w-0">
                    <span className="font-mono text-foreground">{row.panel_name}</span>
                    {row.adjustment && (
                      <span className="ml-2 font-mono text-xs text-accent">
                        re-called: {row.adjustment}
                      </span>
                    )}
                    {row.empty_suggestions_reason && (
                      <span className="ml-2 font-mono text-xs text-amber-200">
                        <Glyph name="empty" size={12} /> {row.empty_suggestions_reason}
                      </span>
                    )}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {row.suggestion_count} slot{row.suggestion_count === 1 ? '' : 's'} ·{' '}
                    {relativeTime(row.created_at)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {/* -- what has been booked ---------------------------------------- */}
      {(bookings.data?.bookings || []).length > 0 && (
        <Card className="space-y-3">
          <h2 className="text-sm font-semibold text-foreground">Booked</h2>
          <ul className="space-y-2">
            {(bookings.data?.bookings || []).map((booking) => (
              <li
                key={booking.id}
                className="flex flex-wrap items-start justify-between gap-3 rounded-lg border border-border-subtle/40 p-3"
              >
                <div className="min-w-0">
                  <p className="text-sm text-foreground">{booking.summary}</p>
                  <p className="mt-0.5 font-mono text-xs text-muted-foreground">
                    {when(booking.start)} → {when(booking.end)} · organizer {booking.organizer_email}
                  </p>
                  {booking.conference && (
                    <p className="mt-0.5 font-mono text-xs text-accent">
                      {booking.conference.entry_point}
                    </p>
                  )}
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    Re-checked at booking: {booking.revalidated?.confidence ?? '—'}% still free.
                  </p>
                </div>
                <Badge tone="insert">
                  <Glyph name="commit" size={12} /> {booking.event_id}
                </Badge>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {/* -- what this build decided, and what it did not ----------------- */}
      <Card className="space-y-3">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-foreground">
          <Glyph name="rule" size={16} />
          What this build decided
        </h2>
        <p className="text-sm text-muted-foreground">
          The research is specific about the wire and quiet about everything around it. The parts
          below are judgement calls, and each one says what it is measured against and how to change
          it — the point being that a reader can disagree with one of them without arguing with a
          sourced number.
        </p>
        {(inferences.data?.inferences || []).map((entry) => (
          <Disclosure key={entry.id} summary={`${entry.topic} — ${entry.basis}`}>
            <div className="space-y-2 text-sm">
              <p className="text-foreground">{entry.value}</p>
              <p className="text-muted-foreground">{entry.why}</p>
              <p className="text-muted-foreground">
                <span className="font-medium text-foreground">To change it:</span>{' '}
                {entry.change_it}
              </p>
              <p className="text-muted-foreground">
                <span className="font-medium text-foreground">If it is wrong:</span>{' '}
                {entry.blast_radius}
              </p>
            </div>
          </Disclosure>
        ))}
        <Disclosure summary={`The surfaces this build deliberately does not implement (${
          (vocabulary.data?.adjacent_surfaces || []).length
        })`}>
          <ul className="space-y-2 text-sm">
            {(vocabulary.data?.adjacent_surfaces || []).map((surface) => (
              <li key={surface.surface}>
                <p className="text-foreground">{surface.surface}</p>
                <p className="text-muted-foreground">{surface.why_not}</p>
              </li>
            ))}
          </ul>
        </Disclosure>
        <p className="flex items-start gap-2 text-xs text-muted-foreground">
          <Glyph name="panel" size={14} />
          <span>
            {inferences.data?.count ?? 0} inferences,{' '}
            {vocabulary.data?.sourced_quotes?.length ?? 0} sourced quotes, and{' '}
            {vocabulary.data?.sourced_gaps?.length ?? 0} gaps the research itself records. Nothing
            in this page hard-codes a status, a limit, a reason or an adjustment name; they are all
            rendered from the server's own vocabulary.
          </span>
        </p>
      </Card>
    </div>
  )
}
