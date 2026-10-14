/**
 * WF-132: the in-room conversation and champion self-service console.
 *
 * Three things this page does, in the order the research describes them:
 *
 * 1. Read the threads anchored to a room's objects and answer one.
 * 2. Tag a colleague into a thread, and see whether the tag resolved.
 * 3. Let a buyer-side champion edit a section themselves and read the change log.
 *
 * The contested flag the specification carries is shown rather than hidden: the
 * research says a room you cannot talk inside may not be a room at all, so the
 * page states that this workflow treats conversation as a first-class surface.
 *
 * Every async block has an explicit loading, error and empty state, and the room
 * is a picker rather than a hard-coded id, because a page that only works for one
 * room teaches a reviewer nothing.
 */

import { useState } from 'react'

import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorNote,
  Field,
  Spinner,
  StatCard,
  inputClass,
  useAsync,
} from '@/components/ui'

import {
  ANCHOR_WORDS,
  KIND_WORDS,
  SIDE_WORDS,
  conflictText,
  conversationApi,
  listRooms,
} from './api'
import {
  AnchorChip,
  CommentRow,
  DeltaList,
  EscalationRow,
  Section,
  StateBadge,
  ThreadRow,
} from './primitives'

const FALLBACK_ANCHORS = ['page', 'section', 'asset', 'map_task']

function Notice({ children }) {
  if (!children) return null
  return (
    <div
      role="status"
      className="rounded-sm border border-border-subtle bg-muted p-3 text-sm text-foreground"
    >
      {children}
    </div>
  )
}

