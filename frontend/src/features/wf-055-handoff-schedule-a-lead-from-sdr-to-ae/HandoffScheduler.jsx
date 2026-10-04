/**
 * Handoff scheduler (WF-055).
 *
 * One page, four sections, in the order the researched flow runs in:
 *
 * 1. Pods, and who in each may be assigned a handoff.
 * 2. Routers and their routing paths, each with its gate set spelled out.
 * 3. An evaluation: the SDR enters a guest email or a CRM record id, and the page
 *    shows the one or more routing paths that matched, each with its own start
 *    times, then books one slot on one of them.
 * 4. The routings and the meetings that came out of them.
 *
 * Three things this page refuses to do, each because the research or the design
 * floor requires it. It does not hide a routing path that matched but has no free
 * time, because the SDR's next move is to look at another path. It does not read the
 * calendar of a not-required invitee, and it says which invitees those were so the
 * reason a path was empty is legible. And it does not assert how a path's times are
 * built: the derivation is served from `/inferences` with the quote that fixes it,
 * so a reviewer can disagree with it on screen rather than in a diff.
 *
 * Design floor: every control is `min-h-11`, every glyph sits beside a text label,
 * every status is carried by words rather than by colour alone, and the loading,
 * error and empty branches are all present.
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
import { handoffApi } from './api'
import {
  AssignableChip,
  AvailabilityChip,
  Fact,
  MeetingRow,
  Notice,
  PathRow,
  Quote,
  RoleChip,
  Subhead,
} from './primitives'

function formatTime(value) {
  if (!value) return '--'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return String(value)
  return parsed.toISOString().replace('T', ' ').slice(0, 16)
}

/** How many start times one path shows before it says how many more there are. */
const VISIBLE_SLOTS = 8

