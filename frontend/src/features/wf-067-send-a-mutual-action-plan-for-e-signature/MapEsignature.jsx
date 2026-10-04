/**
 * Mutual action plan e-signature (WF-067).
 *
 * The page a seller uses to send a mutual action plan out for signature and to
 * watch it come back. Four tabs, in the order the research tells the story:
 *
 * 1. **Needs you** - the plans where a person is holding something up. Right now
 *    that means exactly one thing: an approver has not approved, so "APPROVER |
 *    Must approve before signers can sign". Nothing else on the page matters until
 *    that is cleared, so it gets its own tab.
 * 2. **Plans** - every plan in the room, its milestone, its recipients and the two
 *    actions available on it.
 * 3. **Activity** - the webhook log, retries included, because "Webhooks may be
 *    retried, so handle duplicate events" is only demonstrably true if the repeat
 *    is visible.
 * 4. **Decisions** - the inference register, so a reviewer reads the list rather
 *    than reconstructing it from a diff.
 *
 * Two invariants are stated on the page rather than left implicit, because they
 * are the two things a buyer would want to know and a seller would assume
 * instead: this product never signs for a recipient, and the signed document is
 * not retrievable before every recipient has finished.
 */

import { useCallback, useState } from 'react'

import {
  Badge,
  Button,
  Card,
  ErrorNote,
  Field,
  Spinner,
  StatCard,
  useAsync,
} from '@/components/ui'

import {
  eventMeaning,
  isRefusal,
  isTerminal,
  mapApi,
  milestoneMeaning,
  milestoneTone,
  needsAttention,
  refusalSummary,
} from './api'
import {
  Checkbox,
  MilestoneBadge,
  Modal,
  MonoValue,
  NothingYet,
  Notice,
  RecipientStatus,
  RoleBadge,
  Row,
  Select,
  TextInput,
} from './primitives'

const TABS = [
  { id: 'attention', label: 'Needs you' },
  { id: 'plans', label: 'Plans' },
  { id: 'activity', label: 'Activity' },
  { id: 'decisions', label: 'Decisions' },
]

