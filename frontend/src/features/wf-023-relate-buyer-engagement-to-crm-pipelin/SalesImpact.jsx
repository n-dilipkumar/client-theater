import { useCallback, useMemo, useState } from 'react'

import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorNote,
  Field,
  Icon,
  Spinner,
  StatCard,
  inputClass,
  useAsync,
} from '@/components/ui'

import { impactApi } from './api'
import { ICONS } from './icons'
import { BarRow, Disclosure, Notice } from './primitives'

/**
 * WF-023: relate buyer engagement to CRM pipeline and close rate.
 *
 * The Sales Impact report, built from
 * `docs/research/digital-sales-room-workflows/wf/WF-023.md`. The page follows the
 * researched flow: the coverage banner first, then the tiles, then the two deal panels,
 * then the buyer-engagement panel, with the research's own filter vocabulary above them.
 *
 * Three things this page is careful about, because each is easy to get wrong:
 *
 * 1. **The coverage banner is first, not a footnote.** The research's own sentence about
 *    this report is that it can be *missing data* without anyone noticing unless reps are
 *    required to attach a deal to every space. A completeness warning under eight
 *    impressive numbers is a warning nobody reads, so it goes above them and names the
 *    workspaces responsible.
 * 2. **`null` is rendered as "not yet", never as `0`.** A close rate over a population
 *    where nothing has closed is 0/0. Showing `0%` there would assert that every deal was
 *    lost, which is a different claim, and a leadership report is read as a claim. So
 *    `—` with a caption, and the caption says which.
 * 3. **Money always carries its currency.** A single Revenue figure that silently adds
 *    EUR to USD is worse than no figure, so every money tile shows the currency and the
 *    page says so when the deals span more than one.
 */

/* ------------------------------------------------------------------ formatting */

const MONEY = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 })
const MONEY_CENTS = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 })

function money(value, currency, { compact = false } = {}) {
  if (value === null || value === undefined) return '—'
  const format = compact && Math.abs(value) >= 100000 ? MONEY_CENTS : MONEY_CENTS
  const body = format.format(value)
  return currency ? `${body} ${currency}` : body
}

function percent(value) {
  if (value === null || value === undefined) return '—'
  return `${(value * 100).toFixed(value === 1 || value === 0 ? 0 : 1)}%`
}

function count(value) {
  if (value === null || value === undefined) return '—'
  return MONEY.format(value)
}

function shortDate(iso) {
  if (!iso) return '—'
  const parsed = new Date(`${iso}T00:00:00`)
  return Number.isNaN(parsed.getTime())
    ? iso
    : parsed.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: '2-digit' })
}

const CLASS_TONE = { won: 'insert', lost: 'delete', open: 'neutral', unknown: 'restore' }
const CLASS_LABEL = {
  won: 'Closed won',
  lost: 'Closed lost',
  open: 'Still open',
  unknown: 'Not classifiable',
}

/** Human labels for the eight researched tiles, in the researched order. */
const TILE_LABELS = {
  total_deals: 'Total deals',
  total_pipeline_touched: 'Total pipeline touched',
  active_deals: 'Active deals',
  active_pipeline: 'Active pipeline',
  closed_won_deals: 'Closed won deals',
  revenue: 'Revenue',
  close_rate: 'Close rate',
  days_to_close: 'Days to close',
}

const TILE_HINTS = {
  total_deals: 'Opportunities attached to a Sales-type workspace',
  total_pipeline_touched: 'Every in-scope deal, closed included',
  active_deals: 'In-scope deals that have not closed',
  active_pipeline: 'Value of the deals that have not closed',
  closed_won_deals: 'In-scope deals in a closed-won stage',
  revenue: 'Closed-won revenue only',
  close_rate: 'Closed won ÷ (closed won + closed lost)',
  days_to_close: 'Average, over every closed deal',
}

const TILE_DRILL = {
  total_deals: {},
  total_pipeline_touched: {},
  active_deals: { stage: 'open' },
  active_pipeline: { stage: 'open' },
  closed_won_deals: { stage: 'won' },
  revenue: { stage: 'won' },
  close_rate: { stage: 'closed' },
  days_to_close: {},
}

