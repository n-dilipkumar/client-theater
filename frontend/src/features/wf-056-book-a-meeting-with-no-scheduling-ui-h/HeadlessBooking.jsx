/**
 * Headless booking: book a meeting with no scheduling UI (WF-056).
 *
 * Five sections, in the order the researched flow happens in them.
 *
 * **Credentials** first, because nothing works without one. The rule the
 * research states is precise: "Admin generates a scoped API token in `Command
 * Center > Credentials` (`Generate Token`), choosing the `Schedule` permission
 * for the relevant section (Concierge / Scheduling-links / Handoff) plus `Read`
 * where listing assets is needed. Token is shown once. Admins only." So the form
 * takes a role, a set of sections and a set of permissions, and the *generated
 * token is shown exactly once* — in a panel that says so and tells the reader to
 * copy it now, because it cannot be shown again.
 *
 * **Assets** are the bookable targets, and the three surfaces are addressed three
 * different ways in the researched endpoints, so the create form asks for a
 * router slug, or a link id and type, or a workspace and a booker, depending on
 * which surface is selected. The `Ownership` type additionally demands a guest
 * email, which the research states and this page surfaces as a hint rather than
 * letting a caller discover it as a 400.
 *
 * **Two calls** is the section that matters, and it is the one a rep has to be
 * able to *drive*. Call #1 returns a `routeId` and a slot list; the slot buttons
 * are the session's own UTC strings, verbatim, because that is what call #2
 * takes back. Booking a slot, then pressing it again, is how the researched rule
 * becomes visible rather than merely obeyed: the second press is refused, and
 * the page says the remedy is a fresh discover.
 *
 * **Sessions and meetings** are the log. Sessions are listed with their state,
 * and a spent one is visibly spent. Meetings carry `invites_sent: false`, which
 * this page states in words: the research says invites go out immediately, the
 * commit is implemented, and the transmission is not.
 *
 * **What this infers** collects the judgement calls. The research is specific
 * about the wire and silent about almost everything around it — no durations, no
 * grid, no record shapes — so those decisions are product behaviour, and a
 * reviewer should be able to disagree with one by name rather than find it in a
 * diff.
 *
 * Every picker is rendered from `/vocabulary`, never from a list compiled into
 * this file, so a team that adds a section ships a record rather than a change
 * here.
 *
 * One thing this page cannot show: the machine-readable `reason` a refusal
 * carries. The shared `apiRequest` surfaces only `detail` and `status`, and that
 * file is not this feature's to edit - so a refusal renders as the sentence the
 * backend wrote, which is what a person needs, while a programmatic caller
 * reads `reason` from the response body. See `./api.js` for the note.
 */

import { useEffect, useMemo, useState } from 'react'
import { api, relativeTime, absoluteTime } from '@/lib/api'
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
import { headlessApi } from './api'
import Glyphs from './icons'
import { CallChip, Fact, RefusalNote, SlotButton, StateChip } from './primitives'

const VIEWS = [
  { id: 'book', label: 'Book headlessly', glyph: 'headless' },
  { id: 'sessions', label: 'Sessions', glyph: 'session' },
  { id: 'assets', label: 'Bookable assets', glyph: 'asset' },
  { id: 'credentials', label: 'Credentials', glyph: 'token' },
  { id: 'inferences', label: 'What this infers', glyph: 'inference' },
]

/** The first room on the account, which is what the demo seeds sessions against. */
function useFirstRoom() {
  const { data, loading, error } = useAsync(() => api.listRecords('room', { limit: 1 }), [])
  const room = data?.records?.[0] ?? null
  return { room, roomId: room?.id ?? '', loading, error }
}

