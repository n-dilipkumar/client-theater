import { useState } from 'react'

import { Button, Card, EmptyState, ErrorNote, Field, Spinner, StatCard, inputClass, useAsync } from '@/components/ui'

import { actionLabel, alertLabel, escalationApi, formatSeconds, levelLabel, triggerLabel } from './api'
import { AlertResult, Fact, LevelBadge, Quote, Subhead } from './primitives'

/**
 * The SLA escalation page (WF-109).
 *
 * One room is chosen, and the page shows the room's live window: the escalation workflows,
 * the inbox ordered with the escalated conversations first, the runs a sweep produced, and
 * the Data Connector alerts it attempted. A status is always a word as well as a tone: the
 * level chip and the alert chip both carry their text.
 *
 * The sweep is a button, because this product runs no worker. Pressing it runs the same
 * route the scheduler would call.
 */
export default function SlaEscalation() {
  const rooms = useAsync(() => escalationApi.rooms(), [])
  const [roomId, setRoomId] = useState(null)

  const roomList = rooms.data?.records || rooms.data?.items || []
  const activeRoom = roomId || roomList[0]?.id || null

  return (
    <div className="space-y-6">
      <header className="space-y-2">
        <h1 className="font-display text-2xl font-semibold text-foreground">SLA escalation</h1>
        <p className="max-w-3xl text-sm text-muted-foreground">
          Watch the live SLA countdown on a conversation. Fire a workflow a chosen lead time
          before the target is due, or at the moment of breach, so the inbox escalates in
          priority.
        </p>
      </header>

      <EvidencePanel />

      {rooms.loading && <Spinner label="Loading rooms" />}
      {rooms.error && <ErrorNote error={rooms.error} onRetry={rooms.refetch} />}
      {!rooms.loading && !rooms.error && roomList.length === 0 && (
        <EmptyState
          title="No rooms yet"
          description="Seed the demo data, or create a room, to run an SLA escalation."
        />
      )}

      {roomList.length > 0 && (
        <Card>
          <Field label="Room" id="wf109-room">
            <select
              id="wf109-room"
              className={inputClass}
              value={activeRoom || ''}
              onChange={(event) => setRoomId(event.target.value)}
            >
              {roomList.map((room) => (
                <option key={room.id} value={room.id}>
                  {room.name || room.id}
                </option>
              ))}
            </select>
          </Field>
        </Card>
      )}

      {activeRoom && <RoomPanel roomId={activeRoom} />}
    </div>
  )
}

/** The researched sentences the workflow rests on, served by the API as data. */
function EvidencePanel() {
  const register = useAsync(() => escalationApi.inferences(), [])
  const vocabulary = useAsync(() => escalationApi.vocabulary(), [])
  if (register.loading || vocabulary.loading) return <Spinner label="Loading the evidence" />
  if (register.error) return <ErrorNote error={register.error} onRetry={register.refetch} />
  if (vocabulary.error) return <ErrorNote error={vocabulary.error} onRetry={vocabulary.refetch} />
  const evidence = vocabulary.data?.evidence || {}
  return (
    <Card>
      <Subhead count={register.data?.count}>What the research says</Subhead>
      <div className="grid gap-3 sm:grid-cols-2">
        <Quote source="Before SLA breach">{evidence.before_breach}</Quote>
        <Quote source="When SLA breaches">{evidence.on_breach}</Quote>
        <Quote source="Lead time">{evidence.lead_time}</Quote>
        <Quote source="The check is approximate">{evidence.one_minute_check}</Quote>
        <Quote source="Gated">{evidence.gated}</Quote>
        <Quote source="Currently in beta">{evidence.expert_plan_beta}</Quote>
      </div>
      {register.data?.jev_status && (
        <p className="mt-3 text-xs text-muted-foreground">{register.data.jev_status}</p>
      )}
    </Card>
  )
}

