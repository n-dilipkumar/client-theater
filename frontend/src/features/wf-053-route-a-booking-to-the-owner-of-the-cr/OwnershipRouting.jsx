/**
 * Ownership routing: route a booking to the owner of the CRM record (WF-053).
 *
 * Four sections, in the order the researched flow happens in them.
 *
 * **Links** first, because everything else names one. The researched link type is
 * `Ownership` and only that: the other four Chili Piper publishes route by a person,
 * a round robin, or whoever picks the slot, so this page says so in the picker
 * rather than offering a link type it will refuse.
 *
 * **Decisions** is the surface a rep actually reads. Each row states who took the
 * prospect, whether that was the CRM's owner or the catch-all, which CRM object the
 * owner came from, which rule matched, and how many slots were offered. Those last
 * two are the difference between "why did this prospect reach me" and an answer you
 * have to re-derive.
 *
 * **Try a guest** runs the two researched calls against a real room: `check` first,
 * which writes nothing, so the answer can be seen before anything is committed; then
 * `init-simple`, which resolves the owner and opens a routing session; then
 * `schedule-simple`, which books one of the offered slots.
 *
 * **What this infers** is the research's own gaps made arguable. The research names
 * the link type, the required `guestEmail` and the forbidden nodes, and is silent
 * about the resolution order, the slot arithmetic, and what a second schedule call
 * does - so those are product behaviour, and a reviewer should be able to disagree
 * with one by name rather than by reading a diff.
 *
 * The pickers come from `/vocabulary`, never from a list compiled into this file.
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
  StatCard,
  inputClass,
  useAsync,
} from '@/components/ui'
import { ownershipApi } from './api'
import Glyphs from './icons'
import { Fact, Note, OutcomeChip, Quote, Subhead } from './primitives'

const SECTIONS = [
  { id: 'decisions', label: 'Decisions', glyph: 'route' },
  { id: 'links', label: 'Links', glyph: 'owner' },
  { id: 'try', label: 'Try a guest', glyph: 'availability' },
  { id: 'inferences', label: 'What this infers', glyph: 'guard' },
]

/** One decision row, expanding to the rule chain that produced it. */
function DecisionRow({ decision }) {
  const [open, setOpen] = useState(false)
  const data = decision.data || {}
  const owner = data.owner_name || data.owner_id || 'nobody'

  return (
    <li className="border-b border-border-subtle/15 last:border-0">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        // A row is read by a screen reader as one control, so the control needs a
        // name that says what it acts on. The visible cells below carry the same
        // words; this repeats them in one string so the accessible name is the
        // whole fact rather than whichever cell the reader happened to reach.
        aria-label={`Routing decision: ${data.guest_email || 'no guest email'}, routed to ${owner}`}
        className="flex w-full min-h-11 cursor-pointer flex-wrap items-center gap-3 py-2 text-left
          transition-colors duration-150 hover:bg-muted/40"
      >
        <OutcomeChip outcome={data.outcome} glyphs={Glyphs} />
        <span className="min-w-0 flex-1 truncate font-mono text-[13px] text-foreground">
          {data.guest_email || '(no guest email)'} → {owner}
        </span>
        <Badge tone="neutral">{data.matched_object_type || 'no object'}</Badge>
        {data.rule?.name && <Badge tone="update">{data.rule.name}</Badge>}
        {data.owner_unknown && <Badge tone="delete">dead owner</Badge>}
        <span className="shrink-0 font-mono text-xs text-muted-foreground">
          {data.slots_offered} slots
        </span>
        <span className="shrink-0 font-mono text-xs text-muted-foreground" title={absoluteTime(decision.created_at)}>
          {relativeTime(decision.created_at)}
        </span>
      </button>

      {open && (
        <div className="space-y-3 rounded-lg border border-border-subtle/25 bg-background/40 p-3">
          <dl className="grid gap-2 sm:grid-cols-2">
            <Fact label="Outcome">{data.outcome}</Fact>
            <Fact label="Owner">{data.owner_id || '(none)'}</Fact>
            <Fact label="Matched">{data.matched_object_type || 'none'}</Fact>
            <Fact label="Record">{data.matched_record_id?.slice(-8) || 'none'}</Fact>
            <Fact label="Resolved from">{data.owner_source}</Fact>
            <Fact label="Named owner">{data.named_owner_id || '(same)'}</Fact>
            <Fact label="Slots offered">{data.slots_offered}</Fact>
            <Fact label="Chain declared">{data.chain_declared ? 'yes' : 'no'}</Fact>
          </dl>

          <div>
            <Subhead>Resolution order</Subhead>
            <div className="flex flex-wrap gap-1">
              {(data.resolution_order || []).map((entry) => (
                <Badge key={entry} tone="neutral">
                  {entry}
                </Badge>
              ))}
            </div>
          </div>

          {data.considered?.length > 0 && (
            <div>
              <Subhead>Rules considered</Subhead>
              <ul className="space-y-1">
                {data.considered.map((entry, index) => (
                  <li key={`${entry.name}-${index}`} className="flex items-start gap-2 text-xs">
                    <Badge tone={entry.matched ? 'insert' : 'neutral'}>{entry.kind}</Badge>
                    <span className="min-w-0 flex-1 text-foreground/90">
                      <span className="font-medium">{entry.name}</span>
                      {entry.why ? ` — ${entry.why}` : ''}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <details>
            <summary className="min-h-11 cursor-pointer py-2 text-xs text-muted-foreground">
              Full decision payload
            </summary>
            <JsonView value={data} />
          </details>
        </div>
      )}
    </li>
  )
}

/** One routing session, showing the slots it offered and whether any were taken. */
function RouteRow({ route, onBook }) {
  const [open, setOpen] = useState(false)
  const data = route.data || {}
  const slots = data.start_times || []
  const owner = data.owner_name || data.owner_id || 'nobody'

  return (
    <li className="border-b border-border-subtle/15 last:border-0">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        // Named apart from the decision rows on purpose: the same guest and the same
        // rep appear in both, and a reader who cannot tell a routing session from a
        // routing decision is reading a log they cannot act on.
        aria-label={`Routing session: ${data.guest_email || 'no guest email'}, ${
          data.state === 'open' ? 'still open' : 'already booked'
        } with ${owner}`}
        className="flex w-full min-h-11 cursor-pointer flex-wrap items-center gap-3 py-2 text-left
          transition-colors duration-150 hover:bg-muted/40"
      >
        <Badge tone={data.state === 'open' ? 'update' : 'insert'}>{data.state}</Badge>
        <span className="min-w-0 flex-1 truncate font-mono text-[13px] text-foreground">
          {data.guest_email || '(no guest email)'} → {owner}
        </span>
        <span className="shrink-0 font-mono text-xs text-muted-foreground">
          {slots.length} slots
        </span>
      </button>

      {open && (
        <div className="space-y-3 rounded-lg border border-border-subtle/25 bg-background/40 p-3">
          <dl className="grid gap-2 sm:grid-cols-2">
            <Fact label="Routing id">{route.id?.slice(-10)}</Fact>
            <Fact label="Link">{data.link_key || data.link_id?.slice(-8)}</Fact>
            <Fact label="Owner">{data.owner_name || data.owner_id}</Fact>
            <Fact label="Matched">{data.matched_object_type || 'none'}</Fact>
            <Fact label="Booked at">{data.booked_start_time ? absoluteTime(data.booked_start_time) : '—'}</Fact>
            <Fact label="Opened">{absoluteTime(data.opened_at)}</Fact>
          </dl>

          {slots.length > 0 && (
            <div>
              <Subhead count={slots.length}>Slots offered</Subhead>
              <div className="flex flex-wrap gap-2">
                {slots.slice(0, 24).map((slot) => (
                  <Button
                    key={slot}
                    variant={data.state === 'open' ? 'secondary' : 'ghost'}
                    disabled={data.state !== 'open'}
                    // The visible label is localised; this one is the instant the API
                    // expects back. A booker's button has to say the same time in the
                    // form the second call will accept, or the rep reads a time and
                    // the server reads another.
                    aria-label={`Book the slot starting ${slot}`}
                    onClick={() => onBook(route, slot)}
                  >
                    {absoluteTime(slot)}
                  </Button>
                ))}
              </div>
              {slots.length > 24 && (
                <p className="mt-2 text-xs text-muted-foreground">
                  Showing the first 24 of {slots.length}.
                </p>
              )}
            </div>
          )}

          {slots.length === 0 && (
            <Note tone="warn">
              This session offered no slots. The owner&apos;s calendar had nothing free inside the
              link&apos;s interval, which is an answer rather than a failure.
            </Note>
          )}
        </div>
      )}
    </li>
  )
}

/** One booking, with a cancel action that releases the slot. */
function BookingRow({ booking, roomId, onCancel, canCancel }) {
  const data = booking.data || {}
  return (
    <li
      className="flex min-h-11 flex-wrap items-center gap-3 border-b border-border-subtle/15 py-2 last:border-0"
      aria-label={`Booking: ${data.guest_email} with ${data.owner_name || data.owner_id}, ${data.state}`}
    >
      <Badge tone={data.state === 'confirmed' ? 'insert' : 'delete'}>{data.state}</Badge>
      <span className="min-w-0 flex-1 truncate font-mono text-[13px] text-foreground">
        {data.guest_email} → {data.owner_name || data.owner_id}
      </span>
      <span className="shrink-0 font-mono text-xs text-muted-foreground">
        {absoluteTime(data.start_time)}
      </span>
      {canCancel && data.state === 'confirmed' && (
        <Button variant="danger" onClick={() => onCancel(roomId, booking)}>
          Cancel booking
        </Button>
      )}
    </li>
  )
}

/** One declared link, with its chain rendered as it will actually be evaluated. */
function LinkRow({ link, onDelete }) {
  const [open, setOpen] = useState(false)
  const data = link.data || {}
  const chain = data.rules || []

  return (
    <li className="border-b border-border-subtle/15 last:border-0">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-label={`Ownership link: ${data.name || data.linkId}, ${
          link.has_catch_all ? 'with a catch-all' : 'without a catch-all'
        }`}
        className="flex w-full min-h-11 cursor-pointer flex-wrap items-center gap-3 py-2 text-left
          transition-colors duration-150 hover:bg-muted/40"
      >
        <Icon path={Glyphs.owner} size={15} className="text-accent" />
        <span className="min-w-0 flex-1 truncate font-mono text-[13px] text-foreground">
          {data.name || data.linkId}
        </span>
        <Badge tone="neutral">{data.type}</Badge>
        <Badge tone={link.has_catch_all ? 'insert' : 'delete'}>
          {link.has_catch_all ? 'catch-all' : 'no catch-all'}
        </Badge>
        {!data.enabled && <Badge tone="delete">disabled</Badge>}
        <span className="shrink-0 font-mono text-xs text-muted-foreground">{data.linkId}</span>
      </button>

      {open && (
        <div className="space-y-3 rounded-lg border border-border-subtle/25 bg-background/40 p-3">
          <dl className="grid gap-2 sm:grid-cols-2">
            <Fact label="Type">{data.type}</Fact>
            <Fact label="Edge link id">{data.linkId}</Fact>
            <Fact label="Resolution">{data.resolution_source}</Fact>
            <Fact label="Order">{(data.resolution_order || []).join(' → ')}</Fact>
            <Fact label="Duration">{data.interval?.duration_minutes} min</Fact>
            <Fact label="Notice">{data.interval?.min_notice_minutes ?? 0} min</Fact>
          </dl>

          {data.interval && (
            <Note>
              Offers slots from {absoluteTime(data.interval.start)} to {absoluteTime(data.interval.end)}.
            </Note>
          )}

          <div>
            <Subhead count={chain.length}>Routing chain</Subhead>
            <ol className="space-y-1">
              {chain.map((rule, index) => (
                <li key={`${rule.name}-${index}`} className="flex items-center gap-2 text-xs">
                  <span className="font-mono text-muted-foreground">{index + 1}</span>
                  <Badge tone={rule.kind === 'catch_all' ? 'restore' : 'update'}>{rule.kind}</Badge>
                  <span className="text-foreground/90">
                    {rule.name}
                    {rule.team ? ` — team ${rule.team}` : ''}
                    {rule.field ? ` — ${rule.field} == ${JSON.stringify(rule.equals)}` : ''}
                    {rule.owner_id ? ` — owner ${rule.owner_id}` : ''}
                  </span>
                </li>
              ))}
            </ol>
          </div>

          {data.nodes?.length > 0 && (
            <div>
              <Subhead count={data.nodes.length}>Nodes</Subhead>
              <div className="flex flex-wrap gap-1">
                {data.nodes.map((node) => (
                  <Badge key={node} tone="neutral">
                    {node}
                  </Badge>
                ))}
              </div>
            </div>
          )}

          {data.history?.length > 0 && (
            <div>
              <Subhead count={data.history.length}>Recent decisions on this link</Subhead>
              <ul className="space-y-1">
                {data.history.slice(0, 5).map((entry) => (
                  <li key={entry.decision_id} className="flex items-center gap-2 text-xs">
                    <OutcomeChip outcome={entry.outcome} glyphs={Glyphs} />
                    <span className="font-mono text-muted-foreground">{relativeTime(entry.at)}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <Button variant="danger" onClick={() => onDelete(link)}>
            Delete link
          </Button>
        </div>
      )}
    </li>
  )
}

/** The read-only preview plus the two researched calls, against a chosen room. */
function TryAGuest({ links }) {
  const rooms = useAsync(() => api.listRecords('room', { limit: 50 }), [])
  const [roomId, setRoomId] = useState('')
  const [linkId, setLinkId] = useState('')
  const [guestEmail, setGuestEmail] = useState('')
  const [check, setCheck] = useState(null)
  const [session, setSession] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState('')

  const rooms_ = rooms.data?.records || []

  // Default to the first room and the first link rather than rendering an empty
  // picker: a reviewer opening this tab should be able to press the button.
  useEffect(() => {
    if (!roomId && rooms_.length > 0) setRoomId(rooms_[0].id)
  }, [roomId, rooms_])

  useEffect(() => {
    if (!linkId && links.length > 0) setLinkId(links[0].id)
  }, [linkId, links])

  const selected = useMemo(() => links.find((entry) => entry.id === linkId), [links, linkId])

  async function run(name, fn) {
    setBusy(name)
    setError(null)
    try {
      await fn()
    } catch (problem) {
      setError(problem)
    } finally {
      setBusy('')
    }
  }

  async function preview() {
    setSession(null)
    await run('check', async () => {
      setCheck(await ownershipApi.check(roomId, { link_id: linkId, guestEmail }))
    })
  }

  async function openSession() {
    setCheck(null)
    await run('init', async () => {
      setSession(await ownershipApi.initSimple(roomId, { link_id: linkId, guestEmail }))
    })
  }

  async function book(slot) {
    await run('book', async () => {
      await ownershipApi.scheduleSimple(roomId, {
        routing_id: session.routing_id,
        startTime: slot,
        guestEmail,
      })
      setSession(null)
    })
  }

  return (
    <Card>
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="Room" hint="The room this prospect belongs to" id="wf053-room">
          <select
            id="wf053-room"
            className={inputClass}
            value={roomId}
            onChange={(event) => setRoomId(event.target.value)}
          >
            {rooms_.map((room) => (
              <option key={room.id} value={room.id}>
                {room.data?.name || room.id}
              </option>
            ))}
          </select>
        </Field>

        <Field label="Ownership link" hint="Only Ownership links route by CRM owner" id="wf053-link">
          <select
            id="wf053-link"
            className={inputClass}
            value={linkId}
            onChange={(event) => setLinkId(event.target.value)}
          >
            {links.map((link) => (
              <option key={link.id} value={link.id}>
                {link.data?.name || link.data?.linkId}
              </option>
            ))}
          </select>
        </Field>

        <Field
          label="Guest email"
          hint="Required on an Ownership link — it is what resolves the owner"
          id="wf053-guest"
        >
          <input
            id="wf053-guest"
            className={inputClass}
            value={guestEmail}
            onChange={(event) => setGuestEmail(event.target.value)}
            placeholder="lead@example.com"
          />
        </Field>
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        <Button icon="search" disabled={!roomId || !linkId || !guestEmail || busy !== ''} onClick={preview}>
          Who would this reach?
        </Button>
        <Button
          variant="primary"
          icon="plus"
          disabled={!roomId || !linkId || !guestEmail || busy !== ''}
          onClick={openSession}
        >
          Open a routing session
        </Button>
      </div>

      {error && (
        <div className="mt-4">
          <ErrorNote error={error} />
        </div>
      )}

      {check && (
        <div className="mt-4 space-y-2">
          <Subhead>Check — wrote nothing</Subhead>
          <Note tone="good">
            Would route to <strong>{check.would_route_to_name || check.would_route_to || 'nobody'}</strong>{' '}
            ({check.outcome}) across {check.would_route_to_name ? 'the CRM owner chain' : 'the catch-all'}.
          </Note>
          <p className="text-xs text-muted-foreground">
            {check.slots_offered} slots would be offered, first at{' '}
            {check.start_times?.[0] ? absoluteTime(check.start_times[0]) : '—'}.
          </p>
        </div>
      )}

      {session && (
        <div className="mt-4 space-y-2">
          <Subhead count={session.start_times.length}>Slots from this session</Subhead>
          <Note>
            Routed to <strong>{session.owner_name}</strong> by{' '}
            <strong>{session.rule?.name || 'the CRM owner'}</strong> ({session.outcome}).
          </Note>
          {session.start_times.length === 0 ? (
            <Note tone="warn">No slots inside this link&apos;s interval.</Note>
          ) : (
            <div className="flex flex-wrap gap-2">
              {session.start_times.slice(0, 24).map((slot) => (
                <Button
                  key={slot}
                  disabled={busy !== ''}
                  // Named by the instant the API expects, not by the localised
                  // label below, for the same reason as the session rows: the
                  // second call is checked against the offered slot list.
                  aria-label={`Book the slot starting ${slot}`}
                  onClick={() => book(slot)}
                >
                  Book {absoluteTime(slot)}
                </Button>
              ))}
            </div>
          )}
        </div>
      )}

      {selected && (
        <div className="mt-4 space-y-2">
          <Quote source="WF-053 — Ownership">
            “Ownership – routes to the owner of the guest&apos;s CRM record (lead, contact, or account
            owner), resolved at booking time.”
          </Quote>
          {selected.data?.interval && (
            <Note>
              {selected.data.name} offers {selected.data.interval.duration_minutes}-minute meetings from{' '}
              {absoluteTime(selected.data.interval.start)}.
            </Note>
          )}
        </div>
      )}
    </Card>
  )
}

/** One named judgement call, with the researched sentence it rests on. */
function InferenceCard({ entry }) {
  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <h3 className="font-mono text-sm font-semibold text-foreground">{entry.id}</h3>
        <Badge tone="neutral">{entry.topic}</Badge>
      </div>
      <p className="mt-2 text-sm text-foreground/90">{entry.topic}</p>
      <div className="mt-3 space-y-2 text-xs">
        <div>
          <p className="font-medium text-muted-foreground">What the research says</p>
          <p className="text-foreground/90 italic">{entry.basis}</p>
        </div>
        <div>
          <p className="font-medium text-muted-foreground">What this build chose</p>
          <JsonView value={entry.value} />
        </div>
        <div>
          <p className="font-medium text-muted-foreground">Why</p>
          <p className="text-foreground/90">{entry.why}</p>
        </div>
        <div className="grid gap-1 sm:grid-cols-2">
          <Fact label="Change it">{entry.change_it}</Fact>
          <Fact label="Blast radius">{entry.blast_radius}</Fact>
        </div>
      </div>
    </Card>
  )
}

export default function OwnershipRouting() {
  const vocabulary = useAsync(() => ownershipApi.vocabulary(), [])
  const inferences = useAsync(() => ownershipApi.inferences(), [])
  const catalog = useAsync(() => ownershipApi.catalog(), [])
  const [tab, setTab] = useState('decisions')

  const decisions = useAsync(() => ownershipApi.decisions({ limit: 60 }), [])
  const routes = useAsync(() => ownershipApi.routes({ limit: 40 }), [])
  const links = useAsync(() => ownershipApi.links({ limit: 40 }), [])
  const summary = useAsync(() => ownershipApi.summary(), [])

  const rooms = useAsync(() => api.listRecords('room', { limit: 50 }), [])
  const [roomId, setRoomId] = useState('')
  const roomRows = rooms.data?.records || []
  const activeRoom = roomId || roomRows[0]?.id || ''
  const bookings = useAsync(
    () => (activeRoom ? ownershipApi.roomBookings(activeRoom, { limit: 40 }) : Promise.resolve({ bookings: [] })),
    [activeRoom],
  )

  async function reload() {
    await Promise.all([decisions.refetch(), routes.refetch(), links.refetch(), summary.refetch(), bookings.refetch()])
  }

  async function book(route, slot) {
    await ownershipApi.scheduleSimple(activeRoom, {
      routing_id: route.id,
      startTime: slot,
      guestEmail: route.data?.guest_email,
    })
    await reload()
  }

  async function cancel(targetRoom, booking) {
    await ownershipApi.cancelBooking(targetRoom, booking.id)
    await reload()
  }

  async function removeLink(link) {
    await ownershipApi.deleteLink(link.id)
    await reload()
  }

  const loading = vocabulary.loading || inferences.loading || catalog.loading
  const loadError = vocabulary.error || inferences.error || catalog.error

  const totals = summary.data || {}
  const vocab = vocabulary.data || {}

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-foreground">Ownership routing</h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            An Ownership scheduling link resolves the lead, contact, or account owner of the guest&apos;s
            CRM record at booking time, reads that owner&apos;s connected calendar, and offers the
            prospect the owner&apos;s slots.
          </p>
        </div>
        <nav aria-label="Sections" className="flex flex-wrap gap-1">
          {SECTIONS.map((section) => (
            <Button
              key={section.id}
              variant={tab === section.id ? 'primary' : 'ghost'}
              onClick={() => setTab(section.id)}
              aria-current={tab === section.id}
            >
              <Icon path={Glyphs[section.glyph]} size={15} />
              {section.label}
            </Button>
          ))}
        </nav>
      </header>

      {loadError ? (
        <ErrorNote error={loadError} onRetry={vocabulary.refetch} />
      ) : loading ? (
        <Spinner label="Loading ownership routing" />
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard label="Links" value={totals.links ?? 0} hint={`${totals.links_enabled ?? 0} enabled`} icon="rooms" />
            <StatCard
              label="Routes"
              value={totals.routes ?? 0}
              hint={`${totals.routes_open ?? 0} still open`}
              icon="schema"
            />
            <StatCard
              label="Bookings"
              value={totals.bookings_confirmed ?? 0}
              hint={`${totals.bookings_cancelled ?? 0} cancelled`}
              icon="database"
            />
            <StatCard
              label="Routed to catch-all"
              value={totals.decisions_by_outcome?.catch_all ?? 0}
              hint="nobody owned the record"
              icon="audit"
            />
          </div>

          {tab === 'decisions' && (
            <div className="space-y-4">
              <Card>
                <Quote source="WF-053 — research evidence">
                  “For Ownership links, also pass <code>guestEmail</code> in the init call – it is
                  required so Chili Piper can resolve the owner from your CRM.”
                </Quote>
              </Card>

              <Card>
                <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                  <Subhead count={decisions.data?.count ?? 0}>Routing decisions</Subhead>
                  <Button icon="refresh" onClick={reload}>
                    Refresh
                  </Button>
                </div>
                {decisions.loading ? (
                  <Spinner label="Loading decisions" />
                ) : decisions.error ? (
                  <ErrorNote error={decisions.error} onRetry={decisions.refetch} />
                ) : decisions.data?.count === 0 ? (
                  <EmptyState
                    title="No routing decisions yet"
                    description="Open a routing session from the Try a guest tab and one will appear here."
                  />
                ) : (
                  <ul>
                    {decisions.data.decisions.map((decision) => (
                      <DecisionRow key={decision.id} decision={decision} />
                    ))}
                  </ul>
                )}
              </Card>

              <Card>
                <Subhead count={routes.data?.count ?? 0}>Routing sessions</Subhead>
                {routes.loading ? (
                  <Spinner label="Loading sessions" />
                ) : routes.error ? (
                  <ErrorNote error={routes.error} onRetry={routes.refetch} />
                ) : routes.data?.count === 0 ? (
                  <EmptyState
                    title="No routing sessions"
                    description="A session is what init-simple opens, and what schedule-simple books against."
                  />
                ) : (
                  <ul>
                    {routes.data.routes.map((route) => (
                      <RouteRow key={route.id} route={route} onBook={book} />
                    ))}
                  </ul>
                )}
              </Card>

              <Card>
                <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                  <Subhead count={bookings.data?.count ?? 0}>Bookings</Subhead>
                  <select
                    aria-label="Room"
                    className={`${inputClass} max-w-xs`}
                    value={activeRoom}
                    onChange={(event) => setRoomId(event.target.value)}
                  >
                    {roomRows.map((room) => (
                      <option key={room.id} value={room.id}>
                        {room.data?.name || room.id}
                      </option>
                    ))}
                  </select>
                </div>
                {bookings.loading ? (
                  <Spinner label="Loading bookings" />
                ) : bookings.error ? (
                  <ErrorNote error={bookings.error} onRetry={bookings.refetch} />
                ) : bookings.data?.count === 0 ? (
                  <EmptyState
                    title="No bookings in this room"
                    description="Book one of the offered slots from a routing session above."
                  />
                ) : (
                  <ul>
                    {bookings.data.bookings.map((booking) => (
                      <BookingRow
                        key={booking.id}
                        booking={booking}
                        roomId={activeRoom}
                        canCancel={Boolean(activeRoom)}
                        onCancel={cancel}
                      />
                    ))}
                  </ul>
                )}
              </Card>
            </div>
          )}

          {tab === 'links' && (
            <div className="space-y-4">
              <Card>
                <Subhead>The five researched link types</Subhead>
                <p className="mb-3 text-xs text-muted-foreground">
                  Chili Pepper publishes all five. This workflow routes one of them, and says which
                  rather than accepting a link and quietly ignoring its type.
                </p>
                <ul className="space-y-2">
                  {(vocab.link_types || []).map((entry) => (
                    <li key={entry.type} className="flex flex-wrap items-start gap-2">
                      <Badge tone={entry.supported ? 'insert' : 'neutral'}>{entry.label}</Badge>
                      <span className="min-w-0 flex-1 text-sm text-foreground/90">
                        {`routes by ${entry.routes_by}${entry.note ? ` — ${entry.note}` : ''}`}
                      </span>
                    </li>
                  ))}
                </ul>
                <div className="mt-3">
                  <Quote source="WF-053 — node guardrail">
                    “{vocab.node_guardrail?.quote}”
                  </Quote>
                </div>
              </Card>

              <Card>
                <Subhead count={links.data?.count ?? 0}>Ownership links</Subhead>
                {links.loading ? (
                  <Spinner label="Loading links" />
                ) : links.error ? (
                  <ErrorNote error={links.error} onRetry={links.refetch} />
                ) : links.data?.count === 0 ? (
                  <EmptyState
                    title="No Ownership links declared"
                    description="An administrator creates an Ownership scheduling link before a prospect can reach one."
                  />
                ) : (
                  <ul>
                    {links.data.links.map((link) => (
                      <LinkRow key={link.id} link={link} onDelete={removeLink} />
                    ))}
                  </ul>
                )}
              </Card>

              <div className="grid gap-4 lg:grid-cols-2">
                <Card>
                  <Subhead count={catalog.data?.rep_count ?? 0}>Reps and their calendars</Subhead>
                  <p className="mb-2 text-xs text-muted-foreground">
                    Availability is read from the resolved owner&apos;s connected calendar, so a rep
                    without one has no slots to offer.
                  </p>
                  <ul className="space-y-1">
                    {(catalog.data?.reps || []).map((rep) => (
                      <li key={rep.id} className="flex flex-wrap items-center gap-2 text-xs">
                        <Icon path={Glyphs.owner} size={14} className="text-accent" />
                        <span className="min-w-0 flex-1 truncate text-foreground">{rep.name}</span>
                        {rep.team && <Badge tone="neutral">{rep.team}</Badge>}
                        <Badge tone={rep.calendar?.connected === false ? 'delete' : 'insert'}>
                          {rep.calendar?.provider || 'no calendar'}
                        </Badge>
                      </li>
                    ))}
                  </ul>
                </Card>

                <Card>
                  <Subhead count={catalog.data?.record_count ?? 0}>CRM records</Subhead>
                  <p className="mb-2 text-xs text-muted-foreground">
                    Resolved in order: {(vocab.resolution_order || []).join(' → ')}.
                  </p>
                  <ul className="space-y-1">
                    {(catalog.data?.records || []).slice(0, 20).map((record) => (
                      <li key={record.id} className="flex flex-wrap items-center gap-2 text-xs">
                        <Badge tone="neutral">{record.object_type}</Badge>
                        <span className="min-w-0 flex-1 truncate font-mono text-foreground">
                          {record.email || record.domain || record.name}
                        </span>
                        <span className="font-mono text-muted-foreground">{record.owner_id || 'no owner'}</span>
                      </li>
                    ))}
                  </ul>
                </Card>
              </div>
            </div>
          )}

          {tab === 'try' && <TryAGuest links={links.data?.links || []} />}

          {tab === 'inferences' && (
            <div className="space-y-3">
              <Card>
                <Subhead count={inferences.data?.count ?? 0}>Judgement calls</Subhead>
                <p className="text-xs text-muted-foreground">
                  What the research fixes, and what this build chose where the research is silent.
                  Each entry says how to change it and what it would move.
                </p>
                <div className="mt-3">
                  <Quote source="WF-053 — research evidence">
                    “{inferences.data?.sourced_quotes?.ownership}”
                  </Quote>
                </div>
              </Card>
              {(inferences.data?.inferences || []).map((entry) => (
                <InferenceCard key={entry.id} entry={entry} />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  )
}
