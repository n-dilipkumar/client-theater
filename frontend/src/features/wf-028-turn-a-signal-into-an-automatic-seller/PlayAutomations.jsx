import { useCallback, useEffect, useState } from 'react'
import { apiRequest, absoluteTime, relativeTime } from '@/lib/api'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorNote,
  Field,
  Icon,
  JsonView,
  Spinner,
  StatCard,
  inputClass,
  useAsync,
} from '@/components/ui'
import { Glyph } from './icons'
import { Banner, Notice, Section } from './primitives'

/**
 * WF-028: turn a signal into an automatic seller action (Play registration).
 *
 * The page is arranged around the researched flow, because the flow is the part a
 * seller or an admin has to get right:
 *
 *  1. **The automation itself is the headline.** "This workflow *is* the
 *     automation - signal -> task with no human in the loop until the seller
 *     acts." A page that showed a list of Plays without saying that would let a
 *     reader enable one without knowing what it does to their queue.
 *  2. **The registry**, with the two numbers that matter side by side: how many
 *     Plays exist, and how many are *live*. "After registration, the registered
 *     Play must be enabled in the Salesloft UI", so a registry can be full of Plays
 *     that do nothing.
 *  3. **The switch.** Enabling and disabling are the only two controls that change
 *     what happens to a seller, so they are the only ones given a primary style.
 *  4. **The tasks the automation generated** in the selected room, each showing
 *     who it was assigned to and which object decided that, or saying plainly that
 *     it was assigned to nobody.
 *  5. **The outcome events** and where each delivery stands in the researched
 *     retry schedule.
 *  6. **The judgements this build made**, served by the API, so a reader can see
 *     where the sourced research stops and the build's own reading begins without
 *     reading the diff.
 *
 * Two researched rules are surfaced in the UI rather than left in the code,
 * because they change what a reader should expect:
 *
 *  - a Play that is **registered but not enabled** says so on its own row, in the
 *    research's own words, and the switch beside it is the only way to change it;
 *  - a task with **nobody assigned** is still listed, with the object that named
 *    and the reason it came up empty. Dropping it would lose the evidence that the
 *    automation fired.
 *
 * A note on the shared primitives
 * -------------------------------
 * `components/ui.jsx` has no `Notice`, so the two things this page needs beyond it
 * are built in this folder. See `./primitives.jsx`.
 *
 * A note on motion
 * ----------------
 * This page animates nothing, has no scroll reveal, and does not poll, so
 * `prefers-reduced-motion` is satisfied by there being nothing to reduce. That is
 * deliberate: an automation page that ticked every five seconds to watch a task
 * appear would need the reduced-motion branch, and a page that does not tick does
 * not.
 */

const PREFIX = '/wf-028'

/** The tone each delivery state is shown in, from the researched ladder. */
const DELIVERY_TONES = {
  delivered: 'insert',
  retrying: 'update',
  pending: 'neutral',
  failed: 'delete',
}

const ASSIGNMENT_TONES = {
  person_precedence: 'update',
  user_precedence: 'update',
  content_precedence: 'update',
  account_engagement_score: 'update',
  account_last_contact: 'neutral',
  account_no_candidates: 'delete',
  no_object: 'delete',
  object_unresolved: 'delete',
}

/** The reason a Play did not fire, phrased for a seller rather than a developer. */
const MATCH_REASONS = {
  matched: 'fired',
  not_enabled: 'registered, not enabled',
  no_overlap: 'no matching indicator on the signal',
  no_indicators: 'the signal carried no indicators',
  registration_mismatch: 'belongs to a different signal registration',
}

async function get(path, params) {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(params || {})) {
    if (value !== undefined && value !== null && value !== '') query.set(key, value)
  }
  const suffix = query.toString() ? `?${query}` : ''
  return apiRequest(`${PREFIX}${path}${suffix}`)
}

function text(field, locale = 'en') {
  if (!field) return ''
  if (typeof field === 'string') return field
  return field[locale] || field.en || Object.values(field)[0] || ''
}

function roomName(room) {
  return (room?.data?.name || room?.id || '').toString()
}

