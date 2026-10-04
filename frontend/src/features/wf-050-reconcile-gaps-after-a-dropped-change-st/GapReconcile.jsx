import { useCallback, useState } from 'react'

import {
  Badge,
  Card,
  EmptyState,
  ErrorNote,
  Field,
  Spinner,
  StatCard,
  useAsync,
} from '@/components/ui'

import {
  DEFAULT_VENDOR,
  UNSUPPORTED_VENDORS,
  canReconcile,
  canSubscribe,
  cursorNote,
  defaultOf,
  dropNote,
  gapApi,
  gapTypeNote,
  logLineNote,
  runStateNote,
  stateTone,
  subscriptionNote,
  vendorOf,
} from './api'
import { DataTable, Fact, Notice, PathButton, Section, StateLine } from './primitives'

/**
 * The gap and overflow ledger, the data-health view, and the sync log (WF-050).
 *
 * Four things this page can get quietly wrong, and each is answered in words rather
 * than in a colour:
 *
 * 1. A gap event and an overflow event are different shapes. A gap names one record
 *    and is repaired by one read. An overflow names no record and is repaired by a
 *    whole-entity read plus a delete diff. The page says which it is showing.
 * 2. A dirty record is not a broken room. It is a record that owes a full re-read,
 *    and the page says how long it has been waiting.
 * 3. A dropped change event is the research's prescribed behaviour, not a failure.
 *    The page says which of the four reasons dropped it.
 * 4. The two cursors are not the same shape. A Replay ID is per entity type and does
 *    not expire; a delta link is per org and stops being resumable after seven days.
 *
 * Every route is this feature's own, called through `apiRequest` in `./api.js`.
 * Nothing here reaches another feature's page or another's state.
 */

const ROOM_KEY = 'wf-050-reconcile-gaps-after-a-dropped-change-st:last-room'

function readStoredRoom() {
  try {
    return window.localStorage.getItem(ROOM_KEY) || ''
  } catch {
    return ''
  }
}

function storeRoom(roomId) {
  try {
    window.localStorage.setItem(ROOM_KEY, roomId)
  } catch {
    // A room that cannot be remembered is still a room that can be worked on.
  }
}

