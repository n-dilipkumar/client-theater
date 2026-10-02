/**
 * Meeting changes: reschedule or cancel a meeting, and propagate it (WF-064).
 *
 * Four sections, in the order the researched flow happens in them.
 *
 * **Events History** first, because it is the artefact the research says the
 * workflow produces and the one a rep is asked about: "we will display who
 * rescheduled it, to whom, when, and the rescheduling source". Every row states
 * those four things, and each expands to the propagation it caused - the webhooks
 * pushed, the CRM event's fate, the calendar event, and the notices the workflow
 * trigger sent.
 *
 * **Bookings** is the surface a change is started from. Each row shows the two
 * links from the invite and whether each still works, which is how a rep learns
 * that a link is closed *before* telling an attendee to use it.
 *
 * **Make a change** runs any of the three researched intents against a real
 * room. `plan` comes first and writes nothing, so a rep can see the whole
 * propagation - the rows, the webhooks, the CRM decision - before committing, and
 * a refusal is visible before it is caused. The availability list is read with
 * `bookingUidToReschedule`, so the meeting's own time is on offer alongside every
 * genuinely free slot, and the list says which is which.
 *
 * **What this infers** is the research's own gaps made arguable. The research
 * never says where a link's expiry boundary is, whether a cancel link expires
 * with it, or what a request-to-reschedule pushes downstream - so those are
 * product behaviour, and a reviewer should be able to disagree with them by name.
 *
 * The pickers come from `/vocabulary`, never from a list compiled into this file,
 * so a team that widens a vocabulary ships a record rather than a change here.
 */

import { useEffect, useMemo, useState } from 'react'
import { absoluteTime, api, relativeTime } from '@/lib/api'
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
  inputClass,
  useAsync,
} from '@/components/ui'
import { meetingsApi } from './api'
import Glyphs from './icons'
import { ChangeChip, Fact, LinkChip, MoveSummary, Note } from './primitives'

const SECTIONS = [
  { id: 'history', label: 'Events History', glyph: 'reschedule' },
  { id: 'bookings', label: 'Bookings and links', glyph: 'clock' },
  { id: 'change', label: 'Make a change', glyph: 'fanout' },
  { id: 'inferences', label: 'What this infers', glyph: 'inference' },
]

function Stat({ label, value, hint, glyph }) {
  return (
    <Card className="card-hover">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{label}</p>
          <p className="mt-2 font-mono text-3xl font-semibold text-foreground">{value}</p>
          {hint && <p className="mt-1 truncate text-xs text-muted-foreground">{hint}</p>}
        </div>
        <span className="rounded-lg bg-muted p-2 text-accent">
          <Icon path={glyph} size={20} />
        </span>
      </div>
    </Card>
  )
}

