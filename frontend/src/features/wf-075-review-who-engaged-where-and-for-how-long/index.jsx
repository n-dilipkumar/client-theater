import { useState } from 'react'

import {
  Badge,
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
  GEOGRAPHY_SOURCE,
  TIME_UNIT,
  engagementApi,
  formatDuration,
  formatInstant,
} from './api'
import { CountPair, DwellBars, ProofBadge } from './primitives'

/**
 * WF-075: review who engaged, where and for how long.
 *
 * The page has three jobs, in this order, and the order is the design.
 *
 * **Keep the two counts apart.** The specification says a viewer who hits two links
 * "shows up once here, but twice in `papermark views list`". Those are different facts
 * and the page shows them as `CountPair`, side by side, each with a sentence saying
 * what it counts. Merging them into one engagement number would answer a question the
 * rep did not ask, and the two numbers differ every time a buyer opens a second link.
 *
 * **Say whether the identity was proven, and say when nothing says.** The user flow
 * reads `verified` "to confirm the identity was actually proven (not merely typed in)".
 * That is three answers, not two: proven, not proven, and no proof recorded. The third
 * is the common one, because a row is written when a buyer is invited and the proof
 * step usually comes later. A boolean would render those last two the same, which is
 * the exact confusion the specification's sentence warns against.
 *
 * **Label the geography as the inference it is.** The specification marks the
 * geolocation provider as inferred and says so itself: "viewer IP to geolocation provider
 * [inferred - the API returns location.country/city but names no vendor]". So the page
 * shows that sentence beside every country and names no vendor.
 *
 * Anonymous views stay visible. A view with no buyer address is a real view event, so it
 * is counted, listed, and reported separately as `anonymous_views`. A board that dropped
 * them would show a link that an unidentified browser read for nine minutes as unopened.
 *
 * Every state this page can be in is rendered: loading, error, empty. A board that goes
 * blank when the API is down reads as "nobody engaged", which is the one reading that
 * must never be possible.
 */

/** The room selector. A text field with a visible label, per the accessibility floor. */
function RoomFilter({ value, onChange }) {
  return (
    <Field label="Dataroom id" hint="Leave empty to read every room." id="wf075-room">
      <input
        id="wf075-room"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder="room_a"
        className={`${inputClass} font-mono`}
      />
    </Field>
  )
}

/**
 * The time window, in the unit the specification names.
 *
 * The hint states the unit on the field itself, because a rep who pastes a Unix second
 * where a millisecond belongs gets an empty board rather than an error, and an empty
 * board reads as "nobody engaged".
 */
function WindowFilter({ since, until, onChange }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <Field
        label="Since"
        hint={`Unix ${TIME_UNIT}. Blank means no lower bound.`}
        id="wf075-since"
      >
        <input
          id="wf075-since"
          value={since}
          onChange={(event) => onChange({ since: event.target.value })}
          placeholder="1759600000000"
          className={`${inputClass} font-mono`}
        />
      </Field>
      <Field
        label="Until"
        hint={`Unix ${TIME_UNIT}. Blank means no upper bound.`}
        id="wf075-until"
      >
        <input
          id="wf075-until"
          value={until}
          onChange={(event) => onChange({ until: event.target.value })}
          placeholder="1759686400000"
          className={`${inputClass} font-mono`}
        />
      </Field>
    </div>
  )
}

/**
 * The headline numbers.
 *
 * `cached` is rendered rather than hidden. The specification calls these "cheap (cached
 * aggregates)" and tells a poller to cache the response, so a rep looking at a board that
 * is up to a minute old should be able to see that rather than assume it is live.
 */
