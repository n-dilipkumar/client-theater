/**
 * WF-126: asynchronous personalised video inside the room.
 *
 * The page is the seller's view of one room's video. It reads its closed
 * vocabularies from the server's `/vocabulary` route rather than repeating them,
 * so a panel cannot drift from the rules the engine enforces.
 *
 * Three things get deliberate treatment rather than a generic list row:
 *
 *   1. **The player is not drawn.** No object store exists in this build, so the
 *      page shows the media reference and the stand-in URL and says so. Drawing a
 *      working-looking player over a URL that never plays would be a lie about
 *      what shipped.
 *   2. **The low-completion alert is shown with its reason.** A rep who sees
 *      "alert" and not "watched 15 percent" learns nothing they can act on.
 *   3. **The buyer preview is a real call.** Selecting a buyer calls the same
 *      `/hero` route a buyer would reach, so what a seller sees is what the
 *      buyer receives, personalisation and all.
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
  eventLabel,
  formatDateTime,
  kindLabel,
  listRooms,
  percentText,
  placementLabel,
  sourceLabel,
  videoApi,
} from './api'
import {
  AlertRow,
  EngagementRow,
  EventLog,
  Fact,
  HeroPreview,
  InlineNote,
  NotificationRow,
  RecordingRow,
  Section,
  UserFlow,
  VIDEO_GLYPH,
  WordList,
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

function RecordForm({ vocabulary, roomId, onCreated }) {
  const [title, setTitle] = useState('')
  const [kind, setKind] = useState(vocabulary.default_kind || 'personal_intro')
  const [source, setSource] = useState(vocabulary.default_source || 'camera_and_screen')
  const [duration, setDuration] = useState('')
  const [note, setNote] = useState('')
  const { busy, error, note: noteText, run } = useAction(onCreated)

  function submit(event) {
    event.preventDefault()
    run(
      () =>
        videoApi.createRecording({
          title,
          room_id: roomId || undefined,
          kind,
          source,
          duration_seconds: duration ? Number(duration) : undefined,
          note: note || undefined,
        }),
      'The recording was saved and transcoded on save (a stand-in).',
    )
  }

  return (
    <Section title="Record a video">
      <form className="grid gap-4 sm:grid-cols-2" onSubmit={submit}>
        <Field label="Title" id="wf126-title" hint="What the buyer sees above the player.">
          <input
            id="wf126-title"
            className={inputClass}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
        </Field>
        <Field label="Kind" id="wf126-kind" hint="A rep's personal intro, or a product walkthrough.">
          <select
            id="wf126-kind"
            className={inputClass}
            value={kind}
            onChange={(e) => setKind(e.target.value)}
          >
            {(vocabulary.kinds || []).map((option) => (
              <option key={option} value={option}>
                {kindLabel(option)}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Source" id="wf126-source" hint="The recorder itself is a stand-in.">
          <select
            id="wf126-source"
            className={inputClass}
            value={source}
            onChange={(e) => setSource(e.target.value)}
          >
            {(vocabulary.capture_sources || []).map((option) => (
              <option key={option} value={option}>
                {sourceLabel(option)}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Length in seconds" id="wf126-duration" hint="Optional.">
          <input
            id="wf126-duration"
            type="number"
            min="0"
            className={inputClass}
            value={duration}
            onChange={(e) => setDuration(e.target.value)}
          />
        </Field>
        <div className="sm:col-span-2">
          <Field
            label="Why I am reaching out"
            id="wf126-note"
            hint="The buyer-visible message that lands before your call."
          >
            <textarea
              id="wf126-note"
              rows={2}
              className={`${inputClass} py-2`}
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
          </Field>
        </div>
        <div className="flex items-end gap-3 sm:col-span-2">
          <Button variant="primary" icon="plus" type="submit" disabled={busy || !roomId}>
            Save recording
          </Button>
          {!roomId && <span className="text-xs text-muted-foreground">Choose a room first.</span>}
        </div>
      </form>
      {noteText && <InlineNote tone="success">{noteText}</InlineNote>}
      {error && <ErrorNote error={error} />}
    </Section>
  )
}

function PlacePanel({ roomId, recording, onPlaced }) {
  const [note, setNote] = useState(recording.note || '')
  const [buyers, setBuyers] = useState('')
  const { busy, error, note: noteText, run } = useAction(onPlaced)

  function submit(event) {
    event.preventDefault()
    run(
      () =>
        videoApi.placeOnHero(roomId, recording.id, {
          note: note || undefined,
          personalised_for: buyers
            ? buyers
                .split(',')
                .map((part) => part.trim())
                .filter(Boolean)
            : undefined,
        }),
      'The video is on the hero, and each personalised buyer was notified.',
    )
  }

  return (
    <Section
      title={`Place ${recording.title}`}
      action={<Badge tone={recording.status === 'placed' ? 'insert' : 'neutral'}>{recording.status}</Badge>}
    >
      <form className="grid gap-4" onSubmit={submit}>
        <Field
          label="Buyers to personalise for"
          id="wf126-buyers"
          hint="Comma-separated emails. Leave empty to personalise for every buyer in the room."
        >
          <input
            id="wf126-buyers"
            className={inputClass}
            value={buyers}
            onChange={(e) => setBuyers(e.target.value)}
          />
        </Field>
        <Field label="Message on the hero" id="wf126-place-note" hint="Shown above the player.">
          <textarea
            id="wf126-place-note"
            rows={2}
            className={`${inputClass} py-2`}
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
        </Field>
        <div>
          <Button variant="primary" icon="plus" type="submit" disabled={busy}>
            Place on the hero
          </Button>
        </div>
      </form>
      <InlineNote tone="info">
        The hero holds one video. Placing this one replaces the previous placement, which stays on
        the record so earlier playback still resolves.
      </InlineNote>
      {noteText && <InlineNote tone="success">{noteText}</InlineNote>}
      {error && (
        <InlineNote tone="error">
          {error.reason ? conflictText(error.reason) : String(error.message || error)}
        </InlineNote>
      )}
    </Section>
  )
}

function BuyerPreview({ roomId, recipients, onRecorded }) {
  const [viewer, setViewer] = useState('')
  const [event, setEvent] = useState('start')
  const [percent, setPercent] = useState('')
  const { busy, error, note, run } = useAction(onRecorded)

  const hero = useAsync(
    () => (roomId && viewer ? videoApi.hero(roomId, viewer) : Promise.resolve(null)),
    [roomId, viewer],
  )

  function submit(event2) {
    event2.preventDefault()
    if (!viewer || !hero.data) return
    run(
      () =>
        videoApi.playback(hero.data.recording.id, {
          event,
          viewer,
          watched_percent: percent ? Number(percent) : undefined,
        }),
      'The playback event was recorded.',
    )
  }

  return (
    <Section
      title="Buyer preview"
      action={<span className="text-xs text-muted-foreground">The same call a buyer reaches</span>}
    >
      <Field label="Read as" id="wf126-viewer" hint="A recipient of this room, from WF-004.">
        <select
          id="wf126-viewer"
          className={inputClass}
          value={viewer}
          onChange={(e) => setViewer(e.target.value)}
        >
          <option value="">Choose a buyer</option>
          {recipients.map((recipient) => (
            <option key={recipient.email} value={recipient.email}>
              {recipient.email} ({recipient.role})
            </option>
          ))}
        </select>
      </Field>

      {viewer && hero.loading && <Spinner label="Reading the hero" />}
      {viewer && hero.error && <ErrorNote error={hero.error} onRetry={hero.refetch} />}
      {hero.data && <HeroPreview hero={hero.data} />}
      {hero.data && hero.data.engagement && (
        <InlineNote tone="info">
          {viewer} has watched {percentText(hero.data.engagement.max_percent)} of this video and{' '}
          {hero.data.engagement.completed ? 'finished it' : 'not finished it'}.
        </InlineNote>
      )}

      {hero.data && (
        <form className="grid gap-3 sm:grid-cols-3" onSubmit={submit}>
          <Field label="Event" id="wf126-event" hint="Stands in for the player.">
            <select
              id="wf126-event"
              className={inputClass}
              value={event}
              onChange={(e) => setEvent(e.target.value)}
            >
              {['start', 'progress', 'complete', 'replay'].map((option) => (
                <option key={option} value={option}>
                  {eventLabel(option)}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Percent watched" id="wf126-percent" hint="Required for progress.">
            <input
              id="wf126-percent"
              type="number"
              min="0"
              max="100"
              className={inputClass}
              value={percent}
              onChange={(e) => setPercent(e.target.value)}
            />
          </Field>
          <div className="flex items-end">
            <Button icon="plus" type="submit" disabled={busy}>
              Record event
            </Button>
          </div>
        </form>
      )}
      {note && <InlineNote tone="success">{note}</InlineNote>}
      {error && <ErrorNote error={error} />}
    </Section>
  )
}

function PersonalisedVideoPage() {
  const [roomId, setRoomId] = useState('')
  const [selected, setSelected] = useState('')
  const [nonce, setNonce] = useState(0)

  const vocabulary = useAsync(() => videoApi.vocabulary(), [])
  const rooms = useAsync(() => listRooms(), [])
  const summary = useAsync(() => videoApi.summary(roomId), [roomId, nonce])
  const recipients = useAsync(
    () => (roomId ? videoApi.recipients(roomId) : Promise.resolve({ entries: [] })),
    [roomId, nonce],
  )
  const recordings = useAsync(
    () => videoApi.recordings({ room_id: roomId }),
    [roomId, nonce],
  )
  const hero = useAsync(
    () => (roomId ? videoApi.hero(roomId) : Promise.resolve(null)),
    [roomId, nonce],
  )
  const placements = useAsync(
    () => (roomId ? videoApi.placements(roomId) : Promise.resolve({ entries: [] })),
    [roomId, nonce],
  )
  const events = useAsync(
    () => (roomId ? videoApi.events(roomId, selected || undefined) : Promise.resolve({ entries: [] })),
    [roomId, selected, nonce],
  )
  const engagement = useAsync(
    () => (roomId ? videoApi.engagement(roomId, selected || undefined) : Promise.resolve({ entries: [] })),
    [roomId, selected, nonce],
  )
  const alerts = useAsync(
    () => (roomId ? videoApi.alerts(roomId) : Promise.resolve({ entries: [] })),
    [roomId, nonce],
  )
  const notifications = useAsync(
    () => (roomId ? videoApi.notifications(roomId) : Promise.resolve({ entries: [] })),
    [roomId, nonce],
  )
  const detail = useAsync(
    () => (selected ? videoApi.recording(selected) : Promise.resolve(null)),
    [selected, nonce],
  )

  function refresh() {
    setNonce((value) => value + 1)
  }

  if (vocabulary.loading || rooms.loading) return <Spinner label="Loading personalised video" />
  if (vocabulary.error) return <ErrorNote error={vocabulary.error} onRetry={vocabulary.refetch} />
  if (recordings.error) return <ErrorNote error={recordings.error} onRetry={recordings.refetch} />

  const counts = summary.data || {}
  const roomOptions = rooms.data?.records || []
  const recipientEntries = recipients.data?.entries || []
  const recordingEntries = recordings.data?.entries || []
  const placementEntries = placements.data?.entries || []
  const eventEntries = events.data?.entries || []
  const engagementEntries = engagement.data?.entries || []
  const alertEntries = alerts.data?.entries || []
  const notificationEntries = notifications.data?.entries || []
  const words = vocabulary.data || {}

  return (
    <div className="space-y-6">
      <header className="flex items-start gap-3">
        <span className="rounded-sm bg-accent-soft p-2 text-accent">
          <Icon path={VIDEO_GLYPH} size={20} />
        </span>
        <div>
          <h1 className="font-display text-2xl font-semibold text-foreground">Personalised video</h1>
          <p className="text-sm text-muted-foreground">
            A rep records a personal intro, or a product team records a walkthrough, from inside the
            room. The video goes on the room&apos;s hero section, personalised per buyer, and the
            buyer&apos;s playback is recorded. No media is hosted here: the reference and the player
            URL are stand-ins.
          </p>
        </div>
      </header>

      <Card className="flex flex-wrap items-end gap-4">
        <Field label="Room" id="wf126-room" hint="Recordings and playback are room-scoped.">
          <select
            id="wf126-room"
            className={inputClass}
            value={roomId}
            onChange={(event) => {
              setRoomId(event.target.value)
              setSelected('')
            }}
          >
            <option value="">Choose a room</option>
            {roomOptions.map((room) => (
              <option key={room.id} value={room.id}>
                {room.data?.name || room.id}
              </option>
            ))}
          </select>
        </Field>
        <Fact label="Buyers" hint="Reused from WF-004">
          {recipientEntries.length}
        </Fact>
      </Card>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Recordings" value={counts.recordings ?? 0} icon="database" />
        <StatCard label="On the hero" value={counts.live_placements ?? 0} icon="dashboard" />
        <StatCard
          label="Viewers"
          value={counts.viewers ?? 0}
          hint={`${counts.completed ?? 0} completed`}
        />
        <StatCard
          label="Low completion"
          value={counts.low_completion_alerts ?? 0}
          hint={`average ${percentText(counts.average_watched_percent)}`}
        />
      </div>

      {roomId ? (
        <>
          <Section title="On the hero now" action={<Badge tone="insert">{hero.data?.surface || 'hero'}</Badge>}>
            {hero.loading && <Spinner label="Reading the hero" />}
            {hero.error && <ErrorNote error={hero.error} onRetry={hero.refetch} />}
            {!hero.loading && !hero.error && <HeroPreview hero={hero.data} />}
          </Section>

          <RecordForm vocabulary={words} roomId={roomId} onCreated={refresh} />

          <Section
            title="Recordings"
            action={<span className="text-xs text-muted-foreground">{recordingEntries.length} shown</span>}
          >
            {recordingEntries.length === 0 ? (
              <EmptyState
                title="No recordings"
                description="Record a video above. It appears here once saved."
              />
            ) : (
              <ul className="flex flex-col gap-2">
                {recordingEntries.map((recording) => (
                  <li key={recording.id}>
                    <RecordingRow
                      recording={recording}
                      selected={recording.id === selected}
                      onSelect={setSelected}
                    />
                  </li>
                ))}
              </ul>
            )}
          </Section>

          {selected && detail.loading && <Spinner label="Reading the recording" />}
          {selected && detail.error && <ErrorNote error={detail.error} onRetry={detail.refetch} />}
          {detail.data && <PlacePanel roomId={roomId} recording={detail.data} onPlaced={refresh} />}

          <BuyerPreview roomId={roomId} recipients={recipientEntries} onRecorded={refresh} />

          <Section
            title="Placement history"
            action={<span className="text-xs text-muted-foreground">{placementEntries.length}</span>}
          >
            {placementEntries.length === 0 ? (
              <p className="text-sm text-muted-foreground">No video has been placed yet.</p>
            ) : (
              <ul className="flex flex-col gap-2">
                {placementEntries.map((placement) => (
                  <li
                    key={placement.id}
                    className="flex flex-wrap items-center justify-between gap-2 rounded-sm border border-border-subtle bg-surface px-3 py-2"
                  >
                    <span className="flex min-w-0 flex-col">
                      <span className="truncate font-mono text-xs text-foreground">
                        {placement.recording_id}
                      </span>
                      <span className="text-xs text-muted-foreground">
                        {formatDateTime(placement.placed_at_ms)} &middot;{' '}
                        {placement.personalised_for.length} buyer(s)
                      </span>
                    </span>
                    <span className="flex items-center gap-2">
                      <Badge tone={placement.status === 'live' ? 'insert' : 'neutral'}>
                        {placementLabel(placement.status)}
                      </Badge>
                      {placement.status === 'live' && (
                        <Button
                          onClick={() =>
                            videoApi.withdrawPlacement(placement.id).then(refresh)
                          }
                        >
                          Withdraw
                        </Button>
                      )}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Section>

          <div className="grid gap-4 lg:grid-cols-2">
            <Section
              title="Viewer engagement"
              action={<span className="text-xs text-muted-foreground">{engagementEntries.length}</span>}
            >
              {engagementEntries.length === 0 ? (
                <p className="text-sm text-muted-foreground">No buyer has watched yet.</p>
              ) : (
                <ul className="flex flex-col gap-2">
                  {engagementEntries.map((row) => (
                    <EngagementRow key={row.id} row={row} />
                  ))}
                </ul>
              )}
            </Section>

            <Section
              title="Low-completion alerts"
              action={<span className="text-xs text-muted-foreground">{alertEntries.length}</span>}
            >
              {alertEntries.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  No buyer has stopped short of the end.
                </p>
              ) : (
                <ul className="flex flex-col gap-2">
                  {alertEntries.map((alert) => (
                    <AlertRow key={alert.id} alert={alert} />
                  ))}
                </ul>
              )}
            </Section>
          </div>

          <Section
            title="Playback log"
            action={<span className="text-xs text-muted-foreground">{eventEntries.length}</span>}
          >
            <EventLog events={eventEntries} />
          </Section>

          <Section
            title="Buyer notifications"
            action={<span className="text-xs text-muted-foreground">{notificationEntries.length}</span>}
          >
            {notificationEntries.length === 0 ? (
              <p className="text-sm text-muted-foreground">No buyer has been notified yet.</p>
            ) : (
              <ul className="flex flex-col gap-2">
                {notificationEntries.map((entry) => (
                  <NotificationRow key={entry.id} note={entry} />
                ))}
              </ul>
            )}
          </Section>
        </>
      ) : (
        <EmptyState
          title="Choose a room"
          description="Pick a room above to see its video, its buyers and their playback."
        />
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Section title="Automations this build owns">
          <WordList items={words.implemented_automations} />
        </Section>
        <Section title="Not implemented">
          <WordList items={words.unimplemented_automations} />
          <p className="text-sm text-muted-foreground">
            Auto-embed into new rooms from a template depends on the room template surface, which is
            another workflow&apos;s.
          </p>
        </Section>
      </div>

      <Section title="What the research left open">
        <WordList items={words.sourced_gaps} />
      </Section>

      <Section title="The researched user flow">
        <UserFlow steps={words.user_flow} />
      </Section>
    </div>
  )
}

export default {
  id: 'wf-126-personalised-video',
  label: 'Personalised video',
  icon: 'dashboard',
  iconPath: VIDEO_GLYPH,
  order: 126,
  Component: PersonalisedVideoPage,
}