/** One history row, expanding to the propagation it caused. */
function ChangeRow({ change, webhooks, notifications }) {
  const [open, setOpen] = useState(false)
  const data = change.data || {}
  const mine = webhooks.filter((hook) => hook.data?.change_id === change.id)
  const notices = notifications.filter((note) => note.data?.change_id === change.id)

  return (
    <li className="border-b border-border-subtle/15 last:border-0">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex w-full min-h-11 flex-wrap items-center gap-3 py-2 text-left
          transition-colors duration-150 hover:bg-muted/40"
      >
        <ChangeChip type={data.type} glyphs={Glyphs} />
        {data.reschedule_source && <Badge tone="neutral">{data.reschedule_source}</Badge>}
        <span className="min-w-0 flex-1 truncate text-[13px] text-foreground">
          {data.type === 'cancelled' ? (
            <span className="font-mono">{data.booking_uid}</span>
          ) : (
            <MoveSummary from={data.from} to={data.to} />
          )}
        </span>
        <span className="shrink-0 font-mono text-xs text-muted-foreground">
          {data.actor_email}
        </span>
        <span
          className="shrink-0 font-mono text-xs text-muted-foreground"
          title={absoluteTime(change.created_at)}
        >
          {relativeTime(change.created_at)}
        </span>
      </button>

      {open && (
        <div className="space-y-3 rounded-lg border border-border-subtle/25 bg-background/40 p-3">
          {/* The four things the research says Events History displays. Labelled by
              those words, so a reader can check the row against the sentence. */}
          <dl className="grid gap-2 sm:grid-cols-2">
            <Fact label="Who">{`${data.actor_email} (${data.actor_kind})`}</Fact>
            <Fact label="To whom">{data.to_host_email || data.to_attendee_email || '—'}</Fact>
            <Fact label="When">{data.at}</Fact>
            <Fact label="Rescheduling source">{data.reschedule_source || '—'}</Fact>
            <Fact label="Booking">{data.booking_uid || '—'}</Fact>
            <Fact label="Reschedule id">{data.reschedule_id ?? '—'}</Fact>
            <Fact label="Triggers">{(data.triggers || []).join(', ') || '—'}</Fact>
            <Fact label="Reminders">
              {data.reminders
                ? `${data.reminders.before} before, ${data.reminders.after} after`
                : '—'}
            </Fact>
          </dl>

          {data.cancellation_reason && (
            <p className="rounded-lg border border-border-subtle/25 bg-background/60 p-2 text-xs text-foreground/90">
              <span className="font-medium text-muted-foreground">Cancellation reason: </span>
              {data.cancellation_reason}
            </p>
          )}

          {data.crm_event && (
            <div>
              <p className="mb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
                CRM Event
              </p>
              <p className="font-mono text-[13px] text-foreground">
                {data.crm_event.sobject} · {data.crm_event.action}
                {data.crm_event.because ? ` · ${data.crm_event.because}` : ''}
              </p>
            </div>
          )}

          {data.calendar && (
            <div>
              <p className="mb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
                Calendar event
              </p>
              <p className="font-mono text-[13px] text-foreground">
                {data.calendar.action} · {data.calendar.event_id}
              </p>
            </div>
          )}

          {mine.length > 0 && (
            <div>
              <p className="mb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
                Webhooks pushed
              </p>
              <ul className="space-y-1">
                {mine.map((hook) => (
                  <li key={hook.id} className="flex items-center gap-2">
                    <Icon path={Glyphs.send} size={13} />
                    <span className="font-mono text-[13px] text-foreground">
                      {hook.data.webhook}
                    </span>
                    <Badge tone={hook.data.status === 'delivered' ? 'insert' : 'delete'}>
                      {hook.data.status}
                    </Badge>
                  </li>
                ))}
              </ul>
              <div className="mt-1">
                <JsonView value={mine.map((hook) => hook.data.payload)} />
              </div>
            </div>
          )}

          {notices.length > 0 && (
            <div>
              <p className="mb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
                Notices sent
              </p>
              <p className="font-mono text-[13px] text-foreground">
                {notices.map((note) => `${note.data.channel} → ${note.data.recipient}`).join(', ')}
              </p>
            </div>
          )}

          <div>
            <p className="mb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
              The row as stored
            </p>
            <JsonView value={data} />
          </div>
        </div>
      )}
    </li>
  )
}

/** One booking, with the two links from its invite and whether each still works. */
function BookingRow({ booking, links, onSelect, selected }) {
  const data = booking.data || {}
  const mine = links.filter((link) => link.booking_uid === data.uid)
  const live = data.status === 'booked'

  return (
    <li className="border-b border-border-subtle/15 last:border-0">
      <button
        type="button"
        onClick={() => onSelect(booking)}
        aria-pressed={selected}
        className={`flex w-full min-h-11 flex-wrap items-center gap-3 py-2 text-left
          transition-colors duration-150 ${selected ? 'bg-accent/10' : 'hover:bg-muted/40'}`}
      >
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm text-foreground">{data.title}</span>
          <span className="block font-mono text-xs text-muted-foreground">
            {data.start_at} · {data.attendee_email || 'no attendee on file'}
          </span>
        </span>
        {data.recurring_group && (
          <span className="flex items-center gap-1 text-xs text-muted-foreground">
            <Icon path={Glyphs.series} size={13} />
            occurrence {data.recurrence_index}
          </span>
        )}
        <Badge tone={live ? 'insert' : 'neutral'}>{data.status}</Badge>
        {mine.map((link) => (
          <LinkChip key={link.kind} state={link} glyphs={Glyphs} />
        ))}
      </button>
    </li>
  )
}

