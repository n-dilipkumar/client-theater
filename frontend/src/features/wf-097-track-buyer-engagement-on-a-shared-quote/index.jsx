/**
 * WF-097: track buyer engagement on a shared quote and drive the follow-up.
 *
 * The page is built from `@/components/ui` and from the primitives in this
 * folder. It reads its event labels, its counter labels, its activity classes and
 * its bounds from the server's `/vocabulary` route rather than repeating them, so
 * the panel cannot tell a seller a bound the rules do not enforce.
 *
 * Four things this page deliberately does not do, each quoted from
 * `docs/research/digital-sales-room-workflows/wf/WF-097.md`:
 *
 *   - It does not invent a threshold. "The activity threshold is configurable"
 *     and the research gives no number, so a workspace that has set none is told
 *     it has set none and every quote reads "Not classified". Showing a default
 *     would show a finding the workspace never asked for.
 *   - It does not claim to have sent an email. This product has no mail
 *     transport; what the page shows is the recorded `Quote sent` event.
 *   - It does not show an Activity Count Since Last Communication figure. The
 *     research names a connected email channel as the source for that column and
 *     this build has none, so the cell says why it is empty rather than counting
 *     outbound events and calling the buyer's silence.
 *   - It does not offer the revenue analytics suite, custom reports, board-view
 *     chips or the published-quote export. The research mentions them; it does
 *     not ask for them, and the page says so rather than omitting them quietly.
 */

import { useState } from 'react'

import {
  Button,
  Card,
  EmptyState,
  ErrorNote,
  Field,
  Spinner,
  StatCard,
  inputClass,
  useAsync,
} from '@/components/ui'

import {
  SETTING_FIELDS,
  amount,
  engagementApi,
  listRooms,
  percent,
  shortTimestamp,
  withinBounds,
} from './api'
import {
  ActivityClassBadge,
  BubbleChart,
  Counter,
  CounterRow,
  FollowUpCard,
  InlineNote,
  QuoteIndexRow,
  SettingField,
} from './primitives'

const BASIS_LABELS = {
  no_email_channel_connected: 'No email channel connected',
  no_communication_recorded: 'No communication recorded yet',
}

/** Everything the page reads, loaded together so the panels cannot disagree. */
function useDashboard(roomId, includeExpired) {
  const vocabulary = useAsync(() => engagementApi.vocabulary(), [])
  const overview = useAsync(() => engagementApi.overview(roomId), [roomId])
  const followUps = useAsync(() => engagementApi.followUps(roomId), [roomId])
  const chart = useAsync(() => engagementApi.chart(roomId, includeExpired), [
    roomId,
    includeExpired,
  ])
  const quotes = useAsync(() => engagementApi.quotes(roomId), [roomId])

  const loading =
    vocabulary.loading || overview.loading || followUps.loading || chart.loading || quotes.loading
  const error = vocabulary.error || overview.error || followUps.error || chart.error || quotes.error

  return { vocabulary, overview, followUps, chart, quotes, loading, error }
}

