import { useState } from 'react'

import { api } from '@/lib/api'
import { absoluteTime, relativeTime } from '@/lib/api'
import { Badge, Button, Card, EmptyState, ErrorNote, Field, Spinner, StatCard, useAsync } from '@/components/ui'

import Glyph, { PULSE_ICON, PATHS } from './icons'
import { monitorApi } from './api'

/**
 * Integration monitoring, for WF-049.
 *
 * The page is the researched user flow, in the order the research states it:
 * open the room's Integrations → Monitoring dashboard, read remaining daily +
 * burst quota, sync success rate, mean latency, the error-class breakdown and
 * the change-stream lag — then, from the same page, lower a starved
 * connector's concurrency or pause it. So the controls sit next to the numbers
 * they exist for, and nothing here opens a vendor page.
 *
 * Two things on this page are this build's judgement rather than the source's,
 * and both are shown rather than hidden: the 24-hour telemetry window, which
 * the response reports and the header labels, and the inferences list, which
 * is rendered from `/inferences` under a heading that says so.
 *
 * The quota pair is rendered as the vendor reported it: a percentage when the
 * vendor's maximum is known, a count when it is not, and "unknown" with the
 * parser's reason when the vendor said nothing — never a zero nobody
 * measured.
 */

const ERROR_TONES = {
  validation: 'bg-amber-500/20 text-amber-300 border-amber-500/40',
  throttle: 'bg-orange-500/20 text-orange-300 border-orange-500/40',
  auth: 'bg-red-500/20 text-red-300 border-red-500/40',
  vendor_5xx: 'bg-rose-500/20 text-rose-300 border-rose-500/40',
}

const METRIC_UNITS = {
  daily_remaining: 'percent or count',
  window_remaining: 'percent or count',
  stream_lag: 'seconds',
}

function IntegrationMonitorPage() {
  const [selected, setSelected] = useState(null)
  const [ruleDraft, setRuleDraft] = useState({ metric: 'daily_remaining', threshold: '20', channels: ['slack'] })

  const { data, loading, error, refetch } = useAsync(
    () => Promise.all([monitorApi.vocabulary(), api.listRecords('room', { limit: 100 })]),
    [],
  )

  const rooms = (data?.[1] || []).filter((room) => !room.deleted_at)
  const [roomId, selectRoom] = useRoomSelection(selected, rooms[0]?.id)

  const board = useAsync(
    () => (roomId ? monitorApi.dashboard(roomId) : Promise.resolve(null)),
    [roomId],
  )
  const tracking = useAsync(
    () => (roomId ? monitorApi.changeTracking(roomId) : Promise.resolve(null)),
    [roomId],
  )

  if (loading) return <Spinner label="Loading the integration monitor" />
  if (error) return <ErrorNote error={error} onRetry={refetch} />

  const [published] = data
  const dashboard = board.data

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="flex items-center gap-2 text-xl font-semibold text-foreground">
            <Glyph path={PULSE_ICON} size={22} className="text-accent" />
            Integration monitor
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            Remaining daily and burst quota, read from each vendor's own surfaces; sync success rate,
            mean latency, the error-class breakdown, and the live change-stream lag. Quota is handed in
            by the connector: this room opens no socket and holds no vendor credential.
          </p>
        </div>
        <Button icon="refresh" onClick={() => { refetch(); board.refetch(); tracking.refetch() }}>
          Refresh
        </Button>
      </header>

      <section aria-label="Choose a room" className="max-w-md">
        <Field label="Room" id="wf049-room" hint="The room whose Integrations page is on screen">
          <select
            id="wf049-room"
            value={roomId || ''}
            onChange={(event) => selectRoom(event.target.value)}
            className="min-h-11 w-full rounded-lg border border-border-subtle/40 bg-muted/30 px-3 text-sm
              text-foreground focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2
              focus-visible:ring-offset-background"
          >
            {rooms.map((room) => (
              <option key={room.id} value={room.id}>{room.data?.name || room.id}</option>
            ))}
          </select>
        </Field>
      </section>

      {board.loading && <Spinner label="Loading the dashboard" />}
      {board.error && <ErrorNote error={board.error} onRetry={board.refetch} />}

      {dashboard && (
        <>
          <section aria-label="The dashboard at a glance" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard label="Monitored connectors" value={dashboard.connectors.length}
              hint={Object.entries(dashboard.by_vendor).map(([v, n]) => `${v}: ${n}`).join(' · ') || 'none yet'} />
            <StatCard label="Paused" value={dashboard.paused} hint="out of alert evaluation" />
            <StatCard label="Alert rules" value={dashboard.alert_rules.length}
              hint={dashboard.alert_preview?.fired.length ? `${dashboard.alert_preview.fired.length} would fire now` : 'none would fire now'} />
            <StatCard label="Telemetry window" value={`${Math.round(dashboard.window_seconds / 3600)}h`}
              hint="success rate and latency window" />
          </section>

          <section aria-label="Monitored connectors" className="flex flex-col gap-4">
            <h2 className="font-mono text-sm font-semibold text-foreground">Monitoring</h2>
            {dashboard.connectors.length === 0 ? (
              <EmptyState
                title="Nothing monitored yet"
                description="Register a connector for this room, then hand in what its vendor answered."
              />
            ) : (
              dashboard.connectors.map((row) => (
                <ConnectorCard key={row.id} row={row} published={published} onRefresh={board.refetch} />
              ))
            )}
          </section>

          <AlertsCard
            dashboard={dashboard}
            published={published}
            draft={ruleDraft}
            onDraft={setRuleDraft}
            onRefresh={board.refetch}
          />

          {tracking.data && <TrackingCard state={tracking} published={published} />}
        </>
      )}

      <InferencesPanel />
    </div>
  )
}