function GapReconcile() {
  // One state for the room, not two. A picker that defaults to the stored room and a
  // separate "chosen" state has to be synchronised, and a synchronised pair is a
  // cascading render waiting to happen.
  const [roomId, setRoomId] = useState(readStoredRoom)
  const [outcome, setOutcome] = useState(null)
  const [notice, setNotice] = useState(null)
  const [busy, setBusy] = useState(false)

  const vocabulary = useAsync(() => gapApi.vocabulary(), [])
  const rooms = useAsync(() => gapApi.rooms(), [])
  const health = useAsync(() => (roomId ? gapApi.health(roomId) : Promise.resolve(null)), [roomId])
  const dirty = useAsync(() => (roomId ? gapApi.dirty(roomId) : Promise.resolve(null)), [roomId])
  const ledger = useAsync(
    () => (roomId ? gapApi.gapEvents(roomId) : Promise.resolve(null)),
    [roomId]
  )
  const cursors = useAsync(() => (roomId ? gapApi.cursors(roomId) : Promise.resolve(null)), [roomId])
  const runs = useAsync(() => (roomId ? gapApi.runs(roomId) : Promise.resolve(null)), [roomId])
  const [openLog, setOpenLog] = useState(null)

  const log = useAsync(
    () =>
      roomId && openLog ? gapApi.runLog(roomId, openLog).then((body) => body.lines) : Promise.resolve(null),
    [roomId, openLog]
  )

  const run = useCallback(async (work) => {
    setBusy(true)
    setNotice(null)
    try {
      const answer = await work()
      setOutcome(answer)
      await Promise.all([health.refetch(), dirty.refetch(), ledger.refetch(), cursors.refetch(), runs.refetch()])
    } catch (error) {
      setNotice({ tone: 'danger', title: 'The repair did not run', body: error.message })
    } finally {
      setBusy(false)
    }
  }, [health, dirty, ledger, cursors, runs])

  if (vocabulary.loading || rooms.loading) return <Spinner label="Loading gap ledger" />
  if (vocabulary.error) return <ErrorNote error={vocabulary.error} onRetry={vocabulary.refetch} />
  if (rooms.error) return <ErrorNote error={rooms.error} onRetry={rooms.refetch} />

  const published = vocabulary.data
  const roomOptions = rooms.data || []
  const threshold = defaultOf(published, 'overflow_change_threshold')
  const expiryDays = defaultOf(published, 'change_tracking_expiry_days')
  const events = ledger.data?.events || []
  const gapRows = events.filter((row) => row.kind === 'gap')
  const overflowRows = events.filter((row) => row.kind === 'overflow')
  const unsubscribed = canSubscribe(events)
  const healthBody = health.data
  const dirtyRows = dirty.data?.records || []
  const runRows = runs.data?.runs || []

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h1 className="font-display text-2xl font-semibold text-foreground">
          Reconcile gaps after a dropped change stream
        </h1>
        <p className="max-w-3xl text-sm text-muted-foreground">
          A change stream can lose a change. The room marks the affected record dirty, stops applying
          incremental changes for it, and makes a full read to repair it. This page is the ledger of
          what the room owes that read.
        </p>
      </header>

      <Card className="flex flex-col gap-4">
        <Section
          title="Room"
          hint="The gap ledger is scoped to one room, because a room holds several buyers and a CRM record id is unique per org rather than per room."
        >
          <Field
            label="Buyer room"
            id="wf-050-room"
            hint={`${roomOptions.length} room(s) on this server. Nothing is read until one is chosen.`}
          >
            <select
              id="wf-050-room"
              className="min-h-11 w-full rounded-sm border border-border-subtle bg-surface px-3 text-sm text-foreground focus:border-accent"
              value={roomId}
              onChange={(event) => {
                storeRoom(event.target.value)
                setRoomId(event.target.value)
                setOpenLog(null)
                setOutcome(null)
              }}
            >
              <option value="">Choose a room</option>
              {roomOptions.map((room) => (
                <option key={room.id} value={room.id}>
                  {room.name}
                </option>
              ))}
            </select>
          </Field>
        </Section>
      </Card>

      {!roomId && (
        <EmptyState
          title="Choose a room to read its gap ledger"
          description="Nothing is requested from the server until a room is named, so opening this page costs no vendor calls."
        />
      )}

      {roomId && health.error && (
        <ErrorNote error={health.error} onRetry={health.refetch} />
      )}

      {roomId && healthBody && (
        <>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard
              label="Dirty records"
              value={healthBody.dirty_count}
              hint={healthBody.clean ? 'Every gap has been repaired.' : 'Each owes one full read.'}
              icon="audit"
            />
            <StatCard
              label="Gap events"
              value={healthBody.gap_event_count}
              hint="Gap and overflow, reported by the subscriber."
              icon="database"
            />
            <StatCard
              label="Runs"
              value={healthBody.run_count}
              hint={`${healthBody.open_run_count} still open.`}
              icon="schema"
            />
            <StatCard
              label="Overflows"
              value={overflowRows.length}
              hint={`Threshold ${threshold} changes in one transaction.`}
              icon="audit"
            />
          </div>

          {healthBody.dirty_count > 0 && (
            <Notice tone="warning" title="This room owes a full re-read" icon="dirty">
              {healthBody.summary} A dirty record is not a broken room. It is a record the room has
              stopped trusting until it reads it again.
            </Notice>
          )}

          {notice && (
            <Notice
              tone={notice.tone}
              title={notice.title}
              onDismiss={() => setNotice(null)}
              icon="dirty"
            >
              {notice.body}
            </Notice>
          )}

          <Section
            title="Data health"
            hint="The research names this surface directly: a data-health view showing dirty records."
            action={
              <PathButton
                glyph="dirty"
                variant="primary"
                disabled={!canReconcile(dirtyRows.length, healthBody.open_run_count) || busy}
                onClick={() =>
                  run(async () => {
                    const first = gapRows.find((row) => row.record_ids?.includes(dirtyRows[0].record_id))
                    if (!first) throw new Error('No open gap event names the first dirty record.')
                    return gapApi.reconcile(roomId, {
                      event_id: first.id,
                      record_id: dirtyRows[0].record_id,
                    })
                  })
                }
              >
                Reconcile the first dirty record
              </PathButton>
            }
          >
            {dirtyRows.length === 0 ? (
              <Card>
                <p className="py-4 text-sm text-muted-foreground">
                  No record is dirty. Every gap this room has seen has been repaired.
                </p>
              </Card>
            ) : (
              <Card>
                <DataTable
                  rows={dirtyRows}
                  rowKey={(row) => row.id}
                  empty="No record is dirty."
                  columns={[
                    {
                      key: 'record',
                      header: 'Record',
                      render: (row) => (
                        <span className="font-mono text-[13px] text-foreground">
                          {row.entity}/{row.record_id}
                        </span>
                      ),
                    },
                    {
                      key: 'state',
                      header: 'State',
                      render: (row) => (
                        <StateLine tone={stateTone(published, row.state)} label={row.state}>
                          {row.change_type} as of {row.gap_commit_timestamp}
                        </StateLine>
                      ),
                    },
                    {
                      key: 'waiting',
                      header: 'Waiting',
                      render: (row) => (
                        <span className="text-[13px] text-muted-foreground">{row.age_note}</span>
                      ),
                    },
                  ]}
                />
              </Card>
            )}
          </Section>

          <Section
            title="Gap and overflow ledger"
            hint="A gap names one record. An overflow carries no record id at all, so the two are repaired differently and are listed separately here."
          >
            <Card className="flex flex-col gap-4">
              <div className="flex flex-col gap-2">
                <h3 className="font-mono text-sm font-semibold text-foreground">
                  Gap events ({gapRows.length})
                </h3>
                <p className="text-xs text-muted-foreground">
                  Each names one record and is repaired by one full read of that record.
                </p>
                <DataTable
                  rows={gapRows}
                  rowKey={(row) => row.id}
                  empty="No gap event has been reported for this room."
                  columns={[
                    {
                      key: 'type',
                      header: 'Type',
                      render: (row) => (
                        <Badge tone="update">{row.change_type}</Badge>
                      ),
                    },
                    {
                      key: 'record',
                      header: 'Record',
                      render: (row) => (
                        <span className="font-mono text-[13px] text-foreground">
                          {row.entity}/{(row.record_ids || []).join(', ') || 'none'}
                        </span>
                      ),
                    },
                    {
                      key: 'note',
                      header: 'What the room owes',
                      render: (row) => (
                        <span className="text-[13px] text-muted-foreground">
                          {gapTypeNote(published, row.change_type)}
                        </span>
                      ),
                    },
                    {
                      key: 'at',
                      header: 'Committed',
                      render: (row) => (
                        <span className="font-mono text-[13px] text-muted-foreground">
                          {row.commit_timestamp}
                        </span>
                      ),
                    },
                  ]}
                />
              </div>

              <div className="flex flex-col gap-2">
                <h3 className="font-mono text-sm font-semibold text-foreground">
                  Overflow events ({overflowRows.length})
                </h3>
                <p className="text-xs text-muted-foreground">
                  {`One is emitted per entity type beyond ${threshold} changes in a single transaction. It names no record, so the room pauses the stream and re-reads the whole entity.`}
                </p>
                <DataTable
                  rows={overflowRows}
                  rowKey={(row) => row.id}
                  empty="No overflow event has been reported for this room."
                  columns={[
                    {
                      key: 'type',
                      header: 'Type',
                      render: (row) => <Badge tone="delete">{row.change_type}</Badge>,
                    },
                    {
                      key: 'count',
                      header: 'Changes in the transaction',
                      render: (row) => (
                        <span className="flex flex-wrap items-center gap-2">
                          <span className="font-mono text-[13px] text-foreground">
                            {row.change_count ?? 'not reported'}
                          </span>
                          {row.exceeds_overflow_threshold && (
                            <Badge tone="delete">past the threshold</Badge>
                          )}
                        </span>
                      ),
                    },
                    {
                      key: 'replay',
                      header: 'Replay id',
                      render: (row) => (
                        <span className="font-mono text-[13px] text-foreground">
                          {row.replay_id || 'none sent'}
                        </span>
                      ),
                    },
                    {
                      key: 'state',
                      header: 'Subscription',
                      render: (row) => (
                        <StateLine
                          tone={row.subscription_state === 'subscribed' ? 'insert' : 'delete'}
                          label={row.subscription_state}
                        >
                          {subscriptionNote(row)}
                        </StateLine>
                      ),
                    },
                  ]}
                />
                {unsubscribed && (
                  <div>
                    <PathButton
                      glyph="cursor"
                      disabled={busy}
                      onClick={() => run(() => gapApi.subscribe(roomId, {}))}
                    >
                      Resubscribe this room
                    </PathButton>
                  </div>
                )}
              </div>
            </Card>
          </Section>

          <Section
            title="Resumable positions"
            hint="Two named cursors, because the research says the Salesforce Replay ID and the Dataverse delta link are a different shape and were not reconciled in a single source."
          >
            <Card className="grid gap-4 sm:grid-cols-2">
              {(cursors.data?.cursors || []).length === 0 ? (
                <p className="text-sm text-muted-foreground sm:col-span-2">
                  This room holds no resumable position yet. One is stored when an overflow is reported.
                </p>
              ) : (
                (cursors.data?.cursors || []).map((row) => (
                  <div key={row.id} className="flex flex-col gap-2">
                    <StateLine
                      tone={row.resumable ? 'insert' : 'delete'}
                      label={row.resumable ? 'resumable' : row.state}
                    >
                      {cursorNote(row)}
                    </StateLine>
                    <dl className="grid grid-cols-2 gap-3">
                      <Fact term="Kind" mono>
                        {row.kind}
                      </Fact>
                      <Fact term="Vendor" mono>
                        {row.vendor}
                      </Fact>
                      <Fact term="Scope" mono>
                        {row.scope}
                      </Fact>
                      <Fact term="Position" mono>
                        {row.position || 'none'}
                      </Fact>
                    </dl>
                  </div>
                ))
              )}
              <p className="text-xs text-muted-foreground sm:col-span-2">
                {cursors.data?.note}
              </p>
            </Card>
          </Section>

          <Section
            title="Sync log"
            hint="Every state change writes one numbered line, so an auditor can follow a repair without reading a diff."
          >
            <Card className="flex flex-col gap-3">
              <DataTable
                rows={runRows}
                rowKey={(row) => row.id}
                empty="No reconciliation has run for this room."
                columns={[
                  {
                    key: 'state',
                    header: 'State',
                    render: (row) => (
                      <StateLine tone={stateTone(published, row.state)} label={row.state}>
                        {runStateNote(row)}
                      </StateLine>
                    ),
                  },
                  {
                    key: 'scope',
                    header: 'Scope',
                    render: (row) => (
                      <span className="font-mono text-[13px] text-foreground">
                        {row.kind} / {row.record_id || row.entity}
                      </span>
                    ),
                  },
                  {
                    key: 'opened',
                    header: 'Opened',
                    render: (row) => (
                      <span className="font-mono text-[13px] text-muted-foreground">
                        {row.opened_at}
                      </span>
                    ),
                  },
                  {
                    key: 'log',
                    header: 'Steps',
                    render: (row) => (
                      <PathButton
                        glyph="audit"
                        onClick={() => setOpenLog(openLog === row.id ? null : row.id)}
                      >
                        {openLog === row.id ? 'Hide the steps' : 'Read the steps'}
                      </PathButton>
                    ),
                  },
                ]}
              />

              {openLog && (
                <div className="flex flex-col gap-2">
                  <h3 className="font-mono text-sm font-semibold text-foreground">
                    Steps for run {openLog}
                  </h3>
                  {log.loading && <Spinner label="Reading the sync log" />}
                  {log.error && <ErrorNote error={log.error} onRetry={log.refetch} />}
                  {(log.data || []).map((line) => (
                    <p key={line.id} className="text-[13px] text-foreground">
                      <span className="font-mono text-muted-foreground">{logLineNote(line)}</span>
                    </p>
                  ))}
                </div>
              )}
            </Card>
          </Section>

          {outcome && (
            <Section
              title="Last action"
              hint="What the room did, in the words the rules use."
            >
              <Card className="flex flex-col gap-3">
                {outcome.applied !== undefined && (
                  <StateLine tone={outcome.applied ? 'insert' : 'delete'} label={outcome.applied ? 'applied' : 'dropped'}>
                    {dropNote(outcome)}
                  </StateLine>
                )}
                {outcome.replay_id_stored !== undefined && (
                  <StateLine tone="update" label="overflow">
                    {subscriptionNote(outcome.event)}
                  </StateLine>
                )}
                {outcome.run && (
                  <StateLine tone={stateTone(published, outcome.run.state)} label={outcome.run.state}>
                    {runStateNote(outcome.run)}
                  </StateLine>
                )}
                {outcome.subscription_state && (
                  <StateLine tone="insert" label="subscribed">
                    Resubscribed at {outcome.resubscribed_at}.
                  </StateLine>
                )}
                {outcome.replica && (
                  <dl className="grid gap-3 sm:grid-cols-3">
                    <Fact term="Record" mono>
                      {outcome.replica.entity}/{outcome.replica.record_id}
                    </Fact>
                    <Fact term="Tombstoned">{outcome.replica.deleted ? 'yes' : 'no'}</Fact>
                    <Fact term="Reconciled at" mono>
                      {outcome.replica.reconciled_at}
                    </Fact>
                  </dl>
                )}
                {outcome.written && (
                  <p className="text-[13px] text-muted-foreground">
                    {outcome.written.length} row(s) overwritten, {outcome.deleted?.length ?? 0}{' '}
                    tombstoned by {outcome.deleted_source}.
                  </p>
                )}
              </Card>
            </Section>
          )}

          <Section
            title="Report a gap"
            hint="The page posts the header a subscriber would receive, in the vendor's own field names."
          >
            <Card className="flex flex-col gap-4">
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="Change type" id="wf-050-change-type" hint="One of the five values the vendor sends.">
                  <select
                    id="wf-050-change-type"
                    className="min-h-11 w-full rounded-sm border border-border-subtle bg-surface px-3 text-sm text-foreground focus:border-accent"
                    defaultValue="GAP_UPDATE"
                  >
                    {(published.gap_change_types || []).map((value) => (
                      <option key={value} value={value}>
                        {value}
                      </option>
                    ))}
                    <option value={published.overflow_change_type}>{published.overflow_change_type}</option>
                  </select>
                </Field>
                <Field label="Vendor" id="wf-050-vendor" hint={published.unsupported_vendor_quote}>
                  <select
                    id="wf-050-vendor"
                    className="min-h-11 w-full rounded-sm border border-border-subtle bg-surface px-3 text-sm text-foreground focus:border-accent"
                    defaultValue={DEFAULT_VENDOR}
                  >
                    {(published.vendors || []).map((value) => (
                      <option key={value} value={value}>
                        {value}
                      </option>
                    ))}
                    {UNSUPPORTED_VENDORS.map((value) => (
                      <option key={value} value={value}>
                        {value} (no gap path)
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Entity type" id="wf-050-entity" hint="An overflow emits one event per entity type.">
                  <input id="wf-050-entity" className="min-h-11 w-full rounded-sm border border-border-subtle bg-surface px-3 text-sm text-foreground placeholder:text-muted-foreground/70 focus:border-accent" defaultValue="Opportunity" />
                </Field>
                <Field label="Record id" id="wf-050-record" hint="A gap names one record. An overflow names none.">
                  <input id="wf-050-record" className="min-h-11 w-full rounded-sm border border-border-subtle bg-surface px-3 font-mono text-sm text-foreground placeholder:text-muted-foreground/70 focus:border-accent" defaultValue="006A000001" />
                </Field>
              </div>
              <div className="flex flex-wrap gap-3">
                <ReportButton
                  roomId={roomId}
                  vendor={DEFAULT_VENDOR}
                  threshold={threshold}
                  busy={busy}
                  run={run}
                />
              </div>
              <p className="text-xs text-muted-foreground">
                {`A delta link stops being resumable after ${expiryDays} days. Past that the room falls back to a full re-read rather than resuming from a token the vendor will throw on. The stored cursor for this room is currently ${vendorOf({ vendor: cursors.data?.cursors?.[0]?.vendor }) || DEFAULT_VENDOR}.`}
              </p>
            </Card>
          </Section>

          <Section title="Vocabulary" hint="Every value this page renders comes from the server.">
            <Card>
              <dl className="grid gap-3 sm:grid-cols-3">
                <Fact term="Gap types" mono>
                  {(published.gap_change_types || []).join(', ')}
                </Fact>
                <Fact term="Overflow marker" mono>
                  {published.overflow_change_type}
                </Fact>
                <Fact term="Overflow threshold" mono>{String(threshold)}</Fact>
                <Fact term="Delta link window" mono>{`${expiryDays} days`}</Fact>
                <Fact term="Cursor kinds" mono>
                  {(published.cursor_kinds || []).map((row) => row.kind).join(', ')}
                </Fact>
                <Fact term="Deleted sources" mono>
                  {(published.deleted_sources || []).map((row) => row.value).join(', ')}
                </Fact>
                <Fact term="Dirty key" mono>
                  {(published.dirty_key || []).join(', ')}
                </Fact>
                <Fact term="Comparison field" mono>
                  {published.last_modified_field}
                </Fact>
                <Fact term="Unsupported vendors" mono>
                  {(published.unsupported_vendors || []).join(', ')}
                </Fact>
              </dl>
              <p className="mt-3 text-xs text-muted-foreground">{published.classification}</p>
            </Card>
          </Section>
        </>
      )}
    </div>
  )
}

/**
 * The button that reports whatever the picker above names.
 *
 * The two event shapes need different payloads, and the difference is the whole
 * point of the page: a gap names one record, an overflow names none and carries a
 * change count and a Replay ID instead.
 */
function ReportButton({ roomId, vendor, threshold, busy, run }) {
  const read = (id) => {
    const element = document.getElementById(id)
    return element ? element.value : ''
  }
  const report = () =>
    run(async () => {
      const changeType = read('wf-050-change-type')
      const payload = {
        changeType,
        vendor: read('wf-050-vendor') || vendor,
        entity: read('wf-050-entity'),
        transactionKey: `page-${Date.now()}`,
        commitTimestamp: new Date().toISOString(),
      }
      if (changeType === 'GAP_OVERFLOW') {
        payload.changeCount = (threshold || 0) + 1
        payload.replayId = `page-replay-${Date.now()}`
      } else {
        payload.recordIds = [read('wf-050-record')]
      }
      return gapApi.reportGapEvent(roomId, payload)
    })

  return (
    <PathButton glyph="gap" variant="primary" disabled={!roomId || busy} onClick={report}>
      Report this event
    </PathButton>
  )
}

export default GapReconcile