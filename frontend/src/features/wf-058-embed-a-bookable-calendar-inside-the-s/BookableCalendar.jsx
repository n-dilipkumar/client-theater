/**
 * Bookable calendar: embed a bookable calendar inside the sales room (WF-058).
 *
 * Five sections, in the order the researched flow happens in them.
 *
 * **Embed** first, because everything else is downstream of it. Step 5 of the
 * research is that "the prospect books entirely in-room; no Chili-Piper-like
 * external page is shown", which is a claim about the room's own embed: if it
 * names no event type, or its OAuth client has no live token, the room cannot
 * book at all, and the page says so before the rest of it pretends otherwise.
 *
 * **The grid** is the researched availability answer, rendered as it is built:
 * every candidate slot, each labelled with *why* it is unavailable when it is.
 * The four reasons - the host is busy, somebody holds it, the seats are gone, not
 * enough of the named people are free - are different problems, and a prospect
 * choosing between tomorrow and next week needs to tell them apart.
 *
 * **Holds** shows the researched auto-expiry rather than asserting it. A hold
 * whose row still says `held` after its `reservationUntil` is reported as expired
 * with the stored state beside it, and that divergence is the automation: no job
 * ran to make it so.
 *
 * **Bookings** is the write path, and the form carries the three researched knobs
 * that a page can actually get wrong: the recurrence count (refused above 32, not
 * truncated), the instant flag (team events only), and the reschedule reference
 * (which excludes the booking's own slot from busy time).
 *
 * **What this infers** is where the research's gaps went. WF-058 is a build, so
 * most of what is on this page is a decision somebody made rather than a sentence
 * somebody quoted, and a reviewer should be able to disagree with one by name.
 */

import { useEffect, useMemo, useState } from 'react'
import { api, absoluteTime, relativeTime } from '@/lib/api'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Field,
  Icon,
  JsonView,
  Spinner,
  inputClass,
  useAsync,
} from '@/components/ui'
import { calendarApi } from './api'
import Glyphs from './icons'
import { Fact, Note, Refusal, SlotChip, StateChip } from './primitives'

const SECTIONS = [
  { id: 'embed', label: 'Embed', glyph: 'calendar' },
  { id: 'grid', label: 'The grid', glyph: 'grid' },
  { id: 'holds', label: 'Holds', glyph: 'hold' },
  { id: 'bookings', label: 'Bookings', glyph: 'booked' },
  { id: 'routing', label: 'Routing', glyph: 'routing' },
  { id: 'inferences', label: 'What this infers', glyph: 'inference' },
]

/** The default duration the research documents, shown so the number is visible. */
const DEFAULT_HOLD_MINUTES = 5

function Stat({ label, value, hint, glyph }) {
  return (
    <Card className="card-hover flex h-full items-start justify-between gap-3">
      <div className="min-w-0">
        <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{label}</p>
        <p className="mt-2 font-mono text-3xl font-semibold text-foreground">{value}</p>
        {hint && <p className="mt-1 truncate text-xs text-muted-foreground">{hint}</p>}
      </div>
      <span className="rounded-lg bg-muted p-2 text-accent">
        <Icon path={glyph} size={20} />
      </span>
    </Card>
  )
}

function SectionHeading({ title, children, aside }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div>
        <h2 className="font-mono text-lg font-semibold">{title}</h2>
        {children && <p className="text-sm text-muted-foreground">{children}</p>}
      </div>
      {aside}
    </div>
  )
}

/** One hold row, with the stored state beside the state the clock implies. */
function HoldRow({ hold, onExtend, onRelease, busy }) {
  const diverged = hold.stored_state && hold.stored_state !== hold.state
  return (
    <li className="border-b border-border-subtle/15 py-3 last:border-0">
      <div className="flex flex-wrap items-center gap-3">
        <StateChip state={hold.state} glyph={Glyphs.hold} />
        <span className="font-mono text-[13px] text-foreground">{absoluteTime(hold.start)}</span>
        <span className="font-mono text-xs text-muted-foreground">
          until {absoluteTime(hold.reservationUntil)}
        </span>
        <Badge tone="neutral">{hold.reservationDuration} min</Badge>
        {diverged && (
          <span className="text-xs text-amber-300">
            <Icon path={Glyphs.hold} size={13} /> row still says {hold.stored_state}
          </span>
        )}
        {hold.state === 'held' && (
          <span className="ml-auto flex gap-2">
            <Button onClick={() => onExtend(hold)} disabled={busy}>
              Extend
            </Button>
            <Button variant="danger" onClick={() => onRelease(hold)} disabled={busy}>
              Release
            </Button>
          </span>
        )}
      </div>
      {hold.attendee?.email && (
        <p className="mt-1 text-xs text-muted-foreground">
          held for {hold.attendee.name} &lt;{hold.attendee.email}&gt;
        </p>
      )}
    </li>
  )
}

