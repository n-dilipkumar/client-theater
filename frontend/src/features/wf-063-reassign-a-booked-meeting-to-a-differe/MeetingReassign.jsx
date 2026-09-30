/**
 * Meeting reassignment: hand a booked meeting to a different host (WF-063).
 *
 * Four sections, in the order the researched flow happens in them.
 *
 * **Meetings Activity** first, because that is where the research starts: the
 * admin opens `Reporting > Meetings Activity`, picks the **Upcoming** or **Past**
 * tab, filters by Meeting Type / Assignee / Booker / Status / product source, and
 * clicks Open. The tabs here are wired to those five filters, and the list is the
 * same one the researched "Export to CSV" exports.
 *
 * **Reassign** is the step the research describes in the most detail. A rep who
 * knows the target person picks them; a rep who does not asks for the team and
 * lets the round robin choose. Either way the page shows the two fields that
 * cannot change - "You cannot change the Meeting Type or Workspace" - as visibly
 * locked rather than as absent, shows the researched sentence beside the add-on
 * precondition, and offers **Preview** first: the same decision function the write
 * uses, so the answer shown before committing is the answer committing produces.
 *
 * **Events History** is the researched tab, carrying the four things it says it
 * displays: who reassigned it, to whom, when, and the source.
 *
 * **What this infers** is the research's own gaps made arguable. Every entry is
 * named and served from `/inferences`, so a reviewer can disagree with one entry
 * rather than by reading this file.
 *
 * The pickers come from `/vocabulary` and `/outcomes`, never from lists compiled
 * here, so a term the backend adds reaches the page with no change to it.
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
import { reassignApi } from './api'
import {
  DiffRow,
  HostOption,
  LockedField,
  OutcomeChip,
  Quote,
  ineligibleSentence,
} from './primitives'

const SECTIONS = [
  { id: 'activity', label: 'Meetings Activity', glyph: 'schema' },
  { id: 'reassign', label: 'Reassign', glyph: 'rooms' },
  { id: 'history', label: 'Events History', glyph: 'audit' },
  { id: 'inferences', label: 'What this infers', glyph: 'search' },
]

/** A labelled fact, for the dense read-only detail blocks. */
function Fact({ label, children }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 truncate font-mono text-[13px] text-foreground">{children}</dd>
    </div>
  )
}

function Note({ children, tone = 'neutral' }) {
  const tones = {
    neutral: 'border-border-subtle/30 bg-muted/20 text-muted-foreground',
    warn: 'border-amber-500/30 bg-amber-500/10 text-amber-200',
    danger: 'border-destructive/30 bg-destructive/10 text-destructive',
  }
  return (
    <p className={`rounded-lg border px-3 py-2 text-xs ${tones[tone]}`}>{children}</p>
  )
}

