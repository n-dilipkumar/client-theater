import { useCallback, useEffect, useRef, useState } from 'react'
import { relativeTime, absoluteTime } from '@/lib/api'
import {
  Badge,
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
import { BarChart, ShareBar, TrendBadge } from './charts'
import { analyticsApi } from './api'

/**
 * Analytics: review buyer engagement and prioritise follow-up (WF-006).
 *
 * The flow follows the researched one: open Analytics, see the consolidated
 * pipeline, scope the dashboard to a deal from the drop-down at the top right,
 * then drill into a room for the per-room widgets and its Timeline.
 *
 * Everything on this page is derived on read by the backend from the records a
 * team already keeps, so the numbers here are the same numbers the audit trail
 * and the raw records describe - there is no separate metrics store to drift.
 */

const ALL_ROOMS = ''

/** "1 comment" / "3 comments": the counts come straight from the API. */
const plural = (count, singular, pluralForm = `${singular}s`) =>
  `${count} ${count === 1 ? singular : pluralForm}`

/** A deadline that has already passed reads as overdue, not as "in -27 days". */
function deadlineLabel(deadline) {
  if (!deadline) return ''
  const days = deadline.days_remaining
  if (days === null || days === undefined) return ''
  if (days < 0) return `${deadline.field} ${Math.abs(days)}d ago`
  return `${deadline.field} in ${days}d`
}

/* -------------------------------------------------------------------------- */
/* Small presentational pieces                                              */
/* -------------------------------------------------------------------------- */

function AlertList({ alerts = [] }) {
  if (alerts.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No low-engagement or deadline alerts. Every active room has recent buyer activity.
      </p>
    )
  }
  return (
    <ul className="space-y-2">
      {alerts.map((alert) => (
        <li
          key={`${alert.kind}-${alert.room_id}`}
          className={`flex flex-wrap items-start gap-3 rounded-lg border p-3 ${
            alert.severity === 'high'
              ? 'border-amber-300/40 bg-amber-300/10'
              : 'border-sky-300/30 bg-sky-300/10'
          }`}
        >
          <span className="min-w-0 flex-1 text-sm text-foreground">{alert.message}</span>
          <Badge tone="neutral">{alert.kind}</Badge>
        </li>
      ))}
    </ul>
  )
}

function ActivityFeed({ rows = [], emptyTitle = 'No buyer activity yet' }) {
  if (rows.length === 0) {
    return <p className="text-sm text-muted-foreground">{emptyTitle}</p>
  }
  return (
    <ul className="divide-y divide-border-subtle/15">
      {rows.map((row) => (
        <li key={row.id || `${row.person}-${row.occurred_at}`} className="py-2.5">
          {/* The action is the point of the row, so it gets the full width and
              the identity sits above it rather than competing for it. */}
          <p className="text-sm text-foreground">
            {row.action}
            {row.target && <span className="text-muted-foreground"> · {row.target}</span>}
          </p>
          <p className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-xs text-muted-foreground">
            <span className="truncate">{row.person}</span>
            {row.room_name && <Badge tone="neutral">{row.room_name}</Badge>}
            <time dateTime={row.occurred_at} title={absoluteTime(row.occurred_at)}>
              {relativeTime(row.occurred_at)}
            </time>
          </p>
        </li>
      ))}
    </ul>
  )
}

function DocumentTable({ rows = [], showRoom = false }) {
  if (rows.length === 0) {
    return <p className="text-sm text-muted-foreground">No shared asset has been opened yet.</p>
  }
  const peak = Math.max(1, ...rows.map((row) => row.views))
  return (
    <ul className="space-y-3">
      {rows.map((row) => (
        <li key={`${row.title}-${row.room_name || ''}`}>
          <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
            <span className="min-w-0 truncate text-sm text-foreground">{row.title}</span>
            {showRoom && row.room_name && <Badge tone="neutral">{row.room_name}</Badge>}
          </div>
          <div className="mt-1.5">
            <ShareBar value={row.views} max={peak} />
          </div>
          <p className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 font-mono text-xs text-muted-foreground">
            <span>{plural(row.views, 'view')}</span>
            <span>{plural(row.downloads, 'download')}</span>
            <span>{plural(row.comments, 'comment')}</span>
            <span>avg {row.average_time}</span>
            <span>{plural(row.users_involved, 'user')}</span>
            <span>last {relativeTime(row.last_viewed_at)}</span>
          </p>
        </li>
      ))}
    </ul>
  )
}