/** One inferred behaviour: what it is, why, and how to change it. */
function InferenceRow({ entry }) {
  const [open, setOpen] = useState(false)

  return (
    <li className="border-b border-border-subtle/15 last:border-0">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex w-full min-h-11 items-center gap-3 py-2 text-left transition-colors duration-150 hover:bg-muted/40"
      >
        <span className="min-w-0 flex-1 truncate text-[13px] text-foreground">{entry.topic}</span>
        <span className="shrink-0 font-mono text-[11px] text-muted-foreground">{entry.id}</span>
      </button>

      {open && (
        <div className="space-y-2 rounded-lg border border-border-subtle/25 bg-background/40 p-3 text-xs">
          <div>
            <p className="font-medium tracking-wide text-muted-foreground uppercase">
              What the research says
            </p>
            <p className="mt-0.5 text-foreground/90">{entry.basis}</p>
          </div>
          <div>
            <p className="font-medium tracking-wide text-muted-foreground uppercase">Why</p>
            <p className="mt-0.5 text-foreground/90">{entry.why}</p>
          </div>
          <div>
            <p className="font-medium tracking-wide text-muted-foreground uppercase">
              What this build chose
            </p>
            <JsonView value={entry.value} />
          </div>
          <div>
            <p className="font-medium tracking-wide text-muted-foreground uppercase">
              How to change it
            </p>
            <p className="mt-0.5 font-mono text-foreground/90">{entry.change_it}</p>
          </div>
          <div>
            <p className="font-medium tracking-wide text-muted-foreground uppercase">Affects</p>
            <p className="mt-0.5 text-foreground/90">{entry.blast_radius}</p>
          </div>
        </div>
      )}
    </li>
  )
}

