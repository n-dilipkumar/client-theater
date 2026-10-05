/**
 * WF-091: route a discounted quote for standard approval.
 *
 * The page is built from `@/components/ui` and from the primitives in this folder. It
 * reads its operators, its cap, its requirements and its states from the server's
 * `/vocabulary` route rather than repeating them, so the panel cannot tell a seller a
 * cap the rules do not enforce.
 *
 * It follows the researched flow in order. The top card is the rule builder, which is
 * Settings -> Objects -> Quotes -> Approvals. Under it is the queue, which is the
 * quotes index page filtered to Pending approval. Beside each request is the Approvals
 * panel, which is the sidebar a seller and an approver both see.
 *
 * Three things this page deliberately does not do:
 *
 *   - It does not offer a field for a quote's status. The quote belongs to WF-086 and
 *     this workflow reads it, so the panel reports the state rather than editing it.
 *   - It does not claim a notification was delivered. There is no mail transport in this
 *     product, and every row says "recorded, not delivered".
 *   - It does not hide the self-approval rule. Two sourced sentences disagree about a
 *     quote its own creator wrote, and the reading this build took is shown in full with
 *     the audit that chose it.
 */

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
  approvalApi,
  approverCount,
  isShareable,
  listQuotes,
  listRooms,
  requirementLabel,
} from './api'
import {
  ConditionRow,
  FilterRow,
  NotificationRow,
  Notice,
  QUOTE_APPROVAL_ICON,
  SelfApprovalPanel,
  StateBadge,
  TallyRow,
} from './primitives'

const EMPTY_FILTER = { object: 'line_item', property: '', operator: 'gt', value: '' }

/**
 * The rule builder, which is the researched settings screen.
 *
 * The filters, the approvers, the requirement and the note are saved in one call
 * because the research's own UI refuses to save a rule that is missing any of them, so
 * a half-configured rule is not a state this product creates either.
 */
