import { useCallback, useState } from 'react'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorNote,
  Icon,
  Spinner,
  StatCard,
  useAsync,
} from '@/components/ui'
import { chaseApi } from './api'
import {
  CHASE_ICON,
  Dialog,
  LabeledInput,
  LabeledSelect,
  Notice,
  SkipRow,
  StepRow,
  humaniseDuration,
  humaniseInstant,
  humaniseMinutes,
} from './primitives'

/**
 * Chase and reroute (WF-107).
 *
 * The page is ordered the way the research's flow is: the two triggers first, because
 * nothing fires until one exists and is set live; then the sweep, because that is the only
 * thing that fires anything; then the conversations the sweeps act on; then office hours,
 * which back the **Show expected reply time** step; and finally the judgement calls, so a
 * reviewer reads the list rather than reconstructing it from the source.
 *
 * Two things this page says out loud, because both are consequences of a decision rather
 * than facts about a running system:
 *
 * - **Nothing fires until you evaluate.** The triggers are "purely time-based, automatic",
 *   which reads like a worker. There is none: the sweep is a route, so a conversation that
 *   has gone quiet sits at "due" until somebody calls it. The header says so and shows the
 *   count.
 * - **No message has left this product.** The research's extensibility needs an Intercom
 *   credential this product does not hold, so every message is a conversation part written
 *   here and the API says `sent_by_this_product: false` throughout.
 */

const STEP_KIND_OPTIONS = [
  { value: 'message', label: 'Message' },
  { value: 'wait', label: 'Wait' },
  { value: 'snooze', label: 'Snooze' },
  { value: 'close_message', label: 'Closing message' },
  { value: 'show_expected_reply_time', label: 'Show expected reply time' },
  { value: 'mark_priority', label: 'Mark as priority' },
  { value: 'tag', label: 'Tag conversation' },
  { value: 'close', label: 'Close conversation' },
  { value: 'assign', label: 'Assign conversation' },
]

const CHASE_STEPS = [
  { kind: 'message', body: 'Just checking if you are still there?' },
  { kind: 'wait', duration_seconds: 600, interruption_events: ['customer_message', 'teammate_message'] },
  { kind: 'close_message', body: 'Closing this for now.' },
  { kind: 'close' },
  { kind: 'tag', tag: 'no reply' },
]

const REROUTE_STEPS = [
  { kind: 'show_expected_reply_time', duration_seconds: 900 },
  { kind: 'mark_priority' },
  { kind: 'tag', tag: 'delayed response' },
  { kind: 'assign', inbox: 'escalations' },
]

