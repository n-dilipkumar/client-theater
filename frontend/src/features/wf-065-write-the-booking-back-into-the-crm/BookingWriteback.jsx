/**
 * Booking writeback: run a router flow's CRM nodes against a booked meeting
 * (WF-065).
 *
 * The page follows the researched flow rather than a feature tour, because the
 * flow is what an admin does and the order is the rule:
 *
 *   1. "On a scheduled / not-scheduled / disqualified path in the router, admin
 *      adds a `Create or Update Record` node (**must precede the other CRM
 *      nodes**)." So the page opens on the *path* and the *node list*, and the
 *      list is drawn in declared order with the anchor marked. The ordering
 *      sentence is shown above it, because the number one thing this page has to
 *      prevent is somebody adding a `Create Event` above the node it writes to.
 *   2. "Chooses whether to **update the matched record** (Update matched Contact
 *      or Lead / Only update matched Lead) and whether to **create** (Create
 *      Contact or Lead / Create Lead / Always create Lead)." Those six labels are
 *      a builder on the flow, rendered from the server's vocabulary, and the run
 *      says which branch fired and which declined.
 *   3. "Adds downstream nodes: `Create Event` (Salesforce) or `Create Engagement`
 *      (HubSpot), plus optional **Related Object** ... `Update Field`/
 *      `Update Property`, `Add to Campaign`, `Update Ownership`." Each of those is
 *      a node the page can add, and the two selection rules are shown with the
 *      row they chose.
 *   4. "**Sync Meeting Type to the CRM** … applied to all users in your org." So
 *      the toggle lives on the meeting type and is a switch, not a per-run
 *      control - and the page says why, quoting the sentence.
 *   5. "If the Event is successfully created, we will show when it happened. If
 *      the Event failed to be created, we will also show when it happened,
 *      alongside the detailed error." That is Events History: one row per Event,
 *      with the time and the error, and a **retry** on the failures.
 *
 * Two things this page is careful to show rather than hide:
 *
 *   - **The gate on the extra relation.** "If we have found a contact" is not the
 *     same as "if we found a record", so a Lead match that skips the Related
 *     Object says so with the sentence, and the Event still gets the researched
 *     default relation to the Lead.
 *   - **A run that wrote nothing is not a green run.** When the create node
 *     matched nothing and no create branch produced a record, every node after it
 *     skips with the same reason and the run is *not* ok. The page reports that as
 *     a refusal, because a rep who is told "6 of 6 nodes ran" and then finds no
 *     meeting in the CRM has been misled.
 */

import { useCallback, useMemo, useState } from 'react'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorNote,
  Field,
  JsonView,
  Spinner,
  inputClass,
  useAsync,
} from '@/components/ui'
import { listRooms, writebackApi } from './api'
import Glyph, { NODE_GLYPH } from './icons'
import { HistoryRow, NodeRow, Notice, StatTile, Toggle } from './primitives'

/** A short label for a router path, so the picker reads as a choice not a code. */
const PATH_LABEL = {
  scheduled: 'Scheduled — a meeting was booked',
  not_scheduled: 'Not scheduled — qualified, no meeting yet',
  disqualified: 'Disqualified — did not qualify',
}

/** The create node's six researched branch labels, in the research's own words. */
const BRANCH_LABEL = {
  matched_contact_or_lead: 'Update matched Contact or Lead',
  matched_lead_only: 'Only update matched Lead',
  contact_or_lead: 'Create Contact or Lead',
  lead: 'Create Lead',
  always_lead: 'Always create Lead',
  none: 'Do not create',
}

const VENDOR_LABEL = { salesforce: 'Salesforce', hubspot: 'HubSpot' }

const DEFAULT_BOOKING = {
  booking_ref: '',
  subject: 'Enterprise walkthrough',
  path: 'scheduled',
  starts_at: '2026-10-05T09:00:00',
  booker: { name: 'Amara Okonkwo', email: 'a.buyer@northwind.example' },
  guest: { name: 'Bayo Toure', email: 'b.toure@northwind.example' },
  host: { name: 'Dana', email: 'dana@acme.example' },
  assignee: { name: 'Sam', email: 'sam@acme.example' },
  seats: '40',
}

