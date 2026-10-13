/**
 * WF-117: sweep stale opportunities and escalate the pipeline.
 *
 * The page reads its closed vocabularies and its judgements from the server's
 * `/vocabulary` and `/inferences` routes rather than repeating them, so a panel
 * cannot drift from the rules the engine enforces.
 *
 * One thing the page deliberately does not do: it never writes to an
 * opportunity directly. The only write path is Run, which performs the
 * researched Create/Update Opportunity, Add Task and Send Internal Notification
 * actions through the engine.
 */

import { useState } from 'react'

import {
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

import {
  cadencePhrase,
  filterPhrase,
  formatDateTime,
  listRooms,
  reasonLabel,
  stepLabel,
  sweepApi,
} from './api'
import {
  DecisionList,
  Fact,
  InlineNote,
  RunResults,
  Section,
  SweepRow,
  UserFlow,
} from './primitives'

/** A funnel glyph. Passed by path so `components/ui.jsx` is not edited. */
export const SWEEP_GLYPH =
  'M3 4h18l-7 8v7l-4 2v-9L3 4z'

function useAction(onDone) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [note, setNote] = useState(null)

  async function run(action, success) {
    setBusy(true)
    setError(null)
    setNote(null)
    try {
      const result = await action()
      setNote(success)
      onDone?.()
      return result
    } catch (problem) {
      setError(problem)
      return null
    } finally {
      setBusy(false)
    }
  }

  return { busy, error, note, run }
}

