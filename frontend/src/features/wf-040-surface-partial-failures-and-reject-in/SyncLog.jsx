import { useState } from 'react'

import { Button, Card, EmptyState, ErrorNote, Field, JsonView, Spinner, StatCard, useAsync, inputClass } from '@/components/ui'
import { absoluteTime, relativeTime } from '@/lib/api'

import { syncApi } from './api'
import Glyph, { SYNC_LOG_ICON } from './icons'
import { DocLink, PropertyCell, StatusChip, StatusFilter } from './primitives'

/** Every room the demo dataset has, so the page can scope itself to one. */
async function loadRooms() {
  const body = await fetch('/api/records/room?limit=100').then((response) => response.json())
  return body.records || []
}

/**
 * The Sync log, for WF-040.
 *
 * The page is the researched user flow, in the order the research states it. Read
 * the log; click a failed row for the field-level detail; fix the mapping; retry
 * the failed rows only. So the table's columns are the researched ones, the status
 * chip carries both the outcome and the waiting state, and the retry control is
 * scoped to one run and says which rows it will and will not re-send.
 *
 * Two things on this page are this build's judgement rather than the source's, and
 * both are shown rather than hidden. The retryable-versus-terminal boundary, which
 * the page reads from the vocabulary and labels per row with the reason it was
 * classified that way, and the inferences list, which is rendered from
 * `/inferences` under a heading that says so. A rep should be able to tell which
 * numbers came from a vendor's own error contract and which came from us.
 *
 * Everything is server-side. The classification is computed per row from that row's
 * own outcome, and a page that filtered in the browser would be showing a different
 * picture from the one the next person gets.
 */