export default function Chase() {
  const [roomId, setRoomId] = useState('')
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState(null)
  const [sweepResult, setSweepResult] = useState(null)
  const [editing, setEditing] = useState(null)

  const rooms = useAsync(() => chaseApi.rooms(), [])
  const vocabulary = useAsync(() => chaseApi.vocabulary(), [])
  const inferences = useAsync(() => chaseApi.inferences(), [])

  const summary = useAsync(
    () => (roomId ? chaseApi.summary(roomId) : Promise.resolve(null)),
    [roomId]
  )
  const triggers = useAsync(
    () => (roomId ? chaseApi.triggers(roomId) : Promise.resolve(null)),
    [roomId]
  )
  const runs = useAsync(() => (roomId ? chaseApi.runs(roomId) : Promise.resolve(null)), [roomId])
  const conversations = useAsync(
    () => (roomId ? chaseApi.conversations(roomId) : Promise.resolve(null)),
    [roomId]
  )
  const officeHours = useAsync(
    () => (roomId ? chaseApi.officeHours(roomId) : Promise.resolve(null)),
    [roomId]
  )

  const refreshAll = useCallback(() => {
    summary.refetch()
    triggers.refetch()
    runs.refetch()
    conversations.refetch()
    officeHours.refetch()
  }, [summary, triggers, runs, conversations, officeHours])

  /** Run one call and surface its own failure, rather than a page-wide error. */
  const act = useCallback(
    async (work) => {
      setBusy(true)
      setActionError(null)
      try {
        const result = await work()
        refreshAll()
        return result
      } catch (error) {
        setActionError(error)
        return null
      } finally {
        setBusy(false)
      }
    },
    [refreshAll]
  )

  if (vocabulary.loading || rooms.loading) return <Spinner label="Loading chase and reroute" />
  if (vocabulary.error) return <ErrorNote error={vocabulary.error} onRetry={vocabulary.refetch} />
  if (rooms.error) return <ErrorNote error={rooms.error} onRetry={rooms.refetch} />

  const vocab = vocabulary.data
  const roomOptions = (rooms.data?.records || []).map((room) => ({
    value: room.id,
    label: room.data?.name || room.id,
  }))

  return (
    <div className="space-y-6">
      <header className="space-y-2">
        <div className="flex flex-wrap items-center gap-3">
          <Icon path={CHASE_ICON} size={22} className="text-accent" />
          <h1 className="font-display text-2xl font-semibold text-foreground">
            Chase and reroute
          </h1>
        </div>
        <p className="max-w-3xl text-sm text-muted-foreground">
          Two purely time-based triggers. One chases a buyer who has gone quiet with a
          message, a wait a customer or teammate message cancels, then a closing message, a
          close and a tag. The other hands an unattended conversation to another inbox.
        </p>
      </header>

      <Card>
        <div className="grid gap-4 sm:grid-cols-[minmax(0,20rem)_minmax(0,1fr)] sm:items-end">
          <LabeledSelect
            id="wf107-room"
            label="Room"
            value={roomId}
            options={
              roomOptions.length
                ? roomOptions
                : [{ value: '', label: 'No rooms yet' }]
            }
            onChange={(event) => {
              setRoomId(event.target.value)
              setSweepResult(null)
            }}
            hint="Every conversation, trigger and run here belongs to one room."
          />
          <p className="text-xs text-muted-foreground">
            Nothing fires until a sweep is run. These timers are routes rather than a
            background job, so a conversation that has gone quiet stays at &ldquo;due&rdquo;
            until the button below is pressed.
          </p>
        </div>
      </Card>

      {!roomId ? (
        <EmptyState
          title="Pick a room"
          description="Chase and reroute are room-scoped. The room is what a seller is looking at when they ask which of their conversations went quiet."
        />
      ) : (
        <>
          <RoomStats summary={summary} vocab={vocab} />

          <Notice tone="info" title="No message has left this product">
            {vocab.evidence.sent_by_this_product}
          </Notice>

          {actionError && <ErrorNote error={actionError} onRetry={refreshAll} />}

          <SweepPanel
            vocab={vocab}
            triggers={triggers}
            busy={busy}
            result={sweepResult}
            onEvaluate={async (payload) => {
              const result = await act(() => chaseApi.evaluate(roomId, payload))
              if (result) setSweepResult(result)
            }}
            onGoLive={(triggerId) =>
              act(() => chaseApi.goLive(roomId, triggerId))
            }
            onEdit={setEditing}
            onDelete={(triggerId) => act(() => chaseApi.deleteTrigger(roomId, triggerId))}
          />

          <TriggerBuilder
            vocab={vocab}
            busy={busy}
            onSave={(payload) => act(() => chaseApi.createTrigger(roomId, payload))}
          />

          <ConversationsPanel
            roomId={roomId}
            vocab={vocab}
            data={conversations.data}
            loading={conversations.loading}
            error={conversations.error}
            onRetry={conversations.refetch}
            busy={busy}
            onAct={act}
          />

          <RunsPanel
            vocab={vocab}
            data={runs.data}
            loading={runs.loading}
            busy={busy}
            onAdvance={(runId) => act(() => chaseApi.advance(roomId, runId))}
            onResolve={(runId) => act(() => chaseApi.resolve(roomId, runId))}
          />

          <OfficeHoursPanel
            vocab={vocab}
            data={officeHours.data}
            busy={busy}
            onSave={(payload) => act(() => chaseApi.saveOfficeHours(roomId, payload))}
          />
        </>
      )}

      <DecisionsPanel
        data={inferences.data}
        evidence={vocab.evidence}
        loading={inferences.loading}
      />

      {editing && (
        <TriggerEditor
          trigger={editing}
          vocab={vocab}
          busy={busy}
          onClose={() => setEditing(null)}
          onSave={async (payload) => {
            const saved = await act(() =>
              chaseApi.patchTrigger(roomId, editing.id, payload)
            )
            if (saved) setEditing(null)
          }}
        />
      )}
    </div>
  )
}

function RoomStats({ summary, vocab }) {
  if (summary.loading) return <Spinner label="Loading this room" />
  if (summary.error) return <ErrorNote error={summary.error} onRetry={summary.refetch} />
  if (!summary.data) return null
  const data = summary.data
  const labels = vocab.conversation_state_labels

  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <StatCard label="Conversations" value={data.conversations} hint="In this room" />
      <StatCard
        label="Open"
        value={data.by_state.open}
        hint={labels.open}
        icon="rooms"
      />
      <StatCard
        label="Due now"
        value={data.due}
        hint="Past its window on a live trigger"
        icon="refresh"
      />
      <StatCard
        label="Exempt"
        value={data.api_created}
        hint="Created through the REST API"
        icon="audit"
      />
    </div>
  )
}