function CreateSweepForm({ vocabulary, roomId, onCreated }) {
  const [name, setName] = useState('Stale opportunity sweep')
  const [threshold, setThreshold] = useState(String(vocabulary.default_threshold_days || 7))
  const [basis, setBasis] = useState(vocabulary.default_stale_reference || 'latest_of_both')
  const [stages, setStages] = useState('Closed Won, Closed Lost')
  const [valueMin, setValueMin] = useState('')
  const [valueMax, setValueMax] = useState('')
  const [notifyTeam, setNotifyTeam] = useState('sales')
  const { busy, error, note, run } = useAction(onCreated)

  function submit(event) {
    event.preventDefault()
    const days = Math.max(1, Number(threshold) || 1)
    const cadence = [
      { after_days: days, action: 'nudge' },
      { after_days: days + 14, action: 'escalate' },
    ]
    const stageList = stages
      .split(',')
      .map((value) => value.trim())
      .filter(Boolean)
    run(
      () =>
        sweepApi.createSweep({
          name,
          room_id: roomId || undefined,
          inactivity_basis: basis,
          threshold_days: days,
          cadence,
          filters: {
            stages: stageList,
            stage_mode: 'exclude',
            value_min: valueMin === '' ? undefined : Number(valueMin),
            value_max: valueMax === '' ? undefined : Number(valueMax),
          },
          notify_team: notifyTeam,
          published: true,
        }),
      'The sweep was created and published.',
    )
  }

  return (
    <Section title="Define a stale sweep">
      <form className="grid gap-4 sm:grid-cols-2" onSubmit={submit}>
        <Field label="Sweep name" id="wf117-name" hint="What the run log shows.">
          <input id="wf117-name" className={inputClass} value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field
          label="Inactivity threshold (days)"
          id="wf117-threshold"
          hint="An opportunity at or beyond this is swept."
        >
          <input
            id="wf117-threshold"
            type="number"
            min="1"
            className={inputClass}
            value={threshold}
            onChange={(e) => setThreshold(e.target.value)}
          />
        </Field>
        <Field
          label="Measure inactivity from"
          id="wf117-basis"
          hint="The research names the last activity date and the date entered stage."
        >
          <select id="wf117-basis" className={inputClass} value={basis} onChange={(e) => setBasis(e.target.value)}>
            {(vocabulary.stale_references || []).map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Notify team" id="wf117-team" hint="The rep's team the notification is addressed to.">
          <input id="wf117-team" className={inputClass} value={notifyTeam} onChange={(e) => setNotifyTeam(e.target.value)} />
        </Field>
        <Field
          label="Exclude stages"
          id="wf117-stages"
          hint="Comma separated. A stage filter branches before the escalation."
        >
          <input id="wf117-stages" className={inputClass} value={stages} onChange={(e) => setStages(e.target.value)} />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Value at least" id="wf117-vmin" hint="Optional value band.">
            <input id="wf117-vmin" className={inputClass} value={valueMin} onChange={(e) => setValueMin(e.target.value)} />
          </Field>
          <Field label="Value at most" id="wf117-vmax" hint="Optional value band.">
            <input id="wf117-vmax" className={inputClass} value={valueMax} onChange={(e) => setValueMax(e.target.value)} />
          </Field>
        </div>
        <div className="flex items-end gap-3 sm:col-span-2">
          <Button variant="primary" icon="plus" type="submit" disabled={busy}>
            Create and publish
          </Button>
        </div>
      </form>
      {note && <InlineNote tone="success">{note}</InlineNote>}
      {error && <ErrorNote error={error} />}
    </Section>
  )
}

function SweepDetail({ sweep, onChanged }) {
  const [evaluation, setEvaluation] = useState(null)
  const [lastRun, setLastRun] = useState(null)
  const { busy, error, note, run } = useAction(onChanged)

  function preview() {
    run(async () => {
      const result = await sweepApi.evaluate(sweep.id)
      setEvaluation(result)
      return result
    }, 'Preview computed. Nothing was written.')
  }

  function execute() {
    run(async () => {
      const result = await sweepApi.run(sweep.id)
      setLastRun(result)
      setEvaluation(null)
      return result
    }, 'The sweep ran and the run was recorded.')
  }

  return (
    <Section
      title={sweep.name || sweep.id}
      action={
        <span className="flex flex-wrap items-center gap-2">
          <Button
            icon="refresh"
            disabled={busy}
            onClick={() => run(() => sweepApi.publish(sweep.id, !sweep.published), sweep.published ? 'The sweep was unpublished.' : 'The sweep was published.')}
          >
            {sweep.published ? 'Unpublish' : 'Publish'}
          </Button>
          <Button icon="search" disabled={busy} onClick={preview}>
            Preview
          </Button>
          <Button variant="primary" icon="refresh" disabled={busy || !sweep.published} onClick={execute}>
            Run sweep
          </Button>
        </span>
      }
    >
      <p className="font-mono text-xs text-muted-foreground">{sweep.id}</p>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Fact label="Threshold" hint={cadencePhrase(sweep)}>
          {sweep.threshold_days} days
        </Fact>
        <Fact label="Reference" hint="The instant inactivity is measured from">
          {sweep.inactivity_basis}
        </Fact>
        <Fact label="Filters" hint="The If/Else branch">
          {filterPhrase(sweep)}
        </Fact>
        <Fact label="Re-fire control" hint="The researched Remove from Workflow">
          {sweep.refire_control}
        </Fact>
      </div>

      {!sweep.published && (
        <InlineNote tone="warning">A draft sweep refuses to run. Publish it first.</InlineNote>
      )}

      {evaluation && (
        <div className="flex flex-col gap-3">
          <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
            Preview · {evaluation.scanned} scanned · {evaluation.matched} matched
          </p>
          <DecisionList decisions={evaluation.decisions} />
        </div>
      )}

      {lastRun && (
        <div className="flex flex-col gap-3">
          <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
            Run · {lastRun.escalated} escalated · {lastRun.skipped} skipped · {lastRun.failed} failed
          </p>
          <RunResults results={lastRun.results} />
        </div>
      )}

      {note && <InlineNote tone="success">{note}</InlineNote>}
      {error && <ErrorNote error={error} />}
    </Section>
  )
}

function RunLog({ runs, onOpen }) {
  return (
    <Section title="Run log" action={<span className="text-xs text-muted-foreground">{runs.length} runs</span>}>
      {runs.length === 0 ? (
        <p className="text-sm text-muted-foreground">No sweep has run yet.</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {runs.map((entry) => (
            <li key={entry.id}>
              <button
                type="button"
                onClick={() => onOpen(entry.id)}
                className="flex min-h-11 w-full flex-wrap items-center justify-between gap-2 rounded-sm border border-border-subtle bg-surface px-3 py-2 text-left hover:border-accent"
              >
                <span className="flex flex-col">
                  <span className="text-sm text-foreground">{entry.at || entry.id}</span>
                  <span className="font-mono text-xs text-muted-foreground">
                    {entry.scanned} scanned · {entry.escalated} escalated · {entry.failed} failed
                  </span>
                </span>
                <span className="text-xs text-muted-foreground">
                  {Object.entries(entry.by_reason || {})
                    .map(([reason, count]) => `${count} ${reasonLabel(reason).toLowerCase()}`)
                    .join(' · ')}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </Section>
  )
}

function StaleOpportunitiesPage() {
  const [roomId, setRoomId] = useState('')
  const [selected, setSelected] = useState('')
  const [openRun, setOpenRun] = useState('')
  const [nonce, setNonce] = useState(0)

  const vocabulary = useAsync(() => sweepApi.vocabulary(), [])
  const rooms = useAsync(() => listRooms(), [])
  const summary = useAsync(() => sweepApi.summary(roomId), [roomId, nonce])
  const sweeps = useAsync(() => sweepApi.sweeps(roomId), [roomId, nonce])
  const runs = useAsync(() => sweepApi.runs({ room_id: roomId }), [roomId, nonce])
  const escalations = useAsync(() => sweepApi.escalations({ room_id: roomId }), [roomId, nonce])
  const notifications = useAsync(() => sweepApi.notifications({ room_id: roomId }), [roomId, nonce])
  const detail = useAsync(
    () => (selected ? sweepApi.sweep(selected) : Promise.resolve(null)),
    [selected, nonce],
  )
  const runDetail = useAsync(
    () => (openRun ? sweepApi.readRun(openRun) : Promise.resolve(null)),
    [openRun, nonce],
  )

  function refresh() {
    setNonce((value) => value + 1)
  }

  if (vocabulary.loading || rooms.loading) return <Spinner label="Loading stale opportunities" />
  if (vocabulary.error) return <ErrorNote error={vocabulary.error} onRetry={vocabulary.refetch} />
  if (sweeps.error) return <ErrorNote error={sweeps.error} onRetry={sweeps.refetch} />

  const counts = summary.data || {}
  const roomOptions = rooms.data?.records || []
  const sweepEntries = sweeps.data?.entries || []
  const runEntries = runs.data?.entries || []
  const escalationEntries = escalations.data?.entries || []
  const notificationEntries = notifications.data?.entries || []

  return (
    <div className="space-y-6">
      <header className="flex items-start gap-3">
        <span className="rounded-sm bg-accent-soft p-2 text-accent">
          <Icon path={SWEEP_GLYPH} size={20} />
        </span>
        <div>
          <h1 className="font-display text-2xl font-semibold text-foreground">Stale opportunities</h1>
          <p className="text-sm text-muted-foreground">
            A sweep finds opportunities that exceed an inactivity rule, then escalates each one with an
            opportunity write, an owner task and an internal notification.
          </p>
        </div>
      </header>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Opportunities" value={counts.opportunities ?? 0} icon="database" hint="In the room" />
        <StatCard label="Published sweeps" value={counts.published ?? 0} icon="search" hint="May run" />
        <StatCard label="Escalated" value={counts.escalated_total ?? 0} icon="refresh" hint="Across every run" />
        <StatCard label="Notifications" value={counts.notifications ?? 0} icon="audit" hint="Internal, recorded" />
      </div>

      <Card className="flex flex-wrap items-end gap-4">
        <Field label="Room" id="wf117-room" hint="Sweeps and opportunities are room-scoped.">
          <select
            id="wf117-room"
            className={inputClass}
            value={roomId}
            onChange={(event) => {
              setRoomId(event.target.value)
              setSelected('')
            }}
          >
            <option value="">All rooms</option>
            {roomOptions.map((room) => (
              <option key={room.id} value={room.id}>
                {room.data?.name || room.id}
              </option>
            ))}
          </select>
        </Field>
      </Card>

      <CreateSweepForm vocabulary={vocabulary.data || {}} roomId={roomId} onCreated={refresh} />

      <Section title="Sweeps" action={<span className="text-xs text-muted-foreground">{sweepEntries.length} shown</span>}>
        {sweepEntries.length === 0 ? (
          <EmptyState
            title="No sweeps"
            description="Define a stale sweep above. It appears here once created."
          />
        ) : (
          <ul className="flex flex-col gap-2">
            {sweepEntries.map((sweep) => (
              <SweepRow key={sweep.id} sweep={sweep} selected={sweep.id === selected} onSelect={setSelected} />
            ))}
          </ul>
        )}
      </Section>

      {selected && detail.loading && <Spinner label="Reading the sweep" />}
      {selected && detail.error && <ErrorNote error={detail.error} onRetry={detail.refetch} />}
      {detail.data && <SweepDetail sweep={detail.data} onChanged={refresh} />}

      <RunLog runs={runEntries} onOpen={setOpenRun} />

      {openRun && runDetail.loading && <Spinner label="Reading the run" />}
      {openRun && runDetail.error && <ErrorNote error={runDetail.error} onRetry={runDetail.refetch} />}
      {runDetail.data && (
        <Section title={`Run ${runDetail.data.id}`}>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Fact label="Scanned">{runDetail.data.scanned}</Fact>
            <Fact label="Matched">{runDetail.data.matched}</Fact>
            <Fact label="Escalated">{runDetail.data.escalated}</Fact>
            <Fact label="Failed">{runDetail.data.failed}</Fact>
          </div>
          <RunResults results={runDetail.data.results} />
        </Section>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Section title="Escalations" action={<span className="text-xs text-muted-foreground">{escalationEntries.length}</span>}>
          {escalationEntries.length === 0 ? (
            <p className="text-sm text-muted-foreground">No escalation has run yet.</p>
          ) : (
            <ul className="flex flex-col gap-2">
              {escalationEntries.map((entry) => (
                <li key={entry.id} className="flex flex-wrap items-center justify-between gap-2 text-sm">
                  <span className="text-foreground">{stepLabel(entry.step)} · {formatDateTime(entry.at_ms)}</span>
                  <span className="font-mono text-xs text-muted-foreground">{entry.opportunity_id}</span>
                </li>
              ))}
            </ul>
          )}
        </Section>

        <Section title="Internal notifications" action={<span className="text-xs text-muted-foreground">{notificationEntries.length}</span>}>
          {notificationEntries.length === 0 ? (
            <p className="text-sm text-muted-foreground">No notification has been recorded yet.</p>
          ) : (
            <ul className="flex flex-col gap-2">
              {notificationEntries.map((entry) => (
                <li key={entry.id} className="flex flex-col text-sm">
                  <span className="text-foreground">{entry.message}</span>
                  <span className="text-xs text-muted-foreground">
                    {entry.team} · {entry.assignee || 'unassigned'} · {formatDateTime(entry.at_ms)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Section>
      </div>

      <Section title="The researched user flow">
        <UserFlow steps={vocabulary.data?.user_flow} />
      </Section>
    </div>
  )
}

export default {
  id: 'wf-117-stale-opportunities',
  label: 'Stale opportunities',
  icon: 'search',
  iconPath: SWEEP_GLYPH,
  order: 1017,
  Component: StaleOpportunitiesPage,
}