function PlayRow({ play, onEnable, onDisable, onDestroy, busy }) {
  const attributes = play.attributes || {}
  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-foreground">
              <Glyph name="play" />
            </span>
            <h3 className="text-sm font-semibold text-foreground">{text(play.label)}</h3>
            <Badge tone="neutral">{attributes.task_type || 'unknown type'}</Badge>
            <Badge tone={play.enabled ? 'insert' : 'neutral'}>
              {play.enabled ? 'live' : 'registered, not enabled'}
            </Badge>
            {play.routable === false && <Badge tone="delete">no cadence named</Badge>}
          </div>

          <p className="mt-2 text-sm text-muted-foreground">{text(play.description)}</p>

          <dl className="mt-3 grid grid-cols-1 gap-x-6 gap-y-1 text-xs sm:grid-cols-2">
            <div className="flex gap-2">
              <dt className="text-muted-foreground">Triggers on</dt>
              <dd className="font-mono text-foreground">
                {(play.indicators || []).join(', ') || 'nothing'}
              </dd>
            </div>
            <div className="flex gap-2">
              <dt className="text-muted-foreground">Signal registration</dt>
              <dd className="truncate font-mono text-foreground">{play.signal_registration_id}</dd>
            </div>
            <div className="flex gap-2">
              <dt className="text-muted-foreground">Task subject</dt>
              <dd className="font-mono text-foreground">
                {attributes.task_subject || attributes.email_subject || '—'}
              </dd>
            </div>
            <div className="flex gap-2">
              <dt className="text-muted-foreground">Reminder</dt>
              <dd className="font-mono text-foreground">
                {attributes.task_reminder_hours === undefined || attributes.task_reminder_hours === null
                  ? 'none'
                  : `${attributes.task_reminder_hours}h`}
              </dd>
            </div>
            {play.enabled && play.enabled_by && (
              <div className="flex gap-2">
                <dt className="text-muted-foreground">Enabled by</dt>
                <dd className="font-mono text-foreground">
                  {play.enabled_by} · {relativeTime(play.enabled_at)}
                </dd>
              </div>
            )}
          </dl>

          {!play.enabled && play.note && (
            <p className="mt-3 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-foreground">
              {play.note}
            </p>
          )}

          {(play.warnings || []).length > 0 && (
            <ul className="mt-2 flex flex-col gap-1">
              {play.warnings.map((warning) => (
                <li
                  key={warning.code}
                  className="rounded-lg border border-border-subtle/50 p-2 text-xs text-muted-foreground"
                >
                  <span className="font-mono text-foreground">{warning.field || warning.code}</span>
                  {' — '}
                  {warning.detail}
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="flex shrink-0 flex-col gap-2">
          {play.enabled ? (
            <Button icon="close" onClick={() => onDisable(play)} disabled={busy}>
              Disable
            </Button>
          ) : (
            <Button variant="primary" icon="plus" onClick={() => onEnable(play)} disabled={busy}>
              Enable
            </Button>
          )}
          <Button
            variant="danger"
            icon="trash"
            onClick={() => onDestroy(play)}
            disabled={busy || play.enabled}
            title={play.enabled ? 'Disable the Play before destroying it' : 'Retire this Play template'}
          >
            Destroy
          </Button>
        </div>
      </div>
    </Card>
  )
}

function TaskRow({ task, onComplete, busy }) {
  const assignment = task.assignment || {}
  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-accent">
              <Glyph name="task" />
            </span>
            <span className="text-sm font-semibold text-foreground">
              {task.subject || task.task_type_label || task.task_type}
            </span>
            <Badge tone="neutral">{task.task_type}</Badge>
            <Badge tone={task.state === 'completed' ? 'insert' : 'update'}>{task.state}</Badge>
            <Badge tone={task.assigned ? 'insert' : 'delete'}>
              {task.assigned ? `assigned to ${assignment.seller || assignment.person_id}` : 'unassigned'}
            </Badge>
            {task.routable === false && <Badge tone="delete">not routable</Badge>}
            {task.duplicate_attempts > 0 && (
              <Badge tone="neutral">{task.duplicate_attempts} repeat(s) dropped</Badge>
            )}
          </div>

          <dl className="mt-2 grid grid-cols-1 gap-x-6 gap-y-1 text-xs sm:grid-cols-2">
            <div className="flex gap-2">
              <dt className="text-muted-foreground">Decided by</dt>
              <dd className="flex items-center gap-2">
                <Badge tone={ASSIGNMENT_TONES[assignment.rule] || 'neutral'}>
                  {assignment.object || 'nothing'}
                </Badge>
                <span className="font-mono text-foreground">{assignment.rule || '—'}</span>
              </dd>
            </div>
            <div className="flex gap-2">
              <dt className="text-muted-foreground">Triggered by</dt>
              <dd className="font-mono text-foreground">
                {(task.triggered_by || []).join(', ') || '—'}
              </dd>
            </div>
            <div className="flex gap-2">
              <dt className="text-muted-foreground">Created</dt>
              <dd className="font-mono text-foreground" title={absoluteTime(task.created_at)}>
                {relativeTime(task.created_at)}
              </dd>
            </div>
            {task.completed_at && (
              <div className="flex gap-2">
                <dt className="text-muted-foreground">Completed</dt>
                <dd className="font-mono text-foreground" title={absoluteTime(task.completed_at)}>
                  {relativeTime(task.completed_at)}
                </dd>
              </div>
            )}
          </dl>

          {assignment.reason && (
            <p className="mt-2 text-xs text-muted-foreground">{assignment.reason}</p>
          )}
          {(task.missing || []).length > 0 && (
            <ul className="mt-2 flex flex-col gap-1">
              {task.missing.map((entry) => (
                <li
                  key={entry.field}
                  className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-2 text-xs text-foreground"
                >
                  <span className="font-mono">{entry.field}</span> — {entry.detail}
                </li>
              ))}
            </ul>
          )}
          {task.outcome_note && (
            <p className="mt-2 text-xs text-muted-foreground">
              Seller&rsquo;s note: {task.outcome_note}
            </p>
          )}
        </div>

        {task.state === 'open' && (
          <Button onClick={() => onComplete(task)} disabled={busy} className="shrink-0">
            Mark acted on
          </Button>
        )}
      </div>
    </Card>
  )
}

function EventRow({ event }) {
  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-muted-foreground">
              <Glyph name="delivery" />
            </span>
            <span className="font-mono text-sm text-foreground">{event.event_type}</span>
            <Badge tone={DELIVERY_TONES[event.state] || 'neutral'}>{event.state}</Badge>
            {event.state === 'retrying' && (
              <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <Glyph name="retry" size={14} />
                next attempt {relativeTime(event.next_attempt_at)} · {event.retries_remaining} left
              </span>
            )}
          </div>
          <p className="mt-1.5 text-xs text-muted-foreground">{event.meaning}</p>
          {event.state === 'failed' && (
            <p className="mt-1 text-xs text-destructive">{event.detail}</p>
          )}
        </div>
        <details className="shrink-0">
          <summary className="min-h-11 cursor-pointer list-none text-xs text-muted-foreground hover:text-foreground">
            Attempts ({event.attempts?.length || 0})
          </summary>
          <div className="mt-1 max-w-sm">
            <JsonView value={event.attempts || []} />
          </div>
        </details>
      </div>
    </Card>
  )
}

function InferenceList({ inferences }) {
  return (
    <Card>
      <p className="rounded-lg border border-border-subtle/50 bg-muted/40 p-3 font-mono text-xs text-muted-foreground">
        {inferences.sourced_quote}
      </p>
      <ul className="mt-3 flex flex-col gap-3">
        {(inferences.inferences || []).map((entry) => (
          <li key={entry.id} className="border-l-2 border-border-subtle pl-3">
            <p className="font-mono text-xs text-foreground">{entry.id}</p>
            <p className="text-sm text-foreground">{entry.topic}</p>
            <p className="mt-1 text-xs text-muted-foreground">{entry.basis}</p>
            <p className="mt-1 text-xs text-muted-foreground">
              <span className="text-foreground">Why: </span>
              {entry.why}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              <span className="text-foreground">Change it: </span>
              {entry.change_it}
            </p>
          </li>
        ))}
      </ul>
    </Card>
  )
}

export default function PlayAutomations() {
  const [roomId, setRoomId] = useState('')
  const [outcome, setOutcome] = useState(null)
  const [busy, setBusy] = useState(false)

  const vocabulary = useAsync(() => get('/vocabulary'), [])
  const inferences = useAsync(() => get('/inferences'), [])
  const registry = useAsync(() => get('/play-frameworks'), [])
  const rooms = useAsync(() => apiRequest('/records/room?limit=100'), [])

  // The first room is a sensible default, but a room is never assumed for a
  // decision: the summary below says which room it is showing, and an operator who
  // means a different one picks it.
  useEffect(() => {
    const available = rooms.data?.records || []
    if (!roomId && available.length > 0) setRoomId(available[0].id)
  }, [rooms.data, roomId])

  const summary = useAsync(
    () => (roomId ? get(`/rooms/${roomId}/summary`) : Promise.resolve(null)),
    [roomId]
  )
  const tasks = useAsync(
    () => (roomId ? get(`/rooms/${roomId}/tasks`) : Promise.resolve(null)),
    [roomId]
  )
  const events = useAsync(
    () => (roomId ? get(`/rooms/${roomId}/events`) : Promise.resolve(null)),
    [roomId]
  )

  const refresh = useCallback(() => {
    registry.refetch()
    summary.refetch()
    tasks.refetch()
    events.refetch()
  }, [registry, summary, tasks, events])

  const act = useCallback(
    async (label, work) => {
      setBusy(true)
      setOutcome(null)
      try {
        const result = await work()
        refresh()
        setOutcome({ tone: 'ok', text: label, detail: result?.detail || null })
      } catch (error) {
        setOutcome({ tone: 'bad', text: label, detail: String(error?.message || error) })
      } finally {
        setBusy(false)
      }
    },
    [refresh]
  )

  const rooms_list = rooms.data?.records || []
  const plays = registry.data?.play_frameworks || []
  const selected = rooms_list.find((room) => room.id === roomId)

  if (vocabulary.loading || registry.loading) {
    return <Spinner label="Loading Play automations" />
  }
  if (vocabulary.error) return <ErrorNote error={vocabulary.error} onRetry={vocabulary.refetch} />
  if (registry.error) return <ErrorNote error={registry.error} onRetry={registry.refetch} />

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-2">
        <h1 className="text-lg font-semibold text-foreground">Play automations</h1>
        <p className="max-w-3xl text-sm text-muted-foreground">
          A Play turns a buyer-intent signal into a one-off action a seller is given: a call, an
          email, or a place in a cadence. This is the researched flow from signal registration to
          the outcome webhook, with every decision it makes reported rather than implied.
        </p>
      </header>

      <Notice tone="warning" title="This is the automation" icon="play">
        {vocabulary.data?.automation_note}
      </Notice>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="Live plays"
          value={summary.data ? summary.data.live_plays : '—'}
          hint={
            summary.data
              ? `${summary.data.registered_not_enabled} registered and not enabled`
              : 'Pick a room'
          }
          icon="schema"
        />
        <StatCard
          label="Tasks from automation"
          value={summary.data ? summary.data.tasks_from_automation : '—'}
          hint="created with no human in the loop"
          icon="database"
        />
        <StatCard
          label="Unassigned"
          value={summary.data ? summary.data.unassigned_tasks : '—'}
          hint="created, and reported with the reason"
          icon="audit"
        />
        <StatCard
          label="Webhooks failing"
          value={events.data ? events.data.failing : '—'}
          hint={vocabulary.data?.webhook_retries?.rule}
          icon="refresh"
        />
      </div>

      <Banner outcome={outcome} />

      <Card>
        <Field
          label="Room"
          id="wf028-room"
          hint="Tasks and outcome events belong to the room the buyer's signal was raised in."
        >
          <select
            id="wf028-room"
            className={inputClass}
            value={roomId}
            onChange={(event) => setRoomId(event.target.value)}
          >
            <option value="">Choose a room…</option>
            {rooms_list.map((room) => (
              <option key={room.id} value={room.id}>
                {roomName(room)}
              </option>
            ))}
          </select>
        </Field>
        {selected && (
          <p className="mt-2 text-xs text-muted-foreground">
            Showing {roomName(selected)} · owned by {selected.data?.owner || 'nobody named'} ·{' '}
            activation switch: {vocabulary.data?.activation_path}
          </p>
        )}
      </Card>

      <Section
        title="Play frameworks"
        hint={`${plays.length} registered, ${registry.data?.live || 0} live. "An application can create more than one framework per signal registration", so several Plays on one registration is the normal shape.`}
      >
        {plays.length === 0 ? (
          <EmptyState
            title="No Play framework is registered"
            description="A Play is what turns a signal into an action. Until one is registered against a signal registration, every signal in this product is only a signal."
          />
        ) : (
          <div className="flex flex-col gap-3">
            {plays.map((play) => (
              <PlayRow
                key={play.id}
                play={play}
                busy={busy}
                onEnable={(target) =>
                  act(`Enabled "${text(target.label)}"`, () =>
                    apiRequest(`${PREFIX}/play-frameworks/${target.id}/enable?actor=demo`, {
                      method: 'POST',
                    })
                  )
                }
                onDisable={(target) =>
                  act(`Disabled "${text(target.label)}"`, () =>
                    apiRequest(`${PREFIX}/play-frameworks/${target.id}/disable?actor=demo`, {
                      method: 'POST',
                    })
                  )
                }
                onDestroy={(target) =>
                  act(`Destroyed "${text(target.label)}"`, () =>
                    apiRequest(`${PREFIX}/play-frameworks/${target.id}?actor=demo`, { method: 'DELETE' })
                  )
                }
              />
            ))}
          </div>
        )}
      </Section>

      <Section
        title="Tasks the automation generated"
        hint="One signal creates at most one task per Play, because a Play is a one-off action. A task with nobody assigned is still listed, with the reason."
      >
        {!roomId ? (
          <EmptyState title="Choose a room" description="Tasks are scoped to the room their signal was raised in." />
        ) : tasks.loading ? (
          <Spinner label="Loading tasks" />
        ) : tasks.error ? (
          <ErrorNote error={tasks.error} onRetry={tasks.refetch} />
        ) : (tasks.data?.tasks || []).length === 0 ? (
          <EmptyState
            title="No task in this room yet"
            description="Nothing has fired. Either no Play is enabled, or no signal has carried one of their trigger indicators."
          />
        ) : (
          <div className="flex flex-col gap-3">
            {tasks.data.tasks.map((task) => (
              <TaskRow
                key={task.id}
                task={task}
                busy={busy}
                onComplete={(target) =>
                  act(`Closed the task for ${target.subject || target.task_type}`, () =>
                    apiRequest(
                      `${PREFIX}/rooms/${roomId}/tasks/${target.id}/complete?actor=demo`,
                      { method: 'POST', body: JSON.stringify({}) }
                    )
                  )
                }
              />
            ))}
          </div>
        )}
      </Section>

      <Section
        title="Outcome events"
        hint={vocabulary.data?.webhook_retries?.rule}
      >
        {!roomId ? (
          <EmptyState title="Choose a room" description="Outcome events are scoped to a room." />
        ) : events.loading ? (
          <Spinner label="Loading outcome events" />
        ) : events.error ? (
          <ErrorNote error={events.error} onRetry={events.refetch} />
        ) : (events.data?.events || []).length === 0 ? (
          <EmptyState
            title="No outcome event yet"
            description="An event is recorded when a task is created, and again when its seller acts on it."
          />
        ) : (
          <div className="flex flex-col gap-3">
            {events.data.events.map((event) => (
              <EventRow key={event.id} event={event} />
            ))}
          </div>
        )}
      </Section>

      <Section
        title="Where the research stops"
        hint={`${inferences.data?.count || 0} judgement calls, each with what the research says, what this build chose, and how to change it. Served by ${PREFIX}/inferences.`}
      >
        {inferences.loading ? (
          <Spinner label="Loading the judgement register" />
        ) : inferences.error ? (
          <ErrorNote error={inferences.error} onRetry={inferences.refetch} />
        ) : (
          <InferenceList inferences={inferences.data || { inferences: [] }} />
        )}
      </Section>

      <footer className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <Icon name="audit" size={14} />
        <span>
          Every read and write on this page goes through the audited store, and each write&rsquo;s
          audit row names the route that served it.
        </span>
      </footer>
    </div>
  )
}
