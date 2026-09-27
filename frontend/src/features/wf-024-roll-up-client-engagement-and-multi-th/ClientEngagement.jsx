import { Fragment, useMemo, useState } from 'react'

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

import { engagementApi } from './api'
import { SortHeader, Tabs, Tile, ToggleChip } from './primitives'

/**
 * WF-024: roll up client engagement and multi-threading portfolio-wide.
 *
 * Built from `docs/research/digital-sales-room-workflows/wf/WF-024.md`, which is
 * the specification. The page follows the researched flow exactly: open the
 * report, read the five tiles, click a tile to expand the full account list, sort
 * by any column, filter by date range / owners / teams, and read out loud whether
 * the account is multi-threaded and who the champion is.
 *
 * Three things this page is careful about, because each is easy to get wrong:
 *
 * 1. **Client activity only, and it says so.** The report analyses *external*
 *    activity. The coverage line under the tiles names how many events were
 *    excluded as internal or unattributable, so a reader can see that the
 *    distinction is being made rather than having to trust it.
 * 2. **The average's denominator is every workspace, including the ones nobody
 *    opened.** The hint on that tile says how many workspaces it is over, so the
 *    number cannot be read as "average across the deals that worked".
 * 3. **A single-threaded account is surfaced, not buried.** A `Thread` column
 *    that reads "1 person" in a warning tone is the answer to the question the
 *    report exists for, and it is on the row rather than behind a click.
 *
 * The three tabs are the three reports the research names in one family. Client
 * Engagement is the primary one; Team Usage and Implementations are reads over
 * the same rollup. Every number comes from this feature's own `engagementApi`,
 * never from a method added to the shared `api` object.
 */

const TABS = [
  { id: 'engagement', label: 'Client engagement' },
  { id: 'team', label: 'Team usage' },
  { id: 'implementations', label: 'Implementations' },
]

/** Columns shown in the account table, in order. `sort` is the server key. */
const COLUMNS = [
  { key: 'account', label: 'Account', numeric: false },
  { key: 'views', label: 'Client views', numeric: true },
  { key: 'actions', label: 'Client actions', numeric: true },
  { key: 'unique_clients', label: 'Clients', numeric: true },
  { key: 'workspace_count', label: 'Rooms', numeric: true },
  { key: 'champion', label: 'Champion', numeric: false },
  { key: 'multi_threaded', label: 'Thread', numeric: true },
  { key: 'last_activity_at', label: 'Last seen', numeric: false },
]

const EMPTY_FILTERS = { date_from: '', date_to: '', owner: [], team: [] }

function toggle(values, value) {
  return values.includes(value) ? values.filter((item) => item !== value) : [...values, value]
}

function day(value) {
  if (!value) return '—'
  return String(value).slice(0, 10)
}