function VisitorTable({ rows = [] }) {
  if (rows.length === 0) {
    return <p className="text-sm text-muted-foreground">No visitor has interacted with this room.</p>
  }
  const peak = Math.max(1, ...rows.map((row) => row.actions))
  return (
    <ul className="space-y-3">
      {rows.map((row, index) => (
        <li key={row.person}>
          <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
            <span className="min-w-0 truncate font-mono text-sm text-foreground">
              <span className="mr-2 text-muted-foreground">{index + 1}</span>
              {row.person}
            </span>
            <span className="font-mono text-xs text-muted-foreground">
              {plural(row.actions, 'action')} · {row.view_time} · seen{' '}
              {relativeTime(row.last_seen_at)}
            </span>
          </div>
          <div className="mt-1.5">
            <ShareBar value={row.actions} max={peak} />
          </div>
        </li>
      ))}
    </ul>
  )
}

/* -------------------------------------------------------------------------- */
/* Deal cards: the ranked follow-up list                                     */
/* -------------------------------------------------------------------------- */

function DealCard({ card, selected, onSelect }) {
  const trend = card.trend?.classification
  const priority = card.priority || {}
  const engagement = card.engagement || {}

  return (
    <button
      type="button"
      onClick={() => onSelect(card.room_id)}
      aria-current={selected ? 'true' : undefined}
      // min-w-0 lets this grid item shrink below its min-content width, which
      // a long room name otherwise refuses to do, forcing the whole page to
      // scroll sideways on a phone.
      className={`glass card-hover w-full min-w-0 cursor-pointer rounded-xl p-4 text-left ${
        selected ? 'ring-2 ring-accent' : ''
      }`}
    >
      <div className="flex min-w-0 items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="font-mono text-xs text-muted-foreground">
            #{priority.rank} {card.account ? `· ${card.account}` : ''}
          </p>
          <p className="mt-0.5 truncate font-mono text-sm font-semibold text-foreground">
            {card.name}
          </p>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1.5">
          <TrendBadge classification={trend} />
          {!card.active && <Badge tone="neutral">{card.stage || 'inactive'}</Badge>}
        </div>
      </div>

      <dl className="mt-3 grid min-w-0 grid-cols-3 gap-2 text-xs">
        <div className="min-w-0">
          <dt className="truncate text-muted-foreground">Actions</dt>
          <dd className="truncate font-mono text-foreground">{engagement.actions_in_window ?? 0}</dd>
        </div>
        <div className="min-w-0">
          <dt className="truncate text-muted-foreground">Visitors</dt>
          <dd className="truncate font-mono text-foreground">{engagement.visitors_in_window ?? 0}</dd>
        </div>
        <div className="min-w-0">
          <dt className="truncate text-muted-foreground">View time</dt>
          <dd className="truncate font-mono text-foreground">
            {engagement.view_time_in_window || '—'}
          </dd>
        </div>
      </dl>

      <p className="mt-3 text-sm text-accent">{priority.suggested_action}</p>
      {priority.reasons?.length > 0 && (
        <ul className="mt-1.5 flex min-w-0 flex-wrap gap-1.5">
          {priority.reasons.slice(0, 3).map((reason) => (
            <li
              key={reason}
              className="max-w-full rounded-md bg-muted px-2 py-0.5 font-mono text-xs break-words text-muted-foreground"
            >
              {reason}
            </li>
          ))}
        </ul>
      )}
      <p className="mt-2.5 font-mono text-xs text-muted-foreground">
        Priority {priority.score}
        {deadlineLabel(card.deadline) && ` · ${deadlineLabel(card.deadline)}`}
        {card.alerts?.length ? ` · ${plural(card.alerts.length, 'alert')}` : ''}
      </p>
    </button>
  )
}

/* -------------------------------------------------------------------------- */
/* Tabs (Timeline is a sourced tab of the room view)                        */
/* -------------------------------------------------------------------------- */

