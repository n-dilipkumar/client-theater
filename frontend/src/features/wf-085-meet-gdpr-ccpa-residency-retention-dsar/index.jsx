import { useState } from 'react'

import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorNote,
  Field,
  Icon,
  Spinner,
  StatCard,
  inputClass,
  useAsync,
} from '@/components/ui'

import {
  CONSENT_STATES,
  DSAR_STATES,
  formatInstant,
  gateOutcome,
  plural,
  privacyApi,
} from './api'
import { ConsentRow, GateVerdict, ResidueList, WindowTable } from './primitives'

/**
 * WF-085: meet GDPR / CCPA with residency, retention, consent and a DSAR path.
 *
 * The page has five jobs, in this order, and the order is the design.
 *
 * **Say where the data is.** Residency is a deployment-time choice, and the specification
 * says it "fixes the jurisdiction of both content and engagement data". So the region and
 * the jurisdiction it sits in are the first thing on the page, not a setting buried in a
 * form.
 *
 * **Say what the gate does, not just what it decided.** The specification requires the
 * consent gate to fail closed, and gives the deny behaviour exactly: "a unique ID per
 * page view" and "does not use cookies to persist session data". So a deny renders those
 * two consequences as words. A page that showed only the word "denied" would leave a
 * reader unable to tell a working gate from a broken one.
 *
 * **Show the configured window beside the researched ceiling.** The evidence says "up to
 * 30 days", which is a maximum. A deployment may promise less. If the page showed one
 * number, an operator could not tell which of the two the room enforces.
 *
 * **Name what the erasure left behind.** Per-subject deletion is the specification's "hard
 * part", and the audit trail keeps the rows the erasure removed. So the board reports
 * residue with a reason on every row. An erasure that rendered a clean tick would be the
 * failure this workflow exists to prevent.
 *
 * **Claim nothing this repository does not hold.** The vendor's certifications are carried
 * as unverified claims with the subject they are about, and no badge, tick or "compliant"
 * flag appears anywhere on this page.
 *
 * Every state this page can be in is rendered: loading, error, and empty. A board that
 * goes blank when the API is down reads as "nothing is being tracked", which is the one
 * reading that must never be possible.
 */

/** The status line after a write. One shape, so every outcome reads the same way. */
function Outcome({ outcome, onDismiss }) {
  if (!outcome) return null
  const failed = outcome.status === 'error'
  return (
    <div
      role="status"
      className={`flex flex-wrap items-center justify-between gap-3 rounded-sm border p-4 ${
        failed
          ? 'border-destructive/40 bg-destructive/10'
          : 'border-accent/30 bg-accent/10'
      }`}
    >
      <div className="min-w-0">
        <p className={`text-sm font-semibold ${failed ? 'text-destructive' : 'text-accent'}`}>
          {outcome.title}
        </p>
        <p className="mt-0.5 text-sm text-muted-foreground">{outcome.detail}</p>
      </div>
      <Button icon="close" onClick={onDismiss}>
        Dismiss
      </Button>
    </div>
  )
}

/** A labelled text field. The label is always visible; placeholder-only is banned. */
function TextField({ id, label, hint, value, onChange, mono = true, placeholder }) {
  return (
    <Field label={label} hint={hint} id={id}>
      <input
        id={id}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        className={`${inputClass} ${mono ? 'font-mono' : ''}`}
      />
    </Field>
  )
}

/** A labelled select. The option list is served, so it is never written here twice. */
function SelectField({ id, label, hint, value, onChange, options, mono = true }) {
  return (
    <Field label={label} hint={hint} id={id}>
      <select
        id={id}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className={`${inputClass} ${mono ? 'font-mono' : ''}`}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </Field>
  )
}

/**
 * The region, its jurisdiction, and whether this room needs the consent gate.
 *
 * `records_unstamped` is rendered beside the counts because a record holding no region is
 * a record no jurisdiction question can be answered about.
 */