function AggregateBoard({ stats }) {
  if (!stats) return null
  const cached = Boolean(stats.cached)
  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <h2 className="text-lg font-semibold">Room aggregate</h2>
        <Badge tone={cached ? 'neutral' : 'info'}>
          {cached ? 'Cached' : 'Freshly computed'}
        </Badge>
      </div>

      <p className="mt-1 text-xs text-muted-foreground">
        Computed at {formatInstant(stats.computed_at)} in Unix {TIME_UNIT}.{' '}
        {stats.rate_limit_note}
      </p>

      <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="View events" value={stats.total_views ?? 0} hint="Every view, addressed or not" />
        <StatCard
          label="Unique viewers"
          value={stats.unique_visitors ?? 0}
          hint="Distinct buyer addresses behind those views"
        />
        <StatCard
          label="Time spent"
          value={formatDuration(stats.time_spent_seconds)}
          hint="Total recorded dwell"
        />
        <StatCard label="Persistent visitors" value={stats.viewers ?? 0} hint="One row per buyer email" />
      </div>

      <div className="mt-6">
        <h3 className="text-base font-semibold">Per-page engagement</h3>
        <p className="mb-3 text-xs text-muted-foreground">
          How long each page held attention across the window, and how many readers it had.
        </p>
        <DwellBars rows={stats.per_page || []} />
      </div>
    </Card>
  )
}

/** The viewers list: one row per buyer email, with First Seen and Last Seen. */
function ViewersList({ rows, onSelect }) {
  if (!rows.length) {
    return (
      <EmptyState
        title="No visitors yet"
        description="A buyer appears here once they have been invited to a room."
      />
    )
  }

  return (
    <Card>
      <h2 className="text-lg font-semibold">Viewers</h2>
      <p className="mb-3 text-xs text-muted-foreground">
        One row per buyer email. A buyer who opens two links is one row here and two view
        events in the list below.
      </p>

      <ul className="divide-y divide-border-subtle">
        {rows.map((row) => (
          <li key={row.id}>
            <button
              type="button"
              onClick={() => onSelect(row)}
              className="flex min-h-11 w-full flex-wrap items-center justify-between gap-3 px-1 py-2 text-left hover:bg-muted"
            >
              <span className="min-w-0">
                <span className="block truncate font-mono text-sm text-foreground">
                  {row.email}
                </span>
                <span className="block font-mono text-xs text-muted-foreground">
                  first seen {formatInstant(row.first_seen)} / last seen{' '}
                  {formatInstant(row.last_seen)}
                </span>
              </span>
              <span className="flex items-center gap-3">
                <span className="font-mono text-xs text-muted-foreground">
                  {row.total_views} view{row.total_views === 1 ? '' : 's'}
                </span>
                <ProofBadge value={row.verification} />
              </span>
            </button>
          </li>
        ))}
      </ul>
    </Card>
  )
}

/** One view's drill-down: page dwell, location and client. */
function ViewDetail({ view }) {
  if (!view) return null
  const location = view.location || {}
  const client = view.client || {}
  const download = view.download || {}

  return (
    <Card>
      <h3 className="text-base font-semibold">View detail</h3>
      <p className="mt-1 font-mono text-xs text-muted-foreground">
        {view.viewer_email || 'anonymous'} / {view.view_type} /{' '}
        {formatInstant(view.viewed_at)}
      </p>

      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <div>
          <h4 className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
            Where
          </h4>
          <p className="font-mono text-sm text-foreground">
            {location.city}, {location.country}
          </p>
          <p className="mt-1 text-xs text-muted-foreground">{view.geography_source || GEOGRAPHY_SOURCE}</p>
        </div>
        <div>
          <h4 className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
            What they used
          </h4>
          <p className="font-mono text-sm text-foreground">
            {client.browser} on {client.os} ({client.device})
          </p>
        </div>
      </div>

      <div className="mt-4">
        <h4 className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
          Download
        </h4>
        <p className="font-mono text-sm text-foreground">
          {download.downloaded ? download.download_type : 'nothing downloaded'}
          {download.downloaded_at ? ` at ${formatInstant(download.downloaded_at)}` : ''}
        </p>
      </div>

      <div className="mt-4">
        <h4 className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
          Time on each page
        </h4>
        <DwellBars rows={(view.page_durations || []).map((page) => ({
          page_number: page.page_number,
          total_duration_seconds: page.duration_seconds,
          viewers: 1,
        }))} />
        <p className="mt-2 font-mono text-xs text-muted-foreground">
          Total {formatDuration(view.total_duration_seconds)}
        </p>
      </div>
    </Card>
  )
}