/**
 * One connector's monitoring row: the quota pair, the health aggregate, and
 * the researched controls beside them.
 */
function ConnectorCard({ row, published, onRefresh }) {
  const [busy, setBusy] = useState(false)
  const quota = useAsync(() => monitorApi.quota(row.id), [row.id])
  const health = useAsync(() => monitorApi.telemetryView(row.id), [row.id])

  async function control(patch) {
    setBusy(true)
    try {
      await monitorApi.updateConnector(row.id, patch)
      onRefresh()
    } finally {
      setBusy(false)
    }
  }

  const labels = published?.error_class_labels || {}
  const reading = quota.data?.latest

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="font-medium text-foreground">{row.label}</h3>
          <p className="text-xs text-muted-foreground">
            {published?.vendor_labels?.[row.vendor] || row.vendor} · concurrency {row.concurrency} · policy{' '}
            {row.quota_policy?.kind || 'unknown'}
            {row.paused && ` · paused ${row.paused_at ? relativeTime(row.paused_at) : ''}`}
          </p>
        </div>
        <div className="flex gap-2">
          {row.paused ? (
            <Button icon="play" onClick={() => control({ paused: false })} disabled={busy}>Resume</Button>
          ) : (
            <Button onClick={() => control({ paused: true })} disabled={busy}>
              <Glyph path={PATHS.pause} size={14} className="mr-1" /> Pause
            </Button>
          )}
          <Button onClick={() => control({ concurrency: Math.max(1, row.concurrency - 1) })} disabled={busy}>
            −
          </Button>
          <Button onClick={() => control({ concurrency: Math.min(64, row.concurrency + 1) })} disabled={busy}>
            +
          </Button>
        </div>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <div>
          <QuotaHalf label="Remaining today" reading={reading?.daily} />
          <div className="mt-3">
            <QuotaHalf label="Remaining this window" reading={reading?.window} />
          </div>
          {quota.data?.delta?.daily_remaining != null && (
            <p className="mt-2 text-xs text-muted-foreground">
              Burned since the previous reading: {Math.abs(quota.data.delta.daily_remaining).toLocaleString()}
            </p>
          )}
          {quota.error && <ErrorNote error={quota.error} onRetry={quota.refetch} />}
        </div>

        <div>
          {health.loading && <Spinner label="Loading health" />}
          {health.error && <ErrorNote error={health.error} onRetry={health.refetch} />}
          {health.data && (health.data.known ? (
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
              <dt className="text-muted-foreground">Sync success rate</dt>
              <dd className="font-mono">{Math.round(health.data.success_rate * 100)}%</dd>
              <dt className="text-muted-foreground">Mean latency</dt>
              <dd className="font-mono">{Math.round(health.data.mean_latency_ms)} ms</dd>
              <dt className="text-muted-foreground">Calls in window</dt>
              <dd className="font-mono">{health.data.calls}</dd>
            </dl>
          ) : (
            <p className="text-sm text-muted-foreground">
              No calls in the window yet — no success rate is shown, rather than a perfect one
              nobody earned.
            </p>
          ))}
          <div className="mt-3 flex flex-wrap gap-2">
            {Object.entries(health.data?.error_breakdown || {}).map(([errorClass, count]) => (
              <Badge key={errorClass} tone={count ? 'delete' : 'neutral'}>
                <span className={count ? ERROR_TONES[errorClass] : ''}>
                  {labels[errorClass] || errorClass}: {count}
                </span>
              </Badge>
            ))}
          </div>
        </div>
      </div>

      {(reading?.notes?.length || 0) > 0 && (
        <ul className="mt-3 flex flex-col gap-1 text-xs text-muted-foreground/80">
          {reading.notes.map((note) => <li key={note}>{note}</li>)}
        </ul>
      )}
      {reading?.observed_at && (
        <p className="mt-2 text-xs text-muted-foreground">
          Quota read {relativeTime(reading.observed_at)} ({absoluteTime(reading.observed_at)}) via{' '}
          <span className="font-mono">{reading.surface}</span>.
        </p>
      )}
    </Card>
  )
}

