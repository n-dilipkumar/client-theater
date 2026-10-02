import { useMemo, useState } from 'react'

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

import * as api from './api'

/**
 * WF-079: read the audit trail as evidence a third party can check.
 *
 * The page is built around one claim the research makes - the trail is
 * tamper-evident: any modification invalidates it - and the one thing that claim
 * needs in order to be worth anything: a reader who can recompute the digest
 * without trusting this application. So the integrity card shows the head *and* the
 * recipe, and the anchor controls exist because a fresh export always agrees with
 * itself and that is not verification.
 *
 * Three states are shown rather than described, because each is a rule that is easy
 * to state and easy to get wrong:
 *
 * * the administrator gate, as a role switcher that really gets refused;
 * * a sandbox key, which masks every address to the literal `hidden`;
 * * an address that was never captured, which is not the same fact as a blank one.
 */

const PAGE_LIMIT = 25

function shortHash(value) {
  return typeof value === 'string' && value.length > 16 ? `${value.slice(0, 16)}…` : value || '—'
}

function codeOptions(vocabulary) {
  const codes = vocabulary?.vocabulary?.codes || []
  return [...codes].sort((a, b) => a.code - b.code)
}

export default function AuditTrailExport() {
  const [role, setRole] = useState('instance_admin')
  const [roomId, setRoomId] = useState('')
  const [sandbox, setSandbox] = useState(false)
  const [codeFilter, setCodeFilter] = useState('')
  const [actorFilter, setActorFilter] = useState('')
  const [offset, setOffset] = useState(0)
  const [notice, setNotice] = useState(null)
  const [failure, setFailure] = useState(null)

  const vocabularyState = useAsync(() => api.vocabulary(), [])
  const roomState = useAsync(() => api.rooms(), [])

  const roomOptions = roomState.data?.records || []
  // Resolved before the read effects, and the effects key off this rather than the
  // selection: on a fresh page `roomId` is empty and the fallback is the first room,
  // so keying off the selection left the page empty until the selector was touched.
  const effectiveRoom = roomId || roomOptions[0]?.id || ''

  const reader = useMemo(() => ({ role, sandbox: sandbox ? 'true' : '' }), [role, sandbox])

  const filters = useMemo(
    () => ({
      ...reader,
      limit: PAGE_LIMIT,
      offset,
      action: codeFilter,
      actor: actorFilter,
    }),
    [reader, offset, codeFilter, actorFilter],
  )

  const trailState = useAsync(
    () => (effectiveRoom ? api.trail(effectiveRoom, filters) : Promise.resolve(null)),
    [effectiveRoom, filters],
  )
  const summaryState = useAsync(
    () => (effectiveRoom ? api.summary(effectiveRoom, reader) : Promise.resolve(null)),
    [effectiveRoom, reader],
  )

  if (vocabularyState.loading) return <Spinner label="Loading the export vocabulary" />
  if (vocabularyState.error) {
    return <ErrorNote error={vocabularyState.error} onRetry={vocabularyState.refetch} />
  }

  const vocabulary = vocabularyState.data
  const roles = vocabulary?.access?.roles || []
  const denied = trailState.error?.status === 403

  async function run(label, action) {
    setNotice(null)
    setFailure(null)
    try {
      const result = await action()
      setNotice(`${label}: ${typeof result === 'string' ? result : 'done'}`)
      trailState.refetch()
      summaryState.refetch()
    } catch (error) {
      setFailure(`${label} failed: ${error.message}`)
    }
  }

  return (
    <div className="space-y-6">
      <header>
        <h1 className="font-display text-xl font-semibold text-foreground">Audit trail export</h1>
        <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
          The trail as evidence: who acted, what they did, when, from where, and a SHA-256 chain a
          third party can recompute from this export alone.
        </p>
      </header>

      <Card>
        <div className="grid gap-4 md:grid-cols-4">
          <Field
            id="wf079-role"
            label="Caller role"
            hint={
              vocabulary?.access?.reader_role
                ? `Only ${vocabulary.access.reader_role} may read the export.`
                : undefined
            }
          >
            <select
              id="wf079-role"
              className={inputClass}
              value={role}
              onChange={(event) => {
                setRole(event.target.value)
                setOffset(0)
              }}
            >
              {roles.map((candidate) => (
                <option key={candidate.id} value={candidate.id}>
                  {candidate.label}
                  {candidate.may_read ? ' — may read' : ''}
                </option>
              ))}
            </select>
          </Field>

          <Field id="wf079-room" label="Room">
            <select
              id="wf079-room"
              className={inputClass}
              value={effectiveRoom}
              onChange={(event) => {
                setRoomId(event.target.value)
                setOffset(0)
              }}
            >
              {roomOptions.length === 0 && <option value="">No rooms yet</option>}
              {roomOptions.map((room) => (
                <option key={room.id} value={room.id}>
                  {room.data?.name || room.id}
                </option>
              ))}
            </select>
          </Field>

          <Field id="wf079-sandbox" label="Sandbox key" hint="Masks every address to hidden.">
            <label className="flex min-h-11 items-center gap-3 text-sm text-foreground">
              <input
                id="wf079-sandbox"
                type="checkbox"
                className="h-4 w-4"
                checked={sandbox}
                onChange={(event) => setSandbox(event.target.checked)}
              />
              Present a sandbox key
            </label>
          </Field>

          <Field id="wf079-code" label="Action code" hint="Compliance reviews query by code.">
            <select
              id="wf079-code"
              className={inputClass}
              value={codeFilter}
              onChange={(event) => {
                setCodeFilter(event.target.value)
                setOffset(0)
              }}
            >
              <option value="">Every code</option>
              {codeOptions(vocabulary).map((entry) => (
                <option key={entry.code} value={entry.code}>
                  {entry.code} — {entry.name}
                </option>
              ))}
            </select>
          </Field>

          <Field id="wf079-actor" label="Actor" hint="Matches the audit actor exactly.">
            <input
              id="wf079-actor"
              className={inputClass}
              value={actorFilter}
              onChange={(event) => {
                setActorFilter(event.target.value)
                setOffset(0)
              }}
            />
          </Field>
        </div>
      </Card>

      {denied && (
        <Card className="border-destructive/40 bg-destructive/5">
          <p className="text-sm font-semibold text-destructive">{trailState.error.message}</p>
          <p className="mt-1 text-sm text-muted-foreground">
            {/* The shared `apiRequest` raises `body.detail || body.error`, so the
                server's `remediation` field never reaches this page and `lib/api.js`
                is shared. The remedy comes from the vocabulary instead, which
                publishes the gate's own wording; the fallback keeps a refusal
                actionable before that has loaded. */}
            {vocabulary?.access?.denied?.remediation ||
              'Switch the caller role to the administrator tier to read the export.'}
          </p>
        </Card>
      )}

      {failure && (
        <Card className="border-destructive/40 bg-destructive/5">
          <p className="text-sm text-destructive">{failure}</p>
        </Card>
      )}
      {notice && (
        <Card className="border-accent/30 bg-accent/5">
          <p className="text-sm text-foreground">{notice}</p>
        </Card>
      )}

      {effectiveRoom && summaryState.data && (
        <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard
            label="Entries in scope"
            value={summaryState.data.scoped_rows ?? 0}
            hint={`of ${summaryState.data.audit_log_rows ?? 0} in the whole log`}
          />
          <StatCard
            label="Addresses not captured"
            value={summaryState.data.addresses_not_captured ?? 0}
            hint="Rows whose request this workflow never saw"
          />
          <StatCard
            label="Checks passed"
            value={summaryState.data.verification_pass ?? 0}
            hint="Identity checks that succeeded"
          />
          <StatCard
            label="Checks rejected"
            value={summaryState.data.verification_fail ?? 0}
            hint="A rejected attempt is as visible as a pass"
          />
        </section>
      )}

      {effectiveRoom && !denied && trailState.loading && <Spinner label="Reading the trail" />}

      {effectiveRoom && !denied && trailState.data && (
        <>
          <Card>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h2 className="font-display text-base font-semibold text-foreground">
                {trailState.data.count} of {trailState.data.total} entries
              </h2>
              <div className="flex gap-2">
                <Button
                  onClick={() => setOffset(Math.max(0, offset - PAGE_LIMIT))}
                  disabled={offset === 0}
                >
                  Previous page
                </Button>
                <Button
                  onClick={() => setOffset(offset + PAGE_LIMIT)}
                  disabled={!trailState.data.has_more}
                >
                  Next page
                </Button>
              </div>
            </div>

            <div className="mt-4 overflow-x-auto">
              <table className="w-full min-w-[720px] text-left text-sm">
                <thead>
                  <tr className="border-b border-border-subtle text-[11px] uppercase tracking-[0.12em] text-muted-foreground">
                    <th scope="col" className="py-2 pr-3">Seq</th>
                    <th scope="col" className="py-2 pr-3">When</th>
                    <th scope="col" className="py-2 pr-3">Code</th>
                    <th scope="col" className="py-2 pr-3">Actor</th>
                    <th scope="col" className="py-2 pr-3">Address</th>
                    <th scope="col" className="py-2">Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {trailState.data.entries.map((entry) => (
                    <tr key={entry.seq} className="border-b border-border-subtle/60">
                      <td className="py-2 pr-3 font-mono text-xs text-muted-foreground">
                        {entry.seq}
                      </td>
                      <td className="whitespace-nowrap py-2 pr-3 font-mono text-xs text-muted-foreground">
                        {String(entry.date_created || '').replace('T', ' ').slice(0, 19)}
                      </td>
                      <td className="py-2 pr-3">
                        <Badge>{entry.action?.code}</Badge>{' '}
                        <span className="text-xs text-muted-foreground">{entry.action?.name}</span>
                      </td>
                      <td className="py-2 pr-3 font-mono text-xs">{entry.actor || '—'}</td>
                      <td className="py-2 pr-3 font-mono text-xs">
                        {entry.ip_status === 'not_captured' ? (
                          <span className="text-muted-foreground">not captured</span>
                        ) : (
                          entry.ip_address
                        )}
                      </td>
                      <td className="py-2 text-xs text-muted-foreground">{entry.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {trailState.data.entries.length === 0 && (
              <EmptyState
                title="Nothing in this scope"
                description="No audit row in this room matches the filters you chose."
              />
            )}
          </Card>

          <IntegrityCard
            reader={reader}
            roomId={effectiveRoom}
            integrity={trailState.data.integrity}
            onPin={() =>
              run('Anchor pinned', () => api.pinAnchor(effectiveRoom, reader))
            }
          />
        </>
      )}

      <ReportsCard reader={reader} roomId={effectiveRoom} notify={setNotice} fail={setFailure} />
      <InferencesCard />
    </div>
  )
}

/**
 * The chain head, the recipe, and the two controls that make the claim falsifiable.
 *
 * A fresh export always agrees with itself, because it is sealed as it is built, so
 * `Verify` here compares against a *pinned* head rather than recomputing the same
 * rows. That distinction is the whole reason the anchors route exists.
 */
function IntegrityCard({ reader, roomId, integrity, onPin }) {
  const [check, setCheck] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  async function verify() {
    setBusy(true)
    setError(null)
    try {
      setCheck(await api.verifyTrail(roomId, reader))
    } catch (failure) {
      setError(failure.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h2 className="font-display text-base font-semibold text-foreground">Integrity</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            {integrity?.entries ?? 0} entries folded into a {integrity?.algorithm} chain.
          </p>
        </div>
        <div className="flex gap-2">
          <Button onClick={onPin}>Pin this head</Button>
          <Button variant="primary" onClick={verify} disabled={busy}>
            {busy ? 'Verifying' : 'Verify against anchors'}
          </Button>
        </div>
      </div>

      <dl className="mt-4 grid gap-3 sm:grid-cols-2">
        <div>
          <dt className="text-[11px] uppercase tracking-[0.12em] text-muted-foreground">Chain head</dt>
          <dd className="mt-1 break-all font-mono text-xs text-foreground">
            {integrity?.head || '—'}
          </dd>
        </div>
        <div>
          <dt className="text-[11px] uppercase tracking-[0.12em] text-muted-foreground">
            Document hash
          </dt>
          <dd className="mt-1 break-all font-mono text-xs text-foreground">
            {integrity?.document_hash || '—'}
          </dd>
        </div>
      </dl>

      <details className="mt-4">
        <summary className="min-h-11 cursor-pointer text-sm font-medium text-foreground">
          How to recompute this yourself
        </summary>
        <div className="mt-2 space-y-2 text-xs text-muted-foreground">
          <p>
            Hashed fields, in order: <span className="font-mono">{integrity?.hashed_fields?.join(', ')}</span>
          </p>
          <p className="break-all font-mono">{integrity?.canonical_form}</p>
          <p className="break-all font-mono">{integrity?.chain_step}</p>
          <p>Order: {integrity?.order}</p>
          <p>{integrity?.hashed_values}</p>
          <p>{integrity?.document_hash_basis?.does_not_cover}</p>
          <p>{integrity?.document_hash_basis?.completeness}</p>
        </div>
      </details>

      {error && <p className="mt-3 text-sm text-destructive">{error}</p>}

      {check && (
        <div className="mt-4 rounded-sm border border-border-subtle p-3">
          <p className="text-sm text-foreground">
            {check.anchors === 0
              ? 'No anchors pinned yet, so nothing was compared. Pin a head first.'
              : `${check.anchors} anchor(s) checked. ${check.intact ? 'All intact.' : 'DIVERGENCE FOUND.'}`}
          </p>
          {check.checks?.map((entry) => (
            <p key={entry.anchor_id} className="mt-1 font-mono text-xs text-muted-foreground">
              {entry.head_at_anchor ? shortHash(entry.head_at_anchor) : '—'} →{' '}
              {entry.head_now ? shortHash(entry.head_now) : '—'}
              {entry.first_divergent ? ` first divergent seq ${entry.first_divergent.seq}` : ''}
            </p>
          ))}
        </div>
      )}
    </Card>
  )
}

/** Request, generate and download the date-ranged CSV reports. */
function ReportsCard({ reader, roomId, notify, fail }) {
  const range = useMemo(() => api.thirtyDayRange(), [])
  const [form, setForm] = useState({
    report_type: ['user_activity'],
    start_date: range.startDate,
    end_date: range.endDate,
    email: 'compliance@example.com',
  })
  const [busy, setBusy] = useState(false)
  const listing = useAsync(() => (roomId ? api.reports(reader) : Promise.resolve(null)), [roomId, reader])

  const reportTypes = ['user_activity', 'document_status', 'sms_activity', 'fax_usage']

  function toggle(type) {
    setForm((previous) => ({
      ...previous,
      report_type: previous.report_type.includes(type)
        ? previous.report_type.filter((item) => item !== type)
        : [...previous.report_type, type],
    }))
  }

  async function submit(event) {
    event.preventDefault()
    setBusy(true)
    try {
      const result = await api.requestReports(roomId, form, reader)
      notify(`Requested ${result.requested} report(s); each is notified when generated.`)
      listing.refetch()
    } catch (error) {
      fail(`Report request refused: ${error.message}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <h2 className="font-display text-base font-semibold text-foreground">CSV reports</h2>
      <p className="mt-1 text-sm text-muted-foreground">
        A date range of at most twelve months, starting no more than ten years back. One notification
        is recorded per requested report type, each carrying a download link rather than the file.
      </p>

      <form className="mt-4 space-y-4" onSubmit={submit}>
        <fieldset>
          <legend className="text-[13px] font-medium text-foreground">Report types</legend>
          <div className="mt-2 flex flex-wrap gap-3">
            {reportTypes.map((type) => (
              <label key={type} className="flex min-h-11 items-center gap-2 text-sm text-foreground">
                <input
                  type="checkbox"
                  className="h-4 w-4"
                  checked={form.report_type.includes(type)}
                  onChange={() => toggle(type)}
                />
                {type}
              </label>
            ))}
          </div>
        </fieldset>

        <div className="grid gap-4 sm:grid-cols-3">
          <Field id="wf079-start" label="Start date" hint="MM/DD/YYYY">
            <input
              id="wf079-start"
              className={inputClass}
              value={form.start_date}
              onChange={(event) => setForm({ ...form, start_date: event.target.value })}
            />
          </Field>
          <Field id="wf079-end" label="End date" hint="Inclusive">
            <input
              id="wf079-end"
              className={inputClass}
              value={form.end_date}
              onChange={(event) => setForm({ ...form, end_date: event.target.value })}
            />
          </Field>
          <Field id="wf079-email" label="Deliver to">
            <input
              id="wf079-email"
              className={inputClass}
              value={form.email}
              onChange={(event) => setForm({ ...form, email: event.target.value })}
            />
          </Field>
        </div>

        <Button type="submit" variant="primary" disabled={busy || !roomId}>
          {busy ? 'Requesting' : 'Request reports'}
        </Button>
      </form>

      {listing.data && (
        <div className="mt-5 space-y-3">
          {listing.data.count === 0 && (
            <EmptyState title="No reports requested yet" description="Request one above." />
          )}
          {listing.data.reports.map((report) => (
            <ReportRow key={report.id} report={report} reader={reader} onDone={listing.refetch} />
          ))}
        </div>
      )}
    </Card>
  )
}

function ReportRow({ report, reader, onDone }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  async function generate() {
    setBusy(true)
    setError(null)
    try {
      await api.generateReport(report.id, reader)
      onDone()
    } catch (failure) {
      setError(failure.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="rounded-sm border border-border-subtle p-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-medium text-foreground">
            {report.report_type} <Badge>{report.state}</Badge>
          </p>
          <p className="mt-1 font-mono text-xs text-muted-foreground">
            {report.start_date} to {report.end_date} — {report.row_count ?? 0} row(s)
            {report.sha256 ? ` — sha256 ${shortHash(report.sha256)}` : ''}
          </p>
          {report.note && <p className="mt-1 text-xs text-muted-foreground">{report.note}</p>}
          {report.delivery && (
            <p className="mt-1 font-mono text-xs text-muted-foreground">
              notified {report.delivery.to} — {report.delivery.carries}
            </p>
          )}
        </div>
        <div className="flex gap-2">
          {report.state === 'pending' && (
            <Button onClick={generate} disabled={busy}>
              {busy ? 'Generating' : 'Generate'}
            </Button>
          )}
          {report.state === 'ready' && (
            <a
              href={api.downloadUrl(report.id, report.delivery?.token, reader)}
              className="inline-flex min-h-11 items-center rounded-sm border border-border-subtle bg-surface px-4 text-sm text-foreground hover:border-accent hover:text-accent"
            >
              Download CSV
            </a>
          )}
        </div>
      </div>
      {error && <p className="mt-2 text-sm text-destructive">{error}</p>}
    </div>
  )
}

/** Every judgement call this build made, so a reviewer can disagree with a named entry. */
function InferencesCard() {
  const state = useAsync(() => api.inferences(), [])
  if (state.loading) return null
  if (state.error) return null
  const groups = [
    ['Action codes', state.data?.action_codes],
    ['Export', state.data?.export],
    ['Reports', state.data?.reports],
  ].filter(([, items]) => Array.isArray(items))

  return (
    <Card>
      <details>
        <summary className="min-h-11 cursor-pointer font-display text-base font-semibold text-foreground">
          What this build had to decide, and why
        </summary>
        <div className="mt-3 space-y-4">
          {groups.map(([title, items]) => (
            <div key={title}>
              <h3 className="text-[11px] uppercase tracking-[0.12em] text-muted-foreground">{title}</h3>
              <ul className="mt-2 space-y-3">
                {items.map((item) => (
                  <li key={item.claim}>
                    <p className="text-sm font-medium text-foreground">{item.claim}</p>
                    <p className="mt-0.5 text-xs text-muted-foreground">{item.basis}</p>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </details>
    </Card>
  )
}
