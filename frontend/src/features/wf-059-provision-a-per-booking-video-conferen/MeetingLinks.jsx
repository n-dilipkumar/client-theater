import { useState } from 'react'

import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorNote,
  Field,
  JsonView,
  Spinner,
  StatCard,
  inputClass,
  useAsync,
} from '@/components/ui'

import { apiRequest } from '@/lib/api'

import { linksApi } from './api'
import { Fact, JoinLink, ReadinessChip, SourceNote, StateChip } from './primitives'

/**
 * Meeting links (WF-059).
 *
 * Four panels, in the order the researched user_flow runs:
 *
 *   1. the **Location picker** - the seven options, several of them, one the
 *      default, each saying whether it mints a one-time link and whether that
 *      needs a connection;
 *   2. the **Integrations tab** - the providers, each with a readiness report,
 *      because "Connecting Zoom on the Integrations tab is mandatory for this
 *      one to work" is only visible when a connected-but-unusable provider is on
 *      the same screen as a Location that needs it;
 *   3. **this room's bookings** - each with its researched Location state, its
 *      join link, and the swap that moved it;
 *   4. **the outbound request** - the exact call this build would send Google or
 *      Cal, and the fact that it does not send it.
 *
 * The room is chosen from the core `/rooms` route through `apiRequest` rather
 * than being threaded through the nav, because a feature must not add a route to
 * `App.jsx` - and a room-scoped page with no room selected has nothing to show.
 */
function MeetingLinks() {
  const [roomId, setRoomId] = useState('')
  const rooms = useAsync(() => apiRequest('/records/room?limit=200'), [])
  const vocabulary = useAsync(() => linksApi.vocabulary(), [])

  if (rooms.loading || vocabulary.loading) return <Spinner label="Loading meeting links" />
  if (rooms.error) return <ErrorNote error={rooms.error} onRetry={rooms.refetch} />
  if (vocabulary.error) return <ErrorNote error={vocabulary.error} onRetry={vocabulary.refetch} />

  const options = rooms.data?.records || []
  const kinds = vocabulary.data?.location_catalogue?.kinds || []
  const providers = vocabulary.data?.providers || {}

  return (
    <div className="space-y-6">
      <header className="space-y-2">
        <h1 className="text-xl font-semibold text-foreground">Meeting links</h1>
        <p className="max-w-3xl text-sm text-muted-foreground">
          Every booking mints its own conference, writes the link into the booking location and the
          meetingLocation, and refuses to reuse a conference another booking already holds. Moving a
          meeting to a different tool re-provisions the link and emails the attendees.
        </p>
      </header>

      <Card>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <Field
            id="wf059-room"
            label="Room"
            hint="Bookings are keyed to the room the buyer was looking at."
          >
            <select
              id="wf059-room"
              className={inputClass}
              value={roomId}
              onChange={(event) => setRoomId(event.target.value)}
            >
              <option value="">Choose a room…</option>
              {options.map((room) => (
                <option key={room.id} value={room.id}>
                  {room.data?.name || room.id}
                </option>
              ))}
            </select>
          </Field>
          <p className="text-xs text-muted-foreground">
            {kinds.length} researched Location options · {providers.picker_count ?? 0} of them mint a
            one-time link · Cal documents {providers.enum_count ?? 0} integrations
          </p>
        </div>
      </Card>

      <Integrations providers={providers} />
      <LocationPicker vocabulary={vocabulary.data} />

      {roomId ? (
        <RoomBookings roomId={roomId} vocabulary={vocabulary.data} />
      ) : (
        <EmptyState
          title="Choose a room"
          description="A booking belongs to the room the buyer was looking at, so its links are scoped to one."
        />
      )}
    </div>
  )
}

