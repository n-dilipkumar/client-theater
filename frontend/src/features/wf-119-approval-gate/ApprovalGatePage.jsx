import { useState } from 'react'

import {
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

import {
  OUTCOMES,
  contactLabel,
  extrasLabel,
  gateApi,
  gatePayload,
  holdSummary,
  pauseEnvelope,
  remainingLabel,
  resumeBody,
  windowLabel,
} from './api'
import { Notice, StateBadge, Toggle, UrlRow, ValueRow } from './primitives'

const EMPTY_GATE_FORM = {
  key: '',
  name: '',
  workflowId: 'wf-demo-proposal-approval',
  actionKey: 'pause_for_approval',
  pauseExecution: true,
  approvedBranch: 'branch_proposal_approved',
  refusedBranch: 'branch_proposal_refused',
}

const EMPTY_PAUSE_FORM = {
  gateKey: '',
  contactId: 'contact_demo',
  locationId: 'loc_northwind',
  workflowId: 'wf-demo-proposal-approval',
  metaKey: 'proposal-approval',
  version: '1',
}

function Section({ title, description, children }) {
  return (
    <Card>
      <h2 className="text-lg font-semibold text-foreground">{title}</h2>
      {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
      <div className="mt-4">{children}</div>
    </Card>
  )
}

function GateList({ gates }) {
  if (!gates.length) {
    return (
      <EmptyState
        title="No approval gate yet"
        description="Create a pause node: the workflow holds a contact at it until a person approves or refuses."
      />
    )
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[760px] border-collapse text-left text-sm">
        <caption className="sr-only">
          Every configured pause node, with the pause toggle and the two resume URLs.
        </caption>
        <thead>
          <tr className="border-b border-border-subtle text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
            <th className="px-2 py-2 font-medium">Gate</th>
            <th className="px-2 py-2 font-medium">Pause</th>
            <th className="px-2 py-2 font-medium">Window</th>
            <th className="px-2 py-2 font-medium">Branches</th>
            <th className="px-2 py-2 font-medium">Action URL</th>
          </tr>
        </thead>
        <tbody>
          {gates.map((gate) => (
            <tr key={gate.key} className="border-b border-border-subtle align-top">
              <td className="px-2 py-3">
                <p className="font-medium text-foreground">{gate.name || gate.key}</p>
                <p className="font-mono text-xs text-muted-foreground">{gate.key}</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  Workflow {gate.workflow_id} at {gate.action_key}
                </p>
              </td>
              <td className="px-2 py-3">
                <span className={gate.pause_execution ? 'text-accent' : 'text-muted-foreground'}>
                  {gate.pause_execution ? 'Pause on' : 'Pause off'}
                </span>
              </td>
              <td className="px-2 py-3 font-mono text-xs text-muted-foreground">
                {windowLabel(gate.hold_seconds)}
              </td>
              <td className="px-2 py-3">
                <span className="font-mono text-xs text-muted-foreground">
                  {gate.branches.approved} / {gate.branches.refused}
                </span>
              </td>
              <td className="max-w-[24rem] px-2 py-3">
                <UrlRow label="Action" url={gate.action_url} />
                <UrlRow label="Success execution" url={gate.resume_urls.approved} />
                <UrlRow label="Failed execution" url={gate.resume_urls.refused} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function CreateGateForm({ onCreate, busy }) {
  const [form, setForm] = useState(EMPTY_GATE_FORM)
  const update = (field) => (value) => setForm((previous) => ({ ...previous, [field]: value }))

  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault()
        onCreate(form)
      }}
    >
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <Field label="Gate key" hint="Lowercase letters, digits, dashes" id="gate-key">
          <input
            id="gate-key"
            className={`${inputClass} font-mono`}
            value={form.key}
            onChange={(event) => update('key')(event.target.value)}
          />
        </Field>
        <Field label="Name" hint="What a reviewer reads" id="gate-name">
          <input
            id="gate-name"
            className={inputClass}
            value={form.name}
            onChange={(event) => update('name')(event.target.value)}
          />
        </Field>
        <Field label="Workflow id" hint="The automation the pause sits in" id="gate-workflow">
          <input
            id="gate-workflow"
            className={`${inputClass} font-mono`}
            value={form.workflowId}
            onChange={(event) => update('workflowId')(event.target.value)}
          />
        </Field>
        <Field label="Action key" hint="The pause node in that workflow" id="gate-action">
          <input
            id="gate-action"
            className={`${inputClass} font-mono`}
            value={form.actionKey}
            onChange={(event) => update('actionKey')(event.target.value)}
          />
        </Field>
        <Field label="Approved branch" hint="Where an approval resumes" id="gate-approved">
          <input
            id="gate-approved"
            className={`${inputClass} font-mono`}
            value={form.approvedBranch}
            onChange={(event) => update('approvedBranch')(event.target.value)}
          />
        </Field>
        <Field label="Refused branch" hint="Where a refusal resumes" id="gate-refused">
          <input
            id="gate-refused"
            className={`${inputClass} font-mono`}
            value={form.refusedBranch}
            onChange={(event) => update('refusedBranch')(event.target.value)}
          />
        </Field>
      </div>
      <Toggle
        id="gate-pause"
        label="Pause execution"
        hint="The contact is held only while this is on."
        checked={form.pauseExecution}
        onChange={update('pauseExecution')}
        disabled={busy}
      />
      <Button variant="primary" type="submit" disabled={busy || !form.key}>
        Create gate
      </Button>
    </form>
  )
}

function PauseForm({ gates, onPause, busy }) {
  const [form, setForm] = useState(EMPTY_PAUSE_FORM)
  const update = (field) => (value) => setForm((previous) => ({ ...previous, [field]: value }))
  const target = form.gateKey || (gates[0] && gates[0].key) || ''

  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault()
        onPause(target, pauseEnvelope(form))
      }}
    >
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <Field label="Gate" hint="The pause node the action posts to" id="pause-gate">
          <select
            id="pause-gate"
            className={`${inputClass} appearance-none`}
            value={target}
            onChange={(event) => update('gateKey')(event.target.value)}
          >
            {gates.map((gate) => (
              <option key={gate.key} value={gate.key}>
                {gate.key}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Contact id" hint="extras.contactId" id="pause-contact">
          <input
            id="pause-contact"
            className={`${inputClass} font-mono`}
            value={form.contactId}
            onChange={(event) => update('contactId')(event.target.value)}
          />
        </Field>
        <Field label="Location id" hint="extras.locationId" id="pause-location">
          <input
            id="pause-location"
            className={`${inputClass} font-mono`}
            value={form.locationId}
            onChange={(event) => update('locationId')(event.target.value)}
          />
        </Field>
        <Field label="Workflow id" hint="extras.workflowId" id="pause-workflow">
          <input
            id="pause-workflow"
            className={`${inputClass} font-mono`}
            value={form.workflowId}
            onChange={(event) => update('workflowId')(event.target.value)}
          />
        </Field>
        <Field label="Meta key" hint="meta.key" id="pause-meta">
          <input
            id="pause-meta"
            className={`${inputClass} font-mono`}
            value={form.metaKey}
            onChange={(event) => update('metaKey')(event.target.value)}
          />
        </Field>
        <Field label="Version" hint="meta.version" id="pause-version">
          <input
            id="pause-version"
            type="number"
            inputMode="numeric"
            min="1"
            className={`${inputClass} font-mono`}
            value={form.version}
            onChange={(event) => update('version')(event.target.value)}
          />
        </Field>
      </div>
      <Button variant="primary" type="submit" disabled={busy || !target}>
        Hold this contact
      </Button>
    </form>
  )
}

function HoldList({ holds, gates, onDecide, busy }) {
  if (!holds.length) {
    return (
      <EmptyState
        title="No contact is held"
        description="Create a gate, then hold a contact at it. Each hold appears here with Approve and Refuse controls."
      />
    )
  }
  const byKey = Object.fromEntries(gates.map((gate) => [gate.key, gate]))

  return (
    <ul className="space-y-3">
      {holds.map((hold) => (
        <li key={hold.id} className="rounded-sm border border-border-subtle p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0 space-y-1">
              <div className="flex flex-wrap items-center gap-3">
                <span className="font-medium text-foreground">{contactLabel(hold.extras)}</span>
                <StateBadge hold={hold} />
              </div>
              <p className="font-mono text-xs text-muted-foreground">{extrasLabel(hold.extras)}</p>
              <p className="text-sm text-muted-foreground">{holdSummary(hold)}</p>
              <p className="text-xs text-muted-foreground">
                Gate {hold.gate_key} · {remainingLabel(hold)}
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              {OUTCOMES.map((option) => (
                <Button
                  key={option.value}
                  variant="secondary"
                  disabled={busy || !hold.can_resume}
                  aria-label={`${option.label}: ${option.description} for ${contactLabel(
                    hold.extras,
                  )}`}
                  onClick={() => onDecide(hold, option.value, byKey[hold.gate_key])}
                >
                  {option.label}
                </Button>
              ))}
            </div>
          </div>
        </li>
      ))}
    </ul>
  )
}

function DecisionsPanel({ decisions, notBuilt, resume }) {
  return (
    <div className="space-y-4">
      <Notice tone="info" title="Resume mode: async">
        The branch is sent to the resume webhook and stored on the held record, so the decision
        survives a restart. The synchronous alternative, returning the branch from the pause
        response, was rejected. Jev audit {resume.audit_id}, chosen at confidence{' '}
        {resume.confidence}.
      </Notice>
      <ul className="space-y-3">
        {decisions.map((entry) => (
          <li key={entry.id} className="rounded-sm border border-border-subtle p-4">
            <p className="font-mono text-xs text-accent">{entry.id}</p>
            <p className="mt-1 text-sm text-foreground">{entry.question}</p>
            <div className="mt-2 grid gap-3 sm:grid-cols-2">
              <ValueRow label="Chosen" value={entry.choice} />
              <ValueRow label="Rejected" value={entry.rejected} />
            </div>
            <p className="mt-2 font-mono text-xs text-muted-foreground">
              {entry.audit_id ? `${entry.verdict} · ${entry.audit_id}` : entry.verdict}
            </p>
          </li>
        ))}
      </ul>
      <ValueRow
        label="Not built, and why"
        value={notBuilt.map((entry) => `${entry.item}: ${entry.why}`).join(' ')}
      />
    </div>
  )
}

function Page() {
  const { data, loading, error, refetch } = useAsync(
    () =>
      Promise.all([
        gateApi.summary(),
        gateApi.gates(),
        gateApi.holds(),
        gateApi.decisions(),
        gateApi.vocabulary(),
      ]).then(([summary, gates, holds, decisions, vocabulary]) => ({
        summary,
        gates: gates.gates,
        holds: holds.holds,
        decisions: decisions.decisions,
        notBuilt: decisions.not_built,
        resume: decisions.resume_mode,
        vocabulary,
      })),
    [],
  )
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState(null)

  if (loading) return <Spinner label="Loading approval gates" />
  if (error) return <ErrorNote error={error} onRetry={refetch} />

  async function run(action, message) {
    setBusy(true)
    setNote(null)
    try {
      await action()
      setNote({ tone: 'info', text: message })
      refetch()
    } catch (failure) {
      setNote({ tone: 'destructive', text: failure.message })
    } finally {
      setBusy(false)
    }
  }

  const { summary, gates, holds, decisions, notBuilt, resume, vocabulary } = data

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-foreground">Approval gate</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          An automation holds a contact at a pause node until a person approves or refuses it in an
          external system. The hold is a record, so it survives a restart, and the resume webhook
          resumes the automation on the branch that decision selected.
        </p>
      </div>

      {note && <Notice tone={note.tone}>{note.text}</Notice>}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Gates" value={summary.gates} hint={`${summary.pause_enabled} with pause on`} />
        <StatCard label="Held" value={summary.by_state.held || 0} hint="Waiting on a human" />
        <StatCard
          label="Decided"
          value={summary.by_state.resumed || 0}
          hint={`${summary.resumes} resume records`}
        />
        <StatCard
          label="Expired"
          value={summary.by_state.expired || 0}
          hint={`${summary.expired_lazily} lapsed with no decision`}
        />
      </div>

      <Section
        title="Pause nodes"
        description="Each gate holds a contact and publishes the two resume webhook URLs: Success Execution and Failed Execution."
      >
        <GateList gates={gates} />
      </Section>

      <div className="grid gap-6 lg:grid-cols-2">
        <Section
          title="Held contacts"
          description="A retried resume with the same decision is an idempotent replay. A resume that changes a recorded decision is refused."
        >
          <div className="mb-4">
            <Button
              variant="secondary"
              disabled={busy}
              onClick={() => run(() => gateApi.sweep(), 'Lapsed holds were expired.')}
            >
              Expire lapsed holds
            </Button>
          </div>
          <HoldList
            holds={holds}
            gates={gates}
            busy={busy}
            onDecide={(hold, outcome, gate) =>
              run(
                () => gateApi.resume(hold.id, resumeBody(outcome, gate)),
                `${contactLabel(hold.extras)} was recorded as ${outcome}.`,
              )
            }
          />
        </Section>

        <Section
          title="Configure a gate"
          description={`The hold window is ${windowLabel(vocabulary.hold_window.default_seconds)} by default, derived from a weekend plus one working day.`}
        >
          <CreateGateForm
            busy={busy}
            onCreate={(form) =>
              run(() => gateApi.createGate(gatePayload(form)), `Gate ${form.key} was created.`)
            }
          />
        </Section>
      </div>

      <Section
        title="Hold a contact"
        description="This is the documented action envelope the pause node posts: data, extras and meta."
      >
        <PauseForm
          gates={gates}
          busy={busy}
          onPause={(key, envelope) =>
            run(() => gateApi.pause(key, envelope), `The contact is held at ${key}.`)
          }
        />
      </Section>

      <Section
        title="Decisions"
        description="Every judgement this build recorded, with the alternative rejected and the Jev audit where one exists."
      >
        <DecisionsPanel decisions={decisions} notBuilt={notBuilt} resume={resume} />
      </Section>
    </div>
  )
}

export default Page