function RuleBuilder({ vocabulary, roomId, actor, onSaved }) {
  const [key, setKey] = useState('')
  const [label, setLabel] = useState('')
  const [filters, setFilters] = useState([{ ...EMPTY_FILTER }])
  const [approvers, setApprovers] = useState('')
  const [requirement, setRequirement] = useState('all')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [done, setDone] = useState(null)

  const cap = vocabulary?.approvers?.max ?? 10
  const objects = vocabulary?.filters?.objects || []
  const operators = vocabulary?.filters?.operators || []

  function updateFilter(index, patch) {
    setFilters((current) =>
      current.map((one, position) => (position === index ? { ...one, ...patch } : one)),
    )
  }

  function removeFilter(index) {
    setFilters((current) => current.filter((_, position) => position !== index))
  }

  function addFilter() {
    setFilters((current) => [...current, { ...EMPTY_FILTER }])
  }

  async function save() {
    setBusy(true)
    setError(null)
    setDone(null)
    try {
      const names = approvers
        .split(',')
        .map((entry) => entry.trim())
        .filter(Boolean)
      const created = await approvalApi.createRule(
        {
          key,
          label: label || key,
          filters: filters.map((one) => ({
            ...one,
            // The list operators take a list, so the comma-separated field is split
            // rather than sent as one string that would match nothing.
            value: ['in', 'not_in'].includes(one.operator)
              ? String(one.value)
                  .split(',')
                  .map((entry) => entry.trim())
                  .filter(Boolean)
              : one.value,
          })),
          approvers: names,
          requirement,
          approval_note: note,
        },
        { roomId, actor },
      )
      setDone(`Saved ${created.data?.label || key}.`)
      setKey('')
      setLabel('')
      setFilters([{ ...EMPTY_FILTER }])
      setApprovers('')
      setNote('')
      onSaved()
    } catch (problem) {
      setError(problem)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card className="flex flex-col gap-4">
      <div>
        <h2 className="text-lg font-semibold text-foreground">Approval rules</h2>
        <p className="text-sm text-muted-foreground">
          A rule holds the filters, the approvers and the requirement together. Every filter in a
          rule must be met.
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Rule name" id="wf091-rule-key" hint="Lower-case letters, digits and hyphens.">
          <input
            id="wf091-rule-key"
            className={inputClass}
            value={key}
            onChange={(event) => setKey(event.target.value)}
          />
        </Field>
        <Field label="Label" id="wf091-rule-label" hint="What a seller reads on the queue.">
          <input
            id="wf091-rule-label"
            className={inputClass}
            value={label}
            onChange={(event) => setLabel(event.target.value)}
          />
        </Field>
      </div>

      <div className="flex flex-col gap-2">
        <p className="text-sm font-medium text-foreground">And these conditions are met</p>
        {filters.map((one, index) => (
          <FilterRow
            key={index}
            index={index}
            filter={one}
            objects={objects}
            operators={operators}
            onChange={updateFilter}
            onRemove={removeFilter}
            disabled={busy}
          />
        ))}
        <div>
          <Button icon="plus" onClick={addFilter} disabled={busy}>
            Add filter
          </Button>
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <Field
          label="Approvers"
          id="wf091-approvers"
          hint={`Comma separated, up to ${cap}. ${vocabulary?.approvers?.cap_quote || ''}`}
        >
          <input
            id="wf091-approvers"
            className={inputClass}
            value={approvers}
            onChange={(event) => setApprovers(event.target.value)}
            placeholder="sam, priya"
          />
        </Field>
        <Field label="Approver requirements" id="wf091-requirement">
          <select
            id="wf091-requirement"
            className={inputClass}
            value={requirement}
            onChange={(event) => setRequirement(event.target.value)}
          >
            {(vocabulary?.approvers?.requirements || []).map((row) => (
              <option key={row.requirement} value={row.requirement}>
                {row.label}
              </option>
            ))}
          </select>
        </Field>
      </div>

      <Field
        label="With this approval note"
        id="wf091-note"
        hint="Copied onto every request this rule makes, so a past request explains itself."
      >
        <input
          id="wf091-note"
          className={inputClass}
          value={note}
          onChange={(event) => setNote(event.target.value)}
        />
      </Field>

      {done && (
        <p role="status" aria-live="polite" className="text-xs text-success">
          {done}
        </p>
      )}
      {error && <ErrorNote error={error} />}

      <div>
        <Button variant="primary" disabled={busy} onClick={save}>
          Save the rule
        </Button>
      </div>
    </Card>
  )
}

/**
 * One configured rule, with its switch.
 *
 * Turning the switch off enrols nobody, and the answer says so rather than leaving the
 * seller to wonder whether the rule fired and missed.
 */
function RuleRow({ rule, vocabulary, actor, onChanged }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const data = rule.data || {}
  const enabled = data.enabled !== false

  async function toggle() {
    setBusy(true)
    setError(null)
    try {
      await approvalApi.patchRule(rule.id, { enabled: !enabled }, { actor })
      onChanged()
    } catch (problem) {
      setError(problem)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-2 border-b border-border-subtle py-3 last:border-b-0">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-foreground">{data.label || data.key}</p>
          <p className="text-xs text-muted-foreground">
            {requirementLabel(data.requirement, vocabulary)}. Approvers{' '}
            <span className="font-mono">{approverCount(rule)}</span> of{' '}
            <span className="font-mono">{vocabulary?.approvers?.max ?? 10}</span>.
          </p>
        </div>
        <Button disabled={busy} onClick={toggle}>
          {enabled ? 'Turn off' : 'Turn on'}
        </Button>
      </div>

      <ul className="flex flex-col gap-1">
        {(data.filters || []).map((one, index) => (
          <li key={index} className="text-xs text-muted-foreground">
            <span className="font-mono text-foreground">
              {one.object}.{one.property}
            </span>{' '}
            {one.operator}{' '}
            <span className="font-mono">{JSON.stringify(one.value)}</span>
          </li>
        ))}
      </ul>

      {data.approval_note && (
        <p className="text-xs italic text-muted-foreground">{data.approval_note}</p>
      )}
      {!enabled && <Notice tone="warning">This rule is off. It enrols nobody.</Notice>}
      {error && <ErrorNote error={error} />}
    </div>
  )
}

/**
 * The Approvals panel for one request: the conditions, the tally, and the two buttons.
 *
 * The research's step five is one panel with **Approve** and **Request changes**. The
 * change request needs a reason, so the field appears only when that branch is chosen
 * rather than always sitting there beside the approve button.
 */
function RequestPanel({ request, vocabulary, actor, onChanged }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [message, setMessage] = useState('')
  const [asking, setAsking] = useState(false)

  const data = request.data || {}
  const approvers = request.approvers || data.approvers || []
  const removed = request.removed_approvers || data.removed_approvers || []
  const identity = actor || approvers[0] || ''

  async function run(decision) {
    setBusy(true)
    setError(null)
    try {
      await approvalApi.decide(
        request.id,
        { decision, message, approver: identity, actor: identity },
      )
      setMessage('')
      setAsking(false)
      onChanged()
    } catch (problem) {
      setError(problem)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-3 border-b border-border-subtle py-3 last:border-b-0">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-foreground">
            {data.quote_name || request.quote_id}
          </p>
          <p className="font-mono text-xs text-muted-foreground">
            {data.rule_label || data.rule_key}
          </p>
        </div>
        <StateBadge state={request.status} vocabulary={vocabulary} />
      </div>

      {request.exempt && (
        <Notice tone="info" title="No approval was required">
          {request.exemption_reason === 'sole_approver_is_the_creator'
            ? 'The only configured approver created this quote, so the research says it does not require approval.'
            : data.exemption_reason}
        </Notice>
      )}

      {removed.length > 0 && (
        <Notice tone="neutral" title="An approver was removed">
          <span className="font-mono">{removed.join(', ')}</span> created this quote, so they were
          removed from the approver list and cannot approve it.
        </Notice>
      )}

      <TallyRow
        tally={request.tally}
        requirementLabel={requirementLabel(data.requirement || request.requirement, vocabulary)}
      />

      {(request.conditions?.filters || []).length > 0 && (
        <div className="flex flex-col gap-1">
          <p className="text-xs font-medium text-foreground">Why approval was required</p>
          {request.conditions.filters.map((one, index) => (
            <ConditionRow key={index} condition={one} />
          ))}
        </div>
      )}

      {data.approval_note && (
        <p className="text-xs italic text-muted-foreground">Note to approvers: {data.approval_note}</p>
      )}
      {data.notes_to_approver && (
        <p className="text-xs italic text-muted-foreground">
          Notes to approver: {data.notes_to_approver}
        </p>
      )}

      {request.status === 'PENDING_APPROVAL' && !request.exempt && (
        <>
          {asking && (
            <Field
              label="What changes are needed"
              id={`wf091-change-${request.id}`}
              hint="A change request with nothing in it is not an instruction the seller can act on."
            >
              <input
                id={`wf091-change-${request.id}`}
                className={inputClass}
                value={message}
                onChange={(event) => setMessage(event.target.value)}
              />
            </Field>
          )}
          {error && <ErrorNote error={error} />}
          <div className="flex flex-wrap gap-2">
            <Button
              variant="primary"
              icon="audit"
              disabled={busy || !approvers.includes(identity)}
              onClick={() => run('approve')}
            >
              Approve
            </Button>
            <Button
              variant="danger"
              icon="audit"
              disabled={busy || !approvers.includes(identity)}
              onClick={() => setAsking(!asking)}
            >
              Request changes
            </Button>
            {asking && (
              <Button disabled={busy || message.trim() === ''} onClick={() => run('request_changes')}>
                Send the change request
              </Button>
            )}
          </div>
          {!approvers.includes(identity) && (
            <p className="text-xs text-muted-foreground">
              You are not one of the approvers on this request, so both buttons are off.
            </p>
          )}
        </>
      )}

      {request.shareable && (
        <Notice tone="success">
          This quote is approved, so it may be shared with the buyer.
        </Notice>
      )}
    </div>
  )
}

/** The seller's side: pick a quote, see why approval is required, and submit. */
function QuotePanel({ quote, vocabulary, roomId, actor, onChanged }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [answer, setAnswer] = useState(null)
  const [notes, setNotes] = useState('')

  const conditions = useAsync(() => approvalApi.conditions(quote.id), [quote.id])
  const state = useAsync(() => approvalApi.quoteState(quote.id), [quote.id])

  async function submit() {
    setBusy(true)
    setError(null)
    setAnswer(null)
    try {
      const result = await approvalApi.submit(quote.id, {
        notesToApprover: notes,
        actor,
        roomId,
      })
      setAnswer(result)
      setNotes('')
      conditions.refetch()
      state.refetch()
      onChanged()
    } catch (problem) {
      setError(problem)
    } finally {
      setBusy(false)
    }
  }

  async function share() {
    setBusy(true)
    setError(null)
    try {
      await approvalApi.share(quote.id, { roomId, actor })
      state.refetch()
      onChanged()
    } catch (problem) {
      setError(problem)
    } finally {
      setBusy(false)
    }
  }

  if (conditions.loading || state.loading) return <Spinner label="Reading the approval conditions" />
  if (conditions.error) return <ErrorNote error={conditions.error} onRetry={conditions.refetch} />

  const panel = conditions.data || {}
  const effective = state.data?.effective_status || 'DRAFT'

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-foreground">
            {quote.data?.name || quote.data?.title || quote.id}
          </p>
          <p className="font-mono text-xs text-muted-foreground">{quote.id}</p>
        </div>
        <StateBadge state={effective} vocabulary={vocabulary} />
      </div>

      {panel.required ? (
        <>
          <Notice tone="warning" title="Approval is required">
            {panel.rules.length} rule matched. Every approver named below must decide.
          </Notice>
          {(panel.rules || []).map((one, index) => (
            <div key={index} className="flex flex-col gap-2">
              <p className="text-xs font-medium text-foreground">{one.rule_label}</p>
              <TallyRow
                tally={{
                  approved: [],
                  rejected: [],
                  outstanding: one.approvers,
                  outstanding_count: one.approvers.length,
                }}
                requirementLabel={requirementLabel(one.requirement, vocabulary)}
              />
              {(one.matched_filters || []).map((condition, position) => (
                <ConditionRow key={position} condition={condition} />
              ))}
            </div>
          ))}
        </>
      ) : (
        <Notice tone="neutral" title="No approval is required">
          No configured filter matched this quote, so nothing holds it.
        </Notice>
      )}

      <Field
        label="Notes to approver"
        id={`wf091-notes-${quote.id}`}
        hint="Optional. Carried onto the request."
      >
        <input
          id={`wf091-notes-${quote.id}`}
          className={inputClass}
          value={notes}
          onChange={(event) => setNotes(event.target.value)}
        />
      </Field>

      {answer && (
        <Notice tone={answer.required ? 'warning' : 'info'}>
          {answer.required
            ? `Sent to ${(answer.approvers || []).join(', ')}. The quote is now ${answer.enrolment?.status}.`
            : answer.explanation ||
              `No approval required. ${answer.reason || ''}`}
        </Notice>
      )}
      {error && <ErrorNote error={error} />}

      <div className="flex flex-wrap gap-2">
        <Button variant="primary" disabled={busy} onClick={submit}>
          Request approval
        </Button>
        <Button disabled={busy || !isShareable(effective)} onClick={share}>
          Share with the buyer
        </Button>
      </div>
      {!isShareable(effective) && (
        <p className="text-xs text-muted-foreground">
          {state.data?.share?.reason || 'Only on approval can the quote be shared.'}
        </p>
      )}
    </div>
  )
}

function QuoteApprovalPage() {
  const [roomId, setRoomId] = useState('')
  const [actor, setActor] = useState('')
  const [status, setStatus] = useState('PENDING_APPROVAL')

  const rooms = useAsync(() => listRooms(), [])
  const vocabulary = useAsync(() => approvalApi.vocabulary(), [])
  const inferences = useAsync(() => approvalApi.inferences(), [])
  const summary = useAsync(() => approvalApi.summary(roomId), [roomId])
  const rules = useAsync(() => approvalApi.rules(roomId), [roomId])
  const queue = useAsync(() => approvalApi.requests({ roomId, status }), [roomId, status])
  const quotes = useAsync(() => listQuotes(), [])
  const notifications = useAsync(() => approvalApi.notifications(), [])

  if (vocabulary.loading || rooms.loading) return <Spinner label="Loading quote approval" />
  if (vocabulary.error) return <ErrorNote error={vocabulary.error} onRetry={vocabulary.refetch} />
  if (queue.error) return <ErrorNote error={queue.error} onRetry={queue.refetch} />

  const refreshAll = () => {
    queue.refetch()
    rules.refetch()
    summary.refetch()
    notifications.refetch()
  }

  const entries = queue.data?.requests || []
  const quoteEntries = quotes.data?.records || []

  return (
    <div className="space-y-6">
      <header className="flex flex-col gap-1">
        <h1 className="font-display text-2xl font-semibold text-foreground">
          Route a discounted quote for approval
        </h1>
        <p className="text-sm text-muted-foreground">
          A quote is held at submit, at publish and at share when its properties match a configured
          filter. Its approvers decide, and only an approved quote can go to a buyer.
        </p>
      </header>

      <SelfApprovalPanel reading={inferences.data?.self_approval} />

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Waiting on an approver"
          value={summary.data?.awaiting_an_approver ?? 0}
          icon="audit"
          hint="Quotes in Pending approval"
        />
        <StatCard
          label="Approved"
          value={summary.data?.by_state?.APPROVED ?? 0}
          icon="audit"
          hint="May be shared with a buyer"
        />
        <StatCard
          label="Rejected"
          value={summary.data?.by_state?.REJECTED ?? 0}
          icon="audit"
          hint="Changes were requested"
        />
        <StatCard
          label="Rules on"
          value={summary.data?.rules_enabled ?? 0}
          icon="database"
          hint={`Of ${summary.data?.rules ?? 0} configured`}
        />
      </div>

      <Card className="flex flex-col gap-4">
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Room" id="wf091-room" hint="Rules and requests are scoped to a room.">
            <select
              id="wf091-room"
              className={inputClass}
              value={roomId}
              onChange={(event) => setRoomId(event.target.value)}
            >
              <option value="">All rooms</option>
              {(rooms.data?.records || []).map((room) => (
                <option key={room.id} value={room.id}>
                  {room.data?.name || room.id}
                </option>
              ))}
            </select>
          </Field>

          <Field label="Status" id="wf091-status" hint="The researched Pending approval view.">
            <select
              id="wf091-status"
              className={inputClass}
              value={status}
              onChange={(event) => setStatus(event.target.value)}
            >
              {(vocabulary.data?.quote_states || []).map((row) => (
                <option key={row.state} value={row.state}>
                  {row.state}
                </option>
              ))}
            </select>
          </Field>

          <Field label="Acting as" id="wf091-actor" hint="Who is submitting or deciding.">
            <input
              id="wf091-actor"
              className={inputClass}
              value={actor}
              onChange={(event) => setActor(event.target.value)}
              placeholder="dana"
            />
          </Field>
        </div>
      </Card>

      <RuleBuilder
        vocabulary={vocabulary.data}
        roomId={roomId}
        actor={actor}
        onSaved={refreshAll}
      />

      <Card className="flex flex-col gap-2">
        <div>
          <h2 className="text-lg font-semibold text-foreground">Configured rules</h2>
          <p className="text-sm text-muted-foreground">
            A rule that is off enrols nobody, and the answer names which rules matched.
          </p>
        </div>
        {(rules.data?.rules || []).length === 0 && (
          <EmptyState
            title="No rules yet"
            description="A rule holds the filters, the approvers and the requirement. With no rule, nothing holds a quote."
          />
        )}
        {(rules.data?.rules || []).map((rule) => (
          <RuleRow
            key={rule.id}
            rule={rule}
            vocabulary={vocabulary.data}
            actor={actor}
            onChanged={refreshAll}
          />
        ))}
      </Card>

      <Card className="flex flex-col gap-2">
        <div>
          <h2 className="text-lg font-semibold text-foreground">
            Quotes, and why they are held
          </h2>
          <p className="text-sm text-muted-foreground">
            The conditions run before a seller submits, so what is shown is what will be enforced.
          </p>
        </div>
        {quoteEntries.length === 0 && (
          <EmptyState
            title="No quotes to approve"
            description="This workflow reads the quotes another workflow provisions. Point it at a collection that holds some."
          />
        )}
        {quoteEntries.map((quote) => (
          <QuotePanel
            key={quote.id}
            quote={quote}
            vocabulary={vocabulary.data}
            roomId={roomId}
            actor={actor}
            onChanged={refreshAll}
          />
        ))}
      </Card>

      <Card className="flex flex-col gap-2">
        <div>
          <h2 className="text-lg font-semibold text-foreground">Pending approval</h2>
          <p className="text-sm text-muted-foreground">
            One approver's place in the requirement, and the two buttons.
          </p>
        </div>
        {entries.length === 0 && (
          <EmptyState
            title="Nothing is waiting"
            description="No approval request matches this filter. A request appears here as soon as a rule matches a quote."
          />
        )}
        {entries.map((request) => (
          <RequestPanel
            key={request.id}
            request={request}
            vocabulary={vocabulary.data}
            actor={actor}
            onChanged={refreshAll}
          />
        ))}
      </Card>

      <Card className="flex flex-col gap-2">
        <div>
          <h2 className="text-lg font-semibold text-foreground">Recorded notifications</h2>
          <p className="text-sm text-muted-foreground">
            {vocabulary.data?.notifications?.recorded_only_note}
          </p>
        </div>
        {(notifications.data?.notifications || []).length === 0 && (
          <EmptyState
            title="No notifications recorded"
            description="A notification is recorded when a quote is submitted or decided. None has been."
          />
        )}
        {(notifications.data?.notifications || []).slice(0, 20).map((one, index) => (
          <NotificationRow key={`${one.id}-${index}`} notification={one} />
        ))}
      </Card>
    </div>
  )
}

export default {
  id: 'wf-091-route-a-discounted-quote-for-standard-approval',
  label: 'Quote approval',
  icon: 'audit',
  iconPath: QUOTE_APPROVAL_ICON,
  order: 910,
  Component: QuoteApprovalPage,
}