function RoomPanel({ roomId }) {
  const [nonce, setNonce] = useState(0)
  const [running, setRunning] = useState(false)
  const [runError, setRunError] = useState(null)

  const summary = useAsync(() => escalationApi.summary(roomId), [roomId, nonce])
  const workflows = useAsync(() => escalationApi.workflows(roomId), [roomId, nonce])
  const inbox = useAsync(() => escalationApi.inbox(roomId), [roomId, nonce])
  const runs = useAsync(() => escalationApi.runs(roomId), [roomId, nonce])
  const alerts = useAsync(() => escalationApi.alerts(roomId), [roomId, nonce])

  async function runCheck() {
    setRunning(true)
    setRunError(null)
    try {
      await escalationApi.evaluate(roomId, {})
      setNonce((value) => value + 1)
    } catch (error) {
      setRunError(error)
    } finally {
      setRunning(false)
    }
  }

  if (summary.loading) return <Spinner label="Loading the escalation window" />
  if (summary.error) return <ErrorNote error={summary.error} onRetry={summary.refetch} />

  const counts = summary.data || {}
  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Live workflows"
          value={`${counts.live_workflows ?? 0}/${counts.workflows ?? 0}`}
          hint="live of configured"
        />
        <StatCard label="Escalation runs" value={counts.runs ?? 0} hint="since the first sweep" />
        <StatCard
          label="Escalated"
          value={counts.escalated_conversations ?? 0}
          hint={`${counts.by_level?.breach ?? 0} breached, ${counts.by_level?.pre_warning ?? 0} warned`}
        />
        <StatCard
          label="Failed alerts"
          value={counts.failed_alerts ?? 0}
          hint={`of ${counts.alert_executions ?? 0} attempts`}
        />
      </div>

      <Card>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <Subhead count={counts.workflows}>Trigger category</Subhead>
            <p className="text-sm text-foreground">{counts.trigger_category_label}</p>
          </div>
          <Button variant="primary" icon="refresh" onClick={runCheck} disabled={running}>
            {running ? 'Running the SLA check' : 'Run the SLA check'}
          </Button>
        </div>
        {runError && <div className="mt-3"><ErrorNote error={runError} /></div>}
      </Card>

      <WorkflowList workflows={workflows} />
      <EscalatedInbox inbox={inbox} />
      <RunList runs={runs} />
      <AlertList alerts={alerts} />
    </div>
  )
}

function WorkflowList({ workflows }) {
  if (workflows.loading) return <Spinner label="Loading workflows" />
  if (workflows.error) return <ErrorNote error={workflows.error} onRetry={workflows.refetch} />
  const rows = workflows.data?.workflows || []
  if (rows.length === 0) {
    return (
      <EmptyState
        title="No escalation workflows yet"
        description="Create one with a Before SLA breach or a When SLA breaches trigger."
      />
    )
  }
  return (
    <Card>
      <Subhead count={rows.length}>Escalation workflows</Subhead>
      <ul className="divide-y divide-border-subtle">
        {rows.map((workflow) => (
          <li key={workflow.id} className="flex min-h-11 flex-wrap items-center gap-3 py-2">
            <LevelBadge level={workflow.level}>{levelLabel(workflow.level)}</LevelBadge>
            <span className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">
              {workflow.name}
            </span>
            <span className="text-xs text-muted-foreground">{triggerLabel(workflow.trigger)}</span>
            <span className="font-mono text-xs text-muted-foreground">
              {workflow.metric_label || workflow.metric}
            </span>
            <span className="font-mono text-xs text-muted-foreground">
              {workflow.lead_seconds ? `lead ${formatSeconds(workflow.lead_seconds)}` : 'no lead'}
            </span>
            <span className="font-mono text-xs text-muted-foreground">
              {workflow.live ? 'live' : 'draft'}
            </span>
          </li>
        ))}
      </ul>
    </Card>
  )
}