function SweepPanel({ vocab, triggers, busy, result, onEvaluate, onGoLive, onEdit, onDelete }) {
  const [kind, setKind] = useState('')

  // The whole ``useAsync`` result is passed in, not ``.data``, because ``data`` is null
  // until the request resolves and reading ``loading`` off a null would crash the panel
  // on the render that first picks a room.
  if (triggers.loading) return <Spinner label="Loading triggers" />
  if (triggers.error) return <ErrorNote error={triggers.error} onRetry={triggers.refetch} />

  const rows = triggers.data?.triggers || []
  const live = rows.filter((row) => row.live).length

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="font-display text-lg font-semibold text-foreground">Triggers</h2>
          <p className="text-sm text-muted-foreground">
            {live} live of {rows.length}. A draft fires nothing, because step 7 of the flow
            is &ldquo;save both and set live&rdquo;.
          </p>
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <LabeledSelect
            id="wf107-sweep-kind"
            label="Trigger kind"
            value={kind}
            options={[
              { value: '', label: 'Both kinds' },
              ...vocab.trigger_kinds.map((value) => ({
                value,
                label: vocab.trigger_kind_labels[value],
              })),
            ]}
            onChange={(event) => setKind(event.target.value)}
          />
          <Button
            variant="primary"
            icon="refresh"
            disabled={busy || !rows.length}
            onClick={() => onEvaluate(kind ? { kind } : {})}
          >
            Evaluate
          </Button>
        </div>
      </div>

      {rows.length === 0 ? (
        <EmptyState
          title="No triggers yet"
          description="Build one below. Nothing fires until it exists, is live, and somebody evaluates."
        />
      ) : (
        <ul className="space-y-3">
          {rows.map((trigger) => (
            <TriggerCard
              key={trigger.id}
              trigger={trigger}
              vocab={vocab}
              busy={busy}
              onGoLive={() => onGoLive(trigger.id)}
              onEdit={() => onEdit(trigger)}
              onDelete={() => onDelete(trigger.id)}
            />
          ))}
        </ul>
      )}

      {result && <SweepResult result={result} vocab={vocab} />}
    </section>
  )
}

function TriggerCard({ trigger, vocab, busy, onGoLive, onEdit, onDelete }) {
  const authority = trigger.close_authority
  const authorityTone =
    authority.authority === 'workflow'
      ? 'success'
      : authority.authority === 'global'
        ? 'warning'
        : 'neutral'
  const authorityLabel =
    authority.authority === 'workflow'
      ? 'This workflow closes it'
      : authority.authority === 'global'
        ? 'The global setting closes it'
        : 'Nothing closes it automatically'

  return (
    <li className="rounded-sm border border-border-subtle bg-surface p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-[16rem] flex-1">
          {/* A heading, not a paragraph: the trigger's name is what identifies the card
              to a screen reader, and it is what a test asserts against. */}
          <h3 className="text-sm font-semibold text-foreground">
            {vocab.trigger_kind_labels[trigger.kind]}
          </h3>
          <p className="font-mono text-xs text-muted-foreground">{trigger.id}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {trigger.live ? (
            <Badge tone="success">Live</Badge>
          ) : (
            <Badge tone="warning">Draft</Badge>
          )}
          <Badge tone="info">{humaniseDuration(trigger.duration_seconds)}</Badge>
          <Badge tone={authorityTone}>{authorityLabel}</Badge>
        </div>
      </div>

      <dl className="mt-3 grid gap-x-6 gap-y-2 text-xs sm:grid-cols-2 lg:grid-cols-4">
        <div>
          <dt className="text-muted-foreground">Measured from</dt>
          <dd className="text-foreground">{trigger.anchor_label}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Channels</dt>
          <dd className="text-foreground">
            {(trigger.channels || []).map((value) => vocab.channel_labels[value]).join(', ') ||
              'none'}
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Goal</dt>
          <dd className="text-foreground">{trigger.goal || 'not set'}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Audience</dt>
          <dd className="text-foreground">{trigger.audience || 'not set'}</dd>
        </div>
      </dl>

      <ol className="mt-3 flex flex-wrap gap-1.5">
        {(trigger.steps || []).map((step, index) => (
          <li key={`${step.kind}-${index}`}>
            <Badge tone={step.kind === 'close' ? 'warning' : 'neutral'}>
              {vocab.step_labels[step.kind] || step.kind}
            </Badge>
          </li>
        ))}
        {(trigger.steps || []).length === 0 && (
          <li className="text-xs text-muted-foreground">No steps, so this trigger does nothing.</li>
        )}
      </ol>

      {!trigger.live && (
        <p className="mt-3 text-xs text-muted-foreground">{authority.quote}</p>
      )}

      <div className="mt-3 flex flex-wrap gap-2">
        {!trigger.live && (
          <Button variant="primary" icon="plus" disabled={busy} onClick={onGoLive}>
            Set live
          </Button>
        )}
        <Button disabled={busy} onClick={onEdit}>
          Edit
        </Button>
        <Button variant="danger" icon="trash" disabled={busy} onClick={onDelete}>
          Remove
        </Button>
      </div>
    </li>
  )
}