export default function MeetingReassign() {
  const [section, setSection] = useState('activity')
  const [roomId, setRoomId] = useState('')
  const [selected, setSelected] = useState(null)
  const [tab, setTab] = useState('all')
  const [filters, setFilters] = useState({ meeting_type: '', host_id: '', booker: '', status: '', product_source: '' })

  const rooms = useAsync(() => api.listRecords('room', { limit: 50 }), [])

  // The first room is selected automatically so the page is useful on arrival,
  // which is the difference between a demo that works and one that needs setup.
  useEffect(() => {
    if (!roomId && rooms.data?.records?.length) {
      setRoomId(rooms.data.records[0].id)
    }
  }, [rooms.data, roomId])

  const summary = useAsync(() => (roomId ? reassignApi.summary(roomId) : Promise.resolve(null)), [roomId])

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-mono text-xl font-semibold text-foreground">Meeting reassignment</h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            Move a booked meeting to a different host. The Meeting Type and the Workspace stay
            locked; the invite takes the new assignee&rsquo;s details; the round-robin credit moves
            with the host.
          </p>
        </div>
        {rooms.data?.records?.length > 1 && (
          <Field label="Room" id="wf063-room">
            <select
              id="wf063-room"
              value={roomId}
              onChange={(event) => {
                setRoomId(event.target.value)
                setSelected(null)
              }}
              className={inputClass}
            >
              {rooms.data.records.map((room) => (
                <option key={room.id} value={room.id}>
                  {room.data?.name || room.id}
                </option>
              ))}
            </select>
          </Field>
        )}
      </header>

      {!roomId && rooms.data && (
        <EmptyState
          title="No rooms yet"
          description="A meeting belongs to a room, so this page needs one before it can list anything."
        />
      )}

      {roomId && (
        <>
          {summary.data && (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <StatCard label="Meetings" value={summary.data.meings} hint={`${summary.data.upcoming} upcoming`} icon="rooms" />
              <StatCard label="Reassignments" value={summary.data.reassignments} hint={`${summary.data.history_rows} history rows`} icon="audit" />
              <StatCard label="Hosts" value={summary.data.hosts} hint={`${summary.data.inactive_hosts} inactive`} icon="rooms" />
              <StatCard
                label="Sources"
                value={Object.keys(summary.data.by_source || {}).length}
                hint={Object.keys(summary.data.by_source || {}).join(', ') || 'none yet'}
                icon="schema"
              />
            </div>
          )}

          <nav aria-label="Sections" className="flex flex-wrap gap-2">
            {SECTIONS.map((entry) => (
              <Button
                key={entry.id}
                variant={section === entry.id ? 'primary' : 'secondary'}
                icon={entry.glyph}
                onClick={() => setSection(entry.id)}
                aria-current={section === entry.id ? 'page' : undefined}
              >
                {entry.label}
              </Button>
            ))}
          </nav>

          {section === 'activity' && (
            <ActivitySection
              roomId={roomId}
              tab={tab}
              setTab={setTab}
              filters={filters}
              setFilters={setFilters}
              onOpen={setSelected}
            />
          )}
          {section === 'reassign' &&
            (selected ? (
              <ReassignSection roomId={roomId} meeting={selected} onClose={() => setSelected(null)} />
            ) : (
              <EmptyState
                title="Pick a meeting"
                description="Choose a meeting from Meetings Activity to see who could take it and to reassign it."
              />
            ))}
          {section === 'history' && <HistorySection roomId={roomId} meeting={selected} />}
          {section === 'inferences' && <InferencesSection />}
        </>
      )}
    </div>
  )
}

/* ------------------------------------------------------------------------- */
/* Meetings Activity                                                          */
/* ------------------------------------------------------------------------- */

