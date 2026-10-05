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
  CONSENT_OUTCOMES,
  MASKING_MODES,
  consentApi,
  consentOutcome,
  formatInstant,
  ingestState,
  maskingMode,
  plural,
} from './api'
import { AxisMatrix, LabelBudget, RefusalNote, RetentionTable, VisitVerdict } from './primitives'

/**
 * WF-083: record buyer sessions behind a consent gate.
 *
 * The page has five jobs, in this order, and the order is the design.
 *
 * **Say what the gate decided, axis by axis.** The specification gives a two-axis call,
 * `window.clarity('consentv2', {ad_Storage, analytics_Storage})`, and says the axes are
 * independent. So the first thing on the page is both axes as separate words. A single
 * "consent granted" badge would hide the partial grant, which is the case a reviewer most
 * needs to see and the one a one-flag implementation gets wrong.
 *
 * **Say what a refusal means, not just that it happened.** A visit that was not recorded
 * carries a reason and its consequence: a blocked visitor produced no session row at all,
 * a partial grant produced no replay. A page that showed only "not recorded" would leave a
 * reader unable to tell a working gate from a broken one.
 *
 * **Say what masking is doing before the frame is stored.** "Is masked data uploaded to
 * Clarity? No." So the masking mode is on the page in words, and the default is named as
 * the default rather than presented as a choice somebody made.
 *
 * **Show both retention windows side by side.** Thirty days, or nine months for a
 * favourite. One number would hide that a favourite outlives the ordinary window.
 *
 * **Say what this workflow refuses to do.** A single recording cannot be deleted or
 * downloaded, and there is no authentication path. Both are sourced limits, so both are
 * rendered as limits with their evidence, not as gaps.
 *
 * No badge on this page claims compliance, a certification, or that a session was
 * collected. A gate that failed closed says so.
 */