/** One booking row, expanding to the researched payload it stored. */
function BookingRow({ booking, onCancel, onReschedule, busy }) {
  const [open, setOpen] = useState(false)
  const data = booking.data || booking
  const kindGlyph =
    data.kind === 'instant'
      ? Glyphs.instant
      : data.kind === 'recurring'
        ? Glyphs.recurring
        : Glyphs.booked

  return (
    <li className="border-b border-border-subtle/15 last:border-0">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex min-h-11 w-full flex-wrap items-center gap-3 py-2 text-left transition-colors duration-150 hover:bg-muted/40"
      >
        <Icon path={kindGlyph} size={16} className="text-accent" />
        <StateChip state={data.status} />
        <Badge tone="neutral">{data.kind}</Badge>
        <span className="min-w-0 flex-1 truncate font-mono text-[13px] text-foreground">
          {absoluteTime(data.start)}
        </span>
        <span className="truncate text-xs text-muted-foreground">
          {data.attendee?.name} &lt;{data.attendee?.email}&gt;
        </span>
        {data.moved_from && (
          <span className="flex items-center gap-1 text-xs text-sky-300">
            <Icon path={Glyphs.reschedule} size={13} />
            rescheduled
          </span>
        )}
        {data.held && (
          <span className="flex items-center gap-1 text-xs text-muted-foreground">
            <Icon path={Glyphs.hold} size={13} />
            from a hold
          </span>
        )}
        {data.video && (
          <a
            href={data.video}
            target="_blank"
            rel="noreferrer"
            onClick={(event) => event.stopPropagation()}
            className="inline-flex min-h-11 items-center gap-1 text-xs text-accent hover:underline"
          >
            <Icon path={Glyphs.video} size={13} />
            Join
          </a>
        )}
        <span className="shrink-0 font-mono text-xs text-muted-foreground" title={absoluteTime(data.booked_at)}>
          {relativeTime(data.booked_at)}
        </span>
      </button>

      {open && (
        <div className="space-y-3 rounded-lg border border-border-subtle/25 bg-background/40 p-3">
          <dl className="grid gap-2 sm:grid-cols-2">
            <Fact label="uid">{data.uid}</Fact>
            <Fact label="eventTypeId">{data.eventTypeId}</Fact>
            <Fact label="Start">{absoluteTime(data.start)}</Fact>
            <Fact label="End">{absoluteTime(data.end)}</Fact>
            <Fact label="Host">{data.host}</Fact>
            <Fact label="Location">{data.location}</Fact>
            <Fact label="Held">{data.held ? 'yes' : 'no'}</Fact>
            {data.moved_from && <Fact label="Moved from">{absoluteTime(data.moved_from)}</Fact>}
          </dl>

          {data.recurrence && (
            <div>
              <p className="mb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
                Recurrence
              </p>
              <p className="font-mono text-[13px] text-foreground">
                {data.recurrence.count} occurrences, {data.recurrence.interval}
              </p>
            </div>
          )}

          <div>
            <p className="mb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
              Booking fields
            </p>
            <JsonView value={data.bookingFieldsResponses} />
          </div>

          <div>
            <p className="mb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
              Metadata
            </p>
            <p className="text-xs text-muted-foreground">
              50 keys, 40 characters per key, 500 per value. This booking carries the room context as
              well as what the caller sent.
            </p>
            <JsonView value={data.metadata} />
          </div>

          {data.status === 'confirmed' && (
            <div className="flex gap-2">
              <Button onClick={() => onReschedule(data)} disabled={busy}>
                Reschedule this
              </Button>
              <Button variant="danger" onClick={() => onCancel(data)} disabled={busy}>
                Cancel
              </Button>
            </div>
          )}
        </div>
      )}
    </li>
  )
}

/** A labelled checkbox, for the booking form's own switches. */
function Switch({ label, hint, checked, onChange, disabled }) {
  return (
    <label
      className={`flex min-h-11 items-start gap-3 rounded-lg border border-border-subtle/40 px-3 py-2
        ${disabled ? 'opacity-60' : 'cursor-pointer hover:border-accent/40'}`}
    >
      <input
        type="checkbox"
        className="mt-0.5 h-4 w-4 accent-[var(--color-accent)]"
        checked={Boolean(checked)}
        onChange={(event) => onChange(event.target.checked)}
        disabled={disabled}
      />
      <span className="min-w-0">
        <span className="block text-sm text-foreground">{label}</span>
        {hint && <span className="block text-xs text-muted-foreground">{hint}</span>}
      </span>
    </label>
  )
}

