/**
 * WF-051: route and book a demo request inline from a web form.
 *
 * The page is organised around the researched flow rather than around the
 * tables behind it: what an operator sees first is who is waiting on a slot,
 * because a prospect sitting on a calendar with nobody chasing them is the state
 * the whole workflow exists to prevent.
 *
 * The Time Elapsed timer is the thing a reviewer most needs to understand, so it
 * gets its own panel with the researched sentence beside it rather than a column
 * nobody reads.
 */

import { useMemo, useState } from 'react'
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
import { apiRequest } from '@/lib/api'
import routerApi from './api'
import { Checkbox, Fact, Facts, Modal, Notice, Quote, SectionHead, StateChip } from './primitives'

function BookingRow({ booking, onOpen, onExpire, onCancel }) {
  const guest = booking.guest || {}
  const open = booking.state === 'pending'
  const booked = booking.state === 'booked'

  return (
    <li className="flex flex-col gap-3 border-b border-border-subtle py-3 last:border-b-0 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium text-foreground">
            {guest.email || 'No address on this request'}
          </span>
          <StateChip state={booking.state} />
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          {guest.company ? `${guest.company} · ` : ''}
          routed by <span className="font-mono">{booking.rule}</span>
          {booking.seller_name ? ` to ${booking.seller_name}` : ''}
        </p>
        {booking.notification && booking.notification.reason ? (
          <p className="mt-1 text-xs text-muted-foreground">
            <span className="font-mono uppercase tracking-[0.14em]">Follow-up</span>{' '}
            {booking.notification.reason}
          </p>
        ) : null}
      </div>

      <div className="flex shrink-0 flex-wrap gap-2">
        {open ? (
          <>
            <Button onClick={() => onOpen(booking)}>Book a slot</Button>
            <Button variant="ghost" onClick={() => onExpire(booking)}>
              Run the timer
            </Button>
          </>
        ) : null}
        {booked ? (
          <Button variant="ghost" onClick={() => onCancel(booking)}>
            Cancel
          </Button>
        ) : null}
      </div>
    </li>
  )
}

