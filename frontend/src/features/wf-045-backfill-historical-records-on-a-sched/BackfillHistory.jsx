/**
 * WF-045: backfill history on a schedule with a resumable cursor.
 *
 * The page follows the researched user flow, in the order the flow implies:
 *
 *   1. Pick the history. **Sync -> Backfill**, a date range or the full-history
 *      option, the connection it comes from, and an optional field map. The
 *      range picker says which way the researched volume rule will fall before
 *      anything is created, because that is the one decision in the workflow an
 *      admin can still change cheaply.
 *   2. Start. The room asks the CRM for an asynchronous job when the volume is
 *      large and a delta or paged read when it is moderate, stores the job id or
 *      the delta token as a cursor, and writes rows in pages as the poller
 *      returns them.
 *   3. Watch it. Progress as a percentage with a per-run log beside it, and the
 *      stored cursor with the day it stops being resumable - which is the earliest
 *      place an operator can be told, before the resume that would have failed.
 *   4. Pick it up. A stalled run is the state that needs a human, so it is the
 *      one the page puts at the top and names the next backfill to open.
 *
 * Two things this page is careful not to do. It never shows an ETA: the research
 * quotes Salesforce saying it "doesn't guarantee a service level agreement", and
 * a bar with a finish time is a promise the vendor has declined to make. And a
 * run the vendor has not given a total for is shown as indeterminate rather than
 * as 0%, because a percentage needs a denominator and a wrong one moves
 * backwards.
 *
 * Nothing here reaches upwards with a relative path and nothing was added to a
 * shared file: the API calls are in `./api.js` and the small pieces in
 * `./primitives.jsx`.
 */

import { useEffect, useState } from 'react'
import { absoluteTime, relativeTime } from '@/lib/api'
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
import { backfillApi, blockedByPreflight, defaultOf, isLive, progressNote, progressPercent, stateTone } from './api'
import { BACKFILL_ICON, CLOCK_ICON, POLL_ICON, DataTable, Fact, Notice, PathButton, ProgressBar, Section } from './primitives'

const TABS = [
  { id: 'runs', label: 'Runs' },
  { id: 'wizard', label: 'Start a backfill' },
  { id: 'cursors', label: 'Cursors' },
  { id: 'replica', label: 'Replica' },
  { id: 'contract', label: 'Vendors & decisions' },
]

function TabBar({ current, onChange }) {
  return (
    <div role="tablist" aria-label="Backfill sections" className="flex flex-wrap gap-1.5">
      {TABS.map((tab) => {
        const selected = tab.id === current
        return (
          <button
            key={tab.id}
            type="button"
            role="tab"
            id={`wf045-tab-${tab.id}`}
            aria-selected={selected}
            aria-controls={`wf045-panel-${tab.id}`}
            onClick={() => onChange(tab.id)}
            className={`inline-flex min-h-11 cursor-pointer items-center rounded-lg px-4 text-sm transition-colors
              duration-200 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none ${
                selected
                  ? 'bg-accent/15 font-semibold text-accent'
                  : 'bg-muted text-muted-foreground hover:bg-border-subtle hover:text-foreground'
              }`}
          >
            {tab.label}
          </button>
        )
      })}
    </div>
  )
}

/**
 * Split a run list into what is still moving and what is finished.
 *
 * `done` is returned alongside `live` because the two are worth counting
 * separately on a page whose whole point is that a stalled run needs a human: a
 * list of finished runs tells you nothing about whether anything needs doing.
 */
function groupByState(rows) {
  return {
    live: rows.filter(isLive),
    done: rows.filter((row) => !isLive(row)),
  }
}

// --------------------------------------------------------------------------- //
// Run row
// --------------------------------------------------------------------------- //

function RunStateBadge({ run, vocabulary }) {
  const state = run?.data?.state
  return <Badge tone={stateTone(vocabulary, state)}>{state}</Badge>
}

function RunProgress({ run }) {
  const percent = progressPercent(run.progress)
  return (
    <span className="flex items-center gap-2">
      <span className="h-1.5 w-20 overflow-hidden rounded-full bg-muted">
        <span
          className="block h-full rounded-full bg-accent transition-[width] duration-300 motion-reduce:transition-none"
          style={{ width: `${percent ?? 0}%` }}
        />
      </span>
      <span className="font-mono text-[13px] text-foreground">
        {run.progress?.indeterminate ? '—' : `${percent ?? 0}%`}
      </span>
    </span>
  )
}

// --------------------------------------------------------------------------- //
// The wizard: the researched first step
// --------------------------------------------------------------------------- //

