import { useCallback, useEffect, useState } from 'react'
import { Badge, Card, ErrorNote, Spinner, useAsync } from '@/components/ui'
import { throttleApi, budgetOf, decisionNote, isPreemptive } from './api'
import {
  BudgetBar,
  DataTable,
  Fact,
  Glyph,
  PathButton,
  Section,
  WaitLine,
  formatSeconds,
  isLive,
  kindLabel,
  stateTone,
} from './primitives'

/**
 * Quota and throttling (WF-046).
 *
 * The researched step 5 surface: "Admin sees remaining budget and the throttle
 * log in **Integrations -> <connection> -> Quota**, and can manually pause/resume
 * a connection."
 *
 * **Three rules this page holds to, and each one is a place a page like this
 * normally lies.**
 *
 * 1. *No status by colour alone.* Every state renders its own word beside the
 *    badge. A `Badge` tinted by tone is a colour a screen reader and a
 *    colour-blind reader both miss, so the word is never optional.
 * 2. *An unreported half is "not reported", not 0%.* HubSpot's own documentation
 *    says the daily rate-limit headers are absent on OAuth responses, so a
 *    connection that sent none has an unknown daily budget. The bar renders that
 *    as a flat grey track with the words beside it rather than as an empty bar
 *    that would tell an operator they have spent everything.
 * 3. *Every wait says where its number came from.* `Retry-After: 45` and "the
 *    room's ladder said 30" are different facts, and an operator deciding whether
 *    to wait has to be able to tell them apart at a glance.
 */