/** The Integrations tab, with each provider's readiness beside the picker. */
function Integrations({ providers }) {
  const connections = useAsync(() => linksApi.listConnections(), [])

  if (connections.loading) return <Spinner label="Loading provider connections" />
  if (connections.error) return <ErrorNote error={connections.error} onRetry={connections.refetch} />

  const rows = connections.data?.connections || []

  return (
    <Card>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-sm font-semibold text-foreground">Integrations</h2>
        <Badge tone={connections.data?.ready ? 'insert' : 'delete'}>
          {connections.data?.ready ?? 0} of {connections.data?.count ?? 0} can provision
        </Badge>
      </div>

      <SourceNote cite="Chili Piper, Meeting Types in MyApp">
        Connecting Zoom on the Integrations tab is mandatory for this one to work.
      </SourceNote>

      <div className="mt-4 space-y-3">
        {(providers.providers || []).map((provider) => {
          const connection = rows.find((row) => row.provider === provider.provider)
          return (
            <div
              key={provider.provider}
              className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border-subtle/40 p-3"
            >
              <div className="min-w-0">
                <p className="text-sm font-medium text-foreground">{provider.label}</p>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  {connection
                    ? `Host ${connection.host}`
                    : 'Not connected — a Location on this provider cannot provision'}
                </p>
              </div>
              <div className="flex items-center gap-2">
                {provider.gong_redirects_to_zoom && (
                  <Badge tone="restore">redirects to Zoom</Badge>
                )}
                {provider.in_cal_integration_enum ? (
                  <Badge>Cal enum #{provider.enum_position}</Badge>
                ) : (
                  <Badge tone="neutral">not in Cal&apos;s enum</Badge>
                )}
                {connection ? (
                  <ReadinessChip readiness={connection.readiness} />
                ) : (
                  <Badge tone="delete">Not connected</Badge>
                )}
              </div>
            </div>
          )
        })}
      </div>

      <details className="mt-4">
        <summary className="min-h-11 cursor-pointer py-2 text-xs text-muted-foreground">
          The other {providers.in_cal_enum_count ?? 0} Cal integrations
        </summary>
        <p className="mb-2 text-xs text-muted-foreground">
          Cal documents these and this build can record a connection for any of them, but no
          researched Location option reaches one — so none of them is on the picker.
        </p>
        <div className="flex flex-wrap gap-1.5">
          {(providers.other_cal_integrations || []).map((provider) => (
            <Badge key={provider.provider}>{provider.provider}</Badge>
          ))}
        </div>
      </details>
    </Card>
  )
}

/** The seven researched Location options, with what each one generates. */
function LocationPicker({ vocabulary }) {
  const locations = useAsync(() => linksApi.listLocations(), [])
  const [busy, setBusy] = useState(null)
  const [problem, setProblem] = useState(null)

  if (locations.loading) return <Spinner label="Loading Location options" />
  if (locations.error) return <ErrorNote error={locations.error} onRetry={locations.refetch} />

  const catalogue = vocabulary?.location_catalogue?.kinds || []
  const configured = locations.data?.meeting_locations || []
  const byKind = new Map(configured.map((row) => [row.kind, row]))

  async function setDefault(id) {
    setProblem(null)
    setBusy(id)
    try {
      await linksApi.setDefaultLocation(id)
      locations.refetch()
    } catch (error) {
      setProblem(error)
    } finally {
      setBusy(null)
    }
  }

  return (
    <Card>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-sm font-semibold text-foreground">Meeting Type Location</h2>
        <Badge tone={locations.data?.defaults === 1 ? 'insert' : 'delete'}>
          {locations.data?.defaults ?? 0} default of {locations.data?.count ?? 0} options
        </Badge>
      </div>

      {problem && (
        <div role="alert" className="mb-3 rounded-lg border border-destructive/40 bg-destructive/10 p-3">
          <p className="text-sm text-destructive">{String(problem.message || problem)}</p>
        </div>
      )}

      <div className="space-y-2">
        {catalogue.map((option) => {
          const row = byKind.get(option.kind)
          return (
            <div
              key={option.kind}
              className="flex flex-wrap items-start justify-between gap-3 rounded-lg border border-border-subtle/40 p-3"
            >
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="text-sm font-medium text-foreground">{option.label}</p>
                  {option.one_time && <Badge tone="insert">one-time link</Badge>}
                  {option.guest_supplied && <Badge tone="restore">guest supplies it</Badge>}
                  {row?.is_default && <Badge tone="update">Default</Badge>}
                </div>
                <p className="mt-1 text-xs text-muted-foreground italic">“{option.researched}”</p>
                {row && row.gaps?.length > 0 && (
                  <p className="mt-1 text-xs text-amber-300">
                    Still needs: {row.gaps.join(', ')}
                  </p>
                )}
                {row && (
                  <div className="mt-2">
                    <Fact label="Cal location type">{row.wire_location?.type || '—'}</Fact>
                    {row.wire_location?.integration && (
                      <Fact label="integration">{row.wire_location.integration}</Fact>
                    )}
                    {row.wire_location?.link && <Fact label="link">{row.wire_location.link}</Fact>}
                  </div>
                )}
              </div>
              <div className="flex shrink-0 items-center gap-2">
                {row && !row.is_default && (
                  <Button disabled={busy === row.id} onClick={() => setDefault(row.id)}>
                    Set as Default
                  </Button>
                )}
                {row?.kind_errors?.length > 0 && (
                  <Badge tone="delete">kind is not one of the seven</Badge>
                )}
                {!row && <Badge tone="neutral">Not configured</Badge>}
              </div>
            </div>
          )
        })}
      </div>

      {locations.data?.with_gaps > 0 && (
        <p className="mt-4 text-xs text-muted-foreground">
          {locations.data.with_gaps} option(s) still need something. A blank Conference Details is
          reported rather than refused — an admin filling it in tomorrow is a real workflow — but a
          one-time option with no connection cannot provision at all.
        </p>
      )}
    </Card>
  )
}