function SweepResult({ result, vocab }) {
  return (
    <div className="space-y-3 rounded-sm border border-border-subtle bg-surface p-4">
      <div className="flex flex-wrap items-center gap-3">
        <h3 className="font-display text-base font-semibold text-foreground">Last sweep</h3>
        <Badge tone={result.fired_count ? 'success' : 'neutral'}>
          {result.fired_count} fired
        </Badge>
        <Badge tone={result.skipped_count ? 'warning' : 'neutral'}>
          {result.skipped_count} skipped
        </Badge>
        <span className="text-xs text-muted-foreground">
          {result.due_next} due on the next sweep
        </span>
        <span className="font-mono text-xs text-muted-foreground">
          {humaniseInstant(result.evaluated_at)}
        </span>
      </div>

      {result.fired.length > 0 && (
        <ul className="space-y-2">
          {result.fired.map((run) => (
            <li key={run.id} className="flex flex-wrap items-center gap-2 text-sm">
              <Badge tone="success">Fired</Badge>
              <span className="font-mono text-xs text-muted-foreground">{run.id}</span>
              <span className="text-xs text-muted-foreground">
                {vocab.anchor_labels[run.anchor_kind] || run.anchor_kind}
              </span>
            </li>
          ))}
        </ul>
      )}

      {result.skipped.length > 0 && (
        <>
          <p className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
            Why nothing else fired
          </p>
          <ul>
            {result.skipped.map((skip, index) => (
              <SkipRow
                key={`${skip.conversation_id || 'room'}-${skip.reason}-${index}`}
                skip={skip}
                vocabulary={vocab}
              />
            ))}
          </ul>
        </>
      )}
    </div>
  )
}

function TriggerBuilder({ vocab, busy, onSave }) {
  const [kind, setKind] = useState(vocab.trigger_kinds[0])
  const [seconds, setSeconds] = useState(600)
  const [goal, setGoal] = useState('')
  const [steps, setSteps] = useState(CHASE_STEPS)

  const applyTemplate = useCallback((nextKind) => {
    setKind(nextKind)
    setSeconds(600)
    setSteps(nextKind === 'customer_idle' ? CHASE_STEPS : REROUTE_STEPS)
  }, [])

  const bounds = vocab.bounds
  const outOfRange =
    Number(seconds) <= bounds.min_duration_seconds ||
    Number(seconds) >= bounds.max_duration_seconds

  return (
    <Card>
      <div className="space-y-4">
        <div>
          <h2 className="font-display text-lg font-semibold text-foreground">New trigger</h2>
          <p className="text-sm text-muted-foreground">
            Step 2 sets the inactivity timer and the trigger&rsquo;s Channels, Audience,
            Scheduling and Goal. Step 3 adds the message block, step 4 the Wait, step 5 the
            closing message, Close and Tag.
          </p>
        </div>

        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <LabeledSelect
            id="wf107-new-kind"
            label="Trigger"
            value={kind}
            options={vocab.trigger_kinds.map((value) => ({
              value,
              label: vocab.trigger_kind_labels[value],
            }))}
            onChange={(event) => applyTemplate(event.target.value)}
            hint={
              kind === 'customer_idle'
                ? 'Measured from the last message of any kind.'
                : "Measured from the customer's first message, so a burst of three anchors to the first."
            }
          />
          <LabeledInput
            id="wf107-new-seconds"
            label="Inactivity timer (seconds)"
            type="number"
            value={seconds}
            onChange={(event) => setSeconds(event.target.value)}
            error={outOfRange ? vocab.error_codes.duration_out_of_range.detail : undefined}
            hint={`Longer than ${bounds.min_duration_seconds} seconds and shorter than 14 days.`}
          />
          <LabeledInput
            id="wf107-new-goal"
            label="Goal"
            value={goal}
            placeholder="Chase a buyer who has gone quiet"
            onChange={(event) => setGoal(event.target.value)}
            hint="Free text. The research names the field and gives it no vocabulary."
          />
        </div>

        <Notice tone="warning" title={vocab.evidence.duration_bounds}>
          Both bounds are exclusive. {bounds.min_duration_seconds} seconds and exactly 14
          days are both refused.
        </Notice>

        <div className="space-y-2">
          <p className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
            Steps, in order
          </p>
          <ul className="space-y-2">
            {steps.map((step, index) => (
              <StepRow
                key={`${step.kind}-${index}`}
                step={step}
                vocabulary={vocab}
                disabled={busy}
                onChange={(next) =>
                  setSteps(steps.map((entry, at) => (at === index ? next : entry)))
                }
                onRemove={() => setSteps(steps.filter((_, at) => at !== index))}
              />
            ))}
          </ul>
          <LabeledSelect
            id="wf107-add-step"
            label="Add a step"
            value=""
            options={STEP_KIND_OPTIONS}
            onChange={(event) => {
              if (!event.target.value) return
              const added = { kind: event.target.value }
              if (added.kind === 'wait' || added.kind === 'snooze') {
                added.duration_seconds = 600
                added.interruption_events = ['customer_message', 'teammate_message']
              }
              if (added.kind === 'tag') added.tag = 'no reply'
              if (added.kind === 'assign') added.inbox = 'escalations'
              setSteps([...steps, added])
            }}
          />
        </div>

        <Button
          variant="primary"
          icon="plus"
          disabled={busy || outOfRange}
          onClick={() =>
            onSave({
              kind,
              duration_seconds: Number(seconds),
              channels: vocab.channels.slice(0, 2),
              goal: goal || null,
              steps,
            })
          }
        >
          Create draft trigger
        </Button>
      </div>
    </Card>
  )
}

