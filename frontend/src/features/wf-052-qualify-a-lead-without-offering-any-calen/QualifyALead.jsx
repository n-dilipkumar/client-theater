/**
 * Qualify a lead without offering any calendar (WF-052).
 *
 * The page is built around the one guarantee this workflow makes: evaluating a
 * router's rules writes nothing. The counters the backend returns sit next to
 * every answer, so a reader sees `routing_sessions_consumed: 0` rather than being
 * told it in prose, and the record button is a separate, deliberate act.
 *
 * `apiRequest` throws with the response status attached, which is what lets this
 * page tell a 400 (the body carried an interval) apart from a 404 (that router
 * is not declared) instead of showing one generic failure.
 */

import { useState } from 'react'
import {
  Button,
  Card,
  EmptyState,
  ErrorNote,
  Field,
  inputClass,
  Spinner,
  StatCard,
  useAsync,
} from '@/components/ui'
import { api } from '@/lib/api'
import { leadQualificationApi } from './api'
import {
  DataTable,
  Fact,
  Glyph,
  Notice,
  SchedulingBadge,
  Section,
  VerdictBadge,
} from './primitives'

const GUARANTEE_LABELS = {
  consumes_no_session: 'No routing session is consumed',
  queries_no_availability: 'Availability is not queried',
  computes_no_slots: 'No slot list is computed',
  fires_no_automation: 'No automation fires',
  assigns_nobody: 'Nobody is assigned',
  reads_no_calendar: 'No calendar is read',
}

const EMPTY_FORM = { email: '', company: '', seats: '', rating: '' }

const CHAIN_HINT =
  'Declare a router and an assignee first. A rule needs an owner, and a chain needs a catch-all.'

function RouterChain({ router }) {
  const rules = router.data?.rules || []
  return (
    <ol className="flex flex-col gap-2">
      {rules.map((rule, index) => (
        <li key={`${router.id}-${index}`} className="rounded-sm border border-border-subtle p-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-xs text-muted-foreground">{index + 1}</span>
            <span className="text-[13px] font-medium text-foreground">{rule.name || 'unnamed rule'}</span>
            <span className="rounded-xs border border-border-subtle bg-muted px-2 py-0.5 font-mono text-xs text-muted-foreground">
              {rule.kind}
            </span>
            {rule.scheduling_allowed === false && (
              <span className="rounded-xs border border-warning/30 bg-warning/10 px-2 py-0.5 font-mono text-xs text-warning">
                no scheduler
              </span>
            )}
          </div>
          <p className="mt-1 font-mono text-xs text-muted-foreground">
            proposes {rule.assign_user_id || 'nobody'}
            {(rule.conditions || []).length > 0 && (
              <>
                {' when '}
                {rule.conditions
                  .map(
                    (condition) =>
                      `${condition.source}.${condition.field} ${condition.operator} ${
                        condition.value === undefined ? 'is present' : condition.value
                      }`,
                  )
                  .join(' and ')}
              </>
            )}
          </p>
        </li>
      ))}
    </ol>
  )
}

function AnswerCard({ answer }) {
  const effects = answer.side_effects || {}
  return (
    <Card className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <VerdictBadge verdict={answer.verdict} />
        <SchedulingBadge allowed={answer.scheduling_allowed} />
        <span className="font-mono text-xs text-muted-foreground">{answer.reason}</span>
      </div>
      <dl className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Fact term="Proposed owner" mono>
          {answer.assignment?.userId || 'nobody'}
        </Fact>
        <Fact term="Assignment type" mono>
          {answer.assignment?.type || 'unassigned'}
        </Fact>
        <Fact term="Rules evaluated" mono>
          {answer.evaluated_rules}
        </Fact>
        <Fact term="routeId" mono>
          {answer.route_id}
        </Fact>
        <Fact term="routingLink" mono>
          <span className="break-all">{answer.routing_link}</span>
        </Fact>
        <Fact term="Rows written" mono>
          {effects.records_written ?? 0}
        </Fact>
        <Fact term="Sessions consumed" mono>
          {effects.routing_sessions_consumed ?? 0}
        </Fact>
        <Fact term="Availability queries" mono>
          {effects.availability_queries ?? 0}
        </Fact>
      </dl>
      {answer.conditions?.length > 0 && (
        <div>
          <p className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
            Conditions read
          </p>
          <ul className="mt-1 flex flex-col gap-1">
            {answer.conditions.map((condition) => (
              <li key={`${condition.source}-${condition.field}`} className="font-mono text-xs text-foreground">
                {condition.source}.{condition.field} = {String(condition.actual)} matched{' '}
                {String(condition.matched)}
              </li>
            ))}
          </ul>
        </div>
      )}
      {answer.crm_writeback && (
        <Notice tone="warning" title="A CRM writeback is staged, not applied">
          The rule that matched refuses a scheduler, so the row carries what a writeback node would
          write. Nothing pushed it, because no automation fires beyond rule evaluation.
        </Notice>
      )}
    </Card>
  )
}