export default function HeadlessBooking() {
  const [view, setView] = useState('book')
  const { data: vocabulary, loading, error, refetch } = useAsync(
    () => headlessApi.vocabulary(),
    [],
  )
  const { room, roomId, loading: roomLoading } = useFirstRoom()

  if (loading) return <Spinner label="Loading the headless booking vocabulary" />
  if (error) return <ErrorNote error={error} onRetry={refetch} />

  return (
    <div className="space-y-6">
      <header className="space-y-2">
        <h1 className="text-xl font-semibold text-foreground">Book a meeting with no scheduling UI</h1>
        <p className="max-w-3xl text-sm text-muted-foreground">
          Programmatic scheduling means booking without sending the lead through any scheduling UI.
          Your own code - a backend process, a custom frontend, an AI assistant - makes two calls:
          one to discover or route, one to book. Between them sits a{' '}
          <strong className="text-foreground">single-use, short-lived session</strong>, so a
          schedule failure is answered with a fresh discover rather than a retry.
        </p>
      </header>

      <nav aria-label="Headless booking sections" className="flex flex-wrap gap-2">
        {VIEWS.map((entry) => (
          <Button
            key={entry.id}
            onClick={() => setView(entry.id)}
            variant={view === entry.id ? 'primary' : 'secondary'}
            aria-current={view === entry.id ? 'page' : undefined}
          >
            <Icon path={Glyphs[entry.glyph]} size={16} />
            {entry.label}
          </Button>
        ))}
      </nav>

      {roomLoading && <Spinner label="Loading rooms" />}

      {view === 'book' && (
        <BookPanel vocabulary={vocabulary} roomId={roomId} room={room} />
      )}
      {view === 'sessions' && <SessionsPanel roomId={roomId} vocabulary={vocabulary} />}
      {view === 'assets' && <AssetsPanel vocabulary={vocabulary} roomId={roomId} />}
      {view === 'credentials' && <CredentialsPanel vocabulary={vocabulary} />}
      {view === 'inferences' && <InferencesPanel />}
    </div>
  )
}

/* ------------------------------------------------------------------------- */
/* Book: the two researched calls                                            */
/* ------------------------------------------------------------------------- */

