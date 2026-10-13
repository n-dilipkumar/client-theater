/**
 * WF-123: the seller's qualification-capture console.
 *
 * Three things a rep does here, in the order the research describes them:
 *
 * 1. Read what the buyer is being asked, in the buyer's own view.
 * 2. Embed a short form in a room and publish it.
 * 3. Read the qualification the submissions produced - the score, the band and
 *    where the band routed the deal.
 *
 * The buyer's view is a separate fetch from the seller's, and the page never
 * derives it from a seller record: the one rule the research states is that the
 * qualification summary is visible to the seller only.
 *
 * Every async block has an explicit loading, error and empty state, and the room
 * is a picker rather than a hard-coded id, because a page that only works for
 * one room teaches a reviewer nothing.
 */

import { useEffect, useMemo, useState } from 'react'

import {
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
  bandBreakdown,
  listRooms,
  qualificationApi,
} from './api'
import {
  BuyerPreview,
  DimensionChecklist,
  InlineNote,
  Section,
  SubmissionDetail,
  SubmissionRow,
  FormRow,
} from './primitives'

const DEFAULT_DIMENSIONS = ['budget', 'timeline', 'authority', 'procurement_path']

export default function QualificationCapture() {
  const rooms = useAsync(() => listRooms(), [])
  const roomList = rooms.data?.records || []
  const [roomId, setRoomId] = useState('')

  // Default to the first room once the list arrives, without clobbering a
  // choice the seller has already made.
  useEffect(() => {
    if (!roomId && roomList.length) setRoomId(roomList[0].id)
  }, [roomList, roomId])

  const forms = useAsync(
    () => (roomId ? qualificationApi.forms({ room_id: roomId }) : Promise.resolve(null)),
    [roomId],
  )
  const submissions = useAsync(
    () => (roomId ? qualificationApi.submissions({ room_id: roomId }) : Promise.resolve(null)),
    [roomId],
  )
  const summary = useAsync(
    () => (roomId ? qualificationApi.qualification(roomId) : Promise.resolve(null)),
    [roomId],
  )

  const [buyerForm, setBuyerForm] = useState(null)
  const [selectedSubmissionId, setSelectedSubmissionId] = useState('')
  const [notice, setNotice] = useState(null)
  const [name, setName] = useState('')
  const [intro, setIntro] = useState('')
  const [dimensions, setDimensions] = useState(DEFAULT_DIMENSIONS)

  const detail = useAsync(
    () =>
      selectedSubmissionId
        ? qualificationApi.submission(selectedSubmissionId)
        : Promise.resolve(null),
    [selectedSubmissionId],
  )

  // The buyer's view is its own read, and a room with no live form is the
  // ordinary first state rather than an error.
  useEffect(() => {
    if (!roomId) {
      setBuyerForm(null)
      return undefined
    }
    let cancelled = false
    qualificationApi
      .roomForm(roomId)
      .then((data) => {
        if (!cancelled) setBuyerForm(data)
      })
      .catch(() => {
        if (!cancelled) setBuyerForm(null)
      })
    return () => {
      cancelled = true
    }
  }, [roomId, forms.data])

  const questions = useMemo(() => buyerForm?.questions || [], [buyerForm])

  const refresh = () => {
    forms.refetch()
    submissions.refetch()
    summary.refetch()
  }

  async function createForm(event) {
    event.preventDefault()
    setNotice(null)
    if (!name.trim()) {
      setNotice({ tone: 'danger', text: 'A form needs a name.' })
      return
    }
    try {
      await qualificationApi.createForm({
        name: name.trim(),
        intro: intro.trim() || null,
        room_id: roomId,
        dimensions,
      })
      setName('')
      setIntro('')
      setDimensions(DEFAULT_DIMENSIONS)
      setNotice({ tone: 'success', text: 'Form created as a draft. Publish it to show it to the buyer.' })
      refresh()
    } catch (error) {
      setNotice({ tone: 'danger', text: error.message })
    }
  }

  async function publish(formId) {
    setNotice(null)
    try {
      await qualificationApi.publishForm(formId)
      setNotice({ tone: 'success', text: 'Form published. The buyer now sees it in the room.' })
      refresh()
    } catch (error) {
      setNotice({ tone: 'danger', text: error.message })
    }
  }

  function toggleDimension(dimension) {
    setDimensions((current) =>
      current.includes(dimension)
        ? current.filter((item) => item !== dimension)
        : [...current, dimension],
    )
  }

  if (rooms.loading) return <Spinner label="Loading rooms" />
  if (rooms.error) return <ErrorNote error={rooms.error} onRetry={rooms.refetch} />

  const formEntries = forms.data?.entries || []
  const submissionEntries = submissions.data?.entries || []
  const bands = bandBreakdown(summary.data)

  return (
    <div className="space-y-6">
      <header>
        <h1 className="font-display text-2xl font-semibold text-foreground">Qualification capture</h1>
        <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
          Embed a short qualifying form inside a room, capture the buyer&apos;s answers without
          leaving it, and read the qualification band that routes the deal. The summary is visible
          to the seller only.
        </p>
      </header>

      <Card>
        <Field label="Room" id="room-select" hint="The form is embedded in the room the buyer is already in.">
          <select
            id="room-select"
            className={inputClass}
            value={roomId}
            onChange={(event) => setRoomId(event.target.value)}
          >
            {roomList.length === 0 && <option value="">No rooms yet</option>}
            {roomList.map((room) => (
              <option key={room.id} value={room.id}>
                {room.data?.name || room.id}
              </option>
            ))}
          </select>
        </Field>
      </Card>

      {summary.data && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard label="Submissions" value={summary.data.submissions} icon="audit" />
          <StatCard label="Qualified" value={summary.data.qualified} icon="dashboard" />
          <StatCard label="In progress" value={summary.data.in_progress} icon="refresh" />
          <StatCard label="Forms" value={summary.data.forms} icon="schema" />
        </div>
      )}

      {notice && <InlineNote tone={notice.tone}>{notice.text}</InlineNote>}

      <Section title="Buyer view">
        <BuyerPreview form={buyerForm} />
      </Section>

      <Section title="Bands">
        <ul className="grid gap-3 sm:grid-cols-3">
          {bands.map((entry) => (
            <li key={entry.band} className="rounded-sm border border-border-subtle bg-surface p-3">
              <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
                {entry.label}
              </p>
              <p className="mt-1 font-mono text-lg text-foreground">{entry.count}</p>
            </li>
          ))}
        </ul>
      </Section>

      <div className="grid gap-6 lg:grid-cols-2">
        <Section title="Forms">
          {forms.loading ? (
            <Spinner label="Loading forms" />
          ) : forms.error ? (
            <ErrorNote error={forms.error} onRetry={forms.refetch} />
          ) : formEntries.length === 0 ? (
            <EmptyState
              title="No form in this room yet"
              description="Create one below and publish it so the buyer sees it."
            />
          ) : (
            <ul className="flex flex-col gap-2">
              {formEntries.map((form) => (
                <FormRow key={form.id} form={form} selected={false} onSelect={() => {}} onPublish={publish} />
              ))}
            </ul>
          )}

          <form onSubmit={createForm} className="flex flex-col gap-3 border-t border-border-subtle pt-4">
            <Field label="Form name" id="form-name">
              <input
                id="form-name"
                className={inputClass}
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="Northwind qualification"
              />
            </Field>
            <Field label="Intro" id="form-intro" hint="One sentence the buyer reads above the questions.">
              <input
                id="form-intro"
                className={inputClass}
                value={intro}
                onChange={(event) => setIntro(event.target.value)}
              />
            </Field>
            <DimensionChecklist
              dimensions={DEFAULT_DIMENSIONS}
              selected={dimensions}
              onToggle={toggleDimension}
            />
            <Button type="submit" variant="primary" icon="plus">
              Create form
            </Button>
          </form>
        </Section>

        <Section title="Submissions">
          {submissions.loading ? (
            <Spinner label="Loading submissions" />
          ) : submissions.error ? (
            <ErrorNote error={submissions.error} onRetry={submissions.refetch} />
          ) : submissionEntries.length === 0 ? (
            <EmptyState
              title="No answers yet"
              description="Submissions appear here as buyers complete the form in the room."
            />
          ) : (
            <ul className="flex flex-col gap-2">
              {submissionEntries.map((submission) => (
                <SubmissionRow
                  key={submission.id}
                  submission={submission}
                  selected={selectedSubmissionId === submission.id}
                  onSelect={setSelectedSubmissionId}
                />
              ))}
            </ul>
          )}

          {selectedSubmissionId && detail.loading && <Spinner label="Loading submission" />}
          {selectedSubmissionId && detail.error && (
            <ErrorNote error={detail.error} onRetry={detail.refetch} />
          )}
          {selectedSubmissionId && detail.data && (
            <div className="border-t border-border-subtle pt-4">
              <SubmissionDetail submission={detail.data} questions={questions} />
            </div>
          )}
        </Section>
      </div>
    </div>
  )
}