const TILE_ICONS = {
  total_deals: 'schema',
  total_pipeline_touched: 'database',
  active_deals: 'dashboard',
  active_pipeline: 'database',
  closed_won_deals: 'audit',
  revenue: 'schema',
  close_rate: ICONS.pipeline,
  days_to_close: 'refresh',
}

/* ------------------------------------------------------------------- the tiles */

function Tiles({ tiles, currency, onDrill }) {
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      {Object.keys(TILE_LABELS).map((key) => {
        const value = tiles[key]
        const isRate = key === 'close_rate'
        const display = isRate ? percent(value) : count(value)
        const isMoney = key.endsWith('pipeline_touched') || key.endsWith('pipeline') || key === 'revenue'
        const shown = isMoney && value !== null && value !== undefined ? money(value, currency) : display
        const drill = TILE_DRILL[key]
        return (
          <button
            key={key}
            type="button"
            onClick={() => onDrill(drill)}
            title={`Show the deals behind ${TILE_LABELS[key]}`}
            className="cursor-pointer text-left"
          >
            <StatCard
              label={TILE_LABELS[key]}
              value={shown}
              hint={
                value === null || value === undefined
                  ? key === 'close_rate'
                    ? 'Nothing has closed yet'
                    : 'Nothing to average yet'
                  : TILE_HINTS[key]
              }
              icon={TILE_ICONS[key]}
            />
            <p className="mt-1 text-right text-xs text-accent">
              {drill && (drill.stage ? `Deals in ${drill.stage}` : 'All in-scope deals')}
            </p>
          </button>
        )
      })}
    </div>
  )
}

/* ------------------------------------------------------------------ the banner */

