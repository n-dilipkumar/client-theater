/**
 * Conversation tickets (WF-110).
 *
 * One page, in the order the researched flow runs in:
 *
 * 1. The two triggers: "A ticket is created" and "Teammate changes the state of a
 *    ticket", each with its conditions and its background actions.
 * 2. The tickets, each with its type, state, owning team and converted flag.
 * 3. What the workflow emitted: the queued owning-team notifications and the
 *    `ticket.*` webhooks, each with its payload shape.
 * 4. The empty and non-success states the workflow exists to make visible: a
 *    ticket created with no owning-team notification, and an action skipped
 *    because the ticket was not converted.
 *
 * Three things this page states rather than hides: `resolved` and `closed` are two
 * different events, `ticket.closed` has a different payload shape, and a tracker
 * ticket never fires the trigger. Each is labelled in words, not colour.
 *
 * Design floor: every control is `min-h-11`, every glyph sits beside a text label,
 * every status is carried by words rather than by colour alone, and the loading,
 * error and empty branches are all present.
 */

import { useState } from 'react'
import {
  Badge,
  Card,
  EmptyState,
  ErrorNote,
  Spinner,
  StatCard,
  useAsync,
} from '@/components/ui'
import {
  actionLabel,
  actionOutcomeLabel,
  shapeLabel,
  skipReasonLabel,
  stateBadge,
  ticketApi,
  triggerKindLabel,
  viaLabel,
} from './api'
import { ActionRow, Fact, Notice, Quote, Subhead, TicketRow, WebhookRow } from './primitives'

function formatTime(value) {
  if (!value) return '--'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return String(value)
  return parsed.toISOString().replace('T', ' ').slice(0, 16)
}