function ConversationsPanel({
  roomId,
  vocab,
  data,
  loading,
  error,
  onRetry,
  busy,
  onAct,
}) {
  const [state, setState] = useState('')
  const [draft, setDraft] = useState(null)

  if (loading) return <Spinner label="Loading conversations" />
  if (error) return <ErrorNote error={error} onRetry={onRetry} />

  const rows = data?.conversations || []
  const shown = state ? rows.filter((row) => row.state === state) : rows

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="font-display text-lg font-semibold text-foreground">
            Conversations
          </h2>
          <p className="text-sm text-muted-foreground">
            A conversation created through the REST API is readable and writable here and
            never triggers, because the rule scopes triggers rather than records.
          </p>
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <LabeledSelect
            id="wf107-state"
            label="State"
            value={state}
            options={[
              { value: '', label: 'All states' },
              ...vocab.conversation_states.map((value) => ({
                value,
                label: vocab.conversation_state_labels[value],
              })),
            ]}
            onChange={(event) => setState(event.target.value)}
          />
          <Button icon="plus" disabled={busy} onClick={() => setDraft({ origin: 'inbox', inbox: 'sales', subject: '' })}>
            Open a conversation
          </Button>
        </div>
      </div>

      {shown.length === 0 ? (
        <EmptyState
          title="No conversations"
          description="A conversation needs a customer message before either trigger has an anchor to measure from."
        />
      ) : (
        <ul className="grid gap-3 sm:grid-cols-2">
          {shown.map((row) => (
            <ConversationCard
              key={row.id}
              roomId={roomId}
              row={row}
              vocab={vocab}
              inboxes={data.inboxes}
              busy={busy}
              onAct={onAct}
            />
          ))}
        </ul>
      )}

      {draft && (
        <Dialog
          onClose={() => setDraft(null)}
          title="Open a conversation"
          footer={
            <>
              <Button onClick={() => setDraft(null)}>Cancel</Button>
              <Button
                variant="primary"
                disabled={busy}
                onClick={async () => {
                  const saved = await onAct(() =>
                    chaseApi.openConversation(roomId, {
                      origin: draft.origin,
                      inbox: draft.inbox || null,
                      subject: draft.subject || null,
                    })
                  )
                  if (saved) setDraft(null)
                }}
              >
                Open
              </Button>
            </>
          }
        >
          <div className="space-y-4">
            <LabeledInput
              id="wf107-draft-subject"
              label="Subject"
              value={draft.subject}
              onChange={(event) => setDraft({ ...draft, subject: event.target.value })}
            />
            <LabeledSelect
              id="wf107-draft-origin"
              label="Origin"
              value={draft.origin}
              options={vocab.origins.map((value) => ({
                value,
                label: vocab.origin_labels[value],
              }))}
              onChange={(event) => setDraft({ ...draft, origin: event.target.value })}
              hint={vocab.evidence.api_created_exempt}
            />
            <LabeledInput
              id="wf107-draft-inbox"
              label="Inbox"
              value={draft.inbox}
              onChange={(event) => setDraft({ ...draft, inbox: event.target.value })}
              hint="A reroute must name a different inbox."
            />
          </div>
        </Dialog>
      )}
    </section>
  )
}

