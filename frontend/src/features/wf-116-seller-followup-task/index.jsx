/**
 * WF-116: create an automated seller follow-up task with a reminder.
 *
 * The page reads its closed vocabularies, its signal rules and its judgements
 * from the server's `/vocabulary` and `/inferences` routes rather than repeating
 * them, so a panel cannot drift from the rules the engine enforces.
 *
 * One thing the page deliberately does not do: it never edits a task's lifecycle
 * outside the workflow's own actions - Complete, Fire reminder, Attach, Detach,
 * Pin, Delete, Restore - so the three documented triggers stay the only way a
 * task moves.
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
  SIGNALS,
  attachmentPhrase,
  completeBlockedReason,
  duePhrase,
  formatDateTime,
  fromLocalInput,
  isReminderDue,
  listRooms,
  priorityLabel,
  reminderBlockedReason,
  statusLabel,
  taskApi,
  typeLabel,
} from './api'
import { ContactField, Fact, InlineNote, PriorityBadge, Section, StatusBadge, TaskRow, TriggerList } from './primitives'

/** A check-in-box glyph. Passed by path so `components/ui.jsx` is not edited. */
export const TASK_GLYPH =
  'M9 11l3 3L22 4M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11'

function useAction(onDone) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [note, setNote] = useState(null)

  async function run(action, success) {
    setBusy(true)
    setError(null)
    setNote(null)
    try {
      await action()
      setNote(success)
      onDone()
    } catch (problem) {
      setError(problem)
    } finally {
      setBusy(false)
    }
  }

  return { busy, error, note, run, setNote }
}