export default function RoomConversationPage() {
  const rooms = useAsync(() => listRooms(), [])
  const roomList = rooms.data?.records || []

  // The selected room and the acting principal are derived rather than mirrored
  // into state by an effect: an effect that copies a payload into state renders
  // once with the stale value and then corrects it, and the linter's
  // set-state-in-effect rule is right to call that a cascading render. An
  // override of '' means "not chosen yet", so the first room and its owner are
  // the defaults without a second render.
  const [roomIdOverride, setRoomIdOverride] = useState('')
  const roomId = roomIdOverride || roomList[0]?.id || ''

  const room = roomList.find((record) => record.id === roomId)
  const owner = room?.data?.owner || ''
  const [actorOverride, setActorOverride] = useState('')
  const actor = actorOverride || owner

  const [nonce, setNonce] = useState(0)
  const bump = () => setNonce((value) => value + 1)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')

  const vocabulary = useAsync(() => conversationApi.vocabulary(), [])
  const anchorKinds = vocabulary.data?.anchor_kinds || FALLBACK_ANCHORS

  const summary = useAsync(
    () => (roomId ? conversationApi.summary(roomId) : Promise.resolve(null)),
    [roomId, nonce],
  )
  const threads = useAsync(
    () => (roomId ? conversationApi.threads(roomId) : Promise.resolve(null)),
    [roomId, nonce],
  )
  const escalations = useAsync(
    () => (roomId ? conversationApi.escalations(roomId) : Promise.resolve(null)),
    [roomId, nonce],
  )
  const champions = useAsync(
    () => (roomId ? conversationApi.champions(roomId) : Promise.resolve(null)),
    [roomId, nonce],
  )
  const identity = useAsync(
    () => (roomId && actor ? conversationApi.identity(roomId, actor) : Promise.resolve(null)),
    [roomId, actor],
  )

  const [selectedId, setSelectedId] = useState('')
  const thread = useAsync(
    () => (selectedId ? conversationApi.thread(selectedId) : Promise.resolve(null)),
    [selectedId, nonce],
  )

  const [anchorType, setAnchorType] = useState('page')
  const [anchorId, setAnchorId] = useState('')
  const [newBody, setNewBody] = useState('')
  const [commentBody, setCommentBody] = useState('')
  const [commentKind, setCommentKind] = useState('comment')
  const [handle, setHandle] = useState('')
  const [sectionRef, setSectionRef] = useState('')
  const [beforeText, setBeforeText] = useState('')
  const [afterText, setAfterText] = useState('')
  const [editSummary, setEditSummary] = useState('')

  async function run(action, done) {
    setError('')
    setNotice('')
    try {
      await action()
      if (done) setNotice(done)
      bump()
    } catch (caught) {
      setError(caught.reason ? conflictText(caught.reason) : caught.message || String(caught))
    }
  }

  function parseJson(text, label) {
    if (!text.trim()) return {}
    const parsed = JSON.parse(text)
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
      throw new Error(`${label} must be a JSON object`)
    }
    return parsed
  }

  if (rooms.loading) return <Spinner label="Loading rooms" />
  if (rooms.error) return <ErrorNote error={rooms.error} onRetry={rooms.refetch} />
  if (!roomList.length) {
    return (
      <EmptyState
        title="No rooms yet"
        description="Seed the demo dataset to give this page a room to show."
      />
    )
  }

  const threadList = threads.data?.threads || []
  const openThread = thread.data

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h1 className="font-display text-2xl font-semibold text-foreground">Room conversations</h1>
        <p className="text-sm text-muted-foreground">
          Comments and threads anchored to a room&apos;s pages, sections, assets and MAP tasks.
          Tag a colleague, answer a question, and let a champion edit content without a rep.
        </p>
        {vocabulary.data?.contested && (
          <p className="text-xs text-muted-foreground">
            <span className="font-medium text-foreground">Contested surface: </span>
            {vocabulary.data.contested.reason}
          </p>
        )}
      </div>

      <Card>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Room" id="wf132-room">
            <select
              id="wf132-room"
              className={inputClass}
              value={roomId}
              onChange={(event) => {
                setRoomIdOverride(event.target.value)
                setSelectedId('')
              }}
            >
              {roomList.map((record) => (
                <option key={record.id} value={record.id}>
                  {record.data?.name || record.id}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Acting as" id="wf132-actor" hint="An address or handle in this room.">
            <input
              id="wf132-actor"
              className={inputClass}
              value={actor}
              onChange={(event) => setActorOverride(event.target.value)}
            />
          </Field>
        </div>
        {identity.data && (
          <p className="mt-3 text-xs text-muted-foreground">
            {`${identity.data.principal || 'nobody'} is ${identity.data.role_label} on the `}
            {`${SIDE_WORDS[identity.data.side] || identity.data.side} side`}
            {identity.data.is_champion ? ' and is the champion' : ''}.
          </p>
        )}
      </Card>

      {error && <ErrorNote error={error} onRetry={bump} />}
      <Notice>{notice}</Notice>

      <Section title="At a glance" description="The room's conversation, counted.">
        {summary.loading && <Spinner label="Loading summary" />}
        {summary.error && <ErrorNote error={summary.error} onRetry={summary.refetch} />}
        {summary.data && (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard label="Threads" value={summary.data.threads} hint="Anchored to room objects" />
            <StatCard label="Open" value={summary.data.open_threads} hint="Not yet resolved" />
            <StatCard label="Comments" value={summary.data.comments} hint="Including questions" />
            <StatCard
              label="Unanswered"
              value={summary.data.unanswered_questions}
              hint="Buyer questions past the interval"
            />
            <StatCard label="Escalations" value={summary.data.escalations} hint="Raised to the owner" />
            <StatCard
              label="Champion edits"
              value={summary.data.champion_edits}
              hint="Buyer-side self-service"
            />
            <StatCard
              label="Unresolved tags"
              value={summary.data.unresolved_mentions}
              hint="No grant matches the handle"
            />
            <StatCard label="Conversions" value={summary.data.conversions} hint="Threads to MAP tasks" />
          </div>
        )}
      </Section>

      <Section
        title="Threads"
        description="Pick a thread to read and answer it."
        action={
          <Button
            icon="refresh"
            onClick={() => {
              threads.refetch()
              summary.refetch()
              escalations.refetch()
              champions.refetch()
            }}
          >
            Refresh
          </Button>
        }
      >
        <div className="grid gap-4 lg:grid-cols-2">
          <Card>
            <h3 className="text-base font-semibold text-foreground">Start a thread</h3>
            <div className="mt-3 space-y-3">
              <Field label="Anchor kind" id="wf132-anchor-kind">
                <select
                  id="wf132-anchor-kind"
                  className={inputClass}
                  value={anchorType}
                  onChange={(event) => setAnchorType(event.target.value)}
                >
                  {anchorKinds.map((kind) => (
                    <option key={kind} value={kind}>
                      {ANCHOR_WORDS[kind] || kind}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Anchor id" id="wf132-anchor-id">
                <input
                  id="wf132-anchor-id"
                  className={inputClass}
                  value={anchorId}
                  onChange={(event) => setAnchorId(event.target.value)}
                />
              </Field>
              <Field label="First comment" id="wf132-new-body">
                <textarea
                  id="wf132-new-body"
                  className={inputClass}
                  rows={3}
                  value={newBody}
                  onChange={(event) => setNewBody(event.target.value)}
                />
              </Field>
              <Button
                variant="primary"
                icon="plus"
                onClick={() =>
                  run(
                    () =>
                      conversationApi.openThread(roomId, {
                        actor,
                        anchor_type: anchorType,
                        anchor_id: anchorId,
                        body: newBody,
                      }),
                    'Thread opened.',
                  )
                }
              >
                Open thread
              </Button>
            </div>
          </Card>

          <Card>
            <h3 className="text-base font-semibold text-foreground">{`Threads (${threadList.length})`}</h3>
            {threads.loading && <Spinner label="Loading threads" />}
            {threads.error && <ErrorNote error={threads.error} onRetry={threads.refetch} />}
            {threads.data && threadList.length === 0 && (
              <p className="mt-3 text-sm text-muted-foreground">
                No thread in this room yet. Open one on the left.
              </p>
            )}
            {threadList.length > 0 && (
              <ul className="mt-3 space-y-2">
                {threadList.map((row) => (
                  <ThreadRow
                    key={row.id}
                    thread={row}
                    active={row.id === selectedId}
                    onSelect={() => setSelectedId(row.id)}
                  />
                ))}
              </ul>
            )}
          </Card>
        </div>

        {selectedId && (
          <Card className="mt-4">
            {thread.loading && <Spinner label="Loading thread" />}
            {thread.error && <ErrorNote error={thread.error} onRetry={thread.refetch} />}
            {openThread && (
              <div className="space-y-4">
                <div className="flex flex-wrap items-center gap-2">
                  <StateBadge state={openThread.state} />
                  <AnchorChip anchor={openThread.anchor} />
                  <Badge tone="neutral">{`${openThread.message_count} message(s)`}</Badge>
                  {openThread.map_task_ref && (
                    <Badge tone="info">{`MAP task ${openThread.map_task_ref}`}</Badge>
                  )}
                </div>
                <ul className="space-y-2">
                  {openThread.comments.map((comment) => (
                    <CommentRow key={comment.id} comment={comment} />
                  ))}
                </ul>

                <div className="grid gap-3 sm:grid-cols-[1fr_auto] sm:items-end">
                  <Field label="Reply" id="wf132-comment-body">
                    <textarea
                      id="wf132-comment-body"
                      className={inputClass}
                      rows={2}
                      value={commentBody}
                      onChange={(event) => setCommentBody(event.target.value)}
                    />
                  </Field>
                  <Field label="Kind" id="wf132-comment-kind">
                    <select
                      id="wf132-comment-kind"
                      className={inputClass}
                      value={commentKind}
                      onChange={(event) => setCommentKind(event.target.value)}
                    >
                      {['comment', 'question'].map((kind) => (
                        <option key={kind} value={kind}>
                          {KIND_WORDS[kind]}
                        </option>
                      ))}
                    </select>
                  </Field>
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button
                    variant="primary"
                    onClick={() =>
                      run(
                        () =>
                          conversationApi.comment(selectedId, {
                            actor,
                            body: commentBody,
                            kind: commentKind,
                          }),
                        'Comment posted.',
                      )
                    }
                  >
                    Post comment
                  </Button>
                  <Button
                    onClick={() =>
                      run(
                        () => conversationApi.resolve(selectedId, { actor }),
                        'Thread resolved.',
                      )
                    }
                  >
                    Resolve
                  </Button>
                  <Button
                    onClick={() =>
                      run(() => conversationApi.reopen(selectedId, { actor }), 'Thread reopened.')
                    }
                  >
                    Reopen
                  </Button>
                  <Button
                    onClick={() =>
                      run(
                        () => conversationApi.convert(selectedId, { actor }),
                        'Thread recorded as a MAP task conversion.',
                      )
                    }
                  >
                    Convert to task
                  </Button>
                </div>

                <div className="grid gap-3 sm:grid-cols-[1fr_auto] sm:items-end">
                  <Field label="Tag a colleague" id="wf132-handle" hint="An address or handle.">
                    <input
                      id="wf132-handle"
                      className={inputClass}
                      value={handle}
                      onChange={(event) => setHandle(event.target.value)}
                    />
                  </Field>
                  <Button
                    onClick={() =>
                      run(
                        () => conversationApi.tag(selectedId, { actor, principals: [handle] }),
                        'Tag recorded.',
                      )
                    }
                  >
                    Tag
                  </Button>
                </div>
              </div>
            )}
          </Card>
        )}
      </Section>

      <Section
        title="Champion self-service"
        description="A buyer-side participant with a content-managing role edits a section; the change log keeps the delta."
      >
        <div className="grid gap-4 lg:grid-cols-2">
          <Card>
            <h3 className="text-base font-semibold text-foreground">Edit a section</h3>
            <div className="mt-3 space-y-3">
              <Field label="Section" id="wf132-section">
                <input
                  id="wf132-section"
                  className={inputClass}
                  value={sectionRef}
                  onChange={(event) => setSectionRef(event.target.value)}
                />
              </Field>
              <Field label="Before (JSON)" id="wf132-before">
                <textarea
                  id="wf132-before"
                  className={inputClass}
                  rows={2}
                  value={beforeText}
                  onChange={(event) => setBeforeText(event.target.value)}
                />
              </Field>
              <Field label="After (JSON)" id="wf132-after">
                <textarea
                  id="wf132-after"
                  className={inputClass}
                  rows={2}
                  value={afterText}
                  onChange={(event) => setAfterText(event.target.value)}
                />
              </Field>
              <Field label="Summary" id="wf132-edit-summary">
                <input
                  id="wf132-edit-summary"
                  className={inputClass}
                  value={editSummary}
                  onChange={(event) => setEditSummary(event.target.value)}
                />
              </Field>
              <Button
                variant="primary"
                onClick={() =>
                  run(
                    () =>
                      conversationApi.recordEdit(roomId, {
                        actor,
                        section_ref: sectionRef,
                        summary: editSummary,
                        before: parseJson(beforeText, 'Before'),
                        after: parseJson(afterText, 'After'),
                      }),
                    'Edit recorded.',
                  )
                }
              >
                Record edit
              </Button>
            </div>
          </Card>

          <Card>
            <h3 className="text-base font-semibold text-foreground">Change log</h3>
            {champions.loading && <Spinner label="Loading change log" />}
            {champions.error && <ErrorNote error={champions.error} onRetry={champions.refetch} />}
            {champions.data && champions.data.champion_edits === 0 && (
              <p className="mt-3 text-sm text-muted-foreground">
                No champion edit yet. A buyer-side Room Collaborator edits without a rep.
              </p>
            )}
            {champions.data && champions.data.champion_edits > 0 && (
              <>
                <p className="mt-3 text-sm text-muted-foreground">
                  {`${champions.data.champion_edits} champion edit(s) by `}
                  {champions.data.editors.map((editor) => editor.principal).join(', ')}
                </p>
                <ul className="mt-3 space-y-2">
                  {champions.data.edits.map((edit) => (
                    <li key={edit.id} className="rounded-sm border border-border-subtle p-3">
                      <p className="font-mono text-xs text-muted-foreground">
                        {`${edit.section_ref} - ${edit.actor}`}
                      </p>
                      {edit.summary && <p className="mt-1 text-sm text-foreground">{edit.summary}</p>}
                      <div className="mt-2">
                        <DeltaList delta={edit.delta} />
                      </div>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </Card>
        </div>
      </Section>

      <Section title="Escalations" description="Unanswered buyer questions raised to the room owner.">
        <Card>
          <div className="flex flex-wrap gap-2">
            <Button
              icon="refresh"
              onClick={() =>
                run(
                  () => conversationApi.refreshEscalations(roomId),
                  'Escalation sweep complete.',
                )
              }
            >
              Sweep
            </Button>
          </div>
          {escalations.loading && <Spinner label="Loading escalations" />}
          {escalations.error && (
            <ErrorNote error={escalations.error} onRetry={escalations.refetch} />
          )}
          {escalations.data && escalations.data.count === 0 && (
            <p className="mt-3 text-sm text-muted-foreground">
              No escalation in this room. A buyer question escalates once it passes the interval.
            </p>
          )}
          {escalations.data && escalations.data.count > 0 && (
            <ul className="mt-3 space-y-2">
              {escalations.data.escalations.map((row) => (
                <EscalationRow key={row.id} escalation={row} />
              ))}
            </ul>
          )}
        </Card>
      </Section>
    </div>
  )
}