const ROOM_TABS = [
  { id: 'engagement', label: 'Engagement' },
  { id: 'timeline', label: 'Timeline' },
]

function TabList({ tabs, active, onChange, label }) {
  const refs = useRef({})

  const move = (delta) => {
    const index = tabs.findIndex((tab) => tab.id === active)
    const next = tabs[(index + delta + tabs.length) % tabs.length]
    onChange(next.id)
    refs.current[next.id]?.focus()
  }

  return (
    <div
      role="tablist"
      aria-label={label}
      className="flex gap-1 border-b border-border-subtle/25"
      onKeyDown={(event) => {
        if (event.key === 'ArrowRight') {
          event.preventDefault()
          move(1)
        } else if (event.key === 'ArrowLeft') {
          event.preventDefault()
          move(-1)
        }
      }}
    >
      {tabs.map((tab) => (
        <button
          key={tab.id}
          type="button"
          role="tab"
          id={`tab-${tab.id}`}
          aria-selected={tab.id === active}
          aria-controls={`panel-${tab.id}`}
          tabIndex={tab.id === active ? 0 : -1}
          ref={(node) => {
            refs.current[tab.id] = node
          }}
          onClick={() => onChange(tab.id)}
          className={`-mb-px min-h-11 cursor-pointer border-b-2 px-4 text-sm transition-colors duration-150 ${
            tab.id === active
              ? 'border-accent text-accent'
              : 'border-transparent text-muted-foreground hover:text-foreground'
          }`}
        >
          {tab.label}
        </button>
      ))}
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* Timeline panel                                                            */
/* -------------------------------------------------------------------------- */

function TimelinePanel({ roomId, entries = [], onAdded }) {
  const [summary, setSummary] = useState('')
  const [author, setAuthor] = useState('')
  const [state, setState] = useState({ saving: false, error: null })

  const submit = async (event) => {
    event.preventDefault()
    if (!summary.trim()) return
    setState({ saving: true, error: null })
    try {
      await analyticsApi.addTimelineNote(
        roomId,
        { summary: summary.trim(), ...(author.trim() ? { actor: author.trim() } : {}) },
        { actor: author.trim() || 'you' },
      )
      setSummary('')
      setState({ saving: false, error: null })
      onAdded()
    } catch (error) {
      setState({ saving: false, error })
    }
  }

  return (
    <div className="space-y-4">
      <form onSubmit={submit} className="space-y-3 rounded-lg border border-border-subtle/25 p-4">
        <Field
          id="timeline-summary"
          label="Record an update"
          hint="Appended to the room's chronological log and written to the audit trail."
        >
          <textarea
            id="timeline-summary"
            value={summary}
            onChange={(event) => setSummary(event.target.value)}
            rows={2}
            placeholder="Called procurement, agreed a technical deep-dive for Thursday."
            className={inputClass}
          />
        </Field>
        <Field id="timeline-author" label="Author" hint="Optional. Defaults to the recorded actor.">
          <input
            id="timeline-author"
            value={author}
            onChange={(event) => setAuthor(event.target.value)}
            className={inputClass}
            placeholder="dana"
          />
        </Field>
        {state.error && <ErrorNote error={state.error} />}
        <Button type="submit" variant="primary" icon="plus" disabled={state.saving || !summary.trim()}>
          {state.saving ? 'Saving' : 'Add to timeline'}
        </Button>
      </form>

      <div>
        <h3 className="font-mono text-sm font-semibold tracking-wide uppercase">Chronological log</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          Internal updates and buyer actions, newest first.
        </p>
        {entries.length === 0 ? (
          <div className="mt-3">
            <EmptyState title="Nothing logged yet" description="Add the first update above." />
          </div>
        ) : (
          <ol className="mt-3 space-y-0">
            {entries.map((entry) => (
              <li key={entry.id} className="flex gap-3 py-3">
                <span
                  className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-accent"
                  aria-hidden="true"
                />
                <div className="min-w-0 flex-1">
                  <p className="text-sm text-foreground">{entry.summary}</p>
                  <p className="mt-0.5 flex flex-wrap items-center gap-2 font-mono text-xs text-muted-foreground">
                    <Badge tone="neutral">{entry.kind}</Badge>
                    <span>{entry.actor}</span>
                    <time dateTime={entry.at} title={absoluteTime(entry.at)}>
                      {relativeTime(entry.at)}
                    </time>
                  </p>
                </div>
              </li>
            ))}
          </ol>
        )}
      </div>
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* Connection gate                                                          */
/* -------------------------------------------------------------------------- */

function ConnectPanel({ onConnected }) {
  const [token, setToken] = useState('')
  const [environment, setEnvironment] = useState('')
  const [state, setState] = useState({ saving: false, error: null })

  const submit = async (event) => {
    event.preventDefault()
    if (!token.trim()) return
    setState({ saving: true, error: null })
    try {
      await analyticsApi.updateConfig({
        connection: { token: token.trim(), environment: environment.trim() },
      })
      setState({ saving: false, error: null })
      onConnected()
    } catch (error) {
      setState({ saving: false, error })
    }
  }

  return (
    <Card>
      <h2 className="font-mono text-sm font-semibold tracking-wide uppercase">
        Connect the analytics environment
      </h2>
      <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
        Engagement metrics are read from a connected analytics environment, so every widget on
        this page stays empty until a token is present. Paste the environment token to connect;
        the write is recorded in the audit trail like any other change.
      </p>
      <form onSubmit={submit} className="mt-4 grid gap-3 sm:grid-cols-2">
        <Field id="connect-token" label="Environment token" hint="Required. Presence is what connects.">
          <input
            id="connect-token"
            value={token}
            onChange={(event) => setToken(event.target.value)}
            className={inputClass}
            autoComplete="off"
            spellCheck="false"
          />
        </Field>
        <Field id="connect-environment" label="Environment" hint="Optional label, e.g. prod.">
          <input
            id="connect-environment"
            value={environment}
            onChange={(event) => setEnvironment(event.target.value)}
            className={inputClass}
            autoComplete="off"
          />
        </Field>
        {state.error && (
          <div className="sm:col-span-2">
            <ErrorNote error={state.error} />
          </div>
        )}
        <div className="sm:col-span-2">
          <Button type="submit" variant="primary" icon="database" disabled={state.saving || !token.trim()}>
            {state.saving ? 'Connecting' : 'Connect'}
          </Button>
        </div>
      </form>
    </Card>
  )
}

/* -------------------------------------------------------------------------- */
/* Page                                                                     */
/* -------------------------------------------------------------------------- */

export default function Engagement() {
  const [roomId, setRoomId] = useState(ALL_ROOMS)
  const [grain, setGrain] = useState('day')
  const [tab, setTab] = useState('engagement')
  const [nonce, setNonce] = useState(0)

  const overview = useAsync(
    () => analyticsApi.overview({ roomId, grain }),
    [roomId, grain, nonce],
  )
  const detail = useAsync(
    () => (roomId ? analyticsApi.room(roomId, { grain }) : Promise.resolve(null)),
    [roomId, grain, nonce],
  )

  const refetch = useCallback(() => setNonce((n) => n + 1), [])

  // A new deal is a new drill-down, so the tab resets rather than leaving the
  // previous room's Timeline open under the new room's heading.
  useEffect(() => setTab('engagement'), [roomId])

  const notConnected = overview.error?.status === 428

  if (notConnected) return <ConnectPanel onConnected={refetch} />

  if (overview.loading && !overview.data) return <Spinner label="Loading engagement" />
  if (overview.error) return <ErrorNote error={overview.error} onRetry={overview.refetch} />

  const data = overview.data || {}
  const totals = data.totals || {}
  const rooms = data.rooms || []

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="font-mono text-2xl font-semibold">Analytics</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Buyer engagement across the pipeline, and what to follow up on next.
          </p>
        </div>

        <div className="flex flex-wrap items-end gap-3">
          <Field id="scope-room" label="Scope" hint="Defaults to all deals.">
            <select
              id="scope-room"
              value={roomId}
              onChange={(event) => setRoomId(event.target.value)}
              className={`${inputClass} min-w-56`}
            >
              <option value={ALL_ROOMS}>All Rooms</option>
              {rooms.map((card) => (
                <option key={card.room_id} value={card.room_id}>
                  {card.name}
                </option>
              ))}
            </select>
          </Field>
          <Field id="scope-grain" label="Chart grain" hint="Visit frequency by day or week.">
            <select
              id="scope-grain"
              value={grain}
              onChange={(event) => setGrain(event.target.value)}
              className={inputClass}
            >
              <option value="day">Per day</option>
              <option value="week">Per week</option>
            </select>
          </Field>
        </div>
      </header>

      <section aria-label="Key metrics" className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Total active deals"
          value={data.total_active_deals ?? 0}
          icon="rooms"
          hint={data.scope?.label}
        />
        <StatCard
          label="Buyer actions"
          value={totals.actions ?? 0}
          icon="analytics"
          hint="In the engagement window"
        />
        <StatCard
          label="View time viewed"
          value={totals.view_time || '0s'}
          icon="dashboard"
          hint={`${plural(totals.visitors ?? 0, 'visitor')} · ${plural(totals.visits ?? 0, 'visit')}`}
        />
        <StatCard
          label="Open alerts"
          value={totals.alerts ?? 0}
          icon="audit"
          hint={`${totals.hot ?? 0} hot · ${totals.warm ?? 0} warm · ${totals.cold ?? 0} cold`}
        />      </section>

      <Card>
        <h2 className="font-mono text-sm font-semibold tracking-wide uppercase">
          Alerts for rooms with low engagement or approaching deadlines
        </h2>
        <div className="mt-3">
          <AlertList alerts={data.alerts} />
        </div>
      </Card>

      <section aria-label="Prioritised follow-up" className="space-y-3">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="font-mono text-sm font-semibold tracking-wide uppercase">
            Follow-up priority
          </h2>
          <p className="text-xs text-muted-foreground">
            Ranked by engagement, deadline pressure, and open alerts. Select a deal to drill down.
          </p>
        </div>
        {rooms.length === 0 ? (
          <EmptyState
            title="No sales rooms yet"
            description="Create a room and its buyer activity will be ranked here automatically."
          />
        ) : (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {rooms.map((card) => (
              <DealCard
                key={card.room_id}
                card={card}
                selected={card.room_id === roomId}
                onSelect={(id) => setRoomId(id === roomId ? ALL_ROOMS : id)}
              />
            ))}
          </div>
        )}
      </section>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card>
          <h2 className="font-mono text-sm font-semibold tracking-wide uppercase">Recent engagement</h2>
          <div className="mt-3">
            <BarChart
              points={data.recent_engagement}
              metric="actions"
              label="Buyer actions per bucket"
              tone="hot"
              grain={data.grain}
            />
          </div>
        </Card>
        <Card>
          <h2 className="font-mono text-sm font-semibold tracking-wide uppercase">Visit frequency</h2>
          <div className="mt-3">
            <BarChart
              points={data.visit_frequency}
              metric="visits"
              label="Visits per bucket"
              tone="warm"
              grain={data.grain}
            />
          </div>
        </Card>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card>
          <h2 className="font-mono text-sm font-semibold tracking-wide uppercase">
            Most engaged documents
          </h2>
          <div className="mt-3">
            <DocumentTable rows={data.most_engaged_documents} showRoom={!roomId} />
          </div>
        </Card>
        <Card>
          <h2 className="font-mono text-sm font-semibold tracking-wide uppercase">Recent buyer activity</h2>
          <div className="mt-3">
            <ActivityFeed rows={data.latest_activity} />
          </div>
        </Card>
      </div>

      {roomId && (
        <section aria-label="Room drill-down" className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="font-mono text-sm font-semibold tracking-wide uppercase">
              Room engagement
            </h2>
            <Button icon="close" onClick={() => setRoomId(ALL_ROOMS)}>
              Back to all rooms
            </Button>
          </div>

          {detail.loading ? (
            <Spinner label="Loading room engagement" />
          ) : detail.error ? (
            <ErrorNote error={detail.error} onRetry={detail.refetch} />
          ) : detail.data ? (
            <RoomDetail
              data={detail.data}
              tab={tab}
              setTab={setTab}
              onTimelineChange={refetch}
            />
          ) : null}
        </section>
      )}
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* Per-room drill-down                                                      */
/* -------------------------------------------------------------------------- */

function RoomDetail({ data, tab, setTab, onTimelineChange }) {
  const room = data.room || {}
  const stats = data.room_stats || {}
  const trend = data.room_trend || {}

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className="font-mono text-lg font-semibold text-foreground">{room.name}</h3>
            <p className="mt-1 text-sm text-muted-foreground">
              {[room.account, room.stage, room.owner && `owner ${room.owner}`]
                .filter(Boolean)
                .join(' · ')}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <TrendBadge classification={trend.classification}>
              Room Trend: {trend.classification}
            </TrendBadge>
            {data.deadline && <Badge tone="neutral">{deadlineLabel(data.deadline)}</Badge>}
          </div>
        </div>
        {trend.reasons?.length > 0 && (
          <ul className="mt-3 flex flex-wrap gap-1.5">
            {trend.reasons.map((reason) => (
              <li
                key={reason}
                className="rounded-md bg-muted px-2 py-0.5 font-mono text-xs text-muted-foreground"
              >
                {reason}
              </li>
            ))}
          </ul>
        )}
        <AlertList alerts={data.alerts} />
      </Card>

      <section aria-label="Room stats" className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="View time viewed" value={stats.view_time || '0s'} icon="dashboard" />
        <StatCard label="Total visits" value={stats.total_visits ?? 0} icon="analytics" />
        <StatCard label="Visitors" value={stats.visitors ?? 0} icon="rooms" />
        <StatCard label="Actions" value={stats.actions ?? 0} icon="audit" />
      </section>

      <TabList tabs={ROOM_TABS} active={tab} onChange={setTab} label="Room views" />

      {tab === 'engagement' ? (
        <div
          role="tabpanel"
          id="panel-engagement"
          aria-labelledby="tab-engagement"
          className="space-y-5"
        >
          <div className="grid gap-5 lg:grid-cols-2">
            <Card>
              <h4 className="font-mono text-sm font-semibold tracking-wide uppercase">
                Most active visitors
              </h4>
              <p className="mt-1 text-xs text-muted-foreground">
                Individuals ranked by their total actions.
              </p>
              <div className="mt-3">
                <VisitorTable rows={data.most_active_visitors} />
              </div>
            </Card>
            <Card>
              <h4 className="font-mono text-sm font-semibold tracking-wide uppercase">
                Most engaged documents
              </h4>
              <p className="mt-1 text-xs text-muted-foreground">
                Views, downloads, average time, users involved, last viewed.
              </p>
              <div className="mt-3">
                <DocumentTable rows={data.most_engaged_documents} />
              </div>
            </Card>
          </div>

          <div className="grid gap-5 lg:grid-cols-2">
            <Card>
              <h4 className="font-mono text-sm font-semibold tracking-wide uppercase">
                Recent engagement
              </h4>
              <div className="mt-3">
                <BarChart
                  points={data.recent_engagement}
                  metric="actions"
                  label="Buyer actions per bucket"
                  tone="hot"
                  grain={data.grain}
                />
              </div>
            </Card>
            <Card>
              <h4 className="font-mono text-sm font-semibold tracking-wide uppercase">
                Visit frequency
              </h4>
              <div className="mt-3">
                <BarChart
                  points={data.visit_frequency}
                  metric="visits"
                  label="Visits per bucket"
                  tone="warm"
                  grain={data.grain}
                />
              </div>
            </Card>
          </div>

          <Card>
            <h4 className="font-mono text-sm font-semibold tracking-wide uppercase">Latest activity</h4>
            <div className="mt-3">
              <ActivityFeed rows={data.latest_activity} />
            </div>
          </Card>
        </div>
      ) : (
        <div role="tabpanel" id="panel-timeline" aria-labelledby="tab-timeline">
          <Card>
            <TimelinePanel
              roomId={room.id}
              entries={data.timeline}
              onAdded={onTimelineChange}
            />
          </Card>
        </div>
      )}
    </div>
  )
}
