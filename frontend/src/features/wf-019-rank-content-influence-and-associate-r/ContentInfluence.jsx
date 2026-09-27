import { useCallback, useMemo, useState } from 'react'

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
import { absoluteTime, relativeTime } from '@/lib/api'

import { contentFilters, influenceApi } from './api'
import { Notice, Segmented, SortableTh, TrendChart } from './primitives'

/**
 * WF-019: Content Influence.
 *
 * The page follows the researched user flow in order, because the research
 * describes a screen and the order is the screen:
 *
 *   1. portfolio metrics - number of assets, content shares, content client
 *      views, utilization rate, engagement rate;
 *   2. content engagement over time, at day / week / month / quarter / year;
 *   3. Top content, sorted by any column;
 *   4. Content & Sales Influence - revenue and deals per asset;
 *   5. filters by collection, a client-activity range and a shares range, which
 *      sit at the top because they scope everything below them.
 *
 * Three things this page is careful about, because each is easy to get wrong:
 *
 * 1. **A rate with no denominator is not shown as a number.** The research
 *    defines both rates but not the population, so the API sends the numerator
 *    and the asset count alongside each rate and the hint under the tile names
 *    the denominator. A bare "0%" would send someone to go and fix content that
 *    is fine.
 * 2. **Content & Sales Influence is a panel, not an error.** The research says
 *    the report is "shown assuming you have integrated with your CRM", so a
 *    missing link renders as a checklist of what to connect, with each item's
 *    remedy, rather than as a red box.
 * 3. **The evidence is on the page.** Each associated asset carries the views,
 *    shares and last activity *in that workspace* that justify its revenue,
 *    because the report's claim is that content influenced revenue and a number
 *    with nothing behind it cannot be checked.
 */

const GRAINS = [
  { value: 'day', label: 'Day' },
  { value: 'week', label: 'Week' },
  { value: 'month', label: 'Month' },
  { value: 'quarter', label: 'Quarter' },
  { value: 'year', label: 'Year' },
]

const SERIES = ['shares', 'views', 'downloads']

const EMPTY_FILTERS = {
  collection: '',
  activityFrom: '',
  activityTo: '',
  sharedFrom: '',
  sharedTo: '',
}

function percent(value) {
  return value === null || value === undefined ? '—' : `${value.toFixed(2)}%`
}