function Coverage({ coverage, onOpenRoom }) {
  if (!coverage) return null
  const complete = coverage.complete
  const roomsWithoutDeal = coverage.rooms_without_deal || []
  const untyped = coverage.untyped || []

  return (
    <Notice
      tone={complete ? 'good' : 'warn'}
      icon={complete ? ICONS.inScope : ICONS.warning}
      title={
        complete
          ? 'This report covers every Sales-type workspace'
          : 'This report is missing workspaces'
      }
    >
      {complete ? (
        <p>
          The {coverage.provider || 'CRM'} integration is on and all {coverage.sales_typed_rooms}{' '}
          Sales-type {coverage.sales_typed_rooms === 1 ? 'workspace has' : 'workspaces have'} a CRM
          opportunity attached, so every figure below is the whole population.
        </p>
      ) : (
        <div className="space-y-2">
          <p>
            {coverage.crm_connected
              ? 'The CRM integration is on, but the figures below do not cover every workspace.'
              : 'The CRM integration is off, so no deal is reaching this report. The research is explicit that the report is incomplete in that case.'}
          </p>
          {roomsWithoutDeal.length > 0 && (
            <div>
              <p className="font-semibold">
                Sales-type {roomsWithoutDeal.length === 1 ? 'workspace with' : 'workspaces with'} no
                deal attached ({roomsWithoutDeal.length}):
              </p>
              <ul className="mt-1 space-y-0.5">
                {roomsWithoutDeal.map((room) => (
                  <li key={room.room_id}>
                    <button
                      type="button"
                      onClick={() => onOpenRoom(room.room_id)}
                      className="cursor-pointer text-left underline decoration-dotted underline-offset-2 hover:no-underline"
                    >
                      {room.name || room.room_id}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {untyped.length > 0 && (
            <div>
              <p className="font-semibold">
                Workspaces not typed &ldquo;Sales&rdquo; in their Internal settings ({untyped.length}):
              </p>
              <ul className="mt-1 space-y-0.5">
                {untyped.map((room) => (
                  <li key={room.room_id}>
                    <button
                      type="button"
                      onClick={() => onOpenRoom(room.room_id)}
                      className="cursor-pointer text-left underline decoration-dotted underline-offset-2 hover:no-underline"
                    >
                      {room.name || room.room_id}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {coverage.deals_not_attached > 0 && (
            <p>
              {coverage.deals_not_attached} {coverage.deals_not_attached === 1 ? 'deal is' : 'deals are'}{' '}
              attached to no workspace, so {coverage.deals_not_attached === 1 ? 'it' : 'they'} contribute
              to no figure here.
            </p>
          )}
        </div>
      )}
    </Notice>
  )
}

/* -------------------------------------------------------------------- the drill */

function DealDrill({ deals, onClose }) {
  if (!deals) return null
  if (deals.count === 0) {
    return (
      <Card className="mt-4">
        <EmptyState
          title="No deals match this filter"
          description="Widen the date range or clear the stage filter. The tiles above are the same population, so if a tile is non-zero this list should not be empty."
        />
      </Card>
    )
  }
  return (
    <Card className="mt-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-foreground">
          {deals.count} in-scope {deals.count === 1 ? 'deal' : 'deals'}
          {deals.returned < deals.count && `, showing ${deals.returned}`}
        </h3>
        <Button onClick={onClose}>Close</Button>
      </div>
      <p className="mt-1 text-xs text-muted-foreground">
        Totals cover all {deals.count}, not the {deals.returned} shown, so paging never changes a
        number you would quote.
      </p>
      <div className="mt-3 overflow-x-auto">
        <table className="w-full min-w-[46rem] text-left text-sm">
          <thead>
            <tr className="border-b border-border-subtle/40 text-xs uppercase tracking-wide text-muted-foreground">
              <th scope="col" className="py-2 pr-3 font-medium">Opportunity</th>
              <th scope="col" className="py-2 pr-3 font-medium">Stage</th>
              <th scope="col" className="py-2 pr-3 font-medium">Owner</th>
              <th scope="col" className="py-2 pr-3 font-medium">Team</th>
              <th scope="col" className="py-2 pr-3 text-right font-medium">Amount</th>
              <th scope="col" className="py-2 pr-3 font-medium">Created</th>
              <th scope="col" className="py-2 font-medium">Closed</th>
            </tr>
          </thead>
          <tbody>
            {deals.deals.map((deal) => (
              <tr key={deal.id} className="border-b border-border-subtle/20 align-top">
                <td className="py-2 pr-3">
                  <span className="font-mono text-xs text-muted-foreground">{deal.crm_deal_id || '—'}</span>
                  <span className="block text-foreground">{deal.name || deal.account || 'Untitled'}</span>
                </td>
                <td className="py-2 pr-3">
                  <Badge tone={CLASS_TONE[deal.stage_class] || 'neutral'}>
                    {deal.stage || CLASS_LABEL[deal.stage_class] || 'unclassified'}
                  </Badge>
                </td>
                <td className="py-2 pr-3">
                  {deal.owner || '—'}
                  {deal.owner_source === 'room' && (
                    <span className="block text-xs text-muted-foreground">borrowed from the workspace</span>
                  )}
                </td>
                <td className="py-2 pr-3">{deal.team || '—'}</td>
                <td className="py-2 pr-3 text-right font-mono">
                  {deal.amount === null ? '—' : money(deal.amount, deal.currency)}
                </td>
                <td className="py-2 pr-3">{shortDate(String(deal.created_at || '').slice(0, 10))}</td>
                <td className="py-2">{shortDate(String(deal.closed_at || '').slice(0, 10))}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}

/* ------------------------------------------------------------------ the panels */

function DealPanels({ createdOverTime, byOwner, bucket }) {
  const peakDeals = Math.max(1, ...createdOverTime.map((row) => row.deals))
  const peakAmount = Math.max(1, ...byOwner.map((row) => row.amount))
  const peakViews = Math.max(
    1,
    ...createdOverTime.map((row) => row.views ?? 0),
    ...byOwner.map((row) => row.views ?? 0),
  )
  void peakViews

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <h3 className="text-sm font-semibold text-foreground">Deals Created Over Time</h3>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Grouped by {bucket}, ranged on each deal&rsquo;s own creation date.
        </p>
        {createdOverTime.length === 0 ? (
          <p className="mt-3 text-sm text-muted-foreground">
            No dated deals in this range. A deal with no creation date is in every tile and in no
            bucket, and the report says so under data warnings.
          </p>
        ) : (
          <ul className="mt-3 space-y-1">
            {createdOverTime.map((row) => (
              <BarRow
                key={row.date}
                label={shortDate(row.date)}
                value={row.deals}
                max={peakDeals}
                display={`${count(row.deals)} ${row.deals === 1 ? 'deal' : 'deals'}`}
                hint={row.amount ? money(row.amount) : undefined}
              />
            ))}
          </ul>
        )}
      </Card>

      <Card>
        <h3 className="text-sm font-semibold text-foreground">Deals By Owner</h3>
        <p className="mt-0.5 text-xs text-muted-foreground">
          A deal with no owner is reported under its workspace&rsquo;s owner, and says so.
        </p>
        {byOwner.length === 0 ? (
          <p className="mt-3 text-sm text-muted-foreground">No in-scope deals in this range.</p>
        ) : (
          <ul className="mt-3 space-y-1">
            {byOwner.map((row) => (
              <BarRow
                key={row.owner}
                label={row.owner}
                value={row.amount}
                max={peakAmount}
                tone={row.owner_source === 'room' ? 'sky' : 'accent'}
                display={money(row.amount)}
                hint={`${count(row.deals)} ${row.deals === 1 ? 'deal' : 'deals'} · ${row.won} won · ${row.lost} lost · ${row.open} open${
                  row.owner_source === 'room' ? ' · owner borrowed from the workspace' : ''
                }`}
              />
            ))}
          </ul>
        )}
      </Card>
    </div>
  )
}

function EngagementPanel({ engagement, currency, onDrillBuyers }) {
  const series = engagement.buyer_views_over_time || []
  const peak = Math.max(1, ...series.map((row) => row.views))
  const ranked = engagement.most_engaged_buyers || []

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <h3 className="text-sm font-semibold text-foreground">Buyer Engagement</h3>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Counted only in the {coverageRoomPhrase(engagement)} this report covers.
        </p>
        <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <MiniStat icon={ICONS.eye} label="Buyer views" value={count(engagement.buyer_views)} />
          <MiniStat icon={ICONS.action} label="Buyer actions" value={count(engagement.buyer_actions)} />
          <MiniStat icon={ICONS.buyer} label="Unique buyers" value={count(engagement.unique_buyers)} />
          <MiniStat
            icon="dashboard"
            label="Avg per workspace"
            value={
              engagement.average_buyers_per_workspace === null ||
              engagement.average_buyers_per_workspace === undefined
                ? '—'
                : engagement.average_buyers_per_workspace.toFixed(2)
            }
          />
        </div>
        <p className="mt-2 text-xs text-muted-foreground">
          Actions include views: a client interacting with a space is opening one of its pages.
        </p>
        {series.length > 0 && (
          <>
            <h4 className="mt-4 text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Buyer Views Over Time
            </h4>
            <ul className="mt-1 space-y-1">
              {series.map((row) => (
                <BarRow
                  key={row.date}
                  label={shortDate(row.date)}
                  value={row.views}
                  max={peak}
                  tone="sky"
                  display={`${count(row.views)} views · ${count(row.actions)} actions`}
                />
              ))}
            </ul>
          </>
        )}
        {series.length === 0 && (
          <p className="mt-3 text-sm text-muted-foreground">
            No buyer activity in this range. A workspace with a deal and no engagement is still in
            the pipeline, and a report that dropped it would understate pipeline for exactly the
            deals nobody looked at.
          </p>
        )}
      </Card>

      <Card>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-sm font-semibold text-foreground">Most Engaged Buyers</h3>
          {ranked.length > 0 && (
            <Button onClick={onDrillBuyers}>Open as a list</Button>
          )}
        </div>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Ranked by actions, then views, then email, so two runs do not reshuffle.
        </p>
        {ranked.length === 0 ? (
          <p className="mt-3 text-sm text-muted-foreground">No buyers engaged yet.</p>
        ) : (
          <div className="mt-3 overflow-x-auto">
            <table className="w-full min-w-[28rem] text-left text-sm">
              <thead>
                <tr className="border-b border-border-subtle/40 text-xs uppercase tracking-wide text-muted-foreground">
                  <th scope="col" className="py-2 pr-3 font-medium">Buyer</th>
                  <th scope="col" className="py-2 pr-3 text-right font-medium">Views</th>
                  <th scope="col" className="py-2 pr-3 text-right font-medium">Actions</th>
                  <th scope="col" className="py-2 pr-3 text-right font-medium">Spaces</th>
                  <th scope="col" className="py-2 font-medium">Last view</th>
                </tr>
              </thead>
              <tbody>
                {ranked.map((row) => (
                  <tr key={row.buyer} className="border-b border-border-subtle/20">
                    <td className="py-2 pr-3 font-mono text-[13px]">{row.buyer}</td>
                    <td className="py-2 pr-3 text-right font-mono">{count(row.views)}</td>
                    <td className="py-2 pr-3 text-right font-mono">{count(row.actions)}</td>
                    <td className="py-2 pr-3 text-right font-mono">{count(row.workspaces)}</td>
                    <td className="py-2">{shortDate(String(row.last_view_at || '').slice(0, 10))}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {currency && ranked.length > 0 && (
          <p className="mt-2 text-xs text-muted-foreground">
            Engagement is not weighted by deal value, so the currency ({currency}) does not apply to
            anything on this panel.
          </p>
        )}
      </Card>
    </div>
  )
}

function coverageRoomPhrase() {
  return 'in-scope workspaces'
}

function MiniStat({ icon, label, value }) {
  return (
    <div className="rounded-lg border border-border-subtle/40 bg-background/40 p-3">
      <div className="flex items-center gap-1.5 text-muted-foreground">
        <Icon path={icon} size={14} />
        <span className="text-[11px] uppercase tracking-wide">{label}</span>
      </div>
      <p className="mt-1 font-mono text-xl font-semibold text-foreground">{value}</p>
    </div>
  )
}

/* ------------------------------------------------------------------- the funnel */

function Funnel({ funnel, currency }) {
  if (!funnel || funnel.length === 0) return null
  const peak = Math.max(1, ...funnel.map((row) => row.deals))
  return (
    <Card>
      <h3 className="text-sm font-semibold text-foreground">Stage funnel</h3>
      <p className="mt-0.5 text-xs text-muted-foreground">
        The same deals the tiles count, grouped by stage, so the close rate can be checked rather
        than trusted.
      </p>
      <ul className="mt-3 space-y-1">
        {funnel.map((row) => (
          <BarRow
            key={row.stage}
            label={row.stage}
            value={row.deals}
            max={peak}
            tone={row.class === 'won' ? 'accent' : row.class === 'lost' ? 'amber' : 'sky'}
            display={`${count(row.deals)} ${row.deals === 1 ? 'deal' : 'deals'} · ${money(row.amount, currency)}`}
            hint={
              row.class === 'unknown'
                ? 'This stage string cannot be classified, so it counts as an active deal and sits outside both arms of the close rate.'
                : `${CLASS_LABEL[row.class]} · ${row.won} won · ${row.lost} lost · ${row.open} open`
            }
          />
        ))}
      </ul>
    </Card>
  )
}

/* ------------------------------------------------------------------ the filters */

function FilterBar({ vocabulary, filters, onChange, onReset }) {
  const buckets = vocabulary?.filters?.bucket?.values || ['day', 'week', 'month']
  const stageClasses = vocabulary?.stage_classes || ['won', 'lost', 'open', 'unknown']
  const stageOptions = [
    ...stageClasses.map((value) => ({ value, label: CLASS_LABEL[value] || value })),
    ...(vocabulary?.won_stages || []).map((value) => ({ value, label: `${value} (exact)` })),
    ...(vocabulary?.lost_stages || []).map((value) => ({ value, label: `${value} (exact)` })),
  ]

  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2">
        <span className="flex items-center gap-1.5 text-sm text-foreground">
          <Icon path={ICONS.filter} size={16} />
          Filters
        </span>
        <p className="text-xs text-muted-foreground">
          The research&rsquo;s vocabulary: date range, CRM stage, owners, teams.
        </p>
      </div>
      <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <Field label="From" id="wf023-from" hint="Ranges a deal's created date">
          <input
            id="wf023-from"
            type="date"
            value={filters.from}
            onChange={(event) => onChange({ ...filters, from: event.target.value })}
            className={inputClass}
          />
        </Field>
        <Field label="To" id="wf023-to" hint="Inclusive">
          <input
            id="wf023-to"
            type="date"
            value={filters.to}
            onChange={(event) => onChange({ ...filters, to: event.target.value })}
            className={inputClass}
          />
        </Field>
        <Field label="CRM stage" id="wf023-stage" hint="A class or an exact stage string">
          <select
            id="wf023-stage"
            value={filters.stage}
            onChange={(event) => onChange({ ...filters, stage: event.target.value })}
            className={inputClass}
          >
            <option value="">Any stage</option>
            {stageOptions.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Owner" id="wf023-owner" hint="Comma-separated">
          <input
            id="wf023-owner"
            type="text"
            placeholder="dana, sam"
            value={filters.owner}
            onChange={(event) => onChange({ ...filters, owner: event.target.value })}
            className={inputClass}
          />
        </Field>
        <Field label="Team" id="wf023-team" hint="Comma-separated">
          <input
            id="wf023-team"
            type="text"
            placeholder="enterprise"
            value={filters.team}
            onChange={(event) => onChange({ ...filters, team: event.target.value })}
            className={inputClass}
          />
        </Field>
      </div>
      <div className="mt-3 flex flex-wrap items-end gap-3">
        <Field label="Series granularity" id="wf023-bucket" hint="Both &ldquo;over time&rdquo; panels">
          <select
            id="wf023-bucket"
            value={filters.bucket}
            onChange={(event) => onChange({ ...filters, bucket: event.target.value })}
            className={inputClass}
          >
            {buckets.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </Field>
        <div className="flex gap-2">
          <Button icon="refresh" onClick={onReset}>
            Reset
          </Button>
        </div>
      </div>
    </Card>
  )
}

/* ------------------------------------------------------------------ inferences */

function Inferences({ payload }) {
  if (!payload) return null
  const quotes = Object.entries(payload.sourced_quotes || {})
  return (
    <Disclosure
      summary={`Design decisions the research does not make (${payload.count}) and the sourced quotes beside them`}
    >
      <div className="space-y-4">
        <div>
          <h4 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            What the research quotes
          </h4>
          <dl className="mt-1 space-y-1">
            {quotes.map(([key, text]) => (
              <div key={key}>
                <dt className="font-mono text-xs text-accent">{key}</dt>
                <dd className="text-sm text-muted-foreground">&ldquo;{text}&rdquo;</dd>
              </div>
            ))}
          </dl>
        </div>
        <div>
          <h4 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            What this build chose, and how to change it
          </h4>
          <ul className="mt-1 space-y-2">
            {payload.inferences.map((entry) => (
              <li key={entry.id} className="rounded-lg border border-border-subtle/40 p-3">
                <p className="font-mono text-xs text-accent">{entry.id}</p>
                <p className="text-sm font-medium text-foreground">{entry.topic}</p>
                <p className="mt-1 text-sm text-muted-foreground">{entry.why}</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  <span className="text-foreground">To change it:</span>{' '}
                  <span className="font-mono">{entry.change_it}</span>
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  <span className="text-foreground">Affects:</span> {entry.blast_radius}
                </p>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </Disclosure>
  )
}

/* ------------------------------------------------------------------ the room */

function RoomDetail({ room, onClose }) {
  if (!room) return null
  return (
    <Card className="mt-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-foreground">{room.room.name || room.room.id}</h3>
          <p className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <Badge tone={room.in_scope ? 'insert' : 'restore'}>
              {room.in_scope ? 'In the report' : 'Excluded'}
            </Badge>
            <span>type: {room.room.type || '(not set)'}</span>
            <span>owner: {room.room.owner || '—'}</span>
          </p>
        </div>
        <Button onClick={onClose}>Close</Button>
      </div>

      {!room.in_scope && (
        <div className="mt-3">
          <Notice tone="warn" title="This workspace is not in the report">
            {room.reason === 'not_sales'
              ? 'Its workspace type is not "Sales". The report pulls in workspaces designated as a Sales type, so set the type in this workspace’s Internal settings.'
              : 'It is typed "Sales" but has no CRM opportunity attached. The research is explicit that a Sales workspace with no deal means the report is missing data for it.'}
          </Notice>
        </div>
      )}

      {room.in_scope && (
        <>
          <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <MiniStat icon="schema" label="Deals" value={count(room.tiles.total_deals)} />
            <MiniStat icon="database" label="Active pipeline" value={money(room.tiles.active_pipeline, room.currency)} />
            <MiniStat icon="audit" label="Revenue" value={money(room.tiles.revenue, room.currency)} />
            <MiniStat icon="refresh" label="Days to close" value={room.tiles.days_to_close ?? '—'} />
          </div>
          {room.deals.length > 0 && (
            <div className="mt-3 overflow-x-auto">
              <table className="w-full min-w-[34rem] text-left text-sm">
                <thead>
                  <tr className="border-b border-border-subtle/40 text-xs uppercase tracking-wide text-muted-foreground">
                    <th scope="col" className="py-2 pr-3 font-medium">Opportunity</th>
                    <th scope="col" className="py-2 pr-3 font-medium">Stage</th>
                    <th scope="col" className="py-2 pr-3 text-right font-medium">Amount</th>
                    <th scope="col" className="py-2 font-medium">Created</th>
                  </tr>
                </thead>
                <tbody>
                  {room.deals.map((deal) => (
                    <tr key={deal.id} className="border-b border-border-subtle/20">
                      <td className="py-2 pr-3">
                        <span className="font-mono text-xs text-muted-foreground">{deal.crm_deal_id || '—'}</span>
                        <span className="block">{deal.name || 'Untitled'}</span>
                      </td>
                      <td className="py-2 pr-3">
                        <Badge tone={CLASS_TONE[deal.stage_class] || 'neutral'}>
                          {deal.stage || CLASS_LABEL[deal.stage_class] || 'unclassified'}
                        </Badge>
                      </td>
                      <td className="py-2 pr-3 text-right font-mono">
                        {deal.amount === null ? '—' : money(deal.amount, deal.currency)}
                      </td>
                      <td className="py-2">{shortDate(deal.created)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </Card>
  )
}

/* ---------------------------------------------------------------------- the page */

const EMPTY_FILTERS = { from: '', to: '', stage: '', owner: '', team: '', bucket: 'week' }

export default function SalesImpact() {
  const [filters, setFilters] = useState(EMPTY_FILTERS)
  const [drill, setDrill] = useState(null)
  const [roomId, setRoomId] = useState(null)

  const params = useMemo(
    () => ({
      from: filters.from,
      to: filters.to,
      stage: filters.stage,
      owner: filters.owner,
      team: filters.team,
      bucket: filters.bucket,
    }),
    [filters],
  )

  const vocabulary = useAsync(() => impactApi.vocabulary(), [])
  const report = useAsync(() => impactApi.report(params), [params])
  const drillDeals = useAsync(
    () => (drill ? impactApi.deals({ ...params, ...drill, limit: 100 }) : Promise.resolve(null)),
    [params, drill],
  )
  const room = useAsync(
    () => (roomId ? impactApi.room(roomId, params) : Promise.resolve(null)),
    [roomId, params],
  )
  const inferences = useAsync(() => impactApi.inferences(), [])

  const openDrill = useCallback((extra) => setDrill(extra || {}), [])
  const closeDrill = useCallback(() => setDrill(null), [])
  const openRoom = useCallback((id) => setRoomId(id), [])
  const closeRoom = useCallback(() => setRoomId(null), [])

  if (vocabulary.loading) return <Spinner label="Loading the Sales Impact report" />
  if (vocabulary.error) return <ErrorNote error={vocabulary.error} onRetry={vocabulary.refetch} />
  if (report.loading) return <Spinner label="Computing the report" />
  if (report.error) return <ErrorNote error={report.error} onRetry={report.refetch} />

  const data = report.data
  const currency = data.currency
  const warnings = data.warnings || []
  const dataWarnings = data.data_warnings || []

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold text-foreground">Sales Impact</h1>
          <p className="mt-0.5 text-sm text-muted-foreground">
            Buyer engagement in Sales-type workspaces, joined to the CRM opportunities attached to
            them. Every figure is computed on read; nothing here is a stored total.
          </p>
        </div>
        <Button icon="refresh" onClick={report.refetch}>
          Recompute
        </Button>
      </header>

      <Coverage coverage={data.coverage} onOpenRoom={openRoom} />

      <FilterBar
        vocabulary={vocabulary.data}
        filters={filters}
        onChange={setFilters}
        onReset={() => {
          setFilters(EMPTY_FILTERS)
          setDrill(null)
        }}
      />

      {report.data.filters &&
        (report.data.filters.from ||
          report.data.filters.to ||
          report.data.filters.stage.length ||
          report.data.filters.stage_class.length ||
          report.data.filters.owner.length ||
          report.data.filters.team.length) ? (
          <p className="text-xs text-muted-foreground">
            Filtered: {describeFilter(report.data.filters)}. The tiles below are for this slice only.
          </p>
        ) : null}

      {data.scope.in_scope_rooms === 0 ? (
        <EmptyState
          title="No workspace qualifies yet"
          description="The report pulls in workspaces designated as a Sales type that have a CRM opportunity attached. Type a workspace as Sales in its Internal settings and attach a deal, and it appears here."
          action={
            <Button
              onClick={() =>
                openDrill({})
              }
            >
              Look at the deals anyway
            </Button>
          }
        />
      ) : (
        <>
          <Tiles tiles={data.tiles} currency={currency} onDrill={openDrill} />
          <DealDrill deals={drillDeals.data} onClose={closeDrill} />
          <RoomDetail room={room.data} onClose={closeRoom} />
          <DealPanels
            createdOverTime={data.deals_created_over_time}
            byOwner={data.deals_by_owner}
            bucket={report.data.filters.bucket}
          />
          <Funnel funnel={data.funnel} currency={currency} />
          <EngagementPanel
            engagement={data.engagement}
            currency={currency}
            onDrillBuyers={() => openDrill({})}
          />
        </>
      )}

      {warnings.length > 0 && (
        <section>
          <h2 className="text-sm font-semibold text-foreground">Why the numbers may be low</h2>
          <ul className="mt-2 space-y-2">
            {warnings.map((warning) => (
              <li key={warning.code}>
                <Notice tone="warn" icon={ICONS.warning}>
                  {warning.message}
                </Notice>
              </li>
            ))}
          </ul>
        </section>
      )}

      {dataWarnings.length > 0 && (
        <section>
          <h2 className="text-sm font-semibold text-foreground">Data warnings</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Rows a figure could not use. They are named rather than dropped, because a total that
            shrinks for a reason nobody can see is the failure this report exists to prevent.
          </p>
          <ul className="mt-2 space-y-2">
            {dataWarnings.map((warning, position) => (
              <li key={`${warning.code}-${position}`}>
                <Notice tone="info" icon={ICONS.info} title={warning.code.replace(/_/g, ' ')}>
                  {warning.message}
                </Notice>
              </li>
            ))}
          </ul>
        </section>
      )}

      <Inferences payload={inferences.data} />
    </div>
  )
}

function describeFilter(applied) {
  const parts = []
  if (applied.from || applied.to) parts.push(`${applied.from || 'any'} to ${applied.to || 'any'}`)
  if (applied.stage_class.length) parts.push(`class ${applied.stage_class.join(' or ')}`)
  if (applied.stage.length) parts.push(`stage ${applied.stage.join(' or ')}`)
  if (applied.owner.length) parts.push(`owner ${applied.owner.join(' or ')}`)
  if (applied.team.length) parts.push(`team ${applied.team.join(' or ')}`)
  return parts.join(', ')
}