function SyncLogPage() {
  const [roomId, setRoomId] = useState('')
  const [filters, setFilters] = useState({ status: '', disposition: '', connector: '' })
  const [selected, setSelected] = useState(null)
  const [retryRunId, setRetryRunId] = useState('')
  const [drainResult, setDrainResult] = useState(null)
  const [action, setAction] = useState(null)

  const rooms = useAsync(loadRooms, [])
  const currentRoom = roomId || rooms.data?.[0]?.id || ''

  const vocabulary = useAsync(() => syncApi.vocabulary(), [])
  const connectors = useAsync(() => syncApi.connectors(), [])
  const queue = useAsync(() => syncApi.queue(currentRoom ? { room_id: currentRoom } : {}), [currentRoom])
  const log = useAsync(
    () =>
      currentRoom
        ? syncApi.syncLog(currentRoom, {
            status: filters.status,
            disposition: filters.disposition,
            connector: filters.connector,
          })
        : Promise.resolve(null),
    [currentRoom, filters.status, filters.disposition, filters.connector],
  )
  const runs = useAsync(
    () => (currentRoom ? syncApi.runs({ room_id: currentRoom }) : Promise.resolve(null)),
    [currentRoom],
  )

  const detail = useAsync(
    () => (selected ? syncApi.row(currentRoom, selected) : Promise.resolve(null)),
    [currentRoom, selected],
  )

  const inferences = useAsync(() => syncApi.inferences(), [])

  if (vocabulary.loading || connectors.loading || rooms.loading) {
    return <Spinner label="Loading the Sync log" />
  }
  if (vocabulary.error) return <ErrorNote error={vocabulary.error} onRetry={vocabulary.refetch} />
  if (rooms.error) return <ErrorNote error={rooms.error} onRetry={rooms.refetch} />

  const published = vocabulary.data
  const labels = published.row_status_labels || {}
  const dispositionLabels = published.disposition_labels || {}
  const summary = log.data?.summary || {}
  const rows = log.data?.rows || []
  const connectorNames = Object.fromEntries(
    (connectors.data?.connectors || []).map((entry) => [entry.id, entry.label]),
  )

  function refetchAll() {
    log.refetch()
    queue.refetch()
    runs.refetch()
  }

  async function onRetry() {
    if (!retryRunId) return
    setAction('retry')
    try {
      const first = await syncApi.retryRun(retryRunId)
      setDrainResult({ kind: 'retry', ...first })
      if (first.awaiting_vendor_response) {
        setDrainResult({
          kind: 'retry',
          ...first,
          detail: `${first.sent.length} failed row(s) to re-send. ${first.untouched.length} succeeded row(s) are not in the list and will not be re-sent.`,
        })
      } else {
        setDrainResult({
          kind: 'retry',
          ...first,
          detail: `${first.succeeded_now} row(s) accepted, ${first.still_failed} still failed, ${first.untouched.length} succeeded row(s) left alone.`,
        })
      }
      refetchAll()
      if (selected) detail.refetch()
    } catch (error) {
      setDrainResult({ kind: 'retry', error: String(error.message || error) })
    } finally {
      setAction(null)
    }
  }

  async function onDrain() {
    setAction('drain')
    try {
      // as_of an hour ahead, so the queue's own backoff has elapsed. The schedule is
      // a rule, not advice - and this is how a caller gets past it deliberately.
      const asOf = new Date(Date.now() + 3600_000).toISOString()
      const body = await syncApi.drain({ as_of: asOf })
      setDrainResult({
        kind: 'drain',
        detail: `${body.sent.length} row(s) due to send, ${body.scheduled.length} still backing off, ${body.expired_now.length} moved to "needs a person", ${body.succeeded_not_resent} succeeded row(s) not re-sent.`,
        body,
      })
      refetchAll()
      if (selected) detail.refetch()
    } catch (error) {
      setDrainResult({ kind: 'drain', error: String(error.message || error) })
    } finally {
      setAction(null)
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="flex items-center gap-2 text-xl font-semibold text-foreground">
            <Glyph path={SYNC_LOG_ICON} size={22} className="text-accent" />
            Sync log
          </h1>
          <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
            Per-record outcomes from {published.connector_ids.map((id) => connectorNames[id] || id).join(', ')},
            collapsed into one error model. Each failed row carries the offending property, and a retry
            re-sends the failed rows only.
          </p>
        </div>
        <Button icon="refresh" onClick={refetchAll}>
          Refresh
        </Button>
      </header>

      <section aria-label="Choose a room" className="grid gap-4 sm:grid-cols-3">
        <Field label="Room" id="wf040-room" hint="The Sync log is scoped to one deal.">
          <select
            id="wf040-room"
            className={`${inputClass} cursor-pointer`}
            value={currentRoom}
            onChange={(event) => {
              setRoomId(event.target.value)
              setSelected(null)
            }}
          >
            {(rooms.data || []).map((room) => (
              <option key={room.id} value={room.id}>
                {room.data?.name || room.id}
              </option>
            ))}
          </select>
        </Field>
        <StatCard label="Failed rows" value={summary.failed ?? 0} hint="on this room" icon="audit" />
        <StatCard
          label="Needs a person"
          value={summary.needs_action ?? 0}
          hint="terminal; no retry will clear them"
          icon="audit"
        />
      </section>

      <section aria-label="Filter the Sync log">
        <div className="flex flex-wrap items-center gap-2">
          <StatusFilter
            active={!filters.status && !filters.disposition && !filters.connector}
            onClick={() => setFilters({ status: '', disposition: '', connector: '' })}
          >
            All rows ({summary.rows ?? 0})
          </StatusFilter>
          {published.row_statuses.map((status) => (
            <StatusFilter
              key={status}
              tone={status === 'succeeded' ? 'insert' : 'delete'}
              active={filters.status === status}
              onClick={() =>
                setFilters((previous) => ({
                  ...previous,
                  status: previous.status === status ? '' : status,
                }))
              }
            >
              {labels[status]} ({summary[status] ?? 0})
            </StatusFilter>
          ))}
          {published.dispositions.map((disposition) => (
            <StatusFilter
              key={disposition}
              active={filters.disposition === disposition}
              onClick={() =>
                setFilters((previous) => ({
                  ...previous,
                  disposition: previous.disposition === disposition ? '' : disposition,
                }))
              }
            >
              {dispositionLabels[disposition]} ({summary[disposition] ?? 0})
            </StatusFilter>
          ))}
          {published.connector_ids.map((connector) => (
            <StatusFilter
              key={connector}
              active={filters.connector === connector}
              onClick={() =>
                setFilters((previous) => ({
                  ...previous,
                  connector: previous.connector === connector ? '' : connector,
                }))
              }
            >
              {connectorNames[connector] || connector}
            </StatusFilter>
          ))}
        </div>
        <p className="mt-2 text-xs text-muted-foreground">
          {summary.with_property ?? 0} of {summary.rows ?? 0} rows name the offending property, and{' '}
          {summary.with_doc_link ?? 0} carry a vendor documentation link.
        </p>
      </section>

      <section aria-label="The Sync log rows" className="glass overflow-hidden rounded-xl">
        {log.error ? (
          <div className="p-5">
            <ErrorNote error={log.error} onRetry={log.refetch} />
          </div>
        ) : log.loading ? (
          <Spinner label="Reading the Sync log" />
        ) : rows.length === 0 ? (
          <EmptyState
            title="No rows match"
            description="Clear a filter, or record a sync run for this room."
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-sm">
              <caption className="sr-only">
                One row per sync outcome: its status, the offending property, the reason, and when
                it was last attempted.
              </caption>
              <thead className="border-b border-border-subtle/30">
                <tr>
                  <th scope="col" className="px-3 py-2 text-left font-mono text-xs uppercase">Row</th>
                  <th scope="col" className="px-3 py-2 text-left font-mono text-xs uppercase">Status</th>
                  <th scope="col" className="px-3 py-2 text-left font-mono text-xs uppercase">Property</th>
                  <th scope="col" className="px-3 py-2 text-left font-mono text-xs uppercase">Reason</th>
                  <th scope="col" className="px-3 py-2 text-left font-mono text-xs uppercase">Last attempt</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr
                    key={row.id}
                    className={`border-b border-border-subtle/20 last:border-0 ${
                      selected === row.id ? 'bg-muted/40' : ''
                    }`}
                  >
                    <td className="px-3 py-3">
                      <button
                        type="button"
                        onClick={() => setSelected(row.id)}
                        aria-expanded={selected === row.id}
                        className="min-h-11 cursor-pointer rounded-md text-left hover:text-accent
                          focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2
                          focus-visible:ring-offset-background"
                      >
                        <span className="block font-mono text-foreground">{row.row_key}</span>
                        <span className="block text-xs text-muted-foreground">
                          {connectorNames[row.connector] || row.connector}
                          {row.entity ? ` · ${row.entity}` : ''}
                          {row.attempts > 1 ? ` · attempt ${row.attempts}` : ''}
                        </span>
                      </button>
                    </td>
                    <td className="px-3 py-3">
                      <StatusChip
                        status={row.status}
                        disposition={row.disposition}
                        labels={labels}
                        dispositions={dispositionLabels}
                      />
                    </td>
                    <td className="px-3 py-3">
                      <PropertyCell row={row} />
                    </td>
                    <td className="max-w-md px-3 py-3">
                      <span className="block text-foreground">{row.reason || '—'}</span>
                      <span className="mt-0.5 block font-mono text-xs text-muted-foreground">
                        {row.code || '—'}
                        {row.next_retry_at ? ` · next attempt ${relativeTime(row.next_retry_at)}` : ''}
                      </span>
                    </td>
                    <td className="px-3 py-3 text-muted-foreground">
                      {row.last_attempt_at ? relativeTime(row.last_attempt_at) : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {selected && (
        <RowDetail
          state={detail}
          connectorNames={connectorNames}
          onClose={() => setSelected(null)}
        />
      )}

      <QueuePanel
        queue={queue}
        runs={runs}
        retryRunId={retryRunId}
        onRetryRun={setRetryRunId}
        action={action}
        onRetry={onRetry}
        onDrain={onDrain}
        result={drainResult}
        onDismissResult={() => setDrainResult(null)}
      />

      <ConnectorPanel connectors={connectors.data} />
      <InferencesPanel inferences={inferences} />
    </div>
  )
}

/**
 * One row in full: which property, what was sent, what was expected.
 *
 * The researched detail view, and the three answers a rep needs in order to fix the
 * mapping rather than guess at it. `basis` is shown because the room classifies
 * errors on its own, and a rep who is told "this will not be retried" deserves to
 * be told why as well as what.
 */
function RowDetail({ state, connectorNames, onClose }) {
  const { data, loading, error, refetch } = state
  if (loading) return <Card><Spinner label="Loading the row" /></Card>
  if (error) return <Card><ErrorNote error={error} onRetry={refetch} /></Card>
  if (!data) return null

  const { detail, retry, history, run } = data

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <h2 className="font-mono text-sm font-semibold text-foreground">
          {data.row.row_key}
          <span className="ml-2 font-sans text-xs font-normal text-muted-foreground">
            {connectorNames[data.row.connector] || data.row.connector}
          </span>
        </h2>
        <Button onClick={onClose}>Close</Button>
      </div>

      <div className="mt-4 grid gap-6 lg:grid-cols-2">
        <section aria-label="What the room expected">
          <h3 className="font-mono text-xs uppercase text-muted-foreground">
            Which property, what was sent, what was expected
          </h3>
          <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
            <dt className="text-muted-foreground">Property</dt>
            <dd className="font-mono">
              {detail.property || 'none named'}
              {detail.property_source && (
                <span className="ml-1 font-sans text-xs text-muted-foreground">
                  ({detail.property_source === 'vendor' ? 'the vendor said so' : 'the room supplied it'})
                </span>
              )}
            </dd>
            <dt className="text-muted-foreground">Sent</dt>
            <dd className="break-all font-mono">
              {detail.property ? String(detail.sent ?? '—') : '—'}
              {detail.sent_length != null && (
                <span className="ml-1 text-xs text-muted-foreground">({detail.sent_length} characters)</span>
              )}
            </dd>
            <dt className="text-muted-foreground">Expected</dt>
            <dd>
              {detail.expected.length === 0 ? (
                <span className="text-muted-foreground">the room holds no rule for this property</span>
              ) : (
                <ul className="flex flex-col gap-0.5">
                  {detail.expected.map((item) => (
                    <li key={item.rule_id} className="font-mono">
                      {item.expected}
                    </li>
                  ))}
                </ul>
              )}
            </dd>
            <dt className="text-muted-foreground">Code</dt>
            <dd className="font-mono">{detail.code || '—'}</dd>
            <dt className="text-muted-foreground">Reason</dt>
            <dd>{detail.reason || '—'}</dd>
            <dt className="text-muted-foreground">Retryable</dt>
            <dd>{String(detail.retryable)}</dd>
          </dl>
          <div className="mt-3">
            <DocLink href={detail.doc_link} />
          </div>
        </section>

        <section aria-label="Why the room classified it that way">
          <h3 className="font-mono text-xs uppercase text-muted-foreground">
            Why the room classified it that way
          </h3>
          <p className="mt-2 text-sm text-muted-foreground">{detail.basis}</p>
          {detail.matched_rule && (
            <p className="mt-1 font-mono text-xs text-muted-foreground/80">
              matched rule: {detail.matched_rule}
            </p>
          )}
          {detail.hold_message && (
            <p className="mt-3 border-l-2 border-border-subtle/40 pl-3 text-sm text-foreground">
              {detail.hold_message}
            </p>
          )}
          <dl className="mt-4 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
            <dt className="text-muted-foreground">Attempts</dt>
            <dd className="font-mono">{retry.attempts} of an automatic bound of {retry.max_attempts}</dd>
            <dt className="text-muted-foreground">Waiting for</dt>
            <dd>{retry.disposition ? dispositionText(retry.disposition) : 'nothing'}</dd>
            <dt className="text-muted-foreground">Next attempt</dt>
            <dd>{retry.next_retry_at ? absoluteTime(retry.next_retry_at) : 'not scheduled'}</dd>
            <dt className="text-muted-foreground">Correlated by</dt>
            <dd className="font-mono">
              {detail.correlation_basis}
              {detail.correlation ? `: ${detail.correlation}` : ''}
            </dd>
          </dl>
          <p className="mt-2 text-xs text-muted-foreground">{retry.bound_is}</p>
          {run && (
            <p className="mt-3 text-xs text-muted-foreground">
              From a {run.http_status} out of {connectorNames[run.connector] || run.connector}
              {run.per_record ? ' with per-record outcomes' : ' with no per-record outcomes'}.{' '}
              {run.notes?.[0] || ''}
            </p>
          )}
        </section>
      </div>

      {detail.related.length > 0 && (
        <section aria-label="The other errors on the same row" className="mt-4">
          <h3 className="font-mono text-xs uppercase text-muted-foreground">
            The other errors this row reported
          </h3>
          <ul className="mt-1 flex flex-col gap-0.5 text-sm text-muted-foreground">
            {detail.related.map((item, index) => (
              <li key={`${item.code}-${index}`} className="font-mono">
                {item.code} {item.field ? `on ${item.field}` : ''} — {item.message}
              </li>
            ))}
          </ul>
        </section>
      )}

      {history.length > 0 && (
        <section aria-label="Every attempt" className="mt-4">
          <h3 className="font-mono text-xs uppercase text-muted-foreground">
            Every attempt ({history.length})
            {data.history_truncated ? ' — oldest dropped' : ''}
          </h3>
          <ol className="mt-1 flex flex-col gap-0.5 text-sm">
            {history.map((entry, index) => (
              <li key={`${entry.at}-${index}`} className="flex flex-wrap gap-2 text-muted-foreground">
                <span className="font-mono text-xs">{entry.at}</span>
                <span className="font-mono text-xs">attempt {entry.attempt}</span>
                <span>{entry.code || entry.status}</span>
              </li>
            ))}
          </ol>
        </section>
      )}

      <details className="mt-4">
        <summary className="min-h-11 cursor-pointer list-none font-mono text-sm text-foreground hover:text-accent
          focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2
          focus-visible:ring-offset-background">
          What the vendor actually said
        </summary>
        <div className="mt-2 rounded-lg bg-background/40 p-3">
          <JsonView value={detail.raw} />
        </div>
      </details>
    </Card>
  )
}

function dispositionText(disposition) {
  return (
    {
      queued: 'the automatic retry queue',
      needs_action: 'a person',
      resolved: 'nothing — it was fixed',
    }[disposition] || disposition
  )
}

/**
 * The researched automation, and the admin action.
 *
 * The two researched waiting states are shown side by side because the research
 * draws the line between them precisely and a rep needs to see which side of it a
 * row is on. The retry control is scoped to one run and reports what it left alone,
 * because "Retry failed rows only" is a promise about the successes and a control
 * that cannot show it kept them is a promise nobody can check.
 */
function QueuePanel({
  queue,
  runs,
  retryRunId,
  onRetryRun,
  action,
  onRetry,
  onDrain,
  result,
  onDismissResult,
}) {
  const { data, loading, error, refetch } = queue
  const counts = data?.counts || {}
  const runOptions = runs.data?.runs || []

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="font-mono text-sm font-semibold text-foreground">The retry queue</h2>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            Retryable classes drain on their own; terminal ones wait for a person. A row past the
            automatic bound is moved to &ldquo;needs a person&rdquo; rather than retried forever.
          </p>
        </div>
        <Button icon="refresh" onClick={refetch}>
          Refresh
        </Button>
      </div>

      {loading && <Spinner label="Reading the queue" />}
      {error && <ErrorNote error={error} onRetry={refetch} />}

      {data && (
        <>
          <dl className="mt-4 grid gap-x-6 gap-y-1 text-sm sm:grid-cols-4">
            <dt className="text-muted-foreground">Due now</dt>
            <dd className="font-mono">{counts.drain ?? 0}</dd>
            <dt className="text-muted-foreground">Backing off</dt>
            <dd className="font-mono">{counts.scheduled ?? 0}</dd>
            <dt className="text-muted-foreground">Past the bound</dt>
            <dd className="font-mono">{counts.expired ?? 0}</dd>
            <dt className="text-muted-foreground">Needs a person</dt>
            <dd className="font-mono">{counts.waiting ?? 0}</dd>
          </dl>
          <p className="mt-2 text-xs text-muted-foreground">
            Automatic bound: {data.max_attempts} attempts. Backoff: {data.backoff}. This room opens
            no socket and holds no vendor credential, so draining reports the rows for the connector
            to send rather than sending them.
          </p>

          {(data.drain.length > 0 || data.scheduled.length > 0) && (
            <div className="mt-4 flex flex-col gap-2">
              <h3 className="font-mono text-xs uppercase text-muted-foreground">Due to send</h3>
              <ul className="flex flex-col gap-1">
                {[...data.drain, ...data.scheduled].map((entry) => (
                  <li key={entry.id} className="flex flex-wrap items-baseline gap-2 text-sm">
                    <span className="font-mono text-foreground">{entry.row_key}</span>
                    <span className="text-muted-foreground">{entry.code}</span>
                    <span className="font-mono text-xs text-muted-foreground/80">
                      attempt {entry.attempts} of {data.max_attempts}
                      {entry.scheduled_because ? ` · ${entry.scheduled_because}` : ''}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {data.waiting.length > 0 && (
            <div className="mt-4 flex flex-col gap-2">
              <h3 className="font-mono text-xs uppercase text-muted-foreground">
                Waiting for a person ({data.waiting.length})
              </h3>
              <ul className="flex flex-col gap-1">
                {data.waiting.map((entry) => (
                  <li key={entry.id} className="flex flex-wrap items-baseline gap-2 text-sm">
                    <span className="font-mono text-foreground">{entry.row_key}</span>
                    <span className="text-muted-foreground">{entry.code}</span>
                    {entry.field && (
                      <span className="font-mono text-xs text-muted-foreground/80">on {entry.field}</span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}

      <div className="mt-5 flex flex-wrap items-end gap-3">
        <div className="min-w-64 flex-1">
          <Field
            label="Retry the failed rows of one run"
            id="wf040-retry-run"
            hint="Successes in that run are not re-sent."
          >
            <select
              id="wf040-retry-run"
              className={`${inputClass} cursor-pointer`}
              value={retryRunId}
              onChange={(event) => onRetryRun(event.target.value)}
            >
              <option value="">Choose a run…</option>
              {runOptions.map((entry) => (
                <option key={entry.id} value={entry.id}>
                  {entry.connector} · {entry.label || entry.id} · {entry.failed} failed of {entry.rows}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <Button variant="primary" onClick={onRetry} disabled={!retryRunId || action !== null}>
          Retry failed rows only
        </Button>
        <Button onClick={onDrain} disabled={action !== null}>
          {action === 'drain' ? 'Draining…' : 'Drain the queue'}
        </Button>
      </div>

      {result && (
        <div className="mt-4 rounded-lg border border-border-subtle/40 bg-background/40 p-3">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <p className="text-sm text-foreground">
              {result.error ? (
                <span className="text-destructive">{result.error}</span>
              ) : (
                result.detail
              )}
            </p>
            <Button onClick={onDismissResult}>Dismiss</Button>
          </div>
          {result.body?.sent?.length > 0 && (
            <details className="mt-2">
              <summary className="min-h-11 cursor-pointer list-none text-sm text-muted-foreground hover:text-accent
                focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2
                focus-visible:ring-offset-background">
                The batch the connector should send ({result.body.sent.length})
              </summary>
              <div className="mt-2 rounded-lg bg-background/40 p-3">
                <JsonView value={result.body.sent} />
              </div>
            </details>
          )}
        </div>
      )}
    </Card>
  )
}

/**
 * What each vendor must be asked for, and what the research does not say.
 *
 * Rendered from `/connectors` rather than compiled here, so a connector added on the
 * server reaches this page with no change to the frontend. The `gap` is shown
 * deliberately: a connector relying on something the source set never confirmed
 * should be able to see that before it reaches production.
 */
function ConnectorPanel({ connectors }) {
  if (!connectors) return null

  return (
    <Card>
      <h2 className="font-mono text-sm font-semibold text-foreground">
        What each vendor must be asked for
      </h2>
      <p className="mt-1 text-sm text-muted-foreground">
        Per-record outcomes instead of one verdict for the whole batch. The gap on each one is the
        research&rsquo;s own statement about what it could not confirm.
      </p>
      <div className="mt-4 flex flex-col gap-4">
        {connectors.connectors.map((entry) => (
          <details key={entry.id} className="border-t border-border-subtle/20 pt-3">
            <summary className="min-h-11 cursor-pointer list-none font-mono text-sm text-foreground
              hover:text-accent focus-visible:ring-2 focus-visible:ring-accent
              focus-visible:ring-offset-2 focus-visible:ring-offset-background">
              {entry.label} — {entry.status.partial} means partial
            </summary>
            <div className="mt-2 grid gap-3 text-sm lg:grid-cols-2">
              <div>
                <h3 className="font-mono text-xs uppercase text-muted-foreground">To ask for it</h3>
                <ul className="mt-1 list-disc pl-4 text-muted-foreground">
                  {entry.per_record_request.request_notes.map((note) => (
                    <li key={note}>{note}</li>
                  ))}
                </ul>
                {entry.per_record_request.request_headers?.Prefer && (
                  <p className="mt-2 font-mono text-xs break-all text-foreground">
                    Prefer: {entry.per_record_request.request_headers.Prefer}
                  </p>
                )}
              </div>
              <div>
                <h3 className="font-mono text-xs uppercase text-muted-foreground">
                  How a result is keyed back to its row
                </h3>
                <p className="mt-1 text-foreground">{entry.correlation.key}</p>
                <p className="text-muted-foreground">{entry.correlation.note}</p>
                <h3 className="mt-3 font-mono text-xs uppercase text-muted-foreground">Doc links</h3>
                <p className="mt-1 text-muted-foreground">{entry.doc_link.note}</p>
              </div>
            </div>
            <p className="mt-3 border-l-2 border-border-subtle/40 pl-3 text-sm text-muted-foreground">
              {entry.status.note}
            </p>
            <p className="mt-2 text-xs text-muted-foreground/80">{entry.per_record_request.gap}</p>
          </details>
        ))}
      </div>
    </Card>
  )
}

/**
 * What the research left open, published as data.
 *
 * A native `<details>`, so it works without JavaScript and is announced correctly by
 * a screen reader. Collapsed by default: it is the reference, not the reading a rep
 * opens the page for.
 */
function InferencesPanel({ inferences }) {
  const { data, loading, error, refetch } = inferences
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
          &ldquo;{data.sourced_automation}&rdquo;
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

export default SyncLogPage