export default function QualifyALead() {
  const summaryState = useAsync(() => leadQualificationApi.summary(), [])
  const routersState = useAsync(() => leadQualificationApi.routers(), [])
  const assigneesState = useAsync(() => leadQualificationApi.assignees(), [])
  const verdictsState = useAsync(() => leadQualificationApi.verdicts({ limit: 25 }), [])
  const roomsState = useAsync(() => api.listRecords('room', { limit: 50 }), [])

  const [routerSlug, setRouterSlug] = useState('')
  const [roomId, setRoomId] = useState('')
  const [form, setForm] = useState(EMPTY_FORM)
  const [answer, setAnswer] = useState(null)
  const [message, setMessage] = useState(null)
  const [busy, setBusy] = useState(false)

  const routers = routersState.data?.routers || []
  const assignees = assigneesState.data?.assignees || []
  const rooms = roomsState.data?.records || []
  const chosenRouter = routerSlug || routers[0]?.data?.router_slug || ''
  const chosenRoom = roomId || rooms[0]?.id || ''

  const refetchAll = () => {
    summaryState.refetch()
    routersState.refetch()
    assigneesState.refetch()
    verdictsState.refetch()
  }

  const payload = () => {
    const body = {
      form: { email: form.email, company: form.company },
      crm: form.rating ? { lead: { rating: form.rating } } : {},
    }
    if (form.seats !== '') body.form.seats = Number(form.seats)
    return body
  }

  const onQualify = async () => {
    if (!chosenRouter) {
      setMessage({ tone: 'warning', title: 'Declare a router first', text: CHAIN_HINT })
      return
    }
    setBusy(true)
    setMessage(null)
    try {
      const body = await leadQualificationApi.qualify(payload(), { router_slug: chosenRouter })
      setAnswer(body)
      setMessage({
        tone: body.verdict === 'qualified' ? 'success' : 'warning',
        title: `${body.verdict.replace('_', ' ')}`,
        text: body.reason,
      })
    } catch (error) {
      setAnswer(null)
      setMessage({ tone: 'danger', title: 'Qualification refused', text: String(error.message || error) })
    } finally {
      setBusy(false)
    }
  }

  const onRecord = async () => {
    if (!chosenRouter || !chosenRoom) {
      setMessage({ tone: 'warning', title: 'Pick a router and a room', text: CHAIN_HINT })
      return
    }
    setBusy(true)
    setMessage(null)
    try {
      const row = await leadQualificationApi.recordVerdict(chosenRoom, payload(), {
        router_slug: chosenRouter,
      })
      refetchAll()
      setMessage({
        tone: 'success',
        title: 'Verdict recorded',
        text: `One row kept in room ${row.room_id}. No session was consumed and no calendar was read.`,
      })
    } catch (error) {
      setMessage({ tone: 'danger', title: 'Recording refused', text: String(error.message || error) })
    } finally {
      setBusy(false)
    }
  }

  if (
    summaryState.loading ||
    routersState.loading ||
    assigneesState.loading ||
    verdictsState.loading ||
    roomsState.loading
  ) {
    return <Spinner label="Loading lead qualification" />
  }
  if (summaryState.error) return <ErrorNote error={summaryState.error} onRetry={summaryState.refetch} />
  if (routersState.error) return <ErrorNote error={routersState.error} onRetry={routersState.refetch} />
  if (assigneesState.error) {
    return <ErrorNote error={assigneesState.error} onRetry={assigneesState.refetch} />
  }
  if (verdictsState.error) return <ErrorNote error={verdictsState.error} onRetry={verdictsState.refetch} />
  if (roomsState.error) return <ErrorNote error={roomsState.error} onRetry={roomsState.refetch} />

  const summary = summaryState.data || {}
  const verdicts = verdictsState.data?.verdicts || []
  const guarantees = Object.entries(summary.side_effects || {})

  return (
    <div className="flex flex-col gap-6">
      <Section
        title="Qualify a lead without offering any calendar"
        hint="Runs a Concierge router's rules against a form or lead payload and answers with the proposed owner, the schedulingAllowed signal and a routingLink. It queries no availability and consumes no routing session."
      >
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard label="Routers" value={summary.routers ?? 0} hint="declared chains" />
          <StatCard label="Assignees" value={summary.assignees ?? 0} hint="users a rule may propose" />
          <StatCard
            label="Recorded"
            value={summary.verdicts ?? 0}
            hint={`${summary.scheduling_allowed ?? 0} may open a scheduler`}
          />
          <StatCard
            label="Refused a scheduler"
            value={summary.scheduling_refused ?? 0}
            hint="gated off on schedulingAllowed"
          />
        </div>
      </Section>

      <Section
        title="What this workflow does not do"
        hint="The researched guarantee, counted rather than promised."
      >
        <Card>
          <ul className="grid gap-2 sm:grid-cols-2">
            {Object.entries(GUARANTEE_LABELS).map(([key, label]) => (
              <li key={key} className="flex items-center gap-2 text-[13px] text-foreground">
                <Glyph name="gate" size={16} className="text-accent" />
                {label}
              </li>
            ))}
          </ul>
          {guarantees.length > 0 && (
            <dl className="mt-4 grid gap-4 sm:grid-cols-3">
              {guarantees.map(([key, value]) => (
                <Fact key={key} term={key.replace(/_/g, ' ')} mono>
                  {value}
                </Fact>
              ))}
            </dl>
          )}
        </Card>
      </Section>

      <Section
        title="Run the router"
        hint="The form below is the researched call: form data, and deliberately no interval. A body carrying an interval is refused with 400."
      >
        <Card>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <Field label="Router" hint="which chain to run" id="wf052-router">
              <select
                id="wf052-router"
                className={inputClass}
                value={chosenRouter}
                onChange={(event) => setRouterSlug(event.target.value)}
              >
                {routers.length === 0 && <option value="">no router declared</option>}
                {routers.map((router) => (
                  <option key={router.id} value={router.data.router_slug}>
                    {router.data.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Room" hint="only needed to keep the verdict" id="wf052-room">
              <select
                id="wf052-room"
                className={inputClass}
                value={chosenRoom}
                onChange={(event) => setRoomId(event.target.value)}
              >
                {rooms.length === 0 && <option value="">no room yet</option>}
                {rooms.map((room) => (
                  <option key={room.id} value={room.id}>
                    {room.data?.name || room.data?.account || room.id}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Email" hint="any value qualifies; no address is required" id="wf052-email">
              <input
                id="wf052-email"
                className={inputClass}
                value={form.email}
                onChange={(event) => setForm({ ...form, email: event.target.value })}
              />
            </Field>
            <Field label="Company" id="wf052-company">
              <input
                id="wf052-company"
                className={inputClass}
                value={form.company}
                onChange={(event) => setForm({ ...form, company: event.target.value })}
              />
            </Field>
            <Field label="Seats" hint="the Data Field a rule reads" id="wf052-seats">
              <input
                id="wf052-seats"
                className={inputClass}
                inputMode="numeric"
                value={form.seats}
                onChange={(event) => setForm({ ...form, seats: event.target.value })}
              />
            </Field>
            <Field label="CRM lead rating" hint="a CRM value the caller passes in" id="wf052-rating">
              <select
                id="wf052-rating"
                className={inputClass}
                value={form.rating}
                onChange={(event) => setForm({ ...form, rating: event.target.value })}
              >
                <option value="">not supplied</option>
                <option value="hot">hot</option>
                <option value="warm">warm</option>
                <option value="cold">cold</option>
              </select>
            </Field>
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            <Button variant="primary" icon="search" disabled={busy} onClick={onQualify}>
              Qualify this lead
            </Button>
            <Button disabled={busy} onClick={onRecord}>
              Qualify and record it
            </Button>
          </div>
        </Card>
      </Section>

      {message && (
        <Notice tone={message.tone} title={message.title}>
          {message.text}
        </Notice>
      )}

      {answer && <AnswerCard answer={answer} />}

      <Section title="Routers" hint="Each chain must end with a catch-all, so every inbound lead is acknowledged.">
        {routers.length === 0 ? (
          <EmptyState
            title="No router declared yet"
            description="A router is a slug, a name and an ordered chain. The chain ends with a catch-all."
          />
        ) : (
          <div className="grid gap-4">
            {routers.map((router) => (
              <Card key={router.id} className="flex flex-col gap-3">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-sm font-semibold text-foreground">{router.data.name}</p>
                    <p className="mt-0.5 font-mono text-xs text-muted-foreground">
                      {router.data.router_slug} · {router.data.tenant}
                    </p>
                  </div>
                  <span className="rounded-xs border border-border-subtle bg-muted px-2 py-0.5 font-mono text-xs text-muted-foreground">
                    {router.data.enabled ? 'published' : 'unpublished'}
                  </span>
                </div>
                {router.data.notes && (
                  <p className="text-xs text-muted-foreground">{router.data.notes}</p>
                )}
                <RouterChain router={router} />
              </Card>
            ))}
          </div>
        )}
      </Section>

      <Section
        title="Recorded verdicts"
        hint="One row per kept qualification, with the rule that held and the signal it produced."
      >
        <DataTable
          columns={[
            {
              key: 'verdict',
              header: 'Verdict',
              render: (row) => <VerdictBadge verdict={row.data.verdict} />,
            },
            {
              key: 'signal',
              header: 'Signal',
              render: (row) => <SchedulingBadge allowed={row.data.scheduling_allowed} />,
            },
            {
              key: 'owner',
              header: 'Proposed owner',
              render: (row) => <span className="font-mono text-xs">{row.data.assignment?.userId || 'nobody'}</span>,
            },
            {
              key: 'rule',
              header: 'Rule',
              render: (row) => <span className="text-xs">{row.data.matched_rule?.name || 'none'}</span>,
            },
            {
              key: 'router',
              header: 'Router',
              render: (row) => <span className="font-mono text-xs">{row.data.router_slug}</span>,
            },
            {
              key: 'when',
              header: 'Evaluated',
              render: (row) => <span className="font-mono text-xs text-muted-foreground">{row.data.evaluated_at}</span>,
            },
          ]}
          rows={verdicts}
          rowKey={(row) => row.id}
          empty="Nothing recorded yet. Qualify a lead and keep it with the second button above."
        />
      </Section>

      <Section
        title="Users a rule may propose"
        hint="A rule naming an id that is not here answers unroutable rather than qualified."
      >
        <DataTable
          columns={[
            {
              key: 'user',
              header: 'User id',
              render: (row) => <span className="font-mono text-xs">{row.data.user_id}</span>,
            },
            { key: 'name', header: 'Name', render: (row) => <span className="text-[13px]">{row.data.name}</span> },
            {
              key: 'team',
              header: 'Team',
              render: (row) => <span className="text-xs text-muted-foreground">{row.data.team || 'none'}</span>,
            },
          ]}
          rows={assignees}
          rowKey={(row) => row.id}
          empty="No assignee declared yet. Every rule needs one."
        />
      </Section>
    </div>
  )
}
