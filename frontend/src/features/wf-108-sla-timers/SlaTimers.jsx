import { useMemo, useState } from 'react'

import { Badge, Button, Card, EmptyState, ErrorNote, Field, Spinner, StatCard, useAsync, inputClass } from '@/components/ui'

import { badgeLabel, ratePercent, slaApi, timerStateLabel } from './api'
import { Fact, Quote, SlaBadge, Subhead, formatSeconds } from './primitives'

/**
 * The SLA timers page (WF-108).
 *
 * One room at a time, in the order a support lead works: the counts, then the report,
 * then the inbox sorted by the next SLA target, then the policies, then the judgment
 * calls. The sweep is a button because there is no worker: the timer is a projection over
 * the conversation, and pressing the button is what writes its state back and logs the
 * transitions.
 */

function ratesFor(entry) {
  const outcomes = entry.outcomes || {}
  const decided = (outcomes.hit || 0) + (outcomes.miss || 0) + (outcomes.fixed || 0)
  return { decided, outcomes }
}

function RateRow({ entry }) {
  const { decided, outcomes } = ratesFor(entry)
  const rates = entry.rates || {}
  return (
    <li className="flex min-h-11 flex-wrap items-center gap-x-6 gap-y-1 border-b border-border-subtle py-2 last:border-b-0">
      <span className="min-w-0 flex-1 truncate text-sm text-foreground">{entry.sla}</span>
      <span className="flex items-center gap-3 text-xs">
        <span className="text-muted-foreground">
          hit <span className="font-mono text-foreground">{ratePercent(rates.hit, decided > 0)}</span>
        </span>
        <span className="text-muted-foreground">
          miss <span className="font-mono text-foreground">{ratePercent(rates.miss, decided > 0)}</span>
        </span>
        <span className="text-muted-foreground">
          fixed <span className="font-mono text-foreground">{ratePercent(rates.fixed, decided > 0)}</span>
        </span>
      </span>
      <Badge tone="neutral">{decided} decided</Badge>
    </li>
  )
}

function InboxRow({ row, onRemove }) {
  return (
    <li className="flex min-h-11 flex-wrap items-center gap-x-4 gap-y-1 border-b border-border-subtle py-2 last:border-b-0">
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm text-foreground">{row.subject || 'Untitled conversation'}</span>
        <span className="block font-mono text-xs text-muted-foreground">
          {row.sla || 'no SLA'} / {timerStateLabel(row.timer_state)}
        </span>
      </span>
      <span className="min-w-[11rem] text-right font-mono text-xs text-muted-foreground">
        {row.nearest ? `${row.nearest.short} ${formatSeconds(row.nearest.seconds)}` : 'no target'}
      </span>
      <SlaBadge badge={row.badge}>{row.badge ? badgeLabel(row.badge) : 'No timer'}</SlaBadge>
      <Button variant="ghost" onClick={() => onRemove(row.conversation_id)} disabled={!row.sla}>
        Remove
      </Button>
    </li>
  )
}

function PolicyCard({ policy }) {
  const targets = policy.target_labels || {}
  return (
    <Card>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="truncate text-base font-semibold text-foreground">{policy.name}</h3>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {policy.object_kind} / {policy.workflow_name || 'no workflow'}
          </p>
        </div>
        <Badge tone={policy.live ? 'update' : 'neutral'}>{policy.live ? 'Live' : 'Draft'}</Badge>
      </div>
      <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
        {Object.entries(targets).map(([metric, label]) => (
          <Fact key={metric} label={label}>
            {formatSeconds(policy.targets?.[metric])}
          </Fact>
        ))}
      </div>
      <p className="mt-3 text-xs text-muted-foreground">
        Pauses: {(policy.pause_reasons || []).map((reason) => policy.pause_labels?.[reason] || reason).join(', ') || 'none'}
      </p>
    </Card>
  )
}