const DEFAULT_MAP = '{\n  "Name": "title",\n  "Stage__c": "stage"\n}'

function StartWizard({ vocabulary, connections, roomId, onStarted, onError }) {
  const [connectionId, setConnectionId] = useState('')
  const [kind, setKind] = useState('full_history')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [direction, setDirection] = useState('pull')
  const [pageSize, setPageSize] = useState('')
  const [estimate, setEstimate] = useState('')
  const [fieldMap, setFieldMap] = useState(DEFAULT_MAP)
  const [mapError, setMapError] = useState('')
  // Owned here rather than lifted: the wizard is the only thing that knows
  // whether a Start is in flight, and a `busy` prop the page never sets true
  // would be a button that says "Opening…" only after it has already opened.
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!connectionId && connections.length) setConnectionId(connections[0].id)
  }, [connections, connectionId])

  const chosen = connections.find((row) => row.id === connectionId)
  const threshold = defaultOf(vocabulary, 'bulk_threshold_records') ?? 2000
  const pageCeiling = defaultOf(vocabulary, 'dataverse_page_size') ?? 5000
  const numeric = Number(estimate)
  const willUse =
    Number.isFinite(numeric) && numeric > 0
      ? numeric > threshold
        ? 'An asynchronous job'
        : 'A paged read'
      : 'Whichever strategy the vendor prefers, reconsidered once a total arrives'

  async function start() {
    setMapError('')
    let parsedMap = {}
    if (fieldMap.trim()) {
      try {
        parsedMap = JSON.parse(fieldMap)
      } catch (error) {
        setMapError(`The field map is not valid JSON: ${error.message}`)
        return
      }
    }
    const scope =
      kind === 'full_history'
        ? { kind: 'full_history', from: from || null, to: to || null }
        : { kind: 'range', from: from || null, to: to || null }
    setBusy(true)
    try {
      const run = await backfillApi.start(roomId, {
        connection_id: connectionId,
        scope,
        direction,
        field_map: parsedMap,
        page_size: pageSize ? Number(pageSize) : null,
        estimated_records: estimate ? Number(estimate) : null,
        poll_interval_seconds: 0,
      })
      onStarted(run)
    } catch (error) {
      onError(error)
    } finally {
      setBusy(false)
    }
  }

  if (!connections.length) {
    return (
      <Card>
        <EmptyState
          title="No CRM connection to backfill from"
          description="A backfill reads a room's replica from a connected CRM. Declare a connection first — this workflow ships adapters for Salesforce Bulk API 2.0, Dataverse change tracking and the HubSpot exports API."
        />
      </Card>
    )
  }

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <Section
          title="How much history"
          hint={`The volume decides the strategy: more than ${threshold.toLocaleString()} records asks the CRM for an asynchronous job, and at or below it for a paged read. That figure is the vendor's own, and it is why this page can tell you which one you are about to get.`}
        >
          <div className="mt-3 flex flex-col gap-4">
            <fieldset>
              <legend className="text-xs font-medium text-muted-foreground">Range</legend>
              <div className="mt-2 grid gap-1.5 sm:grid-cols-2">
                {(vocabulary?.scopes || ['range', 'full_history']).map((option) => {
                  const selected = option === kind
                  const label =
                    option === 'range' ? 'A date range' : 'Everything the CRM still has'
                  return (
                    <button
                      key={option}
                      type="button"
                      aria-pressed={selected}
                      onClick={() => setKind(option)}
                      className={`inline-flex min-h-11 cursor-pointer items-center rounded-lg px-3 text-left text-sm transition-colors
                        duration-200 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none ${
                          selected
                            ? 'bg-accent/15 font-semibold text-accent'
                            : 'bg-muted text-muted-foreground hover:bg-border-subtle hover:text-foreground'
                        }`}
                    >
                      {label}
                    </button>
                  )
                })}
              </div>
            </fieldset>

            <Field
              label="From (inclusive)"
              id="wf045-from"
              hint="Omit for the full-history option. The lower bound is inclusive and the upper bound is exclusive, so two adjacent ranges never double-count the row on their shared edge."
            >
              <input
                id="wf045-from"
                type="date"
                value={from}
                onChange={(event) => setFrom(event.target.value)}
                className={inputClass}
              />
            </Field>

            <Field label="To (exclusive)" id="wf045-to" hint="Defaults to now.">
              <input
                id="wf045-to"
                type="date"
                value={to}
                onChange={(event) => setTo(event.target.value)}
                className={inputClass}
              />
            </Field>

            <Field
              label="Roughly how many records"
              id="wf045-estimate"
              hint="Optional. Supplying it applies the volume rule up front; leaving it blank lets the room use the vendor's own record count and reconsider once."
            >
              <input
                id="wf045-estimate"
                type="number"
                min="0"
                inputMode="numeric"
                value={estimate}
                onChange={(event) => setEstimate(event.target.value)}
                placeholder="leave blank if you do not know"
                className={inputClass}
              />
            </Field>

            <p className="text-xs text-muted-foreground">
              This run will use <span className="font-mono text-foreground">{willUse}</span>.
            </p>
          </div>
        </Section>
      </Card>

      <Card>
        <Section
          title="Where it comes from, and where it goes"
          hint="A connection is an account-level thing — the researched cursor record is keyed on it — so the same one serves every room."
        >
          <div className="mt-3 flex flex-col gap-4">
            <Field label="Connection" id="wf045-connection">
              <select
                id="wf045-connection"
                value={connectionId}
                onChange={(event) => setConnectionId(event.target.value)}
                className={inputClass}
              >
                {connections.map((connection) => (
                  <option key={connection.id} value={connection.id}>
                    {connection.data.name} — {connection.data.vendor}
                    {connection.supported ? '' : ' (no adapter)'}
                  </option>
                ))}
              </select>
            </Field>

            {chosen && (
              <dl className="grid gap-3 sm:grid-cols-2">
                <Fact term="Mechanism">{chosen.strategies.join(', ') || 'none'}</Fact>
                <Fact term="Cursor">{chosen.data?.replica_key_field || 'id'}</Fact>
                <Fact term="Required scope">
                  {chosen.required_scopes?.join(', ') || 'none documented'}
                </Fact>
                <Fact term="Allowance left today">
                  {chosen.quota?.limited
                    ? `${chosen.quota.remaining} of ${chosen.quota.daily_limit}`
                    : 'no daily limit documented'}
                </Fact>
              </dl>
            )}

            <fieldset>
              <legend className="text-xs font-medium text-muted-foreground">Direction</legend>
              <div className="mt-2 grid gap-1.5 sm:grid-cols-2">
                {(vocabulary?.directions || []).map((option) => {
                  const selected = option.value === direction
                  const blocked = option.value === 'push' && chosen && !chosen.supports_push
                  return (
                    <button
                      key={option.value}
                      type="button"
                      aria-pressed={selected}
                      disabled={blocked}
                      title={blocked ? 'This vendor cannot receive a reverse backfill' : option.meaning}
                      onClick={() => setDirection(option.value)}
                      className={`inline-flex min-h-11 cursor-pointer items-center rounded-lg px-3 text-left text-sm transition-colors
                        duration-200 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50 ${
                          selected
                            ? 'bg-accent/15 font-semibold text-accent'
                            : 'bg-muted text-muted-foreground hover:bg-border-subtle hover:text-foreground'
                        }`}
                    >
                      {option.value === 'pull' ? 'CRM into this room' : 'This room back out to the CRM'}
                    </button>
                  )
                })}
              </div>
            </fieldset>

            <Field
              label={`Page size (at most ${pageCeiling.toLocaleString()})`}
              id="wf045-page"
              hint="A page boundary the vendor did not issue is a resume point that does not exist, so this cannot go above the vendor's own paging count."
            >
              <input
                id="wf045-page"
                type="number"
                min="1"
                max={pageCeiling}
                inputMode="numeric"
                value={pageSize}
                onChange={(event) => setPageSize(event.target.value)}
                placeholder={String(pageCeiling)}
                className={inputClass}
              />
            </Field>

            <Field
              label="Field map (JSON)"
              id="wf045-map"
              hint="CRM field to replica field. Anything the map does not mention is carried through under its own name, so a field your team added later needs no change here."
            >
              <textarea
                id="wf045-map"
                rows={5}
                value={fieldMap}
                onChange={(event) => setFieldMap(event.target.value)}
                spellCheck={false}
                className={`${inputClass} font-mono`}
              />
            </Field>
            {mapError && <Notice tone="danger" title="The field map was not accepted">{mapError}</Notice>}

            <div className="flex flex-wrap gap-2">
              <PathButton
                glyph={BACKFILL_ICON}
                variant="primary"
                onClick={start}
                disabled={busy || !connectionId}
              >
                {busy ? 'Opening…' : 'Start backfill'}
              </PathButton>
              <p className="self-center text-xs text-muted-foreground">
                Starting adopts any cursor this room already holds for the connection, so a
                scheduled backfill continues rather than re-reading.
              </p>
            </div>
          </div>
        </Section>
      </Card>
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Run detail: progress, cursor, and the log
// --------------------------------------------------------------------------- //

