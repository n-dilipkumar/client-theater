import { useState } from 'react'

import { Button, Card, EmptyState, ErrorNote, Field, Spinner, StatCard, inputClass, useAsync } from '@/components/ui'

import { LinkBadge, Machine, MicroLabel, StateBadge } from './primitives'
import { bookingApi, listRooms, refusalText } from './api'

/**
 * WF-136's page: the room's next steps, in one place.
 *
 * The seller reads what is waiting, proposes the next step, and watches the
 * meeting land; the buyer's half of the same workflow is the slot picker and the
 * one-click accept, which is why they share a page rather than living on two
 * routes. Every state carries a word beside its colour, every control is at
 * least 44px, and the loading, empty and error states are all real.
 *
 * Nothing here re-derives a rule the server owns: the proposal states, the slots
 * and the refusal sentences all come from the API, so a change to the workflow
 * cannot leave the page disagreeing with it.
 */

function formatInstant(value) {
  if (!value) return 'not set'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return String(value)
  return parsed.toLocaleString(undefined, {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function RoomPicker({ rooms, roomId, onChange }) {
  return (
    <Field label="Room" id="wf136-room">
      <select
        id="wf136-room"
        className={inputClass}
        value={roomId}
        onChange={(event) => onChange(event.target.value)}
      >
        {rooms.map((room) => (
          <option key={room.id} value={room.id}>
            {room.data?.name || room.id}
          </option>
        ))}
      </select>
    </Field>
  )
}

function ProposeForm({ roomId, tasks, onCreated }) {
  const [summary, setSummary] = useState('')
  const [taskId, setTaskId] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  async function submit(event) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await bookingApi.createProposal(roomId, {
        summary: summary.trim(),
        task_id: taskId || undefined,
      })
      setSummary('')
      setTaskId('')
      onCreated()
    } catch (failure) {
      const fieldMessage = failure?.errors?.summary || failure?.errors?.duration_minutes
      setError({ message: fieldMessage || refusalText(failure?.reason), fields: failure?.errors })
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <h2 className="text-lg font-semibold text-foreground">Propose the next step</h2>
      <p className="mt-1 text-sm text-muted-foreground">
        The buyer picks a time from the seller's live availability, inside the room.
      </p>
      <form className="mt-4 flex flex-col gap-4" onSubmit={submit}>
        <Field label="Next step" id="wf136-summary" hint="What the two sides are meeting about.">
          <input
            id="wf136-summary"
            className={inputClass}
            value={summary}
            onChange={(event) => setSummary(event.target.value)}
            placeholder="Book the technical deep dive"
          />
        </Field>
        <Field label="Complete a task from the action plan" id="wf136-task" hint="Optional.">
          <select
            id="wf136-task"
            className={inputClass}
            value={taskId}
            onChange={(event) => setTaskId(event.target.value)}
          >
            <option value="">No task</option>
            {tasks.map((task) => (
              <option key={task.id} value={task.id}>
                {task.title}
              </option>
            ))}
          </select>
        </Field>
        {error && (
          <p role="alert" className="text-sm font-medium text-destructive">
            {error.message}
          </p>
        )}
        <div>
          <Button type="submit" variant="primary" disabled={busy || !summary.trim()}>
            {busy ? 'Proposing…' : 'Propose the next step'}
          </Button>
        </div>
      </form>
    </Card>
  )
}

function SlotPicker({ proposal, onBooked }) {
  const [open, setOpen] = useState(false)
  const [chosen, setChosen] = useState('')
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [reload, setReload] = useState(0)

  const grid = useAsync(
    () => (open ? bookingApi.slots(proposal.id) : Promise.resolve(null)),
    [open, proposal.id, reload],
  )

  async function book() {
    setBusy(true)
    setError(null)
    try {
      await bookingApi.accept(proposal.id, {
        start: chosen,
        attendee: { name: name.trim(), email: email.trim() },
      })
      onBooked()
    } catch (failure) {
      setError(refusalText(failure?.reason))
    } finally {
      setBusy(false)
    }
  }

  if (proposal.effective_state !== 'proposed') return null

  return (
    <div className="mt-3 border-t border-border-subtle pt-3">
      <div className="flex flex-wrap items-center gap-2">
        <Button onClick={() => setOpen((value) => !value)} aria-expanded={open}>
          {open ? 'Hide times' : 'Show times'}
        </Button>
        <Button variant="ghost" onClick={() => setReload((value) => value + 1)}>
          Refresh availability
        </Button>
      </div>

      {open && grid.loading && <Spinner label="Loading availability" />}
      {open && grid.error && <ErrorNote error={grid.error} onRetry={grid.refetch} />}
      {open && grid.data && (
        <div className="mt-3">
          <MicroLabel>Available times</MicroLabel>
          {grid.data.available_count === 0 ? (
            <p className="mt-2 text-sm text-muted-foreground">
              No free times right now. Try again later, or widen the room's calendar.
            </p>
          ) : (
            <div className="mt-2 flex flex-wrap gap-2">
              {grid.data.slots
                .filter((slot) => slot.available)
                .slice(0, 8)
                .map((slot) => (
                  <Button
                    key={slot.start}
                    variant={slot.start === chosen ? 'primary' : 'secondary'}
                    aria-label={`Book ${slot.start}`}
                    aria-pressed={slot.start === chosen}
                    onClick={() => setChosen(slot.start)}
                  >
                    <span className="font-mono text-xs">{formatInstant(slot.start)}</span>
                  </Button>
                ))}
            </div>
          )}

          {chosen && (
            <div className="mt-4 grid gap-4 sm:grid-cols-2">
              <Field label="Who is being booked" id={`wf136-name-${proposal.id}`} hint="Required for the invite.">
                <input
                  id={`wf136-name-${proposal.id}`}
                  className={inputClass}
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                />
              </Field>
              <Field label="Their email" id={`wf136-email-${proposal.id}`}>
                <input
                  id={`wf136-email-${proposal.id}`}
                  className={inputClass}
                  type="email"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                />
              </Field>
            </div>
          )}
          {error && (
            <p role="alert" className="mt-3 text-sm font-medium text-destructive">
              {error}
            </p>
          )}
          {chosen && (
            <div className="mt-4">
              <Button
                variant="primary"
                onClick={book}
                disabled={busy || !name.trim() || !email.trim()}
              >
                {busy ? 'Booking…' : 'Accept and book this time'}
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function ProposalCard({ proposal, onChanged }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  async function decline() {
    setBusy(true)
    setError(null)
    try {
      await bookingApi.decline(proposal.id, { reason: 'declined in the room' })
      onChanged()
    } catch (failure) {
      setError(refusalText(failure?.reason))
    } finally {
      setBusy(false)
    }
  }

  return (
    <li className="rounded-sm border border-border-subtle bg-surface p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-foreground">{proposal.summary}</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Proposed {formatInstant(proposal.proposed_at)} · expires {formatInstant(proposal.expires_at)}
          </p>
        </div>
        <StateBadge state={proposal.effective_state} />
      </div>

      <SlotPicker proposal={proposal} onBooked={onChanged} />

      {proposal.effective_state === 'proposed' && (
        <div className="mt-3 flex flex-wrap gap-2">
          <Button variant="danger" onClick={decline} disabled={busy}>
            Decline
          </Button>
        </div>
      )}
      {error && (
        <p role="alert" className="mt-2 text-sm font-medium text-destructive">
          {error}
        </p>
      )}
    </li>
  )
}

function MeetingCard({ meeting }) {
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [saved, setSaved] = useState(meeting.recording_url)

  async function attach(event) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const updated = await bookingApi.attachRecording(meeting.id, { url: url.trim() })
      setSaved(updated.recording_url)
      setUrl('')
    } catch (failure) {
      setError(refusalText(failure?.reason))
    } finally {
      setBusy(false)
    }
  }

  return (
    <li className="rounded-sm border border-border-subtle bg-surface p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-foreground">{meeting.summary}</p>
          <p className="mt-1 text-xs text-muted-foreground">
            {formatInstant(meeting.start)} · <Machine>{meeting.booking_uid}</Machine>
          </p>
        </div>
        <LinkBadge state={meeting.state} />
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-3">
        {meeting.conference_url ? (
          <a
            className="text-sm font-medium text-accent underline underline-offset-2"
            href={meeting.conference_url}
            target="_blank"
            rel="noreferrer"
          >
            Join link
          </a>
        ) : (
          <span className="text-sm text-muted-foreground">
            No join link: {meeting.conference_note || 'the provider produced none.'}
          </span>
        )}
        {saved ? (
          <a
            className="text-sm font-medium text-accent underline underline-offset-2"
            href={saved}
            target="_blank"
            rel="noreferrer"
          >
            Watch recording
          </a>
        ) : (
          <form className="flex flex-wrap items-end gap-2" onSubmit={attach}>
            <Field label="Recording link" id={`wf136-rec-${meeting.id}`}>
              <input
                id={`wf136-rec-${meeting.id}`}
                className={inputClass}
                value={url}
                onChange={(event) => setUrl(event.target.value)}
              />
            </Field>
            <Button type="submit" disabled={busy || !url.trim()}>
              Attach
            </Button>
          </form>
        )}
      </div>
      {error && (
        <p role="alert" className="mt-2 text-sm font-medium text-destructive">
          {error}
        </p>
      )}
    </li>
  )
}

export default function NextStepsBoard() {
  const rooms = useAsync(() => listRooms(), [])
  const [roomChoice, setRoomChoice] = useState('')

  // The effective room is the one the seller picked, or else the first room once
  // the list arrives. Deriving it rather than writing it into state from an
  // effect keeps this a single render pass: an effect that called `setRoomId`
  // would run again on the value it just set.
  const roomId = roomChoice || rooms.data?.records?.[0]?.id || ''

  const board = useAsync(
    () =>
      roomId
        ? Promise.all([
            bookingApi.proposals(roomId),
            bookingApi.meetings(roomId),
            bookingApi.nextSteps(roomId),
            bookingApi.summary(roomId),
          ]).then(([proposals, meetings, nextSteps, summary]) => ({
            proposals: proposals.proposals,
            meetings: meetings.meetings,
            nextSteps,
            summary,
          }))
        : Promise.resolve(null),
    [roomId],
  )

  if (rooms.loading) return <Spinner label="Loading rooms" />
  if (rooms.error) return <ErrorNote error={rooms.error} onRetry={rooms.refetch} />
  if (!rooms.data?.records?.length) {
    return (
      <EmptyState
        title="No rooms yet"
        description="Seed the demo, or create a room, and its next steps will appear here."
      />
    )
  }

  const reload = () => {
    board.refetch()
    rooms.refetch()
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-foreground">Next steps</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Book the next step from inside the room. The meeting, its join link and the
          recording all land on the room's timeline.
        </p>
      </div>

      <Card>
        <RoomPicker rooms={rooms.data.records} roomId={roomId} onChange={setRoomChoice} />
      </Card>

      {board.loading && <Spinner label="Loading the room's next steps" />}
      {board.error && <ErrorNote error={board.error} onRetry={board.refetch} />}

      {board.data && (
        <>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard label="Awaiting the buyer" value={board.data.summary.answerable} />
            <StatCard label="Booked" value={board.data.summary.booked} />
            <StatCard label="Meetings" value={board.data.summary.meetings} />
            <StatCard
              label="Open plan tasks"
              value={board.data.nextSteps.map_tasks.length}
              hint="From the mutual action plan"
            />
          </div>

          <ProposeForm roomId={roomId} tasks={board.data.nextSteps.map_tasks} onCreated={reload} />

          <section className="space-y-3">
            <h2 className="text-lg font-semibold text-foreground">Proposals</h2>
            {board.data.proposals.length === 0 ? (
              <EmptyState
                title="No next step proposed yet"
                description="Propose one above, and the buyer can pick a time from inside the room."
              />
            ) : (
              <ul className="space-y-3">
                {board.data.proposals.map((proposal) => (
                  <ProposalCard key={proposal.id} proposal={proposal} onChanged={reload} />
                ))}
              </ul>
            )}
          </section>

          <section className="space-y-3">
            <h2 className="text-lg font-semibold text-foreground">Booked meetings</h2>
            {board.data.meetings.length === 0 ? (
              <EmptyState
                title="No meeting booked yet"
                description="Once the buyer accepts a time, the meeting appears here with its join link."
              />
            ) : (
              <ul className="space-y-3">
                {board.data.meetings.map((meeting) => (
                  <MeetingCard key={meeting.id} meeting={meeting} />
                ))}
              </ul>
            )}
          </section>

          <section className="space-y-3">
            <h2 className="text-lg font-semibold text-foreground">Action plan tasks still open</h2>
            {board.data.nextSteps.map_tasks.length === 0 ? (
              <EmptyState
                title="No open plan tasks"
                description="The mutual action plan has nothing left open for this room."
              />
            ) : (
              <ul className="space-y-2">
                {board.data.nextSteps.map_tasks.map((task) => (
                  <li
                    key={task.id}
                    className="flex flex-wrap items-center justify-between gap-2 rounded-sm border border-border-subtle bg-surface p-3 text-sm"
                  >
                    <span className="font-medium text-foreground">{task.title}</span>
                    <span className="text-xs text-muted-foreground">
                      {task.owner} · {task.owner_side}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}
    </div>
  )
}