function ConversationCard({ roomId, row, vocab, inboxes, busy, onAct }) {
  const [expanded, setExpanded] = useState(false)
  const activity = useAsync(
    () => (expanded ? chaseApi.activity(roomId, row.id) : Promise.resolve(null)),
    [expanded, row.id]
  )
  const [rerouteTo, setRerouteTo] = useState('')

  return (
    <li className="rounded-sm border border-border-subtle bg-surface p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-[12rem] flex-1">
          {/* A heading, so the conversation is identifiable by name rather than by
              position, to a screen reader and to a test alike. */}
          <h3 className="text-sm font-semibold text-foreground">
            {row.subject || 'Untitled conversation'}
          </h3>
          <p className="font-mono text-xs text-muted-foreground">{row.id}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={row.state === 'closed' ? 'neutral' : 'success'}>
            {vocab.conversation_state_labels[row.state]}
          </Badge>
          {row.origin === 'api' && <Badge tone="warning">Exempt</Badge>}
          {row.priority && <Badge tone="warning">Priority</Badge>}
        </div>
      </div>

      <dl className="mt-3 grid gap-x-4 gap-y-1 text-xs sm:grid-cols-2">
        <div className="flex gap-2">
          <dt className="text-muted-foreground">Inbox</dt>
          <dd className="text-foreground">
            {row.inbox || 'none'}
            {row.previous_inbox ? (
              <span className="text-muted-foreground"> (was {row.previous_inbox})</span>
            ) : null}
          </dd>
        </div>
        <div className="flex gap-2">
          <dt className="text-muted-foreground">Tags</dt>
          <dd className="text-foreground">{(row.tags || []).join(', ') || 'none'}</dd>
        </div>
        <div className="flex gap-2">
          <dt className="text-muted-foreground">Customer first</dt>
          <dd className="font-mono text-foreground">
            {humaniseInstant(row.customer_first_message_at)}
          </dd>
        </div>
        <div className="flex gap-2">
          <dt className="text-muted-foreground">Last activity</dt>
          <dd className="font-mono text-foreground">
            {humaniseInstant(row.last_activity_at)}
          </dd>
        </div>
      </dl>

      <div className="mt-3 flex flex-wrap gap-2">
        <Button disabled={busy} onClick={() => setExpanded(!expanded)}>
          {expanded ? 'Hide activity' : 'Show activity'}
        </Button>
        <Button
          disabled={busy || row.state === 'closed'}
          onClick={() => onAct(() => chaseApi.snooze(roomId, row.id, {}))}
        >
          Snooze
        </Button>
        <Button
          disabled={busy || row.state === 'closed'}
          onClick={() => onAct(() => chaseApi.close(roomId, row.id, {}))}
        >
          Close
        </Button>
        <Button
          disabled={busy || row.state === 'closed' || !rerouteTo}
          onClick={() => onAct(() => chaseApi.reroute(roomId, row.id, { inbox: rerouteTo }))}
        >
          Assign
        </Button>
      </div>

      <div className="mt-3 flex flex-wrap items-end gap-2">
        <LabeledSelect
          id={`wf107-reroute-${row.id}`}
          label="Assign conversation to"
          value={rerouteTo}
          options={[{ value: '', label: 'Choose an inbox' }].concat(
            // Published by the backend on every conversation response, so the picker cannot
            // offer a destination the reroute route would refuse with `inbox_unknown`.
            (inboxes || []).map((value) => ({ value, label: value }))
          )}
          onChange={(event) => setRerouteTo(event.target.value)}
        />
      </div>

      {expanded && (
        <div className="mt-3 border-t border-border-subtle pt-3">
          {activity.loading ? (
            <Spinner label="Loading activity" />
          ) : activity.error ? (
            <ErrorNote error={activity.error} onRetry={activity.refetch} />
          ) : (
            <ol className="space-y-1">
              {(activity.data?.activity || []).map((event) => (
                <li key={event.id} className="flex flex-wrap items-baseline gap-2 text-xs">
                  <span className="font-mono text-muted-foreground">
                    {humaniseInstant(event.at)}
                  </span>
                  <span className="text-foreground">{event.label}</span>
                  <span className="font-mono text-muted-foreground">{event.code}</span>
                </li>
              ))}
              {(activity.data?.activity || []).length === 0 && (
                <li className="text-xs text-muted-foreground">Nothing has happened yet.</li>
              )}
            </ol>
          )}
        </div>
      )}
    </li>
  )
}

