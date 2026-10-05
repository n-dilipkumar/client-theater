/**
 * Sequential multi-level quote approvals (WF-092).
 *
 * The page has four parts, and the order is the order a seller would work in.
 *
 * 1. The chain. This is the part a person came for: which quotes are waiting, on whom,
 *    and at which priority. Every approval button sits next to the approver it belongs
 *    to, so nobody has to remember which row they are on.
 * 2. The branch configuration. What triggers a chain, and who it goes to.
 * 3. The researched limits. The two caps, the three requirements and the sequential
 *    rule, rendered from the server's own vocabulary so a page cannot quote one cap
 *    while the validator enforces another.
 * 4. The judgement calls, including the three notification channels this build does not
 *    deliver. A page that does not show its own edges overstates itself.
 *
 * Two behaviours are worth naming because they are the researched rules rather than
 * conveniences.
 *
 * A quote that matched no branch comes back `auto_approved`, and the page says so
 * plainly rather than rendering an error. The research is explicit that this is the
 * valve: "if a quote approval step hasn't been added above this action, quotes will be
 * auto-approved."
 *
 * A decision from a lower priority comes back `not_yet_your_priority`, and the page
 * shows that as a notice rather than an error. That refusal is the sequential rule
 * working, and treating it as a fault would teach a seller that the chain is broken.
 */

import { useCallback, useMemo, useState } from 'react'
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
import { approvalApi } from './api'
import {
  BranchCard,
  ChainLadder,
  Notice,
  NotificationList,
  REQUIREMENT_WORD,
  SectionLabel,
  StatusPill,
  Toggle,
} from './primitives'

/** A quote formatted for the amount column. Machine values stay in mono. */
function money(value) {
  const number = Number(value)
  if (Number.isNaN(number)) return 'no amount'
  return number.toLocaleString(undefined, { maximumFractionDigits: 2 })
}

