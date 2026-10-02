import { useCallback, useMemo, useState } from 'react'

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
import { absoluteTime, apiRequest, relativeTime } from '@/lib/api'

import { engagementApi } from './api'
import { ICONS } from './icons'
import { BarRow, Disclosure, Notice, StatePill } from './primitives'

/**
 * WF-037: log a single buyer engagement event into the CRM.
 *
 * Built from `docs/research/digital-sales-room-workflows/wf/WF-037.md`. The page follows the
 * researched flow rather than a generic admin layout: the room's own **Analytics /
 * Engagement feed** first, then the **Sync log / Errors** panel beside it, with the
 * configuration that decides whether anything can be sent above both.
 *
 * Four things this page is careful about, because each is easy to get wrong:
 *
 * 1. **The readiness banner is first, not a footnote.** The research's extensibility
 *    promise is that a new event type "needs a mapping row, not a code path" - which is
 *    only true if a rep can see which row is missing. A Sync log full of green with a
 *    quietly unmapped event type is the failure this build most wanted to avoid, so what
 *    is missing is stated above the numbers, in the same words the queue rows carry.
 * 2. **A blocked row is shown, never hidden.** Every row carries its reason, and the
 *    reason is the only thing on it a person can act on. A queue that filtered out what it
 *    could not send is how engagement events go missing with nobody able to say which.
 * 3. **"Synced" is only ever shown with an id beside it.** The researched step 5 needs the
 *    CRM record id to store, so a row that says synced and shows no id would be asserting
 *    something the row cannot support. The one case where a vendor accepted a create and
 *    returned no id is reported as failed, and the page says why.
 * 4. **`—` is never rendered as `0`.** A dwell total over a feed where nothing carried one
 *    is not zero dwell; it is no measurement. Same reasoning as the nulls in the
 *    sibling reports.
 */

/* ------------------------------------------------------------------ formatting */

