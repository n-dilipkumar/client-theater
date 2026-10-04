import { useState } from 'react'

import { conflictText, listRooms, mapApi, SIDE_WORDS } from './api'
import { DueLabel, Notice, PhaseBadge, StatusBadge, Toggle } from './primitives'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorNote,
  Field,
  StatCard,
  Spinner,
  inputClass,
  useAsync,
} from '@/components/ui'

/**
 * WF-124: the seller's board for mutual action plans.
 *
 * What the page is shaped around
 * ------------------------------
 *
 * A mutual action plan is a shared roadmap, so the page's first job is to show the
 * tasks in the order the two sides agreed and to say who owns each one. A task's state
 * carries a word as well as a colour, because the four states are easy to confuse at a
 * glance and `blocked` in particular looks like ordinary work unless it says otherwise.
 *
 * Three things get deliberate treatment rather than a generic list row:
 *
 *   1. **The blocked state is shown as derived.** `blocked` comes from the graph, not
 *      from the stored status, so the page says a task waits on another. A seller who
 *      set it by hand and watched it revert would conclude the board is broken.
 *   2. **The internal-only flag is a switch, and the buyer view is a separate call.**
 *      The buyer view goes to `/shared`, where the server omits internal tasks from
 *      the response. The switch on this page is a preview of that view, so what a
 *      seller sees when it is on is exactly what the client receives.
 *   3. **Closing a plan is presented as reversible-looking but is not.** The button
 *      says what happens: the agreed tasks are carried into a new implementation plan
 *      and the selling plan stays as the record. Nothing is deleted, and the page says
 *      so before the click rather than after it.
 *
 * Loading, error and empty states are all present, as the design system requires.
 */