export default function SlaTimers() {
  const rooms = useAsync(() => slaApi.rooms(), [])
  const [roomId, setRoomId] = useState('')
  const [sweepNote, setSweepNote] = useState(null)
  const [busy, setBusy] = useState(false)
  const [nonce, setNonce] = useState(0)

  const board = useAsync(async () => {
    if (!roomId) return null
    const [summary, policies, inbox, report, hours] = await Promise.all([
      slaApi.summary(roomId),
      slaApi.policies(roomId),
      slaApi.inbox(roomId, { sort: 'next_target' }),
      slaApi.report(roomId),
      slaApi.officeHours(roomId),
    ])
    return { summary, policies, inbox, report, hours }
  }, [roomId, nonce])

  const roomOptions = useMemo(() => {
    const records = rooms.data?.records || []
    return records.map((record) => ({ id: record.id, name: record.data?.name || record.id }))
  }, [rooms.data])

  async function runSweep() {
    if (!roomId) return
    setBusy(true)
    setSweepNote(null)
    try {
      const result = await slaApi.evaluate(roomId, {})
      setSweepNote(
        `Checked ${result.checked} timer(s); ${result.transitions.length} transition(s), ` +
          `${result.reopened.length} conversation(s) reopened.`,
      )
      setNonce((value) => value + 1)
    } catch (error) {
      setSweepNote(`The sweep failed: ${String(error?.message || error)}`)
    } finally {
      setBusy(false)
    }
  }

  async function removeSla(conversationId) {
    try {
      await slaApi.removeSla(roomId, conversationId)
      setNonce((value) => value + 1)
    } catch (error) {
      setSweepNote(`Could not remove the SLA: ${String(error?.message || error)}`)
    }
  }

  if (rooms.loading) return <Spinner label="Loading rooms" />
  if (rooms.error) return <ErrorNote error={rooms.error} onRetry={rooms.refetch} />

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-foreground">SLA response timers</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          First reply, next reply, time to close and time to resolution, counted in office hours and paused
          while a conversation waits on the customer.
        </p>
      </div>

      <Card>
        <div className="flex flex-wrap items-end gap-4">
          <div className="min-w-[16rem] flex-1">
            <Field label="Room" id="wf108-room" hint="One room at a time, so the counts describe one team.">
              <select
                id="wf108-room"
                className={inputClass}
                value={roomId}
                onChange={(event) => setRoomId(event.target.value)}
              >
                <option value="">Choose a room</option>
                {roomOptions.map((room) => (
                  <option key={room.id} value={room.id}>
                    {room.name}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          <Button variant="primary" icon="refresh" onClick={runSweep} disabled={!roomId || busy}>
            Run the sweep
          </Button>
        </div>
        {sweepNote && <p className="mt-3 text-xs text-muted-foreground">{sweepNote}</p>}
      </Card>

      {!roomId && <EmptyState title="Choose a room" description="The SLA timer is a projection over one room's conversations." />}

      {roomId && board.loading && <Spinner label="Loading the SLA board" />}
      {roomId && board.error && <ErrorNote error={board.error} onRetry={board.refetch} />}

      {roomId && board.data && (
        <>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard label="Active timers" value={board.data.summary.active_timers} icon="audit" />
            <StatCard label="Breached" value={board.data.summary.timers_by_state?.breached ?? 0} icon="audit" />
            <StatCard label="Paused" value={board.data.summary.timers_by_state?.paused ?? 0} icon="audit" />
            <StatCard
              label="Policies"
              value={`${board.data.summary.live_policies}/${board.data.summary.policies}`}
              hint="live / total"
              icon="schema"
            />
          </div>

          <Card>
            <Subhead count={board.data.report.count}>SLAs report</Subhead>
            {board.data.report.count === 0 ? (
              <EmptyState title="No SLA outcomes yet" description="Apply an SLA, then run the sweep." />
            ) : (
              <ul>
                {board.data.report.slas.map((entry) => (
                  <RateRow key={entry.sla} entry={entry} />
                ))}
              </ul>
            )}
          </Card>

          <Card>
            <Subhead count={board.data.inbox.count}>Inbox by next SLA target</Subhead>
            {board.data.inbox.count === 0 ? (
              <EmptyState title="No conversations in this room" />
            ) : (
              <ul>
                {board.data.inbox.conversations.map((row) => (
                  <InboxRow key={row.conversation_id} row={row} onRemove={removeSla} />
                ))}
              </ul>
            )}
          </Card>

          <div>
            <Subhead count={board.data.policies.count}>SLA policies</Subhead>
            {board.data.policies.count === 0 ? (
              <EmptyState title="No SLA policies" description="Create one through the API to see it here." />
            ) : (
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {board.data.policies.policies.map((policy) => (
                  <PolicyCard key={policy.id} policy={policy} />
                ))}
              </div>
            )}
          </div>

          <Card>
            <Subhead>Office hours</Subhead>
            <p className="text-sm text-foreground">
              {board.data.hours.derived ? 'Derived default' : 'Stored schedule'}, timezone{' '}
              <span className="font-mono">{board.data.hours.timezone}</span>
            </p>
            <ul className="mt-3 grid grid-cols-1 gap-1 sm:grid-cols-2 lg:grid-cols-4">
              {Object.entries(board.data.hours.schedule).map(([day, window]) => (
                <li key={day} className="flex items-center justify-between gap-2 text-xs">
                  <span className="text-muted-foreground">{day}</span>
                  <span className="font-mono text-foreground">
                    {window.open === null || window.close === null
                      ? 'closed'
                      : `${String(Math.floor(window.open / 60)).padStart(2, '0')}:${String(window.open % 60).padStart(
                          2,
                          '0',
                        )}-${String(Math.floor(window.close / 60)).padStart(2, '0')}:${String(
                          window.close % 60,
                        ).padStart(2, '0')}`}
                  </span>
                </li>
              ))}
            </ul>
            <div className="mt-3">
              <Quote source="The workflow specification">
                Red: Overdue. Orange: Less than 5 minutes left on the timer. Grey: More than 5 minutes left on
                the timer.
              </Quote>
            </div>
          </Card>
        </>
      )}
    </div>
  )
}