function SlotPicker({ booking, onClose, onBooked }) {
  const [busy, setBusy] = useState(false)
  const [refusal, setRefusal] = useState(null)
  const [meetingType, setMeetingType] = useState(
    (booking.meeting_types && booking.meeting_types[0]) || 'demo',
  )
  const slots = useAsync(
    () => routerApi.slots(booking.room_id, booking.id),
    [booking.room_id, booking.id],
  )

  async function book(start) {
    setBusy(true)
    setRefusal(null)
    try {
      await routerApi.schedule(booking.room_id, booking.id, {
        start,
        meeting_type: meetingType,
      })
      onBooked()
      onClose()
    } catch (error) {
      setRefusal(error)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      open
      title="Pick a slot"
      onClose={onClose}
      footer={
        <Button variant="secondary" onClick={onClose}>
          Close
        </Button>
      }
    >
      <div className="space-y-4">
        <p className="text-sm text-muted-foreground">
          The matched Display Calendar node offers{' '}
          <span className="font-mono text-foreground">
            {booking.meeting_types && booking.meeting_types.length
              ? booking.meeting_types.join(', ')
              : 'no meeting type'}
          </span>{' '}
          to {booking.seller_name || 'the assigned seller'}.
        </p>

        {refusal ? <Notice tone="bad" word="Refused">{refusal.message}</Notice> : null}

        {slots.loading ? <Spinner label="Loading the seller's calendar" /> : null}
        {slots.error ? <ErrorNote error={slots.error} onRetry={slots.refetch} /> : null}

        {slots.data && slots.data.slots && slots.data.slots.length ? (
          <>
            <Field label="Meeting type" id="wf051-meeting-type">
              <select
                id="wf051-meeting-type"
                className={inputClass}
                value={meetingType}
                onChange={(event) => setMeetingType(event.target.value)}
              >
                {(slots.data.meetingTypes || []).map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            </Field>

            <ul className="max-h-64 space-y-1 overflow-y-auto">
              {slots.data.slots.map((slot) => (
                <li key={slot.start}>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => book(slot.start)}
                    aria-label={`Book the slot starting ${slot.start}`}
                    className="flex min-h-11 w-full items-center justify-between gap-3 rounded-sm border border-border-subtle px-3 text-left text-sm text-foreground hover:border-accent hover:text-accent focus-visible:border-accent disabled:opacity-50"
                  >
                    <span className="font-mono text-[13px]">{slot.start}</span>
                    <span className="text-xs text-muted-foreground">{slot.duration_minutes} min</span>
                  </button>
                </li>
              ))}
            </ul>
          </>
        ) : null}

        {slots.data && !(slots.data.slots || []).length ? (
          <Notice tone="warn" word="No slots">
            This seller's calendar has no free time inside the search horizon.
          </Notice>
        ) : null}
      </div>
    </Modal>
  )
}

function RouterPanel({ routers, onPublish, onToggle }) {
  if (!routers.length) {
    return (
      <EmptyState
        title="No Concierge Router is declared yet"
        description="A router is a flow of nodes. Its first node is always a Trigger, and the chain must end in a Catch All path."
      />
    )
  }

  return (
    <ul className="space-y-3">
      {routers.map((router) => (
        <li key={router.id}>
          <Card className="space-y-3">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <h3 className="text-base font-semibold text-foreground">{router.name || router.slug}</h3>
                <p className="mt-0.5 font-mono text-xs text-muted-foreground">{router.slug}</p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={router.publish_state === 'published' ? 'update' : 'neutral'}>
                  {router.publish_state}
                </Badge>
                {router.enabled === false ? <Badge tone="neutral">switched off</Badge> : null}
              </div>
            </div>

            <Facts columns={3}>
              <Fact label="Nodes" value={(router.nodes || []).length} mono />
              <Fact label="Catch all" value={router.catch_all} />
              <Fact
                label="Deployment"
                value={(router.deployment || []).join(', ')}
              />
            </Facts>

            <ol className="flex flex-wrap gap-1.5">
              {(router.nodes || []).map((node, index) => (
                <li
                  key={`${node.type}-${index}`}
                  className="rounded-xs border border-border-subtle bg-muted px-2 py-0.5 font-mono text-[11px] text-muted-foreground"
                >
                  {node.type}
                  {node.name ? `: ${node.name}` : ''}
                </li>
              ))}
            </ol>

            <div className="flex flex-wrap gap-2">
              <Button variant="ghost" onClick={() => onToggle(router)}>
                {router.enabled === false ? 'Switch on' : 'Switch off'}
              </Button>
              {router.publish_state !== 'published' ? (
                <Button onClick={() => onPublish(router)}>Publish and deploy</Button>
              ) : null}
            </div>
          </Card>
        </li>
      ))}
    </ul>
  )
}

export default function ConciergeRouterPage() {
  const [roomId, setRoomId] = useState('')
  const [picking, setPicking] = useState(null)
  const [notice, setNotice] = useState(null)
  const [refusal, setRefusal] = useState(null)
  const [busy, setBusy] = useState(false)
  const [filter, setFilter] = useState({})

  const rooms = useAsync(() => apiRequest('/records/room?limit=100'), [])
  const summary = useAsync(() => routerApi.summary(), [])

  const effectiveRoom = roomId || (rooms.data && rooms.data.records && rooms.data.records[0]
    ? rooms.data.records[0].id
    : '')

  const routers = useAsync(
    () => (effectiveRoom ? routerApi.routers({ room_id: effectiveRoom }) : Promise.resolve(null)),
    [effectiveRoom],
  )
  const bookings = useAsync(
    () =>
      effectiveRoom
        ? routerApi.bookings(effectiveRoom, filter.state ? { state: filter.state } : {})
        : Promise.resolve(null),
    [effectiveRoom, filter.state],
  )

  const states = useMemo(
    () => ['pending', 'booked', 'not_scheduled', 'not_offered'],
    [],
  )

  function refresh() {
    summary.refetch()
    routers.refetch()
    bookings.refetch()
  }

  async function run(action, message) {
    setBusy(true)
    setRefusal(null)
    setNotice(null)
    try {
      await action()
      setNotice({ tone: 'good', word: 'Done', text: message })
      refresh()
    } catch (error) {
      setRefusal(error)
    } finally {
      setBusy(false)
    }
  }

  if (rooms.error) return <ErrorNote error={rooms.error} onRetry={rooms.refetch} />
  if (summary.error) return <ErrorNote error={summary.error} onRetry={summary.refetch} />
  if (rooms.loading || summary.loading) return <Spinner label="Loading the concierge router" />

  const counts = summary.data || {}
  const rows = (bookings.data && bookings.data.bookings) || []
  const routerRows = (routers.data && routers.data.routers) || []

  return (
    <div className="space-y-6">
      <header>
        <h1 className="font-display text-2xl font-semibold text-foreground">
          Route and book a demo request inline
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          A prospect submits a webform, the router qualifies and routes them, and the same
          screen shows the right seller's calendar so they book without an email thread.
        </p>
      </header>

      {refusal ? (
        <Notice tone="bad" word="Refused">
          {refusal.message}
        </Notice>
      ) : null}
      {notice ? (
        <Notice tone={notice.tone} word={notice.word}>
          {notice.text}
        </Notice>
      ) : null}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Routers" value={counts.routers || 0} hint="declared" icon="schema" />
        <StatCard label="Deployed" value={counts.deployed || 0} hint="published" icon="audit" />
        <StatCard label="Bookings" value={counts.bookings || 0} hint="all states" icon="database" />
        <StatCard
          label="Waiting"
          value={(counts.bookings_by_state || {}).pending || 0}
          hint="pending a slot"
          icon="rooms"
        />
      </div>

      <Card className="space-y-4">
        <SectionHead title="Room">
          <Icon name="rooms" className="text-muted-foreground" />
        </SectionHead>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Room" id="wf051-room" hint="Every routed path is scoped to one room.">
            <select
              id="wf051-room"
              className={inputClass}
              value={effectiveRoom}
              onChange={(event) => setRoomId(event.target.value)}
            >
              {(rooms.data && rooms.data.records ? rooms.data.records : []).map((row) => (
                <option key={row.id} value={row.id}>
                  {row.data.name || row.data.account || row.id}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Booking state" id="wf051-state" hint="Filter the list below.">
            <select
              id="wf051-state"
              className={inputClass}
              value={filter.state || ''}
              onChange={(event) => setFilter({ state: event.target.value })}
            >
              <option value="">Every state</option>
              {states.map((state) => (
                <option key={state} value={state}>
                  {state.replace(/_/g, ' ')}
                </option>
              ))}
            </select>
          </Field>
        </div>
      </Card>

      <section className="space-y-3">
        <SectionHead title="Routing sessions" count={rows.length}>
          <div className="flex gap-2">
            <Button variant="ghost" onClick={bookings.refetch} icon="refresh">
              Refresh
            </Button>
          </div>
        </SectionHead>

        {bookings.loading ? <Spinner label="Loading routing sessions" /> : null}
        {bookings.error ? <ErrorNote error={bookings.error} onRetry={bookings.refetch} /> : null}

        {!bookings.loading && !rows.length ? (
          <EmptyState
            title="No routing session in this room yet"
            description="Route a webform submission to open one. The router must be published and deployed before it will answer."
            action={
              effectiveRoom ? (
                <Button
                  onClick={() =>
                    run(
                      () =>
                        routerApi.route(effectiveRoom, {
                          router_id: routerRows[0] && routerRows[0].id,
                          form_fields: { work_email: 'prospect@example.test', company: 'Northwind' },
                        }),
                      'Opened a routing session for a test prospect.',
                    )
                  }
                  disabled={busy || !routerRows.length}
                >
                  Route a test prospect
                </Button>
              ) : null
            }
          />
        ) : null}

        {rows.length ? (
          <ul>
            {rows.map((booking) => (
              <BookingRow
                key={booking.id}
                booking={booking}
                onOpen={setPicking}
                onExpire={(row) =>
                  run(
                    () => routerApi.expire(row.room_id, row.id),
                    'The Time Elapsed timer ran and the meeting is now not scheduled.',
                  )
                }
                onCancel={(row) =>
                  run(
                    () => routerApi.cancelBooking(row.room_id, row.id),
                    'The booking was cancelled and the slot is free on that calendar again.',
                  )
                }
              />
            ))}
          </ul>
        ) : null}
      </section>

      <section className="space-y-3">
        <SectionHead title="Declared routers" count={routerRows.length} />
        {routers.loading ? <Spinner label="Loading declared routers" /> : null}
        {routers.error ? <ErrorNote error={routers.error} onRetry={routers.refetch} /> : null}
        {!routers.loading && !routers.error ? (
          <RouterPanel
            routers={routerRows}
            onPublish={(router) =>
              run(
                () =>
                  routerApi.publishRouter(router.id, {
                    publish_state: 'published',
                    deployment: ['web_form'],
                  }),
                `${router.name || router.slug} is published and deployed to the web form.`,
              )
            }
            onToggle={(router) =>
              run(
                () => routerApi.publishRouter(router.id, { enabled: router.enabled === false }),
                router.enabled === false
                  ? `${router.name || router.slug} is switched on.`
                  : `${router.name || router.slug} is switched off.`,
              )
            }
          />
        ) : null}
      </section>

      <section className="space-y-3">
        <SectionHead title="The Time Elapsed timer" />
        <Card className="space-y-4">
          <Quote source="WF-051 research, automations">
            when it expires the meeting will be considered not scheduled, and you can notify your
            rep to follow up
          </Quote>
          <div className="flex items-start gap-3">
            <Checkbox
              checked
              disabled
              onChange={() => {}}
              label="Notices every pending session on read"
              hint="Nothing polls. A session past its deadline moves when it is read, and the change is audited."
            />
          </div>
          <Facts columns={4}>
            <Fact label="Default" value="60 minutes" mono />
            <Fact label="On expiry" value="not scheduled" mono />
            <Fact label="Then" value="Assign To, Send Notification" />
            <Fact label="Notifications sent" value="recorded, not sent" />
          </Facts>
          <Notice tone="info" word="Recorded">
            This build records the follow-up notification rather than sending it. The research names
            the channel and publishes no delivery contract, so a record an operator can act on is
            honest where a claim to have sent mail would not be.
          </Notice>
        </Card>
      </section>

      {picking ? (
        <SlotPicker booking={picking} onClose={() => setPicking(null)} onBooked={refresh} />
      ) : null}
    </div>
  )
}