function RunsPanel({ vocab, data, loading, busy, onAdvance, onResolve }) {
  if (loading) return <Spinner label="Loading runs" />
  if (!data) return null

  const rows = data.runs || []

  return (
    <section className="space-y-4">
      <div>
        <h2 className="font-display text-lg font-semibold text-foreground">Runs</h2>
        <p className="text-sm text-muted-foreground">
          A run holds the step cursor and the wait, which is the whole of &ldquo;the
          Wait/Snooze timer runs and can be interrupted&rdquo;. An interrupted run is
          finished: an interruption cancels the wait rather than pausing it.
        </p>
      </div>

      {rows.length === 0 ? (
        <EmptyState
          title="No runs"
          description="Nothing has fired. A conversation has to be quiet for its trigger's timer, on a trigger that is live."
        />
      ) : (
        <ul className="space-y-3">
          {rows.map((run) => (
            <li key={run.id} className="rounded-sm border border-border-subtle bg-surface p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-[16rem] flex-1">
                  <p className="text-sm font-semibold text-foreground">
                    {vocab.trigger_kind_labels[run.kind] || run.kind}
                  </p>
                  <p className="font-mono text-xs text-muted-foreground">{run.id}</p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={run.state === 'interrupted' ? 'warning' : 'info'}>
                    {run.state_label}
                  </Badge>
                  <Badge tone="neutral">
                    Step {run.cursor} of {run.steps.length}
                  </Badge>
                  {run.interrupted_by && (
                    <Badge tone="warning">Interrupted by {run.interrupted_by}</Badge>
                  )}
                </div>
              </div>

              <ol className="mt-3 flex flex-wrap gap-1.5">
                {run.steps.map((step, index) => (
                  <li key={`${step.kind}-${index}`}>
                    <Badge tone={index < run.cursor ? 'success' : 'neutral'}>
                      {vocab.step_labels[step.kind] || step.kind}
                    </Badge>
                  </li>
                ))}
              </ol>

              <dl className="mt-3 grid gap-x-4 gap-y-1 text-xs sm:grid-cols-3">
                <div className="flex gap-2">
                  <dt className="text-muted-foreground">Anchor</dt>
                  <dd className="text-foreground">{run.anchor_label}</dd>
                </div>
                <div className="flex gap-2">
                  <dt className="text-muted-foreground">Started</dt>
                  <dd className="font-mono text-foreground">
                    {humaniseInstant(run.started_at)}
                  </dd>
                </div>
                <div className="flex gap-2">
                  <dt className="text-muted-foreground">Wait started</dt>
                  <dd className="font-mono text-foreground">
                    {humaniseInstant(run.wait_started_at)}
                  </dd>
                </div>
              </dl>

              <div className="mt-3 flex flex-wrap gap-2">
                <Button
                  disabled={busy || !['running'].includes(run.state)}
                  onClick={() => onAdvance(run.id)}
                >
                  Advance
                </Button>
                <Button
                  disabled={busy || run.state !== 'waiting'}
                  onClick={() => onResolve(run.id)}
                >
                  Resolve wait
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function OfficeHoursPanel({ vocab, data, busy, onSave }) {
  const [draft, setDraft] = useState(null)

  if (!data) return null
  const schedule = (draft ? draft.schedule : data.schedule) || {}

  const setDay = (day, field, value) => {
    const base = draft ? draft.schedule : data.schedule
    const next = { ...base, [day]: { ...(base[day] || {}) } }
    if (field === 'open' && value === '') {
      next[day] = { open: null, close: null }
    } else if (field === 'open') {
      next[day] = { ...next[day], open: Number(value) }
    } else {
      next[day] = { ...next[day], close: Number(value) }
    }
    setDraft({ schedule: next, timezone: draft ? draft.timezone : data.timezone })
  }

  return (
    <section className="space-y-4">
      <div>
        <h2 className="font-display text-lg font-semibold text-foreground">Office hours</h2>
        <p className="text-sm text-muted-foreground">
          The <strong>Show expected reply time</strong> step uses this schedule.
          {data.derived && ' Nothing has been stored, so the derived default is in use.'}
        </p>
      </div>

      <Notice tone="info" title="Derived, not researched">
        {data.derivation}
      </Notice>

      <Card>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {(vocab.weekdays || []).map((day) => {
            const entry = schedule[day] || { open: null, close: null }
            const closed = entry.open === null || entry.open === undefined
            return (
              <fieldset key={day} className="rounded-sm border border-border-subtle p-3">
                <legend className="px-1 text-[13px] font-medium capitalize text-foreground">
                  {day}
                </legend>
                {closed ? (
                  <>
                    <p className="text-xs text-muted-foreground">Closed all day</p>
                    <Button
                      className="mt-2"
                      disabled={busy}
                      onClick={() => setDay(day, 'open', vocab.default_office_open_minutes)}
                    >
                      Open this day
                    </Button>
                  </>
                ) : (
                  <>
                    <p className="text-xs text-muted-foreground">
                      {humaniseMinutes(entry.open)} to {humaniseMinutes(entry.close)}
                    </p>
                    <div className="mt-2 flex flex-wrap gap-2">
                      <label className="flex flex-col gap-1 text-[11px] text-muted-foreground">
                        Open
                        <input
                          type="number"
                          className="min-h-11 w-24 rounded-sm border border-border-subtle bg-surface px-2 text-sm text-foreground"
                          value={entry.open}
                          disabled={busy}
                          onChange={(event) => setDay(day, 'open', event.target.value)}
                        />
                      </label>
                      <label className="flex flex-col gap-1 text-[11px] text-muted-foreground">
                        Close
                        <input
                          type="number"
                          className="min-h-11 w-24 rounded-sm border border-border-subtle bg-surface px-2 text-sm text-foreground"
                          value={entry.close}
                          disabled={busy}
                          onChange={(event) => setDay(day, 'close', event.target.value)}
                        />
                      </label>
                    </div>
                    <Button
                      className="mt-2"
                      disabled={busy}
                      onClick={() => setDay(day, 'open', '')}
                    >
                      Close all day
                    </Button>
                  </>
                )}
              </fieldset>
            )
          })}
        </div>

        <div className="mt-4 flex flex-wrap gap-2">
          <Button
            variant="primary"
            disabled={busy}
            onClick={() => onSave(draft || { schedule: data.schedule, timezone: data.timezone })}
          >
            Save office hours
          </Button>
          {draft && (
            <Button disabled={busy} onClick={() => setDraft(null)}>
              Discard changes
            </Button>
          )}
        </div>
      </Card>
    </section>
  )
}

function DecisionsPanel({ data, evidence, loading }) {
  if (loading) return <Spinner label="Loading the judgement calls" />
  if (!data) return null

  return (
    <section className="space-y-4">
      <div>
        <h2 className="font-display text-lg font-semibold text-foreground">
          What the research left open
        </h2>
        <p className="text-sm text-muted-foreground">
          Every decision this workflow rests on, with the alternative that was rejected.
          Rendered rather than documented, so a reviewer reads the list instead of
          reconstructing it from the diff.
        </p>
      </div>

      <ul className="space-y-3">
        {(data.decisions || []).map((entry) => (
          <li key={entry.id} className="rounded-sm border border-border-subtle bg-surface p-4">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="text-sm font-semibold text-foreground">{entry.question}</h3>
              {entry.unsourced && <Badge tone="warning">Unsourced</Badge>}
            </div>
            <div className="mt-2 space-y-2 text-sm">
              <p className="text-foreground">{entry.chosen}</p>
              <p className="text-muted-foreground">
                <span className="font-medium">Rejected: </span>
                {entry.rejected}
              </p>
              <p className="text-muted-foreground">{entry.consequence}</p>
            </div>
          </li>
        ))}
      </ul>

      <EvidenceList evidence={evidence} />
    </section>
  )
}

/**
 * The sentences the specification itself quotes, so a reader can check the rules against
 * the source rather than taking this page's summary of them on trust.
 */
function EvidenceList({ evidence }) {
  if (!evidence) return null
  return (
    <details className="rounded-sm border border-border-subtle bg-surface p-4">
      <summary className="cursor-pointer text-sm font-medium text-foreground">
        What the specification itself quotes
      </summary>
      <dl className="mt-3 space-y-2">
        {Object.entries(evidence).map(([key, value]) => (
          <div key={key}>
            <dt className="font-mono text-xs text-muted-foreground">{key}</dt>
            <dd className="text-sm text-foreground">{value}</dd>
          </div>
        ))}
      </dl>
    </details>
  )
}

function TriggerEditor({ trigger, vocab, busy, onClose, onSave }) {
  const [steps, setSteps] = useState(trigger.steps || [])
  const [seconds, setSeconds] = useState(trigger.duration_seconds)

  const bounds = vocab.bounds
  const outOfRange = Number(seconds) <= bounds.min_duration_seconds
    || Number(seconds) >= bounds.max_duration_seconds

  return (
    <Dialog
      onClose={onClose}
      title={`Edit ${vocab.trigger_kind_labels[trigger.kind]}`}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            disabled={busy || outOfRange}
            onClick={() => onSave({ duration_seconds: Number(seconds), steps })}
          >
            Save
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <LabeledInput
          id="wf107-edit-seconds"
          label="Inactivity timer (seconds)"
          type="number"
          value={seconds}
          onChange={(event) => setSeconds(event.target.value)}
          error={outOfRange ? vocab.error_codes.duration_out_of_range.detail : undefined}
          hint={vocab.evidence.duration_bounds}
        />
        <p className="text-sm text-muted-foreground">
          This trigger is measured from <strong>{trigger.anchor_label}</strong>, and
          closing is owned by {trigger.close_authority.authority === 'workflow' ? 'this workflow' : 'the global setting'}.
        </p>
        <ul className="space-y-2">
          {steps.map((step, index) => (
            <StepRow
              key={`${step.kind}-${index}`}
              step={step}
              vocabulary={vocab}
              disabled={busy}
              onChange={(next) =>
                setSteps(steps.map((entry, at) => (at === index ? next : entry)))
              }
              onRemove={() => setSteps(steps.filter((_, at) => at !== index))}
            />
          ))}
        </ul>
      </div>
    </Dialog>
  )
}