export default function BookingWriteback() {
  const [roomId, setRoomId] = useState('')
  const [path, setPath] = useState('scheduled')
  const [flowId, setFlowId] = useState('')
  const [booking, setBooking] = useState(DEFAULT_BOOKING)
  const [run, setRun] = useState(null)
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState(null)
  const [retrying, setRetrying] = useState('')
  const [retryResult, setRetryResult] = useState(null)
  const [showInferences, setShowInferences] = useState(false)

  const rooms = useAsync(() => listRooms({ limit: 100 }), [])
  const vocabulary = useAsync(() => writebackApi.vocabulary(), [])
  const inferences = useAsync(() => writebackApi.inferences(), [])

  const meetingTypes = useAsync(
    () => (roomId ? writebackApi.listMeetingTypes(roomId) : Promise.resolve(null)),
    [roomId]
  )
  const flows = useAsync(
    () => (roomId ? writebackApi.listFlows(roomId) : Promise.resolve(null)),
    [roomId]
  )
  const summary = useAsync(
    () => (roomId ? writebackApi.summary(roomId) : Promise.resolve(null)),
    [roomId]
  )
  const history = useAsync(
    () => (roomId ? writebackApi.listHistory(roomId, { limit: 50 }) : Promise.resolve(null)),
    [roomId]
  )

  const reload = useCallback(() => {
    summary.refetch()
    history.refetch()
  }, [summary, history])

  const selectedFlow = useMemo(
    () => (flows.data?.flows || []).find((entry) => entry.id === flowId) || null,
    [flows.data, flowId]
  )

  async function writeback() {
    setBusy(true)
    setFailure(null)
    setRun(null)
    try {
      const payload = {
        booking_ref: booking.booking_ref || `ui-${Date.now()}`,
        subject: booking.subject,
        path,
        starts_at: booking.starts_at,
        booker: { name: booking.booker.name, email: booking.booker.email },
        // The child Event setting is the flow's, so a booking that names a guest
        // only produces a child Event when the flow asked for one. Sending an empty
        // list rather than omitting the key keeps the shape the API documents.
        guests: booking.guest.email ? [{ name: booking.guest.name, email: booking.guest.email }] : [],
        host: { name: booking.host.name, email: booking.host.email },
        assignee: { name: booking.assignee.name, email: booking.assignee.email },
        data_fields: { seats: booking.seats },
      }
      const response = flowId
        ? await writebackApi.writeback(roomId, flowId, payload)
        : await writebackApi.writebackForPath(roomId, payload)
      setRun(response.data)
      reload()
    } catch (error) {
      setFailure(error)
    } finally {
      setBusy(false)
    }
  }

  async function toggleSync(meetingType, next) {
    try {
      await writebackApi.updateMeetingType(roomId, meetingType.id, { sync_to_crm: next })
      meetingTypes.refetch()
      flows.refetch()
    } catch (error) {
      setFailure(error)
    }
  }

  async function retry(historyId) {
    setRetrying(historyId)
    setFailure(null)
    setRetryResult(null)
    try {
      const result = await writebackApi.retry(roomId, historyId)
      // A row that succeeded comes back `retried: false`, with the researched
      // sentence explaining why the button was not really there.
      if (!result.retried) setFailure(new Error(result.message))
      else setRetryResult(result)
      history.refetch()
      summary.refetch()
    } catch (error) {
      setFailure(error)
    } finally {
      setRetrying('')
    }
  }

  if (rooms.error) return <ErrorNote error={rooms.error} onRetry={rooms.refetch} />
  if (vocabulary.error) return <ErrorNote error={vocabulary.error} onRetry={vocabulary.refetch} />
  if (rooms.loading || vocabulary.loading) return <Spinner label="Loading the writeback contract" />

  const order = vocabulary.data?.ordering_quote
  const stats = summary.data
  const rows = history.data?.history || []
  const detailsAvailable = history.data?.event_details?.event_details_available

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h2 className="flex items-center gap-2 font-mono text-lg font-semibold text-foreground">
            <span className="text-accent">
              <Glyph name="writeback" size={20} />
            </span>
            Write the booking back into the CRM
          </h2>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            Meeting and guest Data Fields become a matched or created Lead or Contact, the
            Event or Engagement is written and related, the selected fields are updated, the
            CampaignMember is created or updated with status <code>Booked</code>, and the
            record Owner is reassigned to the assignee.
          </p>
        </div>
        <Button
          icon="schema"
          variant="secondary"
          onClick={() => setShowInferences((value) => !value)}
          aria-expanded={showInferences}
        >
          {showInferences ? 'Hide the design decisions' : 'Why this behaves this way'}
        </Button>
      </header>

      {order && (
        <Notice tone="warn" title="The one rule every flow has to keep">
          <p>
            <strong>{order}</strong>
          </p>
          <p className="mt-1">
            Every other node writes to the record the create node produced, so a flow that
            declares one of them above it is refused when it is saved - not sorted. The node
            list below is drawn in the order it will run, and the anchor is marked.
          </p>
        </Notice>
      )}

      {/* -- step 1: the path, the meeting type, the flow ------------------- */}
      <Card>
        <h3 className="flex items-center gap-2 font-mono text-sm font-semibold text-foreground">
          <span className="text-muted-foreground">
            <Glyph name="path" size={16} />
          </span>
          1. The router path, and the flow on it
        </h3>
        <p className="mt-1 text-sm text-muted-foreground">
          [sourced] &ldquo;writes fire on the scheduled, not-scheduled and disqualified paths
          automatically&rdquo;, so the booking knows its path and the flow is found. A path with
          no declared flow comes back as a setup gap rather than a silent no-op.
        </p>

        <div className="mt-4 grid gap-3 sm:grid-cols-3">
          <Field label="Room" id="wf065-room">
            <select
              id="wf065-room"
              className={inputClass}
              value={roomId}
              onChange={(event) => {
                setRoomId(event.target.value)
                setFlowId('')
                setRun(null)
              }}
            >
              <option value="">Choose a room…</option>
              {(rooms.data?.records || []).map((entry) => (
                <option key={entry.id} value={entry.id}>
                  {entry.data.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Router path" id="wf065-path" hint={PATH_LABEL[path]}>
            <select
              id="wf065-path"
              className={inputClass}
              value={path}
              onChange={(event) => {
                setPath(event.target.value)
                setFlowId('')
              }}
            >
              {(vocabulary.data?.paths || []).map((name) => (
                <option key={name} value={name}>
                  {PATH_LABEL[name] || name}
                </option>
              ))}
            </select>
          </Field>
          <Field
            label="Flow"
            id="wf065-flow"
            hint="Leave on the path to let the booking resolve the flow itself."
          >
            <select
              id="wf065-flow"
              className={inputClass}
              value={flowId}
              onChange={(event) => setFlowId(event.target.value)}
              disabled={!roomId}
            >
              <option value="">Find the flow from the path</option>
              {(flows.data?.flows || [])
                .filter((entry) => entry.data.path === path)
                .map((entry) => (
                  <option key={entry.id} value={entry.id}>
                    {entry.data.name || entry.id}
                  </option>
                ))}
            </select>
          </Field>
        </div>

        {stats && (
          <dl className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <StatTile
              label="Flows"
              value={stats.flows}
              hint={Object.entries(stats.flows_by_path || {})
                .map(([name, count]) => `${name} ${count}`)
                .join(' · ')}
              glyph="path"
            />
            <StatTile
              label="Writebacks"
              value={stats.runs}
              hint={`${stats.runs_ok} ok`}
              glyph="event"
            />
            <StatTile
              label="Events created"
              value={stats.events_created}
              hint={`${stats.events_retried} after a retry`}
              glyph="event"
            />
            <StatTile
              label="Events failed"
              value={stats.events_failed}
              hint="each with a time and an error"
              glyph="retry"
            />
          </dl>
        )}
      </Card>

      {/* -- step 2: the toggle, and the meeting types ---------------------- */}
      {roomId && meetingTypes.data && (
        <Card>
          <h3 className="flex items-center gap-2 font-mono text-sm font-semibold text-foreground">
            <span className="text-muted-foreground">
              <Glyph name="toggle" size={16} />
            </span>
            2. Sync Meeting Type to the CRM
          </h3>
          <p className="mt-1 text-sm text-muted-foreground">
            [sourced] &ldquo;{vocabulary.data?.sourced_quotes?.find((q) => q.id === 'sync_toggle')?.quote}&rdquo;
          </p>
          <Notice tone="info" title="It is a meeting-type setting, and it is org-wide">
            <p>&ldquo;{vocabulary.data?.sourced_quotes?.find((q) => q.id === 'sync_toggle_org_wide')?.quote}&rdquo;</p>
            <p className="mt-1">
              So a run cannot carry its own answer, and a rep cannot turn it on for their own
              link. A meeting type with the toggle off still records the writeback, as a skip
              that says why - a booking that produced no CRM write is a fact, not an absence.
            </p>
          </Notice>
          <ul className="mt-3 divide-y divide-border-subtle/15">
            {(meetingTypes.data.meeting_types || []).map((entry) => (
              <li key={entry.id} className="flex flex-wrap items-center gap-3 py-2">
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm text-foreground">{entry.data.name}</span>
                  <span className="block font-mono text-[11px] text-muted-foreground">
                    {VENDOR_LABEL[entry.data.vendor] || entry.data.vendor}
                    {entry.data.event_type_id ? ` · event type ${entry.data.event_type_id}` : ''}
                  </span>
                </span>
                <Badge tone="neutral">{entry.data.path || '—'}</Badge>
                <Toggle
                  checked={Boolean(entry.data.sync_to_crm)}
                  onChange={(next) => toggleSync(entry, next)}
                  label={`Sync Meeting Type to the CRM for ${entry.data.name}`}
                  hint="Applied to all users in your org, so this is a per-meeting-type setting."
                />
              </li>
            ))}
          </ul>
          {(meetingTypes.data.meeting_types || []).length === 0 && (
            <p className="mt-3 text-sm text-muted-foreground">
              No meeting types yet. A flow hangs off one, because the toggle does.
            </p>
          )}
        </Card>
      )}

      {/* -- the declared nodes -------------------------------------------- */}
      {selectedFlow && (
        <Card>
          <h3 className="flex items-center gap-2 font-mono text-sm font-semibold text-foreground">
            <span className="text-muted-foreground">
              <Glyph name="anchor" size={16} />
            </span>
            3. The nodes, in the order they will run
          </h3>
          <ol className="mt-2">
            {(selectedFlow.data.nodes || []).map((node, index) => (
              <li
                key={`${node.node}-${index}`}
                className="flex flex-wrap items-baseline gap-2 border-b border-border-subtle/15 py-2 last:border-0"
              >
                <span className="w-5 shrink-0 font-mono text-xs text-muted-foreground">
                  {index + 1}
                </span>
                <span className="shrink-0 text-muted-foreground">
                  <Glyph name={NODE_GLYPH[node.node] || 'anchor'} size={16} />
                </span>
                <span className="font-mono text-[13px] text-foreground">{node.node}</span>
                {node.node === 'create_or_update_record' || node.node === 'create_or_update_contact' ? (
                  <span className="text-xs text-muted-foreground">
                    update: {BRANCH_LABEL[node.update] || node.update} · create:{' '}
                    {BRANCH_LABEL[node.create] || node.create}
                    {node.record_type ? ` (${node.record_type})` : ''}
                    {node.l2a ? ' · L2A' : ''}
                  </span>
                ) : null}
                {node.node === 'related_object' ? (
                  <span className="text-xs text-muted-foreground">
                    {node.object} ·{' '}
                    {vocabulary.data?.selection_rules?.[node.object]
                      ? `by ${vocabulary.data.selection_rules[node.object].replace(/_/g, ' ')}`
                      : 'no stated rule'}
                    {node.campaign ? ` · ${node.campaign}` : ''}
                  </span>
                ) : null}
                {node.node === 'create_event' || node.node === 'create_engagement' ? (
                  <span className="text-xs text-muted-foreground">
                    {node.child_events ? 'a child Event per additional guest' : 'no child Events'}
                    {node.activity_assigned_to
                      ? ` · Activity Assigned to ${node.activity_assigned_to}`
                      : ''}
                    {node.delete_event && node.delete_event !== 'never'
                      ? ` · delete Event ${node.delete_event.replace(/_/g, ' ')}`
                      : ''}
                  </span>
                ) : null}
                {node.node === 'add_to_campaign' ? (
                  <span className="text-xs text-muted-foreground">
                    status {node.status}
                    {node.campaign ? ` · ${node.campaign}` : ''}
                  </span>
                ) : null}
                {node.node === 'update_ownership' ? (
                  <span className="text-xs text-muted-foreground">
                    {node.assign_to} · fallback {node.fallback_mode}
                    {node.skip_contact_owner ? ' · skipContactOwner' : ''}
                  </span>
                ) : null}
              </li>
            ))}
          </ol>
        </Card>
      )}

      {/* -- step 4: the booking, and the writeback ------------------------ */}
      {roomId && (
        <Card>
          <h3 className="flex items-center gap-2 font-mono text-sm font-semibold text-foreground">
            <span className="text-muted-foreground">
              <Glyph name="event" size={16} />
            </span>
            4. The booking, written back
          </h3>
          <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            <Field label="Booking reference" id="wf065-ref">
              <input
                id="wf065-ref"
                className={inputClass}
                value={booking.booking_ref}
                placeholder="auto"
                onChange={(event) => setBooking({ ...booking, booking_ref: event.target.value })}
              />
            </Field>
            <Field label="Subject" id="wf065-subject">
              <input
                id="wf065-subject"
                className={inputClass}
                value={booking.subject}
                onChange={(event) => setBooking({ ...booking, subject: event.target.value })}
              />
            </Field>
            <Field
              label="Starts at"
              id="wf065-starts"
              hint="The Opportunity with the nearest Close Date is measured against this."
            >
              <input
                id="wf065-starts"
                type="datetime-local"
                className={inputClass}
                value={booking.starts_at}
                onChange={(event) => setBooking({ ...booking, starts_at: event.target.value })}
              />
            </Field>
            <Field label="Booker email" id="wf065-booker" hint="[sourced] matched by email.">
              <input
                id="wf065-booker"
                className={inputClass}
                value={booking.booker.email}
                onChange={(event) =>
                  setBooking({ ...booking, booker: { ...booking.booker, email: event.target.value } })
                }
              />
            </Field>
            <Field
              label="Additional guest"
              id="wf065-guest"
              hint="A child Event per additional guest, when the flow asks for one."
            >
              <input
                id="wf065-guest"
                className={inputClass}
                value={booking.guest.email}
                onChange={(event) =>
                  setBooking({ ...booking, guest: { ...booking.guest, email: event.target.value } })
                }
              />
            </Field>
            <Field label="Assignee" id="wf065-assignee" hint="The record Owner becomes this.">
              <input
                id="wf065-assignee"
                className={inputClass}
                value={booking.assignee.email}
                onChange={(event) =>
                  setBooking({
                    ...booking,
                    assignee: { ...booking.assignee, email: event.target.value },
                  })
                }
              />
            </Field>
            <Field
              label="Data Field: seats"
              id="wf065-seats"
              hint="Mappable to any CRM property, including a custom one."
            >
              <input
                id="wf065-seats"
                className={inputClass}
                value={booking.seats}
                onChange={(event) => setBooking({ ...booking, seats: event.target.value })}
              />
            </Field>
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <Button variant="primary" onClick={writeback} disabled={busy}>
              <span className="text-on-accent" aria-hidden="true">
                <Glyph name="writeback" size={16} />
              </span>
              {busy ? 'Writing back…' : 'Write this booking back'}
            </Button>
            <span className="text-xs text-muted-foreground">
              {flowId ? `Running ${selectedFlow?.data?.name || 'the selected flow'}.` : 'Letting the path resolve the flow.'}
            </span>
          </div>
        </Card>
      )}

      {retryResult && (
        <Notice
          tone={retryResult.status === 'created' ? 'good' : 'bad'}
          title={
            retryResult.status === 'created'
              ? `Retried: attempt ${retryResult.attempt} created ${retryResult.crm_id}`
              : `Retried: attempt ${retryResult.attempt} failed again`
          }
        >
          <p>
            {retryResult.cleaned_previous?.length
              ? `The previous attempt left ${retryResult.cleaned_previous.join(', ')} behind, and the flow's Delete Event setting removed it before this one. `
              : ''}
            The retry appended a row rather than rewriting the failed one, so the history
            reads &ldquo;failed at T, retried at T, {retryResult.status}&rdquo;.
          </p>
        </Notice>
      )}

      {failure && (
        <ErrorNote
          error={failure}
          onRetry={() => {
            setFailure(null)
            reload()
          }}
        />
      )}

      {/* -- the run ------------------------------------------------------- */}
      {run && (
        <Card>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <h3 className="flex items-center gap-2 font-mono text-sm font-semibold text-foreground">
              <span className="text-muted-foreground">
                <Glyph name="writeback" size={16} />
              </span>
              What one writeback did
            </h3>
            <Badge tone={run.ok ? 'insert' : 'delete'}>{run.ok ? 'ok' : 'not ok'}</Badge>
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            {run.counts?.applied || 0} applied · {run.counts?.skipped || 0} skipped ·{' '}
            {run.counts?.failed || 0} failed · {run.booking?.path} ·{' '}
            {VENDOR_LABEL[run.vendor] || run.vendor}
          </p>

          {!run.ok && !run.actionable_error && (
            <Notice tone="warn" title="Nothing was written, and the run says so">
              Every node after the create node skipped because there was no record to write
              to. A run that wrote nothing is not a green run.
            </Notice>
          )}
          {run.actionable_error && (
            <Notice
              tone={run.actionable_error.reason ? 'bad' : 'info'}
              title={
                run.actionable_error.node
                  ? `One thing to fix: ${run.actionable_error.node}`
                  : 'One thing to fix'
              }
            >
              <p>{run.actionable_error.message}</p>
              <p className="mt-1 font-mono text-[12px] text-muted-foreground">
                {run.actionable_error.reason}
              </p>
            </Notice>
          )}

          {run.record?.crm_id && (
            <p className="mt-3 text-sm text-muted-foreground">
              The record the flow wrote to:{' '}
              <span className="font-mono text-foreground">{run.record.crm_id}</span> (
              {run.record.type})
              {run.related?.crm_id ? (
                <>
                  {' '}
                  related to <span className="font-mono text-foreground">{run.related.crm_id}</span>{' '}
                  ({run.related.object}, by the{' '}
                  <span className="font-mono">{String(run.related.rule).replace(/_/g, ' ')}</span>{' '}
                  rule)
                </>
              ) : null}
            </p>
          )}

          <ol className="mt-3">
            {(run.steps || []).map((step, index) => (
              <NodeRow
                key={`${step.node}-${index}`}
                step={step}
                index={index}
                isAnchor={step.node.startsWith('create_or_update')}
              />
            ))}
          </ol>

          <details className="mt-3">
            <summary className="min-h-11 cursor-pointer py-2 text-sm text-muted-foreground">
              The whole run record
            </summary>
            <div className="rounded-lg border border-border-subtle/25 bg-background/40 p-3 text-xs">
              <JsonView value={run} />
            </div>
          </details>
        </Card>
      )}

      {/* -- step 5: Events History ---------------------------------------- */}
      {roomId && (
        <Card>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <h3 className="flex items-center gap-2 font-mono text-sm font-semibold text-foreground">
              <span className="text-muted-foreground">
                <Glyph name="history" size={16} />
              </span>
              5. Meetings Activity → Events History
            </h3>
            {roomId && (
              <a
                href={writebackApi.exportUrl(roomId)}
                download
                className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-border-subtle/50 px-3 text-sm text-foreground transition-colors duration-200 hover:bg-muted"
              >
                Export to CSV
              </a>
            )}
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            [sourced] &ldquo;{vocabulary.data?.sourced_quotes?.find((q) => q.id === 'history_shows_when')?.quote}&rdquo;
          </p>
          <p className="mt-1 text-sm text-muted-foreground">
            One row per Event, including the children, because a retry is offered on{' '}
            <em>any failed CRM Event</em> - so a child that failed is retryable on its own.
          </p>
          {history.loading && <Spinner label="Loading Events History" />}
          {history.data && rows.length === 0 && (
            <div className="mt-3">
              <EmptyState
                title="No Events yet"
                description="Write a booking back and every Event attempt lands here with the time it happened."
              />
            </div>
          )}
          {rows.length > 0 && (
            <>
              <ul className="mt-2">
                {rows.map((row) => (
                  <HistoryRow
                    key={row.id}
                    row={row.data}
                    detailsAvailable={detailsAvailable}
                    onRetry={() => retry(row.id)}
                    retrying={retrying === row.id}
                  />
                ))}
              </ul>
              <p className="mt-2 text-xs text-muted-foreground">
                Newest first. {history.data.created} created, {history.data.failed} failed,{' '}
                {history.data.retryable} awaiting a retry.
              </p>
            </>
          )}
        </Card>
      )}

      {/* -- what the CRM holds ------------------------------------------- */}
      {showInferences && inferences.data && (
        <Card>
          <h3 className="flex items-center gap-2 font-mono text-sm font-semibold text-foreground">
            <span className="text-muted-foreground">
              <Glyph name="inference" size={16} />
            </span>
            The design decisions, and how to change each one
          </h3>
          <p className="mt-1 text-sm text-muted-foreground">
            The research is specific about the nodes and about the two selection rules, and
            silent about most of what is around them. Each of these is a judgement call, named
            so it can be argued with by name.
          </p>
          <ul className="mt-3 divide-y divide-border-subtle/15">
            {inferences.data.inferences.map((entry) => (
              <li key={entry.id} className="py-3">
                <p className="font-mono text-[13px] text-foreground">{entry.topic}</p>
                <p className="mt-1 text-sm text-muted-foreground">{entry.why}</p>
                <p className="mt-1 font-mono text-[11px] text-muted-foreground/80">
                  change it in {entry.change_it}
                </p>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {!roomId && (
        <EmptyState
          title="Choose a room"
          description="A flow belongs to one router path on one room, and the Sync Meeting Type toggle belongs to the meeting type it hangs off."
        />
      )}
    </div>
  )
}