/** One half of the normalised pair, as the vendor reported it. */
function QuotaHalf({ label, reading }) {
  if (!reading || !reading.known) {
    return (
      <p className="text-sm text-muted-foreground">
        {label}: <span className="font-mono">unknown</span>
        {reading?.notes?.[0] ? ` — ${reading.notes[0]}` : ''}
      </p>
    )
  }
  const shown = reading.remaining_pct != null
    ? `${Math.round(reading.remaining_pct)}% of ${Number(reading.max).toLocaleString()}`
    : `${Number(reading.remaining ?? 0).toLocaleString()} of ${Number(reading.max ?? 0).toLocaleString()}`
  return (
    <p className="text-sm">
      <span className="text-muted-foreground">{label}: </span>
      <span className="font-mono text-foreground">{shown}</span>
      <span className="ml-2 text-muted-foreground">
        ({Number(reading.remaining).toLocaleString()} left)
      </span>
    </p>
  )
}

/** The alert rules, their fire history, and the evaluate action. */
function AlertsCard({ dashboard, published, draft, onDraft, onRefresh }) {
  const [busy, setBusy] = useState(false)
  const [lastEvaluation, setLastEvaluation] = useState(null)

  async function evaluate() {
    if (!dashboard.room_id) return
    setBusy(true)
    try {
      setLastEvaluation(await monitorApi.evaluateAlerts(dashboard.room_id))
      onRefresh()
    } finally {
      setBusy(false)
    }
  }

  async function addRule(event) {
    event.preventDefault()
    if (!dashboard.room_id) return
    setBusy(true)
    try {
      await monitorApi.createAlertRule(dashboard.room_id, {
        metric: draft.metric,
        threshold: Number(draft.threshold),
        channels: draft.channels,
      })
      onDraft({ ...draft, threshold: '' })
      onRefresh()
    } finally {
      setBusy(false)
    }
  }

  function toggleChannel(channel) {
    const has = draft.channels.includes(channel)
    onDraft({
      ...draft,
      channels: has ? draft.channels.filter((c) => c !== channel) : [...draft.channels, channel],
    })
  }

  const firedIds = new Set((lastEvaluation?.fired || []).map((entry) => entry.rule_id))
  const previewIds = new Set((dashboard.alert_preview?.fired || []).map((entry) => entry.rule_id))

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 font-mono text-sm font-semibold text-foreground">
            <Glyph path={PATHS.alert} size={16} className="text-accent" /> Alert rules
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Budget rules fire when remaining budget drops below their threshold; lag rules fire
            when the change-stream lag exceeds it.
          </p>
        </div>
        <Button icon="play" onClick={evaluate} disabled={busy}>Evaluate now</Button>
      </div>

      <ul className="mt-4 flex flex-col gap-2">
        {dashboard.alert_rules.length === 0 && (
          <li className="text-sm text-muted-foreground">No rules armed for this room yet.</li>
        )}
        {dashboard.alert_rules.map((rule) => (
          <li key={rule.id} className="rounded-lg border border-border-subtle/30 bg-muted/20 px-3 py-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <p className="text-sm text-foreground">
                  {rule.label || <span className="font-mono">{rule.metric}</span>}
                  {!rule.enabled && <span className="ml-2 text-xs text-muted-foreground">(disabled)</span>}
                </p>
                <p className="font-mono text-xs text-muted-foreground">
                  {rule.metric} {rule.comparison} {rule.threshold} ·{' '}
                  {rule.channels.join(', ')} · cooldown {rule.cooldown_minutes}m
                </p>
              </div>
              <div className="flex items-center gap-2">
                {(previewIds.has(rule.id) || firedIds.has(rule.id)) && (
                  <Badge tone="error">{firedIds.has(rule.id) ? 'fired' : 'would fire'}</Badge>
                )}
                {rule.last_fired_at && (
                  <span className="text-xs text-muted-foreground">
                    last fired {relativeTime(rule.last_fired_at)}
                  </span>
                )}
                <Button variant="ghost" onClick={async () => { await monitorApi.deleteAlertRule(rule.id); onRefresh() }}>
                  Remove
                </Button>
              </div>
            </div>
          </li>
        ))}
      </ul>

      {lastEvaluation && (
        <ul className="mt-3 flex flex-col gap-1 text-xs text-muted-foreground">
          {lastEvaluation.rules.map((entry) => (
            <li key={entry.rule_id}>
              <span className="font-mono">{entry.metric}</span> — {entry.reason}
            </li>
          ))}
        </ul>
      )}

      <form onSubmit={addRule} className="mt-4 flex flex-wrap items-end gap-3">
        <Field label="Metric" id="wf049-metric">
          <select
            id="wf049-metric"
            value={draft.metric}
            onChange={(event) => onDraft({ ...draft, metric: event.target.value })}
            className="min-h-11 rounded-lg border border-border-subtle/40 bg-muted/30 px-3 text-sm text-foreground
              focus-visible:ring-2 focus-visible:ring-accent"
          >
            {(published?.metrics || Object.keys(METRIC_UNITS)).map((metric) => (
              <option key={metric} value={metric}>{metric}</option>
            ))}
          </select>
        </Field>
        <Field label="Threshold" id="wf049-threshold" hint={METRIC_UNITS[draft.metric]}>
          <input
            id="wf049-threshold"
            type="number"
            min="0"
            step="any"
            value={draft.threshold}
            onChange={(event) => onDraft({ ...draft, threshold: event.target.value })}
            className="min-h-11 w-28 rounded-lg border border-border-subtle/40 bg-muted/30 px-3 text-sm
              text-foreground focus-visible:ring-2 focus-visible:ring-accent"
          />
        </Field>
        <Field label="Channels" id="wf049-channels">
          <div className="flex gap-2" id="wf049-channels">
            {(published?.channels || []).map((channel) => (
              <Button
                key={channel}
                type="button"
                aria-pressed={draft.channels.includes(channel)}
                onClick={() => toggleChannel(channel)}
                variant={draft.channels.includes(channel) ? 'secondary' : 'ghost'}
              >
                {channel}
              </Button>
            ))}
          </div>
        </Field>
        <Button type="submit" disabled={busy || !draft.threshold}>Arm rule</Button>
      </form>
    </Card>
  )
}