function duration(seconds) {
  const total = Math.max(0, Number(seconds) || 0)
  if (total < 60) return `${total}s`
  const minutes = Math.floor(total / 60)
  if (minutes < 60) return `${minutes}m`
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`
}

function money(amount, currency) {
  if (amount === null || amount === undefined) return '—'
  const formatted = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }).format(amount)
  return currency ? `${currency} ${formatted}` : formatted
}

function MetricTiles({ metrics }) {
  const denominator = `${metrics.assets_in_scope} asset${metrics.assets_in_scope === 1 ? '' : 's'} in scope`
  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
      <StatCard label="Number of assets" value={metrics.number_of_assets} hint={denominator} icon="database" />
      <StatCard label="Content shares" value={metrics.content_shares} hint="Internal shares recorded" />
      <StatCard label="Content client views" value={metrics.content_client_views} hint="External views recorded" />
      <StatCard
        label="Utilization rate"
        value={percent(metrics.utilization_rate)}
        hint={`${metrics.utilized_assets} of ${denominator} shared at least once`}
      />
      <StatCard
        label="Engagement rate"
        value={percent(metrics.engagement_rate)}
        hint={`${metrics.engaged_assets} of ${denominator} viewed at least once`}
      />
      <StatCard
        label="Workspaces engaged"
        value={metrics.workspaces_engaged}
        hint={`${duration(metrics.total_time_seconds)} on content in total`}
      />
    </div>
  )
}

function FilterBar({ state, onChange, collections, rooms, onReset }) {
  return (
    <Card>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <Field label="Collection" id="wf019-collection" hint="The research's collection filter.">
          <select
            id="wf019-collection"
            className={inputClass}
            value={state.collection}
            onChange={(event) => onChange({ collection: event.target.value })}
          >
            <option value="">All collections</option>
            {collections.map((entry) => (
              <option key={entry.collection} value={entry.collection}>
                {entry.collection} ({entry.assets})
              </option>
            ))}
          </select>
        </Field>

        <Field label="Workspace" id="wf019-room" hint="One workspace, or the whole portfolio.">
          <select
            id="wf019-room"
            className={inputClass}
            value={state.roomId}
            onChange={(event) => onChange({ roomId: event.target.value })}
          >
            <option value="">All workspaces</option>
            {rooms.map((room) => (
              <option key={room.id} value={room.id}>
                {room.data?.name || room.id}
              </option>
            ))}
          </select>
        </Field>

        <Field label="Client activity from" id="wf019-activity-from" hint="Bounds views and downloads only.">
          <input
            id="wf019-activity-from"
            type="date"
            className={inputClass}
            value={state.activityFrom}
            onChange={(event) => onChange({ activityFrom: event.target.value })}
          />
        </Field>

        <Field label="Client activity to" id="wf019-activity-to" hint="Inclusive.">
          <input
            id="wf019-activity-to"
            type="date"
            className={inputClass}
            value={state.activityTo}
            onChange={(event) => onChange({ activityTo: event.target.value })}
          />
        </Field>

        <Field label="Shares from" id="wf019-shared-from" hint="Bounds shares only, not views.">
          <input
            id="wf019-shared-from"
            type="date"
            className={inputClass}
            value={state.sharedFrom}
            onChange={(event) => onChange({ sharedFrom: event.target.value })}
          />
        </Field>

        <Field label="Shares to" id="wf019-shared-to" hint="Inclusive.">
          <input
            id="wf019-shared-to"
            type="date"
            className={inputClass}
            value={state.sharedTo}
            onChange={(event) => onChange({ sharedTo: event.target.value })}
          />
        </Field>
      </div>

      <div className="mt-4 flex flex-wrap items-end justify-between gap-3 border-t border-border-subtle/30 pt-4">
        <Segmented
          label="Trend grain"
          name="wf019-grain"
          value={state.grain}
          options={GRAINS}
          onChange={(grain) => onChange({ grain })}
          hint="The research's five grains: day, week, month, quarter, year."
        />
        <Button icon="refresh" onClick={onReset}>
          Clear filters
        </Button>
      </div>
    </Card>
  )
}

function TopContent({ body, sort, direction, onSort, onLoadEvent }) {
  if (!body.rows.length) {
    return (
      <EmptyState
        title="No content in scope"
        description="Nothing in the library matches these filters. Widen the collection or clear the time ranges."
      />
    )
  }
  return (
    <>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[52rem] border-collapse text-sm">
          <caption className="sr-only">
            Top content, {body.total} assets, sorted by {sort} {direction}.
          </caption>
          <thead className="border-b border-border-subtle/40">
            <tr>
              <SortableTh column="title" label="Asset" sort={sort} direction={direction} onSort={onSort} />
              <SortableTh column="shares" label="Shares" sort={sort} direction={direction} onSort={onSort} numeric />
              <SortableTh column="views" label="Client views" sort={sort} direction={direction} onSort={onSort} numeric />
              <SortableTh
                column="downloads"
                label="Downloads"
                sort={sort}
                direction={direction}
                onSort={onSort}
                numeric
              />
              <SortableTh
                column="total_time_seconds"
                label="Time on content"
                sort={sort}
                direction={direction}
                onSort={onSort}
                numeric
              />
              <SortableTh
                column="last_share_at"
                label="Last share"
                sort={sort}
                direction={direction}
                onSort={onSort}
              />
              <SortableTh
                column="last_view_at"
                label="Last view"
                sort={sort}
                direction={direction}
                onSort={onSort}
              />
              <th scope="col" className="px-3 py-2" />
            </tr>
          </thead>
          <tbody>
            {body.rows.map((row) => (
              <tr key={row.asset_id} className="border-b border-border-subtle/20 last:border-0">
                <th scope="row" className="px-3 py-2 text-left font-normal">
                  <span className="font-medium text-foreground">{row.title}</span>
                  <span className="mt-0.5 flex flex-wrap gap-1">
                    {row.kind && <Badge>{row.kind}</Badge>}
                    {row.collections.slice(0, 2).map((name) => (
                      <Badge key={name}>{name}</Badge>
                    ))}
                    {row.workspaces > 0 && <Badge tone="insert">{row.workspaces} rooms</Badge>}
                  </span>
                </th>
                <td className="px-3 py-2 text-right font-mono">{row.shares}</td>
                <td className="px-3 py-2 text-right font-mono">
                  {row.views}
                  {row.internal_views > 0 && (
                    <span className="ml-1 text-muted-foreground">+{row.internal_views} internal</span>
                  )}
                </td>
                <td className="px-3 py-2 text-right font-mono">{row.downloads}</td>
                <td className="px-3 py-2 text-right font-mono">{duration(row.total_time_seconds)}</td>
                <td className="px-3 py-2 text-muted-foreground">
                  {row.last_share_at ? relativeTime(row.last_share_at) : '—'}
                </td>
                <td className="px-3 py-2 text-muted-foreground">
                  {row.last_view_at ? relativeTime(row.last_view_at) : '—'}
                </td>
                <td className="px-3 py-2 text-right">
                  <Button variant="ghost" onClick={() => onLoadEvent(row)}>
                    Evidence
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-3 text-xs text-muted-foreground">
        Showing {body.count} of {body.total}. Assets nothing has been done with are still listed: what is not
        used is the half of the report a content owner needs.
      </p>
    </>
  )
}

function SalesInfluence({ body }) {
  if (!body.available) {
    return (
      <div className="space-y-3">
        <Notice tone="warn" title="Content & Sales Influence needs its CRM links before it can report">
          The research states this report is shown "assuming you have integrated with your CRM, and have
          connected accounts and deals to workspaces". Each missing link is named below, with what to do
          about it.
        </Notice>
        <ul className="space-y-2">
          {body.blockers.map((blocker) => (
            <li key={blocker.code} className="rounded-lg border border-border-subtle/40 p-3 text-sm">
              <p className="font-mono text-xs text-accent">{blocker.code}</p>
              <p className="mt-1 text-foreground">{blocker.detail}</p>
              <p className="mt-1 text-muted-foreground">{blocker.remedy}</p>
            </li>
          ))}
        </ul>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="Revenue attributed to content"
          value={money(body.totals.revenue, body.currency)}
          hint={`of ${money(body.totals.deal_revenue, body.currency)} across the connected deals`}
        />
        <StatCard label="Won revenue attributed" value={money(body.totals.won_revenue, body.currency)} />
        <StatCard label="Open revenue attributed" value={money(body.totals.open_revenue, body.currency)} />
        <StatCard
          label="Influenced assets"
          value={body.totals.influenced_assets}
          hint={`across ${body.totals.influenced_workspaces} workspace(s)`}
        />
      </div>

      {body.revenue_mixed_currencies && (
        <Notice tone="warn" title="Revenue is not summed across currencies">
          No source documents a conversion rate, so this report refuses to invent one. Per currency:{' '}
          {Object.entries(body.revenue_by_currency)
            .map(([code, amount]) => `${code} ${money(amount)}`)
            .join(', ')}
          .
        </Notice>
      )}

      {body.assets.length === 0 ? (
        <EmptyState
          title="No revenue could be attributed to any asset"
          description="The CRM links exist, but no deal names a library asset that was actually shared with or viewed by someone in that deal's workspace."
        />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[48rem] border-collapse text-sm">
            <caption className="sr-only">
              Revenue attributed to each asset, with the evidence inside the deal&rsquo;s workspace.
            </caption>
            <thead className="border-b border-border-subtle/40">
              <tr>
                <th scope="col" className="px-3 py-2 text-left text-xs font-medium uppercase text-muted-foreground">
                  Asset
                </th>
                <th scope="col" className="px-3 py-2 text-right text-xs font-medium uppercase text-muted-foreground">
                  Revenue
                </th>
                <th scope="col" className="px-3 py-2 text-right text-xs font-medium uppercase text-muted-foreground">
                  Deals
                </th>
                <th scope="col" className="px-3 py-2 text-left text-xs font-medium uppercase text-muted-foreground">
                  Evidence in the workspace
                </th>
              </tr>
            </thead>
            <tbody>
              {body.assets.map((entry) => (
                <tr key={entry.asset_id} className="border-b border-border-subtle/20 align-top last:border-0">
                  <th scope="row" className="px-3 py-2 text-left font-medium text-foreground">
                    {entry.title}
                  </th>
                  <td className="px-3 py-2 text-right font-mono">
                    {money(entry.revenue_by_currency[Object.keys(entry.revenue_by_currency)[0]], Object.keys(entry.revenue_by_currency)[0])}
                  </td>
                  <td className="px-3 py-2 text-right font-mono">{entry.deal_count}</td>
                  <td className="px-3 py-2">
                    {entry.deals.map((deal) => (
                      <div key={`${deal.deal_id}-${deal.asset_id}`} className="mb-1 last:mb-0">
                        <p className="text-foreground">
                          {deal.deal}
                          {deal.won && <span className="ml-2 text-accent">won</span>}
                        </p>
                        <p className="text-xs text-muted-foreground">
                          {deal.room}: {deal.evidence.views} views, {deal.evidence.shares} shares,{' '}
                          {deal.evidence.downloads} downloads
                          {deal.evidence.last_activity_at &&
                            `, last ${relativeTime(deal.evidence.last_activity_at)}`}
                        </p>
                      </div>
                    ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {body.unassociated_links.length > 0 && (
        <Notice tone="warn" title={`${body.unassociated_links.length} link(s) could not be attributed`}>
          A deal names an asset, but that asset has no share, view or download recorded in the deal&rsquo;s own
          workspace. This report does not infer influence, so no revenue is attributed.
          <ul className="mt-2 space-y-1">
            {body.unassociated_links.map((entry) => (
              <li key={`${entry.deal_id}-${entry.asset_id}`} className="text-sm">
                <span className="font-medium text-foreground">
                  {entry.deal} &rarr; {entry.asset_title}
                </span>
                <span className="block text-xs text-muted-foreground">{entry.reason}</span>
              </li>
            ))}
          </ul>
        </Notice>
      )}

      {body.unattributed_workspaces.length > 0 && (
        <Notice tone="warn" title={`${body.unattributed_workspaces.length} workspace(s) have content but no asset named`}>
          Content was shared and viewed in these workspaces, but the deal attached to them names no asset, so
          no revenue is attributed to any of it.
          <ul className="mt-2 space-y-1">
            {body.unattributed_workspaces.map((entry) => (
              <li key={entry.deal_id} className="text-sm">
                <span className="font-medium text-foreground">
                  {entry.room} &rarr; {entry.deal}
                </span>
                <span className="block text-xs text-muted-foreground">
                  Engaged with: {entry.assets_engaged.join(', ') || 'nothing attributable'}
                </span>
              </li>
            ))}
          </ul>
        </Notice>
      )}
    </div>
  )
}

function InferencePanel({ body }) {
  const [open, setOpen] = useState(false)
  const unsourced = body.inferences.filter((entry) => entry.basis.startsWith('inference'))
  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-foreground">What this report inferred</h2>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            {body.count} decisions shape these numbers. {unsourced.length} of them the researched sources say
            nothing about at all, and those are the ones worth disagreeing with.
          </p>
        </div>
        <Button variant="ghost" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
          {open ? 'Hide' : 'Show'} the {body.count} decisions
        </Button>
      </div>
      {open && (
        <ul className="mt-4 space-y-3 border-t border-border-subtle/30 pt-4">
          {body.inferences.map((entry) => (
            <li key={entry.id} className="rounded-lg border border-border-subtle/40 p-3 text-sm">
              <p className="font-mono text-xs text-accent">{entry.id}</p>
              <p className="mt-1 font-medium text-foreground">{entry.question}</p>
              <p className="mt-1 text-muted-foreground">{entry.decision}</p>
              <p className="mt-2 text-xs text-muted-foreground/80">
                <span className="text-foreground/70">Basis: </span>
                {entry.basis}
              </p>
              <p className="mt-1 text-xs text-muted-foreground/80">
                <span className="text-foreground/70">Changeable by: </span>
                {entry.changeable_by}
              </p>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

function AssetEvidence({ detail, onClose }) {
  if (!detail) return null
  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-foreground">{detail.asset.title}</h3>
          <p className="mt-1 text-sm text-muted-foreground">
            {detail.asset.shares} shares, {detail.asset.views} client views,{' '}
            {duration(detail.asset.total_time_seconds)} on content.
          </p>
        </div>
        <Button variant="ghost" icon="close" onClick={onClose}>
          Close
        </Button>
      </div>
      <div className="mt-3 overflow-x-auto">
        <table className="w-full min-w-[36rem] border-collapse text-sm">
          <caption className="sr-only">Every recorded occurrence of this asset.</caption>
          <thead className="border-b border-border-subtle/40">
            <tr>
              {['When', 'Action', 'Audience', 'Workspace', 'Person', 'Seconds'].map((label) => (
                <th
                  key={label}
                  scope="col"
                  className="px-2 py-2 text-left text-xs font-medium uppercase text-muted-foreground"
                >
                  {label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {detail.events.map((event) => (
              <tr key={event.id} className="border-b border-border-subtle/20 last:border-0">
                <td className="px-2 py-2" title={absoluteTime(event.at)}>
                  {relativeTime(event.at)}
                </td>
                <td className="px-2 py-2 font-mono text-xs">{event.action}</td>
                <td className="px-2 py-2 font-mono text-xs">{event.audience}</td>
                <td className="px-2 py-2 font-mono text-xs text-muted-foreground">
                  {event.room_id || 'library'}
                </td>
                <td className="px-2 py-2 text-muted-foreground">{event.person || '—'}</td>
                <td className="px-2 py-2 text-right font-mono">{event.seconds}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}

export default function ContentInfluence() {
  const [filters, setFilters] = useState(EMPTY_FILTERS)
  const [grain, setGrain] = useState('day')
  const [roomId, setRoomId] = useState('')
  const [sort, setSort] = useState({ column: 'views', direction: 'desc' })
  const [evidence, setEvidence] = useState(null)

  const patch = useCallback((changes) => {
    setFilters((current) => ({ ...current, ...changes }))
    setEvidence(null)
  }, [])

  const state = useMemo(() => ({ ...filters, grain, roomId }), [filters, grain, roomId])
  const applied = useMemo(() => contentFilters(state), [state])

  const { data, loading, error, refetch } = useAsync(async () => {
    const [portfolio, trend, top, sales, available, rooms, vocabulary] = await Promise.all([
      roomId ? influenceApi.room(roomId, applied) : influenceApi.portfolio(applied),
      influenceApi.engagement(applied, grain),
      influenceApi.topContent(applied, { sort: sort.column, direction: sort.direction, limit: 50 }),
      influenceApi.salesInfluence(applied),
      influenceApi.collections(),
      influenceApi.rooms(),
      influenceApi.vocabulary(),
    ])
    return { portfolio, trend, top, sales, available, rooms, vocabulary }
  }, [applied, grain, roomId, sort.column, sort.direction])

  const { data: inferences } = useAsync(() => influenceApi.inferences(), [])

  if (loading) return <Spinner label="Loading Content Influence" />
  if (error) return <ErrorNote error={error} onRetry={refetch} />

  const { portfolio, trend, top, sales, available, rooms, vocabulary } = data
  const metrics = portfolio.metrics

  async function showEvidence(row) {
    setEvidence(await influenceApi.asset(row.asset_id, applied))
  }

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-xl font-semibold text-foreground">Content Influence</h1>
        <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
          {roomId
            ? 'Library content activity inside one workspace. A share made straight from the library is not attributable to a workspace, so it is excluded here and counted in the portfolio view.'
            : 'Library content activity across all workspaces: how much content is shared, how much a client actually looks at, and which of it is attached to revenue.'}
        </p>
      </header>

      <FilterBar
        state={state}
        onChange={patch}
        onReset={() => {
          setFilters(EMPTY_FILTERS)
          setRoomId('')
          setGrain('day')
          setEvidence(null)
        }}
        collections={available.collections}
        rooms={rooms.records || []}
      />

      {!metrics.library_built_out && (
        <Notice tone="warn" title="The content library has not been built out">
          The researched report assumes a library exists to report on. With no assets there is no utilization or
          engagement rate to compute, so none is shown — a 0% here would read as a content failure rather than a
          missing library.
        </Notice>
      )}

      <section aria-labelledby="wf019-metrics">
        <h2 id="wf019-metrics" className="mb-3 text-sm font-semibold text-foreground">
          Portfolio
        </h2>
        <MetricTiles metrics={metrics} />
      </section>

      <Card>
        <h2 className="text-sm font-semibold text-foreground">Content engagement over time</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          {trend.totals.shares} shares and {trend.totals.views} client views across {trend.count}{' '}
          {grain}(s).
          {trend.span_complete === false && ' The series is too wide to draw in full.'}
        </p>
        <div className="mt-4">
          <TrendChart points={trend.points} grain={trend.grain} series={SERIES} />
        </div>
        <details className="mt-3">
          <summary className="min-h-11 cursor-pointer py-2 text-sm text-muted-foreground">
            Read the values as a table
          </summary>
          <div className="max-h-64 overflow-auto">
            <table className="w-full border-collapse text-sm">
              <thead className="sticky top-0 bg-background">
                <tr>
                  {['Bucket', 'Shares', 'Client views', 'Downloads', 'Assets'].map((label) => (
                    <th
                      key={label}
                      scope="col"
                      className="px-2 py-2 text-left text-xs font-medium uppercase text-muted-foreground"
                    >
                      {label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {trend.points.map((point) => (
                  <tr key={point.bucket} className="border-t border-border-subtle/20">
                    <td className="px-2 py-1.5 font-mono">{point.bucket}</td>
                    <td className="px-2 py-1.5 font-mono">{point.shares}</td>
                    <td className="px-2 py-1.5 font-mono">{point.views}</td>
                    <td className="px-2 py-1.5 font-mono">{point.downloads}</td>
                    <td className="px-2 py-1.5 font-mono">{point.assets}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      </Card>

      <Card>
        <h2 className="text-sm font-semibold text-foreground">Top content</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Most viewed first, and sortable by {vocabulary.report.sort_columns.join(', ')}.
        </p>
        <div className="mt-4">
          <TopContent
            body={top}
            sort={sort.column}
            direction={sort.direction}
            onSort={(column, direction) => {
              setSort({ column, direction })
              setEvidence(null)
            }}
            onLoadEvent={showEvidence}
          />
        </div>
      </Card>

      <AssetEvidence detail={evidence} onClose={() => setEvidence(null)} />

      <Card>
        <h2 className="text-sm font-semibold text-foreground">Content &amp; Sales Influence</h2>
        <p className="mt-1 mb-4 max-w-3xl text-sm text-muted-foreground">
          Revenue and deals per asset. Association needs a deal connected to a workspace, the deal naming the
          asset, and the asset having been shared or viewed in that workspace. This report does not infer
          influence beyond that.
        </p>
        <SalesInfluence body={sales} />
      </Card>

      {inferences && <InferencePanel body={inferences} />}
    </div>
  )
}
