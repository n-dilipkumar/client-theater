/**
 * Batch-upsert engagement rows keyed on the external ID (WF-038).
 *
 * The researched flow, in the order the research states it:
 *
 *  1. The room's queue accumulates pending engagement rows.
 *  2. Admin-triggered **Sync now** opens **Sync -> Run upsert**.
 *  3. The connector chunks rows into vendor-sized batches and sends one upsert per
 *     chunk, keyed on the sync key.
 *  4. Each row either updates the existing CRM record or creates a new one.
 *  5. Per-row outcomes are written back; failures appear in the sync log with the
 *     row's error text.
 *
 * So the page is that flow: a queue to read, a connection to configure, **Sync
 * now**, a progress bar and a per-row error list, and a sync log to come back to.
 *
 * Two things this page deliberately does not hide:
 *
 *  - **Unconfirmed is its own state, not a success.** A vendor whose upsert
 *    returns no per-item result (Dataverse answers 204 NoContent) can never
 *    confirm a row, so those rows are counted and listed separately. Folding them
 *    into "synced" would be the most flattering possible lie.
 *  - **The outbound request is visible.** Each chunk expands to the exact request
 *    that went out, so a run can be checked against the vendor's own documentation
 *    rather than taken on trust.
 *
 * Every control meets the design system's floor: 44px touch targets, a visible
 * focus ring, a text label beside every icon, no emoji as an icon, and motion
 * disabled under `prefers-reduced-motion`.
 */

import { useCallback, useMemo, useState } from 'react'
import { relativeTime } from '@/lib/api'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorNote,
  Field,
  JsonView,
  Spinner,
  StatCard,
  inputClass,
  useAsync,
} from '@/components/ui'
import { batchUpsertApi as api } from './api'
import { UPSERT_ICON } from './icons'
import { ChunkRequest, OutcomeList, ProgressBar, outcomeMeta } from './primitives'

const SECTIONS = [
  { id: 'queue', label: 'Queue', glyph: 'rows' },
  { id: 'connection', label: 'Connection', glyph: 'settings' },
  { id: 'run', label: 'Run upsert', glyph: 'run' },
  { id: 'log', label: 'Sync log', glyph: 'log' },
  { id: 'inferences', label: 'What this infers', glyph: 'gap' },
]

function Glyph({ path, size = 18, className = '' }) {
  return (
    <svg aria-hidden="true" focusable="false" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className={className}>
      <path d={path} />
    </svg>
  )
}

function SectionHeading({ glyph, children, hint }) {
  return (
    <div className="mb-3 flex flex-wrap items-baseline gap-2">
      <h2 className="flex items-center gap-2 font-mono text-sm font-semibold text-foreground">
        <Glyph path={glyph} size={16} className="text-accent" />
        {children}
      </h2>
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  )
}

