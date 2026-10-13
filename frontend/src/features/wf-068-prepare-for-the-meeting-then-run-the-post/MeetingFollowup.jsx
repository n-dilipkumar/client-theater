import { useState } from 'react'
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
import { followupApi, listRooms } from './api'
import {
  ACTIVITY_WORD,
  GateQuote,
  MachineValue,
  STATUSES,
  StatusBadge,
  assignErrorWord,
  isDateTrigger,
  positionLabel,
  triggerLabel,
} from './primitives'

/**
 * Meeting prep and the follow-up sequence (WF-068).
 *
 * The researched flow in two halves: a rep opens the **pre-meeting prep page**
 * before the call, and after the call an automation **enrols the prospect in a
 * multi-touch follow-up sequence** that owns its to-dos and its emails.
 *
 * **Six rules this page holds to, and each one is a place a page like this
 * normally lies.**
 *
 * 1. *The three statuses are named, never tinted alone.* `Running`, `Pending` and
 *    `Paused` is the whole researched enum. A page that adds a fourth word, or
 *    renders the three as three colours, tells a reader something the vendor has
 *    never documented.
 * 2. *A per-prospect failure is not a batch failure.* One contact with no parent
 *    account comes back as one entry beside two successful ones. Rendering that as
 *    "the batch failed" would send a seller to rebuild a request that mostly
 *    worked.
 * 3. *A date-triggered automation has not run, and the page says when it will.*
 *    "They do not run immediately when an automation is created or edited" is a
 *    rule a page that drew a date trigger like any other would break.
 * 4. *The two permission gates are quoted, not paraphrased.* "If they don't, the
 *    automation won't run" is the sentence an operator acts on.
 * 5. *Nothing here claims to have been sent.* This product is a client of neither
 *    Gong nor Cal, so every outbound request is rendered as recorded and not sent.
 * 6. *The to-do position is the researched string.* `1/5`, not two numbers in two
 *    columns.
 */

function Section({ title, children, note }) {
  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-lg font-semibold text-foreground">{title}</h2>
        {note && <p className="text-xs text-muted-foreground">{note}</p>}
      </div>
      {children}
    </section>
  )
}

function MicroLabel({ children }) {
  return (
    <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
      {children}
    </p>
  )
}