/** The Dataverse change-tracking audit and the drift signal beside it. */
function TrackingCard({ state, published }) {
  const data = state.data
  return (
    <Card>
      <h2 className="flex items-center gap-2 font-mono text-sm font-semibold text-foreground">
        <Glyph path={PATHS.stream} size={16} className="text-accent" /> Change tracking
      </h2>
      {state.error && <ErrorNote error={state.error} onRetry={state.refetch} />}
      {data.total === 0 ? (
        <p className="mt-2 text-sm text-muted-foreground">
          No EntityDefinitions audit recorded for this room yet.
        </p>
      ) : (
        <>
          {data.latest?.drift && (
            <p className="mt-3 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-200">
              {data.latest.note}
            </p>
          )}
          <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-sm sm:grid-cols-3">
            <dt className="text-muted-foreground">Schema version</dt>
            <dd className="font-mono">{data.latest?.globalmetadataversion}</dd>
            <dt className="text-muted-foreground">Tables tracked</dt>
            <dd className="font-mono">{data.latest?.tracked} of {data.latest?.entities.length}</dd>
            <dt className="text-muted-foreground">Drifts recorded</dt>
            <dd className="font-mono">{data.drift_count}</dd>
          </dl>
          <ul className="mt-2 flex flex-wrap gap-2 text-xs">
            {(data.latest?.entities || []).map((entity) => (
              <Badge key={entity.schema_name} tone={entity.change_tracking_enabled ? 'insert' : 'neutral'}>
                {entity.schema_name}
              </Badge>
            ))}
          </ul>
        </>
      )}
    </Card>
  )
}

/** What the research left open, published as data. */
function InferencesPanel() {
  const { data, loading, error, refetch } = useAsync(() => monitorApi.inferences(), [])

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
          “{data.sourced_automation}”
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

/** The calls a connector made, read through the feature's own telemetry view. */

function useRoomSelection(selected, fallback) {
  const [value, setValue] = useState(selected)
  const room = value || fallback || ''
  const select = (next) => setValue(next)
  return [room, select]
}

export default IntegrationMonitorPage