function ThrottleQuotaPage() {
  const [roomId, setRoomId] = useState('')
  const [notice, setNotice] = useState('')

  const rooms = useAsync(() => throttleApi.rooms(), [])
  useEffect(() => {
    if (!roomId && rooms.data?.records?.length) setRoomId(rooms.data.records[0].id)
  }, [rooms.data, roomId])

  const connections = useAsync(() => throttleApi.connections({ room_id: roomId }), [roomId])
  const summary = useAsync(() => throttleApi.summary(roomId), [roomId])
  const quota = useAsync(() => throttleApi.quota(roomId), [roomId])
  const batches = useAsync(
    () => (roomId ? throttleApi.batches(roomId, { limit: 50 }) : Promise.resolve({ batches: [] })),
    [roomId],
  )
  const vocabulary = useAsync(() => throttleApi.vocabulary(), [])

  const after = useCallback(
    (message) => {
      setNotice(message)
      connections.refetch()
      summary.refetch()
      quota.refetch()
    },
    [connections, summary, quota],
  )

  if (rooms.loading || vocabulary.loading) return <Spinner label="Loading quota and throttling" />
  if (rooms.error) return <ErrorNote error={rooms.error} onRetry={rooms.refetch} />

  const meterByConnection = new Map(
    (quota.data?.meters || []).map((meter) => [meter.connection_id, meter]),
  )

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="font-display text-xl font-semibold text-foreground">Quota and throttling</h1>
          <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
            What each connector has left, why each batch was held back, and the control that stops a
            connection sending. Every wait names the vendor sentence or the room&rsquo;s arithmetic it
            came from.
          </p>
        </div>
        <label className="flex min-h-11 items-center gap-2 text-sm">
          <span className="text-muted-foreground">Room</span>
          <select
            value={roomId}
            onChange={(event) => setRoomId(event.target.value)}
            className="min-h-11 rounded-sm border border-border-subtle bg-surface px-2 text-sm focus-visible:ring-2 focus-visible:ring-ring"
          >
            {(rooms.data?.records || []).map((room) => (
              <option key={room.id} value={room.id}>
                {room.data?.name || room.id}
              </option>
            ))}
          </select>
        </label>
      </header>

      {notice && (
        <p role="status" className="rounded-sm border border-accent/40 bg-accent/10 p-3 text-sm">
          {notice}
        </p>
      )}

      {summary.error && <ErrorNote error={summary.error} onRetry={summary.refetch} />}

      {summary.data && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {[
            { term: 'Batches', value: summary.data.batches },
            { term: 'Deferred', value: summary.data.deferred },
            { term: 'Waiting for a person', value: summary.data.needs_action },
            { term: 'Connections', value: summary.data.connections },
          ].map((card) => (
            <Card key={card.term} className="p-3">
              <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
                {card.term}
              </p>
              <p className="mt-1 font-mono text-lg text-foreground">{card.value}</p>
            </Card>
          ))}
        </div>
      )}

      <Section
        title="Connections and remaining budget"
        hint="A bar reads only what the vendor reported on its own headers. A half the vendor did not send reads as not reported, never as empty: HubSpot omits the daily headers on OAuth responses, so a connection that sent none has an unknown daily budget rather than none."
      >
        {connections.loading && <Spinner label="Loading connections" />}
        {connections.error && <ErrorNote error={connections.error} onRetry={connections.refetch} />}
        {connections.data && connections.data.count === 0 && (
          <p className="py-6 text-center text-sm text-muted-foreground">
            No connector has been declared for this room yet. A throttle needs a connection, because
            the token bucket is per connector.
          </p>
        )}
        <div className="grid gap-3 md:grid-cols-2">
          {(connections.data?.connections || []).map((connection) => (
            <ConnectionCard
              key={connection.id}
              connection={connection}
              meter={meterByConnection.get(connection.id)}
              onPause={async () => {
                await throttleApi.pause(connection.id, { reason: 'paused from the quota page' })
                after(`Paused ${connection.label}.`)
              }}
              onResume={async () => {
                await throttleApi.resume(connection.id)
                after(`Resumed ${connection.label}. Its remaining tokens were not refilled.`)
              }}
            />
          ))}
        </div>
      </Section>

      <Section
        title="Batches"
        hint="A held batch keeps the idempotency keys it was first given, so a retry cannot duplicate a CRM row. The keys are shown in mono because they are machine values."
      >
        {batches.loading && <Spinner label="Loading batches" />}
        {batches.error && <ErrorNote error={batches.error} onRetry={batches.refetch} />}
        <DataTable
          rows={batches.data?.batches || []}
          rowKey={(row) => row.id}
          empty="No batch has been submitted for this room yet. Submit one and the bucket decides whether it goes out now."
          columns={[
            {
              key: 'state',
              header: 'State',
              render: (batch) => (
                <span className="flex items-center gap-2">
                  <Badge tone={stateTone(vocabulary.data, batch.state)}>{batch.state}</Badge>
                  {isLive(batch) && <span className="text-xs text-muted-foreground">still moving</span>}
                </span>
              ),
            },
            { key: 'vendor', header: 'Vendor', render: (batch) => batch.vendor },
            {
              key: 'signal',
              header: 'Why',
              render: (batch) => (
                <span className="text-[13px] text-foreground">
                  {kindLabel(batch.signal?.kind)}
                  {batch.signal?.code ? ` (${batch.signal.code})` : ''}
                </span>
              ),
            },
            {
              key: 'wait',
              header: 'Wait',
              render: (batch) =>
                batch.state === 'complete' ? (
                  <span className="text-[13px] text-muted-foreground">none</span>
                ) : (
                  <WaitLine schedule={batch.schedule} />
                ),
            },
            {
              key: 'keys',
              header: 'Idempotency keys',
              render: (batch) => (
                <span className="font-mono text-xs text-muted-foreground">
                  {(batch.keys || []).length} key(s)
                  {batch.keys_reused ? ', reused' : ''}
                </span>
              ),
            },
            {
              key: 'retry',
              header: 'Retry',
              render: (batch) =>
                batch.state === 'complete' ? null : (
                  <PathButton
                    glyph="wait"
                    onClick={async () => {
                      const outcome = await throttleApi.retry(roomId, batch.id, { force: true })
                      after(
                        outcome.sent
                          ? 'Retry scheduled under the same idempotency keys.'
                          : 'Not due yet. The room will send it when its wait has elapsed.',
                      )
                      batches.refetch()
                    }}
                  >
                    Retry now
                  </PathButton>
                ),
            },
          ]}
        />
      </Section>

      <Section
        title="Throttle log"
        hint="Oldest first, because a log an operator reads to find out what happened is a story and a story told backwards is not one. Every entry says the vendor sentence or the room arithmetic it came from."
      >
        <ThrottleLog roomId={roomId} />
      </Section>
    </div>
  )
}

