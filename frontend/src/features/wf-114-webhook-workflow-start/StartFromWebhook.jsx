import { useState } from 'react'
import {
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
  DEFAULT_MAPPINGS,
  DOCUMENTED_PAYLOAD,
  bindingLines,
  invocationNote,
  invocationTone,
  invocationWord,
  methodsLine,
  shortHash,
  signingRuleLine,
  triggerSavedTone,
  triggerSavedWord,
  webhookWorkflowApi,
  workflowTone,
  workflowWord,
} from './api'
import {
  BulletList,
  DataTable,
  FactList,
  GlyphButton,
  InlineNote,
  MachineValue,
  Section,
  StatusBadge,
} from './primitives'

/**
 * The start-from-webhook page (WF-114).
 *
 * The researched flow, laid out in order: open or create a workflow, select the
 * trigger Inbound Webhook, copy the generated unique URL, send a test request to
 * validate the integration, map the incoming data to variables, then Save Trigger
 * and publish. The page also shows the delivery log and the runs, so a reviewer
 * can see the states the issue asks for - including a delivery that matched no
 * mapping.
 */

export default function StartFromWebhook() {
  const [chosenRoomId, setChosenRoomId] = useState('')
  const [workflowName, setWorkflowName] = useState('Northwind payments - start on webhook')
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [armed, setArmed] = useState(null)
  const [nonce, setNonce] = useState(0)

  const rooms = useAsync(() => webhookWorkflowApi.rooms(), [])
  const vocabulary = useAsync(() => webhookWorkflowApi.vocabulary(), [])
  const inferences = useAsync(() => webhookWorkflowApi.inferences(), [])

  const roomOptions = rooms.data?.records || []
  const roomId = chosenRoomId || roomOptions[0]?.id || ''

  const workflows = useAsync(
    () => (roomId ? webhookWorkflowApi.workflows(roomId) : Promise.resolve({ workflows: [] })),
    [roomId, nonce],
  )
  const summary = useAsync(
    () => (roomId ? webhookWorkflowApi.summary(roomId) : Promise.resolve(null)),
    [roomId, nonce],
  )
  const triggers = useAsync(
    () => (roomId ? webhookWorkflowApi.triggers(roomId) : Promise.resolve({ triggers: [] })),
    [roomId, nonce],
  )
  const invocations = useAsync(
    () => (roomId ? webhookWorkflowApi.invocations(roomId) : Promise.resolve({ invocations: [] })),
    [roomId, nonce],
  )
  const runs = useAsync(
    () => (roomId ? webhookWorkflowApi.runs(roomId) : Promise.resolve({ runs: [] })),
    [roomId, nonce],
  )

  const reload = () => setNonce((value) => value + 1)

  if (rooms.loading || vocabulary.loading) {
    return <Spinner label="Loading webhook triggers" />
  }
  if (rooms.error) return <ErrorNote error={rooms.error} onRetry={rooms.refetch} />
  if (vocabulary.error) return <ErrorNote error={vocabulary.error} onRetry={vocabulary.refetch} />
  if (roomOptions.length === 0) {
    return (
      <EmptyState
        title="No rooms yet"
        description="A webhook-triggered workflow belongs to a room. Create a room first, then come back."
      />
    )
  }

  async function handleCreateWorkflow(event) {
    event.preventDefault()
    setError('')
    setNotice('')
    const trimmed = workflowName.trim()
    if (!trimmed) {
      setError('Give the workflow a name.')
      return
    }
    try {
      const created = await webhookWorkflowApi.createWorkflow(roomId, {
        name: trimmed,
        description: 'Started by an external system that posts to the generated webhook URL.',
        actions: ['Update the lead status', 'Send a confirmation email'],
      })
      setNotice(`Created "${created.name}". Add the Inbound Webhook trigger to get its URL.`)
      reload()
    } catch (err) {
      setError(err.message)
    }
  }

  async function handlePublish(workflow) {
    setError('')
    setNotice('')
    try {
      const published = await webhookWorkflowApi.publishWorkflow(workflow.id)
      setNotice(`"${published.name}" is ${published.status}. A live delivery now starts it.`)
      reload()
    } catch (err) {
      setError(err.message)
    }
  }

  async function handleCreateTrigger(workflow) {
    setError('')
    setNotice('')
    try {
      const created = await webhookWorkflowApi.createTrigger(workflow.id, {
        name: 'Inbound Webhook',
        methods: ['POST', 'GET', 'PUT'],
        mappings: DEFAULT_MAPPINGS,
      })
      setArmed({ ...created, signingValue: created.signing_value })
      setNotice(
        `Generated a URL for "${created.name}". Copy it and the signing value into the external service.`,
      )
      reload()
    } catch (err) {
      setError(err.message)
    }
  }

  async function handleSaveTrigger(trigger) {
    setError('')
    setNotice('')
    try {
      const saved = await webhookWorkflowApi.saveTrigger(trigger.id)
      setNotice(`"${saved.name}" is saved. A live delivery now starts the workflow.`)
      reload()
    } catch (err) {
      setError(err.message)
    }
  }

  async function handleSend(trigger, { test }) {
    setError('')
    setNotice('')
    try {
      const report = await webhookWorkflowApi.deliverToHook(trigger.token, DOCUMENTED_PAYLOAD, {
        test,
        signingValue: trigger.signingValue,
      })
      setNotice(report.message)
      reload()
    } catch (err) {
      setError(err.message)
    }
  }

  const signing = vocabulary.data?.signing
  const served = vocabulary.data?.vocabulary
  const methods = served?.method_payload_sources || []
  const notes = summary.data?.notes || []

  return (
    <div className="space-y-6">
      <header className="space-y-2">
        <h1 className="font-display text-2xl font-semibold text-foreground">
          Start from webhook
        </h1>
        <p className="max-w-3xl text-sm text-muted-foreground">
          Mint a unique webhook URL for a workflow trigger, copy it into an external system, and let
          a signed POST, GET or PUT start the workflow with the payload mapped onto its variables.
        </p>
      </header>

      {notice && <InlineNote tone="success">{notice}</InlineNote>}
      {error && <InlineNote tone="danger">{error}</InlineNote>}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Workflows" value={summary.data?.workflows ?? 0} icon="schema" />
        <StatCard
          label="Published"
          value={summary.data?.published ?? 0}
          hint="eligible to start"
        />
        <StatCard
          label="Triggers"
          value={summary.data?.triggers ?? 0}
          hint={`${summary.data?.saved_triggers ?? 0} saved`}
        />
        <StatCard
          label="Deliveries"
          value={summary.data?.invocations ?? 0}
          hint={`${summary.data?.runs ?? 0} run(s) started`}
        />
      </div>

      <Section title="The constraints this workflow carries">
        <Card>
          <BulletList
            items={[
              'The webhook URL is unique for each trigger, not for each workflow.',
              'The payload shape is unvalidated. Nothing is refused for a missing key unless a mapping requires it.',
              'Every delivery is signed. A minted URL that anyone can call is a published write endpoint.',
              `A GET reads its payload from the query string (${served?.methods?.join(', ') ?? 'POST, GET, PUT'}).`,
              'A delivery that matches no mapping is recorded as unmapped and starts nothing.',
            ]}
          />
          {notes.length > 0 && (
            <p className="mt-4 border-t border-border-subtle pt-3 text-xs text-muted-foreground">
              {notes.join(' ')}
            </p>
          )}
        </Card>
      </Section>

      <Section title="Open or create a workflow">
        <div className="grid gap-4 lg:grid-cols-2">
          <Card>
            <form className="space-y-4" onSubmit={handleCreateWorkflow}>
              <Field label="Room" id="wf114-room">
                <select
                  id="wf114-room"
                  className={inputClass}
                  value={roomId}
                  onChange={(event) => {
                    setChosenRoomId(event.target.value)
                    setArmed(null)
                  }}
                >
                  {roomOptions.map((room) => (
                    <option key={room.id} value={room.id}>
                      {room.data?.name || room.id}
                    </option>
                  ))}
                </select>
              </Field>
              <Field
                label="Workflow name"
                id="wf114-name"
                hint="Shown beside the generated URL so a team knows which workflow it starts."
              >
                <input
                  id="wf114-name"
                  className={inputClass}
                  value={workflowName}
                  onChange={(event) => setWorkflowName(event.target.value)}
                />
              </Field>
              <GlyphButton glyph="webhook" variant="primary" type="submit">
                Create the workflow
              </GlyphButton>
            </form>
          </Card>

          <Card>
            <h3 className="font-display text-base font-semibold text-foreground">
              Then add the Inbound Webhook trigger
            </h3>
            <p className="mt-2 text-sm text-muted-foreground">
              The generator mints one URL per trigger. Copy the URL and the signing value into the
              external system, then send the test request the flow asks for.
            </p>
            <div className="mt-3 flex flex-wrap gap-3">
              {(workflows.data?.workflows || []).slice(0, 3).map((workflow) => (
                <GlyphButton
                  key={workflow.id}
                  glyph="webhook"
                  onClick={() => handleCreateTrigger(workflow)}
                >
                  Trigger for {workflow.name}
                </GlyphButton>
              ))}
              {(workflows.data?.workflows || []).length === 0 && (
                <p className="text-sm text-muted-foreground">
                  Create a workflow to generate its trigger URL.
                </p>
              )}
            </div>
          </Card>
        </div>
      </Section>

      {armed && (
        <Section title="The generated URL, shown once">
          <Card>
            <FactList
              facts={[
                ['Webhook URL', armed.url],
                ['Signing value', armed.signingValue],
                ['Signature header', armed.signature_header],
                ['Timestamp header', armed.timestamp_header],
                ['Methods', methodsLine(armed)],
                ['Mappings', String(armed.mapping_count ?? 0)],
              ]}
            />
            <p className="mt-3 text-xs text-muted-foreground">
              The signing value is served once, here. Every read afterwards carries only whether one
              exists. The external service signs each delivery with it.
            </p>
            <div className="mt-4 flex flex-wrap items-center gap-3">
              <StatusBadge
                tone={triggerSavedTone(armed)}
                word={triggerSavedWord(armed)}
              />
              {!armed.saved && (
                <GlyphButton glyph="start" onClick={() => handleSaveTrigger(armed)}>
                  Save trigger
                </GlyphButton>
              )}
              <GlyphButton glyph="mapping" onClick={() => handleSend(armed, { test: true })}>
                Send a test request
              </GlyphButton>
              <GlyphButton
                glyph="start"
                variant="primary"
                disabled={!armed.saved}
                onClick={() => handleSend(armed, { test: false })}
              >
                Send a live delivery
              </GlyphButton>
            </div>
            {!armed.saved && (
              <p className="mt-2 text-xs text-muted-foreground">
                A test request is accepted before the trigger is saved, because the flow sends it
                first. A live delivery needs the trigger saved and the workflow published.
              </p>
            )}
          </Card>
        </Section>
      )}

      <Section title="Workflows" count={workflows.data?.count ?? 0}>
        {workflows.error ? (
          <ErrorNote error={workflows.error} onRetry={workflows.refetch} />
        ) : (
          <DataTable
            caption="The workflows in this room, with their status and downstream actions."
            rowKey={(row) => row.id}
            empty="No workflows in this room yet."
            columns={[
              { key: 'name', label: 'Name', render: (row) => row.name },
              {
                key: 'status',
                label: 'Status',
                render: (row) => (
                  <StatusBadge tone={workflowTone(row)} word={workflowWord(row)} />
                ),
              },
              {
                key: 'actions',
                label: 'Downstream actions',
                render: (row) => (
                  <span className="font-mono text-[13px]">{row.action_count ?? 0}</span>
                ),
              },
              {
                key: 'publish',
                label: 'Next step',
                render: (row) =>
                  row.status === 'published' ? (
                    <span className="text-muted-foreground">live</span>
                  ) : (
                    <GlyphButton glyph="start" onClick={() => handlePublish(row)}>
                      Publish
                    </GlyphButton>
                  ),
              },
            ]}
            rows={workflows.data?.workflows || []}
          />
        )}
      </Section>

      <Section title="Triggers" count={triggers.data?.count ?? 0}>
        <DataTable
          caption="The generated webhook URL for each trigger and whether it is saved."
          rowKey={(row) => row.id}
          empty="No triggers in this room yet. Add one to a workflow to get its URL."
          columns={[
            { key: 'name', label: 'Name', render: (row) => row.name },
            {
              key: 'saved',
              label: 'Saved',
              render: (row) => (
                <StatusBadge tone={triggerSavedTone(row)} word={triggerSavedWord(row)} />
              ),
            },
            { key: 'methods', label: 'Methods', render: (row) => methodsLine(row) },
            {
              key: 'mappings',
              label: 'Mappings',
              render: (row) => (
                <span className="font-mono text-[13px]">{row.mapping_count ?? 0}</span>
              ),
            },
            {
              key: 'url',
              label: 'Webhook URL',
              render: (row) => <MachineValue>{row.url}</MachineValue>,
            },
          ]}
          rows={triggers.data?.triggers || []}
        />
      </Section>

      <Section title="Deliveries" count={invocations.data?.count ?? 0}>
        <DataTable
          caption="Every delivery, including the refusals and the ones that matched no mapping."
          rowKey={(row) => row.id}
          empty="Nothing has been delivered to this room's webhook URLs yet."
          columns={[
            {
              key: 'received',
              label: 'Received',
              render: (row) => <MachineValue>{row.received_at}</MachineValue>,
            },
            {
              key: 'state',
              label: 'State',
              render: (row) => (
                <StatusBadge tone={invocationTone(row)} word={invocationWord(row)} />
              ),
            },
            {
              key: 'mode',
              label: 'Mode',
              render: (row) => <MachineValue>{row.mode}</MachineValue>,
            },
            {
              key: 'bound',
              label: 'Bound',
              render: (row) => (
                <span className="font-mono text-[13px]">{row.bindings?.length ?? 0}</span>
              ),
            },
            {
              key: 'fingerprint',
              label: 'Fingerprint',
              render: (row) => <MachineValue>{shortHash(row.fingerprint)}</MachineValue>,
            },
            {
              key: 'note',
              label: 'What happened',
              render: (row) => <span className="text-muted-foreground">{invocationNote(row)}</span>,
            },
          ]}
          rows={invocations.data?.invocations || []}
        />
      </Section>

      <Section title="Runs started" count={runs.data?.count ?? 0}>
        <DataTable
          caption="The runs this workflow started, with the variables bound and the actions planned."
          rowKey={(row) => row.id}
          empty="No run has started yet."
          columns={[
            {
              key: 'started',
              label: 'Started',
              render: (row) => <MachineValue>{row.started_at}</MachineValue>,
            },
            {
              key: 'bindings',
              label: 'Variables bound',
              render: (row) => (
                <MachineValue>{bindingLines(row.bindings).join(', ') || 'none'}</MachineValue>
              ),
            },
            {
              key: 'actions',
              label: 'Planned actions',
              render: (row) =>
                (row.planned_actions || []).length > 0 ? (
                  <span className="text-muted-foreground">{row.planned_actions.join('; ')}</span>
                ) : (
                  <span className="text-muted-foreground">none declared</span>
                ),
            },
            {
              key: 'fingerprint',
              label: 'Payload',
              render: (row) => <MachineValue>{row.payload_shape}</MachineValue>,
            },
          ]}
          rows={runs.data?.runs || []}
        />
      </Section>

      <Section title="The signing contract">
        <Card>
          <FactList
            facts={[
              ['Rule', signingRuleLine(vocabulary.data)],
              ['Signed input', signing?.signed_input],
              ['Encoding', signing?.encoding],
              ['Timestamp', signing?.timestamp_unit],
              ['Freshness window', signing?.window_seconds ? `${signing.window_seconds} s` : null],
              ['Required', signing?.required_when],
              ['Reused from', signing?.reused_from],
            ]}
          />
        </Card>
      </Section>

      <Section title="Where each method carries its payload" count={methods.length}>
        <DataTable
          caption="The three researched methods and the payload source each one uses."
          rowKey={(row) => row.method}
          empty="No methods served."
          columns={[
            {
              key: 'method',
              label: 'Method',
              render: (row) => <MachineValue>{row.method}</MachineValue>,
            },
            {
              key: 'source',
              label: 'Payload',
              render: (row) => <MachineValue>{row.source}</MachineValue>,
            },
            {
              key: 'body',
              label: 'Accepts a body',
              render: (row) => (
                <StatusBadge
                  tone={row.accepts_body ? 'insert' : 'neutral'}
                  word={row.accepts_body ? 'yes' : 'no, the query string'}
                />
              ),
            },
            {
              key: 'note',
              label: 'Why',
              render: (row) => <span className="text-muted-foreground">{row.note}</span>,
            },
          ]}
          rows={methods}
        />
      </Section>

      <Section title="Decisions behind this workflow" count={inferences.data?.count ?? 0}>
        <div className="grid gap-4 lg:grid-cols-2">
          {(inferences.data?.decisions || []).map((decision) => (
            <Card key={decision.id}>
              <p className="text-[11px] font-medium tracking-[0.14em] text-muted-foreground uppercase">
                {decision.topic}
              </p>
              <p className="mt-1 text-sm font-semibold text-foreground">{decision.chosen}</p>
              <p className="mt-2 text-xs text-muted-foreground">{decision.rejected_because}</p>
              <p className="mt-2 font-mono text-[11px] break-all text-muted-foreground/70">
                {decision.change_it}
              </p>
            </Card>
          ))}
        </div>
      </Section>
    </div>
  )
}