/**
 * The per-link view list, reverse chronological, anonymous views included.
 *
 * The specification's fourth user-flow step, and the sentence that keeps anonymous
 * views reachable: they are "still reachable per-link via GET /v1/links/{id}/views".
 */
function LinkViews({ rows, onSelect, selectedId }) {
  if (!rows.length) {
    return (
      <EmptyState
        title="No views on this link"
        description="A view appears here the moment a buyer opens the link, addressed or not."
      />
    )
  }

  return (
    <Card>
      <h2 className="text-lg font-semibold">View events</h2>
      <p className="mb-3 text-xs text-muted-foreground">
        Newest first, per link. Views with no buyer address are included and marked.
      </p>

      <ul className="divide-y divide-border-subtle">
        {rows.map((row, index) => (
          <li key={`${row.link_id}-${row.viewed_at}-${index}`}>
            <button
              type="button"
              onClick={() => onSelect(row)}
              className={`flex min-h-11 w-full flex-wrap items-center justify-between gap-3 px-1 py-2 text-left hover:bg-muted ${
                selectedId === row.id ? 'bg-accent-soft' : ''
              }`}
            >
              <span className="min-w-0">
                <span className="block truncate font-mono text-sm text-foreground">
                  {row.viewer_email || 'anonymous'}
                </span>
                <span className="block font-mono text-xs text-muted-foreground">
                  {formatInstant(row.viewed_at)} / {row.location?.country} /{' '}
                  {row.client?.browser}
                </span>
              </span>
              <span className="flex items-center gap-3">
                <span className="font-mono text-xs text-muted-foreground">
                  {formatDuration(row.total_duration_seconds)}
                </span>
                {row.anonymous ? <Badge tone="warning">anonymous</Badge> : null}
                {row.download?.downloaded ? (
                  <Badge tone="info">{row.download.download_type}</Badge>
                ) : null}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </Card>
  )
}

/** Every judgement call this workflow made, with what it rejected. */
function RecordedDecisions({ decisions }) {
  if (!decisions?.length) return null
  return (
    <Card>
      <h2 className="text-lg font-semibold">Recorded decisions</h2>
      <p className="mb-3 text-xs text-muted-foreground">
        The specification asks an implementer to record each derivation rather than assume
        it. These are the questions the research left open and what this build chose.
      </p>
      <ul className="space-y-3">
        {decisions.map((decision) => (
          <li key={decision.id} className="rounded-sm border border-border-subtle p-3">
            <p className="font-mono text-xs text-muted-foreground">{decision.id}</p>
            <p className="text-sm text-foreground">{decision.question}</p>
            <p className="mt-1 text-xs text-muted-foreground">
              Chose <span className="font-mono text-foreground">{decision.chosen}</span>.{' '}
              {decision.rejected_because}
            </p>
          </li>
        ))}
      </ul>
    </Card>
  )
}

export function EngagementReviewPage() {
  const [dataroomId, setDataroomId] = useState('')
  const [bounds, setBounds] = useState({ since: '', until: '' })
  const [linkId, setLinkId] = useState('')
  const [selectedViewId, setSelectedViewId] = useState(null)

  // Blank bounds are dropped by the api wrapper, so an untouched filter asks for
  // everything rather than for a window of the Unix epoch.
  const window = { since: bounds.since || undefined, until: bounds.until || undefined }

  const board = useAsync(
    () => engagementApi.summary(dataroomId || undefined),
    [dataroomId],
  )
  const stats = useAsync(
    () => engagementApi.dataroomStats(dataroomId || 'all', window),
    [dataroomId, bounds.since, bounds.until],
  )
  const visitors = useAsync(() => engagementApi.visitors(undefined, dataroomId || undefined), [dataroomId])
  const views = useAsync(
    () => (linkId ? engagementApi.linkViews(linkId) : Promise.resolve({ views: [] })),
    [linkId],
  )
  const detail = useAsync(
    () => (selectedViewId ? engagementApi.view(selectedViewId) : Promise.resolve(null)),
    [selectedViewId],
  )
  const decisions = useAsync(() => engagementApi.decisions(), [])

  if (board.loading) return <Spinner label="Loading engagement review" />
  if (board.error) return <ErrorNote error={board.error} onRetry={board.refetch} />

  const summary = board.data || {}

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold">Engagement review</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Who engaged, where and for how long. Every timestamp on this page is Unix{' '}
          {TIME_UNIT}.
        </p>
      </header>

      <Card>
        <div className="grid gap-4 lg:grid-cols-3">
          <RoomFilter value={dataroomId} onChange={setDataroomId} />
          <WindowFilter
            since={bounds.since}
            until={bounds.until}
            onChange={(next) => setBounds((current) => ({ ...current, ...next }))}
          />
          <Field label="Link id" hint="Blank reads every view, not one link's." id="wf075-link">
            <input
              id="wf075-link"
              value={linkId}
              onChange={(event) => setLinkId(event.target.value)}
              placeholder="link_a"
              className={`${inputClass} font-mono`}
            />
          </Field>
        </div>
      </Card>

      <Card>
        <h2 className="text-lg font-semibold">This room</h2>
        <p className="mb-3 text-xs text-muted-foreground">
          Two counts that are not versions of each other.
        </p>
        <CountPair visitors={summary.visitors ?? 0} views={summary.views ?? 0} />
        <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard
            label="Verified"
            value={summary.verified ?? 0}
            hint="Identity was proven"
          />
          <StatCard
            label="Not verified"
            value={summary.unverified ?? 0}
            hint="Typed, never proven"
          />
          <StatCard
            label="No proof recorded"
            value={summary.unknown_verification ?? 0}
            hint="No proof step has run"
          />
          <StatCard
            label="Anonymous views"
            value={summary.anonymous_views ?? 0}
            hint="Real views, no buyer address"
          />
        </div>
        {summary.time_spent_seconds !== undefined ? (
          <p className="mt-4 font-mono text-sm text-foreground">
            {formatDuration(summary.time_spent_seconds)} of recorded dwell,{' '}
            {summary.downloads ?? 0} download{summary.downloads === 1 ? '' : 's'}.
          </p>
        ) : null}
      </Card>

      {stats.loading ? (
        <Spinner label="Loading the room aggregate" />
      ) : stats.error ? (
        <ErrorNote error={stats.error} onRetry={stats.refetch} />
      ) : (
        <AggregateBoard stats={stats.data} />
      )}

      {visitors.loading ? (
        <Spinner label="Loading viewers" />
      ) : visitors.error ? (
        <ErrorNote error={visitors.error} onRetry={visitors.refetch} />
      ) : (
        <ViewersList
          rows={visitors.data?.visitors || []}
          onSelect={async (row) => {
            try {
              const full = await engagementApi.visitor(row.id)
              setSelectedViewId(full.views?.[0]?.id || null)
            } catch {
              setSelectedViewId(null)
            }
          }}
        />
      )}

      {linkId ? (
        views.loading ? (
          <Spinner label="Loading view events" />
        ) : views.error ? (
          <ErrorNote error={views.error} onRetry={views.refetch} />
        ) : (
          <LinkViews
            rows={views.data?.views || []}
            selectedId={selectedViewId}
            onSelect={(row) => setSelectedViewId(row.id)}
          />
        )
      ) : null}

      {selectedViewId ? (
        detail.loading ? (
          <Spinner label="Loading the view detail" />
        ) : detail.error ? (
          <ErrorNote error={detail.error} onRetry={detail.refetch} />
        ) : (
          <ViewDetail view={detail.data} />
        )
      ) : null}

      {decisions.data ? <RecordedDecisions decisions={decisions.data.decisions} /> : null}
    </div>
  )
}

export default {
  id: 'wf-075-review-who-engaged-where-and-for-how-long',
  label: 'Engagement review',
  // `audit` is the shared glyph that reads closest to a review board, and it is a name
  // that exists in the shared PATHS map. `components/ui.jsx` is not edited, and no
  // glyph is hand-rolled for an interface icon.
  icon: 'audit',
  order: 750,
  Component: EngagementReviewPage,
}