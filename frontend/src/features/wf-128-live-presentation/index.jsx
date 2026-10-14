/**
 * WF-128: in-room live presentation / co-browsing.
 *
 * The page is the seller's view of a room's live sessions. It reads its closed
 * vocabularies from the server's `/vocabulary` route rather than repeating them,
 * so a panel cannot drift from the rules the engine enforces.
 *
 * Three things get deliberate treatment rather than a generic list row:
 *
 *   1. **The live transport is labelled a stand-in.** No SFU and no stream exist
 *      in this build, so the page says so where a "live" badge would otherwise
 *      imply a working stream.
 *   2. **The reveal order is drawn as an order**, so a reader can see why a
 *      forward jump is refused without reading the server's message.
 *   3. **The follower control is a real call.** "Simulate this follower joining"
 *      calls the same `/sync` route a follower would reach, so what a seller sees
 *      is what the stand-in transport records.
 *
 * Loading, error and empty states are all present, as the design system requires.
 */

import { useState } from 'react'

import {
  Badge,
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
  conflictText,
  formatDateTime,
  listRooms,
  sessionApi,
  sessionStateLabel,
  sessionStateTone,
} from './api'
import {
  AgendaList,
  AttendanceTable,
  EventLog,
  Fact,
  InlineNote,
  ParticipantTable,
  QuestionList,
  ReplayPanel,
  Section,
} from './primitives'

const ICON =
  'M3 4h18v11H3zM8 20h8M12 15v5M10 7.5l4 2-4 2z'

async function loadSessionBundle(sessionId) {
  const [session, participants, questions, events, attendance, recordings] = await Promise.all([
    sessionApi.session(sessionId),
    sessionApi.participants(sessionId),
    sessionApi.questions(sessionId),
    sessionApi.events(sessionId),
    sessionApi.attendance(sessionId),
    sessionApi.recordings(sessionId),
  ])
  return {
    session,
    participants: participants.entries || [],
    questions: questions.entries || [],
    events: events.entries || [],
    attendance: attendance.entries || [],
    recordings: recordings.entries || [],
  }
}

function SessionDetail({ sessionId, onChanged }) {
  const { data, loading, error, refetch } = useAsync(
    () => loadSessionBundle(sessionId),
    [sessionId],
  )
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState(null)
  const [asker, setAsker] = useState('')
  const [questionText, setQuestionText] = useState('')

  async function run(action) {
    setBusy(true)
    setActionError(null)
    try {
      await action()
      refetch()
      onChanged()
    } catch (caught) {
      setActionError(caught.reason ? conflictText(caught.reason) : String(caught.message || caught))
    } finally {
      setBusy(false)
    }
  }

  if (loading) return <Spinner label="Loading session" />
  if (error) return <ErrorNote error={error} onRetry={refetch} />

  const session = data.session
  const participantRows = data.participants || []
  const questionRows = data.questions || []
  const eventRows = data.events || []
  const attendanceRows = data.attendance || []
  const recordings = data.recordings || []
  const live = session.state === 'live'
  const stepCount = session.step_count || 0
  const canReveal = live && session.position < stepCount - 1
  const canStepBack = live && session.position > 0

  return (
    <div className="space-y-6">
      <Section
        title={session.title}
        description={`Room ${session.room_id}`}
        actions={
          <Badge tone={sessionStateTone(session.state)}>
            {sessionStateLabel(session.state)}
          </Badge>
        }
      >
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <Fact label="Presenter" value={session.presenter} mono />
          <Fact label="Steps" value={`${session.position + 1} of ${stepCount}`} mono />
          <Fact label="Revealed so far" value={`${session.revealed + 1} of ${stepCount}`} mono />
        </div>

        <div className="mt-4">
          <InlineNote label="Live transport is a stand-in">
            {session.transport_note} There is no stream here, which is why a follower
            reports in rather than watching a tile.
          </InlineNote>
        </div>

        <div className="mt-4 flex flex-wrap gap-2">
          {session.state === 'scheduled' && (
            <Button
              icon="plus"
              disabled={busy}
              onClick={() => run(() => sessionApi.startSession(session.id, {}))}
            >
              Start session
            </Button>
          )}
          <Button
            icon="chevron"
            disabled={busy || !canReveal}
            onClick={() => run(() => sessionApi.reveal(session.id, { position: session.position + 1 }))}
          >
            Reveal next step
          </Button>
          <Button
            icon="restore"
            disabled={busy || !canStepBack}
            onClick={() => run(() => sessionApi.reveal(session.id, { position: session.position - 1 }))}
          >
            Step back
          </Button>
          {live && (
            <Button
              variant="danger"
              icon="close"
              disabled={busy}
              onClick={() => run(() => sessionApi.endSession(session.id, {}))}
            >
              End and publish replay
            </Button>
          )}
        </div>
        {actionError && (
          <p className="mt-3 text-sm text-destructive" role="alert">
            {actionError}
          </p>
        )}
      </Section>

      <Section
        title="Reveal order"
        description="The presenter reveals one step at a time. A step cannot be revealed before the one before it."
      >
        <AgendaList
          agenda={session.agenda}
          position={session.position}
          revealed={session.revealed}
        />
      </Section>

      <Section title="Participants" description="Each participant has their own link.">
        <ParticipantTable
          participants={participantRows}
          busy={busy}
          onSync={(email) => run(() => sessionApi.sync(session.id, { as: email }))}
        />
      </Section>

      <Section title="Questions" description="Questions asked live are logged against the deal.">
        <form
          className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-[1fr_2fr_auto] sm:items-end"
          onSubmit={(event) => {
            event.preventDefault()
            run(async () => {
              await sessionApi.askQuestion(session.id, { asker, text: questionText })
              setQuestionText('')
            })
          }}
        >
          <Field label="Asked by" id="wf128-asker">
            <input
              id="wf128-asker"
              className={inputClass}
              value={asker}
              onChange={(event) => setAsker(event.target.value)}
              placeholder="a.buyer@northwind.example"
            />
          </Field>
          <Field label="Question" id="wf128-question">
            <input
              id="wf128-question"
              className={inputClass}
              value={questionText}
              onChange={(event) => setQuestionText(event.target.value)}
              placeholder="How does SSO map onto our groups?"
            />
          </Field>
          <Button type="submit" disabled={busy || !asker || !questionText} className="min-h-11">
            Log question
          </Button>
        </form>
        <QuestionList
          questions={questionRows}
          busy={busy}
          onAnswer={(questionId) =>
            run(() => sessionApi.answerQuestion(questionId, { answer: 'Answered in the session.' }))
          }
        />
      </Section>

      <Section title="Attendance" description="Written to the room record for this deal.">
        <AttendanceTable rows={attendanceRows} />
      </Section>

      <Section title="Replay" description="The session is attached to the room as a replay.">
        <ReplayPanel recording={recordings[0]} />
      </Section>

      <Section title="Event log" description="The session recorded as events, newest first.">
        <EventLog events={eventRows} />
      </Section>
    </div>
  )
}