export default function ConversationTicket() {
  const [selected, setSelected] = useState(null)
  const [actionError, setActionError] = useState(null)
  const [busy, setBusy] = useState(false)

  const summary = useAsync(() => ticketApi.summary(), [])
  const catalog = useAsync(() => ticketApi.catalog(), [])
  const tickets = useAsync(() => ticketApi.tickets({ limit: 50 }), [])
  const evaluations = useAsync(() => ticketApi.evaluations({ limit: 50 }), [])
  const notifications = useAsync(() => ticketApi.notifications({ limit: 50 }), [])
  const webhooks = useAsync(() => ticketApi.webhooks({ limit: 50 }), [])
  const vocabulary = useAsync(() => ticketApi.vocabulary(), [])
  const inferences = useAsync(() => ticketApi.inferences(), [])

  const reload = () => {
    summary.refetch()
    catalog.refetch()
    tickets.refetch()
    evaluations.refetch()
    notifications.refetch()
    webhooks.refetch()
  }

  if (summary.loading || catalog.loading || tickets.loading) {
    return <Spinner label="Loading conversation tickets" />
  }
  if (summary.error) return <ErrorNote error={summary.error} onRetry={summary.refetch} />
  if (catalog.error) return <ErrorNote error={catalog.error} onRetry={catalog.refetch} />
  if (tickets.error) return <ErrorNote error={tickets.error} onRetry={tickets.refetch} />

  const counts = summary.data || {}
  const triggers = catalog.data?.triggers || []
  const rows = tickets.data?.tickets || []
  const current = rows.find((entry) => entry.id === selected) || null
  const notificationRows = notifications.data?.notifications || []
  const webhookRows = webhooks.data?.webhooks || []
  const evaluationRows = evaluations.data?.evaluations || []
  const gateSkips = evaluationRows
    .flatMap((record) => record.data?.actions || [])
    .filter((action) => action.outcome === 'skipped_not_converted')
  const propositions = vocabulary.data || {}
  const decisions = inferences.data?.inferences || []

  async function resolveTicket() {
    if (!current) {
      setActionError('Choose a ticket before resolving it.')
      return
    }
    setBusy(true)
    setActionError(null)
    try {
      await ticketApi.changeState(current.id, 'resolved')
      reload()
    } catch (error) {
      setActionError(error.message || String(error))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-6">
      <header>
        <h1 className="font-display text-2xl font-semibold text-foreground">Conversation tickets</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          A converted conversation becomes a ticket that an owning team holds. The trigger fires its
          background actions, and a second trigger fires when a teammate changes the state.
        </p>
      </header>

      <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Tickets"
          value={counts.tickets ?? 0}
          hint={`${counts.converted ?? 0} converted from a conversation`}
          icon="rooms"
        />
        <StatCard
          label="Notified"
          value={counts.notified_tickets ?? 0}
          hint="Owning team told at creation or on a state change"
          icon="audit"
        />
        <StatCard
          label="No notification"
          value={counts.tickets_without_a_notification ?? 0}
          hint="Created without an owning-team notification"
          icon="restore"
        />
        <StatCard
          label="Live triggers"
          value={`${counts.live_triggers ?? 0}/${counts.triggers ?? 0}`}
          hint="A draft fires nothing"
          icon="schema"
        />
      </section>

      {(counts.tickets_without_a_notification ?? 0) > 0 && (
        <Notice tone="warning">
          {counts.tickets_without_a_notification} ticket(s) were created and produced no owning-team
          notification. A tracker-created ticket and a ticket outside the trigger's ticket types are
          the two expected cases.
        </Notice>
      )}

      <section className="grid gap-6 lg:grid-cols-2">
        <Card>
          <Subhead count={triggers.length}>The two triggers</Subhead>
          {triggers.length === 0 ? (
            <EmptyState
              title="No trigger yet"
              description="A ticket-created trigger escalates; a state-changed trigger tells the owner."
            />
          ) : (
            <ul className="space-y-4">
              {triggers.map((trigger) => (
                <li key={trigger.id} className="rounded-sm border border-border-subtle p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-semibold text-foreground">{trigger.data.name}</span>
                    <Badge tone={trigger.data.state === 'live' ? 'insert' : 'restore'}>
                      {trigger.data.state === 'live' ? 'Live' : 'Draft'}
                    </Badge>
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {triggerKindLabel(trigger.data.kind)}
                    {trigger.data.channel ? ` / ${trigger.data.channel}` : ''}
                  </p>
                  <ul className="mt-2">
                    {(trigger.data.actions || []).map((action, index) => (
                      <ActionRow
                        key={`${trigger.id}-${index}`}
                        action={action}
                        actionLabel={actionLabel}
                        outcomeLabel={actionOutcomeLabel}
                      />
                    ))}
                  </ul>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card>
          <Subhead count={rows.length}>Tickets</Subhead>
          {rows.length === 0 ? (
            <EmptyState
              title="No ticket yet"
              description="Convert a conversation or create a ticket to fire the trigger."
            />
          ) : (
            <ul className="space-y-2">
              {rows.map((ticket) => (
                <TicketRow
                  key={ticket.id}
                  ticket={ticket}
                  selected={ticket.id === selected}
                  onSelect={setSelected}
                />
              ))}
            </ul>
          )}
          {actionError && (
            <div className="mt-3">
              <Notice tone="danger">{actionError}</Notice>
            </div>
          )}
        </Card>
      </section>

      {current && (
        <Card>
          <Subhead>Ticket {current.subject}</Subhead>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            <Fact label="id">{current.id}</Fact>
            <Fact label="type">{current.ticket_type}</Fact>
            <Fact label="state">{stateBadge(current.state).label}</Fact>
            <Fact label="team">{current.owning_team || 'no team'}</Fact>
            <Fact label="created via">{viaLabel(current.created_via)}</Fact>
            <Fact label="conversation">{current.conversation_id || 'none'}</Fact>
            <Fact label="sla">
              {current.sla ? `${current.sla.policy} due ${formatTime(current.sla.due_at)}` : 'none'}
            </Fact>
            <Fact label="contacts">{String((current.contacts || []).length)}</Fact>
            <Fact label="tags">{String((current.tags || []).length)}</Fact>
          </div>
          <div className="mt-3">
            <button
              type="button"
              onClick={resolveTicket}
              disabled={busy}
              className="inline-flex min-h-11 items-center rounded-sm border border-border-subtle bg-surface px-4 text-sm text-foreground hover:border-accent disabled:opacity-50"
            >
              {busy ? 'Resolving…' : 'Mark resolved'}
            </button>
          </div>
        </Card>
      )}

      <section className="grid gap-6 lg:grid-cols-2">
        <Card>
          <Subhead count={notificationRows.length}>Owning-team notifications</Subhead>
          <p className="mb-2 text-xs text-muted-foreground">
            Queued, not delivered by this product. Slack delivery belongs to the Slack surface.
          </p>
          {notificationRows.length === 0 ? (
            <EmptyState title="Nothing queued" description="No trigger notified an owning team." />
          ) : (
            <ul className="space-y-2">
              {notificationRows.map((record) => (
                <li
                  key={record.id}
                  className="flex min-h-11 items-center gap-3 border-b border-border-subtle py-2 last:border-b-0"
                >
                  <Badge tone="insert">{record.data.state}</Badge>
                  <span className="min-w-0 flex-1 truncate font-mono text-xs text-foreground">
                    {record.data.channel}
                  </span>
                  <span className="font-mono text-xs text-muted-foreground">
                    ticket {record.data.ticket_id}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card>
          <Subhead count={webhookRows.length}>Webhooks</Subhead>
          <p className="mb-2 text-xs text-muted-foreground">
            ticket.closed nests the ticket under data.item.ticket. Every other topic puts the fields
            on data.item.
          </p>
          {webhookRows.length === 0 ? (
            <EmptyState title="No webhook yet" description="No ticket event has been emitted." />
          ) : (
            <ul>
              {webhookRows.map((record) => (
                <WebhookRow key={record.id} webhook={record} />
              ))}
            </ul>
          )}
        </Card>
      </section>

      <Card>
        <Subhead count={evaluationRows.length}>Trigger decisions</Subhead>
        {evaluationRows.length === 0 ? (
          <EmptyState title="No decision yet" description="No ticket event has been evaluated." />
        ) : (
          <ul className="space-y-2">
            {evaluationRows.map((record) => (
              <li
                key={record.id}
                className="flex min-h-11 flex-wrap items-center gap-3 border-b border-border-subtle py-2 last:border-b-0"
              >
                <Badge tone={record.data.matched ? 'insert' : 'restore'}>
                  {record.data.matched ? 'Fired' : 'Skipped'}
                </Badge>
                <span className="min-w-0 flex-1 text-sm text-foreground">
                  {record.data.trigger_name}
                </span>
                <span className="text-xs text-muted-foreground">
                  {skipReasonLabel(record.data.reason)}
                </span>
              </li>
            ))}
          </ul>
        )}
        {gateSkips.length > 0 && (
          <div className="mt-3">
            <Notice tone="info">
              {gateSkips.length} action(s) were skipped because the ticket was not converted from a
              conversation. Tag conversation and Apply SLA only run on a converted ticket.
            </Notice>
          </div>
        )}
      </Card>

      <section className="grid gap-6 lg:grid-cols-2">
        <Card>
          <Subhead>The researched limits</Subhead>
          <div className="space-y-3">
            {propositions.background_only && (
              <Quote source={propositions.background_only.source}>
                {propositions.background_only.quote}
              </Quote>
            )}
            {propositions.tracker_exemption && (
              <Quote source={propositions.tracker_exemption.source}>
                {propositions.tracker_exemption.quote}
              </Quote>
            )}
            {propositions.conversion_gate && (
              <Quote source={propositions.conversion_gate.source}>
                {propositions.conversion_gate.quote}
              </Quote>
            )}
          </div>
        </Card>

        <Card>
          <Subhead count={decisions.length}>Judgement calls</Subhead>
          <ul className="space-y-3">
            {decisions.map((entry) => (
              <li key={entry.id} className="rounded-sm border border-border-subtle p-3">
                <p className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
                  {entry.topic}
                </p>
                <p className="mt-1 text-sm text-foreground">{entry.decision}</p>
                <p className="mt-1 text-xs text-muted-foreground">{entry.change_if}</p>
              </li>
            ))}
          </ul>
          {inferences.data?.jev && (
            <p className="mt-3 font-mono text-xs text-muted-foreground">
              Jev {inferences.data.jev.audit_id} / {inferences.data.jev.verdict} /{' '}
              {inferences.data.jev.confidence}
            </p>
          )}
        </Card>
      </section>

      <Card>
        <Subhead>Payload shapes</Subhead>
        <ul className="space-y-1">
          {Object.entries(propositions.webhook_payload_shapes?.value || {}).map(([topic, shape]) => (
            <li key={topic} className="flex min-h-11 items-center gap-3">
              <span className="min-w-0 flex-1 truncate font-mono text-xs text-foreground">
                {topic}
              </span>
              <Badge tone="neutral">{shapeLabel(shape)}</Badge>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  )
}