export default function MeetingChanges() {
  const [section, setSection] = useState('history')
  const [type, setType] = useState('')
  const [roomId, setRoomId] = useState('')
  const [selected, setSelected] = useState('')
  const [notice, setNotice] = useState(null)
  const [noticeError, setNoticeError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [preview, setPreview] = useState(null)

  // The form
  const [uid, setUid] = useState('')
  const [intent, setIntent] = useState('reschedule')
  const [startAt, setStartAt] = useState('')
  const [reason, setReason] = useState('')
  const [scope, setScope] = useState('this')
  const [source, setSource] = useState('')
  const [viaLink, setViaLink] = useState('')

  const vocabulary = useAsync(() => meetingsApi.vocabulary(), [])
  const summary = useAsync(() => meetingsApi.summary(), [])
  const changes = useAsync(() => meetingsApi.listChanges({ change_type: type, limit: 60 }), [type])
  const bookings = useAsync(() => meetingsApi.listBookings({ limit: 60 }), [])
  const links = useAsync(() => meetingsApi.listLinks({ limit: 120 }), [])
  const webhooks = useAsync(() => meetingsApi.listWebhooks({ limit: 400 }), [])
  const notifications = useAsync(() => meetingsApi.listNotifications({ limit: 400 }), [])
  // Rooms are a core collection, not this feature's, so they come from the core
  // client's own reader. This feature's routes are reached through `meetingsApi`.
  const rooms = useAsync(() => api.listRecords('room', { limit: 100 }), [])
  const inferences = useAsync(() => meetingsApi.inferences(), [])

  const roomOptions = rooms.data || []

  // Default to the first room once the list arrives. An effect rather than a
  // setState during render, so React is not asked to re-render mid-render.
  useEffect(() => {
    if (!roomId && roomOptions.length > 0) setRoomId(roomOptions[0].id)
  }, [roomId, roomOptions])

  // The recomputed availability, with the researched release applied. Only read
  // once a booking is picked, because it is the booking being rescheduled whose
  // own time has to reappear.
  const availability = useAsync(
    () =>
      uid
        ? meetingsApi.availability({
            meeting_type_id: (bookings.data?.bookings || []).find(
              (booking) => booking.data?.uid === uid,
            )?.data?.meeting_type_id,
            booking_uid_to_reschedule: intent === 'reschedule' ? uid : undefined,
          })
        : Promise.resolve(null),
    [uid, intent, bookings.data],
  )

  const chosen = (bookings.data?.bookings || []).find((booking) => booking.data?.uid === uid)

  function payload() {
    const body = {}
    if (intent === 'cancel') {
      body.scope = scope
    } else {
      if (startAt) body.start_at = startAt
    }
    if (reason) body.reason = reason
    if (source) body.reschedule_source = source
    if (viaLink) body.link_token = viaLink
    return body
  }

  async function runPlan() {
    setBusy(true)
    setNoticeError(null)
    setPreview(null)
    try {
      setPreview(await meetingsApi.plan(roomId, uid, { intent, ...payload() }))
    } catch (error) {
      setNoticeError(error)
    } finally {
      setBusy(false)
    }
  }

  async function runChange() {
    setBusy(true)
    setNoticeError(null)
    setNotice(null)
    try {
      let result
      if (intent === 'reschedule') {
        result = await meetingsApi.reschedule(roomId, uid, payload())
        setNotice(`Rescheduled. The new booking is ${result.data.new_booking_uid}.`)
      } else if (intent === 'request_reschedule') {
        result = await meetingsApi.requestReschedule(roomId, uid, payload())
        setNotice('The booking is released and the attendee has a link to pick a new time.')
      } else {
        result = await meetingsApi.cancel(roomId, uid, payload())
        setNotice(
          result.sweep
            ? `Cancelled ${result.sweep.count} occurrences of the series.`
            : 'Cancelled, and propagated downstream.',
        )
      }
      setPreview(null)
      changes.refetch()
      summary.refetch()
      bookings.refetch()
      links.refetch()
      webhooks.refetch()
      notifications.refetch()
    } catch (error) {
      setNoticeError(error)
    } finally {
      setBusy(false)
    }
  }

  const stats = useMemo(() => {
    const byType = summary.data?.by_type || {}
    const bySource = summary.data?.by_reschedule_source || {}
    return [
      {
        label: 'Changes',
        value: summary.data?.changes ?? 0,
        hint: `${byType.rescheduled ?? 0} rescheduled, ${byType.cancelled ?? 0} cancelled`,
        glyph: Glyphs.reschedule,
      },
      {
        label: 'Live meetings',
        value: summary.data?.live_bookings ?? 0,
        hint: `of ${summary.data?.bookings ?? 0} booked`,
        glyph: Glyphs.clock,
      },
      {
        label: 'Webhooks pushed',
        value: summary.data?.webhooks_pushed ?? 0,
        hint: `${summary.data?.crm_events_deleted ?? 0} CRM events deleted`,
        glyph: Glyphs.send,
      },
      {
        label: 'From a link',
        value: (bySource.reschedule_link ?? 0) + (bySource.calendar_event ?? 0),
        hint: `${bySource.reschedule_link ?? 0} attendee, ${bySource.calendar_event ?? 0} calendar`,
        glyph: Glyphs.open,
      },
    ]
  }, [summary.data])

  // Every list endpoint in this feature answers with an envelope, so the rows are
  // read off the envelope key here in one place rather than at each use - the
  // single easiest mistake to make against a schema-flexible API, and one that
  // renders as an empty page rather than an error.
  const changeRows = changes.data?.changes || []
  const bookingRows = bookings.data?.bookings || []
  const linkRows = links.data?.links || []
  const webhookRows = webhooks.data?.webhooks || []
  const noticeRows = notifications.data?.notifications || []
  const slots = availability.data?.slots || []
  const canAct = Boolean(roomId) && Boolean(uid)

  return (
    <div className="space-y-5">
      <header>
        <h1 className="font-mono text-2xl font-semibold">Meeting changes</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Move or release a booked meeting from the link in the invite or the host&rsquo;s panel,
          then propagate it: the calendar event moves, the CRM Event is updated or deleted per
          the Meeting Type, the webhooks go out, and Events History records who, to whom, when,
          and from which source.
        </p>
      </header>

      {noticeError && <ErrorNote error={noticeError} onRetry={runPlan} />}
      {notice && <Note>{notice}</Note>}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {stats.map((stat) => (
          <Stat key={stat.label} {...stat} />
        ))}
      </div>

      {/* Section switcher. A tablist, so the arrow-key semantics and the aria
          relationship are right and the choice can be shared by URL. */}
      <div role="tablist" aria-label="Meeting change sections" className="flex flex-wrap gap-2">
        {SECTIONS.map((item) => (
          <button
            key={item.id}
            type="button"
            role="tab"
            id={`tab-${item.id}`}
            aria-selected={section === item.id}
            aria-controls={`panel-${item.id}`}
            onClick={() => setSection(item.id)}
            className={`inline-flex min-h-11 items-center gap-2 rounded-lg px-4 text-sm
              transition-colors duration-200 ${
                section === item.id
                  ? 'bg-accent/15 font-medium text-accent'
                  : 'bg-muted text-muted-foreground hover:border-border-subtle hover:text-foreground'
              }`}
          >
            <Icon path={item.glyph} size={16} />
            {item.label}
          </button>
        ))}
      </div>

      {/* -- events history -- */}
      {section === 'history' && (
        <div id="panel-history" role="tabpanel" aria-labelledby="tab-history" className="space-y-4">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <h2 className="font-mono text-lg font-semibold">Events History</h2>
              <p className="text-sm text-muted-foreground">
                Every change, showing who made it, to whom, when, and the rescheduling source.
                Select a row for the webhooks it pushed, the CRM event&rsquo;s fate, and the
                notices the workflow trigger sent.
              </p>
            </div>
            <div className="flex flex-wrap items-end gap-3">
              <Field label="Change type" id="filter-type">
                <select
                  id="filter-type"
                  className={inputClass}
                  value={type}
                  onChange={(event) => setType(event.target.value)}
                >
                  <option value="">All</option>
                  {(vocabulary.data?.change_type_names || []).map((name) => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ))}
                </select>
              </Field>
              <Button icon="refresh" onClick={changes.refetch}>
                Refresh
              </Button>
            </div>
          </div>

          {changes.loading && <Spinner label="Loading Events History" />}
          {changes.error && <ErrorNote error={changes.error} onRetry={changes.refetch} />}

          {!changes.loading && !changes.error && changeRows.length === 0 && (
            <EmptyState
              title="No changes yet"
              description="A meeting moved or released through this page appears here, with the propagation it caused."
            />
          )}

          {changeRows.length > 0 && (
            <Card className="p-4">
              <ul>
                {changeRows.map((change) => (
                  <ChangeRow
                    key={change.id}
                    change={change}
                    webhooks={webhookRows}
                    notifications={noticeRows}
                  />
                ))}
              </ul>
            </Card>
          )}

          <section aria-labelledby="crm-heading" className="space-y-3">
            <h3 id="crm-heading" className="font-mono text-base font-semibold">
              The CRM events
            </h3>
            <p className="text-sm text-muted-foreground">
              A cancelled meeting&rsquo;s Event is deleted or kept according to the Meeting
              Type&rsquo;s <span className="font-mono">Delete Event</span> setting, so both
              outcomes have to be visible.
            </p>
            <CrmTable />
          </section>
        </div>
      )}

      {/* -- bookings and links -- */}
      {section === 'bookings' && (
        <div
          id="panel-bookings"
          role="tabpanel"
          aria-labelledby="tab-bookings"
          className="space-y-4"
        >
          <div>
            <h2 className="font-mono text-lg font-semibold">Bookings and links</h2>
            <p className="text-sm text-muted-foreground">
              Each booking carries the two links Chili Piper injects into the invite. A reschedule
              link closes once the meeting has happened if the Meeting Type says so; a cancel link
              is gated on the booking still being live. Select a row to act on it.
            </p>
          </div>

          {bookings.loading && <Spinner label="Loading bookings" />}
          {bookings.error && <ErrorNote error={bookings.error} onRetry={bookings.refetch} />}

          {!bookings.loading && !bookings.error && bookingRows.length === 0 && (
            <EmptyState
              title="No bookings yet"
              description="Register a booked meeting to reschedule or cancel it."
            />
          )}

          {bookingRows.length > 0 && (
            <Card className="p-4">
              <ul>
                {bookingRows.map((booking) => (
                  <BookingRow
                    key={booking.id}
                    booking={booking}
                    links={linkRows}
                    selected={selected === booking.id}
                    onSelect={(picked) => {
                      setSelected(picked.id)
                      setUid(picked.data.uid)
                      setViaLink('')
                      setStartAt('')
                      setPreview(null)
                    }}
                  />
                ))}
              </ul>
            </Card>
          )}

          {uid && (
            <InvitePanel uid={uid} onUseToken={(token) => setViaLink(token)} viaLink={viaLink} />
          )}
        </div>
      )}

      {/* -- make a change -- */}
      {section === 'change' && (
        <div id="panel-change" role="tabpanel" aria-labelledby="tab-change" className="space-y-4">
          <div>
            <h2 className="font-mono text-lg font-semibold">Make a change</h2>
            <p className="text-sm text-muted-foreground">
              Plan writes nothing and shares every rule with the writes, so the answer is the
              answer the change will give &mdash; including a refusal, which comes back as the same
              error. The availability list is read with the booking&rsquo;s own time released, so it
              can be moved to the time it is already at.
            </p>
          </div>

          {rooms.loading && <Spinner label="Loading rooms" />}

          <Card>
            <form
              className="space-y-4"
              onSubmit={(event) => {
                event.preventDefault()
                runPlan()
              }}
            >
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Room" id="change-room" hint="The change is recorded against this room.">
                  <select
                    id="change-room"
                    className={inputClass}
                    required
                    value={roomId}
                    onChange={(event) => setRoomId(event.target.value)}
                  >
                    <option value="">Choose a room</option>
                    {roomOptions.map((room) => (
                      <option key={room.id} value={room.id}>
                        {room.data?.name || room.id}
                      </option>
                    ))}
                  </select>
                </Field>

                <Field label="Meeting" id="change-booking">
                  <select
                    id="change-booking"
                    className={inputClass}
                    required
                    value={uid}
                    onChange={(event) => {
                      setUid(event.target.value)
                      setPreview(null)
                    }}
                  >
                    <option value="">Choose a booking</option>
                    {bookingRows
                      .filter((booking) => booking.data?.status === 'booked')
                      .map((booking) => (
                        <option key={booking.id} value={booking.data.uid}>
                          {booking.data.title} &mdash; {booking.data.start_at}
                        </option>
                      ))}
                  </select>
                </Field>
              </div>

              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Intent" id="change-intent" hint="The three researched operations.">
                  <select
                    id="change-intent"
                    className={inputClass}
                    value={intent}
                    onChange={(event) => setIntent(event.target.value)}
                  >
                    {(vocabulary.data?.intents || []).map((entry) => (
                      <option key={entry.intent} value={entry.intent}>
                        {entry.label}
                      </option>
                    ))}
                  </select>
                </Field>

                {intent === 'cancel' ? (
                  <Field
                    label="Scope"
                    id="change-scope"
                    hint="One occurrence, or every remaining one in the series."
                  >
                    <select
                      id="change-scope"
                      className={inputClass}
                      value={scope}
                      onChange={(event) => setScope(event.target.value)}
                    >
                      {(vocabulary.data?.cancel_scopes || []).map((entry) => (
                        <option key={entry.scope} value={entry.scope}>
                          {entry.label}
                        </option>
                      ))}
                    </select>
                  </Field>
                ) : (
                  <Field
                    label="New time"
                    id="change-start"
                    hint="Leave blank and plan will use the meeting's own time, which is on offer."
                  >
                    <input
                      id="change-start"
                      className={inputClass}
                      type="datetime-local"
                      value={startAt}
                      onChange={(event) => setStartAt(event.target.value)}
                    />
                  </Field>
                )}
              </div>

              <div className="grid gap-4 sm:grid-cols-2">
                <Field
                  label="Reason"
                  id="change-reason"
                  hint="Optional, and shipped in the webhook so churn can be spotted."
                >
                  <input
                    id="change-reason"
                    className={inputClass}
                    value={reason}
                    placeholder="I am no longer able to attend this session."
                    onChange={(event) => setReason(event.target.value)}
                  />
                </Field>

                <Field
                  label="Rescheduling source"
                  id="change-source"
                  hint="Left blank, a change through a link is the attendee's; otherwise the host panel's."
                >
                  <select
                    id="change-source"
                    className={inputClass}
                    value={source}
                    onChange={(event) => setSource(event.target.value)}
                  >
                    <option value="">Infer from the link</option>
                    {(vocabulary.data?.reschedule_sources || []).map((entry) => (
                      <option key={entry.source} value={entry.source}>
                        {entry.label}
                      </option>
                    ))}
                  </select>
                </Field>
              </div>

              <div className="flex flex-wrap gap-2">
                <Button type="submit" disabled={busy || !canAct}>
                  {busy ? 'Planning…' : 'Plan (writes nothing)'}
                </Button>
                <Button
                  variant="primary"
                  icon={intent === 'cancel' ? 'trash' : 'refresh'}
                  disabled={busy || !canAct}
                  onClick={runChange}
                >
                  {intent === 'reschedule'
                    ? 'Reschedule now'
                    : intent === 'request_reschedule'
                      ? 'Ask for a new time'
                      : 'Cancel'}
                </Button>
              </div>
            </form>
          </Card>

          {/* The recomputed availability, with the researched release stated. */}
          {uid && (
            <Card className="space-y-2">
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="font-mono text-base font-semibold">Recomputed availability</h3>
                {intent === 'reschedule' && <Badge tone="neutral">bookingUidToReschedule set</Badge>}
              </div>
              {availability.loading && <Spinner label="Recomputing availability" />}
              {availability.error && (
                <ErrorNote error={availability.error} onRetry={availability.refetch} />
              )}
              {availability.data && (
                <>
                  <p className="text-sm text-muted-foreground">
                    {availability.data.count} slots on offer
                    {intent === 'reschedule'
                      ? ', including the meeting’s own time, which the researched parameter puts back.'
                      : '.'}
                  </p>
                  <ul className="flex flex-wrap gap-2">
                    {slots.slice(0, 24).map((slot) => (
                      <li key={slot.start_at}>
                        <button
                          type="button"
                          onClick={() => setStartAt(slot.start_at.slice(0, 16))}
                          className={`min-h-11 rounded-lg border px-2.5 font-mono text-xs
                            transition-colors duration-150 ${
                              slot.original_slot
                                ? 'border-accent/40 bg-accent/15 text-accent'
                                : slot.in_past
                                  ? 'border-border-subtle/40 bg-muted/40 text-muted-foreground'
                                  : 'border-border-subtle/40 bg-muted text-foreground hover:bg-border-subtle'
                            }`}
                        >
                          {slot.start_at.slice(5, 16)}
                          {slot.original_slot ? ' · own time' : ''}
                        </button>
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </Card>
          )}

          {chosen && (
            <Card className="space-y-2">
              <h3 className="font-mono text-base font-semibold">The meeting as booked</h3>
              <dl className="grid gap-2 sm:grid-cols-2">
                <Fact label="uid">{chosen.data.uid}</Fact>
                <Fact label="status">{chosen.data.status}</Fact>
                <Fact label="starts">{chosen.data.start_at}</Fact>
                <Fact label="ends">{chosen.data.end_at}</Fact>
                <Fact label="host">{chosen.data.host_email}</Fact>
                <Fact label="attendee">{chosen.data.attendee_email || '—'}</Fact>
                <Fact label="location">{chosen.data.location || '—'}</Fact>
                <Fact label="reminders">{(chosen.data.reminders || []).length}</Fact>
              </dl>
            </Card>
          )}

          {preview && (
            <Card className="space-y-3">
              <div className="flex flex-wrap items-center gap-3">
                <h3 className="font-mono text-base font-semibold">What would happen</h3>
                <ChangeChip type={preview.change_type} glyphs={Glyphs} />
                {preview.reschedule_source && <Badge tone="neutral">{preview.reschedule_source}</Badge>}
              </div>
              {preview.to && <MoveSummary from={preview.from} to={preview.to} />}
              <dl className="grid gap-2 sm:grid-cols-2">
                <Fact label="Who">
                  {preview.actor_email ? `${preview.actor_email} (${preview.actor_kind})` : '—'}
                </Fact>
                <Fact label="Reschedule id">{preview.reschedule_id ?? '—'}</Fact>
                <Fact label="Webhooks">{(preview.webhooks || []).join(', ')}</Fact>
                <Fact label="Triggers">{(preview.triggers || []).join(', ') || '—'}</Fact>
                <Fact label="CRM sobject">{preview.crm_sobject}</Fact>
                {preview.scope && <Fact label="Scope">{preview.scope}</Fact>}
                {preview.delete_event !== undefined && (
                  <Fact label="Delete Event">{preview.delete_event ? 'on' : 'off'}</Fact>
                )}
                {preview.targets && <Fact label="Occurrences">{preview.targets.join(', ')}</Fact>}
              </dl>
              {preview.link && <LinkChip state={preview.link} glyphs={Glyphs} />}
              <p className="text-sm text-muted-foreground">{preview.writes}</p>

              {/* A request-to-reschedule has two effects, and the cancellation is the
                  destructive one. Showing only the plan's own `writes` would hide it. */}
              {preview.also && (
                <div className="space-y-2 rounded-lg border border-border-subtle/25 bg-background/40 p-3">
                  <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
                    And it also cancels the booking
                  </p>
                  <div className="flex flex-wrap items-center gap-2">
                    <ChangeChip type={preview.also.change_type} glyphs={Glyphs} />
                    <Badge tone="neutral">{preview.also.cause}</Badge>
                    <Badge tone={preview.also.delete_event ? 'delete' : 'neutral'}>
                      Delete Event {preview.also.delete_event ? 'on' : 'off'}
                    </Badge>
                  </div>
                  <p className="font-mono text-[13px] text-foreground">
                    {(preview.also.webhooks || []).join(', ')}
                  </p>
                </div>
              )}
            </Card>
          )}
        </div>
      )}

      {/* -- what this infers -- */}
      {section === 'inferences' && (
        <div
          id="panel-inferences"
          role="tabpanel"
          aria-labelledby="tab-inferences"
          className="space-y-4"
        >
          <div>
            <h2 className="font-mono text-lg font-semibold">What this infers</h2>
            <p className="text-sm text-muted-foreground">
              The two invite tags, the expiry setting, the <span className="font-mono">Delete Event</span>{' '}
              toggle, the reschedule chain, both webhook payloads and the three rescheduling sources
              are all sourced. The rest is judgement: the parts of this workflow the research makes
              no claims about. Each is listed here with the reason for the choice and how to change
              it.
            </p>
          </div>

          {inferences.loading && <Spinner label="Loading inferences" />}
          {inferences.error && <ErrorNote error={inferences.error} onRetry={inferences.refetch} />}

          {inferences.data && (
            <>
              <Card className="p-4">
                <p className="text-xs leading-relaxed text-muted-foreground">
                  <span className="font-medium text-foreground">From the research: </span>
                  &ldquo;{inferences.data.sourced_quote}&rdquo;
                </p>
              </Card>

              <Card className="p-4">
                <ul>
                  {(inferences.data.inferences || []).map((entry) => (
                    <InferenceRow key={entry.id} entry={entry} />
                  ))}
                </ul>
              </Card>
            </>
          )}
        </div>
      )}
    </div>
  )
}

/** The invite body, with the two researched tags resolved and copyable. */
function InvitePanel({ uid, onUseToken, viaLink }) {
  const invite = useAsync(() => meetingsApi.invite(uid), [uid])

  if (invite.loading) return <Spinner label="Loading the invite" />
  if (invite.error) return <ErrorNote error={invite.error} onRetry={invite.refetch} />
  if (!invite.data) return null

  return (
    <Card className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="font-mono text-base font-semibold">The invite as the attendee sees it</h3>
        {Object.entries(invite.data.links || {}).map(([kind, state]) => (
          <LinkChip key={kind} state={{ ...state, kind }} glyphs={Glyphs} />
        ))}
      </div>
      <p className="text-sm text-muted-foreground">
        Both researched dynamic tags, resolved against this origin so the URLs are openable.
      </p>
      <pre className="overflow-x-auto rounded-lg border border-border-subtle/25 bg-background/60 p-3 font-mono text-xs whitespace-pre-wrap text-foreground">
        {invite.data.description}
      </pre>
      <div className="flex flex-wrap gap-2">
        {(invite.data.links?.reschedule?.expired === false ||
          invite.data.links?.cancel?.expired === false) && (
          <Button
            onClick={() => onUseToken(invite.data.links.reschedule?.token || '')}
            disabled={!invite.data.links.reschedule?.token}
          >
            Reschedule through this link
          </Button>
        )}
        {viaLink && <Badge tone="insert">using link {viaLink.slice(0, 8)}</Badge>}
      </div>
    </Card>
  )
}

/** The CRM Event rows, including the ones Delete Event removed. */
function CrmTable() {
  const rows = useAsync(() => meetingsApi.listCrmEvents(), [])

  if (rows.loading) return <Spinner label="Loading CRM events" />
  if (rows.error) return <ErrorNote error={rows.error} onRetry={rows.refetch} />

  const events = rows.data?.crm_events || []
  if (events.length === 0) {
    return (
      <EmptyState
        title="No CRM events yet"
        description="A booking is given a CRM Event the first time it is rescheduled or cancelled."
      />
    )
  }

  return (
    <div className="overflow-x-auto">
      <Card>
        <table className="w-full min-w-[640px] text-left text-sm">
          <caption className="sr-only">
            CRM Event rows, their status, and which change last touched them
          </caption>
          <thead>
            <tr className="border-b border-border-subtle/30 text-xs tracking-wide text-muted-foreground uppercase">
              <th scope="col" className="px-4 py-3 font-medium">Booking</th>
              <th scope="col" className="px-4 py-3 font-medium">Subject</th>
              <th scope="col" className="px-4 py-3 font-medium">Starts</th>
              <th scope="col" className="px-4 py-3 font-medium">Status</th>
            </tr>
          </thead>
          <tbody>
            {events.map((event) => (
              <tr key={event.id} className="border-b border-border-subtle/15 last:border-0">
                <td className="px-4 py-3">
                  <span className="flex items-center gap-2">
                    <Icon path={Glyphs.crm} size={14} />
                    <span className="truncate font-mono text-[13px] text-foreground">
                      {event.data?.booking_uid}
                    </span>
                  </span>
                </td>
                <td className="px-4 py-3 text-foreground">{event.data?.subject}</td>
                <td className="px-4 py-3 font-mono text-[13px] text-foreground">
                  {event.data?.start_at}
                </td>
                <td className="px-4 py-3">
                  <Badge
                    tone={
                      event.data?.status === 'deleted'
                        ? 'delete'
                        : event.data?.status === 'cancelled'
                          ? 'neutral'
                          : 'insert'
                    }
                  >
                    {event.data?.status}
                  </Badge>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  )
}
