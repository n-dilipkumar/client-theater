/**
 * Duplicate guard: detect and block duplicate records during sync (WF-041).
 *
 * Four sections, in the order the researched flow happens in them.
 *
 * **Connections** first, because the policy is per-connection: "Dedupe policy
 * is a per-connection enum (block / update / merge)", so the thing a rep
 * configures is the connection, and no decision is interpretable without it.
 * Each card shows the researched sentence its policy comes from and whether the
 * policy writes anything at all - two of the four do not, and a picker should
 * say so before an administrator saves one.
 *
 * **Decisions** is the surface a rep actually reads. Every row states which of
 * the six outcomes landed, which matching key fired, what header went out, and
 * whether the in-room check answered without calling the CRM at all. Each row
 * expands to the matched records and the inbound row.
 *
 * **Try a lead** runs the two-stage flow against a real room: `check` first,
 * which writes nothing, so the answer can be seen before anything is committed,
 * and then `ingest`, which is the whole workflow.
 *
 * **What this infers** is the research's own gaps made arguable. The research
 * states that Salesforce auto-merge "is therefore *not* claimed", and this build
 * honours that by escalating rather than merging - so that judgement is a
 * product behaviour, and a reviewer should be able to disagree with it by name.
 *
 * The pickers come from `/vocabulary`, never from a list compiled into this file,
 * so a team that adds a matching key ships a record rather than a change here.
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
  inputClass,
  useAsync,
} from '@/components/ui'
import { dedupeApi } from './api'
import Glyphs from './icons'
import { Fact, Note, OutcomeChip } from './primitives'

// Named up here rather than inlined as string keys, so the stat tiles read as
// labels in the JSX below instead of as magic strings.
const GLYPH_DUPLICATE = Glyphs.duplicate
const GLYPH_BLOCKED = Glyphs.blocked
const GLYPH_ESCALATE = Glyphs.escalate
const GLYPH_RECORD = Glyphs.record

const SECTIONS = [
  { id: 'decisions', label: 'Decisions', glyph: 'duplicate' },
  { id: 'connections', label: 'Connections', glyph: 'record' },
  { id: 'try', label: 'Try a lead', glyph: 'match' },
  { id: 'inferences', label: 'What this infers', glyph: 'escalate' },
]

/** One decision row, expanding to the match detail it recorded. */
function DecisionRow({ decision }) {
  const [open, setOpen] = useState(false)
  const data = decision.data || {}
  const headerFields = Object.keys(data.header || {})

  return (
    <li className="border-b border-border-subtle/15 last:border-0">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex w-full min-h-11 flex-wrap items-center gap-3 py-2 text-left
          transition-colors duration-150 hover:bg-muted/40"
      >
        <OutcomeChip outcome={data.outcome} glyphs={Glyphs} />
        <Badge tone="neutral">{data.policy}</Badge>
        <span className="min-w-0 flex-1 truncate font-mono text-[13px] text-foreground">
          {data.match_key ? `matched on ${data.match_key}` : 'no match'}
        </span>
        {data.status === 300 && <Badge tone="delete">300</Badge>}
        {!data.crm_called && (
          <span className="flex items-center gap-1 text-xs text-muted-foreground">
            <Icon path={Glyphs.match} size={14} />
            answered locally
          </span>
        )}
        {data.needs_human && (
          <span className="flex items-center gap-1 text-xs text-amber-300">
            <Icon path={Glyphs.escalate} size={14} />
            needs a human
          </span>
        )}
        <span
          className="shrink-0 font-mono text-xs text-muted-foreground"
          title={absoluteTime(decision.created_at)}
        >
          {relativeTime(decision.created_at)}
        </span>
      </button>

      {open && (
        <div className="space-y-3 rounded-lg border border-border-subtle/25 bg-background/40 p-3">
          <dl className="grid gap-2 sm:grid-cols-2">
            <Fact label="Outcome">{data.outcome}</Fact>
            <Fact label="Policy">{data.policy}</Fact>
            <Fact label="CRM result">{data.crm_result}</Fact>
            <Fact label="Match key">{data.match_key || 'none'}</Fact>
            <Fact label="Status">{data.status ?? 'none'}</Fact>
            <Fact label="Acknowledged">{data.acknowledged ? 'yes' : 'no'}</Fact>
            <Fact label="Matched ids">
              {data.matched_ids?.length ? data.matched_ids.map((id) => id.slice(-8)).join(', ') : 'none'}
            </Fact>
            <Fact label="Called the CRM">{data.crm_called ? 'yes' : 'no'}</Fact>
          </dl>

          <p className="rounded-lg border border-border-subtle/25 bg-background/60 p-2 text-xs text-foreground/90">
            {data.reason}
          </p>
          {data.detail && (
            <p className="text-xs text-muted-foreground">{data.detail}</p>
          )}

          <div>
            <p className="mb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
              Header sent
            </p>
            <p className="font-mono text-[13px] text-foreground">
              {headerFields.length
                ? `${data.header_name}: ${data.header_wire}`
                : `${data.header_name}: (no options set; every field defaults to false)`}
            </p>
          </div>

          {data.matched?.length > 0 && (
            <div>
              <p className="mb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
                The existing record
              </p>
              <JsonView value={data.matched} />
            </div>
          )}

          {data.write && data.write.kind !== 'none' && (
            <div>
              <p className="mb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
                What was written
              </p>
              <JsonView value={data.write} />
            </div>
          )}

          <div>
            <p className="mb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
              Inbound row
            </p>
            <JsonView value={data.inbound} />
          </div>
        </div>
      )}
    </li>
  )
}