function ConnectionCard({ connection, meter, onPause, onResume }) {
  const budget = budgetOf(meter)
  const preemptive = isPreemptive(connection)
  const bucket = connection.bucket || {}

  return (
    <Card className="flex flex-col gap-3 p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="flex items-center gap-2 text-sm font-semibold text-foreground">
            <Glyph name="bucket" size={16} />
            <span className="truncate">{connection.label}</span>
          </p>
          <p className="mt-0.5 font-mono text-xs text-muted-foreground">
            {connection.vendor} &middot; {preemptive ? 'refuses before it sends' : 'waits on the vendor'}
          </p>
        </div>
        {/* The word is beside the badge, never replaced by its colour. */}
        <Badge tone={connection.paused ? 'delete' : 'neutral'}>
          {connection.paused ? 'paused' : 'sending'}
        </Badge>
      </div>

      <dl className="grid grid-cols-2 gap-3">
        <Fact term="Tokens left" mono>
          {preemptive ? `${bucket.tokens ?? '?'} of ${bucket.capacity ?? '?'}` : 'not countable'}
        </Fact>
        <Fact term="Sends per second" mono>
          {connection.effective.tokens_per_second || '0'}
        </Fact>
      </dl>

      <BudgetBar label="Daily allowance" {...(budget.daily || {})} />
      <BudgetBar
        label="Burst window"
        {...(budget.window || {})}
        note={
          budget.window?.total && budget.window?.window_seconds
            ? `Resets every ${budget.window.window_seconds}s`
            : undefined
        }
      />

      {connection.paused ? (
        <div className="rounded-sm border border-border-subtle p-3">
          <p className="text-[13px] text-foreground">Paused by hand.</p>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {connection.pause_reason || 'No reason was recorded.'} Resuming does not refill the
            bucket, because a pause is usually a response to a throttle.
          </p>
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">
          {decisionNote({ decision_reason: 'proceed' }) ||
            'Sending as the bucket allows. A batch it cannot afford is held back before anything goes out.'}
        </p>
      )}

      <PathButton glyph="pause" onClick={connection.paused ? onResume : onPause}>
        {connection.paused ? 'Resume sending' : 'Pause sending'}
      </PathButton>
    </Card>
  )
}

function ThrottleLog({ roomId }) {
  const log = useAsync(() => (roomId ? throttleApi.throttleLog(roomId) : Promise.resolve({ events: [] })), [roomId])
  if (log.loading) return <Spinner label="Loading the throttle log" />
  if (log.error) return <ErrorNote error={log.error} onRetry={log.refetch} />
  return (
    <DataTable
      rows={log.data?.events || []}
      rowKey={(row) => row.id}
      empty="Nothing has been throttled in this room yet. The log fills in as soon as a vendor refuses a call."
      columns={[
        { key: 'at', header: 'When', render: (row) => <span className="font-mono text-xs">{row.at}</span> },
        { key: 'event', header: 'Event', render: (row) => <span className="font-mono text-xs">{row.event}</span> },
        { key: 'detail', header: 'Why', render: (row) => <span className="text-[13px]">{row.detail}</span> },
        {
          key: 'wait',
          header: 'Waited',
          render: (row) => (
            <span className="font-mono text-xs">
              {row.wait_seconds === undefined || row.wait_seconds === null
                ? '-'
                : formatSeconds(row.wait_seconds)}
            </span>
          ),
        },
      ]}
    />
  )
}

export default ThrottleQuotaPage