/**
 * WF-020: extract DSR viewing sessions (dwell time + geography) for BI.
 *
 * The page follows the researched user flow, in the order the flow implies:
 *
 *   1. Read the contract. The endpoints, the bearer header, the documented query
 *      parameters, the documented fields, the 24 hour refresh SLA and the
 *      tab-level row granularity, with the research quoted verbatim underneath so
 *      the claims on this page can be checked against their sources.
 *   2. Run the sweep. The next `modifiedAt` page is derived from the runs, the
 *      query to send is shown literally, and the sweep refuses while the SLA has
 *      not elapsed. Nothing here pretends the numbers are live: the response
 *      carries the window and whether the previous extraction was inside the SLA.
 *   3. Read the landing zone. Sessions, with the room join resolved on read; the
 *      dwell rollup; where the sessions came from; and per-user engagement.
 *
 * The vocabulary is the point. A row is one session in one browser tab, so the
 * headline numbers keep `sessions` and `visitors` apart and label every total as
 * tab-seconds. A dashboard that reports one as the other is the mistake the
 * researched grain exists to prevent.
 *
 * Nothing here reaches upwards with a relative path, and nothing was added to a
 * shared file: the API calls are in `./api.js`, the extra glyphs in `./icons.js`,
 * and the small table and chip pieces in `./primitives.jsx`.
 */

import { useState } from 'react'
import { absoluteTime, relativeTime } from '@/lib/api'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorNote,
  Icon,
  Spinner,
  StatCard,
  useAsync,
} from '@/components/ui'
import { sessionsApi } from './api'
import { DWELL, EXPORT, GLOBE, JOIN, SWEEP, VIEWER } from './icons'
import {
  AudienceBadge,
  DataTable,
  Fact,
  FlagList,
  Section,
  dwell,
} from './primitives'

const TABS = [
  { id: 'extraction', label: 'Extraction' },
  { id: 'sessions', label: 'Sessions' },
  { id: 'engagement', label: 'Engagement' },
  { id: 'inventory', label: 'Inventory & contract' },
]

/** A shared `Button` carrying a glyph this feature owns. See ./primitives.jsx
 *  for why the path is injected as a child rather than through the shared
 *  `icon` prop, which resolves only through the shared `PATHS` map. */
function PathButton({ glyph, children, ...props }) {
  return (
    <Button {...props}>
      <Icon path={glyph} />
      {children}
    </Button>
  )
}