/** A connection card: its policy, what that policy does, and its header. */
function ConnectionCard({ connection, vocabulary, onChangePolicy, onDelete, busy }) {
  const data = connection.data || {}
  const detail = (vocabulary?.policies_detail?.policies || []).find((p) => p.policy === data.policy)
  const header = detail?.header || {}

  return (
    <Card className="card-hover flex h-full flex-col">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="truncate font-mono text-sm font-semibold text-foreground">{data.name}</h3>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {data.vendor}
            {data.enabled === false && ' · disabled'}
          </p>
        </div>
        <Badge tone={detail?.writes ? 'insert' : 'neutral'}>{data.policy}</Badge>
      </div>

      <p className="mt-3 flex-1 text-sm text-muted-foreground">{detail?.summary}</p>

      <dl className="mt-3 space-y-1.5 text-xs">
        <Fact label="Keys">
          {(data.keys || []).join(', ') || 'none'}
        </Fact>
        <Fact label="Unique">{(data.unique_keys || []).join(', ') || 'none'}</Fact>
        <Fact label="Header">
          {Object.keys(header).length ? Object.keys(header).join(', ') : 'no options set'}
        </Fact>
        <Fact label="Writes">{detail?.writes ? 'yes' : 'no'}</Fact>
      </dl>

      {data.needs_human_note && <p className="mt-2 text-xs text-amber-300">{data.needs_human_note}</p>}

      {detail && !detail.writes && (
        <p className="mt-3 flex items-start gap-2 border-t border-border-subtle/25 pt-3 text-xs text-muted-foreground">
          <Icon path={Glyphs.escalate} size={14} />
          <span>
            {data.policy === 'merge'
              ? 'Merge is the researched escalation target, but the research explicitly does not claim the merge action, so this logs and waits for a person.'
              : 'This policy writes nothing. The decision is logged with the existing record and no CRM row is created.'}
          </span>
        </p>
      )}

      <div className="mt-4 flex flex-wrap items-end gap-2">
        <Field label="Policy" id={`policy-${connection.id}`}>
          <select
            id={`policy-${connection.id}`}
            className={inputClass}
            value={data.policy}
            disabled={busy}
            onChange={(event) => onChangePolicy(connection, event.target.value)}
          >
            {(vocabulary?.policies || []).map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </Field>
        <Button icon="trash" variant="danger" disabled={busy} onClick={() => onDelete(connection)}>
          Delete
        </Button>
      </div>
    </Card>
  )
}

/** One inferred behaviour: what it is, why, and how to change it. */
function InferenceRow({ entry }) {
  const [open, setOpen] = useState(false)

  return (
    <li className="border-b border-border-subtle/15 last:border-0">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex w-full min-h-11 items-center gap-3 py-2 text-left transition-colors duration-150 hover:bg-muted/40"
      >
        <span className="min-w-0 flex-1 truncate font-mono text-[13px] text-foreground">
          {entry.topic}
        </span>
        <span className="shrink-0 font-mono text-[11px] text-muted-foreground">{entry.id}</span>
      </button>

      {open && (
        <div className="space-y-2 rounded-lg border border-border-subtle/25 bg-background/40 p-3 text-xs">
          <div>
            <p className="font-medium tracking-wide text-muted-foreground uppercase">
              What the research says
            </p>
            <p className="mt-0.5 text-foreground/90">{entry.basis}</p>
          </div>
          <div>
            <p className="font-medium tracking-wide text-muted-foreground uppercase">Why</p>
            <p className="mt-0.5 text-foreground/90">{entry.why}</p>
          </div>
          <div>
            <p className="font-medium tracking-wide text-muted-foreground uppercase">
              What this build chose
            </p>
            <JsonView value={entry.value} />
          </div>
          <div>
            <p className="font-medium tracking-wide text-muted-foreground uppercase">How to change it</p>
            <p className="mt-0.5 font-mono text-foreground/90">{entry.change_it}</p>
          </div>
          <div>
            <p className="font-medium tracking-wide text-muted-foreground uppercase">Affects</p>
            <p className="mt-0.5 text-foreground/90">{entry.blast_radius}</p>
          </div>
        </div>
      )}
    </li>
  )
}

function Stat({ label, value, hint, glyph }) {
  return (
    <Card className="card-hover">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{label}</p>
          <p className="mt-2 font-mono text-3xl font-semibold text-foreground">{value}</p>
          {hint && <p className="mt-1 truncate text-xs text-muted-foreground">{hint}</p>}
        </div>
        <span className="rounded-lg bg-muted p-2 text-accent">
          <Icon path={glyph} size={20} />
        </span>
      </div>
    </Card>
  )
}

export default function DuplicateGuard() {
  const [section, setSection] = useState('decisions')
  const [outcome, setOutcome] = useState('')
  const [busyId, setBusyId] = useState(null)
  const [notice, setNotice] = useState(null)
  const [noticeError, setNoticeError] = useState(null)

  const vocabulary = useAsync(() => dedupeApi.vocabulary(), [])
  const summary = useAsync(() => dedupeApi.summary(), [])
  const decisions = useAsync(() => dedupeApi.listDecisions({ outcome, limit: 60 }), [outcome])
  const connections = useAsync(() => dedupeApi.listConnections(), [])
  const records = useAsync(() => dedupeApi.listRecords({ limit: 60 }), [])
  // Rooms are a core collection, not this feature's, so they come from the core
  // client's own reader. This feature's routes are reached through `dedupeApi`.
  const rooms = useAsync(() => api.listRecords('room', { limit: 100 }), [])
  const inferences = useAsync(() => dedupeApi.inferences(), [])

  // -- the "try a lead" form
  const [roomId, setRoomId] = useState('')
  const [email, setEmail] = useState('')
  const [name, setName] = useState('')
  const [domain, setDomain] = useState('')
  const [externalId, setExternalId] = useState('')
  const [connectionId, setConnectionId] = useState('')
  const [preview, setPreview] = useState(null)
  const [tryError, setTryError] = useState(null)
  const [trying, setTrying] = useState(false)

  const roomOptions = rooms.data || []

  // Default to the first room once the list arrives. An effect rather than a
  // setState during render, so React is not asked to re-render mid-render.
  useEffect(() => {
    if (!roomId && roomOptions.length > 0) setRoomId(roomOptions[0].id)
  }, [roomId, roomOptions])

  async function guard(busyKey, successMessage, work) {
    setBusyId(busyKey)
    setNoticeError(null)
    setNotice(null)
    try {
      await work()
      setNotice(successMessage)
    } catch (error) {
      setNoticeError(error)
    } finally {
      setBusyId(null)
    }
  }

  function inboundPayload() {
    const payload = { name, email }
    if (domain) payload.domain = domain
    if (externalId) payload.external_id = externalId
    return payload
  }

  async function runCheck(submitEvent) {
    submitEvent.preventDefault()
    setTrying(true)
    setTryError(null)
    setPreview(null)
    try {
      setPreview(await dedupeApi.check(roomId, inboundPayload(), { connection_id: connectionId }))
    } catch (error) {
      setTryError(error)
    } finally {
      setTrying(false)
    }
  }

  async function runIngest() {
    setTrying(true)
    setTryError(null)
    try {
      const decision = await dedupeApi.ingest(roomId, inboundPayload(), { connection_id: connectionId })
      setPreview(decision.data)
      setNotice(`Logged as ${decision.data.outcome}. The room row now carries the decision.`)
      decisions.refetch()
      summary.refetch()
    } catch (error) {
      setTryError(error)
    } finally {
      setTrying(false)
    }
  }

  function changePolicy(connection, policy) {
    guard(connection.id, `"${connection.data.name}" is now ${policy}.`, async () => {
      await dedupeApi.updateConnection(connection.id, { policy })
      connections.refetch()
    })
  }

  function removeConnection(connection) {
    guard(connection.id, `Deleted "${connection.data.name}".`, async () => {
      await dedupeApi.deleteConnection(connection.id)
      connections.refetch()
      summary.refetch()
    })
  }

  const stats = useMemo(() => {
    const byOutcome = summary.data?.by_outcome || {}
    return [
      { label: 'Decisions', value: summary.data?.decisions ?? 0, hint: 'all policies', glyph: GLYPH_DUPLICATE },
      { label: 'Blocked', value: byOutcome.blocked ?? 0, hint: 'policy refused the write', glyph: GLYPH_BLOCKED },
      { label: 'Hard blocked', value: byOutcome.hard_blocked ?? 0, hint: 'no policy could help', glyph: GLYPH_DUPLICATE },
      { label: 'Needs a human', value: summary.data?.needs_human ?? 0, hint: `${summary.data?.crm_calls_avoided ?? 0} CRM calls avoided`, glyph: GLYPH_ESCALATE },
    ]
  }, [summary.data])

  const decisionRows = decisions.data?.decisions || []
  const connectionRows = connections.data?.connections || []
  const recordRows = records.data?.records || []
  const canTry = Boolean(roomId) && Boolean(email)

  return (
    <div className="space-y-5">
      <header>
        <h1 className="font-mono text-2xl font-semibold">Duplicate guard</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Check an inbound lead against the CRM&rsquo;s duplicate rule, then block, update, or
          deliberately create the duplicate according to the connection&rsquo;s policy. The decision
          and the matched record id are logged on the room.
        </p>
      </header>

      {noticeError && <ErrorNote error={noticeError} />}
      {notice && <Note>{notice}</Note>}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {stats.map((stat) => (
          <Stat key={stat.label} {...stat} />
        ))}
      </div>

      {/* Section switcher. A tablist, so the arrow-key semantics and the aria
          relationship are right and the choice can be shared by URL. */}
      <div role="tablist" aria-label="Duplicate guard sections" className="flex flex-wrap gap-2">
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
            <Icon path={item.glyph} size={16} />
            {item.label}
          </button>
        ))}
      </div>

      {/* -- decisions -- */}
      {section === 'decisions' && (
        <div id="panel-decisions" role="tabpanel" aria-labelledby="tab-decisions" className="space-y-4">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <h2 className="font-mono text-lg font-semibold">Decisions</h2>
              <p className="text-sm text-muted-foreground">
                Every inbound row, what the rule said, and what happened. Select a row for the
                matched record, the header that went out, and the inbound row.
              </p>
            </div>
            <div className="flex flex-wrap items-end gap-3">
              <Field label="Outcome" id="filter-outcome">
                <select
                  id="filter-outcome"
                  className={inputClass}
                  value={outcome}
                  onChange={(event) => setOutcome(event.target.value)}
                >
                  <option value="">All</option>
                  {(vocabulary.data?.outcomes || []).map((name) => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ))}
                </select>
              </Field>
              <Button icon="refresh" onClick={decisions.refetch}>
                Refresh
              </Button>
            </div>
          </div>

          {decisions.loading && <Spinner label="Loading decisions" />}
          {decisions.error && <ErrorNote error={decisions.error} onRetry={decisions.refetch} />}

          {!decisions.loading && !decisions.error && decisionRows.length === 0 && (
            <EmptyState
              title="No decisions yet"
              description="A lead arriving from a room, a CTA, or a download is evaluated here."
            />
          )}

          {decisionRows.length > 0 && (
            <Card className="p-4">
              <ul>
                {decisionRows.map((decision) => (
                  <DecisionRow key={decision.id} decision={decision} />
                ))}
              </ul>
            </Card>
          )}
        </div>
      )}

      {/* -- connections -- */}
      {section === 'connections' && (
        <div
          id="panel-connections"
          role="tabpanel"
          aria-labelledby="tab-connections"
          className="space-y-5"
        >
          <div>
            <h2 className="font-mono text-lg font-semibold">Connections</h2>
            <p className="text-sm text-muted-foreground">
              The dedupe policy is per connection, so two connections to two CRMs can disagree about
              what to do with a duplicate. Each card quotes the researched sentence its policy comes
              from, and says whether it writes anything at all.
            </p>
          </div>

          {connections.loading && <Spinner label="Loading connections" />}
          {connections.error && <ErrorNote error={connections.error} onRetry={connections.refetch} />}

          {!connections.loading && !connections.error && connectionRows.length === 0 && (
            <EmptyState title="No connections yet" description="Declare one to set a dedupe policy." />
          )}

          <ul className="grid gap-4 lg:grid-cols-2">
            {connectionRows.map((connection) => (
              <li key={connection.id}>
                <ConnectionCard
                  connection={connection}
                  vocabulary={vocabulary.data}
                  busy={busyId === connection.id}
                  onChangePolicy={changePolicy}
                  onDelete={removeConnection}
                />
              </li>
            ))}
          </ul>

          <section aria-labelledby="records-heading" className="space-y-3">
            <h3 id="records-heading" className="font-mono text-base font-semibold">
              The rows the rules match against
            </h3>
            <p className="text-sm text-muted-foreground">
              These stand in for the CRM&rsquo;s account and contact tables. A duplicate decision is
              only as explicable as the rows behind it.
            </p>

            {records.loading && <Spinner label="Loading rows" />}
            {records.error && <ErrorNote error={records.error} onRetry={records.refetch} />}

            {recordRows.length > 0 && (
              <div className="overflow-x-auto">
                <Card>
                  <table className="w-full min-w-[640px] text-left text-sm">
                    <caption className="sr-only">
                      The rows duplicate rules match against, and the keys they can match on
                    </caption>
                    <thead>
                      <tr className="border-b border-border-subtle/30 text-xs tracking-wide text-muted-foreground uppercase">
                        <th scope="col" className="px-4 py-3 font-medium">Name</th>
                        <th scope="col" className="px-4 py-3 font-medium">Email</th>
                        <th scope="col" className="px-4 py-3 font-medium">Domain</th>
                        <th scope="col" className="px-4 py-3 font-medium">External ID</th>
                        <th scope="col" className="px-4 py-3 font-medium">Account no.</th>
                      </tr>
                    </thead>
                    <tbody>
                      {recordRows.map((record) => (
                        <tr key={record.id} className="border-b border-border-subtle/15 last:border-0">
                          <td className="px-4 py-3">
                            <span className="flex items-center gap-2">
                              <Icon path={GLYPH_RECORD} size={14} />
                              <span className="truncate font-mono text-[13px] text-foreground">
                                {record.data?.name || '—'}
                              </span>
                            </span>
                            <span className="ml-6 block font-mono text-[11px] text-muted-foreground">
                              {record.data?.object_type}
                            </span>
                          </td>
                          <td className="px-4 py-3 font-mono text-[13px] text-foreground">
                            {record.data?.email || '—'}
                          </td>
                          <td className="px-4 py-3 font-mono text-[13px] text-foreground">
                            {record.data?.domain || '—'}
                          </td>
                          <td className="px-4 py-3 font-mono text-[13px] text-foreground">
                            {record.data?.external_id || '—'}
                          </td>
                          <td className="px-4 py-3 font-mono text-[13px] text-foreground">
                            {record.data?.account_number || '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </Card>
              </div>
            )}
          </section>
        </div>
      )}

      {/* -- try a lead -- */}
      {section === 'try' && (
        <div id="panel-try" role="tabpanel" aria-labelledby="tab-try" className="space-y-4">
          <div>
            <h2 className="font-mono text-lg font-semibold">Try a lead</h2>
            <p className="text-sm text-muted-foreground">
              Check evaluates the row and writes nothing, so the answer can be seen before anything
              is committed. Ingest runs the whole workflow and logs the decision on the room. Both
              call the same evaluation, so they always agree.
            </p>
          </div>

          {rooms.loading && <Spinner label="Loading rooms" />}

          <Card>
            <form onSubmit={runCheck} className="space-y-4">
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Room" id="lead-room" hint="The decision is logged on this room.">
                  <select
                    id="lead-room"
                    className={inputClass}
                    required
                    value={roomId}
                    onChange={(event) => setRoomId(event.target.value)}
                  >
                    <option value="">Choose a room</option>
                    {roomOptions.map((room) => (
                      <option key={room.id} value={room.id}>
                        {room.data?.name || room.id}
                      </option>
                    ))}
                  </select>
                </Field>

                <Field
                  label="Connection"
                  id="lead-connection"
                  hint="Leave blank to use the default policy, which is block."
                >
                  <select
                    id="lead-connection"
                    className={inputClass}
                    value={connectionId}
                    onChange={(event) => setConnectionId(event.target.value)}
                  >
                    <option value="">Default (block)</option>
                    {connectionRows.map((connection) => (
                      <option key={connection.id} value={connection.id}>
                        {connection.data?.name} — {connection.data?.policy}
                      </option>
                    ))}
                  </select>
                </Field>
              </div>

              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Email" id="lead-email" hint="HubSpot's primary unique identifier.">
                  <input
                    id="lead-email"
                    className={inputClass}
                    required
                    type="email"
                    placeholder="priya.raman@northwind.example"
                    value={email}
                    onChange={(event) => setEmail(event.target.value)}
                  />
                </Field>
                <Field label="Name" id="lead-name" hint="Used by the fuzzy domain+name matcher.">
                  <input
                    id="lead-name"
                    className={inputClass}
                    value={name}
                    onChange={(event) => setName(event.target.value)}
                  />
                </Field>
              </div>

              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Domain" id="lead-domain">
                  <input
                    id="lead-domain"
                    className={inputClass}
                    placeholder="northwind.example"
                    value={domain}
                    onChange={(event) => setDomain(event.target.value)}
                  />
                </Field>
                <Field
                  label="External ID"
                  id="lead-external"
                  hint="Two rows on one external ID is the researched 300 hard block."
                >
                  <input
                    id="lead-external"
                    className={inputClass}
                    value={externalId}
                    onChange={(event) => setExternalId(event.target.value)}
                  />
                </Field>
              </div>

              {tryError && <ErrorNote error={tryError} />}

              <div className="flex flex-wrap gap-2">
                <Button type="submit" disabled={trying || !canTry}>
                  {trying ? 'Checking…' : 'Check (writes nothing)'}
                </Button>
                <Button
                  variant="primary"
                  icon="plus"
                  disabled={trying || !canTry}
                  onClick={runIngest}
                >
                  Ingest and log
                </Button>
              </div>
            </form>
          </Card>

          {preview && (
            <Card className="space-y-3">
              <div className="flex flex-wrap items-center gap-3">
                <h3 className="font-mono text-base font-semibold">Result</h3>
                <OutcomeChip outcome={preview.outcome} glyphs={Glyphs} />
                <Badge tone="neutral">{preview.policy}</Badge>
                {!preview.crm_called && <Badge tone="neutral">answered by the in-room check</Badge>}
              </div>
              <dl className="grid gap-2 sm:grid-cols-2">
                <Fact label="CRM result">{preview.crm_result}</Fact>
                <Fact label="Match key">{preview.match_key || 'none'}</Fact>
                <Fact label="Status">{preview.status ?? 'none'}</Fact>
                <Fact label="Needs a human">{preview.needs_human ? 'yes' : 'no'}</Fact>
              </dl>
              <p className="text-sm text-foreground/90">{preview.reason}</p>
              {preview.detail && <p className="text-sm text-muted-foreground">{preview.detail}</p>}
              <div>
                <p className="mb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
                  Header that was asked for
                </p>
                <p className="font-mono text-[13px] text-foreground">
                  {Object.keys(preview.header || {}).length
                    ? `${preview.header_name}: ${preview.header_wire}`
                    : `${preview.header_name}: (no options set)`}
                </p>
              </div>
              {preview.matched?.length > 0 && (
                <div>
                  <p className="mb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
                    The existing record
                  </p>
                  <JsonView value={preview.matched} />
                </div>
              )}
            </Card>
          )}
        </div>
      )}

      {/* -- what this infers -- */}
      {section === 'inferences' && (
        <div
          id="panel-inferences"
          role="tabpanel"
          aria-labelledby="tab-inferences"
          className="space-y-4"
        >
          <div>
            <h2 className="font-mono text-lg font-semibold">What this infers</h2>
            <p className="text-sm text-muted-foreground">
              The header and its three fields, the 300, the unique index, and the four matching keys
              are all sourced. The rest is judgement: the parts of this workflow the research makes
              no claims about, and the one action it explicitly declines to claim. Each is listed
              here with the reason for the choice and how to change it.
            </p>
          </div>

          {inferences.loading && <Spinner label="Loading inferences" />}
          {inferences.error && <ErrorNote error={inferences.error} onRetry={inferences.refetch} />}

          {inferences.data && (
            <>
              <Card className="p-4">
                <p className="text-xs leading-relaxed text-muted-foreground">
                  <span className="font-medium text-foreground">From the research: </span>
                  &ldquo;{inferences.data.sourced_quote}&rdquo;
                </p>
              </Card>

              <Card className="p-4">
                <ul>
                  {(inferences.data.inferences || []).map((entry) => (
                    <InferenceRow key={entry.id} entry={entry} />
                  ))}
                </ul>
              </Card>
            </>
          )}
        </div>
      )}
    </div>
  )
}
