import { useState } from 'react'

import { Button, Card, EmptyState, ErrorNote, Spinner, useAsync } from '@/components/ui'
import { absoluteTime, relativeTime } from '@/lib/api'

import { trendApi } from './api'
import Glyph, { TREND_ICON } from './icons'
import { BUCKET_TONES, BucketBadge, SortHeader, TrendCell } from './primitives'

/**
 * The Trend column, for WF-021.
 *
 * The page is the researched user flow, in the order the research states it:
 * read the four buckets, filter and sort on Trend plus Last Client View to
 * isolate the rooms worth acting on, then act. So the four buckets are filters
 * rather than decoration, the table's columns are the researched ones, and
 * selecting a row opens the per-room health badge with the arithmetic behind it.
 *
 * Two things on this page are this build's judgement rather than the source's,
 * and both are shown rather than hidden: the volume floors, which the page reads
 * from `/rules` and labels as either the researched default or a local override,
 * and the inferences list, which is rendered from `/inferences` under a heading
 * that says so. A rep should be able to tell which numbers came from Dock's help
 * article and which came from us.
 *
 * Sorting and filtering are server-side, because the classification is computed
 * per room from that room's events and the whole point of the `as_of` parameter
 * is that every row in one view is read at one instant. Doing it in the browser
 * would be faster and would quietly break that.
 */
