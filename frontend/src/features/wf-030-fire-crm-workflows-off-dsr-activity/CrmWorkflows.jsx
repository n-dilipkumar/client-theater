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
import {
  ActionRow,
  ActionabilityNote,
  FindingRow,
  MissRow,
  SkipRow,
  StatusMeaning,
  StatusPill,
  WORKFLOW_ICON,
} from './primitives'
import {
  familyLabel,
  isResolvableAction,
  refinementsFor,
  refinementLabel,
  workflowApi,
} from './api'

/**
 * CRM workflows, driven by DSR activity (WF-030).
 *
 * The page follows the researched flow in order, because the flow *is* the
 * product: verify the integration is on and the workspace is connected to a deal
 * (step 1), write a contact-based workflow whose trigger is "When filter criteria is
 * met" on one of the five filter families and refine it (steps 2 to 6), add actions
 * (step 7), publish it (step 8) - after which DSR activity drives it.
 *
 * Three things this page deliberately does not do, each because the research is
 * explicit and a page that implied otherwise would be lying:
 *
 * * it never suggests an enrollment is somebody's task. The note at the top is the
 *   research's own sentence, rendered every time;
 * * it never shows an action as done. Every resolved action says "not executed",
 *   because the write side is a CRM API this product has no credentials for and
 *   records what it would write instead;
 * * it never lets a filter be built that the server would refuse. The family
 *   picker and the refinement controls are both rendered from the served
 *   vocabulary, so the editor cannot offer a refinement the matrix does not
 *   publish.
 */

const ACTION_KIND_DEFAULT = 'send_email'

function emptyDraft() {
  return {
    name: '',
    description: '',
    family: 'downloads',
    refinements: { occurred: '', file_name: '', link_url: '', activity_text: '' },
    action_kind: ACTION_KIND_DEFAULT,
    field: '',
    value: '',
    template: '',
    channel: '',
    stage: '',
    stage_kind: 'deal_stage',
  }
}

function toPayload(draft) {
  const refinements = {}
  for (const [key, value] of Object.entries(draft.refinements)) {
    if (value !== '' && value !== undefined && value !== null) refinements[key] = value
  }
  const action = { kind: draft.action_kind }
  if (draft.action_kind === 'update_field') action.field = draft.field
  if (draft.action_kind === 'send_email') action.template = draft.template
  if (draft.action_kind === 'slack_notification') action.channel = draft.channel
  if (draft.action_kind === 'change_stage') {
    action.stage = draft.stage
    action.stage_kind = draft.stage_kind
  }
  return {
    name: draft.name,
    description: draft.description,
    // "Dock only supports Contact based workflows since the activities are tied to
    // the contact record." Sent explicitly rather than left to the default, so the
    // request a reader can copy says what the page means.
    enrollment_type: 'contact',
    trigger: {
      mode: 'filter_criteria_met',
      integration: 'hubspot',
      criteria: { family: draft.family, refinements },
    },
    actions: [action],
  }
}

