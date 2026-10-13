/**
 * WF-120: enroll records on a schedule with re-enrollment control.
 *
 * The page reads its closed vocabularies and its judgements from the server's
 * `/vocabulary` and `/inferences` routes rather than repeating them, so a panel
 * cannot drift from the rules the engine enforces.
 *
 * One thing the page deliberately does not do: it never writes to a CRM record
 * directly. The only write path is Publish and Run, which perform the researched
 * filter scan, the enrollment writes and the queued actions through the engine.
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
  backfillLabel,
  cadencePhrase,
  filterPhrase,
  formatDateTime,
  listRooms,
  modeLabel,
  parseFilter,
  runKindLabel,
  enrollmentApi,
} from './api'
import {
  DecisionList,
  EnrollmentRow,
  EventLog,
  Fact,
  InlineNote,
  RunResults,
  SCHEDULE_GLYPH,
  ScheduleRow,
  Section,
  UserFlow,
} from './primitives'

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

function CreateScheduleForm({ vocabulary, roomId, onCreated }) {
  const [name, setName] = useState('Scheduled enrollment')
  const [objectName, setObjectName] = useState(vocabulary.default_object || 'Contact')
  const [filter, setFilter] = useState('stage=Qualification')
  const [interval, setInterval] = useState(String(vocabulary.default_interval_minutes || 60))
  const [delay, setDelay] = useState(String(vocabulary.default_delay_minutes || 0))
  const [mode, setMode] = useState(vocabulary.default_reenroll_mode || 'single')
  const [backfill, setBackfill] = useState(vocabulary.default_backfill || 'existing_and_future')
  const { busy, error, note, run } = useAction(onCreated)

  function submit(event) {
    event.preventDefault()
    run(
      () =>
        enrollmentApi.createSchedule({
          name,
          room_id: roomId || undefined,
          object: objectName,
          filters: parseFilter(filter),
          interval_minutes: Math.max(1, Number(interval) || 1),
          delay_minutes: Math.max(0, Number(delay) || 0),
          re_enroll: mode,
          backfill,
        }),
      'The schedule was created.',
    )
  }

  return (
    <Section title="Define a schedule">
      <form className="grid gap-4 sm:grid-cols-2" onSubmit={submit}>
        <Field label="Schedule name" id="wf120-name" hint="What the run log shows.">
          <input
            id="wf120-name"
            className={inputClass}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </Field>
        <Field
          label="Enroll this object"
          id="wf120-object"
          hint="The data sources name contacts, companies, deals, tickets, quotes and custom objects."
        >
          <select
            id="wf120-object"
            className={inputClass}
            value={objectName}
            onChange={(e) => setObjectName(e.target.value)}
          >
            {(vocabulary.crm_objects || []).map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </Field>
        <Field
          label="Enrollment filter"
          id="wf120-filter"
          hint="One condition per line: stage=Qualification, value>=1000, owner=a|b. Empty means every record of the object."
        >
          <textarea
            id="wf120-filter"
            rows={3}
            className={`${inputClass} py-2`}
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        </Field>
        <Field
          label="Re-enroll"
          id="wf120-mode"
          hint="Off by default: a record enrolls once. On: a record re-enrolls only after it completes."
        >
          <select
            id="wf120-mode"
            className={inputClass}
            value={mode}
            onChange={(e) => setMode(e.target.value)}
          >
            {(vocabulary.reenroll_modes || []).map((option) => (
              <option key={option} value={option}>
                {modeLabel(option)}
              </option>
            ))}
          </select>
        </Field>
        <Field
          label="At publish, backfill"
          id="wf120-backfill"
          hint="The Review-and-publish choice. Deferred records still enroll on the next run."
        >
          <select
            id="wf120-backfill"
            className={inputClass}
            value={backfill}
            onChange={(e) => setBackfill(e.target.value)}
          >
            {(vocabulary.backfill_choices || []).map((option) => (
              <option key={option} value={option}>
                {backfillLabel(option)}
              </option>
            ))}
          </select>
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Interval (minutes)" id="wf120-interval" hint="How often it runs.">
            <input
              id="wf120-interval"
              type="number"
              min="1"
              className={inputClass}
              value={interval}
              onChange={(e) => setInterval(e.target.value)}
            />
          </Field>
          <Field label="Queue delay (minutes)" id="wf120-delay" hint="0 fires the actions now.">
            <input
              id="wf120-delay"
              type="number"
              min="0"
              className={inputClass}
              value={delay}
              onChange={(e) => setDelay(e.target.value)}
            />
          </Field>
        </div>
        <div className="flex items-end gap-3 sm:col-span-2">
          <Button variant="primary" icon="plus" type="submit" disabled={busy}>
            Create schedule
          </Button>
        </div>
      </form>
      {note && <InlineNote tone="success">{note}</InlineNote>}
      {error && <ErrorNote error={error} />}
    </Section>
  )
}

function ScheduleDetail({ schedule, onChanged }) {
  const [evaluation, setEvaluation] = useState(null)
  const [lastRun, setLastRun] = useState(null)
  const { busy, error, note, run } = useAction(onChanged)

  function preview() {
    run(async () => {
      const result = await enrollmentApi.evaluate(schedule.id)
      setEvaluation(result)
      return result
    }, 'Preview computed. Nothing was written.')
  }

  function execute() {
    run(async () => {
      const result = await enrollmentApi.run(schedule.id)
      setLastRun(result)
      setEvaluation(null)
      return result
    }, 'The schedule ran and the run was recorded.')
  }

  return (
    <Section
      title={schedule.name || schedule.id}
      action={
        <span className="flex flex-wrap items-center gap-2">
          <Button
            icon="refresh"
            disabled={busy}
            onClick={() =>
              run(
                () => enrollmentApi.publish(schedule.id, !schedule.published, schedule.backfill),
                schedule.published ? 'The schedule was unpublished.' : 'The schedule was published.',
              )
            }
          >
            {schedule.published ? 'Unpublish' : 'Publish'}
          </Button>
          <Button icon="search" disabled={busy} onClick={preview}>
            Preview
          </Button>
          <Button variant="primary" icon="refresh" disabled={busy || !schedule.published} onClick={execute}>
            Run schedule
          </Button>
        </span>
      }
    >
      <p className="font-mono text-xs text-muted-foreground">{schedule.id}</p>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Fact label="Object" hint="The records it scans">
          {schedule.object}
        </Fact>
        <Fact label="Filter" hint={`${schedule.filter_count} condition(s)`}>
          {filterPhrase(schedule)}
        </Fact>
        <Fact label="Interval" hint={cadencePhrase(schedule)}>
          {schedule.interval_minutes} minutes
        </Fact>
        <Fact label="Re-enroll" hint={backfillLabel(schedule.backfill)}>
          {modeLabel(schedule.re_enroll)}
        </Fact>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Fact label="Queue delay" hint="The actions fire once it elapses">
          {schedule.delay_minutes} minutes
        </Fact>
        <Fact label="Next run" hint="From the last run and the interval">
          {formatDateTime(schedule.next_run_at_ms)}
        </Fact>
        <Fact label="Actions" hint="What fires when the queue releases">
          {schedule.action_count}
        </Fact>
        <Fact label="Unenroll criteria" hint="Empty means nobody is removed">
          {schedule.unenroll_count} condition(s)
        </Fact>
      </div>

      {!schedule.published && (
        <InlineNote tone="warning">A draft schedule refuses to run. Publish it first.</InlineNote>
      )}

      {evaluation && (
        <div className="flex flex-col gap-3">
          <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
            Preview · {evaluation.scanned} scanned · {evaluation.matched} to enrol ·{' '}
            {evaluation.queued_due} queue release(s) due
          </p>
          <DecisionList decisions={evaluation.decisions} />
        </div>
      )}

      {lastRun && (
        <div className="flex flex-col gap-3">
          <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
            Run · {lastRun.enrolled} enrolled · {lastRun.re_enrolled} re-enrolled ·{' '}
            {lastRun.released} released · {lastRun.failed} failed
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
    <Section
      title="Run log"
      action={<span className="text-xs text-muted-foreground">{runs.length} runs</span>}
    >
      {runs.length === 0 ? (
        <p className="text-sm text-muted-foreground">No run has been recorded yet.</p>
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
                  <span className="text-sm text-foreground">
                    {runKindLabel(entry.kind)} · {entry.at || entry.id}
                  </span>
                  <span className="font-mono text-xs text-muted-foreground">
                    {entry.scanned} scanned · {entry.enrolled + entry.re_enrolled} enrolled ·{' '}
                    {entry.released} released · {entry.failed} failed
                  </span>
                </span>
                <span className="text-xs text-muted-foreground">
                  {Object.entries(entry.by_reason || {})
                    .map(([reason, count]) => `${count} ${reason.replace(/_/g, ' ')}`)
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

function ScheduleEnrollmentPage() {
  const [roomId, setRoomId] = useState('')
  const [selected, setSelected] = useState('')
  const [openRun, setOpenRun] = useState('')
  const [nonce, setNonce] = useState(0)

  const vocabulary = useAsync(() => enrollmentApi.vocabulary(), [])
  const rooms = useAsync(() => listRooms(), [])
  const summary = useAsync(() => enrollmentApi.summary(roomId), [roomId, nonce])
  const schedules = useAsync(() => enrollmentApi.schedules(roomId), [roomId, nonce])
  const runs = useAsync(() => enrollmentApi.runs({ room_id: roomId }), [roomId, nonce])
  const enrollments = useAsync(() => enrollmentApi.enrollments({ room_id: roomId }), [roomId, nonce])
  const events = useAsync(() => enrollmentApi.events({ room_id: roomId }), [roomId, nonce])
  const notifications = useAsync(
    () => enrollmentApi.notifications({ room_id: roomId }),
    [roomId, nonce],
  )
  const detail = useAsync(
    () => (selected ? enrollmentApi.schedule(selected) : Promise.resolve(null)),
    [selected, nonce],
  )
  const runDetail = useAsync(
    () => (openRun ? enrollmentApi.readRun(openRun) : Promise.resolve(null)),
    [openRun, nonce],
  )

  function refresh() {
    setNonce((value) => value + 1)
  }

  if (vocabulary.loading || rooms.loading) return <Spinner label="Loading scheduled enrollment" />
  if (vocabulary.error) return <ErrorNote error={vocabulary.error} onRetry={vocabulary.refetch} />
  if (schedules.error) return <ErrorNote error={schedules.error} onRetry={schedules.refetch} />

  const counts = summary.data || {}
  const roomOptions = rooms.data?.records || []
  const scheduleEntries = schedules.data?.entries || []
  const runEntries = runs.data?.entries || []
  const enrollmentEntries = enrollments.data?.entries || []
  const eventEntries = events.data?.entries || []
  const notificationEntries = notifications.data?.entries || []

  return (
    <div className="space-y-6">
      <header className="flex items-start gap-3">
        <span className="rounded-sm bg-accent-soft p-2 text-accent">
          <Icon path={SCHEDULE_GLYPH} size={20} />
        </span>
        <div>
          <h1 className="font-display text-2xl font-semibold text-foreground">
            Scheduled enrollment
          </h1>
          <p className="text-sm text-muted-foreground">
            A schedule scans CRM records, enrolls the records that match the enrollment filter, and
            holds each one in the enrollment queue for the configured delay before its actions fire.
          </p>
        </div>
      </header>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Records" value={counts.records ?? 0} icon="database" hint="In the room" />
        <StatCard label="Published schedules" value={counts.published ?? 0} icon="refresh" hint="May run" />
        <StatCard label="Enrolled" value={counts.enrolled_total ?? 0} icon="plus" hint="Across every run" />
        <StatCard label="Queued" value={counts.queued ?? 0} icon="audit" hint="Held by the delay" />
      </div>

      <Card className="flex flex-wrap items-end gap-4">
        <Field label="Room" id="wf120-room" hint="Schedules and records are room-scoped.">
          <select
            id="wf120-room"
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
        <Fact label="Enrollments by state" hint="Queued, completed, unenrolled">
          {`${counts.queued ?? 0} queued · ${counts.completed ?? 0} completed · ${counts.unenrolled ?? 0} unenrolled`}
        </Fact>
      </Card>

      <CreateScheduleForm vocabulary={vocabulary.data || {}} roomId={roomId} onCreated={refresh} />

      <Section
        title="Schedules"
        action={<span className="text-xs text-muted-foreground">{scheduleEntries.length} shown</span>}
      >
        {scheduleEntries.length === 0 ? (
          <EmptyState
            title="No schedules"
            description="Define a schedule above. It appears here once created."
          />
        ) : (
          <ul className="flex flex-col gap-2">
            {scheduleEntries.map((schedule) => (
              <ScheduleRow
                key={schedule.id}
                schedule={schedule}
                selected={schedule.id === selected}
                onSelect={setSelected}
              />
            ))}
          </ul>
        )}
      </Section>

      {selected && detail.loading && <Spinner label="Reading the schedule" />}
      {selected && detail.error && <ErrorNote error={detail.error} onRetry={detail.refetch} />}
      {detail.data && <ScheduleDetail schedule={detail.data} onChanged={refresh} />}

      <RunLog runs={runEntries} onOpen={setOpenRun} />

      {openRun && runDetail.loading && <Spinner label="Reading the run" />}
      {openRun && runDetail.error && <ErrorNote error={runDetail.error} onRetry={runDetail.refetch} />}
      {runDetail.data && (
        <Section title={`Run ${runDetail.data.id}`}>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Fact label="Kind">{runKindLabel(runDetail.data.kind)}</Fact>
            <Fact label="Scanned">{runDetail.data.scanned}</Fact>
            <Fact label="Enrolled">
              {runDetail.data.enrolled + runDetail.data.re_enrolled}
            </Fact>
            <Fact label="Released">{runDetail.data.released}</Fact>
          </div>
          <RunResults results={runDetail.data.results} />
        </Section>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Section
          title="Enrollments"
          action={<span className="text-xs text-muted-foreground">{enrollmentEntries.length}</span>}
        >
          {enrollmentEntries.length === 0 ? (
            <p className="text-sm text-muted-foreground">No record has been enrolled yet.</p>
          ) : (
            <ul className="flex flex-col gap-2">
              {enrollmentEntries.map((enrollment) => (
                <EnrollmentRow key={enrollment.id} enrollment={enrollment} />
              ))}
            </ul>
          )}
        </Section>

        <Section
          title="Workflow log"
          action={<span className="text-xs text-muted-foreground">{eventEntries.length}</span>}
        >
          <EventLog events={eventEntries} />
        </Section>
      </div>

      <Section
        title="Internal notifications"
        action={<span className="text-xs text-muted-foreground">{notificationEntries.length}</span>}
      >
        {notificationEntries.length === 0 ? (
          <p className="text-sm text-muted-foreground">No notification has been recorded yet.</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {notificationEntries.map((entry) => (
              <li key={entry.id} className="flex flex-col text-sm">
                <span className="text-foreground">{entry.message}</span>
                <span className="text-xs text-muted-foreground">
                  {entry.assignee || 'unassigned'} · {formatDateTime(entry.at_ms)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="The researched user flow">
        <UserFlow steps={vocabulary.data?.user_flow} />
      </Section>
    </div>
  )
}

export default {
  id: 'wf-120-schedule-enrollment',
  label: 'Scheduled enrollment',
  icon: 'refresh',
  iconPath: SCHEDULE_GLYPH,
  order: 1020,
  Component: ScheduleEnrollmentPage,
}