function TrendHealthPage() {
  const [filters, setFilters] = useState({ trend: '', owner: '', sort: 'trend', order: 'asc' })
  const [selected, setSelected] = useState(null)

  const { data, loading, error, refetch } = useAsync(
    () =>
      Promise.all([
        trendApi.vocabulary(),
        trendApi.rules(),
        trendApi.summary(),
        trendApi.dashboard(filters),
      ]),
    [filters.trend, filters.owner, filters.sort, filters.order],
  )

  const detail = useAsync(
    () => (selected ? trendApi.roomTrend(selected) : Promise.resolve(null)),
    [selected],
  )

  if (loading) return <Spinner label="Loading engagement health" />
  if (error) return <ErrorNote error={error} onRetry={refetch} />

  const [published, rules, summary, dashboard] = data
  const labels = published.trend_labels || {}
  const buckets = summary.buckets || {}
  const rows = dashboard.rows || []

  function onSort(column) {
    setFilters((previous) => ({
      ...previous,
      sort: column,
      // Clicking the column you are already sorted on flips it; clicking a new
      // one starts from its natural order, which for Trend is hottest first.
      order:
        previous.sort === column && previous.order === 'asc'
          ? 'desc'
          : column === 'trend'
            ? 'asc'
            : 'desc',
    }))
  }

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="flex items-center gap-2 text-xl font-semibold text-foreground">
            <Glyph path={TREND_ICON} size={22} className="text-accent" />
            Engagement health
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            One value per workspace, from workspace activity on the researched {rules.rules.windows.hot} /{' '}
            {rules.rules.windows.warm} / {rules.rules.windows.cold}-day windows. The classification recomputes
            on read, so a room decays without anything having to run.
          </p>
        </div>
        <Button icon="refresh" onClick={refetch}>
          Refresh
        </Button>
      </header>

      <section aria-label="Filter by engagement health">
        <div className="flex flex-wrap items-center gap-2">
          <FilterButton
            active={!filters.trend}
            onClick={() => setFilters((p) => ({ ...p, trend: '' }))}
            tone="neutral"
          >
            All rooms ({summary.rooms})
          </FilterButton>
          {published.trend_values.map((value) => (
            <FilterButton
              key={value}
              active={filters.trend === value}
              onClick={() =>
                setFilters((p) => ({ ...p, trend: p.trend === value ? '' : value }))
              }
              tone={BUCKET_TONES[value]}
            >
              {labels[value]} ({buckets[value] ?? 0})
            </FilterButton>
          ))}
        </div>
        <p className="mt-2 text-xs text-muted-foreground">
          Hot and Cold are the two ends of the pipeline: the rooms to protect, and the rooms to re-engage.
        </p>
      </section>

      <section aria-label="The Trend column" className="glass overflow-hidden rounded-xl">
        {rows.length === 0 ? (
          <EmptyState
            title="No rooms match"
            description="Clear the health filter, or record some engagement for a workspace."
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-sm">
              <caption className="sr-only">
                One row per workspace: its engagement health, the engagement behind that value, and the
                last time a client opened it.
              </caption>
              <thead className="border-b border-border-subtle/30">
                <tr>
                  <SortHeader
                    label="Workspace"
                    column="name"
                    sort={dashboard.sort}
                    order={dashboard.order}
                    onSort={onSort}
                  />
                  <SortHeader
                    label="Owner"
                    column="owner"
                    sort={dashboard.sort}
                    order={dashboard.order}
                    onSort={onSort}
                  />
                  <SortHeader
                    label="Trend"
                    column="trend"
                    sort={dashboard.sort}
                    order={dashboard.order}
                    onSort={onSort}
                  />
                  <SortHeader
                    label="Last client view"
                    column="last_client_view"
                    sort={dashboard.sort}
                    order={dashboard.order}
                    onSort={onSort}
                  />
                  <SortHeader
                    label="Events"
                    column="engagement"
                    sort={dashboard.sort}
                    order={dashboard.order}
                    onSort={onSort}
                  />
                  <th scope="col" className="px-3 py-2 text-left font-mono text-xs uppercase">
                    Next change
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr
                    key={row.room_id}
                    className={`border-b border-border-subtle/20 last:border-0 ${
                      selected === row.room_id ? 'bg-muted/40' : ''
                    }`}
                  >
                    <td className="px-3 py-3">
                      <button
                        type="button"
                        onClick={() => setSelected(row.room_id)}
                        aria-expanded={selected === row.room_id}
                        className="min-h-11 rounded-md text-left hover:text-accent
                          focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2
                          focus-visible:ring-offset-background"
                      >
                        <span className="block font-medium text-foreground">{row.name}</span>
                        <span className="block text-xs text-muted-foreground">
                          {row.account ? `${row.account} · ` : ''}
                          {row.stage || 'no stage'}
                        </span>
                      </button>
                    </td>
                    <td className="px-3 py-3 text-muted-foreground">{row.owner || '—'}</td>
                    <td className="px-3 py-3">
                      <TrendCell row={row} />
                    </td>
                    <td className="px-3 py-3">
                      {row.last_client_view ? (
                        <>
                          <span className="block text-foreground">
                            {relativeTime(row.last_client_view)}
                          </span>
                          <span className="block text-xs text-muted-foreground">
                            {absoluteTime(row.last_client_view)}
                          </span>
                        </>
                      ) : (
                        <span className="text-muted-foreground">never</span>
                      )}
                    </td>
                    <td className="px-3 py-3 font-mono text-muted-foreground">
                      {row.events.total}
                      {row.events.internal > 0 && (
                        <span className="ml-1 text-xs">({row.events.internal} internal)</span>
                      )}
                    </td>
                    <td className="px-3 py-3">
                      {row.next_change ? (
                        <>
                          <span className="block">
                            {labels[row.next_change.trend]} in {relativeTime(row.next_change.at).replace(' ago', '')}
                          </span>
                          <span className="block text-xs text-muted-foreground">
                            if nothing happens
                          </span>
                        </>
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {selected && (
        <RoomBadge
          state={detail}
          labels={labels}
          onClose={() => setSelected(null)}
        />
      )}

      <RulesPanel rules={rules} published={published} />
      <InferencesPanel />
    </div>
  )
}

/**
 * A bucket filter.
 *
 * `aria-pressed` rather than a colour, and the count is in the label rather than
 * in a badge beside it, so the control reads the same with no colour, in forced
 * colours mode, and to a screen reader.
 */
function FilterButton({ active, onClick, tone, children }) {
  const activeTone = {
    neutral: 'bg-muted text-foreground border-border-subtle/50',
    insert: 'bg-accent/20 text-accent border-accent/40',
    update: 'bg-sky-500/20 text-sky-300 border-sky-500/40',
    restore: 'bg-amber-500/20 text-amber-300 border-amber-500/40',
  }[tone]

  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={`inline-flex min-h-11 items-center gap-1.5 rounded-lg border px-3 text-sm
        transition-colors duration-200 focus-visible:ring-2 focus-visible:ring-accent
        focus-visible:ring-offset-2 focus-visible:ring-offset-background ${
          active ? activeTone : 'border-transparent bg-muted/50 text-muted-foreground hover:text-foreground'
        }`}
    >
      {children}
    </button>
  )
}

/**
 * One room's health badge: the researched Liferay "Room Trend" widget, with the
 * Dock windows behind it and the decay that follows.
 *
 * The decay ladder is the thing worth showing. The research says a workspace
 * decays through all four states with no new activity, and a rep who can see
 * "Cooling, becomes Cold in 9 days" can act this week rather than reading a
 * value after the fact.
 */
function RoomBadge({ state, labels, onClose }) {
  const { data, loading, error, refetch } = state

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <h2 className="font-mono text-sm font-semibold text-foreground">Room health badge</h2>
        <Button onClick={onClose}>Close</Button>
      </div>
      {loading && <Spinner label="Loading the room badge" />}
      {error && <ErrorNote error={error} onRetry={refetch} />}
      {data && (
        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          <div>
            <BucketBadge value={data.trend} label={data.label} size={20} />
            <p className="mt-2 text-sm text-muted-foreground">{data.rule}</p>
            <ul className="mt-2 flex flex-col gap-1 text-sm text-foreground">
              {(data.reasons || []).map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
            <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
              <dt className="text-muted-foreground">Last engagement</dt>
              <dd>{data.last_engagement_at ? relativeTime(data.last_engagement_at) : 'never'}</dd>
              <dt className="text-muted-foreground">Last client view</dt>
              <dd>{data.last_client_view ? relativeTime(data.last_client_view) : 'never'}</dd>
              <dt className="text-muted-foreground">Events (external / internal)</dt>
              <dd>
                {data.events.external} / {data.events.internal}
              </dd>
            </dl>
          </div>

          <div>
            <h3 className="font-mono text-xs uppercase text-muted-foreground">Windows</h3>
            <ul className="mt-2 flex flex-col gap-1 text-sm">
              {Object.entries(data.windows || {}).map(([key, window]) => (
                <li key={key} className="flex justify-between gap-3">
                  <span className="text-muted-foreground">
                    last {window.days} days ({key})
                  </span>
                  <span className="font-mono">
                    {window.qualifying} counted / {window.events} seen
                  </span>
                </li>
              ))}
            </ul>

            <h3 className="mt-4 font-mono text-xs uppercase text-muted-foreground">
              Decay if nothing happens
            </h3>
            {(data.decay || []).length === 0 ? (
              <p className="mt-2 text-sm text-muted-foreground">
                Nothing left to decay: this workspace cannot move on its own.
              </p>
            ) : (
              <ol className="mt-2 flex flex-col gap-1 text-sm">
                {data.decay.map((step) => (
                  <li key={step.at} className="flex justify-between gap-3">
                    <span className="text-muted-foreground">
                      {relativeTime(step.at).replace(' ago', '')} from now
                    </span>
                    <span className="inline-flex items-center gap-1.5">
                      <span className="font-mono">{labels[step.trend] || step.trend}</span>
                      <Glyph
                        name={BUCKET_TONES[step.trend] ? step.trend : 'trend'}
                        size={14}
                        className="text-muted-foreground"
                      />
                    </span>
                  </li>
                ))}
              </ol>
            )}
            <p className="mt-3 text-xs text-muted-foreground">
              Evaluated at {absoluteTime(data.as_of)} against{' '}
              {data.rules_source === 'override' ? 'a locally retuned model' : 'the researched windows'}.
            </p>
          </div>
        </div>
      )}
    </Card>
  )
}

/**
 * The model in force, and where each number in it came from.
 *
 * The volume floors are the ones a reader is most likely to want to argue with,
 * because the source says "tons" and gives no number. Saying whether they are
 * the researched default or a local override is the difference between a
 * documented choice and an unexplained one.
 */
function RulesPanel({ rules, published }) {
  const floors = rules.rules.min_events

  return (
    <Card>
      <h2 className="font-mono text-sm font-semibold text-foreground">The model in force</h2>
      <p className="mt-1 text-sm text-muted-foreground">
        {rules.source === 'override'
          ? 'Retuned on this deployment. The researched windows and the sourced sentences still apply.'
          : 'The researched windows, and the volume floors this build chose (see the inferences below).'}
      </p>
      <dl className="mt-3 grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
        <dt className="text-muted-foreground">Hot window</dt>
        <dd className="font-mono">{rules.rules.windows.hot} days</dd>
        <dt className="text-muted-foreground">Warm window</dt>
        <dd className="font-mono">{rules.rules.windows.warm} days</dd>
        <dt className="text-muted-foreground">Cold window</dt>
        <dd className="font-mono">{rules.rules.windows.cold} days</dd>
        <dt className="text-muted-foreground">Hot floor</dt>
        <dd className="font-mono">{floors.hot} events</dd>
        <dt className="text-muted-foreground">Warm floor</dt>
        <dd className="font-mono">{floors.warm} events</dd>
        <dt className="text-muted-foreground">Internal activity</dt>
        <dd className="font-mono">
          {rules.rules.count_only_external ? 'recorded, not counted' : 'counted'}
        </dd>
      </dl>
      <div className="mt-4 flex flex-col gap-2">
        {published.trends.map((trend) => (
          <p key={trend.value} className="text-xs text-muted-foreground">
            <span className="font-mono text-foreground">{trend.label}</span> — {trend.rule}
          </p>
        ))}
      </div>
    </Card>
  )
}

/**
 * What the research left open, published as data.
 *
 * A native `<details>`, so it works without JavaScript and is announced correctly
 * by a screen reader. Collapsed by default: it is the reference, not the reading
 * a rep opens the page for.
 */
function InferencesPanel() {
  const { data, loading, error, refetch } = useAsync(() => trendApi.inferences(), [])

  if (loading) return null
  if (error) return <ErrorNote error={error} onRetry={refetch} />

  return (
    <Card>
      <details>
        <summary className="min-h-11 cursor-pointer list-none font-mono text-sm font-semibold text-foreground
          hover:text-accent focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2
          focus-visible:ring-offset-background">
          What the source did not settle ({data.count} judgement calls)
        </summary>
        <p className="mt-3 border-l-2 border-border-subtle/40 pl-3 text-sm text-muted-foreground italic">
          “{data.sourced_quote}”
        </p>
        <div className="mt-4 flex flex-col gap-4">
          {data.inferences.map((entry) => (
            <section key={entry.id} className="border-t border-border-subtle/20 pt-3">
              <h3 className="font-mono text-sm text-foreground">{entry.topic}</h3>
              <p className="mt-1 text-sm text-muted-foreground">{entry.why}</p>
              <p className="mt-1 text-xs text-muted-foreground/80">
                <span className="font-mono">{entry.id}</span> · {entry.change_it}
              </p>
            </section>
          ))}
        </div>
      </details>
    </Card>
  )
}

export default TrendHealthPage