function PlanCard({ plan, audience, onCloseWon, busy }) {
  const tasks = plan.tasks || []
  const escalations = new Map((plan.escalations || []).map((row) => [row.task_id, row]))
  const dangling = plan.dangling_dependencies || []

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="flex flex-wrap items-center gap-2 text-base font-semibold text-foreground">
            {plan.name}
            <PhaseBadge phase={plan.phase} />
          </h3>
          <p className="mt-1 font-mono text-xs text-muted-foreground">
            {plan.done_count} of {plan.task_count} done
            {plan.internal_count > 0 && audience === 'seller' ? (
              <> &middot; {plan.internal_count} internal only</>
            ) : null}
          </p>
        </div>
        {plan.phase === 'selling' && (
          <Button variant="secondary" disabled={busy} onClick={() => onCloseWon(plan)}>
            Close won
          </Button>
        )}
      </div>

      {dangling.length > 0 && (
        <div className="mt-4">
          <Notice tone="warning" title="A dependency points at a task that is not here">
            {dangling.length} task(s) wait on a task id this plan does not hold. The server reads
            those edges as satisfied, so check the names before relying on this plan.
          </Notice>
        </div>
      )}

      {tasks.length === 0 ? (
        <p className="mt-4 text-sm text-muted-foreground">
          This plan has no tasks yet. Add one to start the roadmap.
        </p>
      ) : (
        <ul className="mt-4 divide-y divide-border-subtle">
          {tasks.map((task) => (
            <li key={task.id} className="flex flex-wrap items-start justify-between gap-3 py-3">
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium text-foreground">{task.title}</p>
                <p className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
                  <span className="font-mono">{task.owner}</span>
                  <span aria-hidden="true">&middot;</span>
                  <span>{SIDE_WORDS[task.owner_side] || task.owner_side}</span>
                  {audience === 'seller' && task.visibility === 'internal' && (
                    <>
                      <span aria-hidden="true">&middot;</span>
                      <span className="font-medium text-warning">internal only</span>
                    </>
                  )}
                  {task.due_date && (
                    <>
                      <span aria-hidden="true">&middot;</span>
                      <span className="font-mono">due {task.due_date}</span>
                    </>
                  )}
                </p>
                {task.status === 'blocked' && task.depends_on?.length > 0 && (
                  <p className="mt-1 text-xs text-muted-foreground">
                    Waiting on {task.depends_on.length} other task(s). The server sets this, so it
                    clears when the task it waits on is done.
                  </p>
                )}
              </div>
              <div className="flex shrink-0 flex-col items-end gap-1">
                <StatusBadge status={task.status} />
                <DueLabel escalation={escalations.get(task.id)} />
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

function AddTaskForm({ planId, onAdded }) {
  const [title, setTitle] = useState('')
  const [owner, setOwner] = useState('')
  const [side, setSide] = useState('seller')
  const [dueDate, setDueDate] = useState('')
  const [internal, setInternal] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [fieldErrors, setFieldErrors] = useState({})

  async function submit(event) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    setFieldErrors({})
    try {
      await mapApi.addTask(planId, {
        title,
        owner,
        owner_side: side,
        due_date: dueDate || null,
        visibility: internal ? 'internal' : 'external',
      })
      setTitle('')
      setOwner('')
      setDueDate('')
      setInternal(false)
      await onAdded()
    } catch (caught) {
      setError(caught)
      // The field-keyed map puts each message beside the input that caused it,
      // which is the whole reason the write calls read the body themselves.
      setFieldErrors(caught.errors || {})
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4" noValidate>
      {error && !Object.keys(fieldErrors).length && (
        <Notice tone="destructive" title="The task was not added">
          {String(error.message || error)}
        </Notice>
      )}

      <Field label="Task" id="wf124-task-title" hint="What has to happen.">
        <input
          id="wf124-task-title"
          className={inputClass}
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          placeholder="Security review"
        />
        {fieldErrors.title && (
          <p className="mt-1 text-xs text-destructive">{fieldErrors.title}</p>
        )}
      </Field>

      <Field label="Owner" id="wf124-task-owner" hint="The named person who does it.">
        <input
          id="wf124-task-owner"
          className={inputClass}
          value={owner}
          onChange={(event) => setOwner(event.target.value)}
          placeholder="Buyer IT"
        />
        {fieldErrors.owner && <p className="mt-1 text-xs text-destructive">{fieldErrors.owner}</p>}
      </Field>

      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Side" id="wf124-task-side">
          <select
            id="wf124-task-side"
            className={inputClass}
            value={side}
            onChange={(event) => setSide(event.target.value)}
          >
            <option value="seller">Seller</option>
            <option value="buyer">Buyer</option>
          </select>
        </Field>
        <Field label="Due date" id="wf124-task-due" hint="Optional. A date, not a time.">
          <input
            id="wf124-task-due"
            type="date"
            className={inputClass}
            value={dueDate}
            onChange={(event) => setDueDate(event.target.value)}
          />
        </Field>
      </div>

      <Toggle
        id="wf124-task-internal"
        label="Internal only"
        hint="The client never receives this task. It stays on the seller's view only."
        checked={internal}
        onChange={setInternal}
      />

      <Button variant="primary" type="submit" disabled={busy}>
        {busy ? 'Adding' : 'Add task'}
      </Button>
    </form>
  )
}

export default function MutualActionPlan() {
  const [chosenRoom, setChosenRoom] = useState('')
  const [buyerView, setBuyerView] = useState(false)
  const [busy, setBusy] = useState(false)
  const [conflict, setConflict] = useState(null)
  const [notice, setNotice] = useState(null)
  const [adding, setAdding] = useState(false)

  const rooms = useAsync(() => listRooms(), [])
  const records = rooms.data?.records || []

  // Derived, not set from an effect. Writing the room from an effect needs a second
  // render to take effect and trips `react-hooks/set-state-in-effect`; deriving it
  // costs nothing and leaves no frame where the board loads the wrong room.
  const roomId = chosenRoom || records[0]?.id || ''

  // The buyer's view is a different *request*, not a different filter. Each plan is
  // re-read from `/shared`, where the server omits internal-only tasks from the
  // response, so what the seller sees with the switch on is exactly what the client
  // receives. Filtering the seller's response on the page would look identical and
  // would not be the guarantee the research asks for.
  const board = useAsync(
    async () => {
      if (!roomId) return { summary: null, plans: [] }
      const summary = await mapApi.summary(roomId)
      const listed = await mapApi.roomPlans(roomId)
      const plans = listed.plans || []
      if (!buyerView) return { summary, plans }

      const shared = await Promise.all(
        plans.map((plan) => mapApi.sharedPlan(plan.id, 'buyer')),
      )
      return { summary, plans: shared }
    },
    [roomId, buyerView],
  )

  async function closeWon(plan) {
    setBusy(true)
    setConflict(null)
    setNotice(null)
    try {
      const outcome = await mapApi.closeWon(plan.id)
      setNotice(
        `Closed won. ${outcome.carried_tasks} agreed task(s) were carried into the implementation plan, and this plan stays as the record.`,
      )
      await board.refetch()
    } catch (caught) {
      setConflict(conflictText(caught.reason) || String(caught.message || caught))
    } finally {
      setBusy(false)
    }
  }

  if (rooms.loading) return <Spinner label="Loading rooms" />
  if (rooms.error) return <ErrorNote error={rooms.error} onRetry={rooms.refetch} />

  if (!records.length) {
    return (
      <EmptyState
        title="No rooms yet"
        description="A mutual action plan belongs to a room. Seed the demo data to see this page with content in it."
      />
    )
  }

  if (board.loading) return <Spinner label="Loading action plans" />
  if (board.error) return <ErrorNote error={board.error} onRetry={board.refetch} />

  const summary = board.data?.summary || {}
  const plans = board.data?.plans || []
  const audience = buyerView ? 'buyer' : 'seller'

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-display text-2xl font-semibold text-foreground">
            Mutual action plan
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            A shared roadmap with named owners on both sides, tracked against the room.
          </p>
        </div>
        {records.length > 1 && (
          <div className="w-full sm:w-64">
            <Field label="Room" id="wf124-room">
              <select
                id="wf124-room"
                className={inputClass}
                value={roomId}
                onChange={(event) => setChosenRoom(event.target.value)}
              >
                {records.map((room) => (
                  <option key={room.id} value={room.id}>
                    {room.data?.name || room.id}
                  </option>
                ))}
              </select>
            </Field>
          </div>
        )}
      </header>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Plans" value={summary.plans ?? 0} icon="rooms" />
        <StatCard
          label="Tasks"
          value={summary.tasks ?? 0}
          hint={`${summary.done ?? 0} done`}
          icon="audit"
        />
        <StatCard
          label="Blocked"
          value={summary.blocked ?? 0}
          hint="Waiting on another task"
          icon="schema"
        />
        <StatCard
          label="Overdue"
          value={summary.overdue ?? 0}
          hint={`${summary.due_soon ?? 0} due soon`}
          icon="search"
        />
      </div>

      {conflict && (
        <Notice tone="destructive" title="The plan refused that change">
          {conflict}
        </Notice>
      )}
      {notice && (
        <Notice tone="success" title="Plan closed">
          {notice}
        </Notice>
      )}

      <Card>
        <Toggle
          id="wf124-buyer-view"
          label="Show the buyer's view"
          hint="Loads the plan the way the client receives it. Internal-only tasks are absent from that response, not hidden by this page."
          checked={buyerView}
          onChange={setBuyerView}
        />
      </Card>

      {plans.length === 0 ? (
        <EmptyState
          title="No action plan in this room yet"
          description="A plan holds the tasks both sides agreed, with an owner and a date on each. Seed the demo data, or create one from a template."
        />
      ) : (
        <div className="space-y-4">
          {plans.map((plan) => (
            <PlanCard
              key={plan.id}
              plan={plan}
              audience={audience}
              busy={busy}
              onCloseWon={closeWon}
            />
          ))}
        </div>
      )}

      {plans[0] && !buyerView && (
        <Card>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="min-w-0">
              <h2 className="text-lg font-semibold text-foreground">Add a task</h2>
              <p className="mt-0.5 text-sm text-muted-foreground">
                It goes on {plans[0].name}. Both sides see it unless it is marked internal.
              </p>
            </div>
            <Button variant="secondary" onClick={() => setAdding((open) => !open)}>
              {adding ? 'Close the form' : 'Add a task'}
            </Button>
          </div>
          {adding && (
            <div className="mt-4">
              <AddTaskForm planId={plans[0].id} onAdded={board.refetch} />
            </div>
          )}
        </Card>
      )}

      <Card>
        <h2 className="text-lg font-semibold text-foreground">How this plan behaves</h2>
        <ul className="mt-3 space-y-2 text-sm text-muted-foreground">
          <li>
            <Badge>blocked</Badge> is set by the server from the graph. A task waits while any task it
            depends on is not done, and it clears by itself when that task closes.
          </li>
          <li>
            <Badge>internal only</Badge> is per task, not per plan, so one hidden task does not take
            the shared roadmap away from the client.
          </li>
          <li>A task due today reads as due soon rather than late, so the board is not red every morning.</li>
          <li>Closing a plan carries its tasks into a new implementation plan. Nothing is deleted.</li>
        </ul>
      </Card>
    </div>
  )
}