function FilterBar({ filters, setFilters, choices, grain, setGrain, onClear }) {
  return (
    <Card>
      <div className="flex flex-col gap-4">
        <div className="flex flex-wrap items-end gap-4">
          <div className="w-full sm:w-44">
            <Field id="wf024-from" label="From" hint="Inclusive">
              <input
                id="wf024-from"
                type="date"
                className={inputClass}
                value={filters.date_from}
                onChange={(event) => setFilters({ ...filters, date_from: event.target.value })}
              />
            </Field>
          </div>
          <div className="w-full sm:w-44">
            <Field id="wf024-to" label="To" hint="Through the end of this day">
              <input
                id="wf024-to"
                type="date"
                className={inputClass}
                value={filters.date_to}
                onChange={(event) => setFilters({ ...filters, date_to: event.target.value })}
              />
            </Field>
          </div>
          <div className="w-full sm:w-40">
            <Field id="wf024-grain" label="Chart grain">
              <select
                id="wf024-grain"
                className={inputClass}
                value={grain}
                onChange={(event) => setGrain(event.target.value)}
              >
                {choices.grains.map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          <Button icon="refresh" onClick={onClear}>
            Clear filters
          </Button>
        </div>

        {choices.owners.length > 0 && (
          <div>
            <p className="mb-1.5 text-xs font-medium text-muted-foreground">
              Owners <span className="text-muted-foreground/70">(and / or)</span>
            </p>
            <div className="flex flex-wrap gap-2">
              {choices.owners.map((owner) => (
                <ToggleChip
                  key={owner}
                  active={filters.owner.includes(owner)}
                  onClick={() => setFilters({ ...filters, owner: toggle(filters.owner, owner) })}
                >
                  {owner}
                </ToggleChip>
              ))}
            </div>
          </div>
        )}

        {choices.teams.length > 0 && (
          <div>
            <p className="mb-1.5 text-xs font-medium text-muted-foreground">
              Teams <span className="text-muted-foreground/70">(and / or)</span>
            </p>
            <div className="flex flex-wrap gap-2">
              {choices.teams.map((team) => (
                <ToggleChip
                  key={team}
                  active={filters.team.includes(team)}
                  onClick={() => setFilters({ ...filters, team: toggle(filters.team, team) })}
                >
                  {team}
                </ToggleChip>
              ))}
            </div>
          </div>
        )}
      </div>
    </Card>
  )
}

function Coverage({ coverage, totals }) {
  const excluded = coverage.internal_events_excluded + coverage.unattributed_events_excluded
  if (!excluded && !coverage.events_out_of_scope) return null
  return (
    <p className="text-xs text-muted-foreground">
      Client activity only.{' '}
      <span className="text-foreground">{totals.client_actions}</span> client action(s) counted.{' '}
      <span className="text-foreground">{coverage.internal_events_excluded}</span> internal action(s) excluded.{' '}
      <span className="text-foreground">{coverage.unattributed_events_excluded}</span> with no identifiable
      person excluded.{' '}
      {coverage.events_out_of_scope > 0 && (
        <>
          <span className="text-foreground">{coverage.events_out_of_scope}</span> on a workspace outside this
          scope excluded.
        </>
      )}
    </p>
  )
}

function Series({ points, grain, widened }) {
  if (!points || points.length === 0) {
    return <p className="text-sm text-muted-foreground">No client activity in this range.</p>
  }
  const peak = Math.max(1, ...points.map((point) => point.views))
  return (
    <div>
      <div
        role="img"
        aria-label={`Client views by ${grain} across ${points.length} periods, peaking at ${peak}`}
        className="flex h-24 items-end gap-px"
      >
        {points.map((point) => (
          <div
            key={point.bucket_start}
            title={`${point.bucket_start}: ${point.views} view(s), ${point.actions} action(s)`}
            className="min-w-[2px] flex-1 rounded-t bg-accent/70"
            style={{ height: `${Math.round((point.views / peak) * 100)}%` }}
          />
        ))}
      </div>
      <p className="mt-2 text-xs text-muted-foreground">
        {points.length} {grain} bucket(s), first {points[0].bucket_start}, last{' '}
        {points[points.length - 1].bucket_start}.
        {widened && ' The grain was widened automatically to keep the chart readable.'}
      </p>
    </div>
  )
}

function ThreadBadge({ row }) {
  if (row.no_client_activity) {
    return <Badge tone="delete">No client activity</Badge>
  }
  if (row.multi_threaded) {
    return <Badge tone="insert">{row.unique_clients} people</Badge>
  }
  return <Badge tone="restore">1 person only</Badge>
}

function ClientRow({ client }) {
  return (
    <li className="flex flex-wrap items-baseline justify-between gap-2 border-t border-border-subtle/30 py-2 first:border-t-0">
      <span className="font-mono text-[13px] text-foreground">{client.person}</span>
      <span className="font-mono text-xs text-muted-foreground">
        {client.actions} action(s), {client.views} view(s), last {day(client.last_seen_at)}
      </span>
    </li>
  )
}

function AccountTable({ body, sort, descending, onSort, expanded, onExpand }) {
  if (!body.accounts.length) {
    return (
      <EmptyState
        title="No accounts in this scope"
        description="Widen the date range, or clear the owner and team filters."
      />
    )
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <thead className="border-b border-border-subtle/40">
          <tr>
            {COLUMNS.map((column) => (
              <SortHeader
                key={column.key}
                column={column.key}
                label={column.label}
                numeric={column.numeric}
                active={sort === column.key}
                direction={descending ? 'descending' : 'ascending'}
                onSort={onSort}
              />
            ))}
          </tr>
        </thead>
        <tbody>
          {body.accounts.map((row) => {
            const isOpen = expanded === row.account_key
            return (
              <Fragment key={row.account_key}>
                <tr className="border-b border-border-subtle/20" aria-expanded={isOpen}>
                  <td className="px-3 py-2">
                    <button
                      type="button"
                      onClick={() => onExpand(isOpen ? null : row.account_key)}
                      aria-controls={`wf024-account-${row.account_key}`}
                      className="min-h-11 cursor-pointer rounded text-left font-medium text-foreground transition-colors duration-200 hover:text-accent"
                    >
                      {row.account}
                    </button>
                    <p className="text-xs text-muted-foreground">
                      {row.owners.join(', ') || 'No owner'}
                      {row.teams.length ? ` · ${row.teams.join(', ')}` : ''}
                    </p>
                  </td>
                  <td className="px-3 py-2 text-right font-mono">{row.views}</td>
                  <td className="px-3 py-2 text-right font-mono">{row.actions}</td>
                  <td className="px-3 py-2 text-right font-mono">{row.unique_clients}</td>
                  <td className="px-3 py-2 text-right font-mono">{row.workspace_count}</td>
                  <td className="px-3 py-2 font-mono text-[13px]">
                    {row.champion ? row.champion.person : '—'}
                  </td>
                  <td className="px-3 py-2">
                    <ThreadBadge row={row} />
                  </td>
                  <td className="px-3 py-2 text-muted-foreground">{day(row.last_activity_at)}</td>
                </tr>
                {isOpen && (
                  <tr key={`${row.account_key}-detail`} id={`wf024-account-${row.account_key}`}>                    <td colSpan={COLUMNS.length} className="bg-muted/20 px-3 py-3">
                      <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
                        Who these individuals are
                      </p>
                      {row.clients.length === 0 ? (
                        <p className="mt-2 text-sm text-muted-foreground">
                          Nobody has looked at this account. That is the finding.
                        </p>
                      ) : (
                        <ul className="mt-2">
                          {row.clients.map((client) => (
                            <ClientRow key={client.person} client={client} />
                          ))}
                        </ul>
                      )}
                      <p className="mt-3 text-xs text-muted-foreground">
                        {row.workspace_count} workspace(s). First activity {day(row.first_activity_at)}, last{' '}
                        {day(row.last_activity_at)}.
                        {row.stale && ' Nothing has been looked at for over the stale threshold.'}
                      </p>
                    </td>
                  </tr>
                )}
              </Fragment>
            )
          })}
        </tbody>
      </table>
      <p className="mt-3 text-xs text-muted-foreground">
        Showing {body.count} of {body.total} account(s), sorted by {body.sort}{' '}
        {body.descending ? 'descending' : 'ascending'}.
      </p>
    </div>
  )
}

function EngagementPanel({ params, choices }) {
  const [sort, setSort] = useState('actions')
  const [descending, setDescending] = useState(true)
  const [expanded, setExpanded] = useState(null)

  const report = useAsync(() => engagementApi.report(params), [JSON.stringify(params)])
  const accounts = useAsync(
    () => engagementApi.accounts({ ...params, sort, descending, limit: 100 }),
    [JSON.stringify(params), sort, descending],
  )

  if (report.loading) return <Spinner label="Loading client engagement" />
  if (report.error) return <ErrorNote error={report.error} onRetry={report.refetch} />
  const data = report.data

  function onSort(column) {
    if (sort === column) {
      setDescending((current) => !current)
    } else {
      setSort(column)
      setDescending(true)
    }
  }

  return (
    <div className="flex flex-col gap-5">
      <section aria-label="Client engagement metrics" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
        {data.tiles.map((tile) => (
          <Tile
            key={tile.key}
            label={tile.label}
            value={tile.value}
            display={tile.display}
            hint={tile.hint}
            expanded={sort === tile.expand && descending}
            onClick={() => {
              setSort(tile.expand)
              setDescending(true)
            }}
          />
        ))}
      </section>

      <Coverage coverage={data.coverage} totals={data.totals} />

      <Card>
        <h2 className="font-mono text-sm font-semibold text-foreground">Client views over time</h2>
        <div className="mt-3">
          <Series
            points={data.tiles.find((tile) => tile.key === 'client_views_over_time')?.series}
            grain={data.grain}
            widened={data.grain_widened}
          />
        </div>
      </Card>

      <Card>
        <h2 className="font-mono text-sm font-semibold text-foreground">Most engaged clients</h2>
        <ul className="mt-2">
          {(data.tiles.find((tile) => tile.key === 'most_engaged_clients')?.clients || []).map((client) => (
            <li
              key={client.person}
              className="flex flex-wrap items-baseline justify-between gap-2 border-t border-border-subtle/30 py-2 first:border-t-0"
            >
              <span className="font-mono text-[13px] text-foreground">{client.person}</span>
              <span className="font-mono text-xs text-muted-foreground">
                {client.actions} action(s) across {client.account_count} account(s), last{' '}
                {day(client.last_seen_at)}
              </span>
            </li>
          ))}
        </ul>
        {data.totals.accounts_without_client_activity > 0 && (
          <p className="mt-3 text-xs text-muted-foreground">
            {data.totals.accounts_without_client_activity} account(s) in scope have had no client activity at all,
            and count in the average above.
          </p>
        )}
      </Card>

      <Card>
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="font-mono text-sm font-semibold text-foreground">Accounts</h2>
          {accounts.error && <p className="text-xs text-destructive">The account list could not load.</p>}
        </div>
        <div className="mt-3">
          {accounts.loading ? (
            <Spinner label="Loading accounts" />
          ) : accounts.data ? (
            <AccountTable
              body={accounts.data}
              sort={sort}
              descending={descending}
              onSort={onSort}
              expanded={expanded}
              onExpand={setExpanded}
            />
          ) : null}
        </div>
      </Card>

      {choices.accounts.length > 0 && (
        <Card>
          <h2 className="font-mono text-sm font-semibold text-foreground">Workspaces in scope</h2>
          <ul className="mt-2 text-sm text-muted-foreground">
            {choices.accounts.map((entry) => (
              <li key={entry.account_key} className="border-t border-border-subtle/30 py-1.5 first:border-t-0">
                <span className="text-foreground">{entry.account}</span>
                <span className="ml-2 font-mono text-xs">{entry.account_key}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  )
}

function TeamPanel({ params }) {
  const body = useAsync(() => engagementApi.teamUsage(params), [JSON.stringify(params)])
  if (body.loading) return <Spinner label="Loading team usage" />
  if (body.error) return <ErrorNote error={body.error} onRetry={body.refetch} />
  const data = body.data

  return (
    <div className="flex flex-col gap-5">
      <section aria-label="Team usage totals" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard label="Owners with a portfolio" value={data.totals.owners} icon="rooms" />
        <StatCard label="Internal actions" value={data.totals.internal_actions} icon="audit" />
        <StatCard label="Client actions" value={data.totals.client_actions} icon="dashboard" />
        <StatCard label="Workspaces with no owner" value={data.totals.workspaces_without_an_owner} icon="rooms" />
      </section>

      <Card>
        <h2 className="font-mono text-sm font-semibold text-foreground">By owner</h2>
        {data.owners_detail.length === 0 ? (
          <p className="mt-2 text-sm text-muted-foreground">No workspace in this scope has an owner.</p>
        ) : (
          <div className="mt-3 overflow-x-auto">
            <table className="w-full border-collapse text-sm">
              <thead className="border-b border-border-subtle/40">
                <tr>
                  {['Owner', 'Rooms', 'Accounts', 'Internal', 'Client actions', 'Client views', 'Multi-threaded', 'No client activity'].map(
                    (label, index) => (
                      <th
                        key={label}
                        scope="col"
                        className={`px-3 py-2 text-xs font-medium tracking-wide uppercase text-muted-foreground ${
                          index === 0 ? 'text-left' : 'text-right'
                        }`}
                      >
                        {label}
                      </th>
                    ),
                  )}
                </tr>
              </thead>
              <tbody>
                {data.owners_detail.map((row) => (
                  <tr key={row.owner} className="border-b border-border-subtle/20">
                    <td className="px-3 py-2 font-medium text-foreground">{row.owner}</td>
                    <td className="px-3 py-2 text-right font-mono">{row.workspaces}</td>
                    <td className="px-3 py-2 text-right font-mono">{row.accounts}</td>
                    <td className="px-3 py-2 text-right font-mono">{row.internal_actions}</td>
                    <td className="px-3 py-2 text-right font-mono">{row.client_actions}</td>
                    <td className="px-3 py-2 text-right font-mono">{row.client_views}</td>
                    <td className="px-3 py-2 text-right font-mono">{row.multi_threaded_accounts}</td>
                    <td className="px-3 py-2 text-right font-mono">{row.accounts_without_client_activity}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {data.workspaces_without_an_owner.length > 0 && (
        <Card>
          <h2 className="font-mono text-sm font-semibold text-foreground">Workspaces with no owner</h2>
          <ul className="mt-2 text-sm text-muted-foreground">
            {data.workspaces_without_an_owner.map((row) => (
              <li key={row.id} className="border-t border-border-subtle/30 py-1.5 first:border-t-0">
                <span className="text-foreground">{row.name}</span>
                {row.account && <span className="ml-2">{row.account}</span>}
              </li>
            ))}
          </ul>
        </Card>
      )}

      {data.unattributed_internal_people.length > 0 && (
        <Card>
          <h2 className="font-mono text-sm font-semibold text-foreground">
            Internal activity with nobody against it
          </h2>
          <p className="mt-1 text-xs text-muted-foreground">
            These people used the room and own no workspace, so none of their activity lands in a row above.
          </p>
          <ul className="mt-2 text-sm text-foreground">
            {data.unattributed_internal_people.map((person) => (
              <li key={person} className="font-mono text-[13px]">
                {person}
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  )
}

function ImplementationsPanel({ params }) {
  const body = useAsync(() => engagementApi.implementations(params), [JSON.stringify(params)])
  if (body.loading) return <Spinner label="Loading implementations" />
  if (body.error) return <ErrorNote error={body.error} onRetry={body.refetch} />
  const data = body.data
  const totals = data.totals

  return (
    <div className="flex flex-col gap-5">
      <section aria-label="Implementation totals" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="Implementations"
          value={totals.implementations}
          hint={`${totals.active} active, ${totals.completed} completed`}
          icon="schema"
        />
        <StatCard
          label="Time to completion"
          value={totals.time_to_completion_days === null ? '—' : `${totals.time_to_completion_days}d`}
          hint="Average, over the ones that carry both dates"
          icon="dashboard"
        />
        <StatCard
          label="Completed on time"
          value={totals.completed_on_time_percent === null ? '—' : `${totals.completed_on_time_percent}%`}
          hint="Over the completions that carry a due date"
          icon="audit"
        />
        <StatCard
          label="At risk"
          value={totals.at_risk}
          hint="Overdue, undated, or delivered late"
          icon="rooms"
        />
      </section>

      <p className="text-xs text-muted-foreground">
        Customer engagement during delivery: <span className="text-foreground">{totals.customer_views}</span>{' '}
        client view(s) and <span className="text-foreground">{totals.customer_actions}</span> client action(s)
        across <span className="text-foreground">{totals.unique_customers}</span> customer(s).
      </p>

      {data.at_risk.length > 0 && (
        <Card>
          <h2 className="font-mono text-sm font-semibold text-foreground">At risk</h2>
          <ul className="mt-2">
            {data.at_risk.map((row) => (
              <li
                key={row.id}
                className="border-t border-border-subtle/30 py-2 first:border-t-0"
              >
                <p className="text-sm font-medium text-foreground">
                  {row.name}{' '}
                  <span className="font-normal text-muted-foreground">
                    · {row.account} · {row.owner || 'no owner'}
                  </span>
                </p>
                <ul className="mt-1">
                  {row.at_risk_reasons.map((reason) => (
                    <li key={reason} className="text-xs text-amber-300">
                      {reason}
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <Card>
        <h2 className="font-mono text-sm font-semibold text-foreground">By owner</h2>
        {data.by_owner.length === 0 ? (
          <p className="mt-2 text-sm text-muted-foreground">No implementations for any account in scope.</p>
        ) : (
          <div className="mt-3 overflow-x-auto">
            <table className="w-full border-collapse text-sm">
              <thead className="border-b border-border-subtle/40">
                <tr>
                  {['Owner', 'Total', 'Active', 'Completed', 'At risk'].map((label, index) => (
                    <th
                      key={label}
                      scope="col"
                      className={`px-3 py-2 text-xs font-medium tracking-wide uppercase text-muted-foreground ${
                        index === 0 ? 'text-left' : 'text-right'
                      }`}
                    >
                      {label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.by_owner.map((row) => (
                  <tr key={row.owner} className="border-b border-border-subtle/20">
                    <td className="px-3 py-2 font-medium text-foreground">{row.owner}</td>
                    <td className="px-3 py-2 text-right font-mono">{row.total}</td>
                    <td className="px-3 py-2 text-right font-mono">{row.active}</td>
                    <td className="px-3 py-2 text-right font-mono">{row.completed}</td>
                    <td className="px-3 py-2 text-right font-mono">{row.at_risk}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card>
        <h2 className="font-mono text-sm font-semibold text-foreground">Every implementation</h2>
        <div className="mt-3 overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead className="border-b border-border-subtle/40">
              <tr>
                {['Implementation', 'Account', 'Status', 'Started', 'Due', 'Took', 'On time'].map(
                  (label, index) => (
                    <th
                      key={label}
                      scope="col"
                      className={`px-3 py-2 text-xs font-medium tracking-wide uppercase text-muted-foreground ${
                        index < 2 ? 'text-left' : 'text-right'
                      }`}
                    >
                      {label}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {data.implementations_detail.map((row) => (
                <tr key={row.id} className="border-b border-border-subtle/20">
                  <td className="px-3 py-2 font-medium text-foreground">{row.name}</td>
                  <td className="px-3 py-2 text-muted-foreground">{row.account}</td>
                  <td className="px-3 py-2 text-right">
                    {row.state === 'unknown' ? <Badge>{row.status || 'unknown'}</Badge> : row.status}
                  </td>
                  <td className="px-3 py-2 text-right text-muted-foreground">{day(row.started_at)}</td>
                  <td className="px-3 py-2 text-right text-muted-foreground">{day(row.due_at)}</td>
                  <td className="px-3 py-2 text-right font-mono">
                    {row.duration_days === null ? '—' : `${row.duration_days}d`}
                  </td>
                  <td className="px-3 py-2 text-right">
                    {row.completed_on_time === null ? (
                      '—'
                    ) : row.completed_on_time ? (
                      <Badge tone="insert">on time</Badge>
                    ) : (
                      <Badge tone="delete">late</Badge>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-3 text-xs text-muted-foreground">
          {data.missing_dates.no_due_date} with no due date, {data.missing_dates.no_completion_date} with no
          completion date, {data.missing_dates.completed_without_a_start} completed without a start date,{' '}
          {data.missing_dates.unknown_state} with a status this report does not recognise.
        </p>
      </Card>
    </div>
  )
}

export default function ClientEngagement() {
  const [tab, setTab] = useState('engagement')
  const [filters, setFilters] = useState(EMPTY_FILTERS)
  const [grain, setGrain] = useState('day')

  const choices = useAsync(() => engagementApi.filters(), [])

  const params = useMemo(
    () => ({
      date_from: filters.date_from,
      date_to: filters.date_to,
      owner: filters.owner,
      team: filters.team,
      grain,
    }),
    [filters, grain],
  )

  return (
    <div className="flex flex-col gap-5">
      <header>
        <h1 className="font-mono text-xl font-semibold text-foreground">Client engagement</h1>
        <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
          Client activity across every workspace in this instance. Read out loud: is the account being
          multi-threaded, and who is the champion?
        </p>
      </header>

      <Tabs tabs={TABS} active={tab} onChange={setTab} />

      {choices.loading ? (
        <Spinner label="Loading filter choices" />
      ) : choices.error ? (
        <ErrorNote error={choices.error} onRetry={choices.refetch} />
      ) : (
        <FilterBar
          filters={filters}
          setFilters={setFilters}
          choices={choices.data}
          grain={grain}
          setGrain={setGrain}
          onClear={() => {
            setFilters(EMPTY_FILTERS)
            setGrain('day')
          }}
        />
      )}

      <div id="wf024-panel" role="tabpanel" aria-labelledby={`wf024-tab-${tab}`}>
        {tab === 'engagement' && <EngagementPanel params={params} choices={choices.data || { accounts: [] }} />}
        {tab === 'team' && <TeamPanel params={params} />}
        {tab === 'implementations' && <ImplementationsPanel params={params} />}
      </div>
    </div>
  )
}