function CreateForm({ vocabulary, contacts, onCreated }) {
  const [subject, setSubject] = useState('')
  const [body, setBody] = useState('')
  const [due, setDue] = useState('')
  const [reminder, setReminder] = useState('')
  const [priority, setPriority] = useState('MEDIUM')
  const [taskType, setTaskType] = useState('TODO')
  const [assignee, setAssignee] = useState('')
  const [notify, setNotify] = useState(false)
  const [contactId, setContactId] = useState('')
  const [signal, setSignal] = useState('')
  const { busy, error, note, run } = useAction(onCreated)

  function submit(event) {
    event.preventDefault()
    const payload = {
      subject,
      body,
      due_at: fromLocalInput(due),
      reminder_at: fromLocalInput(reminder),
      hs_task_priority: priority,
      hs_task_type: taskType,
      assignee: assignee || undefined,
      notify,
      contact_id: contactId || undefined,
    }
    run(() => taskApi.create(payload), 'The follow-up task was created.')
  }

  return (
    <Section title="Create a follow-up task">
      <form className="grid gap-4 sm:grid-cols-2" onSubmit={submit}>
        <Field label="Subject" id="wf116-subject" hint="The task title the assignee reads.">
          <input
            id="wf116-subject"
            className={inputClass}
            value={subject}
            onChange={(event) => setSubject(event.target.value)}
          />
        </Field>
        <Field label="Assignee" id="wf116-assignee" hint="Optional. The internal user who owns the task.">
          <input
            id="wf116-assignee"
            className={inputClass}
            value={assignee}
            onChange={(event) => setAssignee(event.target.value)}
          />
        </Field>
        <div className="sm:col-span-2">
          <Field label="Body" id="wf116-body" hint="Optional notes for the assignee.">
            <textarea
              id="wf116-body"
              className={`${inputClass} min-h-11 py-2`}
              rows={2}
              value={body}
              onChange={(event) => setBody(event.target.value)}
            />
          </Field>
        </div>
        <Field label="Due time" id="wf116-due" hint="Required. The task's due date and time.">
          <input
            id="wf116-due"
            type="datetime-local"
            className={inputClass}
            value={due}
            onChange={(event) => setDue(event.target.value)}
          />
        </Field>
        <Field
          label="Reminder time"
          id="wf116-reminder"
          hint="Optional. It must not fall after the due time."
        >
          <input
            id="wf116-reminder"
            type="datetime-local"
            className={inputClass}
            value={reminder}
            onChange={(event) => setReminder(event.target.value)}
          />
        </Field>
        <Field label="Priority" id="wf116-priority" hint="LOW, MEDIUM or HIGH.">
          <select
            id="wf116-priority"
            className={inputClass}
            value={priority}
            onChange={(event) => setPriority(event.target.value)}
          >
            {(vocabulary.priorities || []).map((option) => (
              <option key={option} value={option}>
                {priorityLabel(option)}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Type" id="wf116-type" hint="EMAIL, CALL or TODO.">
          <select
            id="wf116-type"
            className={inputClass}
            value={taskType}
            onChange={(event) => setTaskType(event.target.value)}
          >
            {(vocabulary.task_types || []).map((option) => (
              <option key={option} value={option}>
                {typeLabel(option)}
              </option>
            ))}
          </select>
        </Field>
        <ContactField
          id="wf116-contact"
          label="Attach to a contact"
          hint="Leave empty to create a standalone internal task."
          contacts={contacts}
          value={contactId}
          onChange={(event) => setContactId(event.target.value)}
        />
        <div className="flex flex-col gap-1.5">
          <span className="text-[13px] font-medium text-foreground">Send an internal notification</span>
          <label
            htmlFor="wf116-notify"
            className="flex min-h-11 items-center gap-3 rounded-sm border border-border-subtle bg-surface px-3 text-sm text-foreground"
          >
            <input
              id="wf116-notify"
              type="checkbox"
              checked={notify}
              onChange={(event) => setNotify(event.target.checked)}
            />
            Notify the assignee when the task is added
          </label>
          <span className="text-xs text-muted-foreground">It needs an assignee.</span>
        </div>
        <Field
          label="Create from a signal"
          id="wf116-signal"
          hint="Optional. The workflow creates a deterministic task from one signal."
        >
          <select
            id="wf116-signal"
            className={inputClass}
            value={signal}
            onChange={(event) => setSignal(event.target.value)}
          >
            <option value="">No signal</option>
            {SIGNALS.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </Field>
        <div className="flex items-end gap-3 sm:col-span-2">
          <Button variant="primary" icon="plus" type="submit" disabled={busy}>
            Create task
          </Button>
          {signal && (
            <Button
              icon="refresh"
              disabled={busy}
              onClick={() =>
                run(
                  () => taskApi.fromSignal(signal, { assignee: assignee || undefined, contact_id: contactId || undefined }),
                  `A task was created from the ${signal} signal.`,
                )
              }
            >
              Create from signal
            </Button>
          )}
        </div>
      </form>
      {note && <InlineNote tone="success">{note}</InlineNote>}
      {error && <ErrorNote error={error} />}
    </Section>
  )
}

function TaskDetail({ task, contacts, onChanged }) {
  const [contactId, setContactId] = useState('')
  const [engagementId, setEngagementId] = useState('')
  const { busy, error, note, run } = useAction(onChanged)

  const completeBlocked = completeBlockedReason(task)
  const reminderBlocked = reminderBlockedReason(task)

  return (
    <Section
      title={task.subject || task.id}
      action={
        <span className="flex items-center gap-2">
          <StatusBadge status={task.status} />
          <PriorityBadge priority={task.priority} />
        </span>
      }
    >
      <p className="font-mono text-xs text-muted-foreground">{task.id}</p>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Fact label="Due" hint={duePhrase(task)}>
          {formatDateTime(task.due_at_ms)}
        </Fact>
        <Fact label="Reminder" hint={task.reminder_fired_at_ms ? 'Already fired' : 'Not yet fired'}>
          {formatDateTime(task.reminder_at_ms)}
        </Fact>
        <Fact label="Type" hint={task.signal ? `From signal ${task.signal}` : 'Set by hand'}>
          {typeLabel(task.task_type)}
        </Fact>
        <Fact label="Assignee" hint="The internal user who owns the task">
          {task.assignee || 'Unassigned'}
        </Fact>
      </div>

      {task.body && <p className="text-sm text-muted-foreground">{task.body}</p>}
      <InlineNote tone={task.attached ? 'info' : 'warning'}>{attachmentPhrase(task)}</InlineNote>
      {isReminderDue(task) && <InlineNote tone="warning">The reminder time has been reached.</InlineNote>}

      <div>
        <p className="mb-2 text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
          Lifecycle triggers
        </p>
        <TriggerList events={task.events} />
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <Button
          variant="primary"
          icon="restore"
          disabled={busy || Boolean(completeBlocked)}
          onClick={() => run(() => taskApi.complete(task.id), 'The task was completed.')}
        >
          Complete
        </Button>
        <Button
          icon="refresh"
          disabled={busy || Boolean(reminderBlocked)}
          onClick={() => run(() => taskApi.fireReminder(task.id), 'The reminder fired.')}
        >
          Fire reminder
        </Button>
        {task.attached ? (
          <Button
            icon="close"
            disabled={busy}
            onClick={() => run(() => taskApi.detach(task.id), 'The task is now standalone.')}
          >
            Detach
          </Button>
        ) : (
          <Button
            icon="database"
            disabled={busy || !contactId}
            onClick={() => run(() => taskApi.attach(task.id, contactId), 'The task is now attached.')}
          >
            Attach
          </Button>
        )}
        <Button
          variant="danger"
          icon="trash"
          disabled={busy}
          onClick={() => run(() => taskApi.remove(task.id), 'The task moved to the recycling bin.')}
        >
          Delete
        </Button>
      </div>

      {!task.attached && (
        <ContactField
          id="wf116-attach-contact"
          label="Attach to a contact"
          hint="Pick a contact, then press Attach."
          contacts={contacts}
          value={contactId}
          onChange={(event) => setContactId(event.target.value)}
        />
      )}

      <div className="flex flex-wrap items-end gap-3">
        <Field label="Pin to the timeline" id="wf116-pin" hint="Records the engagement id on the task row.">
          <input
            id="wf116-pin"
            className={inputClass}
            value={engagementId}
            onChange={(event) => setEngagementId(event.target.value)}
          />
        </Field>
        <Button
          icon="audit"
          disabled={busy || !engagementId}
          onClick={() => run(() => taskApi.pin(task.id, engagementId), 'The pin intent was recorded.')}
        >
          Pin
        </Button>
      </div>

      {completeBlocked && <InlineNote tone="info">{completeBlocked}</InlineNote>}
      {reminderBlocked && task.status !== 'COMPLETED' && (
        <InlineNote tone="warning">{reminderBlocked}</InlineNote>
      )}
      {note && <InlineNote tone="success">{note}</InlineNote>}
      {error && <ErrorNote error={error} />}
    </Section>
  )
}

function BinSection({ entries, onChanged }) {
  const { busy, error, note, run } = useAction(onChanged)

  return (
    <Section
      title="Recycling bin"
      action={
        <Button
          icon="trash"
          disabled={busy || entries.length === 0}
          onClick={() => run(() => taskApi.purgeBin(), 'Expired rows were purged.')}
        >
          Purge expired
        </Button>
      }
    >
      {entries.length === 0 ? (
        <p className="text-sm text-muted-foreground">The bin is empty.</p>
      ) : (
        <ul className="flex flex-col gap-3">
          {entries.map((task) => (
            <li key={task.id} className="flex flex-wrap items-center justify-between gap-3">
              <span className="text-sm text-foreground">{task.subject || task.id}</span>
              <span className="font-mono text-xs text-muted-foreground">
                {formatDateTime(task.due_at_ms)}
              </span>
              <Button
                icon="restore"
                disabled={busy}
                onClick={() => run(() => taskApi.restore(task.id), 'The task was restored.')}
              >
                Restore
              </Button>
            </li>
          ))}
        </ul>
      )}
      {note && <InlineNote tone="success">{note}</InlineNote>}
      {error && <ErrorNote error={error} />}
    </Section>
  )
}

function FollowUpTasksPage() {
  const [roomId, setRoomId] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [selected, setSelected] = useState('')
  const [nonce, setNonce] = useState(0)

  const vocabulary = useAsync(() => taskApi.vocabulary(), [])
  const rooms = useAsync(() => listRooms(), [])
  const summary = useAsync(() => taskApi.summary(roomId), [roomId, nonce])
  const tasks = useAsync(
    () => taskApi.tasks({ room_id: roomId, status: statusFilter }),
    [roomId, statusFilter, nonce],
  )
  const contacts = useAsync(() => taskApi.contacts(roomId), [roomId, nonce])
  const bin = useAsync(() => taskApi.bin(roomId), [roomId, nonce])
  const detail = useAsync(
    () => (selected ? taskApi.task(selected) : Promise.resolve(null)),
    [selected, nonce],
  )

  function refresh() {
    setNonce((value) => value + 1)
  }

  if (vocabulary.loading || rooms.loading) return <Spinner label="Loading follow-up tasks" />
  if (vocabulary.error) return <ErrorNote error={vocabulary.error} onRetry={vocabulary.refetch} />
  if (tasks.error) return <ErrorNote error={tasks.error} onRetry={tasks.refetch} />

  const counts = summary.data || {}
  const roomOptions = rooms.data?.records || []
  const entries = tasks.data?.entries || []
  const contactOptions = contacts.data?.entries || []
  const binEntries = bin.data?.entries || []

  return (
    <div className="space-y-6">
      <header className="flex items-start gap-3">
        <span className="rounded-sm bg-accent-soft p-2 text-accent">
          <Icon path={TASK_GLYPH} size={20} />
        </span>
        <div>
          <h1 className="font-display text-2xl font-semibold text-foreground">Follow-up tasks</h1>
          <p className="text-sm text-muted-foreground">
            A task carries a due time and a reminder. It attaches to a contact or stands alone as an
            internal task. Its lifecycle fires the Task Added, Task Reminder and Task Completed
            triggers.
          </p>
        </div>
      </header>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Open" value={counts.open ?? 0} icon="plus" hint="Not started" />
        <StatCard label="Completed" value={counts.completed ?? 0} icon="restore" hint="Trigger fired" />
        <StatCard
          label="Reminders due"
          value={counts.reminders_due ?? 0}
          icon="refresh"
          hint="Reached and not fired"
        />
        <StatCard label="Binned" value={counts.binned ?? 0} icon="trash" hint="In the recycling bin" />
      </div>

      <Card className="flex flex-wrap items-end gap-4">
        <Field label="Room" id="wf116-room" hint="Tasks belong to a room when one is set.">
          <select
            id="wf116-room"
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
        <Field label="Status" id="wf116-status" hint="Not started or completed.">
          <select
            id="wf116-status"
            className={inputClass}
            value={statusFilter}
            onChange={(event) => setStatusFilter(event.target.value)}
          >
            <option value="">All statuses</option>
            {(vocabulary.data?.statuses || []).map((option) => (
              <option key={option} value={option}>
                {statusLabel(option)}
              </option>
            ))}
          </select>
        </Field>
      </Card>

      <CreateForm vocabulary={vocabulary.data || {}} contacts={contactOptions} onCreated={refresh} />

      <Section title="Task list" action={<span className="text-xs text-muted-foreground">{entries.length} shown</span>}>
        {entries.length === 0 ? (
          <EmptyState
            title="No tasks"
            description="Create a follow-up task above, or pick a different room or status."
          />
        ) : (
          <ul className="flex flex-col gap-2">
            {entries.map((task) => (
              <TaskRow key={task.id} task={task} selected={task.id === selected} onSelect={setSelected} />
            ))}
          </ul>
        )}
      </Section>

      {selected && detail.loading && <Spinner label="Reading the task" />}
      {selected && detail.error && <ErrorNote error={detail.error} onRetry={detail.refetch} />}
      {detail.data && <TaskDetail task={detail.data} contacts={contactOptions} onChanged={refresh} />}

      <BinSection entries={binEntries} onChanged={refresh} />
    </div>
  )
}

export default {
  id: 'wf-116-seller-followup-task',
  label: 'Follow-up tasks',
  icon: 'audit',
  iconPath: TASK_GLYPH,
  order: 1016,
  Component: FollowUpTasksPage,
}