function BatchUpsert() {
  const [roomId, setRoomId] = useState('')
  const [connectionId, setConnectionId] = useState('')
  const [run, setRun] = useState(null)
  const [busy, setBusy] = useState('')
  const [notice, setNotice] = useState(null)
  const [error, setError] = useState(null)

  const vocabulary = useAsync(() => api.vocabulary(), [])
  const capabilities = useAsync(() => api.capabilities(), [])
  const inferences = useAsync(() => api.inferences(), [])

  // The room is chosen by hand rather than guessed: this workflow writes
  // `synced_at` and `crm_record_id` back onto a specific room's engagement rows,
  // and picking the wrong one would write to the wrong room.
  const connections = useAsync(
    () => (roomId ? api.connections(roomId) : Promise.resolve({ connections: [] })),
    [roomId],
  )
  const queue = useAsync(
    () => (roomId && connectionId ? api.queue(roomId, connectionId) : Promise.resolve(null)),
    [roomId, connectionId],
  )
  const preview = useAsync(
    () => (roomId && connectionId ? api.preview(roomId, connectionId) : Promise.resolve(null)),
    [roomId, connectionId],
  )
  const runs = useAsync(
    () => (roomId ? api.runs(roomId, connectionId || undefined) : Promise.resolve({ runs: [] })),
    [roomId, connectionId],
  )

  const active = connections.data?.connections?.find((entry) => entry.id === connectionId) || null
  const counts = queue.data?.counts

  const refreshAll = useCallback(() => {
    queue.refetch()
    preview.refetch()
    runs.refetch()
  }, [queue, preview, runs])

  const startRun = useCallback(async () => {
    setBusy('run')
    setError(null)
    setNotice(null)
    try {
      const body = await api.startRun(roomId, connectionId)
      setRun(body)
      const totals = body.totals || {}
      setNotice(
        `${body.progress?.rows_total ?? 0} rows: ${totals.created ?? 0} created, ` +
          `${totals.updated ?? 0} updated, ${totals.submitted ?? 0} submitted (unconfirmed), ` +
          `${totals.needs_attention ?? 0} needing attention.`,
      )
      refreshAll()
    } catch (failure) {
      setError(failure)
    } finally {
      setBusy('')
    }
  }, [roomId, connectionId, refreshAll])

  const runSchedule = useCallback(async () => {
    setBusy('schedule')
    setError(null)
    setNotice(null)
    try {
      const body = await api.schedule(roomId, connectionId)
      if (!body.ran) {
        setNotice(`Not due: ${(body.backlog?.reasons || []).join('; ')}`)
      } else {
        setRun(body)
        setNotice(`Ran as ${body.driver}. ${(body.backlog?.reasons || []).join('; ')}`)
        refreshAll()
      }
    } catch (failure) {
      setError(failure)
    } finally {
      setBusy('')
    }
  }, [roomId, connectionId, refreshAll])

  if (vocabulary.loading) return <Spinner label="Loading batch upsert" />
  if (vocabulary.error) return <ErrorNote error={vocabulary.error} onRetry={vocabulary.refetch} />

  const words = vocabulary.data || {}

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 font-mono text-lg font-semibold text-foreground">
            <Glyph path={UPSERT_ICON} size={20} className="text-accent" />
            Batch upsert engagement rows
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            Chunk a room&rsquo;s pending engagement rows into vendor-sized upsert batches keyed on the CRM&rsquo;s
            external or alternate ID. Every row comes back created, updated, failed, refused, or sent-but-unconfirmed.
          </p>
        </div>
      </header>

      <Card>
        <div className="grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
          <Field id="wf038-room" label="Room id" hint="The room whose engagement rows this sync writes back to.">
            <input
              id="wf038-room"
              className={inputClass}
              value={roomId}
              placeholder="room_..."
              onChange={(event) => {
                setRoomId(event.target.value.trim())
                setConnectionId('')
                setRun(null)
              }}
            />
          </Field>
          <Field id="wf038-connection" label="Connection" hint="Batch size, key field and allOrNone are per connection.">
            <select
              id="wf038-connection"
              className={inputClass}
              value={connectionId}
              disabled={!roomId}
              onChange={(event) => {
                setConnectionId(event.target.value)
                setRun(null)
              }}
            >
              <option value="">{roomId ? 'Choose a connection' : 'Pick a room first'}</option>
              {(connections.data?.connections || []).map((entry) => (
                <option key={entry.id} value={entry.id}>
                  {entry.vendor} &rarr; {entry.object} (key {entry.key_field}, {entry.effective_batch_size ?? '?'}/batch)
                </option>
              ))}
            </select>
          </Field>
          <div className="flex gap-2">
            <Button icon="refresh" onClick={refreshAll} disabled={!connectionId}>
              Refresh
            </Button>
          </div>
        </div>
      </Card>

      {error && <ErrorNote error={error} onRetry={clearError(setError)} />}
      {notice && (
        <p role="status" className="rounded-lg border border-accent/30 bg-accent/10 px-4 py-2 text-sm text-foreground">
          {notice}
        </p>
      )}

      {!roomId && (
        <EmptyState
          title="Pick a room to begin"
          description="This workflow reads a room's pending engagement rows and writes synced_at and crm_record_id back onto them, so it always acts on one room at a time."
        />
      )}

      {roomId && !connectionId && (
        <EmptyState
          title="Pick a connection"
          description={
            connections.data?.connections?.length
              ? 'Choose which connection to run. Each keeps its own queue on the same rows.'
              : 'This room has no upsert connection yet. Create one with a vendor, the CRM object, and the external-id field to key on.'
          }
        />
      )}

      {roomId && connectionId && (
        <>
          <section aria-labelledby="wf038-queue">
            <SectionHeading glyph={SECTIONS[0].glyph}>
              <span id="wf038-queue">Queue</span>
            </SectionHeading>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <StatCard label="Pending" value={counts?.pending ?? 0} icon="audit" hint="Rows this connection has not sent" />
              <StatCard label="Synced" value={counts?.synced ?? 0} icon="database" hint="The CRM confirmed a record" />
              <StatCard label="Unconfirmed" value={counts?.unconfirmed ?? 0} icon="search" hint="Sent; nothing can confirm them" />
              <StatCard label="Failed" value={counts?.failed ?? 0} icon="warning" hint="Refused by the CRM; queued again" />
            </div>

            {queue.data && (
              <Card className="mt-3">
                <p className="text-sm text-muted-foreground">
                  {queue.data.unsent ?? 0} unsent of {queue.data.total ?? 0} rows, against a researched queue target of{' '}
                  <span className="font-mono text-foreground">{queue.data.target}</span>.
                  {queue.data.at_target ? ' The queue is at its target.' : ''}
                </p>
                {active && (
                  <p className="mt-2 text-xs text-muted-foreground">
                    Mode <span className="font-mono text-foreground">{active.mode}</span> &middot; capability{' '}
                    <span className="font-mono text-foreground">{active.capability_source}</span> &middot; per-item results{' '}
                    <span className="font-mono text-foreground">{String(active.returns_per_item_results)}</span>
                  </p>
                )}
              </Card>
            )}
          </section>

          <section aria-labelledby="wf038-run">
            <SectionHeading glyph={SECTIONS[2].glyph}>
              <span id="wf038-run">Run upsert</span>
            </SectionHeading>
            <Card className="space-y-4">
              <div className="flex flex-wrap gap-2">
                <Button variant="primary" onClick={startRun} disabled={busy !== '' || (counts?.unsent ?? counts?.pending ?? 0) === 0}>
                  <Glyph path={UPSERT_ICON} size={18} />
                  Sync now
                </Button>
                <Button onClick={runSchedule} disabled={busy !== ''}>
                  Run if due
                </Button>
                <Button variant="ghost" onClick={preview.refetch} disabled={!connectionId}>
                  Show the requests
                </Button>
              </div>

              {active && !active.returns_per_item_results && (
                <p className="rounded-md border border-border-subtle/40 bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
                  This connection&rsquo;s vendor returns no per-item result, so rows sent to it are recorded as
                  <span className="font-mono text-foreground"> submitted</span>: they leave the queue and are never
                  counted as synced, because nothing confirms them either way.
                </p>
              )}

              {run && <ProgressBar progress={run.progress} />}

              {run && (
                <div>
                  <h3 className="mb-2 font-mono text-xs font-semibold tracking-wide text-muted-foreground uppercase">
                    Per-row outcomes
                  </h3>
                  <OutcomeList outcomes={run.outcomes || []} />
                </div>
              )}

              {preview.data && !run && (
                <div>
                  <h3 className="mb-1 font-mono text-xs font-semibold tracking-wide text-muted-foreground uppercase">
                    The requests this run would send
                  </h3>
                  <p className="mb-2 text-xs text-muted-foreground">
                    {preview.data.planned} rows in {preview.data.chunks} chunk
                    {preview.data.chunks === 1 ? '' : 's'} of at most {preview.data.batch_size}. Nothing has been sent.
                  </p>
                  {(preview.data.requests || []).map((request) => (
                    <ChunkRequest key={`${request.chunk_index}`} chunk={request} />
                  ))}
                  {preview.data.rejected?.length > 0 && (
                    <div className="mt-3">
                      <h4 className="mb-1 font-mono text-xs font-semibold text-muted-foreground">
                        Refused before sending
                      </h4>
                      <OutcomeList outcomes={preview.data.rejected} />
                    </div>
                  )}
                </div>
              )}
            </Card>
          </section>

          {run && (
            <section aria-labelledby="wf038-chunks">
              <SectionHeading glyph="rows" hint="The exact request each batch carried.">
                <span id="wf038-chunks">Batches sent</span>
              </SectionHeading>
              <Card>
                {(run.chunks || []).length === 0 ? (
                  <p className="text-sm text-muted-foreground">This run sent nothing.</p>
                ) : (
                  run.chunks.map((chunk) => <ChunkRequest key={chunk.index} chunk={chunk} />)
                )}
              </Card>
            </section>
          )}

          <section aria-labelledby="wf038-log">
            <SectionHeading glyph="rows">
              <span id="wf038-log">Sync log</span>
            </SectionHeading>
            {runs.loading ? (
              <Spinner label="Loading the sync log" />
            ) : runs.error ? (
              <ErrorNote error={runs.error} onRetry={runs.refetch} />
            ) : (runs.data?.runs || []).length === 0 ? (
              <EmptyState title="No runs yet" description="Press Sync now to make the first one." />
            ) : (
              <Card className="p-0">
                <ul className="divide-y divide-border-subtle/15">
                  {runs.data.runs.map((entry) => (
                    <RunRow key={entry.id} entry={entry} />
                  ))}
                </ul>
              </Card>
            )}
          </section>
        </>
      )}

      <section aria-labelledby="wf038-inferences">
        <SectionHeading glyph="audit" hint="Every judgement this build made, and what would change it.">
          <span id="wf038-inferences">What this infers</span>
        </SectionHeading>
        {inferences.loading ? (
          <Spinner label="Loading inferences" />
        ) : (
          <div className="space-y-2">
            {(inferences.data?.inferences || []).map((entry) => (
              <InferenceCard key={entry.id} entry={entry} />
            ))}
          </div>
        )}
      </section>

      <section aria-labelledby="wf038-capabilities">
        <SectionHeading glyph="schema" hint="The researched extensibility seam: max batch size, key types, upsert paths.">
          <span id="wf038-capabilities">Bulk capabilities</span>
        </SectionHeading>
        {capabilities.loading ? (
          <Spinner label="Loading capabilities" />
        ) : (
          <Card className="p-0">
            <div className="overflow-x-auto">
              <table className="w-full min-w-[640px] text-left text-sm">
                <thead className="text-xs tracking-wide text-muted-foreground uppercase">
                  <tr>
                    <th className="px-4 py-3 font-medium">Vendor</th>
                    <th className="px-4 py-3 font-medium">Max batch</th>
                    <th className="px-4 py-3 font-medium">Key types</th>
                    <th className="px-4 py-3 font-medium">Per-item results</th>
                    <th className="px-4 py-3 font-medium">Source</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border-subtle/15">
                  {(capabilities.data?.capabilities || []).map((entry) => (
                    <tr key={entry.vendor}>
                      <td className="px-4 py-3 font-mono text-foreground">{entry.vendor}</td>
                      <td className="px-4 py-3 font-mono text-foreground">{entry.max_batch_size}</td>
                      <td className="px-4 py-3 font-mono text-xs text-muted-foreground">
                        {(entry.supported_key_types || []).join(', ')}
                      </td>
                      <td className="px-4 py-3">
                        <Badge tone={entry.returns_per_item_results ? 'insert' : 'neutral'}>
                          {entry.returns_per_item_results ? 'yes' : 'no (204)'}
                        </Badge>
                      </td>
                      <td className="px-4 py-3 font-mono text-xs text-muted-foreground">{entry.origin}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        )}
      </section>

      <p className="text-xs text-muted-foreground">
        Per-row outcomes in this build: {(words.outcomes || []).join(', ')}.
      </p>
    </div>
  )
}

function clearError(setError) {
  return () => setError(null)
}

function RunRow({ entry }) {
  const totals = entry.totals || {}
  const chips = [
    ['created', totals.created],
    ['updated', totals.updated],
    ['submitted', totals.submitted],
    ['failed', totals.failed],
    ['rolled back', totals.rolled_back],
    ['refused', totals.rejected],
  ].filter(([, value]) => value)

  return (
    <li className="px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-[13px] text-foreground">{entry.vendor}</span>
        <Badge tone="neutral">{entry.mode}</Badge>
        <Badge tone="neutral">{entry.driver}</Badge>
        {chips.map(([label, value]) => {
          const meta = outcomeMeta(label === 'rolled back' ? 'rolled_back' : label === 'refused' ? 'rejected' : label)
          return (
            <Badge key={label} tone={meta.tone}>
              {value} {meta.label}
            </Badge>
          )
        })}
        <span className="ml-auto text-xs text-muted-foreground" title={entry.finished_at}>
          {relativeTime(entry.finished_at)}
        </span>
      </div>
    </li>
  )
}

function InferenceCard({ entry }) {
  return (
    <Card>
      <details>
        <summary className="flex min-h-11 cursor-pointer items-center gap-2 text-left">
          <span className="font-mono text-[13px] text-foreground">{entry.topic}</span>
          <span className="ml-auto font-mono text-[11px] text-muted-foreground">{entry.id}</span>
        </summary>
        <div className="space-y-2 pt-2 text-xs">
          <div>
            <p className="font-medium tracking-wide text-muted-foreground uppercase">What the research says</p>
            <p className="mt-0.5 text-foreground/90">{entry.basis}</p>
          </div>
          <div>
            <p className="font-medium tracking-wide text-muted-foreground uppercase">Why this reading</p>
            <p className="mt-0.5 text-foreground/90">{entry.why}</p>
          </div>
          <div>
            <p className="font-medium tracking-wide text-muted-foreground uppercase">What this build chose</p>
            <JsonView value={entry.value} />
          </div>
          <div>
            <p className="font-medium tracking-wide text-muted-foreground uppercase">How to change it</p>
            <p className="mt-0.5 font-mono text-foreground/90">{entry.change_it}</p>
          </div>
        </div>
      </details>
    </Card>
  )
}

export default BatchUpsert