function ResidencyCard({ residency, onPin, pending }) {
  const current = residency?.residency
  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <h2 className="text-lg font-semibold">Residency</h2>
        {current ? (
          <Badge tone="insert">{current.jurisdiction}</Badge>
        ) : (
          <Badge tone="warning">no region recorded</Badge>
        )}
      </div>

      {current ? (
        <>
          <dl className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Fact label="Region" value={current.residency_region} mono />
            <Fact label="Jurisdiction" value={current.jurisdiction} mono />
            <Fact
              label="Cross-border mechanism"
              value={current.transfer_declared ? current.transfer_mechanism : 'none declared'}
              mono
            />
            <Fact label="Re-stamped on the last move" value={current.records_moved} />
          </dl>
          <p className="mt-3 text-xs text-muted-foreground">
            {residency.transfer_mechanism_note}
          </p>
        </>
      ) : (
        <p className="mt-3 text-sm text-muted-foreground">
          No region has been recorded for this room. Residency is a deployment-time choice,
          and until one is recorded the board cannot say which jurisdiction holds this
          room&apos;s data.
        </p>
      )}

      <div className="mt-4 grid gap-3 sm:grid-cols-3">
        <Fact label="Records in region" value={residency?.records_in_region ?? 0} />
        <Fact label="Records with no region" value={residency?.records_unstamped ?? 0} />
        <Fact
          label="Consent required here"
          value={residency?.consent_required_here ? 'yes' : 'no'}
        />
      </div>

      {residency?.rules?.move_recorded ? (
        <p className="mt-4 border-t border-border-subtle pt-3 font-mono text-xs text-muted-foreground">
          {residency.rules.move_recorded}
        </p>
      ) : null}

      {residency?.certification_note ? (
        <p className="mt-3 text-xs text-muted-foreground">{residency.certification_note}</p>
      ) : null}

      {residency?.vendor_claims?.length ? (
        <div className="mt-4 border-t border-border-subtle pt-3">
          <p className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
            Vendor claims, kept as claims
          </p>
          <ul className="mt-2 space-y-2">
            {residency.vendor_claims.map((claim) => (
              <li key={claim.claim} className="text-xs text-muted-foreground">
                <span className="text-foreground">{claim.claim}</span> - about {claim.subject}.
                {' '}
                {claim.note}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {onPin ? (
        <div className="mt-4 border-t border-border-subtle pt-4">
          <p className="text-sm text-foreground">Pin this deployment to a region</p>
          <p className="mt-1 text-xs text-muted-foreground">
            The move re-stamps every record the room holds, so one jurisdiction always
            holds its data. Administrator only.
          </p>
          <div className="mt-3">
            <Button icon="plus" onClick={onPin} disabled={pending}>
              {pending ? 'Moving...' : 'Move region'}
            </Button>
          </div>
        </div>
      ) : null}
    </Card>
  )
}

/** One labelled fact. The label is text and the value is mono, because it is machine data. */
function Fact({ label, value, mono = false }) {
  return (
    <div className="border-t border-border-subtle pt-2">
      <dt className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">{label}</dt>
      <dd className={`mt-1 text-sm text-foreground ${mono ? 'font-mono' : ''}`}>{value}</dd>
    </div>
  )
}

/** The three classes, the schedule beside them, and the collections nothing ages. */
function RetentionCard({ policy, schedule, onRun, onSetWindow, pending }) {
  const byClass = {}
  for (const row of schedule?.classes || []) byClass[row.class] = row
  const rows = (policy?.policy || []).map((row) => ({
    ...row,
    schedule: byClass[row.class] || null,
  }))

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <h2 className="text-lg font-semibold">Retention</h2>
        <Badge tone="neutral">unit: {policy?.window_unit || 'days'}</Badge>
      </div>
      <p className="mt-1 text-xs text-muted-foreground">
        A configured window may be shorter than the researched ceiling and never longer.
        The evidence says &quot;up to&quot;, so the ceiling is a maximum rather than a
        target.
      </p>

      <div className="mt-4">
        <WindowTable rows={rows} windowUnit={policy?.window_unit || 'days'} />
      </div>

      <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Due now"
          value={schedule ? (schedule.classes || []).reduce((n, r) => n + r.due, 0) : 0}
          hint="Past its window"
        />
        <StatCard
          label="Retained"
          value={schedule ? (schedule.classes || []).reduce((n, r) => n + r.retained, 0) : 0}
          hint="Still inside its window"
        />
        <StatCard
          label="Undated"
          value={schedule?.undated ?? 0}
          hint="Cannot be placed in a window"
        />
        <StatCard
          label="Not aged by this table"
          value={policy?.unmapped_collections?.length ?? 0}
          hint="Collections holding data nothing ages"
        />
      </div>

      {schedule?.undated ? (
        <p className="mt-3 text-xs text-muted-foreground">
          {schedule.undated} record(s) carry no readable instant, so they are reported
          rather than aged: guessing a date would either purge a record early or let one
          outlive its window.
        </p>
      ) : null}

      {policy?.unmapped_collections?.length ? (
        <div className="mt-3 rounded-sm border border-border-subtle bg-muted p-3">
          <p className="text-sm text-foreground">Collections nothing ages</p>
          <p className="mt-1 font-mono text-xs text-muted-foreground">
            {policy.unmapped_collections.join(', ')}
          </p>
          <p className="mt-2 text-xs text-muted-foreground">{policy.no_side_channel_rule}</p>
        </div>
      ) : null}

      {onSetWindow ? (
        <div className="mt-4 flex flex-wrap gap-2 border-t border-border-subtle pt-4">
          <Button icon="refresh" onClick={onSetWindow} disabled={pending}>
            Shorten the recording window to 14 days
          </Button>
          <Button icon="trash" onClick={onRun} disabled={pending}>
            {pending ? 'Running...' : 'Run retention now'}
          </Button>
        </div>
      ) : null}
    </Card>
  )
}

/** The gate: its three answers, the decision for the selected region, and the records. */
function ConsentCard({ report, evaluation, selectedRecord, onSelect }) {
  const recorded = report?.recorded || []
  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <h2 className="text-lg font-semibold">Consent gate</h2>
        <Badge tone={report?.fails_closed ? 'insert' : 'warning'}>
          {report?.fails_closed ? 'fails closed' : 'unknown'}
        </Badge>
      </div>
      <p className="mt-1 text-xs text-muted-foreground">
        {report?.fails_closed_rule}
      </p>

      <div className="mt-4">
        <GateVerdict decision={evaluation} />
      </div>

      <p className="mt-4 text-xs text-muted-foreground">{report?.enforcement_quote}</p>

      <div className="mt-4 grid gap-2 sm:grid-cols-3">
        {(report?.consent_regions || []).map((region) => (
          <div
            key={region.id}
            className="flex min-h-11 items-center justify-between gap-2 rounded-sm border border-border-subtle px-3"
          >
            <span className="text-sm text-foreground">{region.label}</span>
            <Badge tone="insert">consent required</Badge>
          </div>
        ))}
      </div>

      {report?.unsupported_signals?.length ? (
        <p className="mt-4 text-xs text-muted-foreground">
          Not a consent signal: {report.unsupported_signals.map((row) => row.signal).join(', ')}.
          {' '}
          {report.unsupported_signals[0]?.reason}
        </p>
      ) : null}

      {report?.revocation_rule ? (
        <p className="mt-2 text-xs text-muted-foreground">{report.revocation_rule}</p>
      ) : null}

      <div className="mt-6">
        <h3 className="text-base font-semibold">Recorded signals</h3>
        {recorded.length === 0 ? (
          <EmptyState
            title="No consent signal recorded yet"
            description="A signal appears here once a page records one for this room."
          />
        ) : (
          <ul className="mt-2 divide-y divide-border-subtle">
            {recorded.map((record) => (
              <li key={record.id}>
                <ConsentRow
                  record={record}
                  onSelect={onSelect}
                  selectedId={selectedRecord?.id}
                />
              </li>
            ))}
          </ul>
        )}
      </div>

      {selectedRecord ? (
        <div className="mt-4 border-t border-border-subtle pt-4">
          <p className="text-sm text-foreground">
            {selectedRecord.subject} - {consentStateLabel(selectedRecord.state)}
          </p>
          <p className="mt-1 text-xs text-muted-foreground">{selectedRecord.reason}</p>
        </div>
      ) : null}
    </Card>
  )
}

function consentStateLabel(state) {
  const found = CONSENT_STATES.find((entry) => entry.value === state)
  return found ? found.label : state
}

/** The erasure requests, what each found, and what each left behind. */
function DsarCard({ list, requests, selectedId, onSelect, onOpen, onFulfil, pending }) {
  const selected = (requests?.requests || []).find((row) => row.id === selectedId) || null
  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <h2 className="text-lg font-semibold">Data-subject requests</h2>
        <Badge tone="neutral">
          {plural(list?.count ?? 0, 'request')} / {list?.window_days ?? 0} day window
        </Badge>
      </div>
      <p className="mt-1 text-xs text-muted-foreground">
        Deletion is per subject, never per room. The specification calls per-subject
        deletion the hard part and quotes the vendor&apos;s limitation: delete the whole
        project to delete one user&apos;s data.
      </p>

      {(requests?.requests || []).length === 0 ? (
        <EmptyState
          title="No data-subject request yet"
          description="A request appears here once a privacy desk opens one for an address."
        />
      ) : (
        <ul className="mt-4 divide-y divide-border-subtle">
          {(requests?.requests || []).map((row) => (
            <li key={row.id}>
              <button
                type="button"
                onClick={() => onSelect(row)}
                aria-pressed={row.id === selectedId}
                className={`flex min-h-11 w-full flex-wrap items-center justify-between gap-3 px-1 py-2 text-left hover:bg-muted ${
                  row.id === selectedId ? 'bg-accent-soft' : ''
                }`}
              >
                <span className="min-w-0">
                  <span className="block truncate font-mono text-sm text-foreground">
                    {row.subject}
                  </span>
                  <span className="block font-mono text-xs text-muted-foreground">
                    found {row.found} / erased {row.erased} / opened {formatInstant(row.opened_at)}
                  </span>
                </span>
                <Badge tone={row.state === 'partial' ? 'warning' : 'neutral'}>
                  {dsarLabel(row.state)}
                </Badge>
              </button>
            </li>
          ))}
        </ul>
      )}

      {selected ? (
        <div className="mt-4 border-t border-border-subtle pt-4">
          <p className="text-sm text-foreground">
            {selected.subject} - {dsarLabel(selected.state)}
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            Found in {Object.keys(selected.found_by_collection || {}).join(', ') || 'nothing'}.
            Deadline {formatInstant(selected.deadline_at)}.
          </p>
          <div className="mt-3">
            <Button icon="trash" onClick={() => onFulfil(selected.id)} disabled={pending}>
              {pending ? 'Erasing...' : 'Erase this subject'}
            </Button>
          </div>
          <div className="mt-4">
            <ResidueList residue={selected.residue} />
          </div>
        </div>
      ) : null}

      {onOpen ? (
        <div className="mt-4 border-t border-border-subtle pt-4">
          <Button icon="plus" onClick={onOpen} disabled={pending}>
            {pending ? 'Opening...' : 'Open a request'}
          </Button>
        </div>
      ) : null}
    </Card>
  )
}

function dsarLabel(state) {
  const found = DSAR_STATES.find((entry) => entry.value === state)
  return found ? found.label : state
}

/** Every judgement call this workflow made, with what it rejected. */
function RecordedDecisions({ decisions }) {
  if (!decisions?.length) return null
  return (
    <Card>
      <h2 className="text-lg font-semibold">Recorded decisions</h2>
      <p className="mt-1 text-xs text-muted-foreground">
        The specification asks an implementer to record each derivation rather than assume
        it. These are the questions the research left open and what this build chose.
      </p>
      <ul className="mt-4 space-y-3">
        {decisions.map((decision) => (
          <li key={decision.id} className="rounded-sm border border-border-subtle p-3">
            <p className="font-mono text-xs text-muted-foreground">{decision.id}</p>
            <p className="text-sm text-foreground">{decision.question}</p>
            <p className="mt-1 text-xs text-muted-foreground">
              Chose <span className="font-mono text-foreground">{decision.chosen}</span>.{' '}
              {decision.rejected_because}
            </p>
          </li>
        ))}
      </ul>
    </Card>
  )
}

export function PrivacyControlsPage() {
  const [roomId, setRoomId] = useState('')
  const [role, setRole] = useState('')
  const [region, setRegion] = useState('')
  const [subject, setSubject] = useState('')
  const [dsarSubject, setDsarSubject] = useState('')
  const [signal, setSignal] = useState('')
  const [outcome, setOutcome] = useState(null)
  const [busy, setBusy] = useState(false)
  const [selectedConsentId, setSelectedConsentId] = useState(null)
  const [selectedRequestId, setSelectedRequestId] = useState(null)

  const board = useAsync(() => privacyApi.summary(roomId || undefined), [roomId])
  const vocabulary = useAsync(() => privacyApi.vocabulary(), [])
  const residency = useAsync(() => privacyApi.residency(roomId || undefined), [roomId])
  const policy = useAsync(() => privacyApi.retentionPolicy(roomId || undefined), [roomId])
  const schedule = useAsync(() => privacyApi.retentionSchedule(roomId || undefined), [roomId])
  const consent = useAsync(
    () => privacyApi.consent({ room_id: roomId || undefined, region: region || undefined }),
    [roomId, region],
  )
  const requests = useAsync(() => privacyApi.dsarRequests(roomId || undefined), [roomId])
  const decisions = useAsync(() => privacyApi.decisions(), [])

  const served = vocabulary.data || {}
  const regions = (served.regions || []).map((row) => ({
    value: row.id,
    label: `${row.label} (${row.jurisdiction})`,
  }))
  const roles = (served.roles || []).map((row) => ({
    value: row.id,
    label: `${row.label}${row.may_change ? ' - may make a blocking change' : ''}`,
  }))
  const signals = [
    { value: '', label: 'no signal sent (the gate denies)' },
    { value: served.grant_word || 'granted', label: `${served.grant_word || 'granted'} (explicit)` },
    { value: 'dnt', label: 'dnt (not a consent signal)' },
  ]

  /** Refresh every read this page holds, so a write and its board never disagree. */
  function refreshAll() {
    board.refetch()
    residency.refetch()
    policy.refetch()
    schedule.refetch()
    consent.refetch()
    requests.refetch()
  }

  /**
   * Run one write, report what happened, and refresh the reads.
   *
   * A failure is reported in the same shape as a success, with the server's own words.
   * The one message this page cannot swallow is an administrator refusal, because it is
   * the control rather than a fault.
   */
  async function act(title, call) {
    setBusy(true)
    try {
      const payload = await call()
      setOutcome({ status: 'ok', title, detail: describeResult(payload) })
      refreshAll()
    } catch (error) {
      setOutcome({
        status: 'error',
        title: `${title} was refused`,
        detail: error?.message || String(error),
      })
    } finally {
      setBusy(false)
    }
  }

  if (board.loading && !board.data) return <Spinner label="Loading privacy controls" />
  if (board.error) return <ErrorNote error={board.error} onRetry={board.refetch} />

  const summary = board.data || {}
  const residencyPayload = residency.data || {}
  const consentPayload = consent.data || {}
  const selectedRecord = (consentPayload.recorded || []).find(
    (row) => row.id === selectedConsentId,
  )

  return (
    <div className="space-y-6">
      <header>
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-semibold">Privacy controls</h1>
          <Badge tone={summary.consent_required ? 'insert' : 'neutral'}>
            {summary.consent_required
              ? 'consent gate enforced here'
              : 'consent gate not enforced here'}
          </Badge>
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          Residency, hard retention limits, a consent gate that fails closed, and a
          per-subject erasure path. Screen text is {summary.screen_text}.
        </p>
      </header>

      <Outcome outcome={outcome} onDismiss={() => setOutcome(null)} />

      <Card>
        <h2 className="text-lg font-semibold">What this page is pointed at</h2>
        <div className="mt-3 grid gap-4 lg:grid-cols-3">
          <TextField
            id="wf085-room"
            label="Room id"
            hint="Leave empty to read every room."
            value={roomId}
            onChange={setRoomId}
            placeholder="room_a"
          />
          <SelectField
            id="wf085-role"
            label="Role presented"
            hint="The blocking changes are administrator-only. The repository's own role tier is used; none is invented."
            value={role}
            onChange={setRole}
            options={[{ value: '', label: 'no role' }, ...roles]}
          />
          <SelectField
            id="wf085-region"
            label="Region for the gate"
            hint="Evaluating this region writes nothing."
            value={region}
            onChange={setRegion}
            options={[{ value: '', label: 'no region' }, ...regions]}
          />
        </div>
      </Card>

      {residency.loading && !residency.data ? (
        <Spinner label="Loading the residency record" />
      ) : residency.error ? (
        <ErrorNote error={residency.error} onRetry={residency.refetch} />
      ) : (
        <ResidencyCard
          residency={residencyPayload}
          pending={busy}
          onPin={() =>
            act('Moving the residency region', () =>
              privacyApi.setResidency(
                { region: region || 'eu-west', transfer_mechanism: 'standard_contractual_clauses' },
                { roomId: roomId || undefined, role: role || undefined, actor: 'privacy-desk' },
              ),
            )
          }
        />
      )}

      {policy.error ? (
        <ErrorNote error={policy.error} onRetry={policy.refetch} />
      ) : (
        <RetentionCard
          policy={policy.data}
          schedule={schedule.data}
          pending={busy}
          onSetWindow={() =>
            act('Shortening the recording window', () =>
              privacyApi.setRetentionPolicy(
                { session_recording: 14 },
                { roomId: roomId || undefined, role: role || undefined, actor: 'privacy-desk' },
              ),
            )
          }
          onRun={() =>
            act('Running retention', () =>
              privacyApi.runRetention({
                roomId: roomId || undefined,
                role: role || undefined,
                actor: 'privacy-desk',
              }),
            )
          }
        />
      )}

      {consent.error ? (
        <ErrorNote error={consent.error} onRetry={consent.refetch} />
      ) : (
        <>
          <ConsentCard
            report={consentPayload}
            evaluation={consentPayload.evaluation}
            selectedRecord={selectedRecord}
            onSelect={(row) => setSelectedConsentId(row.id)}
          />

          <Card>
            <h2 className="text-lg font-semibold">Record a consent signal</h2>
            <p className="mt-1 text-xs text-muted-foreground">
              Not administrator-only: the specification says consent is captured at the
              page, per visitor and per region.
            </p>
            <div className="mt-3 grid gap-4 lg:grid-cols-2">
              <TextField
                id="wf085-subject"
                label="Visitor address"
                hint="Lower-cased before it is stored, so one address is one subject."
                value={subject}
                onChange={setSubject}
                placeholder="buyer@northwind.example"
              />
              <SelectField
                id="wf085-signal"
                label="Signal"
                hint="Only the explicit grant word allows tracking."
                value={signal}
                onChange={setSignal}
                options={signals}
                mono={false}
              />
            </div>
            <div className="mt-4 flex flex-wrap gap-2">
              <Button
                icon="plus"
                disabled={busy}
                onClick={() =>
                  act('Recording the consent signal', () =>
                    privacyApi.recordConsent(
                      {
                        region: region || 'eu-west',
                        subject,
                        signal: signal || null,
                      },
                      { roomId: roomId || undefined },
                    ),
                  )
                }
              >
                Record signal
              </Button>
              <Button
                icon="close"
                disabled={busy}
                onClick={() =>
                  act('Recording a global privacy control opt-out', () =>
                    privacyApi.recordConsent(
                      { region: region || 'eu-west', subject, gpc: '1' },
                      { roomId: roomId || undefined },
                    ),
                  )
                }
              >
                Record opt-out
              </Button>
            </div>
            {consentPayload.evaluation ? (
              <p className="mt-4 font-mono text-xs text-muted-foreground">
                {gateOutcome(consentPayload.evaluation.outcome).label} /{' '}
                {consentPayload.evaluation.reason}
              </p>
            ) : null}
          </Card>
        </>
      )}

      {requests.error ? (
        <ErrorNote error={requests.error} onRetry={requests.refetch} />
      ) : (
        <>
          <DsarCard
            list={{ count: (requests.data?.requests || []).length, window_days: served.dsar_window_days }}
            requests={requests.data}
            selectedId={selectedRequestId}
            pending={busy}
            onSelect={(row) => setSelectedRequestId(row.id)}
            onOpen={() =>
              act('Opening the data-subject request', () =>
                privacyApi.openDsar(
                  { subject: dsarSubject, reason: 'Opened from the privacy controls page.' },
                  { roomId: roomId || undefined, role: role || undefined, actor: 'privacy-desk' },
                ),
              )
            }
            onFulfil={(id) =>
              act('Erasing the subject', () =>
                privacyApi.fulfilDsar(id, { role: role || undefined, actor: 'privacy-desk' }),
              )
            }
          />

          <Card>
            <h2 className="text-base font-semibold">Open a request for one subject</h2>
            <div className="mt-3 grid gap-4 lg:grid-cols-2">
              <TextField
                id="wf085-dsar-subject"
                label="Data subject address"
                hint="Discovery matches on the address the research names."
                value={dsarSubject}
                onChange={setDsarSubject}
                placeholder="buyer@northwind.example"
              />
            </div>
            <p className="mt-3 text-xs text-muted-foreground">
              {plural(requests.data?.count ?? 0, 'request')} on record for this room.
              Administrator only.
            </p>
          </Card>
        </>
      )}

      {decisions.data ? <RecordedDecisions decisions={decisions.data.decisions} /> : null}

      <Card>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <h2 className="text-base font-semibold">What this page does not claim</h2>
          <Icon name="audit" />
        </div>
        <p className="mt-2 text-sm text-muted-foreground">
          No certification is rendered anywhere on this page. The vendor&apos;s
          certifications are a claim about a vendor, and they appear above only as
          unverified claims with the subject they are about.
        </p>
        <p className="mt-2 text-sm text-muted-foreground">
          No fixed enforcement date is applied. The gate reads a configured list of
          jurisdictions rather than a vendor&apos;s deadline.
        </p>
      </Card>
    </div>
  )
}

/** One sentence per write, so the status line says what happened rather than that
 * something did. */
function describeResult(payload) {
  if (!payload) return 'Done.'
  if (payload.relocation) {
    return `Scanned ${payload.relocation.records_scanned} record(s) and re-stamped ${payload.relocation.records_moved} to ${payload.residency_region}.`
  }
  if (payload.erased !== undefined && payload.residue) {
    return `Erased ${payload.erased} record(s). ${payload.residue.audit_rows} audit row(s) still name the subject, so the request reads ${payload.state}.`
  }
  if (payload.erased !== undefined) {
    return `Erased ${payload.erased} record(s).`
  }
  if (payload.policy) {
    const strict = (payload.policy || []).find((row) => row.class === 'session_recording')
    return `The recording window is now ${strict?.configured_days} days against a ${strict?.ceiling_days}-day ceiling.`
  }
  if (payload.found !== undefined) {
    return `Found ${payload.found} record(s) for ${payload.subject}.`
  }
  if (payload.state) {
    return `The gate answered ${payload.outcome} and stored the signal as ${payload.state}.`
  }
  return 'Done.'
}

export default {
  id: 'wf-085-meet-gdpr-ccpa-residency-retention-dsar',
  label: 'Privacy controls',
  // `audit` is the shared glyph that reads closest to a governance board, and it is a
  // name that already exists in the shared PATHS map. `components/ui.jsx` is not edited,
  // and no glyph is hand-rolled for an interface icon.
  icon: 'audit',
  order: 850,
  Component: PrivacyControlsPage,
}