function BookPanel({ vocabulary, roomId, room }) {
  const [assets, setAssets] = useState([])
  const [assetId, setAssetId] = useState('')
  const [guestEmail, setGuestEmail] = useState('')
  const [windowHours, setWindowHours] = useState(24)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [session, setSession] = useState(null)
  const [selected, setSelected] = useState({})
  const [result, setResult] = useState(null)

  const loadAssets = useAsync(() => headlessApi.listAssets({}), [])

  useEffect(() => {
    setAssets(loadAssets.data?.assets ?? [])
  }, [loadAssets.data])

  /** A booking changes the asset list only insofar as availability moved, so
   *  re-read the assets this panel owns rather than a summary above it. */
  function refresh() {
    loadAssets.refetch()
  }

  const chosen = useMemo(
    () => assets.find((row) => row.id === assetId) ?? null,
    [assets, assetId],
  )

  /**
   * The window `interval` opens at.
   *
   * Anchored to midnight UTC *tomorrow* rather than to "now": a window that
   * starts at the current time and runs for N hours can fall entirely outside a
   * 09:00-16:00 desk, which is how this page would report `no_availability` for
   * every asset every evening. Midnight UTC tomorrow is always in the future and
   * a day long always covers one working day, for any offset an asset configures.
   */
  function intervalStart() {
    const now = new Date()
    const tomorrow = new Date(
      Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + 1, 0, 0, 0, 0),
    )
    return tomorrow.toISOString().replace(/\.\d{3}Z$/, 'Z')
  }

  async function discover(event) {
    event.preventDefault()
    if (!assetId || !chosen) {
      setError({ message: 'Pick a bookable asset first: call #1 addresses an asset.' })
      return
    }
    setBusy(true)
    setError(null)
    setResult(null)
    setSelected({})
    try {
      const payload = {
        section: chosen.data.section,
        asset_id: assetId,
        interval: {
          startsAt: intervalStart(),
          duration: Number(windowHours) * 60,
        },
        guest: { guestEmail },
      }
      setSession(await headlessApi.discover(roomId, payload))
    } catch (caught) {
      setError(caught)
      setSession(null)
    } finally {
      setBusy(false)
    }
  }

  async function book() {
    if (!session) return
    const pathId = Object.keys(selected)[0]
    if (!pathId) {
      setError({ message: 'Pick one of the start times this session returned.' })
      return
    }
    setBusy(true)
    setError(null)
    try {
      const payload = {
        startTime: selected[pathId],
        guest: { guestEmail },
        ...(session.section === 'handoff' && pathId ? { pathId } : {}),
      }
      setResult(await headlessApi.book(roomId, session.routeId, payload))
    } catch (caught) {
      setError(caught)
      setResult(null)
    } finally {
      setBusy(false)
      refresh()
    }
  }

  if (!roomId) {
    return (
      <EmptyState
        title="No rooms yet"
        description="This workflow books meetings for a room's leads, so it needs a room to scope them to. Seed the demo dataset and reload."
      />
    )
  }

  return (
    <div className="space-y-4">
      <Card className="space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <Icon path={Glyphs.headless} size={18} className="text-accent" />
          <h2 className="text-sm font-semibold text-foreground">Call 1: discover or route</h2>
          <Badge tone="neutral">room: {room?.data?.name ?? roomId}</Badge>
        </div>
        <p className="text-sm text-muted-foreground">
          Returns a <span className="font-mono text-foreground">routeId</span> and a list of{' '}
          <span className="font-mono text-foreground">startTimes</span>. The session it opens is
          single-use and short-lived: a hold on availability, not a reservation.
        </p>

        <form onSubmit={discover} className="grid gap-3 sm:grid-cols-2">
          <Field label="Bookable asset" hint="The research names three surfaces; each is addressed differently.">
            <select
              className={inputClass}
              value={assetId}
              onChange={(event) => setAssetId(event.target.value)}
            >
              <option value="">Choose an asset</option>
              {assets
                .filter((row) => row.data?.enabled !== false)
                .map((row) => (
                  <option key={row.id} value={row.id}>
                    {row.data.name} ({row.data.section}
                    {row.data.link_type ? ` / ${row.data.link_type}` : ''})
                  </option>
                ))}
            </select>
          </Field>

          <Field
            label="Guest email"
            hint={
              chosen?.data?.link_type === 'ownership'
                ? 'Required: an Ownership link resolves the owner from guestEmail.'
                : 'Whoever the meeting is for.'
            }
          >
            <input
              type="email"
              className={inputClass}
              value={guestEmail}
              onChange={(event) => setGuestEmail(event.target.value)}
              placeholder="buyer@example.com"
            />
          </Field>

          <Field label="Window (hours)" hint="interval.duration: the window the slot list is computed over.">
            <input
              type="number"
              min="1"
              max="24"
              className={inputClass}
              value={windowHours}
              onChange={(event) => setWindowHours(event.target.value)}
            />
          </Field>

          <div className="flex items-end">
            <Button type="submit" variant="primary" icon="refresh" disabled={busy}>
              {busy ? 'Working' : 'Discover or route'}
            </Button>
          </div>
        </form>
      </Card>

      {error && (
        <RefusalNote
          reason={error.reason}
          detail={error.message}
          failureTable={vocabulary.schedule_failures}
          nextStep={error.status === 403 ? 'Get a token with the Schedule permission for this section.' : undefined}
        />
      )}

      {session && (
        <Card className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <Icon path={Glyphs.session} size={18} className="text-accent" />
            <h2 className="text-sm font-semibold text-foreground">Call 2: book</h2>
            <Badge tone="neutral">routeId {session.routeId.slice(0, 12)}...</Badge>
            <Badge tone="neutral">expires {relativeTime(session.expires_at)}</Badge>
          </div>
          <p className="text-sm text-muted-foreground">{session.next_step}</p>

          <div className="space-y-3">
            {session.schedulingData.map((entry) => (
              <div key={entry.pathId || 'only'}>
                <p className="mb-1.5 flex items-center gap-2 text-xs font-medium tracking-wide text-muted-foreground uppercase">
                  {entry.pathId ? (
                    <>
                      <Badge tone="neutral">{entry.pathId}</Badge>
                      {entry.label}
                    </>
                  ) : (
                    'Start times, verbatim'
                  )}
                </p>
                {entry.startTimes.length === 0 ? (
                  <p className="text-xs text-muted-foreground">
                    No slot in this window. Widen it, or check the host's calendar blocks.
                  </p>
                ) : (
                  <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
                    {entry.startTimes.map((value) => (
                      <SlotButton
                        key={value}
                        value={value}
                        selected={selected[entry.pathId || 'only'] === value}
                        onSelect={() => setSelected({ [entry.pathId || 'only']: value })}
                      />
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Button variant="primary" icon="plus" onClick={book} disabled={busy}>
              Book the selected slot
            </Button>
            <span className="text-xs text-muted-foreground">
              Press it twice to see the researched rule: a second call on the same routeId is
              refused, because sessions are single-use.
            </span>
          </div>
        </Card>
      )}

      {result && <BookedCard result={result} />}

      {session && <InstructionsCard session={session} />}
    </div>
  )
}

function BookedCard({ result }) {
  const meeting = result.meeting?.data ?? {}
  return (
    <Card className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Icon path={Glyphs.booked} size={18} className="text-accent" />
        <h2 className="text-sm font-semibold text-foreground">Meeting committed</h2>
        <Badge tone="insert">{result.meetingId.slice(0, 16)}...</Badge>
      </div>
      <dl className="grid gap-2 sm:grid-cols-2">
        <Fact label="Start time">{meeting.startTime}</Fact>
        <Fact label="Passed verbatim">{String(meeting.start_time_was_verbatim)}</Fact>
        <Fact label="Path">{meeting.pathId ?? 'none (single-path surface)'}</Fact>
        <Fact label="Host">{meeting.host_name || meeting.host_email}</Fact>
        <Fact label="Provider">{meeting.provider}</Fact>
        <Fact label="Session">{result.routeId.slice(0, 12)}... (now booked)</Fact>
      </dl>

      <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-foreground">
        <p className="flex items-center gap-1.5 font-semibold text-amber-300">
          <Icon path={Glyphs.invite} size={14} />
          Invites recorded, not sent ({result.invites.length})
        </p>
        <p className="mt-1 text-muted-foreground">{result.invite_note}</p>
      </div>

      <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-foreground">
        <p className="flex items-center gap-1.5 font-semibold text-amber-300">
          <Icon path={Glyphs.webhook} size={14} />
          {result.webhook_event} event emitted, not delivered
        </p>
        <p className="mt-1 text-muted-foreground">
          Bookings immediately emit the &ldquo;For New Meeting&rdquo; webhook. The event is written
          in the same transaction as the meeting; this product opens no outbound connection, so it
          is recorded rather than delivered.
        </p>
      </div>

      <p className="text-xs font-medium text-foreground">{result.next_step}</p>
    </Card>
  )
}

/**
 * The researched `Custom API` tab, as data.
 *
 * The research names the feature: a router's `Custom API` tab with a `Copy`
 * button for the URL and a starter body, plus a `Share Instructions` button. So
 * this panel is what that screen hands a developer, and the warnings under it
 * are the two sentences a caller is most likely to get wrong.
 */
function InstructionsCard({ session }) {
  const steps = session.instructions
  if (!steps) return null
  return (
    <Card className="space-y-3">
      <div className="flex items-center gap-2">
        <Icon path={Glyphs.instructions} size={18} className="text-accent" />
        <h2 className="text-sm font-semibold text-foreground">
          Share Instructions: the {steps.transport} calls for {steps.asset_id.slice(0, 12)}...
        </h2>
      </div>
      <p className="text-xs text-muted-foreground">
        Needs {steps.credentials_required.join(' + ')} on the token. MCP mirrors these as{' '}
        {steps.mcp_tools.join(' and ')}.
      </p>
      <ol className="space-y-2">
        {[steps.step_1, steps.step_2].map((step) => (
          <li key={step.name} className="rounded-lg border border-border-subtle/25 bg-background/50 p-3">
            <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
              {step.method} · {step.name}
            </p>
            <p className="mt-1 font-mono text-[13px] break-all text-foreground">{step.path}</p>
            <div className="mt-1.5">
              <JsonView value={step.body} />
            </div>
          </li>
        ))}
      </ol>
      <ul className="space-y-1">
        {steps.warnings.map((warning) => (
          <li key={warning} className="flex items-start gap-1.5 text-xs text-muted-foreground">
            <Icon path={Glyphs.refused} size={13} className="mt-0.5 shrink-0 text-amber-300" />
            {warning}
          </li>
        ))}
      </ul>
    </Card>
  )
}

/* ------------------------------------------------------------------------- */
/* Sessions                                                                   */
/* ------------------------------------------------------------------------- */

function SessionsPanel({ roomId, vocabulary }) {
  const [state, setState] = useState('')
  const sessions = useAsync(() => headlessApi.listSessions(roomId, { state }), [roomId, state])
  const calls = useAsync(() => headlessApi.listCalls(roomId, { limit: 40 }), [roomId])

  if (sessions.loading) return <Spinner label="Loading sessions" />
  if (sessions.error) return <ErrorNote error={sessions.error} onRetry={sessions.refetch} />

  const rows = sessions.data?.sessions ?? []
  const retryCount = calls.data?.by_outcome?.session_consumed ?? 0

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-3">
        <StatCard label="Sessions" value={rows.length} hint="opened for this room" />
        <StatCard
          label="Spent"
          value={rows.filter((row) => row.data.state !== 'open').length}
          hint="booked, failed or expired"
        />
        <StatCard
          label="Retries on a spent routeId"
          value={retryCount}
          hint="the mistake the research forbids"
        />
      </div>

      <Card>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-sm font-semibold text-foreground">Sessions</h2>
          <div className="w-48">
            <Field label="State" id="session-state">
              <select
                id="session-state"
                className={inputClass}
                value={state}
                onChange={(event) => setState(event.target.value)}
              >
                <option value="">All states</option>
                {(vocabulary.session_states ?? []).map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            </Field>
          </div>
        </div>

        {rows.length === 0 ? (
          <div className="mt-4">
            <EmptyState
              title="No sessions yet"
              description="Book headlessly on the first tab, or seed the demo dataset."
              action={
                <Button onClick={sessions.refetch} icon="refresh">
                  Refresh
                </Button>
              }
            />
          </div>
        ) : (
          <ul className="mt-3 divide-y divide-border-subtle/15">
            {rows.map((row) => (
              <SessionRow key={row.id} session={row} />
            ))}
          </ul>
        )}
      </Card>

      <CallLog data={calls.data} />
    </div>
  )
}

function SessionRow({ session }) {
  const [open, setOpen] = useState(false)
  const data = session.data || {}
  const slots = (data.schedulingData ?? []).flatMap((entry) => entry.startTimes || [])

  return (
    <li className="py-1">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex min-h-11 w-full flex-wrap items-center gap-3 py-2 text-left
          transition-colors duration-150 hover:bg-muted/40"
      >
        <StateChip state={data.state} glyphs={Glyphs} />
        <Badge tone="neutral">{data.section}</Badge>
        <span className="min-w-0 flex-1 truncate font-mono text-[13px] text-foreground">
          {data.routeId?.slice(0, 16)}... · {slots.length} slot{slots.length === 1 ? '' : 's'}
        </span>
        <span className="shrink-0 font-mono text-xs text-muted-foreground">
          {relativeTime(data.created_at)}
        </span>
      </button>

      {open && (
        <div className="space-y-2 rounded-lg border border-border-subtle/25 bg-background/40 p-3">
          <dl className="grid gap-2 sm:grid-cols-2">
            <Fact label="routeId">{data.routeId}</Fact>
            <Fact label="Asset">{data.asset_name ?? data.asset_id}</Fact>
            <Fact label="Interval">
              {data.interval_starts_at} + {data.interval_duration_minutes}m
            </Fact>
            <Fact label="TTL">{Math.round((data.timeout_in_ms ?? 0) / 1000)}s</Fact>
            <Fact label="Authorised as">{data.authorised_as}</Fact>
            <Fact label="Busy blocks seen">{data.busy_considered}</Fact>
            <Fact label="Expires">{absoluteTime(data.expires_at)}</Fact>
            <Fact label="Retry same routeId">{String(data.retry_with_same_route_id)}</Fact>
          </dl>
          {data.detail && <p className="text-xs text-muted-foreground">{data.detail}</p>}
          <p className="text-xs font-medium text-foreground">{data.next_step}</p>
          {slots.length > 0 && (
            <details className="text-xs">
              <summary className="min-h-11 cursor-pointer py-2 text-muted-foreground">
                The {slots.length} start times, verbatim
              </summary>
              <ul className="grid grid-cols-2 gap-1 font-mono text-muted-foreground sm:grid-cols-3">
                {slots.map((value) => (
                  <li key={value}>{value}</li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
    </li>
  )
}

/**
 * The call log, refused calls included.
 *
 * This is the panel that makes the researched instruction *visible*. A caller
 * that retried one routeId twice leaves two rows, the second flagged - and a
 * person holding a token can see exactly what their integration did.
 */
function CallLog({ data }) {
  if (data?.loading) return <Spinner label="Loading the call log" />
  const rows = data?.calls ?? []
  return (
    <Card>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-foreground">The headless call log</h2>
        <span className="text-xs text-muted-foreground">
          Every call, including the refused ones.
        </span>
      </div>
      {rows.length === 0 ? (
        <div className="mt-3">
          <EmptyState title="No calls yet" description="Book headlessly and the log fills in." />
        </div>
      ) : (
        <ul className="mt-3 divide-y divide-border-subtle/15">
          {rows.map((row) => {
            const entry = row.data || {}
            return (
              <li key={row.id} className="flex min-h-11 flex-wrap items-center gap-3 py-2">
                <CallChip outcome={entry.outcome} />
                <span className="font-mono text-xs text-muted-foreground">{entry.tool ?? '—'}</span>
                <span className="min-w-0 flex-1 truncate font-mono text-[13px] text-foreground">
                  {entry.start_time ?? entry.detail}
                </span>
                <span className="font-mono text-xs text-muted-foreground">
                  {relativeTime(entry.at ?? row.created_at)}
                </span>
              </li>
            )
          })}
        </ul>
      )}
    </Card>
  )
}

/* ------------------------------------------------------------------------- */
/* Assets and credentials                                                    */
/* ------------------------------------------------------------------------- */

function AssetsPanel({ vocabulary, roomId }) {
  const list = useAsync(() => headlessApi.listAssets({}), [])
  const [form, setForm] = useState({ section: 'concierge', name: '', slug: '', host: '' })
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  if (list.loading) return <Spinner label="Loading bookable assets" />
  if (list.error) return <ErrorNote error={list.error} onRetry={list.refetch} />

  async function create(event) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const payload = {
        name: form.name,
        section: form.section,
        host_name: form.host,
        host_email: `${form.host.split(' ')[0] || 'host'}@example.com`,
        ...(form.section === 'concierge' ? { router_slug: form.slug } : {}),
        ...(form.section === 'links' ? { link_id: form.slug, link_type: 'personal' } : {}),
        ...(form.section === 'handoff'
          ? {
              workspace_id: form.slug,
              booker_id: 'usr-booker',
              paths: [
                { path_id: 'path-default', host_email: 'host@example.com' },
              ],
            }
          : {}),
      }
      await headlessApi.createAsset(payload, { room_id: roomId })
      setForm({ ...form, name: '', slug: '' })
      list.refetch()
    } catch (caught) {
      setError(caught)
    } finally {
      setBusy(false)
    }
  }

  const rows = list.data?.assets ?? []
  return (
    <div className="space-y-4">
      <Card>
        <div className="flex items-center gap-2">
          <Icon path={Glyphs.asset} size={18} className="text-accent" />
          <h2 className="text-sm font-semibold text-foreground">Declare a bookable asset</h2>
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          The three surfaces are addressed three different ways in the researched endpoints, so
          each asks for its own identity field: a router slug, a link id, or a workspace and a
          booker.
        </p>

        <form onSubmit={create} className="mt-3 grid gap-3 sm:grid-cols-2">
          <Field label="Surface">
            <select
              className={inputClass}
              value={form.section}
              onChange={(event) => setForm({ ...form, section: event.target.value })}
            >
              {(vocabulary.sections ?? []).map((name) => (
                <option key={name} value={name}>
                  {name} ({vocabulary.asset_kinds?.[name]})
                </option>
              ))}
            </select>
          </Field>
          <Field label="Name">
            <input
              className={inputClass}
              value={form.name}
              onChange={(event) => setForm({ ...form, name: event.target.value })}
              placeholder="Northwind — Enterprise"
              required
            />
          </Field>
          <Field
            label={form.section === 'concierge' ? 'Router slug' : form.section === 'links' ? 'Link id' : 'Workspace id'}
            hint="The identifier the researched endpoint path carries."
          >
            <input
              className={inputClass}
              value={form.slug}
              onChange={(event) => setForm({ ...form, slug: event.target.value })}
              placeholder="northwind-enterprise"
              required
            />
          </Field>
          <Field label="Host name" hint="The host the calendar invites and availability belong to.">
            <input
              className={inputClass}
              value={form.host}
              onChange={(event) => setForm({ ...form, host: event.target.value })}
              placeholder="Dana Okafor"
              required
            />
          </Field>
          <div className="sm:col-span-2">
            <Button type="submit" variant="primary" icon="plus" disabled={busy}>
              Create asset
            </Button>
          </div>
        </form>
        {error && <RefusalNote reason={error.reason} detail={error.message} />}
      </Card>

      <Card>
        <h2 className="text-sm font-semibold text-foreground">
          {rows.length} bookable asset{rows.length === 1 ? '' : 's'}
        </h2>
        <ul className="mt-3 divide-y divide-border-subtle/15">
          {rows.map((row) => (
            <li key={row.id} className="flex min-h-11 flex-wrap items-center gap-3 py-2">
              <Badge tone="neutral">{row.data.section}</Badge>
              {row.data.link_type && <Badge tone="neutral">{row.data.link_type}</Badge>}
              {row.data.enabled === false && <Badge tone="delete">disabled</Badge>}
              <span className="min-w-0 flex-1 truncate text-sm text-foreground">{row.data.name}</span>
              <span className="truncate font-mono text-xs text-muted-foreground">
                {row.data.host_email} · {row.data.duration_minutes}m · {row.data.provider}
              </span>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  )
}

function CredentialsPanel({ vocabulary }) {
  const list = useAsync(() => headlessApi.listCredentials({}), [])
  const [role, setRole] = useState('admin')
  const [label, setLabel] = useState('')
  const [sections, setSections] = useState([])
  const [permissions, setPermissions] = useState(['schedule', 'read'])
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [revealed, setRevealed] = useState(null)

  if (list.loading) return <Spinner label="Loading credentials" />
  if (list.error) return <ErrorNote error={list.error} onRetry={list.refetch} />

  async function generate(event) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const minted = await headlessApi.createCredential({
        role,
        label,
        sections: sections.length ? sections : undefined,
        permissions,
      })
      setRevealed(minted)
      setLabel('')
      list.refetch()
    } catch (caught) {
      setError(caught)
    } finally {
      setBusy(false)
    }
  }

  function toggle(list, value, setter) {
    setter(list.includes(value) ? list.filter((item) => item !== value) : [...list, value])
  }

  const rows = list.data?.credentials ?? []

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex items-center gap-2">
          <Icon path={Glyphs.token} size={18} className="text-accent" />
          <h2 className="text-sm font-semibold text-foreground">Generate a scoped token</h2>
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          &ldquo;Only users with the <strong className="text-foreground">Admin</strong> role can
          generate API tokens in Command Center. Workspace Managers do not have access to the
          credentials page.&rdquo; The Schedule permission is granted per section, plus Read where
          listing assets is needed.
        </p>

        <form onSubmit={generate} className="mt-3 grid gap-3 sm:grid-cols-2">
          <Field label="Role" hint="The research's rule is about the role, and Workspace Manager is refused by name.">
            <select className={inputClass} value={role} onChange={(event) => setRole(event.target.value)}>
              <option value="admin">admin</option>
              <option value="workspace_manager">workspace_manager (refused)</option>
              <option value="user">user (refused)</option>
            </select>
          </Field>
          <Field label="Label" hint="So a list of tokens is something a person can read.">
            <input
              className={inputClass}
              value={label}
              onChange={(event) => setLabel(event.target.value)}
              placeholder="Northwind integration"
              required
            />
          </Field>

          <fieldset className="sm:col-span-1">
            <legend className="mb-1.5 text-xs font-medium text-muted-foreground">Sections</legend>
            <div className="flex flex-wrap gap-2">
              {(vocabulary.sections ?? []).map((name) => (
                <Button
                  key={name}
                  variant={sections.includes(name) ? 'primary' : 'secondary'}
                  onClick={() => toggle(sections, name, setSections)}
                  aria-pressed={sections.includes(name)}
                >
                  {name}
                </Button>
              ))}
            </div>
            <p className="mt-1 text-xs text-muted-foreground">
              None selected means all three.
            </p>
          </fieldset>

          <fieldset>
            <legend className="mb-1.5 text-xs font-medium text-muted-foreground">Permissions</legend>
            <div className="flex flex-wrap gap-2">
              {(vocabulary.permissions ?? []).map((name) => (
                <Button
                  key={name}
                  variant={permissions.includes(name) ? 'primary' : 'secondary'}
                  onClick={() => toggle(permissions, name, setPermissions)}
                  aria-pressed={permissions.includes(name)}
                >
                  {name}
                </Button>
              ))}
            </div>
          </fieldset>

          <div className="sm:col-span-2">
            <Button type="submit" variant="primary" icon="plus" disabled={busy}>
              Generate token
            </Button>
          </div>
        </form>
        {error && (
          <RefusalNote
            detail={error.message}
            nextStep={
              error.status === 403
                ? 'Only the Admin role can generate a token; Workspace Managers do not have access to the credentials page.'
                : undefined
            }
          />
        )}
      </Card>

      {revealed && (
        <Card className="space-y-2 border-accent/40">
          <div className="flex items-center gap-2">
            <Icon path={Glyphs.token} size={18} className="text-accent" />
            <h2 className="text-sm font-semibold text-foreground">Your token, shown once</h2>
          </div>
          <p className="break-all rounded-lg border border-border-subtle/40 bg-background/70 p-3 font-mono text-sm text-foreground">
            {revealed.token}
          </p>
          <p className="text-xs text-amber-300">{revealed.notice}</p>
          <Button onClick={() => setRevealed(null)}>I have copied it</Button>
        </Card>
      )}

      <Card>
        <h2 className="text-sm font-semibold text-foreground">
          {rows.length} credential{rows.length === 1 ? '' : 's'}
        </h2>
        <p className="mt-0.5 text-xs text-muted-foreground">
          A read never returns the secret: only a prefix and the last four characters.
        </p>
        <ul className="mt-3 divide-y divide-border-subtle/15">
          {rows.map((row) => (
            <li key={row.id} className="flex min-h-11 flex-wrap items-center gap-2 py-2">
              <span className="min-w-0 flex-1 truncate text-sm text-foreground">{row.label}</span>
              {row.sections.map((name) => (
                <Badge key={name} tone="neutral">
                  {name}
                </Badge>
              ))}
              {row.permissions.map((name) => (
                <Badge key={`p-${name}`} tone="insert">
                  {name}
                </Badge>
              ))}
              {row.enabled === false && <Badge tone="delete">disabled</Badge>}
              <span className="font-mono text-xs text-muted-foreground">
                {row.token_prefix}...{row.token_last4}
              </span>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  )
}

/* ------------------------------------------------------------------------- */
/* Inferences                                                                 */
/* ------------------------------------------------------------------------- */

function InferencesPanel() {
  const rules = useAsync(() => headlessApi.rules(), [])
  const inferences = useAsync(() => headlessApi.inferences(), [])

  if (rules.loading || inferences.loading) return <Spinner label="Loading the sourced rules" />
  if (rules.error) return <ErrorNote error={rules.error} onRetry={rules.refetch} />
  if (inferences.error) {
    return <ErrorNote error={inferences.error} onRetry={inferences.refetch} />
  }

  const sourced = rules.data?.rules ?? []
  const assumed = inferences.data?.inferences ?? []

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex items-center gap-2">
          <Icon path={Glyphs.session} size={18} className="text-accent" />
          <h2 className="text-sm font-semibold text-foreground">The sourced half</h2>
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          What the research actually says. Disagreeing with a build decision is a different act
          from disagreeing with the research, and this panel keeps the two apart.
        </p>
        <ul className="mt-3 space-y-2">
          {sourced.map((entry) => (
            <li key={entry.id} className="rounded-lg border border-border-subtle/25 bg-background/50 p-3">
              <p className="flex flex-wrap items-center gap-2 text-sm font-semibold text-foreground">
                <Badge tone="insert">sourced</Badge>
                {entry.id.replace(/_/g, ' ')}
              </p>
              <p className="mt-1 text-sm text-foreground">{entry.rule}</p>
              <p className="mt-1 text-xs text-muted-foreground">&ldquo;{entry.sourced}&rdquo;</p>
            </li>
          ))}
        </ul>
      </Card>

      <Card>
        <div className="flex items-center gap-2">
          <Icon path={Glyphs.inference} size={18} className="text-accent" />
          <h2 className="text-sm font-semibold text-foreground">The assumed half</h2>
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          The research publishes no durations, no grid and no record shapes. Each of these is a
          decision this build made, with the reason and the blast radius, so a reviewer can
          disagree with one by name rather than find it in a diff.
        </p>
        <ul className="mt-3 space-y-2">
          {assumed.map((entry) => (
            <li key={entry.id} className="rounded-lg border border-border-subtle/25 bg-background/50 p-3">
              <p className="flex flex-wrap items-center gap-2 text-sm font-semibold text-foreground">
                <Badge tone="restore">inferred</Badge>
                {entry.topic}
              </p>
              <p className="mt-1 text-sm text-foreground">{entry.why}</p>
              <dl className="mt-2 grid gap-2 sm:grid-cols-2">
                <Fact label="Change it">{entry.change_it}</Fact>
                <Fact label="Blast radius">{entry.blast_radius}</Fact>
              </dl>
              <details className="mt-2 text-xs">
                <summary className="min-h-11 cursor-pointer py-2 text-muted-foreground">
                  The value and the basis
                </summary>
                <JsonView value={{ value: entry.value, basis: entry.basis }} />
              </details>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  )
}