function EscalatedInbox({ inbox }) {
  if (inbox.loading) return <Spinner label="Loading the inbox" />
  if (inbox.error) return <ErrorNote error={inbox.error} onRetry={inbox.refetch} />
  const rows = inbox.data?.conversations || []
  if (rows.length === 0) {
    return <EmptyState title="The inbox is empty" description="Open a conversation with an SLA." />
  }
  return (
    <Card>
      <Subhead count={rows.length}>Inbox, escalated first</Subhead>
      <ul className="divide-y divide-border-subtle">
        {rows.map((row) => (
          <li key={row.conversation_id} className="flex min-h-11 flex-wrap items-center gap-3 py-2">
            <LevelBadge level={row.escalation_level}>
              {levelLabel(row.escalation_level)}
            </LevelBadge>
            <span className="min-w-0 flex-1 truncate text-sm text-foreground">
              {row.subject || row.conversation_id}
            </span>
            <span className="text-xs text-muted-foreground">{row.state}</span>
            {row.nearest && (
              <span className="font-mono text-xs text-muted-foreground">
                {row.nearest.short} {row.nearest.breached ? 'overdue ' : 'due '}
                {formatSeconds(row.nearest.seconds)}
              </span>
            )}
          </li>
        ))}
      </ul>
    </Card>
  )
}

function RunList({ runs }) {
  if (runs.loading) return <Spinner label="Loading runs" />
  if (runs.error) return <ErrorNote error={runs.error} onRetry={runs.refetch} />
  const rows = runs.data?.runs || []
  if (rows.length === 0) {
    return (
      <EmptyState
        title="No runs yet"
        description="Run the SLA check once a target is inside its lead window or breached."
      />
    )
  }
  return (
    <Card>
      <Subhead count={rows.length}>Escalation runs</Subhead>
      <ul className="space-y-3">
        {rows.slice(-8).reverse().map((run) => (
          <li key={run.id} className="rounded-sm border border-border-subtle p-3">
            <div className="flex flex-wrap items-center gap-3">
              <LevelBadge level={run.level}>{levelLabel(run.level)}</LevelBadge>
              <span className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">
                {run.workflow_name}
              </span>
              <span className="font-mono text-xs text-muted-foreground">{run.metric_label}</span>
            </div>
            <div className="mt-2 grid gap-1 sm:grid-cols-2">
              <Fact label="fire at">{run.fire_at || 'unknown'}</Fact>
              <Fact label="alerts">
                {run.alert_count} ({run.alert_failures} failed)
              </Fact>
            </div>
            {(run.actions || []).length > 0 && (
              <ul className="mt-2 space-y-0.5">
                {run.actions.map((action, index) => (
                  <li key={index} className="text-xs text-muted-foreground">
                    {actionLabel(action)}
                  </li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ul>
    </Card>
  )
}

function AlertList({ alerts }) {
  if (alerts.loading) return <Spinner label="Loading alerts" />
  if (alerts.error) return <ErrorNote error={alerts.error} onRetry={alerts.refetch} />
  const rows = alerts.data?.alerts || []
  if (rows.length === 0) {
    return (
      <EmptyState
        title="No alerts attempted"
        description="Add a Data Connector alert action to a workflow to send one."
      />
    )
  }
  return (
    <Card>
      <Subhead count={rows.length}>
        Data Connector alerts ({alerts.data?.failures ?? 0} failed)
      </Subhead>
      <ul className="divide-y divide-border-subtle">
        {rows.map((alert) => (
          <li key={alert.id} className="flex min-h-11 flex-wrap items-center gap-3 py-2">
            <AlertResult alert={alert} />
            <span className="min-w-0 flex-1 truncate font-mono text-xs text-muted-foreground">
              {alert.connector_id || alert.url || 'unconfigured'}
            </span>
            <span className="font-mono text-xs text-muted-foreground">
              {alert.execution_time_ms} ms
            </span>
            <span className="text-xs text-muted-foreground">{alertLabel(alert)}</span>
          </li>
        ))}
      </ul>
      <p className="mt-3 text-xs text-muted-foreground">
        This product makes no outbound HTTP call. An alert is recorded as attempted, with the
        outcome determined from the action's own configuration.
      </p>
    </Card>
  )
}