function ActivitySection({ roomId, tab, setTab, filters, setFilters, onOpen }) {
  const vocabulary = useAsync(() => reassignApi.vocabulary(), [])
  const list = useAsync(
    () => reassignApi.meetings(roomId, { tab, ...filters }),
    [roomId, tab, JSON.stringify(filters)]
  )

  const setFilter = (key) => (event) =>
    setFilters((previous) => ({ ...previous, [key]: event.target.value }))

  return (
    <section className="space-y-4">
      <Card className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="font-mono text-sm font-semibold text-foreground">Meetings Activity</h2>
            <p className="mt-0.5 text-xs text-muted-foreground">
              The researched list: the Upcoming and Past tabs, and the five filters step 1 names.
            </p>
          </div>
          {tab !== 'all' && (
            <a
              href={reassignApi.exportUrl(roomId, tab)}
              className="inline-flex min-h-11 items-center gap-2 rounded-lg px-4 text-sm
                text-accent underline underline-offset-4 transition-colors duration-200
                hover:text-foreground focus-visible:ring-2 focus-visible:ring-accent"
            >
              <Icon name="database" size={16} />
              Export to CSV
            </a>
          )}
        </div>

        <div role="tablist" aria-label="Meetings Activity tab" className="flex flex-wrap gap-2">
          {(vocabulary.data?.activity_tabs || ['upcoming', 'past', 'all']).map((name) => (
            <Button
              key={name}
              role="tab"
              aria-selected={tab === name}
              variant={tab === name ? 'primary' : 'secondary'}
              onClick={() => setTab(name)}
            >
              {name === 'all' ? 'All' : name[0].toUpperCase() + name.slice(1)}
            </Button>
          ))}
        </div>

        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          <Field label="Meeting Type" id="wf063-filter-type" hint="cannot be changed later">
            <input
              id="wf063-filter-type"
              value={filters.meeting_type}
              onChange={setFilter('meeting_type')}
              placeholder="demo"
              className={inputClass}
            />
          </Field>
          <Field label="Assignee" id="wf063-filter-host" hint="the current host">
            <input
              id="wf063-filter-host"
              value={filters.host_id}
              onChange={setFilter('host_id')}
              placeholder="host id"
              className={inputClass}
            />
          </Field>
          <Field label="Booker" id="wf063-filter-booker">
            <input
              id="wf063-filter-booker"
              value={filters.booker}
              onChange={setFilter('booker')}
              placeholder="buyer@example"
              className={inputClass}
            />
          </Field>
          <Field label="Status" id="wf063-filter-status">
            <select id="wf063-filter-status" value={filters.status} onChange={setFilter('status')} className={inputClass}>
              <option value="">any</option>
              {(vocabulary.data?.meeting_statuses || []).map((name) => (
                <option key={name} value={name}>
                  {name.replace('_', ' ')}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Product source" id="wf063-filter-source">
            <select
              id="wf063-filter-source"
              value={filters.product_source}
              onChange={setFilter('product_source')}
              className={inputClass}
            >
              <option value="">any</option>
              {(vocabulary.data?.surfaces || []).map((name) => (
                <option key={name} value={name}>
                  {vocabulary.data?.surface_labels?.[name] || name}
                </option>
              ))}
            </select>
          </Field>
        </div>

        {Object.values(filters).some(Boolean) && (
          <Button onClick={() => setFilters({ meeting_type: '', host_id: '', booker: '', status: '', product_source: '' })}>
            Clear filters
          </Button>
        )}
      </Card>

      {list.loading && <Spinner label="Loading meetings" />}
      {list.error && <ErrorNote error={list.error} onRetry={list.refetch} />}
      {list.data && list.data.count === 0 && (
        <EmptyState
          title="No meetings match"
          description="Widen the tab or clear a filter. The Upcoming and Past tabs split on when the meeting starts, not on whether it is cancelled."
        />
      )}
      {list.data && list.data.count > 0 && (
        <Card className="overflow-x-auto p-0">
          <table className="w-full min-w-[46rem] text-left text-sm">
            <caption className="sr-only">
              Meetings in this room, with the current host and when each starts
            </caption>
            <thead className="border-b border-border-subtle/30 text-xs uppercase tracking-wide text-muted-foreground">
              <tr>
                <th scope="col" className="px-4 py-3 font-medium">Meeting</th>
                <th scope="col" className="px-4 py-3 font-medium">Host</th>
                <th scope="col" className="px-4 py-3 font-medium">Starts</th>
                <th scope="col" className="px-4 py-3 font-medium">Status</th>
                <th scope="col" className="px-4 py-3 font-medium">Source</th>
                <th scope="col" className="px-4 py-3 font-medium">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {list.data.meetings.map((record) => {
                const data = record.data || {}
                return (
                  <tr key={record.id} className="border-b border-border-subtle/15 last:border-0">
                    <td className="px-4 py-2.5">
                      <span className="block font-mono text-[13px] text-foreground">{data.title}</span>
                      <span className="block text-xs text-muted-foreground">
                        {data.meeting_type} &middot; {data.workspace} &middot; {data.distribution}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 font-mono text-[13px] text-muted-foreground">
                      {data.invite?.organizer || data.host_id?.slice(-8)}
                      {data.reassignment_count > 0 && (
                        <span className="ml-2 text-xs text-accent">reassigned {data.reassignment_count}&times;</span>
                      )}
                    </td>
                    <td className="px-4 py-2.5 font-mono text-[13px] text-muted-foreground">
                      {absoluteTime(data.starts_at)}
                    </td>
                    <td className="px-4 py-2.5">
                      <Badge tone={data.status === 'cancelled' ? 'delete' : 'neutral'}>
                        {String(data.status || '').replace('_', ' ')}
                      </Badge>
                    </td>
                    <td className="px-4 py-2.5 text-xs text-muted-foreground">
                      {data.product_source}
                    </td>
                    <td className="px-4 py-2.5 text-right">
                      <Button icon="chevron" onClick={() => onOpen(record)}>
                        Open
                      </Button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </Card>
      )}
    </section>
  )
}

/* ------------------------------------------------------------------------- */
/* Reassign                                                                   */
/* ------------------------------------------------------------------------- */

function ReassignSection({ roomId, meeting, onClose }) {
  const data = meeting.data || {}
  const [kind, setKind] = useState('individual')
  const [targetId, setTargetId] = useState('')
  const [surface, setSurface] = useState('meetings_activity')
  const [addonReady, setAddonReady] = useState(true)
  const [requestedBy, setRequestedBy] = useState('')
  const [preview, setPreview] = useState(null)
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const vocabulary = useAsync(() => reassignApi.vocabulary(), [])
  const availability = useAsync(
    () => reassignApi.availability(roomId, meeting.id, { kind }),
    [roomId, meeting.id, kind]
  )
  const history = useAsync(() => reassignApi.meetingHistory(roomId, meeting.id), [roomId, meeting.id])

  const eligible = useMemo(
    () => (availability.data?.eligible || []).filter((row) => !row.ineligible_because.includes('already_the_host')),
    [availability.data]
  )

  const needsAddon = (vocabulary.data?.surfaces_requiring_addon || []).includes(surface)

  // The preview is the same decision function the write uses, so what is shown
  // before committing cannot disagree with what committing does.
  const runPreview = async (overrides = {}) => {
    setBusy(true)
    setError(null)
    try {
      const payload = {
        assign_to: { kind, ...(kind === 'individual' && targetId ? { id: targetId } : {}) },
        surface,
        ...(needsAddon ? { extension: { installed: addonReady, logged_in: addonReady } } : {}),
        ...(requestedBy ? { requested_by: requestedBy } : {}),
        ...overrides,
      }
      setPreview(await reassignApi.preview(roomId, meeting.id, payload))
    } catch (caught) {
      setError(caught)
      setPreview(null)
    } finally {
      setBusy(false)
    }
  }

  const commit = async () => {
    setBusy(true)
    setError(null)
    try {
      const payload = {
        assign_to: { kind, ...(kind === 'individual' && targetId ? { id: targetId } : {}) },
        surface,
        ...(needsAddon ? { extension: { installed: addonReady, logged_in: addonReady } } : {}),
        ...(requestedBy ? { requested_by: requestedBy } : {}),
      }
      setResult(await reassignApi.reassign(roomId, meeting.id, payload))
      setPreview(null)
      availability.refetch()
      history.refetch()
    } catch (caught) {
      setError(caught)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="space-y-4">
      <Card className="space-y-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="font-mono text-sm font-semibold text-foreground">{data.title}</h2>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Currently hosted by {data.invite?.organizer || data.host_id} &middot; starts{' '}
              {absoluteTime(data.starts_at)}
            </p>
          </div>
          <Button icon="close" onClick={onClose}>
            Pick another meeting
          </Button>
        </div>

        {history.data?.needs_reassignment && (
          <Note tone="warn">
            This meeting starts inside the distribution&rsquo;s {history.data.bounds.min_notice_minutes}-minute
            notice window. Reassignment ignores that bound, which is exactly what rescues a stale booking
            &mdash; a new booking at this time would be refused.
          </Note>
        )}

        <Quote source="Chili Piper, Reassigning Meetings">
          {vocabulary.data?.evidence?.editable_and_locked}
        </Quote>

        <div className="grid gap-3 sm:grid-cols-2">
          <LockedField
            label="Meeting Type"
            value={data.meeting_type}
            quote="You cannot change the Meeting Type or Workspace."
          />
          <LockedField
            label="Workspace"
            value={data.workspace}
            quote="You cannot change the Meeting Type or Workspace."
          />
        </div>
      </Card>

      <Card className="space-y-4">
        <div>
          <h3 className="font-mono text-sm font-semibold text-foreground">Assign to</h3>
          <p className="mt-0.5 text-xs text-muted-foreground">
            &ldquo;You can change the Distribution, Team, or Individual.&rdquo; A team or distribution asks
            for the host to be auto-selected, which the source supports only for round robin bookings.
          </p>
        </div>

        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Granularity" id="wf063-kind">
            <select
              id="wf063-kind"
              value={kind}
              onChange={(event) => {
                setKind(event.target.value)
                setTargetId('')
                setPreview(null)
              }}
              className={inputClass}
            >
              {(vocabulary.data?.assignment_kinds || []).map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Entry point" id="wf063-surface">
            <select
              id="wf063-surface"
              value={surface}
              onChange={(event) => {
                setSurface(event.target.value)
                setPreview(null)
              }}
              className={inputClass}
            >
              {(vocabulary.data?.surfaces || []).map((name) => (
                <option key={name} value={name}>
                  {vocabulary.data?.surface_labels?.[name] || name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Reassigned by" id="wf063-by" hint="recorded in Events History">
            <input
              id="wf063-by"
              value={requestedBy}
              onChange={(event) => setRequestedBy(event.target.value)}
              placeholder="dana"
              className={inputClass}
            />
          </Field>
        </div>

        {needsAddon && (
          <Note tone="warn">
            <span className="block font-semibold">This entry point needs the ChiliCal add-on.</span>
            <label className="mt-2 flex min-h-11 cursor-pointer items-center gap-2">
              <input
                type="checkbox"
                checked={addonReady}
                onChange={(event) => {
                  setAddonReady(event.target.checked)
                  setPreview(null)
                }}
                className="h-4 w-4 accent-[var(--accent)]"
              />
              Extension is installed and I am logged in
            </label>
            <span className="mt-1 block text-[11px] opacity-80">
              &ldquo;{vocabulary.data?.evidence?.extension_must_be_installed_and_logged_in}&rdquo;
            </span>
          </Note>
        )}

        {availability.loading && <Spinner label="Checking who is free" />}
        {availability.error && <ErrorNote error={availability.error} onRetry={availability.refetch} />}
        {availability.data && (
          <>
            {data.round_robin === false && kind !== 'individual' && (
              <Note tone="danger">
                This booking is not round robin, so the host cannot be auto-selected. &ldquo;{vocabulary.data?.evidence?.auto_is_round_robin_only}&rdquo;
              </Note>
            )}
            <fieldset>
              <legend className="text-xs font-medium text-muted-foreground">
                Hosts in the {data.distribution} distribution
              </legend>
              <div className="mt-2 space-y-2">
                {(availability.data.candidates || []).map((row) => (
                  <HostOption
                    key={row.id}
                    name="wf063-host"
                    row={row}
                    selected={targetId === row.id}
                    onSelect={(chosen) => {
                      setTargetId(chosen.id)
                      setPreview(null)
                    }}
                  />
                ))}
              </div>
            </fieldset>
            {eligible.length === 0 && (
              <Note tone="danger">
                Nobody in this distribution is free for this slot. Use Edit Meeting to pick a different
                time instead.
              </Note>
            )}
          </>
        )}

        <div className="flex flex-wrap gap-2">
          <Button icon="search" disabled={busy} onClick={() => runPreview()}>
            Preview
          </Button>
          <Button
            variant="primary"
            icon="refresh"
            disabled={busy || (kind === 'individual' && !targetId)}
            onClick={commit}
          >
            {busy ? 'Working…' : 'Reassign'}
          </Button>
        </div>
        {error && <ErrorNote error={error} />}
      </Card>

      {preview && <PreviewPanel preview={preview} />}

      {result && <ResultPanel result={result} />}
    </section>
  )
}

function PreviewPanel({ preview }) {
  return (
    <Card className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="font-mono text-sm font-semibold text-foreground">Preview</h3>
        <OutcomeChip outcome={preview.outcome} />
        <span className="text-xs text-muted-foreground">writes nothing</span>
      </div>
      <p className={`text-sm ${preview.allowed ? 'text-foreground' : 'text-destructive'}`}>{preview.reason}</p>
      {preview.allowed && (
        <dl className="grid gap-2 sm:grid-cols-3">
          <Fact label="From">{preview.from_host?.name}</Fact>
          <Fact label="To">{preview.to_host?.name}</Fact>
          <Fact label="Mode">{preview.mode}</Fact>
        </dl>
      )}
      {preview.bounds?.bypassed?.length > 0 && (
        <Note tone="warn">
          Bypassed: {(preview.bounds.bypassed || []).join(' and ')}. Reassignment ignores the minimum
          scheduling notice and the maximum availability range; a new booking would have been refused.
        </Note>
      )}
    </Card>
  )
}

function ResultPanel({ result }) {
  const data = result.reassignment?.data || {}
  const changed = new Set(data.invite_fields_changed || [])
  return (
    <Card className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="font-mono text-sm font-semibold text-foreground">Reassigned</h3>
        <OutcomeChip outcome={data.outcome} />
      </div>
      <p className="text-sm text-foreground">{data.reason}</p>

      <div>
        <h4 className="text-xs font-medium text-muted-foreground">The invite, before and after</h4>
        <ul className="mt-1">
          {Object.keys(data.invite_after || {}).map((field) => (
            <DiffRow
              key={field}
              field={field}
              before={data.invite_before?.[field]}
              after={data.invite_after?.[field]}
              changed={changed.has(field)}
            />
          ))}
        </ul>
      </div>

      <div className="grid gap-2 sm:grid-cols-2">
        <Fact label="Round-robin credit">{data.credit_movement?.reason}</Fact>
        <Fact label="Bounds bypassed">
          {(data.bounds_bypassed || []).join(', ') || 'none'}
        </Fact>
        <Fact label="Webhooks fired">{(data.webhooks || []).join(', ')}</Fact>
        <Fact label="History source">{data.surface_label}</Fact>
      </div>

      {data.credit_movement?.outcome === 'already_returned_by_no_show' && (
        <Note tone="warn">
          The round-robin credit did <strong>not</strong> move. A no-show credit-back had already
          returned it, and moving it again would credit one host twice for one booking.
        </Note>
      )}

      <details className="text-xs">
        <summary className="min-h-11 cursor-pointer py-2 text-muted-foreground">
          Webhook payloads
        </summary>
        <JsonView value={result.webhooks} />
      </details>
    </Card>
  )
}

/* ------------------------------------------------------------------------- */
/* Events History                                                             */
/* ------------------------------------------------------------------------- */

function HistorySection({ roomId, meeting }) {
  const vocabulary = useAsync(() => reassignApi.vocabulary(), [])
  const history = useAsync(
    () => reassignApi.eventsHistory(roomId, meeting ? { meeting_id: meeting.id } : {}),
    [roomId, meeting?.id]
  )

  return (
    <section className="space-y-4">
      <Card className="space-y-2">
        <h2 className="font-mono text-sm font-semibold text-foreground">Events History</h2>
        <Quote source="Chili Piper, Events History">
          {vocabulary.data?.evidence?.events_history_row}
        </Quote>
      </Card>

      {history.loading && <Spinner label="Loading history" />}
      {history.error && <ErrorNote error={history.error} onRetry={history.refetch} />}
      {history.data && history.data.count === 0 && (
        <EmptyState
          title="No reassignments yet"
          description="Reassign a meeting and the row appears here, naming who, to whom, when, and the source."
        />
      )}
      {history.data && history.data.count > 0 && (
        <Card className="p-0">
          <ul>
            {history.data.history.map((row, index) => (
              <li
                key={`${row.meeting_id}-${row.at}-${index}`}
                className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-border-subtle/15 px-4 py-3 last:border-0"
              >
                <span className="font-mono text-[13px] text-foreground">{row.reassigned_to}</span>
                <span className="text-xs text-muted-foreground">
                  by {row.reassigned_by}
                </span>
                <Badge tone="neutral">
                  {vocabulary.data?.surface_labels?.[row.reassignment_source] || row.reassignment_source}
                </Badge>
                <span className="font-mono text-xs text-muted-foreground">{row.meeting_title}</span>
                <span
                  className="ml-auto font-mono text-xs text-muted-foreground"
                  title={absoluteTime(row.at)}
                >
                  {relativeTime(row.at)}
                </span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </section>
  )
}

/* ------------------------------------------------------------------------- */
/* What this infers                                                           */
/* ------------------------------------------------------------------------- */

function InferencesSection() {
  const inferences = useAsync(() => reassignApi.inferences(), [])
  const webhooks = useAsync(() => reassignApi.webhooks(), [])

  if (inferences.loading) return <Spinner label="Loading the inference registry" />
  if (inferences.error) return <ErrorNote error={inferences.error} onRetry={inferences.refetch} />

  return (
    <section className="space-y-4">
      <Card className="space-y-2">
        <h2 className="font-mono text-sm font-semibold text-foreground">What this build infers</h2>
        <p className="text-sm text-muted-foreground">
          The research is explicit about its vocabulary and about what reassignment ignores, and
          silent on the {inferences.data?.count} things below. Each is served from the backend with
          the sentence it rests on, so it can be disagreed with by name rather than by reading this
          page.
        </p>
      </Card>

      {(inferences.data?.inferences || []).map((entry) => (
        <Card key={entry.id} className="space-y-2">
          <div className="flex flex-wrap items-baseline gap-x-3">
            <h3 className="font-mono text-[13px] font-semibold text-foreground">{entry.id}</h3>
            <span className="text-xs text-muted-foreground">{entry.topic}</span>
          </div>
          {entry.basis && <Quote source="Research">{entry.basis}</Quote>}
          <p className="text-sm text-muted-foreground">{entry.why}</p>
          <div className="grid gap-2 sm:grid-cols-2">
            <Fact label="Change it at">{entry.change_it}</Fact>
            <Fact label="Affects">{entry.blast_radius}</Fact>
          </div>
          <details className="text-xs">
            <summary className="min-h-11 cursor-pointer py-2 text-muted-foreground">
              Chosen value
            </summary>
            <JsonView value={entry.value} />
          </details>
        </Card>
      ))}

      {webhooks.data && (
        <Card className="space-y-2">
          <h2 className="font-mono text-sm font-semibold text-foreground">Webhooks this emits</h2>
          <p className="text-sm text-muted-foreground">
            Built and recorded on every reassignment. Not delivered anywhere: the research documents
            no endpoint this product can reach, and the delivery machinery belongs to WF-046 and
            WF-047.
          </p>
          <ul className="space-y-2">
            {webhooks.data.events.map((event) => (
              <li key={event.event} className="rounded-lg border border-border-subtle/25 p-3 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-[13px] text-foreground">{event.event}</span>
                  <Badge tone="neutral">{event.vendor}</Badge>
                  <span className="text-xs text-muted-foreground">scope: {event.scope}</span>
                </div>
                <p className="mt-1 text-xs text-muted-foreground">&ldquo;{event.quoted}&rdquo;</p>
                {event.adds && (
                  <p className="mt-1 font-mono text-xs text-muted-foreground">
                    adds: {event.adds.join(', ')}
                  </p>
                )}
              </li>
            ))}
          </ul>
        </Card>
      )}
    </section>
  )
}