function QuoteEngagementPage() {
  const [roomId, setRoomId] = useState('')
  const [includeExpired, setIncludeExpired] = useState(false)
  const [status, setStatus] = useState('')
  const [activityClass, setActivityClass] = useState('')
  const [openQuote, setOpenQuote] = useState(null)
  const [sendNote, setSendNote] = useState(null)
  const [sendError, setSendError] = useState(null)
  const [busyQuote, setBusyQuote] = useState(null)

  const rooms = useAsync(() => listRooms(), [])
  const board = useDashboard(roomId, includeExpired)

  // One handler for the four panels, so a settings save or a follow-up send
  // refreshes the whole dashboard in one go and the panels cannot end up
  // describing two different moments.
  function refetchAll() {
    board.overview.refetch()
    board.followUps.refetch()
    board.chart.refetch()
    board.quotes.refetch()
  }

  async function send(entry) {
    setBusyQuote(entry.quote_id)
    setSendError(null)
    setSendNote(null)
    try {
      const result = await engagementApi.sendFollowUp(entry.quote_id)
      setSendNote(`${entry.title}: ${result.message}`)
      refetchAll()
    } catch (problem) {
      setSendError(problem)
    } finally {
      setBusyQuote(null)
    }
  }

  if (board.loading && !board.overview.data) return <Spinner label="Loading quote engagement" />
  if (board.error) return <ErrorNote error={board.error} onRetry={refetchAll} />

  const vocabulary = board.vocabulary.data
  const overview = board.overview.data
  const followUps = board.followUps.data
  const chart = board.chart.data
  const settings = overview?.settings || {}

  const entries = (board.quotes.data?.entries || []).filter((row) => {
    if (status && row.status !== status.toUpperCase()) return false
    if (activityClass && row.engagement?.activity_class !== activityClass) return false
    return true
  })

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold text-foreground">Quote engagement</h1>
        <p className="text-sm text-muted-foreground">
          Buyer and seller activity on a shared quote, the four researched counters, and the quotes
          that need a follow-up.
        </p>
      </header>

      {overview && overview.threshold_configured === false && (
        <InlineNote tone="warning">
          No high activity threshold is configured for this workspace, so no quote is classified. Set
          one below. Nothing is defaulted: the research names the setting and gives no number.
        </InlineNote>
      )}

      {sendNote && <InlineNote tone="success">{sendNote}</InlineNote>}
      {sendError && <ErrorNote error={sendError} />}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Quotes"
          value={overview?.quotes ?? 0}
          icon="database"
          hint="In the selected scope"
        />
        <StatCard
          label="High activity"
          value={overview?.high_activity_quotes ?? 0}
          icon="audit"
          hint={overview?.basis?.high_activity}
        />
        <StatCard
          label="At risk"
          value={overview?.quotes_at_risk ?? 0}
          icon="schema"
          hint={overview?.basis?.at_risk}
        />
        <StatCard
          label="Acceptance rate"
          value={percent(overview?.acceptance_rate)}
          icon="plus"
          hint={overview?.basis?.acceptance_rate}
        />
      </div>

      <SettingsPanel
        settings={settings}
        vocabulary={vocabulary}
        roomId={roomId}
        onSaved={refetchAll}
      />

      <div className="grid gap-6 lg:grid-cols-2">
        <Card className="flex flex-col gap-3">
          <h2 className="text-lg font-semibold text-foreground">Your published quotes</h2>
          <Field
            label="Show older expired quotes"
            id="wf097-include-expired"
            hint="With this off, a quote past its expiration date is not on the chart."
          >
            <input
              id="wf097-include-expired"
              type="checkbox"
              className="h-5 w-5 rounded-xs border-border-subtle"
              checked={includeExpired}
              onChange={(event) => setIncludeExpired(event.target.checked)}
            />
          </Field>
          {chart?.expired_hidden > 0 && (
            <InlineNote tone="info">
              {chart.expired_hidden} expired quote(s) are hidden. Tick the box above to show them.
            </InlineNote>
          )}
          <BubbleChart points={chart?.points || []} />
        </Card>

        <Card className="flex flex-col gap-3">
          <h2 className="text-lg font-semibold text-foreground">Recent quote activity</h2>
          {(overview?.recent_activity || []).length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No activity has been recorded against any quote yet.
            </p>
          ) : (
            <ul className="flex flex-col gap-2">
              {overview.recent_activity.map((row) => (
                <li
                  key={row.quote_id}
                  className="flex flex-wrap items-center justify-between gap-2 border-b border-border-subtle pb-2 last:border-b-0"
                >
                  <span className="text-sm text-foreground">{row.name}</span>
                  <span className="flex items-center gap-2">
                    <ActivityClassBadge activityClass={row.activity_class} />
                    <span className="font-mono text-xs text-muted-foreground">
                      {row.activity_count} activities | {amount(row.amount)} |{' '}
                      {shortTimestamp(row.expires_at)}
                    </span>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <Card className="flex flex-col gap-3">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <h2 className="text-lg font-semibold text-foreground">Recommended follow-ups</h2>
          <span className="text-xs text-muted-foreground">
            {followUps?.count ?? 0} quote(s) recommended
          </span>
        </div>
        {!followUps?.windows_configured?.no_activity_after_days &&
          !followUps?.windows_configured?.expiring_within_days && (
            <InlineNote tone="warning">
              No follow-up window is configured for this workspace, so nothing is recommended. The
              research names the reasons and no numbers.
            </InlineNote>
          )}
        {(followUps?.entries || []).length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No quote matched a configured window. A quote with no activity for the whole window, or
            one expiring inside the expiry window, appears here with its reason.
          </p>
        ) : (
          <div className="space-y-4">
            {followUps.entries.map((entry) => (
              <FollowUpCard
                key={entry.quote_id}
                entry={entry}
                vocabulary={vocabulary}
                onSend={send}
                busy={busyQuote === entry.quote_id}
              />
            ))}
          </div>
        )}
      </Card>

      <Card className="flex flex-col gap-3">
        <h2 className="text-lg font-semibold text-foreground">Quote index</h2>
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Room" id="wf097-room" hint="Quotes belong to a room.">
            <select
              id="wf097-room"
              className={inputClass}
              value={roomId}
              onChange={(event) => setRoomId(event.target.value)}
            >
              <option value="">All rooms</option>
              {(rooms.data?.records || []).map((room) => (
                <option key={room.id} value={room.id}>
                  {room.data?.name || room.id}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Status" id="wf097-status" hint="Filter to one quote status.">
            <select
              id="wf097-status"
              className={inputClass}
              value={status}
              onChange={(event) => setStatus(event.target.value)}
            >
              <option value="">Every status</option>
              {[...new Set((board.quotes.data?.entries || []).map((row) => row.status))].map(
                (value) => (
                  <option key={value || 'none'} value={value}>
                    {value || 'No status'}
                  </option>
                ),
              )}
            </select>
          </Field>
          <Field label="Activity class" id="wf097-class" hint="Filter to one classification.">
            <select
              id="wf097-class"
              className={inputClass}
              value={activityClass}
              onChange={(event) => setActivityClass(event.target.value)}
            >
              <option value="">Every class</option>
              {(vocabulary?.activity_classes
                ? Object.values(vocabulary.activity_classes)
                : []
              ).map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </Field>
        </div>

        {entries.length === 0 ? (
          <EmptyState
            title="No quote matched this filter"
            description="A quote appears here once it exists with a status. This workflow reads the quote object; it never authors one."
          />
        ) : (
          <div className="flex flex-col">
            {entries.map((quote) => (
              <QuoteIndexRow
                key={quote.id}
                quote={quote}
                labels={vocabulary?.counter_labels || {}}
                basisLabels={BASIS_LABELS}
                onOpen={setOpenQuote}
              />
            ))}
          </div>
        )}
      </Card>

      {openQuote && (
        <TimelinePanel quoteId={openQuote} onClose={() => setOpenQuote(null)} />
      )}

      {vocabulary?.not_built?.length > 0 && (
        <Card className="flex flex-col gap-2">
          <h2 className="text-base font-semibold text-foreground">Not built here</h2>
          <p className="text-xs text-muted-foreground">
            The research mentions these surfaces and does not ask for them. They are named so their
            absence reads as a decision rather than an oversight.
          </p>
          <ul className="list-inside list-disc text-xs text-muted-foreground">
            {vocabulary.not_built.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  )
}

function SettingsPanel({ settings, vocabulary, roomId, onSaved }) {
  const [draft, setDraft] = useState({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [note, setNote] = useState(null)

  // A value outside its bounds is caught here as well as on the server. The
  // server still refuses; this only means the seller is told before the round
  // trip, and the Save control is disabled rather than offering a failure.
  const refused = SETTING_FIELDS.filter(
    (field) => draft[field.key] !== undefined && draft[field.key] !== '' && !withinBounds(field, draft[field.key]),
  ).map((field) => `${field.label} must be a whole number from ${field.min} to ${field.max}.`)
  const draftCount = Object.keys(draft).length

  async function save() {
    setBusy(true)
    setError(null)
    setNote(null)
    try {
      await engagementApi.saveSettings(roomId, draft)
      setDraft({})
      setNote('Saved. The figures above are recomputed against the new settings.')
      onSaved()
    } catch (problem) {
      setError(problem)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card className="flex flex-col gap-3">
      <h2 className="text-lg font-semibold text-foreground">Analysis settings</h2>
      <p className="text-xs text-muted-foreground">
        These are workspace settings. Each one is optional, and a workspace that sets none gets the
        answer that it has set none rather than a default.
      </p>
      <div className="grid gap-4 sm:grid-cols-3">
        {SETTING_FIELDS.map((field) => (
          <SettingField
            key={field.key}
            field={field}
            value={draft[field.key] ?? settings[field.key] ?? ''}
            disabled={busy}
            onChange={(key, value) => setDraft((current) => ({ ...current, [key]: value }))}
          />
        ))}
      </div>
      {note && <InlineNote tone="success">{note}</InlineNote>}
      {error && <ErrorNote error={error} />}
      {refused.length > 0 && (
        <InlineNote tone="warning">
          {refused.join(' ')} Each number is a whole value inside the bounds the rules enforce.
        </InlineNote>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <Button
          variant="primary"
          icon="audit"
          onClick={save}
          disabled={busy || draftCount === 0 || refused.length > 0}
        >
          Save settings
        </Button>
        <span className="text-xs text-muted-foreground">
          {draftCount === 0 ? 'Nothing changed.' : `${draftCount} change(s) to save.`}
        </span>
      </div>
      {vocabulary?.limits && (
        <p className="text-xs text-muted-foreground">
          A threshold runs from {vocabulary.limits.threshold_min} to{' '}
          {vocabulary.limits.threshold_max} activities. A window runs from {vocabulary.limits.window_min}{' '}
          to {vocabulary.limits.window_max} days.
        </p>
      )}
    </Card>
  )
}

function TimelinePanel({ quoteId, onClose }) {
  const engagement = useAsync(() => engagementApi.engagement(quoteId), [quoteId])
  const timeline = useAsync(() => engagementApi.activity(quoteId, 50), [quoteId])

  if (engagement.loading || timeline.loading) return <Spinner label="Loading the timeline" />
  if (engagement.error) return <ErrorNote error={engagement.error} onRetry={engagement.refetch} />
  if (timeline.error) return <ErrorNote error={timeline.error} onRetry={timeline.refetch} />

  const counters = engagement.data?.engagement || {}

  return (
    <Card className="flex flex-col gap-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <h2 className="text-lg font-semibold text-foreground">
          {engagement.data?.title || quoteId}
        </h2>
        <Button icon="close" onClick={onClose}>
          Close the timeline
        </Button>
      </div>

      <CounterRow
        counters={counters}
        labels={{
          last_activity_at: 'Last Activity Date',
          activity_count: 'Activity Count',
          activity_count_since_last_communication: 'Activity Count Since Last Communication',
          view_count: 'View Count',
        }}
        basisLabels={BASIS_LABELS}
      />

      {counters.pdf_opens_not_counted_as_views > 0 && (
        <InlineNote tone="info">
          {counters.pdf_opens_not_counted_as_views} event(s) on this timeline were the buyer opening
          the PDF attached to the email. They are recorded and they are not counted as views.
        </InlineNote>
      )}

      <ol className="flex flex-col gap-2">
        {(timeline.data?.entries || []).map((row) => (
          <li
            key={row.id}
            className="flex flex-wrap items-center justify-between gap-2 border-b border-border-subtle pb-2 last:border-b-0"
          >
            <span className="text-sm text-foreground">{row.event}</span>
            <span className="font-mono text-xs text-muted-foreground">
              {row.side} | {row.surface} | {shortTimestamp(row.occurred_at)}
              {row.counted_as_view ? ' | counted as a view' : ' | not a view'}
            </span>
          </li>
        ))}
      </ol>

      <div className="grid gap-4 sm:grid-cols-3">
        <Counter label="Events" value={engagement.data?.events ?? 0} />
        <Counter label="Status" value={engagement.data?.status || 'No status'} />
        <Counter label="Expires" value={shortTimestamp(engagement.data?.expires_at)} />
      </div>
    </Card>
  )
}

export default {
  id: 'wf-097-track-buyer-engagement-on-a-shared-quote',
  label: 'Quote engagement',
  icon: 'audit',
  order: 970,
  Component: QuoteEngagementPage,
}