function BookableCalendar() {
  const [section, setSection] = useState('embed')
  const [roomId, setRoomId] = useState('')
  const [notice, setNotice] = useState(null)
  const [failure, setFailure] = useState(null)
  const [busy, setBusy] = useState(false)
  const [selected, setSelected] = useState(null)

  const vocabulary = useAsync(() => calendarApi.vocabulary(), [])
  const inferences = useAsync(() => calendarApi.inferences(), [])
  const rooms = useAsync(() => api.listRecords('room', { limit: 100 }), [])
  const summary = useAsync(() => calendarApi.summary({ room_id: roomId || undefined }), [roomId])
  const clients = useAsync(() => calendarApi.listClients(), [])
  const eventTypes = useAsync(() => calendarApi.listEventTypes(), [])
  const calendars = useAsync(() => calendarApi.listCalendars(), [])
  const forms = useAsync(() => calendarApi.listRoutingForms(), [])
  const embed = useAsync(
    () => (roomId ? calendarApi.embed(roomId) : Promise.resolve(null)),
    [roomId]
  )
  const grid = useAsync(
    () =>
      roomId
        ? calendarApi.slots(roomId, {
            eventTypeId: embed.data?.embed?.eventTypeId || undefined,
            timeZone: embed.data?.embed?.time_zone || undefined,
          })
        : Promise.resolve(null),
    [roomId, embed.data]
  )
  const holds = useAsync(
    () => (roomId ? calendarApi.listHolds(roomId) : Promise.resolve(null)),
    [roomId]
  )
  const bookings = useAsync(
    () => (roomId ? calendarApi.listBookings(roomId) : Promise.resolve(null)),
    [roomId]
  )
  const events = useAsync(
    () => (roomId ? calendarApi.bookingEvents(roomId) : Promise.resolve(null)),
    [roomId]
  )

  // The booking form. Every field here is a researched field of
  // POST /v2/bookings, and the ones with a documented limit are the ones a page
  // can get wrong: recurrenceCount above 32 is refused, and instant is team events
  // only.
  const [form, setForm] = useState({
    name: '',
    email: '',
    eventTypeId: '',
    routingFormId: '',
    start: '',
    reservationUid: '',
    rescheduleUid: '',
    recurrenceCount: '',
    instant: false,
    teamSize: '',
    note: '',
    dealStage: '',
  })

  const [routingAnswer, setRoutingAnswer] = useState('security')
  const [routingResult, setRoutingResult] = useState(null)

  useEffect(() => {
    if (!roomId && rooms.data?.rooms?.length) setRoomId(rooms.data.rooms[0].id)
  }, [rooms.data, roomId])

  useEffect(() => {
    setSelected(null)
    setForm((previous) => ({ ...previous, start: '', reservationUid: '', rescheduleUid: '' }))
  }, [roomId])

  const set = (key) => (value) => setForm((previous) => ({ ...previous, [key]: value }))

  async function guard(message, action) {
    setBusy(true)
    setFailure(null)
    try {
      await action()
      setNotice(message)
    } catch (error) {
      setNotice(null)
      setFailure(error)
    } finally {
      setBusy(false)
    }
  }

  function refreshRoom() {
    embed.refetch()
    grid.refetch()
    holds.refetch()
    bookings.refetch()
    events.refetch()
    summary.refetch()
  }

  function pickSlot(slot) {
    if (!slot.available) return
    setSelected(slot)
    setForm((previous) => ({ ...previous, start: slot.start }))
  }

  function takeHold(slot) {
    if (!roomId || !slot) return
    guard(`Held ${absoluteTime(slot.start)} for ${DEFAULT_HOLD_MINUTES} minutes by default.`, async () => {
      const held = await calendarApi.reserve(roomId, { start: slot.start })
      setSelected({ ...slot, held_by: held.reservationUid })
      setForm((previous) => ({
        ...previous,
        start: held.start,
        reservationUid: held.reservationUid,
      }))
      refreshRoom()
    })
  }

  function book() {
    if (!roomId) return
    const fields = {}
    if (form.teamSize) fields.team_size = form.teamSize
    if (form.note) fields.what_to_cover = form.note
    const payload = {
      start: form.start || undefined,
      attendee: { name: form.name, email: form.email },
      eventTypeId: form.eventTypeId || undefined,
      bookingFieldsResponses: fields,
    }
    if (form.reservationUid) payload.reservationUid = form.reservationUid
    if (form.rescheduleUid) payload.bookingUidToReschedule = form.rescheduleUid
    if (form.recurrenceCount) payload.recurrenceCount = Number(form.recurrenceCount)
    if (form.instant) payload.instant = true
    if (form.dealStage) payload.metadata = { deal_stage: form.dealStage }

    guard('Booked. The slot is now the prospect&rsquo;s.', async () => {
      const booked = await calendarApi.book(roomId, payload)
      setNotice(
        booked.rescheduled
          ? `Moved to ${absoluteTime(booked.start)}. No second booking was created.`
          : `Booked for ${absoluteTime(booked.start)}${booked.video ? ' with a join link' : ''}.`,
      )
      setForm((previous) => ({
        ...previous,
        start: '',
        reservationUid: '',
        rescheduleUid: '',
        recurrenceCount: '',
      }))
      setSelected(null)
      refreshRoom()
    })
  }

  function cancelBooking(booking) {
    guard('Cancelled. The slot is back on the grid.', async () => {
      await calendarApi.cancelBooking(roomId, booking.uid, 'cancelled from the room page')
      refreshRoom()
    })
  }

  function reschedule(booking) {
    setForm((previous) => ({ ...previous, rescheduleUid: booking.uid, start: '' }))
    setSelected(null)
    setSection('bookings')
    setNotice('Pick a new slot below and submit again. The booking moves; it is not duplicated.')
  }

  function extendHold(hold) {
    guard(`Extended to ${absoluteTime(new Date(Date.now() + 30 * 60000))}.`, async () => {
      await calendarApi.extendHold(roomId, hold.reservationUid, { reservationDuration: 30 })
      refreshRoom()
    })
  }

  function releaseHold(hold) {
    guard('Released. The slot is available again.', async () => {
      await calendarApi.releaseHold(roomId, hold.reservationUid)
      setForm((previous) => ({ ...previous, reservationUid: '' }))
      refreshRoom()
    })
  }

  function route() {
    if (!roomId) return
    const formId = forms.data?.routing_forms?.[0]?.id
    guard('', async () => {
      const answer = await calendarApi.routedSlots(roomId, {
        formId,
        responses: JSON.stringify({ topic: routingAnswer }),
      })
      setRoutingResult(answer)
      setNotice(null)
    })
  }

  const embedSummary = embed.data?.summary
  const canBook = Boolean(embedSummary?.can_book)
  const slotRows = grid.data?.slots || []
  const available = slotRows.filter((slot) => slot.available)
  const byDay = useMemo(() => {
    const groups = new Map()
    for (const slot of slotRows) {
      const day = slot.start.slice(0, 10)
      if (!groups.has(day)) groups.set(day, [])
      groups.get(day).push(slot)
    }
    return [...groups.entries()].slice(0, 5)
  }, [slotRows])

  const stats = [
    {
      label: 'Bookings',
      value: summary.data?.bookings ?? 0,
      hint: `${summary.data?.instant_bookings ?? 0} instant, ${summary.data?.recurring_bookings ?? 0} recurring`,
      glyph: Glyphs.booked,
    },
    {
      label: 'Live holds',
      value: summary.data?.holds_live ?? 0,
      hint: `default ${summary.data?.default_hold_minutes ?? DEFAULT_HOLD_MINUTES} minutes`,
      glyph: Glyphs.hold,
    },
    {
      label: 'Expired holds',
      value: summary.data?.holds_expired ?? 0,
      hint: 'expired with nobody acting',
      glyph: Glyphs.hold,
    },
    {
      label: 'Event types',
      value: summary.data?.event_types ?? 0,
      hint: `${summary.data?.rooms_with_embed ?? 0} rooms with an embed`,
      glyph: Glyphs.calendar,
    },
  ]

  return (
    <div className="space-y-5">
      <header>
        <h1 className="font-mono text-2xl font-semibold">Bookable calendar</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Install a bookable calendar in a room: an OAuth client and an embed, a slot grid, an
          optional slot hold, and a booking the prospect submits entirely in-room.
        </p>
      </header>

      <div className="flex flex-wrap items-end gap-3">
        <Field label="Room" id="wf058-room" hint="The researched flow books inside the room, so every write is room-scoped.">
          <select
            id="wf058-room"
            className={inputClass}
            value={roomId}
            onChange={(event) => setRoomId(event.target.value)}
          >
            <option value="">Choose a room</option>
            {(rooms.data?.rooms || []).map((room) => (
              <option key={room.id} value={room.id}>
                {room.data?.name || room.id}
              </option>
            ))}
          </select>
        </Field>
        <Button icon="refresh" onClick={refreshRoom} disabled={!roomId}>
          Refresh
        </Button>
      </div>

      {failure && <Refusal error={failure} onRetry={() => setFailure(null)} />}
      {notice && <Note>{notice}</Note>}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {stats.map((stat) => (
          <Stat key={stat.label} {...stat} />
        ))}
      </div>

      {/* Section switcher. A tablist, so the arrow-key semantics and the aria
          relationship are right and the choice can be shared by URL. */}
      <div role="tablist" aria-label="Bookable calendar sections" className="flex flex-wrap gap-2">
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
            <Icon path={Glyphs[item.glyph]} size={16} />
            {item.label}
          </button>
        ))}
      </div>

      {/* -- embed --------------------------------------------------------- */}
      {section === 'embed' && (
        <div id="panel-embed" role="tabpanel" aria-labelledby="tab-embed" className="space-y-4">
          <SectionHeading title="The embed">
            Step 1 stands up an OAuth client, step 2 renders the Booker. This is what the room
            installs, and whether it can book at all.
          </SectionHeading>

          {embed.loading && <Spinner label="Loading the embed" />}
          {!embed.loading && !roomId && (
            <EmptyState
              title="Choose a room"
              description="Every write in this workflow is room-scoped, because the researched step is that the prospect books entirely in-room."
            />
          )}
          {!embed.loading && roomId && embed.data === null && (
            <EmptyState
              title="This room has no embed"
              description="The researched first step is that the builder installs the embeddable booking components before the room can render a Booker."
            />
          )}

          {embedSummary && (
            <div className="grid gap-4 lg:grid-cols-2">
              <Card>
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h3 className="truncate font-mono text-sm font-semibold">
                      {embedSummary.event_title || embedSummary.eventTypeId}
                    </h3>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      {embedSummary.event_kind} · {embedSummary.length_minutes} minutes
                    </p>
                  </div>
                  <Badge tone={canBook ? 'insert' : 'delete'}>
                    {canBook ? 'can book' : 'cannot book'}
                  </Badge>
                </div>
                <dl className="mt-3 space-y-1.5 text-xs">
                  <Fact label="Components">{(embedSummary.components || []).join(', ')}</Fact>
                  <Fact label="Time zone">{embedSummary.time_zone}</Fact>
                  <Fact label="Routing form">{embedSummary.routingFormId || 'none'}</Fact>
                  <Fact label="Token">{embedSummary.token?.reason}</Fact>
                  <Fact label="Subject">{embedSummary.token?.subject || '—'}</Fact>
                </dl>
                {!canBook && (
                  <p className="mt-3 text-sm text-amber-300">{embedSummary.token?.detail}</p>
                )}
              </Card>

              <Card>
                <h3 className="font-mono text-sm font-semibold">Install on this room</h3>
                <p className="mt-1 text-xs text-muted-foreground">
                  A room must be able to book: a render with no Booker is refused, because the
                  researched step 5 needs one.
                </p>
                <div className="mt-3 space-y-3">
                  <Field label="Event type" id="wf058-embed-event">
                    <select
                      id="wf058-embed-event"
                      className={inputClass}
                      value={form.eventTypeId || embedSummary.eventTypeId || ''}
                      onChange={(event) => set('eventTypeId')(event.target.value)}
                    >
                      <option value="">The embed&rsquo;s own</option>
                      {(eventTypes.data?.event_types || []).map((record) => (
                        <option key={record.id} value={record.data.eventTypeId}>
                          {record.data.title} ({record.data.kind})
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field
                    label="Routing form"
                    id="wf058-embed-form"
                    hint="An embed naming only a form takes that form's catch-all as its event type."
                  >
                    <select
                      id="wf058-embed-form"
                      className={inputClass}
                      value={form.routingFormId || embedSummary.routingFormId || ''}
                      onChange={(event) => set('routingFormId')(event.target.value)}
                    >
                      <option value="">none</option>
                      {(forms.data?.routing_forms || []).map((record) => (
                        <option key={record.id} value={record.id}>
                          {record.data.name}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Button
                    variant="primary"
                    disabled={busy || !roomId}
                    onClick={() =>
                      guard('Embed installed.', async () => {
                        await calendarApi.saveEmbed(roomId, {
                          eventTypeId: form.eventTypeId || embedSummary.eventTypeId,
                          routingFormId:
                            form.routingFormId || embedSummary.routingFormId || undefined,
                          time_zone: embedSummary.time_zone,
                        })
                        refreshRoom()
                      })
                    }
                  >
                    Install
                  </Button>
                </div>
              </Card>
            </div>
          )}

          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <h3 className="font-mono text-sm font-semibold">OAuth clients</h3>
              <p className="mt-1 text-xs text-muted-foreground">
                The research records a token&rsquo;s existence and expiry, never its value. A field
                shaped like a credential is refused by name.
              </p>
              <ul className="mt-3 space-y-2">
                {(clients.data?.clients || []).map((record) => (
                  <li
                    key={record.id}
                    className="flex flex-wrap items-center gap-2 border-b border-border-subtle/15 pb-2 last:border-0"
                  >
                    <Icon path={Glyphs.oauth} size={16} className="text-accent" />
                    <span className="min-w-0 flex-1 truncate font-mono text-[13px]">
                      {record.data.name}
                    </span>
                    <Badge tone={record.token_state?.live ? 'insert' : 'neutral'}>
                      {record.token_state?.reason}
                    </Badge>
                    <Button
                      disabled={busy}
                      onClick={() =>
                        guard(`Recorded a grant for ${record.data.name}.`, async () => {
                          await calendarApi.grant(record.id, { subject: 'priya' })
                          clients.refetch()
                          refreshRoom()
                        })
                      }
                    >
                      Grant
                    </Button>
                  </li>
                ))}
                {(clients.data?.clients || []).length === 0 && (
                  <li className="text-sm text-muted-foreground">No client yet.</li>
                )}
              </ul>
            </Card>

            <Card>
              <h3 className="font-mono text-sm font-semibold">Calendar connects</h3>
              <p className="mt-1 text-xs text-muted-foreground">
                Google, Outlook and Apple. These are part of the embed, not of the booking path, so a
                connect is configuration and affects nothing a booking depends on.
              </p>
              <div className="mt-3 flex flex-wrap gap-2">
                {(vocabulary.data?.calendar_providers || []).map((provider) => {
                  const connected = (calendars.data?.connected || []).includes(provider)
                  return (
                    <Button
                      key={provider}
                      disabled={busy}
                      onClick={() =>
                        guard(
                          connected
                            ? `Disconnected ${provider}.`
                            : `Connected ${provider}. The research lists no endpoint for this, so it is configuration.`,
                          async () => {
                            if (connected) {
                              const record = (calendars.data?.connections || []).find(
                                (entry) => entry.data.provider === provider
                              )
                              await calendarApi.disconnectCalendar(record.id)
                            } else {
                              await calendarApi.connectCalendar({ provider, host: 'priya' })
                            }
                            calendars.refetch()
                            summary.refetch()
                          }
                        )
                      }
                    >
                      {connected ? `Disconnect ${provider}` : `Connect ${provider}`}
                    </Button>
                  )
                })}
              </div>
            </Card>
          </div>
        </div>
      )}

      {/* -- the grid ------------------------------------------------------ */}
      {section === 'grid' && (
        <div id="panel-grid" role="tabpanel" aria-labelledby="tab-grid" className="space-y-4">
          <SectionHeading
            title="The grid"
            aside={<Button icon="refresh" onClick={grid.refetch} disabled={!roomId}>Refresh</Button>}
          >
            Every candidate slot, and why each is or is not bookable. A query names exactly one of
            the four researched selectors, and the grid is generated from working hours and then
            marked &mdash; never generated as only the free ones.
          </SectionHeading>

          {grid.loading && <Spinner label="Reading the slot grid" />}
          {grid.data?.error && <Refusal error={grid.data.error} onRetry={grid.refetch} />}

          {grid.data && (
            <>
              <p className="text-sm text-muted-foreground">
                <span className="font-mono text-foreground">{grid.data.available_count}</span> of{' '}
                <span className="font-mono text-foreground">{grid.data.slot_count}</span> slots
                bookable, in {grid.data.time_zone}, read with{' '}
                <span className="font-mono text-foreground">cal-api-version {grid.data.api_version}</span>
                . Asked: <span className="font-mono text-foreground">{grid.data.selector}</span>.
              </p>

              {grid.data.available_count === 0 && (
                <EmptyState
                  title="Nothing bookable in this window"
                  description="Every slot is unavailable. Each row below says which of the four reasons applies."
                />
              )}

              <div className="space-y-4">
                {byDay.map(([day, slots]) => (
                  <Card key={day}>
                    <p className="font-mono text-xs text-muted-foreground">{day}</p>
                    <div className="mt-2 flex flex-wrap gap-2">
                      {slots.map((slot) => (
                        <SlotChip
                          key={slot.start}
                          slot={slot}
                          selected={selected?.start === slot.start}
                          onSelect={pickSlot}
                        />
                      ))}
                    </div>
                  </Card>
                ))}
              </div>

              {selected && (
                <Card className="card-hover">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="min-w-0">
                      <p className="font-mono text-sm font-semibold">
                        {absoluteTime(selected.start)} &rarr; {absoluteTime(selected.end)}
                      </p>
                      <p className="mt-0.5 text-xs text-muted-foreground">
                        {available.length} slots bookable; hosts free:{' '}
                        {(selected.hosts_available || []).join(', ') || '—'}
                        {selected.seats_left !== null && selected.seats_left !== undefined
                          ? `; ${selected.seats_left} seats left`
                          : ''}
                      </p>
                    </div>
                    <div className="flex gap-2">
                      <Button disabled={busy} onClick={() => takeHold(selected)}>
                        Hold this slot
                      </Button>
                      <Button variant="primary" disabled={busy} onClick={() => setSection('bookings')}>
                        Book it
                      </Button>
                    </div>
                  </div>
                </Card>
              )}
            </>
          )}
        </div>
      )}

      {/* -- holds --------------------------------------------------------- */}
      {section === 'holds' && (
        <div id="panel-holds" role="tabpanel" aria-labelledby="tab-holds" className="space-y-4">
          <SectionHeading
            title="Holds"
            aside={<Button icon="refresh" onClick={holds.refetch} disabled={!roomId}>Refresh</Button>}
          >
            &ldquo;No user action needed for the hold to expire.&rdquo; A hold is live exactly while
            now is before its <span className="font-mono">reservationUntil</span>, and a row that
            still says <span className="font-mono">held</span> after that is reported as expired with
            the stored state beside it.
          </SectionHeading>

          {holds.loading && <Spinner label="Loading holds" />}
          {holds.data?.holds?.length === 0 && (
            <EmptyState
              title="No holds on this room"
              description="A hold is optional: a booking can be submitted against a free slot directly."
            />
          )}
          {holds.data?.holds?.length > 0 && (
            <Card>
              <ul>
                {holds.data.holds.map((hold) => (
                  <HoldRow
                    key={hold.reservationUid}
                    hold={hold}
                    onExtend={extendHold}
                    onRelease={releaseHold}
                    busy={busy}
                  />
                ))}
              </ul>
            </Card>
          )}
        </div>
      )}

      {/* -- bookings ------------------------------------------------------ */}
      {section === 'bookings' && (
        <div id="panel-bookings" role="tabpanel" aria-labelledby="tab-bookings" className="space-y-4">
          <SectionHeading title="Book in-room">
            The researched custom booking flow: intercept, ask, submit. Whatever the page asked on
            the way here arrives as booking fields and a hold, and this form is the submission.
          </SectionHeading>

          <Card>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Attendee name" id="wf058-name">
                <input
                  id="wf058-name"
                  className={inputClass}
                  value={form.name}
                  onChange={(event) => set('name')(event.target.value)}
                  placeholder="Ines Duarte"
                />
              </Field>
              <Field label="Attendee email" id="wf058-email" hint="Required. A calendar invite needs a reachable address.">
                <input
                  id="wf058-email"
                  className={inputClass}
                  value={form.email}
                  onChange={(event) => set('email')(event.target.value)}
                  placeholder="ines@example.com"
                />
              </Field>
              <Field label="Start" id="wf058-start" hint="Pick one from the grid, or leave it out for an instant booking.">
                <input
                  id="wf058-start"
                  className={inputClass}
                  value={form.start}
                  onChange={(event) => set('start')(event.target.value)}
                  placeholder="2026-09-29T09:00:00Z"
                />
              </Field>
              <Field label="Against a hold" id="wf058-hold" hint="Optional, unless the room's embed requires one.">
                <input
                  id="wf058-hold"
                  className={inputClass}
                  value={form.reservationUid}
                  onChange={(event) => set('reservationUid')(event.target.value)}
                  placeholder="rsv_…"
                />
              </Field>
              <Field label="Recurrence count" id="wf058-recurrence" hint="Refused above 32, never truncated.">
                <input
                  id="wf058-recurrence"
                  className={inputClass}
                  value={form.recurrenceCount}
                  onChange={(event) => set('recurrenceCount')(event.target.value)}
                  placeholder="1"
                />
              </Field>
              <Field label="Deal stage" id="wf058-deal" hint="Rides in metadata, beside the room context this product adds.">
                <input
                  id="wf058-deal"
                  className={inputClass}
                  value={form.dealStage}
                  onChange={(event) => set('dealStage')(event.target.value)}
                  placeholder="evaluation"
                />
              </Field>
              <Field label="Booking field: team size" id="wf058-team">
                <input
                  id="wf058-team"
                  className={inputClass}
                  value={form.teamSize}
                  onChange={(event) => set('teamSize')(event.target.value)}
                  placeholder="6-20"
                />
              </Field>
              <Field label="Booking field: what to cover" id="wf058-note">
                <input
                  id="wf058-note"
                  className={inputClass}
                  value={form.note}
                  onChange={(event) => set('note')(event.target.value)}
                  placeholder="migration"
                />
              </Field>
            </div>

            <div className="mt-3 grid gap-2 sm:grid-cols-2">
              <Switch
                label="Instant"
                hint="Team events only, and the soonest free slot when no start is given."
                checked={form.instant}
                onChange={set('instant')}
              />
              <Switch
                label="Rescheduling"
                hint={
                  form.rescheduleUid
                    ? `Moving ${form.rescheduleUid}; the record moves and nothing is duplicated.`
                    : 'No booking selected. Pick one from the list below to move it.'
                }
                checked={Boolean(form.rescheduleUid)}
                onChange={(checked) => set('rescheduleUid')(checked ? 'bkg_choose_one' : '')}
                disabled={!form.rescheduleUid}
              />
            </div>

            <div className="mt-4 flex flex-wrap gap-2">
              <Button
                variant="primary"
                disabled={busy || !roomId || !form.name || !form.email}
                onClick={book}
              >
                Submit the booking
              </Button>
              {form.rescheduleUid && (
                <Button disabled={busy} onClick={() => set('rescheduleUid')('')}>
                  Clear the reschedule
                </Button>
              )}
            </div>
            {!canBook && roomId && (
              <p className="mt-3 text-sm text-amber-300">
                This room&rsquo;s token is not live, so a booking will be refused. Grant one in the
                Embed section first.
              </p>
            )}
          </Card>

          {bookings.loading && <Spinner label="Loading bookings" />}
          {bookings.data?.bookings?.length > 0 && (
            <Card>
              <ul>
                {bookings.data.bookings.map((record) => (
                  <BookingRow
                    key={record.id}
                    booking={record}
                    onCancel={cancelBooking}
                    onReschedule={reschedule}
                    busy={busy}
                  />
                ))}
              </ul>
            </Card>
          )}

          {events.data?.events?.length > 0 && (
            <Card>
              <h3 className="font-mono text-sm font-semibold">The automation</h3>
              <p className="mt-1 text-xs text-muted-foreground">
                &ldquo;On success, <span className="font-mono">BOOKING_CREATED</span> webhook fires;
                downstream automations can chain.&rdquo; The event is recorded so another workflow can
                read it; the transport is not this build&rsquo;s, so the delivery says pending rather
                than sent.
              </p>
              <ul className="mt-3 space-y-1.5">
                {events.data.events.map((record) => (
                  <li key={record.id} className="flex flex-wrap items-center gap-2 text-xs">
                    <Badge tone="update">{record.data.event}</Badge>
                    <span className="font-mono text-foreground">{record.data.bookingUid}</span>
                    <span className="text-muted-foreground">{relativeTime(record.created_at)}</span>
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </div>
      )}

      {/* -- routing ------------------------------------------------------- */}
      {section === 'routing' && (
        <div id="panel-routing" role="tabpanel" aria-labelledby="tab-routing" className="space-y-4">
          <SectionHeading title="Routing">
            &ldquo;It will not actually save the response just return the routed event type and slots
            when it can be booked.&rdquo; This is a read, and every form ends in a catch-all so an
            unmatched answer still reaches a host.
          </SectionHeading>

          <Card>
            <div className="flex flex-wrap items-end gap-3">
              <Field label="Answer" id="wf058-answer" hint="Sent as a query parameter, because the read has nothing to write.">
                <select
                  id="wf058-answer"
                  className={inputClass}
                  value={routingAnswer}
                  onChange={(event) => setRoutingAnswer(event.target.value)}
                >
                  <option value="security">security &mdash; matches a rule</option>
                  <option value="pricing">pricing &mdash; matches another rule</option>
                  <option value="unsubscribe">unsubscribe &mdash; a rule that declines</option>
                  <option value="something-else">something else &mdash; the catch-all</option>
                </select>
              </Field>
              <Button disabled={busy || !roomId} onClick={route}>
                Route it
              </Button>
            </div>

            {routingResult && (
              <div className="mt-4 space-y-2 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={routingResult.routed ? 'insert' : 'delete'}>
                    {routingResult.routed ? 'routed' : 'not routed'}
                  </Badge>
                  <Badge tone="neutral">{routingResult.reason}</Badge>
                  <span className="font-mono text-foreground">{routingResult.eventTypeId || '—'}</span>
                  <span className="text-muted-foreground">
                    {routingResult.matched_rule === null
                      ? 'the catch-all, not a rule'
                      : `rule ${routingResult.matched_rule}`}
                  </span>
                </div>
                <p className="text-muted-foreground">{routingResult.detail}</p>
                <p className="text-xs text-muted-foreground">
                  {routingResult.slot_count} slots, and nothing was saved.
                </p>
              </div>
            )}
          </Card>

          <Card>
            <h3 className="font-mono text-sm font-semibold">Forms</h3>
            <ul className="mt-3 space-y-2">
              {(forms.data?.routing_forms || []).map((record) => (
                <li key={record.id} className="border-b border-border-subtle/15 pb-2 last:border-0">
                  <p className="font-mono text-[13px] text-foreground">{record.data.name}</p>
                  <ol className="mt-1 space-y-0.5 text-xs text-muted-foreground">
                    {record.data.rules.map((rule, index) => (
                      <li key={`${rule.field}-${index}`}>
                        {index}. {rule.field} {rule.operator}{' '}
                        {rule.value !== undefined ? `"${rule.value}"` : ''} &rarr;{' '}
                        {rule.fallback ? 'declines' : rule.eventTypeId}
                      </li>
                    ))}
                    <li className="text-accent">catch-all &rarr; {record.data.fallbackEventTypeId}</li>
                  </ol>
                </li>
              ))}
              {(forms.data?.routing_forms || []).length === 0 && (
                <li className="text-sm text-muted-foreground">No routing form installed.</li>
              )}
            </ul>
          </Card>
        </div>
      )}

      {/* -- inferences ---------------------------------------------------- */}
      {section === 'inferences' && (
        <div id="panel-inferences" role="tabpanel" aria-labelledby="tab-inferences" className="space-y-4">
          <SectionHeading title="What this infers">
            WF-058 is a build rather than a port, so most of what is on this page is a decision
            somebody made rather than a sentence somebody quoted. Each is named, bounded and says how
            to change it.
          </SectionHeading>

          {inferences.loading && <Spinner label="Loading the inference registry" />}
          {inferences.data?.inferences?.map((entry) => (
            <Card key={entry.id}>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <h3 className="font-mono text-sm font-semibold">{entry.topic}</h3>
                <Badge tone="neutral">{entry.id}</Badge>
              </div>
              <p className="mt-2 text-sm text-muted-foreground">{entry.why}</p>
              <p className="mt-2 text-xs text-muted-foreground">
                <span className="font-medium text-foreground">Basis:</span> {entry.basis}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                <span className="font-medium text-foreground">Change it:</span> {entry.change_it}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                <span className="font-medium text-foreground">Blast radius:</span> {entry.blast_radius}
              </p>
              <details className="mt-2">
                <summary className="cursor-pointer text-xs text-accent">What this build chose</summary>
                <JsonView value={entry.value} />
              </details>
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}

export default BookableCalendar