function MeetingPrep({ meetingId, onError }) {
  const page = useAsync(
    () => (meetingId ? followupApi.prepPage(meetingId) : Promise.resolve(null)),
    [meetingId],
  )
  const [question, setQuestion] = useState('')
  const [answer, setAnswer] = useState(null)
  const [dryRun, setDryRun] = useState(null)

  if (!meetingId) {
    return (
      <EmptyState
        title="No prep page yet"
        description="A prep page carries the six researched sections, the participant list and the recent-activity feed. Open one and it appears here."
      />
    )
  }
  if (page.loading) return <Spinner label="Loading the prep page" />
  if (page.error) return <ErrorNote error={page.error} onRetry={page.refetch} />
  if (!page.data) return null

  const view = page.data
  const activities = view.recent_activity || {}

  return (
    <div className="space-y-4">
      <Card className="space-y-3">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h3 className="text-base font-semibold text-foreground">{view.title}</h3>
          <MachineValue className="text-muted-foreground">{view.meeting_id}</MachineValue>
        </div>
        <p className="text-sm text-muted-foreground">
          <MachineValue>{view.starts_at}</MachineValue>
          {view.objective ? ` - ${view.objective}` : ''}
        </p>
        <p className="text-xs text-muted-foreground">
          Summary assembled from stored records. Open it with{' '}
          <MachineValue>{view.keyboard_shortcut}</MachineValue>.
        </p>
      </Card>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {(view.sections || []).map((section) => (
          <Card key={section} className="space-y-2">
            <MicroLabel>{section.replace(/_/g, ' ')}</MicroLabel>
            {Array.isArray(view.summary?.[section]) && view.summary[section].length ? (
              <ul className="space-y-1 text-sm text-foreground">
                {view.summary[section].map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">Nothing recorded yet.</p>
            )}
          </Card>
        ))}
      </div>

      <Card className="space-y-3">
        <MicroLabel>participants</MicroLabel>
        {view.participants?.length ? (
          <ul className="space-y-2">
            {view.participants.map((participant) => (
              <li
                key={participant.email}
                className="flex flex-wrap items-center justify-between gap-2 border-b border-border-subtle pb-2 last:border-0"
              >
                <span className="text-sm text-foreground">
                  {participant.name || participant.email}
                </span>
                <MachineValue className="text-muted-foreground">{participant.email}</MachineValue>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">No participants recorded.</p>
        )}
      </Card>

      <Card className="space-y-3">
        <MicroLabel>recent activity</MicroLabel>
        {view.mobile ? (
          <p className="text-sm text-muted-foreground">
            The mobile app does not display recent activities.
          </p>
        ) : (
          <div className="space-y-3">
            {Object.keys(activities).map((kind) => (
              <div key={kind}>
                <p className="text-[13px] font-medium text-foreground">
                  {ACTIVITY_WORD[kind] || kind}{' '}
                  <MachineValue className="text-muted-foreground">
                    {activities[kind].length}
                  </MachineValue>
                </p>
                <ul className="mt-1 space-y-0.5 text-sm text-muted-foreground">
                  {activities[kind].map((entry, index) => (
                    <li key={`${kind}-${index}`}>{entry.summary}</li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        )}
      </Card>

      <div className="grid gap-4 sm:grid-cols-2">
        <Card className="space-y-3">
          <MicroLabel>ask anything</MicroLabel>
          <Field
            label="Question about this meeting"
            id="wf068-ask"
            hint="The answer is drawn from the prep sections above, and every answer names the sections it used."
          >
            <input
              id="wf068-ask"
              className={inputClass}
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder="What were the objectives?"
            />
          </Field>
          <Button
            variant="primary"
            onClick={async () => {
              try {
                const result = await followupApi.ask(meetingId, question)
                setAnswer(result.data)
              } catch (error) {
                onError(error)
              }
            }}
          >
            Ask
          </Button>
          {answer && (
            <div className="space-y-2">
              <p className="text-sm text-foreground">
                Answered from{' '}
                <MachineValue className="text-muted-foreground">
                  {answer.sources.join(', ') || 'no section'}
                </MachineValue>
              </p>
              <JsonView value={answer.answer} />
            </div>
          )}
        </Card>

        <Card className="space-y-3">
          <MicroLabel>dry run</MicroLabel>
          <p className="text-sm text-muted-foreground">
            Rehearses against a participant built from the meeting context. The research
            names no voice provider, so nothing is spoken.
          </p>
          <Button
            onClick={async () => {
              try {
                const result = await followupApi.dryRun(meetingId, {})
                setDryRun(result.data)
              } catch (error) {
                onError(error)
              }
            }}
          >
            Run a rehearsal
          </Button>
          {dryRun && (
            <div className="space-y-2">
              <p className="text-sm text-foreground">
                Voice <MachineValue>{dryRun.voice}</MachineValue>. Objectives covered{' '}
                <MachineValue>
                  {dryRun.feedback.objectives_covered.length}/
                  {dryRun.feedback.objectives_total}
                </MachineValue>
                .
              </p>
              <JsonView value={dryRun.participant} />
            </div>
          )}
        </Card>
      </div>
    </div>
  )
}

function Automations({ vocabulary, onError, onChanged }) {
  const automations = useAsync(() => followupApi.automations(), [])
  const [crmId, setCrmId] = useState('con-1')
  const [runOn, setRunOn] = useState('')

  if (automations.loading) return <Spinner label="Loading automations" />
  if (automations.error) return <ErrorNote error={automations.error} onRetry={automations.refetch} />

  const labels = vocabulary?.trigger_labels || {}
  const rows = automations.data?.automations || []

  return (
    <div className="space-y-4">
      {rows.length === 0 ? (
        <EmptyState
          title="No automations yet"
          description="An automation is a When, an If, and the flow to enrol into. It will not run until an assignee with an Engage licence holds it."
        />
      ) : (
        <ul className="space-y-2">
          {rows.map((row) => (
            <li key={row.id}>
              <Card className="space-y-2">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-sm font-medium text-foreground">{row.data.name}</span>
                  <StatusBadge status={row.data.enabled ? 'Running' : 'Paused'} />
                </div>
                <p className="text-sm text-muted-foreground">
                  When <MachineValue>{row.data.trigger?.kind}</MachineValue> ({triggerLabel(row.data, labels)})
                  {row.data.trigger?.object_type ? (
                    <>
                      {' '}
                      on <MachineValue>{row.data.trigger.object_type}</MachineValue>
                    </>
                  ) : null}
                  . Assignee <MachineValue>{row.data.assignee_email}</MachineValue>.
                </p>
                {isDateTrigger(row.data) && (
                  <p className="text-sm text-muted-foreground">
                    {row.data.awaiting_morning_run
                      ? 'Waiting for the next morning run. A date trigger does not run the moment it is saved.'
                      : `Last evaluated ${row.data.last_evaluated_at}.`}
                  </p>
                )}
              </Card>
            </li>
          ))}
        </ul>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        <Card className="space-y-3">
          <MicroLabel>evaluate one event</MicroLabel>
          <Field
            label="CRM prospect id"
            id="wf068-event-crm"
            hint="A contact created, a deal updated, or a tracker detection on a call."
          >
            <input
              id="wf068-event-crm"
              className={inputClass}
              value={crmId}
              onChange={(event) => setCrmId(event.target.value)}
            />
          </Field>
          <Button
            onClick={async () => {
              try {
                await followupApi.evaluate({
                  kind: 'crm_entity',
                  operation: 'create',
                  crm_id: crmId,
                })
                onChanged()
              } catch (error) {
                onError(error)
              }
            }}
          >
            Run the rules
          </Button>
        </Card>

        <Card className="space-y-3">
          <MicroLabel>the daily schedule</MicroLabel>
          <p className="text-sm text-muted-foreground">
            {vocabulary?.date_trigger_schedule_quote}
          </p>
          <Field label="Morning to evaluate" id="wf068-morning">
            <input
              id="wf068-morning"
              type="date"
              className={inputClass}
              value={runOn}
              onChange={(event) => setRunOn(event.target.value)}
            />
          </Field>
          <Button
            onClick={async () => {
              try {
                await followupApi.morningRun(runOn)
                onChanged()
              } catch (error) {
                onError(error)
              }
            }}
          >
            Run one morning
          </Button>
        </Card>
      </div>
    </div>
  )
}

function Queue({ ownerEmail, onChanged }) {
  const queue = useAsync(
    () => (ownerEmail ? followupApi.queue(ownerEmail) : Promise.resolve(null)),
    [ownerEmail],
  )
  const instances = useAsync(
    () => (ownerEmail ? followupApi.instances(ownerEmail) : Promise.resolve(null)),
    [ownerEmail],
  )

  if (queue.loading || instances.loading) return <Spinner label="Loading the queue" />
  if (queue.error) return <ErrorNote error={queue.error} onRetry={queue.refetch} />

  const queues = queue.data?.queues || []
  const instanceRows = instances.data?.instances || []

  return (
    <div className="space-y-4">
      {instanceRows.length === 0 ? (
        <EmptyState
          title="Nobody is enrolled yet"
          description="An automation assigns a prospect to a flow, and the assignee then owns that prospect's to-dos."
        />
      ) : (
        <ul className="space-y-2">
          {instanceRows.map((row) => (
            <li key={row.id}>
              <Card className="space-y-2">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <MachineValue className="text-muted-foreground">
                    {row.data.flow_instance_id}
                  </MachineValue>
                  <StatusBadge status={row.view.status} />
                </div>
                <p className="text-sm text-muted-foreground">
                  {row.data.crm_prospect_id} in <MachineValue>{row.data.flow_id}</MachineValue>
                  . Touches delivered{' '}
                  <MachineValue>
                    {row.view.touches_delivered}/{row.view.touches}
                  </MachineValue>
                  .
                </p>
                <div className="flex flex-wrap gap-2">
                  {STATUSES.filter((status) => status !== row.view.status).map((status) => (
                    <Button
                      key={status}
                      onClick={async () => {
                        try {
                          await followupApi.setInstanceStatus(
                            row.data.flow_instance_id,
                            status,
                          )
                          instances.refetch()
                        } catch {
                          instances.refetch()
                        }
                      }}
                    >
                      Make {status}
                    </Button>
                  ))}
                </div>
              </Card>
            </li>
          ))}
        </ul>
      )}

      {queues.map((group) => (
        <Card key={group.queue_id} className="space-y-3">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <MicroLabel>queue</MicroLabel>
            <MachineValue className="text-muted-foreground">{group.flow_reference}</MachineValue>
          </div>
          <ul className="space-y-2">
            {group.todos.map((todo) => (
              <li
                key={todo.todo_id}
                className="flex flex-wrap items-center justify-between gap-2 border-b border-border-subtle pb-2 last:border-0"
              >
                <span className="flex items-center gap-2 text-sm text-foreground">
                  <Badge tone={todo.completed_at ? 'insert' : 'neutral'}>
                    {positionLabel(todo)}
                  </Badge>
                  {todo.subject}
                  {/* Quoted: "the flow reference appears with the activity". It is on
                      the row rather than only in the queue header, because a rep
                      working the queue is looking at the row. */}
                  <MachineValue className="text-muted-foreground">
                    {todo.flow_reference}
                  </MachineValue>
                </span>
                <span className="flex items-center gap-2">
                  <MachineValue className="text-muted-foreground">{todo.due_on}</MachineValue>
                  {!todo.completed_at && (
                    <Button onClick={async () => {
                      try {
                        await followupApi.completeTodo(todo.todo_id)
                        onChanged()
                      } catch {
                        onChanged()
                      }
                    }}>
                      Done
                    </Button>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </Card>
      ))}
    </div>
  )
}

function AssignmentSplit({ response }) {
  if (!response) return null
  const assigned = response.prospectsAssigned || []
  const failed = response.prospectsNotAssigned || []
  return (
    <Card className="space-y-3">
      <MicroLabel>the batch answer</MicroLabel>
      <p className="text-sm text-muted-foreground">
        {assigned.length} assigned and {failed.length} refused, out of {response.requested}{' '}
        requested. A failure is per prospect: one bad id does not fail the batch.
      </p>
      {failed.length > 0 && (
        <ul className="space-y-1">
          {failed.map((entry) => (
            <li key={entry.crmProspectId} className="text-sm text-foreground">
              <MachineValue>{entry.crmProspectId}</MachineValue> -{' '}
              <Badge tone="delete">{entry.errorCode}</Badge>{' '}
              <span className="text-muted-foreground">{assignErrorWord(entry.errorCode)}</span>
            </li>
          ))}
        </ul>
      )}
      <JsonView value={{ prospectsAssigned: assigned }} />
    </Card>
  )
}

function Gates({ vocabulary, onError }) {
  const [prospectIds, setProspectIds] = useState('con-1')
  const [flowId, setFlowId] = useState('f-1')
  const [owner, setOwner] = useState('')
  const [result, setResult] = useState(null)
  const gates = vocabulary?.gates || {}

  return (
    <div className="space-y-4">
      <Card className="space-y-3">
        <MicroLabel>the two researched gates</MicroLabel>
        <GateQuote>{gates.engage_license}</GateQuote>
        <GateQuote>{gates.parent_account}</GateQuote>
        <GateQuote>{gates.personal_flow}</GateQuote>
        <p className="text-sm text-muted-foreground">
          All three are refusals, not warnings. An automation whose assignee has no
          Engage licence will not run, so the page stops it here rather than letting it
          fail silently.
        </p>
      </Card>

      <Card className="space-y-3">
        <MicroLabel>assign a batch</MicroLabel>
        <Field label="CRM prospect ids" id="wf068-batch-ids" hint="Comma separated.">
          <input
            id="wf068-batch-ids"
            className={inputClass}
            value={prospectIds}
            onChange={(event) => setProspectIds(event.target.value)}
          />
        </Field>
        <Field label="Flow id" id="wf068-batch-flow">
          <input
            id="wf068-batch-flow"
            className={inputClass}
            value={flowId}
            onChange={(event) => setFlowId(event.target.value)}
          />
        </Field>
        <Field label="Assignee email" id="wf068-batch-owner">
          <input
            id="wf068-batch-owner"
            className={inputClass}
            value={owner}
            onChange={(event) => setOwner(event.target.value)}
          />
        </Field>
        <Button
          variant="primary"
          onClick={async () => {
            try {
              const response = await followupApi.assign({
                crmProspectsIds: prospectIds
                  .split(',')
                  .map((item) => item.trim())
                  .filter(Boolean),
                flowId,
                flowInstanceOwnerEmail: owner,
              })
              setResult(response)
            } catch (error) {
              onError(error)
            }
          }}
        >
          Assign
        </Button>
        <p className="text-xs text-muted-foreground">
          Up to {vocabulary?.assignment?.max_prospects} prospects in one request. Over that
          is refused, not truncated.
        </p>
      </Card>

      <AssignmentSplit response={result} />
    </div>
  )
}

function Recordings({ vocabulary }) {
  const webhooks = useAsync(() => followupApi.webhooks(), [])
  if (webhooks.loading) return <Spinner label="Loading the Cal webhooks" />
  if (webhooks.error) return <ErrorNote error={webhooks.error} onRetry={webhooks.refetch} />

  const rows = webhooks.data?.webhooks || []
  return (
    <Card className="space-y-3">
      <MicroLabel>cal recordings</MicroLabel>
      <p className="text-sm text-muted-foreground">
        Gong supplies the prep page, the flows and the CRM upload. Cal supplies the
        follow-up webhooks and the recording download links. A link carries a token that
        expires, so it is recorded here and not fetched.
      </p>
      {rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">No Cal webhook recorded yet.</p>
      ) : (
        <ul className="space-y-2">
          {rows.map((row, index) => (
            <li key={`${row.booking_uid}-${index}`} className="text-sm text-foreground">
              <MachineValue>{row.event}</MachineValue> for{' '}
              <MachineValue>{row.booking_uid}</MachineValue>
              {row.transcription ? (
                <>
                  {' '}
                  in <MachineValue>{row.transcription.available.join(', ')}</MachineValue>
                </>
              ) : null}
              {row.recording ? (
                <>
                  {'. '}
                  <MachineValue className="text-muted-foreground">
                    {row.recording.fetched ? 'fetched' : 'recorded, not fetched'}
                  </MachineValue>
                </>
              ) : null}
            </li>
          ))}
        </ul>
      )}
      <p className="text-xs text-muted-foreground">
        Transcription formats served: {vocabulary?.cal?.transcription_formats?.join(', ')}.
      </p>
    </Card>
  )
}

function Derivations() {
  const decisions = useAsync(() => followupApi.decisions(), [])
  if (decisions.loading) return <Spinner label="Loading the derivations" />
  if (decisions.error) return <ErrorNote error={decisions.error} onRetry={decisions.refetch} />

  const rows = decisions.data?.decisions || []
  return (
    <Card className="space-y-3">
      <MicroLabel>what was derived, and what was rejected</MicroLabel>
      <p className="text-sm text-muted-foreground">
        The research states of itself that a flow it does not contain must be derived and
        the derivation recorded, not assumed. Each entry below carries the evidence, the
        reading it rejected and what that reading would have cost.
      </p>
      <ul className="space-y-2">
        {rows.map((entry) => (
          <li key={entry.id} className="border-b border-border-subtle pb-2 last:border-0">
            <p className="text-sm font-medium text-foreground">{entry.topic}</p>
            <p className="text-sm text-muted-foreground">{entry.decision}</p>
            <p className="mt-1 text-xs text-muted-foreground">
              Rejected: {entry.rejected}
            </p>
          </li>
        ))}
      </ul>
    </Card>
  )
}

function MeetingFollowupPage() {
  const [chosenRoomId, setChosenRoomId] = useState('')
  const [notice, setNotice] = useState(null)
  const [nonce, setNonce] = useState(0)
  const [ownerEmail, setOwnerEmail] = useState('')

  const rooms = useAsync(() => listRooms(), [])

  // The room is *derived*, not stored. A page that seeds it with an effect calls
  // setState on first paint, which React calls a cascading render and eslint
  // rightly warns about. Deriving gives the same answer with no second pass: an
  // empty choice means "the first one the server offered", and the moment a person
  // picks something the choice stops being empty and stops moving.
  //
  // It has to be declared before every `useAsync` that reads it, because a
  // dependency array is evaluated when the hook runs, not when the render ends.
  const roomOptions = rooms.data?.records || []
  const roomId = chosenRoomId || roomOptions[0]?.id || ''

  const vocabulary = useAsync(() => followupApi.vocabulary(), [nonce])
  const summary = useAsync(
    () => (roomId ? followupApi.summary(roomId) : Promise.resolve(null)),
    [roomId, nonce],
  )
  const users = useAsync(() => followupApi.users(), [nonce])
  const prepPages = useAsync(() => followupApi.prepPages(), [nonce])
  const trackers = useAsync(() => followupApi.trackers(), [nonce])

  const licensed =
    (users.data?.users || []).find((user) => user.data?.email === ownerEmail)?.data?.email || ''

  const changed = () => setNonce((value) => value + 1)
  const report = (error) => setNotice(error?.message || String(error))

  if (rooms.loading || vocabulary.loading) return <Spinner label="Loading the meeting follow-up" />
  if (rooms.error) return <ErrorNote error={rooms.error} onRetry={rooms.refetch} />
  if (vocabulary.error) return <ErrorNote error={vocabulary.error} onRetry={vocabulary.refetch} />

  const board = summary.data
  const statuses = board?.instances_by_status || {}
  const pages = prepPages.data?.pages || []

  return (
    <div className="space-y-6">
      <header className="space-y-2">
        <h1 className="text-2xl font-semibold text-foreground">
          Prepare for the meeting, then run the follow-up
        </h1>
        <p className="text-sm text-muted-foreground">
          The prep page before the call. The sequence after it, owned by one assignee and
          carrying its own to-dos.
        </p>
      </header>

      {notice && (
        <div
          role="alert"
          className="flex flex-wrap items-center justify-between gap-3 rounded-sm border border-destructive/40 bg-destructive/10 p-4"
        >
          <p className="text-sm text-destructive">{notice}</p>
          <Button onClick={() => setNotice(null)}>Dismiss</Button>
        </div>
      )}

      <Field label="Room" id="wf068-room">
        <select
          id="wf068-room"
          className={inputClass}
          value={roomId}
          onChange={(event) => setChosenRoomId(event.target.value)}
        >
          {roomOptions.map((room) => (
            <option key={room.id} value={room.id}>
              {room.data?.name || room.id}
            </option>
          ))}
        </select>
      </Field>

      {summary.loading ? (
        <Spinner label="Loading the counts" />
      ) : summary.error ? (
        <ErrorNote error={summary.error} onRetry={summary.refetch} />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard
            label="Prep pages"
            value={board?.prep_pages ?? 0}
            hint={`${board?.dry_runs ?? 0} rehearsals recorded`}
          />
          <StatCard
            label="Automations"
            value={board?.automations ?? 0}
            hint={`${board?.automations_awaiting_morning_run ?? 0} awaiting a morning run`}
          />
          <StatCard
            label="Instances"
            value={board?.instances ?? 0}
            hint={STATUSES.map((status) => `${status} ${statuses[status] ?? 0}`).join(', ')}
          />
          <StatCard
            label="To-dos"
            value={board?.todos ?? 0}
            hint={`${board?.todos_open ?? 0} still open`}
          />
        </div>
      )}

      <Section
        title="Before the call"
        note={`Push reminder: ${vocabulary.data?.prep?.push_notification_minutes} minutes before`}
      >
        <MeetingPrep meetingId={pages[0]?.meeting_id} onError={report} />
      </Section>

      <Section
        title="After the call"
        note="A When, an If, and the flow to enrol into"
      >
        <Automations
          vocabulary={vocabulary.data}
          onError={report}
          onChanged={changed}
        />
      </Section>

      <Section title="Assign a batch" note="One request, one split answer">
        <Gates vocabulary={vocabulary.data} onError={report} />
      </Section>

      <Section title="The queue">
        <Field
          label="Assignee"
          id="wf068-owner"
          hint="Only a seller with an Engage licence can be an assignee."
        >
          <select
            id="wf068-owner"
            className={inputClass}
            value={ownerEmail}
            onChange={(event) => setOwnerEmail(event.target.value)}
          >
            <option value="">Choose a seller</option>
            {(users.data?.users || []).map((row) => (
              <option key={row.id} value={row.data.email}>
                {row.data.email}
                {row.data.engage_licensed ? '' : ' (no Engage licence)'}
              </option>
            ))}
          </select>
        </Field>
        {licensed ? (
          <Queue ownerEmail={licensed} onChanged={changed} />
        ) : (
          <EmptyState
            title="Choose an assignee"
            description="The assignee owns the flow to-dos and is the sender for any emails in the flow, so the queue belongs to one seller."
          />
        )}
      </Section>

      <Section title="Recordings and trackers">
        <div className="grid gap-4 sm:grid-cols-2">
          <Recordings vocabulary={vocabulary.data} />
          <Card className="space-y-3">
            <MicroLabel>ai tracker detections</MicroLabel>
            {(trackers.data?.detections || []).length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No tracker detection recorded yet. A concept detected on a call is one
                of the five researched triggers.
              </p>
            ) : (
              <ul className="space-y-1">
                {(trackers.data?.detections || []).map((row, index) => (
                  <li key={`${row.tracker_name}-${index}`} className="text-sm text-foreground">
                    <MachineValue>{row.tracker_name}</MachineValue>
                    {row.concept ? ` - ${row.concept}` : ''}
                    {row.crm_id ? (
                      <>
                        {' '}
                        on <MachineValue>{row.crm_id}</MachineValue>
                      </>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      </Section>

      <Section title="Derivations">
        <Derivations />
      </Section>
    </div>
  )
}

export default MeetingFollowupPage