export default function HandoffScheduler() {
  const [roomId, setRoomId] = useState('')
  const [workspaceId, setWorkspaceId] = useState('')
  const [requestType, setRequestType] = useState('GuestEmailRequest')
  const [guestEmail, setGuestEmail] = useState('')
  const [crmRecordId, setCrmRecordId] = useState('')
  const [bookerRef, setBookerRef] = useState('')
  const [region, setRegion] = useState('')
  const [productLine, setProductLine] = useState('')
  const [selectedPathId, setSelectedPathId] = useState('')
  const [evaluation, setEvaluation] = useState(null)
  const [booked, setBooked] = useState(null)
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState(null)

  const summary = useAsync(() => handoffApi.summary(), [])
  const catalog = useAsync(() => handoffApi.catalog(), [])
  const vocabulary = useAsync(() => handoffApi.vocabulary(), [])
  const inferences = useAsync(() => handoffApi.inferences(), [])
  const routings = useAsync(() => handoffApi.routings({ limit: 25 }), [])
  const meetings = useAsync(() => handoffApi.meetings({ limit: 25 }), [])

  const reloadWrites = () => {
    routings.refetch()
    meetings.refetch()
    summary.refetch()
  }

  if (summary.loading || catalog.loading) {
    return <Spinner label="Loading handoff pods and routers" />
  }
  if (summary.error) return <ErrorNote error={summary.error} onRetry={summary.refetch} />
  if (catalog.error) return <ErrorNote error={catalog.error} onRetry={catalog.refetch} />

  const counts = summary.data || {}
  const workspaces = catalog.data?.workspaces || []
  const routers = catalog.data?.routers || []
  const workspace = workspaces.find((entry) => entry.id === workspaceId) || null
  // A router belongs to one pod, so the pod's routers are the only candidates. The
  // page reads them by workspace_ref rather than by guessing from a selected id,
  // because a router id and a workspace id are different things.
  const podRouters = workspaceId
    ? routers.filter((entry) => entry.data.workspace_ref === workspaceId)
    : []
  const bookers = workspace ? workspace.users.filter((user) => user.can_book) : []
  const paths = evaluation ? evaluation.paths : []
  const selectedPath = paths.find((entry) => entry.path_id === selectedPathId) || null
  const offerable = selectedPath ? selectedPath.start_times.slice(0, VISIBLE_SLOTS) : []
  const hiddenSlots = selectedPath ? Math.max(0, selectedPath.slot_count - VISIBLE_SLOTS) : 0
  const derivation = (inferences.data?.inferences || []).find(
    (entry) => entry.id === 'inference_path_availability_is_intersection',
  )

  /** The researched init payload, built from the two researched request shapes. */
  function requestPayload() {
    const explicits = {}
    if (region.trim()) explicits.region = region.trim()
    if (productLine.trim()) explicits.product_line = productLine.trim()
    const payload = { type: requestType, booker_ref: bookerRef, crmExplicits: explicits }
    if (requestType === 'GuestEmailRequest') payload.guestEmail = guestEmail
    else payload.id = crmRecordId
    return payload
  }

  function missingInput() {
    if (!roomId.trim()) return 'Enter the room id the handoff is being taken for.'
    if (!workspaceId.trim()) return 'Choose the pod the SDR works in.'
    if (!bookerRef) return 'Choose the SDR who opened the Handoff scheduler.'
    if (requestType === 'GuestEmailRequest' && !guestEmail.trim())
      return 'Enter the lead’s email address.'
    if (requestType === 'CrmRequest' && !crmRecordId.trim()) return 'Enter the CRM record id.'
    return null
  }

  /**
   * Run the router without writing anything.
   *
   * `check` is the read-only half of the researched `init-simple`: the same rule
   * evaluation and the same calendar arithmetic, no routing row and no meeting. So
   * nothing is created and only the preview is set.
   */
  async function evaluate() {
    const missing = missingInput()
    if (missing) {
      setActionError(missing)
      return
    }
    setBusy(true)
    setActionError(null)
    setBooked(null)
    try {
      const result = await handoffApi.check(roomId.trim(), workspaceId, requestPayload())
      setEvaluation(result)
      setSelectedPathId(result.paths.find((entry) => entry.slot_count)?.path_id || '')
    } catch (error) {
      setActionError(error.message || String(error))
      setEvaluation(null)
    } finally {
      setBusy(false)
    }
  }

  /**
   * Open the routing for real, then book the chosen slot in one go.
   *
   * Two researched calls, in their researched order: `init-simple` answers with the
   * routing id, and `schedule-simple` takes it with the router, the path and the
   * booker. The slot is taken from what the first call offered, so the second can
   * only ever name a time the SDR was shown.
   */
  async function bookSlot(startTime) {
    if (!selectedPath) return
    setBusy(true)
    setActionError(null)
    try {
      const opened = await handoffApi.initSimple(roomId.trim(), workspaceId, requestPayload())
      const taken = await handoffApi.scheduleSimple(
        roomId.trim(),
        opened.routing_id,
        selectedPath.router_ref,
        selectedPath.path_id,
        opened.booker_ref,
        { startTime },
      )
      setBooked(taken)
      setEvaluation(opened)
      reloadWrites()
    } catch (error) {
      setActionError(error.message || String(error))
    } finally {
      setBusy(false)
    }
  }

  async function cancelMeeting(meetingId) {
    setBusy(true)
    setActionError(null)
    try {
      await handoffApi.cancelMeeting(meetingId)
      reloadWrites()
    } catch (error) {
      setActionError(error.message || String(error))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-6">
      <header>
        <h1 className="font-display text-2xl font-semibold text-foreground">Handoff scheduler</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          A Handoff Router declares routing paths such as region to an AE pod or product line to
          an AE. The SDR opens the scheduler with the lead’s email or a CRM record id, and the
          router answers with one or more paths, each with its own start times.
        </p>
      </header>

      <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Pods" value={counts.workspaces ?? 0} icon="rooms" hint={counts.users} />
        <StatCard
          label="Routers"
          value={counts.routers ?? 0}
          icon="audit"
          hint={`${counts.paths ?? 0} routing paths`}
        />
        <StatCard
          label="Routings"
          value={counts.routings ?? 0}
          icon="dashboard"
          hint={`${counts.routings_open ?? 0} still open`}
        />
        <StatCard
          label="Meetings"
          value={counts.meetings_confirmed ?? 0}
          icon="search"
          hint={`${counts.meetings ?? 0} in all`}
        />
      </section>

      {(counts.routings_by_outcome?.no_availability ?? 0) > 0 && (
        <Notice tone="warning">
          <span className="font-medium">
            {counts.routings_by_outcome.no_availability} routing(s) matched a path with no free
            time.
          </span>{' '}
          Every calendar that emptied a path is named on the routing, so the next step is a
          different path rather than a different pod.
        </Notice>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <Subhead count={workspaces.length}>Pods</Subhead>
          {workspaces.length === 0 ? (
            <EmptyState
              title="No pods yet"
              description="A pod is one SDR/AE group: its users, their roles, and the routers declared for it."
            />
          ) : (
            <ul className="space-y-2">
              {workspaces.map((entry) => (
                <li key={entry.id}>
                  <button
                    type="button"
                    onClick={() => setWorkspaceId(entry.id)}
                    aria-pressed={workspaceId === entry.id}
                    className={`min-h-11 w-full rounded-sm border px-3 py-2 text-left ${
                      workspaceId === entry.id
                        ? 'border-accent bg-accent-soft'
                        : 'border-border-subtle bg-surface hover:border-accent'
                    }`}
                  >
                    <span className="block text-sm font-medium text-foreground">
                      {entry.data.name}
                    </span>
                    <span className="mt-1 block font-mono text-xs text-muted-foreground">
                      {entry.summary.bookers} bookers, {entry.summary.assignees} assignees,{' '}
                      {entry.users.length} users
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card>
          <Subhead count={workspace ? podRouters.length : routers.length}>Handoff Routers</Subhead>
          {!workspaceId ? (
            <EmptyState
              title="Choose a pod first"
              description="A router is declared inside one pod, so the paths and the people they name are read together."
            />
          ) : podRouters.length === 0 ? (
            <EmptyState
              title="This pod has no router"
              description="An SDR cannot open the Handoff scheduler until the pod declares one."
            />
          ) : (
            <ul className="space-y-3">
              {podRouters.map((entry) => (
                <li key={entry.id}>
                  <p className="text-sm font-medium text-foreground">{entry.data.name}</p>
                  <p className="font-mono text-xs text-muted-foreground">{entry.id}</p>
                  <ul className="mt-2 space-y-1">
                    {entry.paths.map((path) => (
                      <li
                        key={path.path_id}
                        className="flex min-h-11 flex-wrap items-center justify-between gap-2 border-b border-border-subtle py-2 last:border-b-0"
                      >
                        <div className="min-w-0">
                          <p className="truncate text-sm text-foreground">
                            {path.path_name || path.path_id}
                          </p>
                          <p className="truncate font-mono text-xs text-muted-foreground">
                            {path.path_id} to {path.assignee_ref}
                          </p>
                        </div>
                        <div className="flex shrink-0 flex-wrap gap-1.5">
                          <Badge tone="neutral">
                            gated by {path.gating_user_ids.join(', ') || 'nobody'}
                          </Badge>
                          {path.ignored_user_ids.length > 0 && (
                            <Badge tone="restore">
                              calendar not read: {path.ignored_user_ids.join(', ')}
                            </Badge>
                          )}
                        </div>
                      </li>
                    ))}
                  </ul>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      {workspace && (
        <Card>
          <Subhead count={workspace.users.length}>Who may be assigned a handoff</Subhead>
          <ul>
            {workspace.users.map((user) => (
              <li
                key={user.user_id}
                className="flex min-h-11 flex-wrap items-center justify-between gap-2 border-b border-border-subtle py-2 last:border-b-0"
              >
                <div className="min-w-0">
                  <p className="truncate text-sm text-foreground">{user.name}</p>
                  <p className="truncate font-mono text-xs text-muted-foreground">
                    {user.user_id}, roles {user.roles.join(' and ') || 'none'}
                  </p>
                </div>
                <AssignableChip user={user} />
              </li>
            ))}
          </ul>
        </Card>
      )}

      <Card>
        <Subhead>Step 2 and 3: evaluate the router</Subhead>
        <div className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            <Field label="Room id" id="wf055-room" hint="The room whose lead is being handed off.">
              <input
                id="wf055-room"
                className={inputClass}
                value={roomId}
                onChange={(event) => setRoomId(event.target.value)}
                placeholder="room-1"
              />
            </Field>
            <Field
              label="SDR, the booker"
              id="wf055-booker"
              hint="Must hold the booker role on the pod."
            >
              <select
                id="wf055-booker"
                className={inputClass}
                value={bookerRef}
                onChange={(event) => setBookerRef(event.target.value)}
              >
                <option value="">Choose an SDR</option>
                {bookers.map((user) => (
                  <option key={user.user_id} value={user.user_id}>
                    {user.name} ({user.user_id})
                  </option>
                ))}
              </select>
            </Field>
            <Field
              label="Request shape"
              id="wf055-request-type"
              hint="The researched payload has exactly two."
            >
              <select
                id="wf055-request-type"
                className={inputClass}
                value={requestType}
                onChange={(event) => setRequestType(event.target.value)}
              >
                {(vocabulary.data?.request_type_names || ['GuestEmailRequest', 'CrmRequest']).map(
                  (name) => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ),
                )}
              </select>
            </Field>
            {requestType === 'GuestEmailRequest' ? (
              <Field
                label="Guest email"
                id="wf055-guest-email"
                hint="The researched GuestEmailRequest carries it."
              >
                <input
                  id="wf055-guest-email"
                  className={inputClass}
                  value={guestEmail}
                  onChange={(event) => setGuestEmail(event.target.value)}
                  placeholder="lead@example.com"
                />
              </Field>
            ) : (
              <Field
                label="CRM record id"
                id="wf055-crm-record"
                hint="The researched CrmRequest carries an id."
              >
                <input
                  id="wf055-crm-record"
                  className={inputClass}
                  value={crmRecordId}
                  onChange={(event) => setCrmRecordId(event.target.value)}
                  placeholder="00Q5s00000AbCdE"
                />
              </Field>
            )}
            <Field
              label="crmExplicits: region"
              id="wf055-region"
              hint="Passed through to the routing rules."
            >
              <input
                id="wf055-region"
                className={inputClass}
                value={region}
                onChange={(event) => setRegion(event.target.value)}
                placeholder="emea"
              />
            </Field>
            <Field
              label="crmExplicits: product line"
              id="wf055-product-line"
              hint="Any key is allowed. These two are read."
            >
              <input
                id="wf055-product-line"
                className={inputClass}
                value={productLine}
                onChange={(event) => setProductLine(event.target.value)}
                placeholder="platform"
              />
            </Field>
          </div>

          <Button
            variant="primary"
            onClick={evaluate}
            disabled={busy}
            className="min-h-11 w-full sm:w-auto"
          >
            {busy ? 'Evaluating' : 'Which paths would this lead reach?'}
          </Button>

          {actionError && <ErrorNote error={actionError} onRetry={evaluate} />}

          {evaluation && (
            <div className="space-y-3 rounded-sm border border-border-subtle p-3">
              <Subhead count={evaluation.path_count}>Matched routing paths</Subhead>
              <div className="grid gap-2 sm:grid-cols-2">
                <Fact label="outcome">{evaluation.outcome}</Fact>
                <Fact label="paths with times">{evaluation.paths_with_availability}</Fact>
                <Fact label="booker">{evaluation.booker_name}</Fact>
                <Fact label="request">{evaluation.request_type}</Fact>
              </div>
              {(evaluation.shadowed_explicit_keys || []).length > 0 && (
                <Notice tone="warning">
                  <span className="font-medium">
                    Dropped from the rule context: {evaluation.shadowed_explicit_keys.join(', ')}.
                  </span>{' '}
                  Those names are reserved for the researched fields, so the researched value won.
                  The routing still keeps your crmExplicits exactly as sent.
                </Notice>
              )}
              {evaluation.path_count === 0 ? (
                <EmptyState
                  title="No routing path matched"
                  description="Nothing was written. Fix the field the router reads, or widen a path's match block."
                />
              ) : (
                <ul className="space-y-2">
                  {evaluation.paths.map((entry) => (
                    <li key={`${entry.router_ref}-${entry.path_id}`}>
                      <PathRow
                        path={entry}
                        selected={selectedPathId === entry.path_id}
                        disabled={!entry.slot_count}
                        onSelect={() => setSelectedPathId(entry.path_id)}
                      />
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
      </Card>

      {selectedPath && (
        <Card>
          <Subhead count={selectedPath.slot_count}>
            Step 4: book {selectedPath.path_name || selectedPath.path_id}
          </Subhead>
          {offerable.length === 0 ? (
            <EmptyState
              title="This path has no start times"
              description="The chip above names whose calendar emptied it. Pick another path, or widen the interval."
            />
          ) : (
            <>
              <div className="mb-3 flex flex-wrap items-center gap-2">
                <RoleChip role="assignee" person={selectedPath.assignee_name} />
                <RoleChip role="booker" person={evaluation?.booker_name} />
                <AvailabilityChip path={selectedPath} />
              </div>
              <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
                {offerable.map((startTime) => (
                  <li key={startTime}>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => bookSlot(startTime)}
                      aria-label={`Book the handoff starting ${formatTime(startTime)}`}
                      className="min-h-11 w-full rounded-sm border border-border-subtle bg-surface px-3 py-2 text-left font-mono text-xs text-foreground hover:border-accent hover:text-accent disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      {formatTime(startTime)}
                    </button>
                  </li>
                ))}
              </ul>
              {hiddenSlots > 0 && (
                <p className="mt-2 text-xs text-muted-foreground">
                  {hiddenSlots} further start time(s) on this path are not shown here. The backend
                  offered {selectedPath.slot_count} in all.
                </p>
              )}
            </>
          )}
        </Card>
      )}

      {booked && (
        <Card>
          <Subhead>Meeting booked</Subhead>
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <RoleChip role="booker" person={booked.meeting?.data?.booker_name} />
              <RoleChip role="assignee" person={booked.meeting?.data?.assignee_name} />
              <Badge tone="insert">{booked.meeting?.data?.state}</Badge>
            </div>
            <div className="grid gap-2 sm:grid-cols-2">
              <Fact label="starts">{formatTime(booked.start_at)}</Fact>
              <Fact label="ends">{formatTime(booked.end_at)}</Fact>
              <Fact label="router">{booked.router_id}</Fact>
              <Fact label="path">{booked.path_id}</Fact>
            </div>
            <p className="text-xs text-muted-foreground">
              The meeting names the workspace, the router and the path it came from, so a later
              reassignment reopens that same routing context instead of choosing a new router.
            </p>
          </div>
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <Subhead count={routings.data?.count ?? 0}>Routings</Subhead>
          {routings.loading ? (
            <Spinner label="Loading routings" />
          ) : routings.error ? (
            <ErrorNote error={routings.error} onRetry={routings.refetch} />
          ) : (routings.data?.routings || []).length === 0 ? (
            <EmptyState
              title="No routings yet"
              description="Evaluate a pod and the router answers with the paths that matched."
            />
          ) : (
            <ul className="space-y-2">
              {routings.data.routings.map((routing) => (
                <li
                  key={routing.id}
                  className="flex min-h-11 flex-wrap items-center justify-between gap-2 rounded-sm border border-border-subtle px-3 py-2"
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm text-foreground">
                      {routing.data.guest_email || routing.data.crm_record_id}
                    </p>
                    <p className="truncate font-mono text-xs text-muted-foreground">
                      {routing.data.path_count} paths, {routing.data.paths_with_availability} with
                      times
                    </p>
                  </div>
                  <div className="flex shrink-0 gap-1.5">
                    <Badge tone={routing.data.outcome === 'paths_offered' ? 'insert' : 'restore'}>
                      {routing.data.outcome}
                    </Badge>
                    <Badge tone={routing.data.state === 'open' ? 'update' : 'neutral'}>
                      {routing.data.state}
                    </Badge>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card>
          <Subhead count={meetings.data?.count ?? 0}>Meetings</Subhead>
          {meetings.loading ? (
            <Spinner label="Loading meetings" />
          ) : meetings.error ? (
            <ErrorNote error={meetings.error} onRetry={meetings.refetch} />
          ) : (meetings.data?.meetings || []).length === 0 ? (
            <EmptyState
              title="No meetings yet"
              description="Pick a routing path and one of its start times."
            />
          ) : (
            <ul className="space-y-2">
              {meetings.data.meetings.map((meeting) => (
                <MeetingRow
                  key={meeting.id}
                  meeting={meeting}
                  onCancel={() => cancelMeeting(meeting.id)}
                />
              ))}
            </ul>
          )}
        </Card>
      </div>

      <Card>
        <Subhead>How a path&apos;s start times are built</Subhead>
        <div className="space-y-3">
          <Quote source={vocabulary.data?.path_availability?.sourced_from}>
            {vocabulary.data?.path_availability?.offered_when ||
              'every one of those people is free for the whole slot'}
          </Quote>
          <p className="text-sm text-muted-foreground">
            The research says a routing path returns its own start times, and that a Required
            invitee&apos;s availability is considered. It never says which calendar operation
            combines the assignee and that invitee. This build uses an intersection, and booking
            re-checks the same gate set and refuses if anyone has taken the slot since.
          </p>
          {derivation && (
            <dl className="space-y-1 rounded-sm border border-border-subtle p-3">
              <Subhead>Why</Subhead>
              <p className="text-sm text-muted-foreground">{derivation.why}</p>
              <Subhead>Change it by</Subhead>
              <p className="text-sm text-muted-foreground">{derivation.change_if}</p>
              <Subhead>Ratified by Jev</Subhead>
              <Fact label="audit">
                {derivation.jev_audit_id} ({derivation.jev_verdict})
              </Fact>
              <Fact label="selected">{derivation.jev_selected}</Fact>
            </dl>
          )}
          <p className="text-xs text-muted-foreground">
            A router with an empty match block is the catch-all: it takes any lead the specific
            paths did not claim, so every matched path is returned rather than only the best one.
          </p>
        </div>
      </Card>
    </div>
  )
}