function CrmWorkflowsPage() {
  const [roomId, setRoomId] = useState('')
  const [draft, setDraft] = useState(emptyDraft)
  const [notice, setNotice] = useState(null)
  const [busy, setBusy] = useState(false)

  const rooms = useAsync(() => workflowApi.rooms(), [])
  const vocabulary = useAsync(() => workflowApi.vocabulary(), [])
  const inferences = useAsync(() => workflowApi.inferences(), [])
  const integrations = useAsync(() => workflowApi.integrations(), [])
  const workflows = useAsync(() => workflowApi.workflows(), [])
  const summary = useAsync(
    () => (roomId ? workflowApi.summary(roomId) : Promise.resolve(null)),
    [roomId]
  )
  const enrollments = useAsync(
    () => (roomId ? workflowApi.enrollments(roomId) : Promise.resolve(null)),
    [roomId]
  )

  const selectedRoom = useMemo(
    () => (rooms.data?.records || []).find((room) => room.id === roomId) || null,
    [rooms.data, roomId]
  )

  const connectedRoomIds = useMemo(() => {
    const linked = new Set()
    for (const integration of integrations.data?.integrations || []) {
      for (const roomKey of Object.keys(integration.connections || {})) linked.add(roomKey)
    }
    return linked
  }, [integrations.data])

  if (vocabulary.loading || rooms.loading) return <Spinner label="Loading CRM workflows" />
  if (vocabulary.error) return <ErrorNote error={vocabulary.error} onRetry={vocabulary.refetch} />

  const voc = vocabulary.data
  const published = refinementsFor(voc, draft.family)

  async function refresh() {
    await Promise.all([
      workflows.refetch(),
      integrations.refetch(),
      summary.refetch(),
      enrollments.refetch(),
    ])
  }

  async function run(action) {
    setBusy(true)
    setNotice(null)
    try {
      const result = await action()
      setNotice({ tone: 'ok', text: result })
    } catch (error) {
      setNotice({ tone: 'error', text: String(error?.message || error) })
    } finally {
      setBusy(false)
      await refresh()
    }
  }

  function set(field, value) {
    setDraft((previous) => ({ ...previous, [field]: value }))
  }

  function setRefinement(name, value) {
    setDraft((previous) => ({
      ...previous,
      refinements: { ...previous.refinements, [name]: value },
    }))
  }

  return (
    <div className="flex flex-col gap-6">
      <ActionabilityNote note={voc.actionability_note} />

      {summary.data && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard
            label="Published workflows"
            value={summary.data.published}
            hint={`${summary.data.drafts} draft(s), ${summary.data.flagged} flagged`}
            icon="schema"
          />
          <StatCard
            label="Enrollments in this room"
            value={summary.data.enrollments}
            hint={`${summary.data.contacts} contact(s), ${summary.data.re_matched} re-matched`}
            icon="database"
          />
          <StatCard
            label="Actions planned"
            value={summary.data.actions_planned}
            hint={`${summary.data.actions_refused} refused, ${summary.data.actions_unresolved} unresolved`}
            icon="audit"
          />
          <StatCard
            label="DSR activity here"
            value={summary.data.activity}
            hint={`${summary.data.activity_unclassified} in no filter family`}
            icon="dashboard"
          />
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-2">
        {/* ---------------------------------------------------- step 1 --- */}
        <Card>
          <SectionHeading
            step="Step 1"
            title="The integration, and the workspace-to-deal link"
          />
          <p className="mt-2 text-sm text-muted-foreground">
            Verify the CRM integration is <strong>on</strong> and the workspace is
            connected to a deal or account. A published workflow whose integration is
            off will not fire, and a room with no connection enrols nobody - both say so
            rather than failing quietly.
          </p>
          {(integrations.data?.integrations || []).length === 0 ? (
            <EmptyState
              title="No integration registered"
              description="A workflow's trigger names one. Register it before publishing."
            />
          ) : (
            <ul className="mt-4 flex flex-col">
              {integrations.data.integrations.map((integration) => (
                <li
                  key={integration.id}
                  className="flex flex-col gap-1 border-t border-border-subtle/30 py-3 first:border-t-0"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-[13px] text-foreground">
                      {integration.name}
                    </span>
                    <Badge tone={integration.enabled ? 'insert' : 'delete'}>
                      {integration.enabled ? 'on' : 'off'}
                    </Badge>
                    <span className="text-xs text-muted-foreground">
                      {Object.keys(integration.connections || {}).length} room(s) connected
                    </span>
                  </div>
                  <Button
                    className="self-start"
                    disabled={busy}
                    onClick={() =>
                      run(async () => {
                        await workflowApi.amendIntegration(integration.id, {
                          enabled: !integration.enabled,
                        })
                        return `${integration.name} is now ${integration.enabled ? 'off' : 'on'}.`
                      })
                    }
                  >
                    {integration.enabled ? 'Turn off' : 'Turn on'}
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </Card>

        {/* ------------------------------------- steps 2 to 7: the editor --- */}
        <Card>
          <SectionHeading
            step="Steps 2 to 7"
            title="Write a contact-based workflow"
          />
          <div className="mt-4 flex flex-col gap-4">
            <Field label="Name" id="wf030-name">
              <input
                id="wf030-name"
                className={inputClass}
                value={draft.name}
                onChange={(event) => set('name', event.target.value)}
                placeholder="Pricing pack downloaded: follow up"
              />
            </Field>

            <Field
              label="Filter family"
              id="wf030-family"
              hint={familyLabel(voc, draft.family)}
            >
              <select
                id="wf030-family"
                className={inputClass}
                value={draft.family}
                onChange={(event) => set('family', event.target.value)}
              >
                {(voc.filter_families || []).map((family) => (
                  <option key={family.name} value={family.name}>
                    {family.name} — {family.group.replace('_', ' ')}
                  </option>
                ))}
              </select>
            </Field>

            <div className="flex flex-col gap-3">
              <p className="text-xs font-medium text-muted-foreground">
                Refinements this family publishes
              </p>
              {published.length === 0 && (
                <p className="text-xs text-muted-foreground/80">
                  None. This family is matched on its own, and a filter that narrows
                  nothing is reported by the lint.
                </p>
              )}
              {published.map((name) => (
                <Field
                  key={name}
                  label={name}
                  id={`wf030-refinement-${name}`}
                  hint={refinementLabel(voc, name)}
                >
                  <input
                    id={`wf030-refinement-${name}`}
                    className={inputClass}
                    value={draft.refinements[name] || ''}
                    onChange={(event) => setRefinement(name, event.target.value)}
                    placeholder={name === 'activity_text' ? 'completed task "Intro call"' : ''}
                  />
                </Field>
              ))}
            </div>

            <Field label="Action" id="wf030-action" hint={voc.action_kind_note}>
              <select
                id="wf030-action"
                className={inputClass}
                value={draft.action_kind}
                onChange={(event) => set('action_kind', event.target.value)}
              >
                {(voc.action_kinds || []).map((action) => (
                  <option key={action.kind} value={action.kind}>
                    {action.kind} — {action.label}
                  </option>
                ))}
              </select>
            </Field>

            {draft.action_kind === 'update_field' && (
              <Field label="Contact property" id="wf030-field">
                <input
                  id="wf030-field"
                  className={inputClass}
                  value={draft.field}
                  onChange={(event) => set('field', event.target.value)}
                  placeholder="pricing_pack_seen"
                />
              </Field>
            )}
            {draft.action_kind === 'send_email' && (
              <Field label="Email template" id="wf030-template">
                <input
                  id="wf030-template"
                  className={inputClass}
                  value={draft.template}
                  onChange={(event) => set('template', event.target.value)}
                />
              </Field>
            )}
            {draft.action_kind === 'slack_notification' && (
              <Field label="Slack channel" id="wf030-channel">
                <input
                  id="wf030-channel"
                  className={inputClass}
                  value={draft.channel}
                  onChange={(event) => set('channel', event.target.value)}
                />
              </Field>
            )}
            {draft.action_kind === 'change_stage' && (
              <>
                <Field label="Stage" id="wf030-stage">
                  <input
                    id="wf030-stage"
                    className={inputClass}
                    value={draft.stage}
                    onChange={(event) => set('stage', event.target.value)}
                  />
                </Field>
                <Field label="Stage vocabulary" id="wf030-stage-kind" hint={voc.lifecycle_constraint}>
                  <select
                    id="wf030-stage-kind"
                    className={inputClass}
                    value={draft.stage_kind}
                    onChange={(event) => set('stage_kind', event.target.value)}
                  >
                    <option value="deal_stage">deal_stage — a pipeline position, either way</option>
                    <option value="lifecyclestage">lifecyclestage — forward only</option>
                  </select>
                </Field>
              </>
            )}

            <div className="flex flex-wrap items-center gap-2">
              <Button
                variant="primary"
                disabled={busy || !draft.name.trim()}
                onClick={() =>
                  run(async () => {
                    const created = await workflowApi.create(toPayload(draft))
                    setDraft(emptyDraft())
                    return `Saved draft "${created.name}". Publish it to let DSR activity drive it.`
                  })
                }
              >
                Save as draft
              </Button>
              {!isResolvableAction(voc, draft.action_kind) && (
                <Badge tone="update">unresolved — stored and reported, not dropped</Badge>
              )}
            </div>

            {notice && (
              <p
                role="status"
                className={`text-sm ${notice.tone === 'error' ? 'text-destructive' : 'text-accent'}`}
              >
                {notice.text}
              </p>
            )}
          </div>
        </Card>
      </div>

      {/* ------------------------------------------------ the library --- */}
      <Card>
        <SectionHeading
          step="Step 8"
          title="The library — publish, stop, or retire a workflow"
        />
        {workflows.loading ? (
          <Spinner label="Loading the library" />
        ) : workflows.error ? (
          <ErrorNote error={workflows.error} onRetry={workflows.refetch} />
        ) : (workflows.data?.workflows || []).length === 0 ? (
          <EmptyState
            title="No workflows yet"
            description="Write one on the left, then publish it so DSR activity drives it."
          />
        ) : (
          <ul className="mt-4 flex flex-col">
            {workflows.data.workflows.map((workflow) => (
              <li
                key={workflow.id}
                className="flex flex-col gap-2 border-t border-border-subtle/30 py-4 first:border-t-0"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-sm text-foreground">{workflow.name}</span>
                  <StatusPill status={workflow.status} />
                  {workflow.flagged > 0 && (
                    <Badge tone="update">{workflow.flagged} flagged</Badge>
                  )}
                  <span className="text-xs text-muted-foreground">
                    {workflow.enrollments} enrollment(s)
                    {workflow.last_enrolled_at ? `, last ${workflow.last_enrolled_at}` : ''}
                  </span>
                </div>
                <StatusMeaning status={workflow.status} />
                <p className="text-sm text-muted-foreground">{workflow.description}</p>
                <p className="font-mono text-[12px] text-sky-300">
                  {(workflow.trigger?.criteria || [])
                    .map(
                      (criteria) =>
                        `${criteria.family}${
                          Object.keys(criteria.refinements || {}).length
                            ? ` (${Object.entries(criteria.refinements)
                                .map(([key, value]) => `${key}=${value}`)
                                .join(', ')})`
                            : ' (unrefined)'
                        }`
                    )
                    .join('  AND  ')}
                </p>
                {(workflow.warnings || []).length > 0 && (
                  <ul className="flex flex-col">
                    {workflow.warnings.map((finding) => (
                      <FindingRow key={`${finding.code}-${finding.field}`} finding={finding} />
                    ))}
                  </ul>
                )}
                <div className="flex flex-wrap gap-2">
                  {workflow.status === 'published' ? (
                    <>
                      <Button
                        disabled={busy}
                        onClick={() =>
                          run(async () => {
                            await workflowApi.unpublish(workflow.id)
                            return `"${workflow.name}" will not fire until it is published again.`
                          })
                        }
                      >
                        Unpublish
                      </Button>
                      <Button
                        variant="danger"
                        disabled={busy}
                        onClick={() =>
                          run(async () => {
                            await workflowApi.withdraw(workflow.id)
                            return `"${workflow.name}" is withdrawn. Its enrollments still name it.`
                          })
                        }
                      >
                        Withdraw
                      </Button>
                    </>
                  ) : (
                    <Button
                      variant="primary"
                      disabled={busy}
                      onClick={() =>
                        run(async () => {
                          await workflowApi.publish(workflow.id)
                          return `"${workflow.name}" is published. DSR activity drives it from now on.`
                        })
                      }
                    >
                      Publish
                    </Button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {/* ------------------------------------- the automation, per room --- */}
      <Card>
        <SectionHeading
          step="The automation"
          title="What fired in a room, and what did not"
        />
        <div className="mt-4 flex flex-col gap-4">
          <Field
            label="Room"
            id="wf030-room"
            hint="Room-scoped: the research's activities are tied to a contact inside a workspace."
          >
            <select
              id="wf030-room"
              className={inputClass}
              value={roomId}
              onChange={(event) => setRoomId(event.target.value)}
            >
              <option value="">Choose a room…</option>
              {(rooms.data?.records || []).map((room) => (
                <option key={room.id} value={room.id}>
                  {room.data?.name} {connectedRoomIds.has(room.id) ? '(connected)' : '(no deal link)'}
                </option>
              ))}
            </select>
          </Field>

          {selectedRoom && !connectedRoomIds.has(selectedRoom.id) && (
            <p className="text-sm text-muted-foreground">
              Step 1 is not satisfied for this room: no deal or account is connected, so no
              workflow can enrol anybody here. The evaluation below says so per workflow.
            </p>
          )}

          {roomId && <EvaluationPanel roomId={roomId} onRun={refresh} />}

          {roomId && enrollments.data && (
            <div className="flex flex-col">
              <h3 className="mt-2 text-sm font-semibold text-foreground">
                Enrollments ({enrollments.data.count})
              </h3>
              <p className="mb-2 text-xs text-muted-foreground">
                {enrollments.data.executed} executed. {enrollments.data.actionability_note}
              </p>
              {(enrollments.data.enrollments || []).length === 0 ? (
                <EmptyState
                  title="Nothing has enrolled yet"
                  description="Publish a workflow and record a matching activity event, then evaluate."
                />
              ) : (
                <ul className="flex flex-col">
                  {enrollments.data.enrollments.map((enrollment) => (
                    <li
                      key={enrollment.id}
                      className="flex flex-col gap-2 border-t border-border-subtle/30 py-4 first:border-t-0"
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-mono text-[13px] text-foreground">
                          {enrollment.workflow_name}
                        </span>
                        <Badge tone="insert">{enrollment.contact}</Badge>
                        <Badge tone="neutral">{enrollment.via}</Badge>
                        <span className="text-xs text-muted-foreground">
                          {enrollment.match_count} matching event(s)
                        </span>
                      </div>
                      <ul className="flex flex-col">
                        {(enrollment.action_plan || []).map((action) => (
                          <ActionRow key={action.index} action={action} />
                        ))}
                      </ul>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
      </Card>

      {/* --------------------------------------- the judgement register --- */}
      <Card>
        <SectionHeading
          step="Read this before changing a rule"
          title="Where the research stops and this build decides"
        />
        {inferences.loading ? (
          <Spinner label="Loading the judgement register" />
        ) : (
          <ul className="mt-4 flex flex-col">
            {(inferences.data?.inferences || []).map((inference) => (
              <li
                key={inference.id}
                className="flex flex-col gap-1 border-t border-border-subtle/30 py-3 first:border-t-0"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-[12px] text-accent">{inference.id}</span>
                  <span className="text-sm text-foreground">{inference.topic}</span>
                </div>
                <p className="text-xs text-muted-foreground">{inference.why}</p>
                <p className="font-mono text-[11px] text-muted-foreground/70">
                  change it in {inference.change_it}
                </p>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  )
}

/**
 * The evaluation panel: record a DSR event, then evaluate one contact.
 *
 * Two routes, one panel, because they are two different questions - "what did this
 * event look like once classified" and "does it enrol anybody" - and the research's
 * data flow is the second one.
 */
function EvaluationPanel({ roomId, onRun }) {
  const [contact, setContact] = useState('')
  const [action, setAction] = useState('viewed')
  const [target, setTarget] = useState('Pricing One-Pager')
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  async function send(body, label) {
    setBusy(true)
    setError(null)
    try {
      const response = await body()
      setResult(response)
      await onRun()
      return label
    } catch (failure) {
      setError(failure)
      return null
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-4 border-t border-border-subtle/30 pt-4">
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="Contact" id="wf030-contact" hint="The activities are tied to the contact record.">
          <input
            id="wf030-contact"
            className={inputClass}
            value={contact}
            onChange={(event) => setContact(event.target.value)}
            placeholder="a.buyer@northwind.example"
          />
        </Field>
        <Field label="Activity" id="wf030-action-word">
          <input
            id="wf030-action-word"
            className={inputClass}
            value={action}
            onChange={(event) => setAction(event.target.value)}
          />
        </Field>
        <Field label="Target" id="wf030-target">
          <input
            id="wf030-target"
            className={inputClass}
            value={target}
            onChange={(event) => setTarget(event.target.value)}
          />
        </Field>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button
          disabled={busy}
          onClick={() =>
            send(
              () =>
                workflowApi.recordActivity(roomId, {
                  person: contact,
                  action,
                  target,
                  delivery: 'webhook',
                }),
              'Activity recorded.'
            )
          }
        >
          Record a DSR event
        </Button>
        <Button
          variant="primary"
          disabled={busy || !contact.trim()}
          onClick={() => send(() => workflowApi.evaluate(roomId, { contact }), 'Evaluated.')}
        >
          Evaluate this contact
        </Button>
      </div>

      {error && <ErrorNote error={error} />}

      {result && (
        <div className="flex flex-col gap-3">
          <p className="text-sm text-muted-foreground">
            {result.enrolled} workflow(s) fired, {result.not_enrolled} matched nothing,{' '}
            {result.skipped.length} skipped, over {result.events_considered} event(s).{' '}
            {result.actionable} actionable, nothing on the seller's screen.
          </p>

          {(result.skipped || []).length > 0 && (
            <ul className="flex flex-col">
              {result.skipped.map((skip) => (
                <SkipRow key={`${skip.workflow_id}-${skip.reason}`} skip={skip} />
              ))}
            </ul>
          )}

          {(result.misses || []).length > 0 && (
            <ul className="flex flex-col">
              {result.misses.map((miss) => (
                <MissRow key={miss.workflow_id} miss={miss} />
              ))}
            </ul>
          )}

          {(result.enrollments || []).length > 0 && (
            <ul className="flex flex-col">
              {result.enrollments.map((row) => (
                <li
                  key={row.enrollment.id}
                  className="flex flex-col gap-1 border-t border-border-subtle/30 py-3 first:border-t-0"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-[13px] text-foreground">
                      {row.enrollment.workflow_name}
                    </span>
                    <Badge tone="insert">{row.outcome}</Badge>
                  </div>
                  <p className="text-xs text-muted-foreground">{row.detail}</p>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}

function SectionHeading({ step, title }) {
  return (
    <div className="flex items-start gap-3">
      <span className="mt-0.5 text-accent">
        <span className="sr-only">{step}.</span>
        <svg
          aria-hidden="true"
          focusable="false"
          width="18"
          height="18"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d={WORKFLOW_ICON} />
        </svg>
      </span>
      <div>
        <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{step}</p>
        <h2 className="text-base font-semibold text-foreground">{title}</h2>
      </div>
    </div>
  )
}

export default CrmWorkflowsPage