function TabBar({ current, onChange }) {
  return (
    <div role="tablist" aria-label="Viewing session sections" className="flex flex-wrap gap-1.5">
      {TABS.map((tab) => {
        const selected = tab.id === current
        return (
          <button
            key={tab.id}
            type="button"
            role="tab"
            id={`wf020-tab-${tab.id}`}
            aria-selected={selected}
            aria-controls={`wf020-panel-${tab.id}`}
            onClick={() => onChange(tab.id)}
            className={`inline-flex min-h-11 items-center rounded-lg px-4 text-sm transition-colors
              duration-200 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none ${
                selected
                  ? 'bg-accent/15 font-semibold text-accent'
                  : 'bg-muted text-muted-foreground hover:bg-border-subtle hover:text-foreground'
              }`}
          >
            {tab.label}
          </button>
        )
      })}
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Extraction
// --------------------------------------------------------------------------- //

function SweepPanel({ plan, onRun, running, error }) {
  const window_ = plan?.window
  const sweep = plan?.sweep
  const due = sweep?.due

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
              Next window
            </p>
            <p className="mt-1 font-mono text-lg text-foreground">
              {window_?.kind === 'modified' ? 'modifiedAt' : window_?.kind}
            </p>
          </div>
          <Badge tone={due ? 'update' : 'neutral'}>{due ? 'sweep due' : 'sweep not due'}</Badge>
        </div>

        <dl className="mt-4 grid gap-3 sm:grid-cols-2">
          <Fact term="from">{absoluteTime(window_?.start)}</Fact>
          <Fact term="to (exclusive)">{absoluteTime(window_?.end)}</Fact>
          <Fact term="page span">
            {window_?.hours ? `${window_.hours}h` : 'unbounded below'}
          </Fact>
          <Fact term="due in">
            {sweep?.due_in_hours ? `${sweep.due_in_hours}h` : 'now'}
          </Fact>
        </dl>

        <p className="mt-3 text-xs text-muted-foreground">{sweep?.detail}</p>
        {plan?.pages_behind > 1 && (
          <p className="mt-2 text-xs text-amber-300">
            {plan.pages_behind} SLA-sized pages are outstanding. Land this one, then ask again.
          </p>
        )}

        <div className="mt-4 flex flex-wrap gap-2">
          <PathButton
            glyph={SWEEP}
            variant="primary"
            onClick={onRun}
            disabled={running || !due}
            title={due ? 'Open the next extraction run' : 'Not due yet'}
          >
            {running ? 'Opening run…' : 'Run the nightly sweep'}
          </PathButton>
          {!due && (
            <p className="self-center text-xs text-muted-foreground">
              The reporting APIs are not designed for high-frequency use, so the refresh SLA is
              the cadence.
            </p>
          )}
        </div>
        {error && <ErrorNote error={error} />}
      </Card>

      <Card>
        <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
          Query to send
        </p>
        <p className="mt-1 text-xs text-muted-foreground">
          Sent as <span className="font-mono">Authorization: Bearer &lt;JWT&gt;</span> with{' '}
          <span className="font-mono">Accept: {plan?.format || 'application/json'}</span>.
        </p>
        <dl className="mt-3 flex flex-col gap-2">
          {Object.entries(plan?.query || {}).map(([key, value]) => (
            <div key={key} className="flex flex-wrap items-baseline gap-2">
              <dt className="font-mono text-[13px] text-accent">{key}</dt>
              <dd className="font-mono text-[13px] text-foreground">{value}</dd>
            </div>
          ))}
        </dl>
        <ul className="mt-4 flex flex-col gap-1.5">
          {(plan?.notes || []).map((note) => (
            <li key={note} className="text-xs text-muted-foreground">
              {note}
            </li>
          ))}
        </ul>
      </Card>
    </div>
  )
}

const RUN_COLUMNS = [
  {
    key: 'state',
    header: 'State',
    render: (run) => (
      <Badge tone={run.data.state === 'landed' ? 'insert' : 'update'}>{run.data.state}</Badge>
    ),
  },
  {
    key: 'kind',
    header: 'Kind',
    render: (run) => <span className="font-mono text-[13px]">{run.data.run_kind}</span>,
  },
  {
    key: 'window',
    header: 'Window',
    render: (run) => {
      const w = run.data.window || {}
      return (
        <span className="font-mono text-[13px] text-foreground">
          {absoluteTime(w.start)} → {absoluteTime(w.end)}
        </span>
      )
    },
  },
  {
    key: 'landed',
    header: 'Landed',
    render: (run) => {
      const counters = run.data.counters || {}
      if (run.data.state !== 'landed') return <span className="text-muted-foreground">—</span>
      return (
        <span className="font-mono text-[13px]">
          {counters.landed ?? 0}
          <span className="text-muted-foreground">
            {' '}
            ({counters.created ?? 0} new, {counters.updated ?? 0} merged,{' '}
            {counters.unchanged ?? 0} unchanged)
          </span>
        </span>
      )
    },
  },
  {
    key: 'dwell',
    header: 'Dwell',
    render: (run) => <span className="font-mono text-[13px]">{dwell(run.data.counters?.dwell_seconds)}</span>,
  },
  {
    key: 'flagged',
    header: 'Flagged',
    render: (run) => {
      const counters = run.data.counters || {}
      const flagged = counters.flagged || 0
      if (!flagged) return <span className="text-muted-foreground">—</span>
      return <span className="font-mono text-[13px] text-amber-300">{flagged}</span>
    },
  },
  { key: 'landed_at', header: 'Landed at', render: (run) => relativeTime(run.data.landed_at) },
]

function ExtractionTab({ contract, plan, runs, onSweep, running, sweepError }) {
  return (
    <div className="flex flex-col gap-6">
      <SweepPanel plan={plan} onRun={onSweep} running={running} error={sweepError} />
      <Section
        title="Extraction runs"
        hint="Which window produced which rows. A run opened by a sweep stays 'requested' until its rows land, so a job that failed between the pull and the landing is visible rather than retried forever."
      >
        <Card>
          <DataTable
            columns={RUN_COLUMNS}
            rows={runs}
            rowKey={(run) => run.id}
            empty="No extraction has landed yet. Run the sweep, or land a batch directly."
          />
        </Card>
      </Section>
      <ContractCard contract={contract} />
    </div>
  )
}

function ContractCard({ contract }) {
  const [open, setOpen] = useState(false)
  if (!contract) return null
  const sessionsEndpoint = contract.endpoints.find((e) =>
    e.name.startsWith('digitalSalesRoomViewing'),
  )

  return (
    <Section
      title="The extraction contract"
      hint="Published, not embedded: this is a projection of the researched sources, so a generated ETL job and a reviewer are reading the same document."
      action={
        <Button onClick={() => setOpen((value) => !value)} aria-expanded={open}>
          {open ? 'Hide the dictionary' : 'Show the dictionary'}
        </Button>
      }
    >
      <Card>
        <dl className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Fact term="Base URL">{contract.base_url}</Fact>
          <Fact term="Auth">{contract.authentication.value}</Fact>
          <Fact term="Accept">{contract.accept.join(' or ')}</Fact>
          <Fact term="Refresh SLA">{contract.refresh.sla_hours}h</Fact>
        </dl>

        <p className="mt-4 text-sm text-foreground">{contract.granularity.one_row_is}</p>
        <p className="mt-1 text-xs text-muted-foreground">
          {contract.granularity.consequence}. Room join: on{' '}
          <span className="font-mono">{contract.join.on}</span>, resolved {contract.join.resolved}.
        </p>

        <div className="mt-4 flex flex-col gap-2 border-l-2 border-accent/40 pl-3">
          {(contract.granularity.quote || contract.refresh.quote || '').split('. ').map((part, i) => (
            <blockquote key={i} className="text-xs text-muted-foreground italic">
              “{part.trim()}{i === 0 ? '.' : ''}”
            </blockquote>
          ))}
        </div>

        {open && (
          <div className="mt-5 flex flex-col gap-5 border-t border-border-subtle/30 pt-4">
            <div>
              <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
                Documented fields · {sessionsEndpoint?.name}
              </p>
              <ul className="mt-2 grid gap-1.5 sm:grid-cols-2">
                {(sessionsEndpoint?.fields || []).map((field) => (
                  <li key={field.name} className="font-mono text-[13px]">
                    <span className="text-accent">{field.name}</span>
                    <span className="text-muted-foreground"> → {field.field}</span>
                    {field.pii && <Badge tone="neutral">pii</Badge>}
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
                Evidence
              </p>
              <dl className="mt-2 flex flex-col gap-2">
                {Object.entries(contract.evidence).map(([name, text]) => (
                  <div key={name}>
                    <dt className="font-mono text-[13px] text-foreground">{name}</dt>
                    <dd className="text-xs text-muted-foreground italic">“{text}”</dd>
                  </div>
                ))}
              </dl>
            </div>
            <div>
              <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
                Sources
              </p>
              <ul className="mt-1 flex flex-col gap-0.5">
                {contract.sources.map((url) => (
                  <li key={url} className="font-mono text-[11px] break-all text-muted-foreground">
                    {url}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        )}
      </Card>
    </Section>
  )
}

// --------------------------------------------------------------------------- //
// Sessions
// --------------------------------------------------------------------------- //

const SESSION_COLUMNS = [
  { key: 'session_started_at', header: 'Started', render: (row) => absoluteTime(row.session_started_at) },
  {
    key: 'room',
    header: 'Room',
    render: (row) => (
      <span className="flex items-center gap-1.5">
        {row.room_resolved ? (
          <Icon path={JOIN} size={14} />
        ) : (
          <span className="text-amber-300" title="The inventory has no row for this room id">
            <Icon path={JOIN} size={14} />
          </span>
        )}
        <span className="font-mono text-[13px]">{row.room_name || row.digital_sales_room_id}</span>
      </span>
    ),
  },
  {
    key: 'viewer',
    header: 'Viewer',
    render: (row) => (
      <span className="flex items-center gap-1.5 font-mono text-[13px]">
        <Icon path={VIEWER} size={14} />
        {row.engagement_user_email || row.viewer_key}
      </span>
    ),
  },
  {
    key: 'dwell',
    header: 'Dwell',
    render: (row) => (
      <span className="flex items-center gap-1.5 font-mono text-[13px]">
        <Icon path={DWELL} size={14} />
        {dwell(row.room_duration_seconds)}
      </span>
    ),
  },
  { key: 'audience', header: 'Audience', render: (row) => <AudienceBadge internal={row.is_engagement_user_internal} /> },
  {
    key: 'geo',
    header: 'Where',
    render: (row) => (
      <span className="flex items-center gap-1.5 text-[13px] text-muted-foreground">
        <Icon path={GLOBE} size={14} />
        {[row.city, row.state, row.country].filter(Boolean).join(', ') || 'unknown'}
      </span>
    ),
  },
  { key: 'quality_flags', header: 'Flags', render: (row) => <FlagList flags={row.quality_flags} /> },
]

function SessionsTab({ sessions, total }) {
  return (
    <Section
      title="Landed viewing sessions"
      hint={`${total} landed. The room join is resolved on read, so a renamed room never leaves a stale label inside a session row.`}
    >
      <Card>
        <DataTable
          columns={SESSION_COLUMNS}
          rows={sessions}
          rowKey={(row) => row.id}
          empty="No sessions have been extracted yet."
        />
      </Card>
    </Section>
  )
}

// --------------------------------------------------------------------------- //
// Engagement
// --------------------------------------------------------------------------- //

const DWELL_COLUMNS = [
  { key: 'label', header: 'Room', render: (row) => <span className="font-mono text-[13px]">{row.label || row.group}</span> },
  { key: 'sessions', header: 'Tab sessions', render: (row) => <span className="font-mono text-[13px]">{row.sessions}</span> },
  { key: 'visitors', header: 'Viewers', render: (row) => <span className="font-mono text-[13px]">{row.visitors}</span> },
  { key: 'dwell_seconds', header: 'Tab-seconds', render: (row) => <span className="font-mono text-[13px]">{row.dwell_seconds}</span> },
  { key: 'mean', header: 'Mean', render: (row) => <span className="font-mono text-[13px]">{dwell(row.mean_dwell_seconds)}</span> },
  { key: 'internal_sessions', header: 'Internal', render: (row) => <span className="font-mono text-[13px]">{row.internal_sessions}</span> },
  { key: 'last_session_at', header: 'Last session', render: (row) => relativeTime(row.last_session_at) },
]

const VIEWER_COLUMNS = [
  {
    key: 'viewer_key',
    header: 'Viewer',
    render: (row) => (
      <span className="font-mono text-[13px]">{row.engagement_user_email || row.viewer_key}</span>
    ),
  },
  { key: 'viewer_kind', header: 'Identity from', render: (row) => <Badge>{row.viewer_kind}</Badge> },
  { key: 'sessions', header: 'Tab sessions', render: (row) => <span className="font-mono text-[13px]">{row.sessions}</span> },
  { key: 'dwell_seconds', header: 'Tab-seconds', render: (row) => <span className="font-mono text-[13px]">{row.dwell_seconds}</span> },
  {
    key: 'audience',
    header: 'Audience',
    render: (row) => (
      <span className="flex items-center gap-1.5">
        {row.is_internal && <Badge tone="update">internal</Badge>}
        {row.is_external && <Badge>external</Badge>}
      </span>
    ),
  },
  { key: 'rooms', header: 'Rooms', render: (row) => <span className="font-mono text-[13px]">{row.rooms.length}</span> },
  {
    key: 'countries',
    header: 'Where',
    render: (row) => (
      <span className="flex items-center gap-1.5 text-[13px] text-muted-foreground">
        <Icon path={GLOBE} size={14} />
        {row.countries.join(', ') || 'unknown'}
      </span>
    ),
  },
  { key: 'last_session_at', header: 'Last seen', render: (row) => relativeTime(row.last_session_at) },
]

function AudienceFilter({ value, onChange }) {
  const options = [
    { value: null, label: 'Everyone' },
    { value: false, label: 'Buyers only' },
    { value: true, label: 'Sellers only' },
  ]
  return (
    <div
      role="group"
      aria-label="Filter viewers by internal or external"
      className="flex flex-wrap gap-1.5"
    >
      {options.map((option) => {
        const selected = option.value === value
        return (
          <button
            key={String(option.value)}
            type="button"
            aria-pressed={selected}
            onClick={() => onChange(option.value)}
            className={`inline-flex min-h-11 items-center rounded-lg px-3 text-sm transition-colors
              duration-200 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none ${
                selected
                  ? 'bg-accent/15 font-semibold text-accent'
                  : 'bg-muted text-muted-foreground hover:bg-border-subtle hover:text-foreground'
              }`}
          >
            {option.label}
          </button>
        )
      })}
    </div>
  )
}

function EngagementTab({ dwellRollup, geography, viewers, internal, setInternal }) {
  return (
    <div className="flex flex-col gap-6">
      <Section
        title="Dwell by room"
        hint="One row is one session in one browser tab, so a total is tab-seconds. Viewers are counted separately from sessions for the same reason."
      >
        <Card>
          <DataTable
            columns={DWELL_COLUMNS}
            rows={dwellRollup}
            rowKey={(row) => row.group}
            empty="No dwell has been extracted yet."
          />
        </Card>
      </Section>

      <Section
        title="Per-user engagement"
        hint="Internal versus external is a dimension, not a filter in the data: a seller previewing a room and a buyer reading it are the same shape of event."
        action={<AudienceFilter value={internal} onChange={setInternal} />}
      >
        <Card>
          <DataTable
            columns={VIEWER_COLUMNS}
            rows={viewers}
            rowKey={(row) => row.viewer_key}
            empty="No viewers have been extracted yet."
          />
        </Card>
      </Section>

      <Section
        title="Geography"
        hint="The centroid is the mean of whatever coordinates arrived; the row count says how much of the rollup it actually covers. IP addresses are stored but not shown."
      >
        {geography.length === 0 ? (
          <Card>
            <p className="text-sm text-muted-foreground">No geography has been extracted yet.</p>
          </Card>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {geography.map((country) => (
              <Card key={country.country}>
                <div className="flex items-center justify-between gap-3">
                  <p className="flex items-center gap-2 font-mono text-sm font-semibold text-foreground">
                    <Icon path={GLOBE} size={16} />
                    {country.country}
                  </p>
                  <span className="font-mono text-xs text-muted-foreground">
                    {dwell(country.dwell_seconds)}
                  </span>
                </div>
                <p className="mt-1 text-xs text-muted-foreground">
                  {country.sessions} tab sessions · {country.visitors} viewers
                </p>
                {country.centroid && (
                  <p className="mt-1 font-mono text-[11px] text-muted-foreground">
                    {country.centroid.latitude}, {country.centroid.longitude} over{' '}
                    {country.rows_with_coordinates} rows
                  </p>
                )}
                <ul className="mt-3 flex flex-col gap-1.5">
                  {country.states.map((state) => (
                    <li key={state.state} className="text-[13px]">
                      <span className="text-foreground">{state.state}</span>
                      <span className="text-muted-foreground">
                        {' '}
                        · {state.sessions} sessions · {dwell(state.dwell_seconds)}
                      </span>
                      <ul className="ml-3 border-l border-border-subtle/30 pl-2">
                        {state.cities.map((city) => (
                          <li key={city.city} className="text-xs text-muted-foreground">
                            {city.city} · {city.sessions} · {dwell(city.dwell_seconds)}
                          </li>
                        ))}
                      </ul>
                    </li>
                  ))}
                </ul>
              </Card>
            ))}
          </div>
        )}
      </Section>
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Inventory
// --------------------------------------------------------------------------- //

const ROOM_COLUMNS = [
  { key: 'name', header: 'Room', render: (row) => <span className="font-mono text-[13px]">{row.name}</span> },
  { key: 'digital_sales_room_id', header: 'Join key', render: (row) => <span className="font-mono text-[13px]">{row.digital_sales_room_id}</span> },
  {
    key: 'template',
    header: 'Template',
    render: (row) => (
      <span className="font-mono text-[13px] text-muted-foreground">
        {row.digital_sales_room_template_id} / {row.digital_sales_room_template_version_id}
      </span>
    ),
  },
  {
    key: 'bound_room_id',
    header: 'Bound core room',
    render: (row) =>
      row.bound_room_id ? (
        <span className="font-mono text-[13px]">{row.bound_room_id}</span>
      ) : (
        <span className="text-xs text-muted-foreground">unbound</span>
      ),
  },
  { key: 'modified_at', header: 'Modified', render: (row) => relativeTime(row.modified_at) },
  { key: 'quality_flags', header: 'Flags', render: (row) => <FlagList flags={row.quality_flags} /> },
]

function InventoryTab({ rooms }) {
  return (
    <Section
      title="Room inventory"
      hint={`${rooms.filter((row) => row.bound_room_id).length} of ${rooms.length} bound to a core room. A session joins to a room on its digitalSalesRoomId, and that binding is what makes the room-scoped paths answer.`}
    >
      <Card>
        <DataTable
          columns={ROOM_COLUMNS}
          rows={rooms}
          rowKey={(row) => row.id}
          empty="No room inventory has been extracted yet."
        />
      </Card>
    </Section>
  )
}

// --------------------------------------------------------------------------- //
// Page
// --------------------------------------------------------------------------- //

export default function ViewingSessions() {
  const [tab, setTab] = useState('extraction')
  const [internal, setInternal] = useState(null)
  const [running, setRunning] = useState(false)
  const [sweepError, setSweepError] = useState(null)
  const [downloading, setDownloading] = useState(false)
  const [nonce, setNonce] = useState(0)

  const contract = useAsync(() => sessionsApi.contract(), [])
  const plan = useAsync(() => sessionsApi.window(), [nonce])
  const runs = useAsync(() => sessionsApi.runs({ limit: 25 }), [nonce])
  const summary = useAsync(() => sessionsApi.summary(), [nonce])
  const sessions = useAsync(() => sessionsApi.sessions({ limit: 60 }), [nonce])
  const dwell = useAsync(() => sessionsApi.dwell({ group_by: 'room' }), [nonce])
  const geography = useAsync(() => sessionsApi.geography(), [nonce])
  const viewers = useAsync(() => sessionsApi.viewers({ internal }), [nonce, internal])
  const rooms = useAsync(() => sessionsApi.rooms(), [nonce])

  const loading = [summary, sessions, dwell, geography, viewers, rooms, runs, plan].some(
    (state) => state.loading,
  )
  const failure = [summary, sessions, dwell, geography, viewers, rooms, runs, plan, contract].find(
    (state) => state.error,
  )

  const numbers = summary.data?.summary

  async function runSweep() {
    setRunning(true)
    setSweepError(null)
    try {
      await sessionsApi.sweep()
      setNonce((value) => value + 1)
    } catch (error) {
      setSweepError(error)
    } finally {
      setRunning(false)
    }
  }

  async function download() {
    setDownloading(true)
    setSweepError(null)
    try {
      await sessionsApi.downloadCsv({ limit: 1000 })
    } catch (error) {
      setSweepError(error)
    } finally {
      setDownloading(false)
    }
  }

  if (loading && !numbers) return <Spinner label="Loading viewing sessions" />
  if (failure) return <ErrorNote error={failure.error} onRetry={() => setNonce((v) => v + 1)} />

  if (!numbers) {
    return (
      <EmptyState
        title="No viewing sessions extracted yet"
        description="This workflow lands the rows an external ETL job pulls from the Seismic Reporting v2 API. Run the sweep to open the first extraction, or land a batch directly."
        action={
          <PathButton glyph={SWEEP} variant="primary" onClick={runSweep} disabled={running}>
            Run the nightly sweep
          </PathButton>
        }
      />
    )
  }

  const roomCount = rooms.data?.rooms?.length ?? 0
  const boundCount = rooms.data?.bound ?? 0

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="font-mono text-xl font-semibold text-foreground">Viewing sessions</h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            Tab-level DSR viewing sessions landed from the Reporting v2 extraction, merged on the
            incremental <span className="font-mono">modifiedAt</span> watermark. A row is one
            session in one browser tab, so a session count is not a visitor count.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <PathButton glyph={EXPORT} onClick={download} disabled={downloading}>
            {downloading ? 'Preparing…' : 'Download CSV'}
          </PathButton>
          <Button icon="refresh" onClick={() => setNonce((value) => value + 1)}>
            Refresh
          </Button>
        </div>
      </header>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <StatCard
          label="Tab sessions"
          value={numbers.sessions}
          hint={`${numbers.flagged_sessions} carrying a quality flag`}
          icon="audit"
        />
        <StatCard
          label="Viewers"
          value={numbers.visitors}
          hint="Distinct viewers, not rows"
          icon="schema"
        />
        <StatCard
          label="Dwell"
          value={dwell(numbers.dwell_seconds)}
          hint={`Mean ${dwell(numbers.mean_dwell_seconds)} per session`}
          icon="database"
        />
        <StatCard
          label="Buyer vs seller"
          value={`${numbers.external.sessions} / ${numbers.internal.sessions}`}
          hint="External / internal tab sessions"
          icon="plus"
        />
        <StatCard
          label="Rooms & countries"
          value={`${numbers.rooms} / ${numbers.countries}`}
          hint={`${boundCount} of ${roomCount} rooms bound to a core room`}
          icon="rooms"
        />
      </div>

      <TabBar current={tab} onChange={setTab} />

      <div
        role="tabpanel"
        id={`wf020-panel-${tab}`}
        aria-labelledby={`wf020-tab-${tab}`}
        className="flex flex-col gap-6"
      >
        {tab === 'extraction' && (
          <ExtractionTab
            contract={contract.data}
            plan={plan.data}
            runs={runs.data?.runs || []}
            onSweep={runSweep}
            running={running}
            sweepError={sweepError}
          />
        )}
        {tab === 'sessions' && (
          <SessionsTab
            sessions={sessions.data?.sessions || []}
            total={sessions.data?.count || 0}
          />
        )}
        {tab === 'engagement' && (
          <EngagementTab
            dwellRollup={dwell.data?.dwell || []}
            geography={geography.data?.geography || []}
            viewers={viewers.data?.viewers || []}
            internal={internal}
            setInternal={setInternal}
          />
        )}
        {tab === 'inventory' && <InventoryTab rooms={rooms.data?.rooms || []} />}
      </div>
    </div>
  )
}