/** The status line after a write. One shape, so every outcome reads the same way. */
function Outcome({ outcome, onDismiss }) {
  if (!outcome) return null
  const failed = outcome.status === 'error'
  return (
    <div
      role="status"
      className={`flex flex-wrap items-center justify-between gap-3 rounded-sm border p-4 ${
        failed ? 'border-destructive/40 bg-destructive/10' : 'border-accent/30 bg-accent/10'
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

function SectionHeading({ title, hint }) {
  return (
    <div className="mb-3">
      <h2 className="font-display text-[15px] font-semibold text-foreground">{title}</h2>
      {hint ? <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p> : null}
    </div>
  )
}

function ConsentRecorder({ roomId, onOutcome }) {
  const [ad, setAd] = useState('granted')
  const [analytics, setAnalytics] = useState('granted')
  const [visitor, setVisitor] = useState('visitor_demo')
  const [last, setLast] = useState(null)
  const [busy, setBusy] = useState(false)

  const axes = { ad_storage: ad, analytics_storage: analytics }

  async function record() {
    setBusy(true)
    try {
      const result = await consentApi.recordConsent(
        { ad_Storage: ad, analytics_Storage: analytics, visitor_id: visitor },
        { roomId },
      )
      setLast(result)
      onOutcome({
        status: 'ok',
        title: consentOutcome(result.outcome).label,
        detail: `${plural(result.destroyed_sessions?.length || 0, 'stored session')} destroyed by this call.`,
      })
    } catch (error) {
      onOutcome({ status: 'error', title: 'The consent call was refused', detail: error.message })
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <SectionHeading
        title="Record a consent call"
        hint="The vendor's own call: window.clarity('consentv2', {ad_Storage, analytics_Storage})."
      />
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Ad storage" hint="granted or denied.">
          <select className={inputClass} value={ad} onChange={(event) => setAd(event.target.value)}>
            <option value="granted">granted</option>
            <option value="denied">denied</option>
          </select>
        </Field>
        <Field label="Analytics storage" hint="The axes are independent of each other.">
          <select
            className={inputClass}
            value={analytics}
            onChange={(event) => setAnalytics(event.target.value)}
          >
            <option value="granted">granted</option>
            <option value="denied">denied</option>
          </select>
        </Field>
      </div>
      <div className="mt-3">
        <Field label="Visitor" hint="A denial hard-deletes this visitor's stored sessions.">
          <input
            className={inputClass}
            value={visitor}
            onChange={(event) => setVisitor(event.target.value)}
          />
        </Field>
      </div>
      <div className="mt-4">
        <Button variant="primary" icon="plus" onClick={record} disabled={busy}>
          Record the call
        </Button>
      </div>
      <div className="mt-4">
        <AxisMatrix axes={axes} granted={Object.entries(axes).filter(([, v]) => v === 'granted').map(([k]) => k)} />
      </div>
      {last ? (
        <div className="mt-4 space-y-3">
          <VisitVerdict visit={last} />
          {last.revoke_call ? (
            <RefusalNote
              title="Revoke call required"
              detail="A denial is destructive, so the integration must make the documented revoke call."
              evidence={`window.clarity('${last.revoke_call.api}', ${last.revoke_call.value})`}
              remediation="The stored sessions were deleted by this workflow. The browser cookie is the integration's to clear."
            />
          ) : null}
        </div>
      ) : null}
    </Card>
  )
}

function BlocklistPanel({ roomId, role, actor, onOutcome, blocks, onChanged }) {
  const [cidr, setCidr] = useState('')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  async function add() {
    setBusy(true)
    setError(null)
    try {
      await consentApi.blockIp(cidr, { roomId, role, actor })
      setCidr('')
      onOutcome({ status: 'ok', title: 'Range blocked', detail: `${cidr} is excluded from recordings.` })
      onChanged()
    } catch (failure) {
      setError(failure.message)
      onOutcome({ status: 'error', title: 'The range was refused', detail: failure.message })
    } finally {
      setBusy(false)
    }
  }

  async function remove(id) {
    try {
      await consentApi.unblockIp(id, { roomId, role, actor })
      onOutcome({ status: 'ok', title: 'Range removed', detail: 'The exclusion no longer applies.' })
      onChanged()
    } catch (failure) {
      onOutcome({ status: 'error', title: 'The range was not removed', detail: failure.message })
    }
  }

  return (
    <Card>
      <SectionHeading
        title="IP blocking"
        hint="Evaluated at ingest, not at view time. A blocked visitor produces no session at all."
      />
      <Field
        label="IPv4 address or range"
        hint="IPv4 only. An IPv6 range is refused, because the vendor supports IPv4 only."
        id="wf083-cidr"
      >
        <input
          id="wf083-cidr"
          className={inputClass}
          placeholder="10.0.0.0/8"
          value={cidr}
          onChange={(event) => setCidr(event.target.value)}
        />
      </Field>
      {error ? <p className="mt-2 text-xs text-destructive">{error}</p> : null}
      <div className="mt-3">
        <Button icon="plus" onClick={add} disabled={busy || !cidr.trim()}>
          Block the range
        </Button>
      </div>
      <p className="mt-3 text-xs text-muted-foreground">
        Excluded from recordings and heatmaps. Changes take about 15 minutes to come into
        effect.
      </p>
      <ul className="mt-3 space-y-2">
        {(blocks || []).map((block) => (
          <li
            key={block.id}
            className="flex min-h-11 items-center justify-between gap-3 rounded-sm border border-border-subtle px-3"
          >
            <span className="font-mono text-xs text-foreground">{block.cidr}</span>
            <Button icon="trash" onClick={() => remove(block.id)}>
              Remove
            </Button>
          </li>
        ))}
        {!(blocks || []).length ? (
          <li className="text-sm text-muted-foreground">No ranges are blocked.</li>
        ) : null}
      </ul>
    </Card>
  )
}

function MaskingPanel({ project, roomId, role, actor, onOutcome, onChanged }) {
  const [mode, setMode] = useState(project?.masking_mode || 'suppress_all')
  const [busy, setBusy] = useState(false)
  const active = maskingMode(project?.masking_mode)

  async function save() {
    setBusy(true)
    try {
      await consentApi.setMasking(mode, [], { roomId, role, actor })
      onOutcome({
        status: 'ok',
        title: 'Masking mode set',
        detail: 'Frames recorded from now on use this mode. Masking runs before the write.',
      })
      onChanged()
    } catch (failure) {
      onOutcome({ status: 'error', title: 'The masking mode was refused', detail: failure.message })
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <SectionHeading
        title="Masking"
        hint="Masked data is not uploaded, so masking runs before the frame is written."
      />
      <p className="text-sm text-foreground">
        In force: <span className="font-medium">{active.label}</span>. {active.detail}
      </p>
      <div className="mt-3">
        <Field label="Mode" hint="Changing this decides what leaves the room.">
          <select className={inputClass} value={mode} onChange={(event) => setMode(event.target.value)}>
            {MASKING_MODES.map((entry) => (
              <option key={entry.value} value={entry.value}>
                {entry.label}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <div className="mt-4">
        <Button icon="audit" onClick={save} disabled={busy}>
          Set the masking mode
        </Button>
      </div>
      <p className="mt-3 text-xs text-muted-foreground">
        The default is total suppression, so a project that configures nothing transmits no
        frame content at all.
      </p>
    </Card>
  )
}

function RecordingsPanel({ recordings, roomId, onOutcome, onChanged, cap }) {
  const [labelsFor, setLabelsFor] = useState(null)
  const [labelText, setLabelText] = useState('')
  const [busy, setBusy] = useState(false)

  async function label(recordingId, labels) {
    setBusy(true)
    try {
      await consentApi.addLabels(recordingId, labels, { roomId })
      setLabelText('')
      onOutcome({ status: 'ok', title: 'Labels saved', detail: `${plural(labels.length, 'label')} added.` })
      onChanged()
    } catch (failure) {
      onOutcome({ status: 'error', title: 'The label was refused', detail: failure.message })
    } finally {
      setBusy(false)
    }
  }

  async function favourite(recording) {
    try {
      await consentApi.markFavourite(recording.id, !recording.favourite, { roomId })
      onOutcome({
        status: 'ok',
        title: recording.favourite ? 'Removed from favourites' : 'Marked a favourite',
        detail: recording.favourite
          ? 'The recording is back on the ordinary retention window.'
          : 'The recording now sits on the nine-month window.',
      })
      onChanged()
    } catch (failure) {
      onOutcome({ status: 'error', title: 'The change was refused', detail: failure.message })
    }
  }

  async function tryDelete(recordingId) {
    try {
      await consentApi.deleteRecording(recordingId)
      onOutcome({ status: 'ok', title: 'Deleted', detail: 'The recording was removed.' })
    } catch (failure) {
      onOutcome({
        status: 'error',
        title: 'A single recording cannot be deleted',
        detail:
          failure.body?.detail ||
          'Deletion is at project granularity. You cannot delete or download specific recordings.',
      })
    }
  }

  if (!recordings.length) {
    return (
      <EmptyState
        title="No recordings yet"
        description="A visit is recorded only after both consent axes are granted and the visitor is outside the blocklist."
      />
    )
  }

  return (
    <Card>
      <SectionHeading
        title={`${plural(recordings.length, 'recording')}`}
        hint="Each frame is masked before it is stored, so no captured content is held here."
      />
      <ul className="space-y-3">
        {recordings.map((recording) => (
          <li key={recording.id} className="rounded-sm border border-border-subtle p-4">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={ingestState(recording.state).tone}>{recording.state}</Badge>
              {recording.favourite ? <Badge tone="update">favourite</Badge> : null}
              <span className="font-mono text-xs text-muted-foreground">{recording.page_path || 'unknown page'}</span>
            </div>
            <dl className="mt-3 grid gap-2 text-xs sm:grid-cols-3">
              <div>
                <dt className="text-muted-foreground">Recorded</dt>
                <dd className="font-mono text-foreground">{formatInstant(recording.recorded_at)}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Masking</dt>
                <dd className="font-mono text-foreground">{recording.masking_mode || 'unknown'}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Identity</dt>
                <dd className="font-mono text-foreground">{recording.identity_kind || 'unknown'}</dd>
              </div>
            </dl>
            <div className="mt-3 flex flex-wrap gap-2">
              <Button icon="plus" onClick={() => favourite(recording)}>
                {recording.favourite ? 'Remove favourite' : 'Mark favourite'}
              </Button>
              <Button
                icon="close"
                onClick={() => {
                  setLabelsFor(labelsFor === recording.id ? null : recording.id)
                  setLabelText('')
                }}
              >
                Add labels
              </Button>
              <Button icon="trash" onClick={() => tryDelete(recording.id)}>
                Delete
              </Button>
            </div>
            {labelsFor === recording.id ? (
              <div className="mt-3">
                <Field label="Labels" hint="Comma separated.">
                  <input
                    className={inputClass}
                    value={labelText}
                    onChange={(event) => setLabelText(event.target.value)}
                  />
                </Field>
                <div className="mt-1">
                  <LabelBudget held={recording.labels} cap={cap} />
                </div>
                <div className="mt-2">
                  <Button
                    variant="primary"
                    icon="plus"
                    disabled={busy || !labelText.trim()}
                    onClick={() =>
                      label(
                        recording.id,
                        labelText
                          .split(',')
                          .map((entry) => entry.trim())
                          .filter(Boolean),
                      )
                    }
                  >
                    Save the labels
                  </Button>
                </div>
              </div>
            ) : null}
            {recording.labels?.length ? (
              <ul className="mt-3 flex flex-wrap gap-2">
                {recording.labels.map((entry) => (
                  <li key={entry}>
                    <Badge>{entry}</Badge>
                  </li>
                ))}
              </ul>
            ) : null}
          </li>
        ))}
      </ul>
    </Card>
  )
}

function DecisionsPanel({ decisions }) {
  if (!decisions?.length) return null
  return (
    <Card>
      <SectionHeading
        title="Recorded decisions"
        hint="Every judgement call this workflow made, with the alternative it rejected."
      />
      <ul className="space-y-4">
        {decisions.map((decision) => (
          <li key={decision.id} className="rounded-sm border border-border-subtle p-4">
            <p className="font-mono text-xs text-accent">{decision.id}</p>
            <p className="mt-1 text-sm font-medium text-foreground">{decision.question}</p>
            <p className="mt-1 text-xs text-muted-foreground">
              Chosen: <span className="font-mono text-foreground">{decision.chosen}</span>
            </p>
            <p className="mt-2 text-xs text-muted-foreground">{decision.rejected_because}</p>
          </li>
        ))}
      </ul>
    </Card>
  )
}

function ConsentGatePage() {
  const [role, setRole] = useState('instance_admin')
  const [actor, setActor] = useState('dana')
  const [outcome, setOutcome] = useState(null)
  const roomId = undefined

  const board = useAsync(
    () =>
      Promise.all([
        consentApi.summary(roomId),
        consentApi.vocabulary(),
        consentApi.project(roomId),
        consentApi.ipBlocks(roomId),
        consentApi.recordings(roomId),
        consentApi.visits(roomId),
        consentApi.retention(roomId),
        consentApi.decisions(),
      ]),
    [],
  )

  if (board.loading) return <Spinner label="Loading the consent gate" />
  if (board.error) return <ErrorNote error={board.error} onRetry={board.refetch} />

  const [summary, vocabulary, project, blockBody, recordBody, visitBody, retention, decisionBody] =
    board.data || []
  const counts = summary?.counts || {}
  const created = summary?.project_created
  const blocks = blockBody?.blocks || []
  const recordings = recordBody?.recordings || []
  const visits = visitBody?.visits || []

  return (
    <div className="space-y-5">
      <header>
        <h1 className="font-display text-xl font-semibold text-foreground">
          Record buyer sessions behind a consent gate
        </h1>
        <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
          A session is recorded only after both consent axes are granted. A denial destroys the
          session rather than skipping it. Masking runs before the write, and the default is total
          suppression.
        </p>
      </header>

      <Outcome outcome={outcome} onDismiss={() => setOutcome(null)} />

      {!created ? (
        <EmptyState
          title="No recording project yet"
          description="Create one to arm the consent gate. A project starts with masking at its documented default and the gate on."
          action={
            <Button
              variant="primary"
              icon="plus"
              onClick={async () => {
                try {
                  await consentApi.createProject({ name: 'Recording project' }, { roomId })
                  setOutcome({
                    status: 'ok',
                    title: 'Project created',
                    detail: 'The consent gate is on and masking is at its default.',
                  })
                  board.refetch()
                } catch (failure) {
                  setOutcome({ status: 'error', title: 'The project was refused', detail: failure.message })
                }
              }}
            >
              Create the project
            </Button>
          }
        />
      ) : null}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Recorded" value={counts.recorded ?? 0} hint="sessions stored" icon="database" />
        <StatCard label="Visits" value={counts.visits ?? 0} hint="including refusals" icon="audit" />
        <StatCard label="Blocked" value={counts.blocked ?? 0} hint="by IP, no session" icon="close" />
        <StatCard
          label="Blocked ranges"
          value={counts.blocked_ranges ?? 0}
          hint="IPv4 only"
          icon="schema"
        />
      </div>

      <Card>
        <SectionHeading
          title="How this room is configured"
          hint="The two axes, the masking default, and the region-scoped enforcement."
        />
        <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <dt className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
              Consent gate
            </dt>
            <dd className="mt-1">
              <Badge tone={project?.[vocabulary?.consent_gate_field] ? 'insert' : 'neutral'}>
                {project?.[vocabulary?.consent_gate_field] ? 'enabled' : 'disabled'}
              </Badge>
            </dd>
          </div>
          <div>
            <dt className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
              Masking mode
            </dt>
            <dd className="mt-1 font-mono text-xs text-foreground">
              {project?.masking_mode || vocabulary?.default_masking_mode}
            </dd>
          </div>
          <div>
            <dt className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
              Enforced regions
            </dt>
            <dd className="mt-1 font-mono text-xs text-foreground">
              {(summary?.enforcement?.regions || []).join(', ') || 'none'}
            </dd>
          </div>
          <div>
            <dt className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
              Enforcement active
            </dt>
            <dd className="mt-1 font-mono text-xs text-foreground">
              {summary?.enforcement?.enforcement_active ? 'yes' : 'no'} from{' '}
              {summary?.enforcement?.enforcement_start}
            </dd>
          </div>
        </dl>
        <p className="mt-3 text-xs text-muted-foreground">
          Region comes from {vocabulary?.geography_source}. No route here reports that this room is
          compliant with anything.
        </p>
      </Card>

      <Card>
        <SectionHeading
          title="Act as"
          hint="IP blocking is an administrator action, so the write routes take a role. This product has no SSO path for this workflow."
        />
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Role" hint={vocabulary?.ip_blocking_role}>
            <select className={inputClass} value={role} onChange={(event) => setRole(event.target.value)}>
              {(vocabulary?.roles || []).map((entry) => (
                <option key={entry.id} value={entry.id}>
                  {entry.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Actor" hint="Recorded on every write.">
            <input className={inputClass} value={actor} onChange={(event) => setActor(event.target.value)} />
          </Field>
        </div>
      </Card>

      <div className="grid gap-5 lg:grid-cols-2">
        <ConsentRecorder roomId={roomId} onOutcome={setOutcome} />
        <div className="space-y-5">
          <BlocklistPanel
            roomId={roomId}
            role={role}
            actor={actor}
            blocks={blocks}
            onOutcome={setOutcome}
            onChanged={board.refetch}
          />
          <MaskingPanel
            project={project}
            roomId={roomId}
            role={role}
            actor={actor}
            onOutcome={setOutcome}
            onChanged={board.refetch}
          />
        </div>
      </div>

      <Card>
        <SectionHeading
          title="What this workflow refuses to do"
          hint="These are sourced limits, not gaps in the build."
        />
        <div className="space-y-3">
          <RefusalNote
            title="A single recording cannot be deleted or downloaded"
            detail={summary?.deletion?.detail}
            evidence={summary?.deletion?.evidence}
            remediation={summary?.deletion?.remediation}
          />
          <RefusalNote
            title="No authentication path"
            detail={`Project membership is ${vocabulary?.authentication}. The specification states the vendor does not support authentication via a corporate directory instance.`}
            evidence="Microsoft Clarity doesn't support authentication via your company's AAD instance."
            remediation="Every response that reports a role carries the membership model beside it."
          />
        </div>
      </Card>

      <div className="grid gap-5 lg:grid-cols-2">
        <div>
          <SectionHeading
            title="Visits the room refused"
            hint="A blocked visitor produced no session. The refusal is kept in the audit log."
          />
          {visits.length ? (
            <div className="space-y-3">
              {visits.slice(0, 6).map((visit) => (
                <VisitVerdict key={visit.id} visit={visit} />
              ))}
            </div>
          ) : (
            <Card>
              <p className="text-sm text-muted-foreground">No visits recorded yet.</p>
            </Card>
          )}
        </div>
        <div>
          <SectionHeading
            title="Retention"
            hint="Both windows. A favourite outlives the ordinary one."
          />
          <Card>
            <RetentionTable
              rows={retention?.rows || []}
              ordinaryDays={retention?.ordinary_days}
              favouriteDays={retention?.favourite_days}
            />
            <div className="mt-4">
              <Button
                icon="trash"
                onClick={async () => {
                  try {
                    const result = await consentApi.runRetention({ roomId, role, actor })
                    setOutcome({
                      status: 'ok',
                      title: 'Retention sweep finished',
                      detail: `${plural(result.removed, 'recording')} aged out.`,
                    })
                    board.refetch()
                  } catch (failure) {
                    setOutcome({ status: 'error', title: 'The sweep was refused', detail: failure.message })
                  }
                }}
              >
                Run the retention sweep
              </Button>
            </div>
          </Card>
        </div>
      </div>

      <RecordingsPanel
        recordings={recordings}
        roomId={roomId}
        onOutcome={setOutcome}
        onChanged={board.refetch}
        cap={vocabulary?.max_labels_per_recording}
      />

      <Card>
        <SectionHeading
          title="The consent answers this workflow records"
          hint="A signal is not a decision, so it is its own answer."
        />
        <ul className="grid gap-3 sm:grid-cols-3">
          {CONSENT_OUTCOMES.map((entry) => (
            <li key={entry.value} className="rounded-sm border border-border-subtle p-4">
              <Badge tone={entry.tone}>{entry.label}</Badge>
              <p className="mt-2 text-xs text-muted-foreground">{entry.meaning}</p>
            </li>
          ))}
        </ul>
      </Card>

      <Card>
        <SectionHeading title="Governance ceiling" hint="A vendor limit, reported rather than enforced." />
        <p className="text-sm text-foreground">
          {summary?.ceiling?.label}. {summary?.ceiling?.count} of {summary?.ceiling?.ceiling}{' '}
          sessions per project per day, {plural(summary?.ceiling?.remaining ?? 0, 'slot')} left.
        </p>
      </Card>

      <DecisionsPanel decisions={decisionBody?.decisions} />

      <p className="flex items-center gap-2 text-xs text-muted-foreground">
        <Icon name="audit" size={14} />
        Every write on this page is audited with the route that served it.
      </p>
    </div>
  )
}

export default {
  id: 'wf-083-record-buyer-sessions-behind-a-consent-gate',
  label: 'Consent-gated recording',
  icon: 'audit',
  order: 150,
  Component: ConsentGatePage,
}