function seconds(value) {
  if (value === null || value === undefined) return '—'
  const total = Math.round(Number(value) || 0)
  if (total < 60) return `${total}s`
  const minutes = Math.floor(total / 60)
  if (minutes < 60) return `${minutes}m ${total % 60}s`
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`
}

function count(value) {
  if (value === null || value === undefined) return '—'
  return new Intl.NumberFormat().format(value)
}

function id(value, from) {
  if (!value) return '—'
  const short = String(value).length > 18 ? `${String(value).slice(0, 17)}…` : String(value)
  return (
    <span className="font-mono text-[13px]" title={`${value} (from ${from || 'an unknown place'})`}>
      {short}
    </span>
  )
}

/**
 * Why a reason is drawn the way it is, read from the server's vocabulary.
 *
 * The tones are shipped beside the reasons in `/vocabulary` rather than compiled here, so
 * a reason this build adds later arrives with its own presentation instead of falling back
 * to whatever the page guessed. The fallback is one level of quiet, not a hard-coded list.
 */
function useTones(vocabulary) {
  return useMemo(
    () => ({ ...(vocabulary?.block_tones || {}), ...(vocabulary?.failure_tones || {}) }),
    [vocabulary]
  )
}

function Reason({ reason, detail, tones }) {
  if (!reason) return null
  return (
    <Notice tone={tones[reason] || 'info'} title={reason}>
      <p>{detail}</p>
    </Notice>
  )
}

const VENDOR_ICON = {
  dataverse: ICONS.dataverse,
  hubspot: ICONS.hubspot,
  salesforce: ICONS.salesforce,
}

function VendorMark({ vendor }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-sm text-foreground">
      <span className="text-muted-foreground" aria-hidden="true">
        <svg
          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d={VENDOR_ICON[vendor] || ICONS.connector} />
        </svg>
      </span>
      <span className="capitalize">{vendor || 'unknown CRM'}</span>
    </span>
  )
}
/* ------------------------------------------------------------------ fragments */

function Findings({ findings }) {
  if (!findings || findings.length === 0) return null
  return (
    <ul className="space-y-1.5">
      {findings.map((finding, index) => (
        <li key={`${finding.code}-${index}`}>
          <Notice
            tone={finding.severity === 'hard' ? 'warn' : 'info'}
            title={`${finding.severity === 'hard' ? 'Omitted' : 'Sent with a caveat'}: ${finding.field || finding.code}`}
          >
            <p className="font-mono text-xs">{finding.code}</p>
            <p className="mt-0.5">{finding.detail}</p>
          </Notice>
        </li>
      ))}
    </ul>
  )
}

function FeedTable({ rows, onSelect, selectedId }) {
  if (rows.length === 0) {
    return (
      <EmptyState
        title="No engagement recorded yet"
        description="A buyer's first asset open or CTA answer appears here with whether it reached the CRM."
      />
    )
  }
  return (
    <ul className="divide-y divide-border-subtle/30">
      {rows.map((row) => (
        <li key={row.id}>
          <button
            type="button"
            onClick={() => onSelect(row.id)}
            aria-expanded={selectedId === row.id}
            className={`flex min-h-11 w-full flex-col gap-1 px-1 py-3 text-left transition-colors duration-200 hover:bg-muted/50 ${
              selectedId === row.id ? 'bg-muted/70' : ''
            }`}
          >
            <span className="flex flex-wrap items-center gap-2">
              <StatePill state={row.sync_state} />
              <span className="font-mono text-sm text-foreground">{row.type || 'untyped'}</span>
              {row.needs_manual_update && <Badge tone="delete">needs a person</Badge>}
            </span>
            <span className="flex flex-wrap gap-x-4 gap-y-0.5 text-xs text-muted-foreground">
              <span className="min-w-0 truncate">{row.asset || 'no asset recorded'}</span>
              <span>{row.buyer_email || row.buyer_crm_id || 'no buyer resolved'}</span>
              <span>{seconds(row.dwell_seconds)} on the asset</span>
              <span title={absoluteTime(row.occurred_at || row.created_at)}>
                {relativeTime(row.occurred_at || row.created_at)}
              </span>
            </span>
            <span className="flex flex-wrap items-center gap-2 text-xs">
              <span className="text-muted-foreground">CRM record</span>
              {row.crm_record_id ? (
                id(row.crm_record_id, row.crm_record_id_from)
              ) : (
                <span className="text-muted-foreground/70">
                  {row.sync_state === 'synced' ? '—' : 'not written yet'}
                </span>
              )}
            </span>
          </button>
        </li>
      ))}
    </ul>
  )
}

function SyncLogTable({ entries, onSelect, selectedId }) {
  if (entries.length === 0) {
    return (
      <EmptyState
        title="No writes attempted yet"
        description="Every create the queue worker made lands here, with the request it sent and each attempt it made."
      />
    )
  }
  return (
    <ul className="divide-y divide-border-subtle/30">
      {entries.map((entry) => {
        const data = entry.data || {}
        const annotations = (data.vendor_error || {}).annotations || []
        return (
          <li key={entry.id}>
            <button
              type="button"
              onClick={() => onSelect(entry.id)}
              aria-expanded={selectedId === entry.id}
              className={`flex min-h-11 w-full flex-col gap-1 px-1 py-3 text-left transition-colors duration-200 hover:bg-muted/50 ${
                selectedId === entry.id ? 'bg-muted/70' : ''
              }`}
            >
              <span className="flex flex-wrap items-center gap-2">
                <StatePill state={data.outcome === 'synced' ? 'synced' : 'failed'} />
                <VendorMark vendor={data.vendor} />
                <span className="font-mono text-xs text-muted-foreground">{data.object || '—'}</span>
                {data.needs_manual_update && <Badge tone="delete">needs a person</Badge>}
                {annotations.length > 0 && (
                  <Badge tone="restore">
                    {annotations.length} annotation{annotations.length === 1 ? '' : 's'}
                  </Badge>
                )}
              </span>
              <span className="flex flex-wrap gap-x-4 gap-y-0.5 text-xs text-muted-foreground">
                <span>HTTP {data.http_status === null || data.http_status === undefined ? '—' : data.http_status}</span>
                <span>
                  {(data.attempt_statuses || []).length || 0} attempt
                  {(data.attempt_statuses || []).length === 1 ? '' : 's'}
                </span>
                <span className="capitalize">{data.trigger || 'drain'}</span>
                <span>{relativeTime(entry.created_at)}</span>
              </span>
              {data.failure_reason && (
                <span className="font-mono text-xs text-destructive">{data.failure_reason}</span>
              )}
            </button>
          </li>
        )
      })}
    </ul>
  )
}

function Configuration({ connectors, eventTypes, fieldMaps, vocabulary }) {
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <Card>
        <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground">
          <span className="text-accent" aria-hidden="true">
            <Icon path={ICONS.connector} />
          </span>
          Connectors
        </h3>
        <p className="mt-1 text-xs text-muted-foreground">
          Which CRM each room writes to. A room's own connector wins; an unscoped one is the
          installation default.
        </p>
        <ul className="mt-3 space-y-2">
          {connectors.length === 0 && (
            <li className="text-sm text-muted-foreground">No connector registered.</li>
          )}
          {connectors.map((connector) => (
            <li key={connector.id} className="rounded-lg border border-border-subtle/40 p-3">
              <div className="flex flex-wrap items-center gap-2">
                <VendorMark vendor={connector.vendor} />
                {!connector.enabled && <Badge tone="restore">switched off</Badge>}
                {connector.room_id ? (
                  <Badge tone="neutral">one room</Badge>
                ) : (
                  <Badge tone="neutral">installation default</Badge>
                )}
              </div>
              <p className="mt-1 truncate font-mono text-xs text-muted-foreground">
                {connector.object || connector.entity_set} · {connector.base_url}
              </p>
              {(connector.preferences || []).length > 0 && (
                <ul className="mt-1.5 space-y-0.5">
                  {connector.preference_detail.map((preference) => (
                    <li key={preference.token} className="text-xs text-muted-foreground">
                      <span className="font-mono text-foreground">Prefer: {preference.token}</span>
                      {preference.known ? ` — ${preference.effect}` : ` — ${preference.effect}`}
                    </li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ul>
      </Card>

      <Card>
        <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground">
          <span className="text-accent" aria-hidden="true">
            <Icon path={ICONS.write} />
          </span>
          Event catalogue
        </h3>
        <p className="mt-1 text-xs text-muted-foreground">
          “New event types are rows in the room's event catalogue mapped by the field map, so
          adding ‘download’, ‘pricing-view’, ‘cta-click’ needs a mapping row, not a code
          path.” Each row below is one such row.
        </p>
        <ul className="mt-3 space-y-2">
          {eventTypes.length === 0 && (
            <li className="text-sm text-muted-foreground">The catalogue is empty.</li>
          )}
          {eventTypes.map((row) => {
            const mapped = fieldMaps.some((map) => map.event_type === row.event_type)
            return (
              <li key={row.id} className="rounded-lg border border-border-subtle/40 p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-sm text-foreground">{row.event_type}</span>
                  {mapped ? (
                    <Badge tone="insert">mapped</Badge>
                  ) : (
                    <Badge tone="restore">no field map</Badge>
                  )}
                </div>
                <p className="mt-0.5 text-xs text-muted-foreground">{row.description || row.label}</p>
              </li>
            )
          })}
        </ul>
      </Card>

      <Card>
        <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground">
          <span className="text-accent" aria-hidden="true">
            <Icon path={ICONS.queue} />
          </span>
          What each CRM expects
        </h3>
        <p className="mt-1 text-xs text-muted-foreground">
          Three vendors, three different answers to “how do I know it worked, and what is the
          new row's id?”.
        </p>
        <ul className="mt-3 space-y-2">
          {(vocabulary?.vendors || []).map((vendor) => {
            const spec = vocabulary.create_endpoints[vendor]
            const locations = vocabulary.record_id_locations[vendor] || []
            return (
              <li key={vendor} className="rounded-lg border border-border-subtle/40 p-3">
                <VendorMark vendor={vendor} />
                <p className="mt-1 font-mono text-xs text-muted-foreground">
                  POST {spec.path_template}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  Success: {spec.success_codes.join(', ')}
                  {spec.sourced_success ? ' (quoted)' : ' (no code sourced — any 2xx)'}
                </p>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  Id from:{' '}
                  {locations
                    .map((entry) => `${entry.where === 'header' ? entry.name : entry.path}${entry.sourced ? '' : ' (guess)'}`)
                    .join(', ') || '—'}
                </p>
              </li>
            )
          })}
        </ul>
      </Card>
    </div>
  )
}

/* ------------------------------------------------------------------ the page */

export default function CrmEngagementLog() {
  const [chosenRoomId, setChosenRoomId] = useState('')
  const [selectedEventId, setSelectedEventId] = useState('')
  const [selectedLogId, setSelectedLogId] = useState('')
  const [busy, setBusy] = useState(false)
  const [flash, setFlash] = useState(null)
  const [logFilter, setLogFilter] = useState('all')

  // No `order_by` here on purpose. The generic records route accepts only `created_at`,
  // `id`, `revision` and `updated_at`, so asking it to sort by name answers 500 rather than
  // a sorted list - and a page whose first request 500s renders an error instead of a picker.
  // The rooms are sorted here instead, which is where the alphabetical order actually
  // belongs anyway: it is a presentation choice, not a storage one.
  const rooms = useAsync(() => apiRequest('/records/room?limit=200'), [])
  const vocabulary = useAsync(() => engagementApi.vocabulary(), [])
  const inferences = useAsync(() => engagementApi.inferences(), [])
  const connectors = useAsync(() => engagementApi.connectors(), [])
  const eventTypes = useAsync(() => engagementApi.eventTypes(), [])

  // The first room, once, so the page is never a picker with nothing picked.
  const sortedRooms = useMemo(
    () =>
      [...(rooms.data?.records || [])].sort((a, b) =>
        String(a.data?.name || a.data?.account || a.id).localeCompare(
          String(b.data?.name || b.data?.account || b.id)
        )
      ),
    [rooms.data]
  )
  // The first room, once, so the page is never a picker with nothing picked.
  // Derived rather than copied into state by an effect, which cost an extra
  // render with no room selected.
  const roomId = chosenRoomId || sortedRooms[0]?.id || ''

  const fieldMaps = useAsync(() => engagementApi.fieldMaps(), [])
  const readiness = useAsync(
    () => (roomId ? engagementApi.readiness(roomId) : Promise.resolve(null)),
    [roomId]
  )
  const feed = useAsync(() => (roomId ? engagementApi.feed(roomId, { limit: 100 }) : Promise.resolve(null)), [roomId])
  const queue = useAsync(() => (roomId ? engagementApi.queue(roomId, { limit: 100 }) : Promise.resolve(null)), [roomId])
  const syncLog = useAsync(
    () =>
      engagementApi.syncLog({
        room_id: roomId || undefined,
        outcome: logFilter === 'all' ? undefined : logFilter,
        limit: 100,
      }),
    [roomId, logFilter]
  )
  const eventDetail = useAsync(
    () =>
      selectedEventId && roomId
        ? engagementApi.event(roomId, selectedEventId)
        : Promise.resolve(null),
    [roomId, selectedEventId]
  )

  const refreshAll = useCallback(() => {
    readiness.refetch()
    feed.refetch()
    queue.refetch()
    syncLog.refetch()
  }, [readiness, feed, queue, syncLog])

  const run = useCallback(
    async (action, message) => {
      setBusy(true)
      setFlash(null)
      try {
        const result = await action()
        setFlash({ tone: 'good', message: typeof message === 'function' ? message(result) : message })
        refreshAll()
      } catch (error) {
        // 428 is its own case and gets its own words: "finish the setup" and "you got the
        // request wrong" are different instructions, and the status is carried on the error
        // for exactly this.
        setFlash({
          tone: error?.status === 428 ? 'warn' : 'danger',
          message: error?.status === 428 ? `Not configured — ${error.message}` : error?.message || String(error),
        })
      } finally {
        setBusy(false)
      }
    },
    [refreshAll]
  )

  const feedRows = feed.data?.events || []
  const logEntries = syncLog.data?.entries || []
  const queueRows = queue.data?.queue || []
  const tones = useTones(vocabulary.data)
  const blocked = queueRows.filter((row) => (row.data || {}).state === 'blocked')
  const summary = feed.data?.summary || {}
  const byType = Object.entries(summary.by_type || {})
  const dwellMax = byType.length
    ? Math.max(...feedRows.map((row) => Number(row.dwell_seconds) || 0), 1)
    : 1

  if (rooms.loading || vocabulary.loading) return <Spinner label="Loading the engagement log" />
  if (rooms.error) return <ErrorNote error={rooms.error} onRetry={rooms.refetch} />
  if (vocabulary.error) return <ErrorNote error={vocabulary.error} onRetry={vocabulary.refetch} />

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold text-foreground">CRM engagement log</h1>
          <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
            A buyer opens an asset or answers a CTA. The room records the event, enqueues a CRM
            write, and the queue worker resolves the buyer, maps the fields and creates the row
            — storing the id it gets back, and reporting everything that will not send.
          </p>
        </div>
        <div className="w-full max-w-xs">
          <Field label="Room" id="wf037-room" hint="Everything below is scoped to this room.">
            <select
              id="wf037-room"
              className={inputClass}
              value={roomId}
              onChange={(event) => {
                setChosenRoomId(event.target.value)
                setSelectedEventId('')
              }}
            >
              {(sortedRooms || []).map((room) => (
                <option key={room.id} value={room.id}>
                  {room.data?.name || room.data?.account || room.id}
                </option>
              ))}
            </select>
          </Field>
        </div>
      </header>

      {flash && (
        <Notice tone={flash.tone === 'good' ? 'good' : flash.tone === 'warn' ? 'warn' : 'danger'}>
          {flash.message}
        </Notice>
      )}

      {/* What is missing, above the numbers. The researched extensibility promise is only
          true if a rep can see which mapping row is absent. */}
      {readiness.data && !readiness.data.ready && (
        <Notice tone="warn" title="This room cannot sync everything yet">
          <ul className="mt-1 space-y-1">
            {readiness.data.missing.map((entry) => (
              <li key={`${entry.reason}-${entry.detail}`}>
                <span className="font-mono">{entry.reason}</span> — {entry.detail}
              </li>
            ))}
          </ul>
        </Notice>
      )}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="Events recorded"
          value={count(summary.events)}
          hint="The room's own record, written before anything is sent"
          icon="audit"
        />
        <StatCard
          label="Synced to the CRM"
          value={count(summary.synced)}
          hint={`${count(summary.pending)} still in the queue`}
          icon="database"
        />
        <StatCard
          label="Will not send"
          value={count(summary.blocked)}
          hint="Blocked with a named reason, never dropped"
          icon="schema"
        />
        <StatCard
          label="Needs a person"
          value={count(summary.needs_manual_update)}
          hint="Failed in a way no retry count can fix"
          icon="rooms"
        />
      </div>

      <div className="flex flex-wrap gap-2">
        <Button
          variant="primary"
          icon="refresh"
          disabled={busy || !roomId}
          onClick={() => run(() => engagementApi.drain(roomId), (result) => {
            const counts = result?.counts || {}
            return `Queue fired: ${counts.synced || 0} synced, ${counts.failed || 0} failed, ${counts.blocked || 0} blocked.`
          })}
        >
          Fire the queue
        </Button>
        <Button
          icon="audit"
          disabled={busy || !roomId}
          onClick={() =>
            run(() => engagementApi.preview(roomId), (result) => {
              const counts = result?.counts || {}
              return `Preview only — nothing was written. ${counts.sendable || 0} would send, ${counts.blocked || 0} would be blocked.`
            })
          }
        >
          Preview the queue
        </Button>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <h2 className="flex items-center gap-2 text-sm font-semibold text-foreground">
            <span className="text-accent" aria-hidden="true">
              <Icon path={ICONS.write} />
            </span>
            Analytics / Engagement feed
          </h2>
          <p className="mt-1 text-xs text-muted-foreground">
            The room's own record and the CRM's copy, side by side. Select a row to see
            exactly what was sent.
          </p>
          {feed.loading && <Spinner label="Loading the feed" />}
          {feed.error && <ErrorNote error={feed.error} onRetry={feed.refetch} />}
          {feed.data && (
            <>
              <div className="mt-3">
                <FeedTable
                  rows={feedRows}
                  onSelect={(id) => setSelectedEventId((current) => (current === id ? '' : id))}
                  selectedId={selectedEventId}
                />
              </div>
              {byType.length > 0 && (
                <div className="mt-4 border-t border-border-subtle/30 pt-3">
                  <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
                    Dwell time by event type
                  </h3>
                  <ul className="mt-1">
                    {byType.map(([type, rows]) => (
                      <BarRow
                        key={type}
                        label={type}
                        display={seconds(
                          feedRows
                            .filter((row) => row.type === type)
                            .reduce((total, row) => total + (Number(row.dwell_seconds) || 0), 0)
                        )}
                        value={feedRows
                          .filter((row) => row.type === type)
                          .reduce((total, row) => total + (Number(row.dwell_seconds) || 0), 0)}
                        max={dwellMax}
                        hint={`${count(rows)} event${rows === 1 ? '' : 's'}`}
                      />
                    ))}
                  </ul>
                </div>
              )}
            </>
          )}
        </Card>

        <Card>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="flex items-center gap-2 text-sm font-semibold text-foreground">
              <span className="text-accent" aria-hidden="true">
                <Icon path={ICONS.queue} />
              </span>
              Sync log / Errors
            </h2>
            <div className="w-40">
              <Field label="Outcome" id="wf037-log-filter">
                <select
                  id="wf037-log-filter"
                  className={inputClass}
                  value={logFilter}
                  onChange={(event) => setLogFilter(event.target.value)}
                >
                  <option value="all">All writes</option>
                  <option value="synced">Synced</option>
                  <option value="failed">Failed</option>
                </select>
              </Field>
            </div>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            Every create the worker made, newest first, with the request it sent and each
            attempt. A failure that will not fix itself is marked so it can be found.
          </p>
          {syncLog.loading && <Spinner label="Loading the sync log" />}
          {syncLog.error && <ErrorNote error={syncLog.error} onRetry={syncLog.refetch} />}
          {syncLog.data && (
            <>
              <p className="mt-2 font-mono text-xs text-muted-foreground">
                {syncLog.data.summary.synced} synced · {syncLog.data.summary.failed} failed ·{' '}
                {syncLog.data.summary.needs_manual_update} need a person
              </p>
              <div className="mt-2">
                <SyncLogTable
                  entries={logEntries}
                  onSelect={(id) => setSelectedLogId((current) => (current === id ? '' : id))}
                  selectedId={selectedLogId}
                />
              </div>
            </>
          )}
        </Card>
      </div>

      {blocked.length > 0 && (
        <Card>
          <h2 className="flex items-center gap-2 text-sm font-semibold text-foreground">
            <span className="text-amber-400" aria-hidden="true">
              <Icon path={ICONS.alert} />
            </span>
            Waiting on configuration ({blocked.length})
          </h2>
          <p className="mt-1 text-xs text-muted-foreground">
            These rows were recorded and enqueued, and cannot be sent yet. Each names the row
            that is missing — adding it and firing the queue again is all it takes.
          </p>
          <ul className="mt-3 space-y-2">
            {blocked.map((row) => {
              const data = row.data || {}
              return (
                <li key={row.id} className="rounded-lg border border-border-subtle/40 p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <StatePill state="blocked" />
                    <span className="font-mono text-sm text-foreground">{data.type || 'untyped'}</span>
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">{data.block_detail}</p>
                  <div className="mt-2">
                    <Button
                      icon="refresh"
                      disabled={busy}
                      onClick={() =>
                        run(() => engagementApi.retry(roomId, row.id), (result) => {
                          const state = result?.state || 'blocked'
                          return state === 'synced'
                            ? `Sent. CRM record ${result.crm_record_id}.`
                            : `Still ${state}: ${result.failure_reason || result.reason || 'nothing changed'}.`
                        })
                      }
                    >
                      Fire this row again
                    </Button>
                  </div>
                </li>
              )
            })}
          </ul>
        </Card>
      )}

      {selectedEventId && eventDetail.data && (
        <Card>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-sm font-semibold text-foreground">What was sent for this event</h2>
            <Button
              icon="close"
              variant="ghost"
              onClick={() => {
                setSelectedEventId('')
                setSelectedLogId('')
              }}
            >
              Close
            </Button>
          </div>
          <div className="mt-3 grid gap-4 lg:grid-cols-2">
            <div>
              <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
                The event
              </h3>
              <div className="mt-1 rounded-lg border border-border-subtle/40 p-2">
                <JsonView value={eventDetail.data.engagement?.data || {}} />
              </div>
              <Reason
                reason={eventDetail.data.sync_state === 'blocked' ? (eventDetail.data.queue?.[0]?.data?.block_reason || 'event_missing') : null}
                detail={eventDetail.data.queue?.[0]?.data?.block_detail}
                tones={tones}
              />
            </div>
            <div>
              <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
                The request, with the token redacted
              </h3>
              {eventDetail.data.queue?.map((row) => (
                <div key={row.id} className="mt-1 space-y-2">
                  {(row.data?.findings || []).length > 0 && <Findings findings={row.data.findings} />}
                  <div className="rounded-lg border border-border-subtle/40 p-2">
                    <JsonView value={row.data?.request || {}} />
                  </div>
                  {(row.data?.attempt_log || []).length > 0 && (
                    <div className="rounded-lg border border-border-subtle/40 p-2">
                      <JsonView value={row.data.attempt_log} />
                    </div>
                  )}
                  {(row.data?.vendor_error || {}).annotations?.length > 0 && (
                    <Notice tone="danger" title="The CRM's own explanation">
                      <ul className="mt-1 space-y-1">
                        {row.data.vendor_error.annotations.map((annotation, index) => (
                          <li key={index} className="text-xs">
                            {annotation.message}
                          </li>
                        ))}
                      </ul>
                    </Notice>
                  )}
                </div>
              ))}
            </div>
          </div>
        </Card>
      )}

      <Configuration
        connectors={connectors.data?.connectors || []}
        eventTypes={eventTypes.data?.event_types || []}
        fieldMaps={fieldMaps.data?.field_maps || []}
        vocabulary={vocabulary.data}
      />

      <div className="space-y-3">
        <Disclosure summary="The researched contract, as data">
          {vocabulary.loading && <Spinner label="Loading the contract" />}
          {vocabulary.data && (
            <div className="space-y-3">
              {Object.entries(vocabulary.data.sourced_quotes || {}).map(([key, quote]) => (
                <div key={key}>
                  <p className="font-mono text-xs text-muted-foreground">{key}</p>
                  <blockquote className="mt-0.5 border-l-2 border-border-subtle/50 pl-3 text-sm text-foreground">
                    {quote}
                  </blockquote>
                </div>
              ))}
            </div>
          )}
        </Disclosure>

        <Disclosure summary={`Every decision the research does not make (${inferences.data?.count || 0})`}>
          {inferences.loading && <Spinner label="Loading the inference registry" />}
          {inferences.data && (
            <ul className="space-y-4">
              {inferences.data.inferences.map((entry) => (
                <li key={entry.id} className="border-l-2 border-border-subtle/50 pl-3">
                  <p className="font-mono text-xs text-accent">{entry.id}</p>
                  <p className="mt-0.5 text-sm font-medium text-foreground">{entry.topic}</p>
                  <p className="mt-1 text-sm text-muted-foreground">{entry.why}</p>
                  <p className="mt-1 font-mono text-xs text-muted-foreground">
                    change it in: {entry.change_it}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </Disclosure>
      </div>
    </div>
  )
}