function LivePresentationPage() {
  const rooms = useAsync(listRooms, [])
  const vocabulary = useAsync(sessionApi.vocabulary, [])
  const [roomId, setRoomId] = useState('')
  const [selected, setSelected] = useState('')
  const [refreshKey, setRefreshKey] = useState(0)
  const [busy, setBusy] = useState(false)
  const [formError, setFormError] = useState(null)
  const [title, setTitle] = useState('')
  const [presenter, setPresenter] = useState('')
  const [invitees, setInvitees] = useState('')

  const roomRecords = rooms.data?.records || []
  const activeRoom = roomId || roomRecords[0]?.id || ''

  const summary = useAsync(
    () => (activeRoom ? sessionApi.summary(activeRoom) : Promise.resolve(null)),
    [activeRoom, refreshKey],
  )
  const sessions = useAsync(
    () => (activeRoom ? sessionApi.sessions(activeRoom) : Promise.resolve({ entries: [] })),
    [activeRoom, refreshKey],
  )

  if (rooms.loading || vocabulary.loading) return <Spinner label="Loading live presentations" />
  if (rooms.error) return <ErrorNote error={rooms.error} onRetry={rooms.refetch} />
  if (vocabulary.error) return <ErrorNote error={vocabulary.error} onRetry={vocabulary.refetch} />

  const words = vocabulary.data

  async function createSession(event) {
    event.preventDefault()
    setBusy(true)
    setFormError(null)
    try {
      const created = await sessionApi.createSession({
        room_id: activeRoom,
        title: title || undefined,
        presenter: presenter || undefined,
        invitees: invitees
          .split(',')
          .map((part) => part.trim())
          .filter(Boolean),
      })
      setSelected(created.id)
      setTitle('')
      setPresenter('')
      setInvitees('')
      setRefreshKey((key) => key + 1)
    } catch (caught) {
      setFormError(
        caught.errors
          ? Object.entries(caught.errors)
              .map(([field, message]) => `${field}: ${message}`)
              .join('; ')
          : caught.reason
            ? conflictText(caught.reason)
            : String(caught.message || caught),
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="font-display text-2xl font-semibold text-foreground">Live presentation</h1>
          <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
            Start a live session from a room. The buying committee joins with their own links and
            follows the presenter through the room's pages as the reveal order is respected. The
            session is published to the room as a replay.
          </p>
        </div>
        <span className="rounded-sm bg-accent-soft p-2 text-accent" aria-hidden="true">
          <Icon path={ICON} size={22} />
        </span>
      </header>

      <InlineNote label={words.stack_choice.sfu.name}>
        {words.stack_choice.sfu.reason}
      </InlineNote>

      <Card>
        <Field label="Room" id="wf128-room" hint="Choose the deal room to present from.">
          <select
            id="wf128-room"
            className={inputClass}
            value={activeRoom}
            onChange={(event) => {
              setRoomId(event.target.value)
              setSelected('')
            }}
          >
            {roomRecords.map((room) => (
              <option key={room.id} value={room.id}>
                {room.data?.name || room.id}
              </option>
            ))}
          </select>
        </Field>
      </Card>

      {!activeRoom ? (
        <EmptyState
          title="No rooms yet"
          description="Create a room before starting a live session from it."
        />
      ) : (
        <>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard label="Sessions" value={summary.data?.sessions ?? '-'} icon="rooms" />
            <StatCard label="Live now" value={summary.data?.live ?? '-'} icon="dashboard" />
            <StatCard label="Attended" value={summary.data?.attended ?? '-'} icon="audit" />
            <StatCard
              label="Open questions"
              value={summary.data?.open_questions ?? '-'}
              icon="schema"
            />
          </div>

          <Section title="Plan a session" description="The agenda is this room's own pages.">
            <form className="grid grid-cols-1 gap-3 sm:grid-cols-2" onSubmit={createSession}>
              <Field label="Session title" id="wf128-title">
                <input
                  id="wf128-title"
                  className={inputClass}
                  value={title}
                  onChange={(event) => setTitle(event.target.value)}
                  placeholder="Live walkthrough"
                />
              </Field>
              <Field label="Presenter" id="wf128-presenter" hint="The rep who drives.">
                <input
                  id="wf128-presenter"
                  className={inputClass}
                  value={presenter}
                  onChange={(event) => setPresenter(event.target.value)}
                  placeholder="rep@northwind.example"
                />
              </Field>
              <div className="sm:col-span-2">
                <Field
                  label="Invitees"
                  id="wf128-invitees"
                  hint="Comma-separated. Left empty, the room's recipients are invited."
                >
                  <input
                    id="wf128-invitees"
                    className={inputClass}
                    value={invitees}
                    onChange={(event) => setInvitees(event.target.value)}
                    placeholder="a.buyer@northwind.example, b.buyer@northwind.example"
                  />
                </Field>
              </div>
              <div className="sm:col-span-2">
                <Button type="submit" variant="primary" icon="plus" disabled={busy} className="min-h-11">
                  Create session
                </Button>
                {formError && (
                  <p className="mt-2 text-sm text-destructive" role="alert">
                    {formError}
                  </p>
                )}
              </div>
            </form>
          </Section>

          <Section title="Sessions" description="Newest first.">
            {sessions.loading ? (
              <Spinner label="Loading sessions" />
            ) : sessions.error ? (
              <ErrorNote error={sessions.error} onRetry={sessions.refetch} />
            ) : (sessions.data?.entries || []).length === 0 ? (
              <EmptyState
                title="No sessions in this room"
                description="Plan a session above to see the workflow run."
              />
            ) : (
              <ul className="flex flex-col gap-2">
                {sessions.data.entries.map((row) => (
                  <li key={row.id}>
                    <button
                      type="button"
                      onClick={() => setSelected(row.id)}
                      className={`flex min-h-11 w-full flex-wrap items-center justify-between gap-3 rounded-sm border px-3 py-2 text-left ${
                        selected === row.id
                          ? 'border-accent bg-accent-soft'
                          : 'border-border-subtle bg-surface hover:border-accent'
                      }`}
                    >
                      <span className="min-w-0">
                        <span className="block text-sm text-foreground">{row.title}</span>
                        <span className="font-mono text-xs text-muted-foreground">
                          {formatDateTime(row.created_at_ms)} · {row.step_count} steps
                        </span>
                      </span>
                      <Badge tone={sessionStateTone(row.state)}>
                        {sessionStateLabel(row.state)}
                      </Badge>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Section>

          {selected && (
            <SessionDetail
              sessionId={selected}
              onChanged={() => setRefreshKey((key) => key + 1)}
            />
          )}
        </>
      )}
    </div>
  )
}

export default {
  id: 'wf-128-live-presentation',
  label: 'Live presentation',
  icon: 'rooms',
  iconPath: ICON,
  order: 1280,
  Component: LivePresentationPage,
}