export default function MapEsignature() {
  const [tab, setTab] = useState('attention')
  const [roomId, setRoomId] = useState('')
  const [composing, setComposing] = useState(false)
  const [selected, setSelected] = useState(null)
  const [busy, setBusy] = useState('')
  const [actionError, setActionError] = useState(null)

  const roomsState = useAsync(() => mapApi.rooms(), [])
  const vocabState = useAsync(() => mapApi.vocabulary(), [])
  const inferenceState = useAsync(() => mapApi.inferences(), [])

  const summaryState = useAsync(
    () => (roomId ? mapApi.summary(roomId) : Promise.resolve(null)),
    [roomId]
  )
  const plansState = useAsync(
    () => (roomId ? mapApi.plans(roomId) : Promise.resolve(null)),
    [roomId]
  )

  // The room list arrives after first paint, so the first room is adopted once
  // the list lands. A room with no MAP yet is a normal state the page renders,
  // not an error, so nothing here blocks on it.
  const availableRooms = roomsState.data?.records || []
  if (!roomId && availableRooms.length) setRoomId(availableRooms[0].id)

  const vocabulary = vocabState.data
  const plans = plansState.data?.plans || []
  const attention = plans.filter(needsAttention)

  const reload = useCallback(() => {
    summaryState.refetch()
    plansState.refetch()
  }, [summaryState, plansState])

  async function run(name, action) {
    setBusy(name)
    setActionError(null)
    try {
      await action()
      reload()
    } catch (error) {
      setActionError(error)
    } finally {
      setBusy('')
    }
  }

  if (roomsState.loading || vocabState.loading) {
    return <Spinner label="Loading mutual action plans" />
  }
  if (roomsState.error) return <ErrorNote error={roomsState.error} onRetry={roomsState.refetch} />
  if (vocabState.error) return <ErrorNote error={vocabState.error} onRetry={vocabState.refetch} />

  const summary = summaryState.data

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <h1 className="font-display text-2xl font-semibold text-foreground">
            Mutual action plan signatures
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Send a plan to named signers and approvers, then track the envelope until the
            milestone flips to approved.
          </p>
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <Field id="wf067-room" label="Room">
            <Select
              id="wf067-room"
              value={roomId}
              onChange={(value) => {
                setRoomId(value)
                setSelected(null)
              }}
              options={availableRooms.map((room) => ({
                value: room.id,
                label: room.data?.name || room.id,
              }))}
            />
          </Field>
          <Button
            variant="primary"
            icon="plus"
            className="min-h-11"
            disabled={!roomId}
            onClick={() => setComposing(true)}
          >
            Send a plan
          </Button>
        </div>
      </header>

      {summary && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard
            label="Awaiting signatures"
            value={summary.awaiting_signature || 0}
            hint="Out for signature now"
            icon="audit"
          />
          <StatCard
            label="Approved"
            value={summary.approved || 0}
            hint="Every recipient finished"
            icon="rooms"
          />
          <StatCard
            label="Webhook events"
            value={summary.events || 0}
            hint="Retries counted, applied once"
            icon="database"
          />
          <StatCard
            label="Unread notices"
            value={summary.unread_notices || 0}
            hint="What the events told the owner"
            icon="plus"
          />
        </div>
      )}

      <Notice tone="info" title="What this product will not do">
        It never signs for a recipient. The vendor states that recipients must sign
        themselves, and the room keeps that: a recipient's status changes only because a
        verified webhook said it did. The signed document is also not retrievable until
        every recipient has finished signing.
      </Notice>

      {actionError && (
        <ErrorNote error={{ message: refusalSummary(actionError) }} onRetry={reload} />
      )}

      <nav aria-label="Sections" className="flex flex-wrap gap-2 border-b border-border-subtle">
        {TABS.map((entry) => (
          <button
            key={entry.id}
            type="button"
            onClick={() => setTab(entry.id)}
            aria-current={tab === entry.id ? 'page' : undefined}
            className={`min-h-11 rounded-sm px-3 text-sm ${
              tab === entry.id
                ? 'border-b-2 border-accent font-medium text-accent'
                : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            {entry.label}
            {entry.id === 'attention' && attention.length > 0 && (
              <span className="ml-2 font-mono text-xs">({attention.length})</span>
            )}
          </button>
        ))}
      </nav>

      {!roomId && (
        <NothingYet
          title="No rooms yet"
          description="A mutual action plan belongs to a room. Create one and it appears here."
        />
      )}

      {roomId && tab === 'attention' && (
        <AttentionTab plans={attention} vocabulary={vocabulary} onOpen={setSelected} />
      )}
      {roomId && tab === 'plans' && (
        <PlansTab
          plans={plans}
          vocabulary={vocabulary}
          loading={plansState.loading}
          onOpen={setSelected}
          onDistribute={(plan) =>
            run(`distribute-${plan.id}`, () => mapApi.distribute(roomId, plan.id))
          }
          onCancel={(plan) => run(`cancel-${plan.id}`, () => mapApi.cancel(roomId, plan.id))}
          busy={busy}
          onCreate={() => setComposing(true)}
        />
      )}
      {roomId && tab === 'activity' && <ActivityTab roomId={roomId} vocabulary={vocabulary} />}
      <DecisionsTab state={inferenceState} />

      {selected && (
        <PlanDetail
          roomId={roomId}
          planId={selected}
          vocabulary={vocabulary}
          onClose={() => setSelected(null)}
          onChanged={reload}
        />
      )}

      {composing && (
        <ComposePlan
          roomId={roomId}
          vocabulary={vocabulary}
          onClose={() => setComposing(false)}
          onCreated={() => {
            setComposing(false)
            reload()
          }}
        />
      )}
    </div>
  )
}

/**
 * The plans where a person is holding something up.
 *
 * The research names exactly one such situation - an approver who has not approved -
 * so this tab is narrow on purpose. A generic "needs attention" list would fill up
 * with plans that are simply waiting on a buyer, which is not a problem.
 */
function AttentionTab({ plans, vocabulary, onOpen }) {
  if (!plans.length) {
    return (
      <NothingYet
        title="Nothing is waiting on a person"
        description="Every plan is either out for signature with its approver cleared, or finished. A plan appears here only while its approver has not approved."
      />
    )
  }
  return (
    <div className="space-y-4">
      {plans.map((plan) => (
        <Card key={plan.id}>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <h3 className="font-display text-base font-semibold text-foreground">
                {plan.plan?.subject || plan.id}
              </h3>
              <MilestoneBadge
                milestone={plan.milestone}
                label={milestoneMeaning(vocabulary, plan.milestone)}
                tone={milestoneTone(vocabulary, plan.milestone)}
              />
            </div>
            <Button className="min-h-11" onClick={() => onOpen(plan.id)}>
              Open
            </Button>
          </div>
          <Notice tone="warning" title="Signers cannot sign yet">
            <ul className="list-disc space-y-1 pl-5">
              {(plan.blocking_approvers || []).map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          </Notice>
        </Card>
      ))}
    </div>
  )
}

/** Every plan in the room, with the two actions the milestone allows. */
function PlansTab({
  plans,
  vocabulary,
  loading,
  onOpen,
  onDistribute,
  onCancel,
  busy,
  onCreate,
}) {
  if (loading) return <Spinner label="Loading plans" />
  if (!plans.length) {
    return (
      <NothingYet
        title="No plans yet"
        description="A mutual action plan needs at least one signer. Build one and send it for signature."
        action={
          <Button variant="primary" icon="plus" className="min-h-11" onClick={onCreate}>
            Send a plan
          </Button>
        }
      />
    )
  }
  return (
    <div className="space-y-4">
      {plans.map((plan) => {
        const row = plan
        const view = row.recipients ? row : { ...row, recipients: [] }
        return (
          <Card key={row.id}>
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <h3 className="font-display text-base font-semibold text-foreground">
                  {row.plan?.subject || row.id}
                </h3>
                <p className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                  <MilestoneBadge
                    milestone={row.milestone}
                    label={milestoneMeaning(vocabulary, row.milestone)}
                    tone={milestoneTone(vocabulary, row.milestone)}
                  />
                  <MonoValue>{row.plan?.external_id}</MonoValue>
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button className="min-h-11" onClick={() => onOpen(row.id)}>
                  Open
                </Button>
                {row.milestone === 'draft' && (
                  <Button
                    variant="primary"
                    icon="plus"
                    className="min-h-11"
                    disabled={busy === `distribute-${row.id}`}
                    onClick={() => onDistribute(row)}
                  >
                    Distribute
                  </Button>
                )}
                {!isTerminal(row.milestone) && (
                  <Button
                    variant="danger"
                    className="min-h-11"
                    disabled={busy === `cancel-${row.id}`}
                    onClick={() => onCancel(row)}
                  >
                    Cancel
                  </Button>
                )}
              </div>
            </div>
            {row.milestone === 'draft' && (
              <p className="mt-3 text-sm text-muted-foreground">
                Not sent yet. Distributing moves the plan from draft to awaiting signatures and
                gives every recipient a signing link.
              </p>
            )}
            {view.recipients.length > 0 && (
              <div className="mt-3 flex flex-wrap gap-2">
                {view.recipients.map((recipient) => (
                  <span key={recipient.id} className="inline-flex items-center gap-1.5">
                    <RoleBadge role={recipient.role} label={recipient.role} />
                    <span className="text-xs text-muted-foreground">{recipient.name}</span>
                    <RecipientStatus status={recipient.status} />
                  </span>
                ))}
              </div>
            )}
          </Card>
        )
      })}
    </div>
  )
}

/** The webhook log. Retries are shown with their attempt count, not collapsed away. */
function ActivityTab({ roomId, vocabulary }) {
  const [unreadOnly, setUnreadOnly] = useState(false)
  const events = useAsync(() => mapApi.events(roomId, { limit: 50 }), [roomId])
  const notices = useAsync(() => mapApi.notices(roomId, { unread_only: unreadOnly, limit: 50 }), [
    roomId,
    unreadOnly,
  ])
  const [busy, setBusy] = useState(false)

  async function acknowledge() {
    setBusy(true)
    try {
      await mapApi.acknowledge(roomId)
      notices.refetch()
    } finally {
      setBusy(false)
    }
  }

  if (events.loading || notices.loading) return <Spinner label="Loading activity" />
  if (events.error) return <ErrorNote error={events.error} onRetry={events.refetch} />

  const rows = events.data?.events || []
  const noteRows = notices.data?.notices || []

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <h2 className="font-display text-lg font-semibold text-foreground">Webhook events</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Every delivery the vendor made, including repeats. A retried event is applied once and
          its attempt count goes up.
        </p>
        {rows.length === 0 ? (
          <p className="mt-4 text-sm text-muted-foreground">
            No events yet. They arrive once the plan is distributed.
          </p>
        ) : (
          <ul className="mt-4 divide-y divide-border-subtle">
            {rows.map((row) => (
              <li key={row.id} className="flex flex-wrap items-baseline justify-between gap-2 py-2">
                <span className="min-w-0">
                  <Badge tone="neutral">
                    <span className="font-mono">{row.event}</span>
                  </Badge>
                  <span className="ml-2 text-sm text-muted-foreground">
                    {eventMeaning(vocabulary, row.event)}
                  </span>
                </span>
                <span className="font-mono text-xs text-muted-foreground">
                  {row.attempts > 1 ? `${row.attempts} deliveries` : row.received_at}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="font-display text-lg font-semibold text-foreground">Owner notices</h2>
          <Button className="min-h-11" disabled={busy} onClick={acknowledge}>
            Mark all read
          </Button>
        </div>
        <div className="mt-3">
          <Checkbox
            id="wf067-unread-only"
            label="Unread only"
            checked={unreadOnly}
            onChange={setUnreadOnly}
          />
        </div>
        {noteRows.length === 0 ? (
          <p className="mt-4 text-sm text-muted-foreground">Nothing to read.</p>
        ) : (
          <ul className="mt-4 divide-y divide-border-subtle">
            {noteRows.map((row) => (
              <li key={row.id} className="py-2">
                <p className="flex items-center gap-2 text-sm text-foreground">
                  {!row.read && <Badge tone="update">new</Badge>}
                  <span>{vocabulary?.reasons?.[row.reason] || row.reason}</span>
                </p>
                {row.detail && <p className="mt-1 text-xs text-muted-foreground">{row.detail}</p>}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  )
}

/** The inference register, rendered verbatim. */
function DecisionsTab({ state }) {
  if (state.loading) return <Spinner label="Loading the decision register" />
  if (state.error) return <ErrorNote error={state.error} onRetry={state.refetch} />
  const decisions = state.data?.decisions || []
  return (
    <Card>
      <h2 className="font-display text-lg font-semibold text-foreground">
        Decisions this workflow rests on
      </h2>
      <p className="mt-1 text-sm text-muted-foreground">
        The research describes the vendor in detail and says almost nothing about what the room
        should do with it. Each entry below names what the research fixed, what it left open, the
        reading this build took, and what would go wrong otherwise.
      </p>
      <ul className="mt-4 space-y-4">
        {decisions.map((entry) => (
          <li key={entry.id} className="rounded-sm border border-border-subtle p-4">
            <p className="text-sm font-semibold text-foreground">{entry.question}</p>
            {entry.research_says && (
              <p className="mt-2 text-sm text-muted-foreground">
                <span className="font-medium text-foreground">Research: </span>
                {entry.research_says}
              </p>
            )}
            <p className="mt-2 text-sm text-muted-foreground">
              <span className="font-medium text-foreground">This build: </span>
              {entry.reading}
            </p>
            <p className="mt-2 text-sm text-muted-foreground">
              <span className="font-medium text-foreground">Otherwise: </span>
              {entry.otherwise}
            </p>
            <p className="mt-2 text-xs text-muted-foreground">
              <span className="font-medium">Changed by: </span>
              {entry.changeable_by}
            </p>
          </li>
        ))}
      </ul>
    </Card>
  )
}

/** One plan in full: its recipients, its gate, its links, and its history. */
function PlanDetail({ roomId, planId, vocabulary, onClose, onChanged }) {
  const plan = useAsync(() => mapApi.plan(roomId, planId), [roomId, planId])
  const events = useAsync(() => mapApi.events(roomId, { plan_id: planId }), [roomId, planId])
  const [busy, setBusy] = useState('')
  const [error, setError] = useState(null)

  async function run(name, action) {
    setBusy(name)
    setError(null)
    try {
      await action()
      plan.refetch()
      events.refetch()
      onChanged()
    } catch (caught) {
      setError(caught)
    } finally {
      setBusy('')
    }
  }

  const view = plan.data
  const reason = vocabulary?.reasons?.[view?.milestone]
  const refusalLine = (view?.recipients || []).find((entry) => entry.rejection_reason)

  return (
    <Modal open title={view?.plan?.subject || 'Plan'} onClose={onClose}>
      {plan.loading && <Spinner label="Loading the plan" />}
      {plan.error && <ErrorNote error={plan.error} onRetry={plan.refetch} />}
      {view && (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <MilestoneBadge
              milestone={view.milestone}
              label={milestoneMeaning(vocabulary, view.milestone)}
              tone={milestoneTone(vocabulary, view.milestone)}
            />
            <Badge tone="neutral">
              <span className="font-mono">{view.status}</span>
            </Badge>
            {isRefusal(view.milestone) && (
              <Notice tone="danger" title="This plan was refused">
                {reason}
                {refusalLine && (
                  <p className="mt-1">
                    {refusalLine.name} said: {refusalLine.rejection_reason}
                  </p>
                )}
              </Notice>
            )}
          </div>

          {!view.signing_unlocked && view.milestone === 'awaiting_signature' && (
            <Notice tone="warning" title="Signers cannot sign yet">
              <ul className="list-disc space-y-1 pl-5">
                {(view.blocking_approvers || []).map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            </Notice>
          )}

          <div>
            <h3 className="font-display text-base font-semibold text-foreground">Recipients</h3>
            <ul className="mt-2 space-y-2">
              {(view.recipients || []).map((recipient) => (
                <li
                  key={recipient.id}
                  className="flex flex-wrap items-center justify-between gap-2 rounded-sm border border-border-subtle p-3"
                >
                  <span className="min-w-0">
                    <span className="block text-sm text-foreground">{recipient.name}</span>
                    <span className="block font-mono text-xs text-muted-foreground">
                      {recipient.email}
                    </span>
                  </span>
                  <span className="flex items-center gap-2">
                    <RoleBadge role={recipient.role} label={recipient.role} />
                    <RecipientStatus status={recipient.status} />
                  </span>
                </li>
              ))}
            </ul>
          </div>

          <div>
            <h3 className="font-display text-base font-semibold text-foreground">Envelope</h3>
            <div className="mt-2">
              <Row label="Join key" mono>
                {view.plan?.external_id}
              </Row>
              <Row label="Signing order">{view.plan?.signing_order}</Row>
              <Row label="Invite path">{view.links?.invite_path}</Row>
              <Row label="Signing link" mono>
                {view.links?.signing_url}
              </Row>
              <Row label="In-room embed" mono>
                {view.links?.embed_url}
              </Row>
              <Row label="Distributed">{view.plan?.distributed_at || 'not yet'}</Row>
            </div>
          </div>

          {error && <Notice tone="danger" title="That did not work">{refusalSummary(error)}</Notice>}

          <div className="flex flex-wrap gap-2">
            {view.milestone === 'draft' && (
              <Button
                variant="primary"
                icon="plus"
                className="min-h-11"
                disabled={busy === 'distribute'}
                onClick={() => run('distribute', () => mapApi.distribute(roomId, planId))}
              >
                Distribute
              </Button>
            )}
            {!isTerminal(view.milestone) && (
              <Button
                variant="danger"
                className="min-h-11"
                disabled={busy === 'cancel'}
                onClick={() => run('cancel', () => mapApi.cancel(roomId, planId))}
              >
                Cancel the plan
              </Button>
            )}
          </div>

          <div>
            <h3 className="font-display text-base font-semibold text-foreground">Events</h3>
            {(events.data?.events || []).length === 0 ? (
              <p className="mt-2 text-sm text-muted-foreground">No events for this plan yet.</p>
            ) : (
              <ul className="mt-2 space-y-1">
                {(events.data?.events || []).map((row) => (
                  <li key={row.id} className="flex flex-wrap justify-between gap-2 text-sm">
                    <span className="font-mono text-xs">{row.event}</span>
                    <span className="text-xs text-muted-foreground">
                      {row.attempts > 1 ? `${row.attempts} deliveries` : row.received_at}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </>
      )}
    </Modal>
  )
}

/**
 * Compose a plan.
 *
 * The recipient rows are the researched shape: each carries `email`, `name`,
 * `role` and `fields[]`, and at least one of them has to be a signer or an
 * approver. The approver row is offered because the research makes that role the
 * one with a gate, and a seller who wants a gated plan should not have to know
 * the role name to type it.
 */
function ComposePlan({ roomId, vocabulary, onClose, onCreated }) {
  const roles = vocabulary?.roles || []
  const [subject, setSubject] = useState('')
  const [message, setMessage] = useState('')
  const [secret, setSecret] = useState('')
  const [invitePath, setInvitePath] = useState('embed')
  const [recipients, setRecipients] = useState([
    { email: '', name: '', role: 'SIGNER', party: 'buyer' },
  ])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  function update(index, patch) {
    setRecipients((rows) => rows.map((row, at) => (at === index ? { ...row, ...patch } : row)))
  }

  async function submit(event) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await mapApi.createPlan(roomId, {
        subject,
        message,
        webhook_secret: secret,
        invite_path: invitePath,
        recipients: recipients
          .filter((row) => row.email.trim())
          .map((row) => ({
            email: row.email.trim(),
            name: row.name.trim() || row.email.trim(),
            role: row.role,
            party: row.party,
            fields:
              row.role === 'SIGNER'
                ? [{ type: 'SIGNATURE', positionX: 10, positionY: 60, width: 25, height: 6 }]
                : [],
          })),
      })
      onCreated()
    } catch (caught) {
      setError(caught)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal open title="Send a mutual action plan" onClose={onClose}>
      <form onSubmit={submit} className="space-y-4">
        <Field id="wf067-subject" label="Subject">
          <TextInput
            id="wf067-subject"
            value={subject}
            onChange={setSubject}
            placeholder="Mutual action plan - enterprise rollout"
          />
        </Field>

        <Field
          id="wf067-message"
          label="Message for the recipients"
          hint="Shown to everyone the plan goes to."
        >
          <TextInput id="wf067-message" value={message} onChange={setMessage} />
        </Field>

        <Field
          id="wf067-secret"
          label="Webhook secret"
          hint="The vendor sends this in the X-Documenso-Secret header. Without it no event can be authenticated for this plan."
        >
          <TextInput id="wf067-secret" value={secret} onChange={setSecret} />
        </Field>

        <Field
          id="wf067-invite"
          label="How recipients open the plan"
          hint={
            vocabulary?.invite_paths?.find((row) => row.path === invitePath)?.label ||
            'Open the plan inside this sales room'
          }
        >
          <Select
            id="wf067-invite"
            value={invitePath}
            onChange={setInvitePath}
            options={(vocabulary?.invite_paths || []).map((row) => ({
              value: row.path,
              label: row.label,
            }))}
          />
        </Field>

        <fieldset className="space-y-3">
          <legend className="text-[13px] font-medium text-foreground">Recipients</legend>
          {recipients.map((row, index) => (
            <div key={index} className="rounded-sm border border-border-subtle p-3">
              <div className="grid gap-3 sm:grid-cols-2">
                <Field id={`wf067-email-${index}`} label="Email">
                  <TextInput
                    id={`wf067-email-${index}`}
                    type="email"
                    value={row.email}
                    onChange={(value) => update(index, { email: value })}
                  />
                </Field>
                <Field id={`wf067-name-${index}`} label="Name">
                  <TextInput
                    id={`wf067-name-${index}`}
                    value={row.name}
                    onChange={(value) => update(index, { name: value })}
                  />
                </Field>
              </div>
              <div className="mt-3">
                <Field
                  id={`wf067-role-${index}`}
                  label="Role"
                  hint={roles.find((entry) => entry.role === row.role)?.meaning}
                >
                  <Select
                    id={`wf067-role-${index}`}
                    value={row.role}
                    onChange={(value) => update(index, { role: value })}
                    options={roles.map((entry) => ({ value: entry.role, label: entry.label }))}
                  />
                </Field>
              </div>
              {recipients.length > 1 && (
                <button
                  type="button"
                  className="mt-3 min-h-11 text-sm text-destructive"
                  onClick={() =>
                    setRecipients((rows) => rows.filter((_, at) => at !== index))
                  }
                >
                  Remove this recipient
                </button>
              )}
            </div>
          ))}
          <Button
            className="min-h-11"
            icon="plus"
            onClick={() =>
              setRecipients((rows) => [...rows, { email: '', name: '', role: 'CC', party: 'seller' }])
            }
          >
            Add a recipient
          </Button>
        </fieldset>

        {error && <Notice tone="danger" title="That plan was refused">{refusalSummary(error)}</Notice>}

        <div className="flex flex-wrap gap-2">
          <Button type="submit" variant="primary" className="min-h-11" disabled={busy}>
            {busy ? 'Creating' : 'Create the plan'}
          </Button>
          <Button className="min-h-11" onClick={onClose}>
            Cancel
          </Button>
        </div>
      </form>
    </Modal>
  )
}