function RunLog({ events }) {
  if (!events.length) {
    return <p className="py-4 text-sm text-muted-foreground">No log lines for this run yet.</p>
  }
  const TONE = {
    run_complete: 'text-accent',
    run_failed: 'text-destructive',
    run_stalled: 'text-amber-300',
    rows_rejected: 'text-amber-300',
    quota_refused: 'text-amber-300',
  }
  return (
    <ol className="flex flex-col gap-1.5">
      {events.map((entry) => (
        <li key={entry.id} className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 border-l-2 border-border-subtle/40 pl-3">
          <span className="font-mono text-[11px] text-muted-foreground">{relativeTime(entry.at)}</span>
          <span className={`font-mono text-xs font-semibold ${TONE[entry.event] || 'text-accent'}`}>
            {entry.event}
          </span>
          <span className="min-w-0 flex-1 text-[13px] text-foreground">{entry.detail}</span>
        </li>
      ))}
    </ol>
  )
}

function RunDetail({ runId, roomId, vocabulary, onChanged, onError }) {
  const run = useAsync(() => backfillApi.run(roomId, runId), [roomId, runId, onChanged])
  const log = useAsync(() => backfillApi.log(roomId, runId, { limit: 400 }), [roomId, runId, onChanged])

  if (run.loading && !run.data) return <Spinner label="Loading the run" />
  if (run.error) return <ErrorNote error={run.error} onRetry={run.refetch} />
  if (!run.data) return null

  const data = run.data
  const counters = data.counters || {}
  const percent = progressPercent(data.progress)
  const blocked = blockedByPreflight(data)
  const live = isLive(data)

  async function act(action) {
    try {
      if (action === 'poll') await backfillApi.poll(roomId, runId)
      if (action === 'resume') await backfillApi.resume(roomId, runId)
      if (action === 'cancel') await backfillApi.cancel(roomId, runId, { reason: 'cancelled from the page' })
      run.refetch()
      log.refetch()
      onChanged()
    } catch (error) {
      onError(error)
    }
  }

  return (
    <div className="flex flex-col gap-4">
      {data.data.state === 'stalled' && (
        <Notice tone="warning" title="This run cannot be resumed" icon={CLOCK_ICON}>
          {data.data.failure?.detail ||
            'The vendor will no longer answer for this cursor. Open a new full-history backfill to read the range again.'}
        </Notice>
      )}
      {blocked && (
        <Notice tone="danger" title="No job was created" icon={CLOCK_ICON}>
          <ul className="flex list-disc flex-col gap-1 pl-4">
            {data.data.findings.map((finding) => (
              <li key={finding}>{finding}</li>
            ))}
          </ul>
        </Notice>
      )}
      {data.data.failure?.reason === 'cursor_expired' && data.data.state !== 'stalled' && (
        <Notice tone="warning" title="The cursor has aged out">
          {data.data.failure.detail}
        </Notice>
      )}

      <Card>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="font-mono text-sm text-foreground">
              {data.data.vendor} · {data.data.strategy} · {data.data.direction}
            </p>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {data.data.scope?.kind === 'range'
                ? `${absoluteTime(data.data.scope.from)} → ${absoluteTime(data.data.scope.to)}`
                : `Full history from ${absoluteTime(data.data.scope?.from)}`}
            </p>
          </div>
          <RunStateBadge run={data} vocabulary={vocabulary} />
        </div>

        <div className="mt-4">
          <ProgressBar
            percent={percent}
            indeterminate={Boolean(data.progress?.indeterminate)}
            label={`Progress of backfill ${runId}`}
            note={progressNote(data.progress)}
          />
        </div>

        <dl className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Fact term="Read" mono>
            {counters.rows_seen ?? 0} of {counters.rows_total ?? '?'}
          </Fact>
          <Fact term="Written" mono>
            {counters.rows_written ?? 0} ({counters.rows_created ?? 0} new, {counters.rows_updated ?? 0} changed)
          </Fact>
          <Fact term="Already current" mono>
            {counters.rows_unchanged ?? 0}
          </Fact>
          <Fact term="Rejected" mono>
            {counters.rows_rejected ?? 0}
          </Fact>
          <Fact term="Pages" mono>
            {counters.pages ?? 0}
          </Fact>
          <Fact term="Resumes" mono>
            {counters.resumes ?? 0}
          </Fact>
          <Fact term="Vendor calls" mono>
            {counters.api_calls ?? 0}
          </Fact>
          <Fact term="Next poll">{data.data.next_poll_at ? relativeTime(data.data.next_poll_at) : 'not scheduled'}</Fact>
        </dl>

        <div className="mt-5 border-l-2 border-accent/40 pl-3">
          <p className="font-mono text-[13px] text-foreground">
            {data.data.cursor
              ? `${data.data.cursor.kind}: ${data.data.cursor.cursor ?? 'nothing yet'}`
              : 'no cursor stored yet'}
          </p>
          {data.data.cursor && (
            <p className="mt-0.5 text-xs text-muted-foreground">
              {data.data.cursor.vendor} · connection {data.data.cursor.connectionId} · recorded{' '}
              {absoluteTime(data.data.cursor.updatedAt)}
            </p>
          )}
          <p className="mt-1 text-xs text-muted-foreground">{data.data.strategy_reason?.detail}</p>
        </div>

        <div className="mt-5 flex flex-wrap gap-2">
          <PathButton glyph={POLL_ICON} onClick={() => act('poll')} disabled={!live}>
            Run one poll
          </PathButton>
          <PathButton glyph={BACKFILL_ICON} onClick={() => act('resume')} disabled={!live}>
            Resume from the cursor
          </PathButton>
          {live && (
            <Button icon="close" onClick={() => act('cancel')}>
              Cancel
            </Button>
          )}
        </div>
        <p className="mt-2 text-xs text-muted-foreground">
          “Run one poll” respects the run's fixed interval and will say so if the run is not due.
          “Resume” ignores it, because a room that has just restarted has not been respecting
          anybody's interval.
        </p>
      </Card>

      <Section
        title="Per-run log"
        hint="Every state change, every page write and every cursor move, in the order they happened. This is the record an operator reads to answer “what happened to this backfill”."
      >
        <Card>
          {log.loading && !log.data ? <Spinner label="Loading the log" /> : <RunLog events={log.data?.events || []} />}
        </Card>
      </Section>
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Cursors
// --------------------------------------------------------------------------- //

const CURSOR_COLUMNS = [
  {
    key: 'vendor',
    header: 'Vendor',
    render: (row) => (
      <span className="flex items-center gap-1.5 font-mono text-[13px]">
        <Icon path={BACKFILL_ICON} size={14} />
        {row.vendor}
      </span>
    ),
  },
  { key: 'kind', header: 'Cursor kind', render: (row) => <Badge>{row.kind}</Badge> },
  {
    key: 'cursor',
    header: 'Value',
    render: (row) => <span className="font-mono text-[13px]">{row.cursor || '—'}</span>,
  },
  {
    key: 'updatedAt',
    header: 'Recorded',
    render: (row) => <span className="text-[13px]">{absoluteTime(row.updatedAt)}</span>,
  },
  {
    key: 'age',
    header: 'Age',
    render: (row) => <span className="font-mono text-[13px]">{row.expiry?.age_days ?? 0}d</span>,
  },
  {
    key: 'resumable',
    header: 'Resumable',
    render: (row) =>
      row.expiry?.expired ? (
        <span className="flex items-center gap-1.5 text-amber-300">
          <Icon path={CLOCK_ICON} size={14} />
          aged out
        </span>
      ) : row.expiry?.applies ? (
        <span className="text-[13px]">until {absoluteTime(row.expiry.resumable_until)}</span>
      ) : (
        <span className="text-[13px] text-muted-foreground">until the job ends</span>
      ),
  },
  {
    key: 'connection',
    header: 'Connection',
    render: (row) => (
      <span className="font-mono text-[11px] break-all text-muted-foreground">
        {row.expiry?.connection_known ? row.connectionId : 'connection not found'}
      </span>
    ),
  },
]

// --------------------------------------------------------------------------- //
// Replica
// --------------------------------------------------------------------------- //

const REPLICA_COLUMNS = [
  {
    key: 'external_id',
    header: 'CRM id',
    render: (row) => <span className="font-mono text-[13px] text-accent">{row.data.external_id}</span>,
  },
  {
    key: 'title',
    header: 'Name',
    render: (row) => <span className="text-[13px]">{row.data.title || row.data.Name || '—'}</span>,
  },
  {
    key: 'stage',
    header: 'Stage',
    render: (row) => <span className="text-[13px]">{row.data.stage || row.data.Stage__c || '—'}</span>,
  },
  {
    key: 'vendor',
    header: 'From',
    render: (row) => <span className="font-mono text-[13px]">{row.data.vendor}</span>,
  },
  {
    key: 'last_seen_at',
    header: 'Content last changed',
    render: (row) => <span className="text-[13px]">{relativeTime(row.data.last_seen_at)}</span>,
  },
  {
    key: 'first_seen_at',
    header: 'First landed',
    render: (row) => <span className="text-[13px]">{relativeTime(row.data.first_seen_at)}</span>,
  },
]

// --------------------------------------------------------------------------- //
// The contract tab: vendors and the decision register
// --------------------------------------------------------------------------- //

function VendorCard({ vendor }) {
  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="font-mono text-sm font-semibold text-foreground">{vendor.label}</p>
          <p className="mt-0.5 text-xs text-muted-foreground">{vendor.mechanism}</p>
        </div>
        <Badge tone={vendor.documented ? 'insert' : 'neutral'}>
          {vendor.documented ? 'researched' : 'third party'}
        </Badge>
      </div>
      <dl className="mt-3 grid gap-2 sm:grid-cols-2">
        <Fact term="Strategies" mono>
          {vendor.strategies.join(', ') || 'none'}
        </Fact>
        <Fact term="Cursor kind" mono>
          {vendor.cursor_kind}
        </Fact>
        <Fact term="Accepts a push">{vendor.supports_push ? 'yes' : 'no'}</Fact>
        <Fact term="Required scopes" mono>
          {vendor.required_scopes.join(', ') || 'none documented'}
        </Fact>
      </dl>
    </Card>
  )
}

function InferenceCard({ entry }) {
  return (
    <Card>
      <p className="font-mono text-sm font-semibold text-foreground">{entry.topic}</p>
      <p className="mt-0.5 text-xs text-muted-foreground">
        <span className="font-mono text-accent">{entry.id}</span> — this workflow is a build, and the
        research fixes the numbers without fixing the edges. This is the edge.
      </p>
      <div className="mt-3 border-l-2 border-accent/40 pl-3">
        <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Basis</p>
        <p className="mt-1 text-[13px] text-foreground">{entry.basis}</p>
      </div>
      <div className="mt-3 border-l-2 border-border-subtle/50 pl-3">
        <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Why</p>
        <p className="mt-1 text-[13px] text-foreground">{entry.why}</p>
      </div>
      <p className="mt-3 text-xs text-muted-foreground">
        <span className="font-medium text-foreground">Change it:</span> {entry.change_it}
      </p>
    </Card>
  )
}

// --------------------------------------------------------------------------- //
// Page
// --------------------------------------------------------------------------- //

export default function BackfillHistory() {
  const [tab, setTab] = useState('runs')
  const [roomId, setRoomId] = useState('')
  const [selectedRun, setSelectedRun] = useState('')
  const [stateFilter, setStateFilter] = useState('')
  const [nonce, setNonce] = useState(0)
  const [actionError, setActionError] = useState(null)

  const vocabulary = useAsync(() => backfillApi.vocabulary(), [])
  const vendors = useAsync(() => backfillApi.vendors(), [])
  const inferences = useAsync(() => backfillApi.inferences(), [])
  const rooms = useAsync(() => backfillApi.rooms(), [])

  useEffect(() => {
    if (!roomId && rooms.data?.records?.length) setRoomId(rooms.data.records[0].id)
  }, [rooms.data, roomId])

  const connections = useAsync(() => backfillApi.connections(), [nonce])
  const runs = useAsync(
    () => (roomId ? backfillApi.runs(roomId, { state: stateFilter, limit: 100 }) : Promise.resolve(null)),
    [roomId, stateFilter, nonce],
  )
  const cursors = useAsync(() => (roomId ? backfillApi.cursors(roomId) : Promise.resolve(null)), [roomId, nonce])
  const replica = useAsync(() => (roomId ? backfillApi.replica(roomId, { limit: 100 }) : Promise.resolve(null)), [roomId, nonce])
  const summary = useAsync(() => (roomId ? backfillApi.summary(roomId) : Promise.resolve(null)), [roomId, nonce])

  const failure = [vocabulary, vendors, inferences, rooms, connections, runs, cursors, replica, summary].find(
    (state) => state.error,
  )
  if (failure) return <ErrorNote error={failure.error} onRetry={() => setNonce((n) => n + 1)} />

  const roomOptions = rooms.data?.records || []
  if (!roomId) {
    return (
      <div className="flex flex-col gap-6">
        <h1 className="font-mono text-xl font-semibold text-foreground">Backfill history</h1>
        {rooms.loading ? <Spinner label="Loading rooms" /> : <EmptyState title="No rooms yet" description="A backfill lands in a room's replica, so it needs a room to land in." />}
      </div>
    )
  }

  const listed = runs.data?.backfills || []
  const { live, done } = groupByState(listed)
  const numbers = summary.data
  const stalled = numbers?.stalled || []

  const RUN_COLUMNS = [
    {
      key: 'state',
      header: 'State',
      render: (row) => <RunStateBadge run={row} vocabulary={vocabulary.data} />,
    },
    {
      key: 'vendor',
      header: 'Source',
      render: (row) => (
        <span className="font-mono text-[13px]">
          {row.data.vendor} · {row.data.strategy}
        </span>
      ),
    },
    {
      key: 'scope',
      header: 'Range',
      render: (row) => (
        <span className="text-[13px] text-muted-foreground">
          {row.data.scope?.kind === 'range'
            ? `${relativeTime(row.data.scope.from)} → ${relativeTime(row.data.scope.to)}`
            : `full history${row.data.adopted_cursor ? ' · continued' : ''}`}
        </span>
      ),
    },
    { key: 'progress', header: 'Progress', render: (row) => <RunProgress run={row} /> },
    {
      key: 'rows',
      header: 'Rows',
      render: (row) => (
        <span className="font-mono text-[13px]">
          {row.counters?.rows_written ?? 0}
          {row.counters?.rows_unchanged ? (
            <span className="text-muted-foreground"> +{row.counters.rows_unchanged} same</span>
          ) : null}
        </span>
      ),
    },
    {
      key: 'cursor',
      header: 'Cursor',
      render: (row) => (
        <span className="font-mono text-[11px] text-muted-foreground">
          {row.data.cursor?.cursor || '—'}
        </span>
      ),
    },
    {
      key: 'open',
      header: 'Open',
      render: (row) => (
        <button
          type="button"
          onClick={() => {
            setSelectedRun(row.id)
            setTab('runs')
          }}
          className="inline-flex min-h-11 cursor-pointer items-center rounded-lg px-3 text-sm text-accent transition-colors duration-200 hover:bg-accent/10 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
        >
          Open log
        </button>
      ),
    },
  ]

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="font-mono text-xl font-semibold text-foreground">Backfill history</h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            Read a room's CRM history into its replica on a schedule, keeping a cursor so a crash
            restarts with no duplicates and no gaps. Progress is a percentage and never an ETA:
            Salesforce states that its asynchronous Bulk APIs guarantee no service level agreement.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor="wf045-room" className="text-xs font-medium text-muted-foreground">
            Room
          </label>
          <select
            id="wf045-room"
            value={roomId}
            onChange={(event) => {
              setRoomId(event.target.value)
              setSelectedRun('')
            }}
            className={`${inputClass} max-w-xs`}
          >
            {roomOptions.map((room) => (
              <option key={room.id} value={room.id}>
                {room.data.name}
              </option>
            ))}
          </select>
          <Button icon="refresh" onClick={() => setNonce((n) => n + 1)}>
            Refresh
          </Button>
        </div>
      </header>

      {stalled.length > 0 && (
        <Notice tone="warning" title={`${stalled.length} run cannot be resumed`} icon={CLOCK_ICON}>
          Its cursor is older than the window the vendor will still answer for. Reading the range
          again under the same run would write every row twice, so these are stalled on purpose:
          open a new full-history backfill.
        </Notice>
      )}

      {actionError && (
        <Notice tone="danger" title="The room refused that" onDismiss={() => setActionError(null)}>
          {String(actionError.message || actionError)}
        </Notice>
      )}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="Runs"
          value={numbers?.runs ?? 0}
          hint={`${live.length} still moving`}
          icon="audit"
        />
        <StatCard
          label="Rows written"
          value={numbers?.totals?.rows_written ?? 0}
          hint={`${numbers?.totals?.rows_unchanged ?? 0} came back already current`}
          icon="database"
        />
        <StatCard
          label="Replica rows"
          value={numbers?.replica_rows ?? 0}
          hint={numbers?.replica_rows_truncated ? 'at least this many — the listing caps at 1,000' : 'in this room'}
          icon="rooms"
        />
        <StatCard
          label="Stored cursors"
          value={numbers?.stored_cursors ?? 0}
          hint={`${stalled.length} aged out`}
          icon="schema"
        />
      </div>

      <TabBar current={tab} onChange={setTab} />

      <div
        role="tabpanel"
        id={`wf045-panel-${tab}`}
        aria-labelledby={`wf045-tab-${tab}`}
        className="flex flex-col gap-6"
      >
        {tab === 'runs' && (
          <div className="flex flex-col gap-6">
            {selectedRun ? (
              <>
                <div>
                  <button
                    type="button"
                    onClick={() => setSelectedRun('')}
                    className="inline-flex min-h-11 cursor-pointer items-center gap-1.5 rounded-lg px-3 text-sm text-muted-foreground transition-colors duration-200 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                  >
                    <Icon name="chevron" size={14} className="rotate-180" />
                    All runs
                  </button>
                </div>
                <RunDetail
                  runId={selectedRun}
                  roomId={roomId}
                  vocabulary={vocabulary.data}
                  onChanged={() => setNonce((n) => n + 1)}
                  onError={setActionError}
                />
              </>
            ) : (
              <>
                <Section
                  title="Backfill runs"
                  hint="Every run in this room, newest first. A run with no cursor yet has not been asked for anything; a run with rows that came back unchanged has proved there is nothing to do."
                  action={
                    <label className="flex items-center gap-2 text-xs text-muted-foreground">
                      <span className="sr-only sm:not-sr-only">State</span>
                      <select
                        value={stateFilter}
                        onChange={(event) => setStateFilter(event.target.value)}
                        className={`${inputClass} max-w-[12rem]`}
                      >
                        <option value="">Any state</option>
                        {(vocabulary.data?.run_states || []).map((entry) => (
                          <option key={entry.value} value={entry.value}>
                            {entry.value}
                            {entry.terminal ? ' (finished)' : ''}
                          </option>
                        ))}
                      </select>
                    </label>
                  }
                >
                  <Card>
                    <DataTable
                      columns={RUN_COLUMNS}
                      rows={listed}
                      rowKey={(row) => row.id}
                      empty="No backfill has been opened for this room. Pick a range in Start a backfill, and the room will ask the CRM for a job and keep a cursor to resume from."
                    />
                  </Card>
                </Section>
                {live.length > 0 && (
                  <p className="text-xs text-muted-foreground">
                    {live.length} run{live.length === 1 ? '' : 's'} still moving, {done.length}{' '}
                    finished. This page does not poll on its own: the room runs a poller on a fixed
                    interval, so nothing is asked of the vendor until its turn comes — which
                    matters, because the research documents a <em>daily</em> limit on one of these
                    vendors.
                  </p>
                )}
              </>
            )}
          </div>
        )}

        {tab === 'wizard' && (
          <StartWizard
            vocabulary={vocabulary.data}
            connections={connections.data?.connections || []}
            roomId={roomId}
            onError={setActionError}
            onStarted={(run) => {
              setSelectedRun(run.id)
              setNonce((n) => n + 1)
            }}
          />
        )}

        {tab === 'cursors' && (
          <Section
            title="Stored cursors"
            hint="The research's own record — vendor, connection, cursor, updatedAt — with the day each one stops being resumable. A Dataverse DataToken ages out after seven days; a Bulk job id or an export id is a handle to a job that is either still there or is not, and a fixed window would be a rule the vendor never stated."
          >
            <Card>
              <DataTable
                columns={CURSOR_COLUMNS}
                rows={cursors.data?.cursors || []}
                rowKey={(row) => row.id}
                empty="This room holds no cursor. A cursor is written after the first page lands, and it is what a crash restarts from."
              />
            </Card>
          </Section>
        )}

        {tab === 'replica' && (
          <Section
            title="Room replica"
            hint="The rows a backfill landed, in the room's own schema-flexible store. Anything a CRM field map did not name is carried through under its own name, so a field your team added later is here without a code change."
          >
            <Card>
              <DataTable
                columns={REPLICA_COLUMNS}
                rows={replica.data?.rows || []}
                rowKey={(row) => row.id}
                empty="Nothing has landed in this room's replica yet."
              />
            </Card>
          </Section>
        )}

        {tab === 'contract' && (
          <div className="flex flex-col gap-6">
            <Section
              title="Registered vendors"
              hint="The research's extensibility note says a third party can add a vendor by implementing only “create job” and “read page”. This is where that is a property of the code rather than of the documentation."
            >
              <div className="grid gap-3 lg:grid-cols-2">
                {(vendors.data?.vendors || []).map((vendor) => (
                  <VendorCard key={vendor.vendor} vendor={vendor} />
                ))}
              </div>
              <p className="mt-3 text-xs text-muted-foreground">
                Interface: {(vendors.data?.interface || []).join(', ')}. The volume rule that picks
                between them is {(vendors.data?.bulk_threshold_records ?? 0).toLocaleString()} records,
                and it is the vendor's own figure.
              </p>
            </Section>

            <Section
              title="Decisions this build took"
              hint={`${inferences.data?.count ?? 0} judgement calls, each with what the research says, what this build chose, and how to change it. Served as data so a reviewer reads the list instead of reconstructing it from a diff.`}
            >
              <div className="grid gap-3 lg:grid-cols-2">
                {(inferences.data?.inferences || []).map((entry) => (
                  <InferenceCard key={entry.id} entry={entry} />
                ))}
              </div>
            </Section>
          </div>
        )}
      </div>
    </div>
  )
}