export default function ApprovalChain() {
  const [branchError, setBranchError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [busy, setBusy] = useState(null)

  const vocabulary = useAsync(() => approvalApi.vocabulary(), [])
  const inferences = useAsync(() => approvalApi.inferences(), [])
  const workflow = useAsync(() => approvalApi.workflow(), [])
  const branches = useAsync(() => approvalApi.branches(), [])
  const quotes = useAsync(() => approvalApi.quotes(), [])
  const enrolments = useAsync(() => approvalApi.enrolments(), [])

  const refresh = useCallback(async () => {
    await Promise.all([enrolments.refetch(), branches.refetch(), quotes.refetch(), workflow.refetch()])
  }, [enrolments, branches, quotes, workflow])

  const loading =
    vocabulary.loading ||
    inferences.loading ||
    workflow.loading ||
    branches.loading ||
    quotes.loading ||
    enrolments.loading
  const error =
    vocabulary.error || inferences.error || workflow.error || branches.error || quotes.error || enrolments.error

  if (loading) return <Spinner label="Loading the approval chain" />
  if (error) return <ErrorNote error={error} onRetry={refresh} />

  const caps = vocabulary.data?.chain?.caps || {}
  const states = vocabulary.data?.chain?.states || []
  const chainStates = enrolments.data?.enrolments || []

  const decided = chainStates.filter((row) => row.state === 'approved' || row.state === 'rejected')
  const waiting = chainStates.filter(
    (row) => row.state === 'pending_approval' || row.state === 'in_review'
  )
  const autoApproved = chainStates.filter((row) => row.auto_approved)

  async function decide(enrolmentId, approver, decision) {
    setBusy(`${enrolmentId}:${approver}`)
    setNotice(null)
    try {
      const result = await approvalApi.decide(enrolmentId, approver, decision)
      if (result.outcome === 'not_yet_your_priority') {
        setNotice({
          tone: 'warning',
          text: `${approver} sits at priority ${result.your_priority}. The chain is waiting at priority ${result.active_priority}, so every approver there must decide first.`,
        })
      } else if (result.outcome === 'complete') {
        setNotice({
          tone: result.state === 'approved' ? 'success' : 'warning',
          text: `The chain finished and wrote ${result.state}.`,
        })
      } else {
        setNotice({
          tone: 'info',
          text: `${approver} decided. The chain is now waiting at priority ${result.next_priority}.`,
        })
      }
      await refresh()
    } catch (err) {
      setNotice({ tone: 'error', text: String(err.message || err) })
    } finally {
      setBusy(null)
    }
  }

  async function enrol(quoteId) {
    setBusy(`enrol:${quoteId}`)
    setNotice(null)
    try {
      const result = await approvalApi.enrol(quoteId)
      setNotice({
        tone: result.auto_approved ? 'info' : 'success',
        text: result.auto_approved
          ? 'No branch matched this quote, so it was auto-approved. That is the researched valve, not an error.'
          : 'The chain started and is waiting at priority 1.',
      })
      await refresh()
    } catch (err) {
      setNotice({ tone: 'error', text: String(err.message || err) })
    } finally {
      setBusy(null)
    }
  }

  async function toggleReEnrol(enabled) {
    setBusy('re-enrol')
    try {
      await approvalApi.toggleReEnrol(enabled)
      await refresh()
    } catch (err) {
      setNotice({ tone: 'error', text: String(err.message || err) })
    } finally {
      setBusy(null)
    }
  }

  const notBuilt = inferences.data?.not_built || []

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="font-display text-2xl font-semibold text-foreground">
            Quote approval chain
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            Each qualifying branch pushes a ranked approval step onto its quote. The chain runs
            sequentially, so a lower-priority approver is never notified before every approver at
            the current priority has decided. The last decision writes the final state.
          </p>
        </div>
        <div className="flex flex-col items-end gap-2">
          <Button icon="refresh" onClick={refresh}>
            Refresh
          </Button>
          <Toggle
            label="Re-approve after an edit"
            checked={Boolean(workflow.data?.re_enroll)}
            disabled={busy === 're-enrol'}
            onChange={toggleReEnrol}
          />
        </div>
      </header>

      {notice && <Notice tone={notice.tone}>{notice.text}</Notice>}

      <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Waiting" value={waiting.length} icon="audit" hint="Chains with a priority to decide" />
        <StatCard label="Decided" value={decided.length} icon="schema" hint="Approved or rejected" />
        <StatCard label="Auto-approved" value={autoApproved.length} icon="restore" hint="No approval step qualified" />
        <StatCard label="Branches" value={branches.data?.branches?.length || 0} icon="database" hint="Quote amount conditions" />
      </section>

      <section className="space-y-3">
        <h2 className="font-display text-lg font-semibold text-foreground">The chains</h2>
        {chainStates.length === 0 ? (
          <EmptyState
            title="No quote has started an approval chain"
            description="Enrol a quote from the list below. A quote above the branch threshold collects its approval steps and waits at priority 1."
          />
        ) : (
          <div className="space-y-4">
            {chainStates.map((row) => (
              <ChainCard key={row.id} enrolment={row} busy={busy} onDecide={decide} />
            ))}
          </div>
        )}
      </section>

      <section className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="font-display text-lg font-semibold text-foreground">Quotes</h2>
          <SectionLabel>WF-086 provisions these rows</SectionLabel>
        </div>
        <QuoteTable
          quotes={quotes.data?.quotes || []}
          enrolments={chainStates}
          busy={busy}
          onEnrol={enrol}
          branchError={branchError}
        />
        <BranchEditor
          branches={branches.data?.branches || []}
          vocabulary={vocabulary.data}
          busy={busy}
          onCreated={async () => {
            setBranchError(null)
            await branches.refetch()
          }}
          onError={setBranchError}
        />
      </section>

      <section className="grid gap-4 lg:grid-cols-2">
        <Card className="space-y-3">
          <SectionLabel>The researched limits</SectionLabel>
          <h2 className="text-base font-semibold text-foreground">What the rules enforce</h2>
          <dl className="space-y-2 text-sm">
            <div className="flex justify-between gap-3">
              <dt className="text-muted-foreground">Sequences per workflow</dt>
              <dd className="font-mono text-foreground">{caps.max_sequences}</dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt className="text-muted-foreground">Approvers per sequence</dt>
              <dd className="font-mono text-foreground">{caps.max_approvers_per_sequence}</dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt className="text-muted-foreground">Default branch threshold</dt>
              <dd className="font-mono text-foreground">{vocabulary.data?.branch?.default_threshold}</dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt className="text-muted-foreground">Requirements</dt>
              <dd className="text-foreground">
                {(vocabulary.data?.chain?.requirements || [])
                  .map((key) => REQUIREMENT_WORD[key] || key)
                  .join(', ')}
              </dd>
            </div>
          </dl>
          <p className="border-t border-border-subtle pt-3 text-sm text-muted-foreground">
            {vocabulary.data?.chain?.sequential_rule}
          </p>
          <p className="text-sm text-muted-foreground">
            Chain states: {states.map((state) => state.replace(/_/g, ' ')).join(', ')}.
          </p>
        </Card>

        <Card className="space-y-3">
          <SectionLabel>Deliberately not built</SectionLabel>
          <h2 className="text-base font-semibold text-foreground">What this workflow leaves out</h2>
          {notBuilt.length === 0 ? (
            <p className="text-sm text-muted-foreground">Nothing is recorded as left out.</p>
          ) : (
            <ul className="space-y-3">
              {notBuilt.map((entry) => (
                <li key={entry.id} className="border-t border-border-subtle pt-3 first:border-0 first:pt-0">
                  <p className="text-sm font-medium text-foreground">{entry.what_is_not_built}</p>
                  <p className="mt-1 text-sm text-muted-foreground">{entry.why}</p>
                  {entry.instead && (
                    <p className="mt-1 text-sm text-muted-foreground">Instead: {entry.instead}</p>
                  )}
                </li>
              ))}
            </ul>
          )}
          <p className="border-t border-border-subtle pt-3 text-sm text-muted-foreground">
            The template syntax is{' '}
            <span className="font-mono text-foreground">
              {vocabulary.data?.template?.example}
            </span>
            , resolved against the quote. A variable the quote does not carry is left in the
            message and named, rather than rendered empty.
          </p>
        </Card>
      </section>

      <Card className="space-y-3">
        <SectionLabel>Judgement calls</SectionLabel>
        <h2 className="text-base font-semibold text-foreground">
          What the research fixed and what this build chose
        </h2>
        <ul className="space-y-3">
          {(inferences.data?.inferences || []).map((entry) => (
            <li key={entry.id} className="border-t border-border-subtle pt-3 first:border-0 first:pt-0">
              <p className="font-mono text-xs text-muted-foreground">{entry.id}</p>
              <p className="mt-1 text-sm font-medium text-foreground">{entry.topic}</p>
              <p className="mt-1 text-sm text-muted-foreground">{entry.basis}</p>
              <p className="mt-1 text-sm text-muted-foreground">
                Chosen: {String(entry.value || entry.chosen)}
              </p>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  )
}

/** One chain, its ladder, and the buttons that move it. */
function ChainCard({ enrolment, busy, onDecide }) {
  const [open, setOpen] = useState(false)
  const levels = enrolment.levels || []
  const decisions = enrolment.decisions || {}
  const waitingAt = enrolment.active_priority

  return (
    <Card className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="font-mono text-sm text-foreground">{enrolment.quote_id}</h3>
          <p className="mt-1 text-sm text-muted-foreground">
            {enrolment.auto_approved
              ? 'No approval step qualified, so this quote was auto-approved.'
              : `Waiting at priority ${waitingAt === null || waitingAt === undefined ? 'none' : waitingAt}.`}
          </p>
        </div>
        <StatusPill state={enrolment.state} publishable={enrolment.publishable} />
      </div>

      {enrolment.reason && enrolment.auto_approved && (
        <p className="text-sm text-muted-foreground">{enrolment.reason}</p>
      )}

      <ChainLadder
        levels={levels}
        decisions={decisions}
        activePriority={waitingAt}
        state={enrolment.state}
      />

      {!enrolment.auto_approved && (
        <div className="space-y-2 border-t border-border-subtle pt-3">
          <SectionLabel>Record a decision</SectionLabel>
          <div className="flex flex-wrap gap-2">
            {levels
              .filter((level) => level.priority === waitingAt)
              .flatMap((level) => level.approvers)
              .map((approver) => (
                <span key={approver} className="flex items-center gap-2">
                  <span className="font-mono text-sm text-foreground">{approver}</span>
                  <Button
                    variant="primary"
                    disabled={busy === `${enrolment.id}:${approver}`}
                    onClick={() => onDecide(enrolment.id, approver, 'approved')}
                  >
                    Approve
                  </Button>
                  <Button
                    variant="danger"
                    disabled={busy === `${enrolment.id}:${approver}`}
                    onClick={() => onDecide(enrolment.id, approver, 'rejected')}
                  >
                    Reject
                  </Button>
                </span>
              ))}
          </div>
          <Button
            icon="audit"
            disabled={busy === `${enrolment.id}:detail`}
            onClick={() => setOpen((value) => !value)}
          >
            {open ? 'Hide the audit trail' : 'Show the audit trail'}
          </Button>
          {open && <AuditTrail id={enrolment.id} />}
        </div>
      )}
    </Card>
  )
}

/** The decisions and notifications behind one chain. */
function AuditTrail({ id }) {
  const detail = useAsync(() => approvalApi.enrolment(id), [id])
  if (detail.loading) return <Spinner label="Loading the audit trail" />
  if (detail.error) return <ErrorNote error={detail.error} onRetry={detail.refetch} />
  return (
    <div className="space-y-3 rounded-sm border border-border-subtle bg-surface p-3">
      <div>
        <SectionLabel>Decisions</SectionLabel>
        {(detail.data?.decision_rows || []).length === 0 ? (
          <p className="mt-1 text-sm text-muted-foreground">No decision has been recorded.</p>
        ) : (
          <ul className="mt-1 space-y-1">
            {detail.data.decision_rows.map((row) => (
              <li key={row.id} className="flex justify-between gap-2 text-sm">
                <span className="font-mono text-foreground">{row.approver}</span>
                <span className="text-muted-foreground">{row.decision}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div>
        <SectionLabel>Notifications</SectionLabel>
        <div className="mt-1">
          <NotificationList notifications={detail.data?.notifications || []} />
        </div>
      </div>
    </div>
  )
}

/** The quotes a branch can be evaluated against, with a start button each. */
function QuoteTable({ quotes, enrolments, busy, onEnrol }) {
  const started = useMemo(
    () => new Set(enrolments.map((row) => row.quote_id)),
    [enrolments]
  )
  if (!quotes.length) {
    return (
      <EmptyState
        title="No quote is available"
        description="WF-086 provisions the authored quote. This workflow reads those rows and never creates one."
      />
    )
  }
  return (
    <Card className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border-subtle text-left">
            <th className="px-2 py-2 text-xs uppercase tracking-[0.14em] text-muted-foreground">
              Quote
            </th>
            <th className="px-2 py-2 text-xs uppercase tracking-[0.14em] text-muted-foreground">
              Amount
            </th>
            <th className="px-2 py-2 text-xs uppercase tracking-[0.14em] text-muted-foreground">
              Chain
            </th>
            <th className="px-2 py-2 text-xs uppercase tracking-[0.14em] text-muted-foreground">
              Action
            </th>
          </tr>
        </thead>
        <tbody>
          {quotes.map((quote) => {
            const amount = quote.quote_amount
            const qualifies = Number(amount) > 5000
            return (
              <tr key={quote.id} className="border-b border-border-subtle last:border-0">
                <td className="px-2 py-2">
                  <span className="font-mono text-foreground">{quote.name || quote.id}</span>
                </td>
                <td className="px-2 py-2 font-mono text-foreground">{money(amount)}</td>
                <td className="px-2 py-2 text-muted-foreground">
                  {started.has(quote.id) ? 'Started' : qualifies ? 'Would qualify' : 'Skips approval'}
                </td>
                <td className="px-2 py-2">
                  {started.has(quote.id) ? (
                    <span className="text-xs text-muted-foreground">Already enrolled</span>
                  ) : (
                    <Button
                      disabled={busy === `enrol:${quote.id}`}
                      onClick={() => onEnrol(quote.id)}
                    >
                      Start the approval flow
                    </Button>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </Card>
  )
}

/** Add one branch: a property, an operator, a threshold and the ranked approvers. */
function BranchEditor({ branches, vocabulary, onCreated, onError }) {
  const [name, setName] = useState('')
  const [property, setProperty] = useState(vocabulary?.branch?.property || 'quote_amount')
  const [operator, setOperator] = useState('greater_than')
  const [threshold, setThreshold] = useState(vocabulary?.branch?.default_threshold ?? 5000)
  const [rows, setRows] = useState([
    { priority: 1, approver: 'sales_manager', requirement: 'sequential' },
  ])

  const maxApprovers = vocabulary?.chain?.caps?.max_approvers_per_sequence ?? 10
  const requirements = vocabulary?.chain?.requirements || ['sequential']

  function updateRow(index, patch) {
    setRows((current) => current.map((row, at) => (at === index ? { ...row, ...patch } : row)))
  }

  function addRow() {
    setRows((current) => [
      ...current,
      {
        priority: current.length + 1,
        approver: '',
        requirement: 'sequential',
      },
    ])
  }

  async function submit() {
    onError(null)
    try {
      await approvalApi.addBranch({
        name: name || 'Quote amount branch',
        property,
        operator,
        threshold: Number(threshold),
        sequences: rows
          .filter((row) => row.approver.trim())
          .map((row) => ({
            priority: Number(row.priority),
            approvers: [row.approver.trim()],
            requirement: row.requirement,
          })),
      })
      setName('')
      setRows([{ priority: 1, approver: 'sales_manager', requirement: 'sequential' }])
      await onCreated()
    } catch (err) {
      onError(err)
    }
  }

  const approverCount = rows.filter((row) => row.approver.trim()).length
  const overCap = approverCount > maxApprovers

  return (
    <Card className="space-y-4">
      <SectionLabel>Add a branch</SectionLabel>
      <h2 className="text-base font-semibold text-foreground">
        What sends a quote into the chain
      </h2>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Field label="Branch name" id="wf092-branch-name">
          <input
            id="wf092-branch-name"
            className={inputClass}
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Quotes above 5000"
          />
        </Field>
        <Field label="Quote property" id="wf092-branch-property">
          <input
            id="wf092-branch-property"
            className={inputClass}
            value={property}
            onChange={(event) => setProperty(event.target.value)}
          />
        </Field>
        <Field label="Operator" id="wf092-branch-operator">
          <select
            id="wf092-branch-operator"
            className={inputClass}
            value={operator}
            onChange={(event) => setOperator(event.target.value)}
          >
            {(vocabulary?.branch?.operators || ['greater_than']).map((key) => (
              <option key={key} value={key}>
                {key.replace(/_/g, ' ')}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Threshold" id="wf092-branch-threshold">
          <input
            id="wf092-branch-threshold"
            className={inputClass}
            type="number"
            value={threshold}
            onChange={(event) => setThreshold(event.target.value)}
          />
        </Field>
      </div>

      <div className="space-y-2">
        <SectionLabel>The ranked approvers this branch pushes</SectionLabel>
        {rows.map((row, index) => (
          <div key={index} className="grid gap-2 sm:grid-cols-4">
            <Field label={`Priority ${row.priority}`} id={`wf092-priority-${index}`}>
              <input
                id={`wf092-priority-${index}`}
                className={inputClass}
                type="number"
                min="1"
                value={row.priority}
                onChange={(event) => updateRow(index, { priority: event.target.value })}
              />
            </Field>
            <Field label="Approver" id={`wf092-approver-${index}`}>
              <input
                id={`wf092-approver-${index}`}
                className={inputClass}
                value={row.approver}
                onChange={(event) => updateRow(index, { approver: event.target.value })}
              />
            </Field>
            <Field label="Requirement" id={`wf092-requirement-${index}`}>
              <select
                id={`wf092-requirement-${index}`}
                className={inputClass}
                value={row.requirement}
                onChange={(event) => updateRow(index, { requirement: event.target.value })}
              >
                {requirements.map((key) => (
                  <option key={key} value={key}>
                    {REQUIREMENT_WORD[key] || key}
                  </option>
                ))}
              </select>
            </Field>
            <div className="flex items-end">
              <Button
                icon="trash"
                variant="ghost"
                disabled={rows.length === 1}
                onClick={() => setRows((current) => current.filter((_, at) => at !== index))}
              >
                Remove priority {row.priority}
              </Button>
            </div>
          </div>
        ))}
        <div className="flex flex-wrap items-center gap-3">
          <Button icon="plus" onClick={addRow}>
            Add a priority
          </Button>
          <span className="text-xs text-muted-foreground">
            {approverCount} of {maxApprovers} approvers used in this sequence.
          </span>
        </div>
      </div>

      {overCap && (
        <Notice tone="error">
          This sequence names {approverCount} approvers. The researched cap is {maxApprovers} per
          sequence.
        </Notice>
      )}

      <Button variant="primary" icon="plus" disabled={overCap} onClick={submit}>
        Add the branch
      </Button>

      {branches.length > 0 && (
        <div className="space-y-3 border-t border-border-subtle pt-4">
          <SectionLabel>Configured branches</SectionLabel>
          {branches.map((branch) => (
            <BranchCard key={branch.id} branch={branch} />
          ))}
        </div>
      )}
    </Card>
  )
}