/** One room's bookings, their researched Location states, and the swap. */
function RoomBookings({ roomId, vocabulary }) {
  const summary = useAsync(() => linksApi.summary(roomId), [roomId])
  const bookings = useAsync(() => linksApi.listBookings(roomId), [roomId])
  const [problem, setProblem] = useState(null)
  const [busy, setBusy] = useState(null)

  if (summary.loading || bookings.loading) return <Spinner label="Loading this room's meetings" />
  if (summary.error) return <ErrorNote error={summary.error} onRetry={summary.refetch} />
  if (bookings.error) return <ErrorNote error={bookings.error} onRetry={bookings.refetch} />

  const rows = bookings.data?.bookings || []
  const stats = summary.data || {}

  function reload() {
    summary.refetch()
    bookings.refetch()
  }

  async function run(label, work) {
    setProblem(null)
    setBusy(label)
    try {
      await work()
      reload()
    } catch (error) {
      setProblem(error)
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <StatCard label="Meetings" value={stats.count ?? 0} hint="in this room" icon="rooms" />
        <StatCard
          label="Fresh per-booking links"
          value={stats.one_time_linked ?? 0}
          hint="one conference each, never reused"
          icon="schema"
        />
        <StatCard
          label="No link yet"
          value={stats.missing_links ?? 0}
          hint="one-time Locations that have not provisioned"
          icon="database"
        />
        <StatCard
          label="Provider failures"
          value={stats.provider_failures ?? 0}
          hint={`${stats.still_retryable ?? 0} still retryable`}
          icon="audit"
        />
        <StatCard
          label="Blocked options"
          value={stats.locations_without_a_connection ?? 0}
          hint="cannot provision on their provider"
          icon="schema"
        />
      </div>

      {stats.stranded_locations?.length > 0 && (
        <Card className="border-amber-500/30">
          <h2 className="text-sm font-semibold text-foreground">
            Location options that cannot provision
          </h2>
          <ul className="mt-2 space-y-1">
            {stats.stranded_locations.map((row) => (
              <li key={row.location_id} className="text-xs text-amber-300">
                {row.name} ({row.kind}) — missing {row.missing?.join(', ')}
              </li>
            ))}
          </ul>
        </Card>
      )}

      {problem && (
        <div role="alert" className="rounded-lg border border-destructive/40 bg-destructive/10 p-3">
          <p className="text-sm text-destructive">{String(problem.message || problem)}</p>
        </div>
      )}

      {rows.length === 0 ? (
        <EmptyState
          title="No meetings booked in this room"
          description="Provisioning is automatic on booking — take a booking and the link appears here."
        />
      ) : (
        <div className="space-y-3">
          {rows.map((booking) => (
            <BookingRow
              key={booking.id}
              roomId={roomId}
              booking={booking}
              vocabulary={vocabulary}
              busy={busy}
              onSwap={(payload) =>
                run(booking.booking_uid, () => linksApi.swap(roomId, booking.booking_uid, payload))
              }
              onProvision={(payload) =>
                run(booking.booking_uid, () =>
                  linksApi.provision(roomId, booking.booking_uid, payload),
                )
              }
            />
          ))}
        </div>
      )}
    </div>
  )
}

/** One meeting: where it is, and the two researched things you can do about it. */
function BookingRow({ roomId, booking, vocabulary, busy, onSwap, onProvision }) {
  const [open, setOpen] = useState(false)
  const [target, setTarget] = useState('gong')
  const [guestLocation, setGuestLocation] = useState('')

  // The trail and the failure reports are only fetched once the panel is open,
  // so a list of fifty meetings does not make a hundred requests on render.
  const history = useAsync(
    () => (open ? linksApi.history(roomId, booking.booking_uid) : Promise.resolve(null)),
    [open, roomId, booking.booking_uid],
  )
  const reports = useAsync(
    () => (open ? linksApi.providerStatus(roomId, booking.booking_uid) : Promise.resolve(null)),
    [open, roomId, booking.booking_uid],
  )

  const oneTimeKinds = (vocabulary?.location_catalogue?.kinds || []).filter((k) => k.one_time)
  const working = busy === booking.booking_uid
  const previous = booking.previous_location

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-sm font-medium text-foreground">
              {booking.attendee_name || booking.attendee_email || booking.booking_uid}
            </p>
            <StateChip state={booking.state} />
            {booking.provision_attempts > 0 && (
              <Badge tone="delete">
                {booking.provision_attempts} failed attempt
                {booking.provision_attempts === 1 ? '' : 's'}
              </Badge>
            )}
          </div>
          <p className="mt-1 font-mono text-xs text-muted-foreground">{booking.booking_uid}</p>
          <div className="mt-2">
            <JoinLink url={booking.meetingLocation} />
          </div>
          {booking.redirects_to && (
            <p className="mt-1 text-xs text-muted-foreground">
              A guest clicking this lands on <span className="font-mono">{booking.redirects_to}</span>
              — Gong redirects to Zoom.
            </p>
          )}
          {previous?.location_provider && (
            <p className="mt-2 text-xs text-sky-300">
              Moved from {previous.location_provider}
              {previous.meetingLocation ? ` (${previous.meetingLocation})` : ''}
              {booking.location_change_reason ? ` — ${booking.location_change_reason}` : ''}
            </p>
          )}
        </div>

        <Button icon="chevron" onClick={() => setOpen(!open)} aria-expanded={open}>
          {open ? 'Hide' : 'Move or retry'}
        </Button>
      </div>

      {open && (
        <div className="mt-4 space-y-4 border-t border-border-subtle/40 pt-4">
          <SourceNote cite="Cal, update booking location for an existing booking">
            For integration locations (e.g. Zoom, Google Meet, Cal Video), the endpoint also
            provisions a conference link. Attendees are notified of the location change by email.
          </SourceNote>

          {booking.state === 'awaiting-guest' && (
            <div className="space-y-2">
              <Field
                id={`guest-${booking.id}`}
                label="Where will the guest meet?"
                hint="Recorded as attendeeAddress — one of the eight researched location types."
              >
                <input
                  id={`guest-${booking.id}`}
                  className={inputClass}
                  value={guestLocation}
                  onChange={(event) => setGuestLocation(event.target.value)}
                  placeholder="https://… or a room name"
                />
              </Field>
              <Button
                variant="primary"
                disabled={working || !guestLocation.trim()}
                onClick={() => onProvision({ guest_location: guestLocation.trim() })}
              >
                Record the guest&apos;s location
              </Button>
            </div>
          )}

          {!booking.conference_id && booking.location_kind !== 'awaiting-guest' && (
            <div className="space-y-2">
              <p className="text-xs text-muted-foreground">
                This booking has no link. Re-running the automation mints one for it alone — the
                conference identity is derived from the booking, so a retry converges rather than
                racing.
              </p>
              <Button
                variant="primary"
                disabled={working}
                onClick={() => onProvision({ reason: 'explicit re-provision' })}
              >
                Provision a link now
              </Button>
            </div>
          )}

          <div className="space-y-2">
            <Field id={`target-${booking.id}`} label="Move this meeting to">
              <select
                id={`target-${booking.id}`}
                className={inputClass}
                value={target}
                onChange={(event) => setTarget(event.target.value)}
              >
                {oneTimeKinds.map((option) => (
                  <option key={option.kind} value={option.kind}>
                    {option.label}
                  </option>
                ))}
              </select>
            </Field>
            <Button
              disabled={working}
              onClick={() =>
                onSwap({
                  kind: target,
                  reason: 'meeting-moved-tool',
                  detail: 'Moved from the meeting list on the conference-links page.',
                })
              }
            >
              Move and email the attendees
            </Button>
            <p className="text-xs text-muted-foreground">
              A swap to the location this meeting already has is refused: the researched endpoint
              emails every attendee, so a no-op swap would announce that nothing changed.
            </p>
          </div>

          <details>
            <summary className="min-h-11 cursor-pointer py-2 text-xs text-muted-foreground">
              The conference this build would create
            </summary>
            <JsonView value={booking.create_request} />
            <p className="mt-2 text-xs text-muted-foreground">
              Built and stored, not sent. This product is the source of the booking, not a proxy for
              Google or Cal.
            </p>
          </details>

          {reports.data?.count > 0 && (
            <details>
              <summary className="min-h-11 cursor-pointer py-2 text-xs text-muted-foreground">
                Provider failure reports ({reports.data.count})
              </summary>
              <JsonView value={reports.data.provider_status} />
              <p className="mt-2 text-xs text-muted-foreground">
                The provider&apos;s own <span className="font-mono">appsStatus[]</span> report,
                normalised to its four fields and judged against the retry budget. A link that is
                already in place is not taken away by a failure somewhere in the chain.
              </p>
            </details>
          )}

          {history.data && (
            <details>
              <summary className="min-h-11 cursor-pointer py-2 text-xs text-muted-foreground">
                previousLocation trail ({history.data.count})
              </summary>
              <JsonView value={history.data.history} />
            </details>
          )}
        </div>
      )}
    </Card>
  )
}

export default MeetingLinks
