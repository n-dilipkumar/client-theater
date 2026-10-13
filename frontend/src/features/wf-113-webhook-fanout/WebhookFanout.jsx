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
  SAMPLE_ORDER,
  automationTone,
  automationWord,
  canSendSample,
  deliveryNote,
  deliveryTone,
  deliveryWord,
  shortHash,
  signingRuleLine,
  triggerTone,
  triggerWord,
  webhookIngestApi,
} from './api'
import {
  DataTable,
  FactList,
  GlyphButton,
  InlineNote,
  MachineValue,
  Section,
  StatusBadge,
} from './primitives'

/**
 * The webhook ingest page (WF-113).
 *
 * The researched flow, laid out in order: create an automation, copy its
 * generated Webhook URL, add actions, start it. The page also sends one
 * representative payload, because the flow offers that as an alternative to
 * giving the URL away, and it shows the trigger, event and delivery logs that
 * record what arrived.
 */

/**
 * The actions a new automation lands with, chosen to mirror the researched flow:
 * a Send Event with a Liquid mapping, a Create or Update Person with a
 * JavaScript mapping, a Batch Update, and a Slack block with no channel so the
 * demo carries a skipped delivery rather than only green rows.
 */
const DEFAULT_ACTIONS = [
  {
    key: 'send_event',
    value_type: 'liquid',
    name: 'order:placed',
    mapping: {
      order_id: '{{trigger.identifiers.id}}',
      placed_at: '{{trigger.attributes.placed_at | date: %s}}',
    },
  },
  {
    key: 'create_or_update_person',
    value_type: 'javascript',
    mapping: { return_id: 'return triggers.identifiers.id;' },
  },
  { key: 'batch_update', name: 'account:members-updated' },
  { key: 'slack' },
]

export default function WebhookFanout() {
  const [chosenRoomId, setChosenRoomId] = useState('')
  const [name, setName] = useState('Order events')
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [newAutomation, setNewAutomation] = useState(null)
  const [nonce, setNonce] = useState(0)

  const rooms = useAsync(() => webhookIngestApi.rooms(), [])
  const vocabulary = useAsync(() => webhookIngestApi.vocabulary(), [])
  const inferences = useAsync(() => webhookIngestApi.inferences(), [])

  const roomOptions = rooms.data?.records || []
  const roomId = chosenRoomId || roomOptions[0]?.id || ''

  const automations = useAsync(
    () =>
      roomId ? webhookIngestApi.automations(roomId) : Promise.resolve({ automations: [] }),
    [roomId, nonce],
  )
  const summary = useAsync(
    () => (roomId ? webhookIngestApi.summary(roomId) : Promise.resolve(null)),
    [roomId, nonce],
  )
  const triggers = useAsync(
    () => (roomId ? webhookIngestApi.triggers(roomId) : Promise.resolve({ triggers: [] })),
    [roomId, nonce],
  )
  const events = useAsync(
    () => (roomId ? webhookIngestApi.events(roomId) : Promise.resolve({ events: [] })),
    [roomId, nonce],
  )
  const deliveries = useAsync(
    () => (roomId ? webhookIngestApi.deliveries(roomId) : Promise.resolve({ deliveries: [] })),
    [roomId, nonce],
  )

  const reload = () => setNonce((value) => value + 1)

  if (rooms.loading || vocabulary.loading) {
    return <Spinner label="Loading webhook automations" />
  }
  if (rooms.error) return <ErrorNote error={rooms.error} onRetry={rooms.refetch} />
  if (vocabulary.error) return <ErrorNote error={vocabulary.error} onRetry={vocabulary.refetch} />
  if (roomOptions.length === 0) {
    return (
      <EmptyState
        title="No rooms yet"
        description="Webhook automations belong to a room. Create a room first, then come back."
      />
    )
  }

  async function handleCreate(event) {
    event.preventDefault()
    setError('')
    setNotice('')
    const trimmed = name.trim()
    if (!trimmed) {
      setError('Give the automation a name.')
      return
    }
    try {
      const created = await webhookIngestApi.createAutomation(roomId, {
        name: trimmed,
        identifier_type: 'id',
        actions: DEFAULT_ACTIONS,
      })
      setNewAutomation(created)
      setNotice(
        `Created "${created.name}". Copy the webhook URL, then choose Start automation to run it.`,
      )
      reload()
    } catch (err) {
      setError(err.message)
    }
  }

  async function handleStart(row) {
    setError('')
    setNotice('')
    try {
      const started = await webhookIngestApi.startAutomation(row.id)
      setNotice(`"${started.name}" is started. The next delivery is fanned out as events.`)
      setNewAutomation(started)
      reload()
    } catch (err) {
      setError(err.message)
    }
  }

  async function handleSend(automation) {
    setError('')
    setNotice('')
    try {
      const report = await webhookIngestApi.deliverToHook(automation.token, SAMPLE_ORDER)
      const word = report.state
      setNotice(
        word === 'duplicate'
          ? 'The same payload was sent before, so it was recorded as a duplicate and fanned out nothing.'
          : `A representative order was accepted and fanned out ${report.events.length} event(s).`,
      )
      reload()
    } catch (err) {
      setError(err.message)
    }
  }

  const signing = vocabulary.data?.signing
  const servedVocabulary = vocabulary.data?.vocabulary
  const limits = servedVocabulary?.rate_limits || []
  const notes = summary.data?.notes || []

  return (
    <div className="space-y-6">
      <header className="space-y-2">
        <h1 className="font-display text-2xl font-semibold text-foreground">Webhook ingest</h1>
        <p className="max-w-3xl text-sm text-muted-foreground">
          Generate a webhook URL per automation, accept a POST of any JSON shape, parse it into a
          Trigger data object, and fan it out as internal events. The webhook is the subject of the
          automation, never the person.
        </p>
      </header>

      {notice && <InlineNote tone="info">{notice}</InlineNote>}
      {error && <InlineNote tone="danger">{error}</InlineNote>}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Automations" value={summary.data?.automations ?? 0} icon="database" />
        <StatCard label="Started" value={summary.data?.started ?? 0} hint="eligible to receive" />
        <StatCard
          label="Triggers"
          value={summary.data?.triggers ?? 0}
          hint={`${summary.data?.duplicates ?? 0} duplicate, ${summary.data?.rejected ?? 0} rejected`}
        />
        <StatCard
          label="Events fanned out"
          value={summary.data?.events ?? 0}
          hint={`${summary.data?.deliveries ?? 0} deliveries`}
        />
      </div>

      <Section title="The constraints this workflow carries">
        <Card>
          <ul className="space-y-2 text-sm text-foreground">
            <li className="flex gap-2">
              <span aria-hidden="true" className="text-muted-foreground">
                &bull;
              </span>
              <span>
                The webhook is the subject. A mapping that references the person renders the empty
                string and does not error.
              </span>
            </li>
            <li className="flex gap-2">
              <span aria-hidden="true" className="text-muted-foreground">
                &bull;
              </span>
              <span>
                A Batch Update covers up to {servedVocabulary?.batch_limit ?? 1000} people. An
                over-limit group is refused, not truncated.
              </span>
            </li>
            <li className="flex gap-2">
              <span aria-hidden="true" className="text-muted-foreground">
                &bull;
              </span>
              <span>
                The quoted rate limits are published as data ({limits.length} served) and this build
                enforces none of them.
              </span>
            </li>
          </ul>
          {notes.length > 0 && (
            <p className="mt-4 border-t border-border-subtle pt-3 text-xs text-muted-foreground">
              {notes.join(' ')}
            </p>
          )}
        </Card>
      </Section>

      <Section title="Create an automation">
        <div className="grid gap-4 lg:grid-cols-2">
          <Card>
            <form className="space-y-4" onSubmit={handleCreate}>
              <Field label="Room" id="wf113-room">
                <select
                  id="wf113-room"
                  className={inputClass}
                  value={roomId}
                  onChange={(event) => {
                    setChosenRoomId(event.target.value)
                    setNewAutomation(null)
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
                label="Automation name"
                id="wf113-name"
                hint="Shown beside the generated URL so a team knows which URL it copied."
              >
                <input
                  id="wf113-name"
                  className={inputClass}
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                />
              </Field>
              <GlyphButton glyph="ingest" variant="primary" type="submit">
                Give me a webhook URL
              </GlyphButton>
            </form>
          </Card>

          <Card>
            <h3 className="font-display text-base font-semibold text-foreground">
              The generated URL
            </h3>
            {newAutomation ? (
              <div className="mt-3 space-y-3">
                <MachineValue>{newAutomation.url}</MachineValue>
                <div className="flex flex-wrap items-center gap-3">
                  <StatusBadge tone={automationTone(newAutomation)} word={automationWord(newAutomation)} />
                  {newAutomation.status !== 'started' && (
                    <GlyphButton glyph="fanout" onClick={() => handleStart(newAutomation)}>
                      Start automation
                    </GlyphButton>
                  )}
                  <GlyphButton
                    glyph="ingest"
                    onClick={() => handleSend(newAutomation)}
                    disabled={!canSendSample(newAutomation)}
                  >
                    Send a representative order
                  </GlyphButton>
                </div>
                {!canSendSample(newAutomation) && (
                  <p className="text-xs text-muted-foreground">
                    This automation carries a secret, so a delivery must be signed by the external
                    service. This page does not hold the secret.
                  </p>
                )}
              </div>
            ) : (
              <p className="mt-3 text-sm text-muted-foreground">
                Create an automation to generate its webhook URL. The URL is per automation, so the
                external service can be pointed at exactly one of them.
              </p>
            )}
          </Card>
        </div>
      </Section>

      <Section title="Automations" count={automations.data?.count ?? 0}>
        {automations.error ? (
          <ErrorNote error={automations.error} onRetry={automations.refetch} />
        ) : (
          <DataTable
            rowKey={(row) => row.id}
            empty="No automations in this room yet."
            columns={[
              { key: 'name', label: 'Name', render: (row) => row.name },
              {
                key: 'status',
                label: 'Status',
                render: (row) => (
                  <StatusBadge tone={automationTone(row)} word={automationWord(row)} />
                ),
              },
              {
                key: 'actions',
                label: 'Actions',
                render: (row) => (
                  <span className="font-mono text-[13px]">{row.action_count ?? 0}</span>
                ),
              },
              {
                key: 'url',
                label: 'Webhook URL',
                render: (row) => <MachineValue>{row.url}</MachineValue>,
              },
            ]}
            rows={automations.data?.automations || []}
          />
        )}
      </Section>

      <Section title="Triggers" count={triggers.data?.count ?? 0}>
        <DataTable
          rowKey={(row) => row.id}
          empty="Nothing has arrived at this room's webhook URLs yet."
          columns={[
            {
              key: 'received',
              label: 'Received',
              render: (row) => <MachineValue>{row.received_at}</MachineValue>,
            },
            {
              key: 'state',
              label: 'State',
              render: (row) => <StatusBadge tone={triggerTone(row)} word={triggerWord(row)} />,
            },
            { key: 'shape', label: 'Shape', render: (row) => row.shape },
            {
              key: 'fingerprint',
              label: 'Fingerprint',
              render: (row) => <MachineValue>{shortHash(row.fingerprint)}</MachineValue>,
            },
            {
              key: 'reason',
              label: 'Note',
              render: (row) =>
                row.reason ? (
                  row.reason
                ) : (
                  <span className="text-muted-foreground">
                    {Object.keys(row.identifiers || {}).length} identifier(s)
                  </span>
                ),
            },
          ]}
          rows={triggers.data?.triggers || []}
        />
      </Section>

      <Section title="Events fanned out" count={events.data?.count ?? 0}>
        <DataTable
          rowKey={(row) => row.id}
          empty="No events have been fanned out yet."
          columns={[
            { key: 'name', label: 'Event name', render: (row) => <MachineValue>{row.name}</MachineValue> },
            { key: 'action', label: 'From action', render: (row) => row.action?.replace(/_/g, ' ') },
            {
              key: 'data',
              label: 'Data',
              render: (row) => <MachineValue>{JSON.stringify(row.data)}</MachineValue>,
            },
          ]}
          rows={events.data?.events || []}
        />
      </Section>

      <Section title="Deliveries" count={deliveries.data?.count ?? 0}>
        <DataTable
          rowKey={(row) => row.id}
          empty="No deliveries recorded yet."
          columns={[
            { key: 'action', label: 'Action', render: (row) => row.action?.replace(/_/g, ' ') },
            {
              key: 'outcome',
              label: 'Outcome',
              render: (row) => (
                <StatusBadge tone={deliveryTone(row)} word={deliveryWord(row)} />
              ),
            },
            {
              key: 'note',
              label: 'What happened',
              render: (row) => <span className="text-muted-foreground">{deliveryNote(row)}</span>,
            },
          ]}
          rows={deliveries.data?.deliveries || []}
        />
      </Section>

      <Section title="The signing contract">
        <Card>
          <FactList
            facts={[
              ['Rule', signingRuleLine(vocabulary.data)],
              ['Encoding', signing?.encoding],
              ['Timestamp', signing?.timestamp_unit],
              ['Freshness window', signing?.window_seconds ? `${signing.window_seconds} s` : null],
              ['Required when', signing?.required_when],
              ['Reused from', signing?.reused_from],
            ]}
          />
        </Card>
      </Section>

      <Section title="Rate limits, as quoted" count={limits.length}>
        <DataTable
          rowKey={(row) => row.surface}
          empty="No rate limits served."
          columns={[
            { key: 'surface', label: 'Surface', render: (row) => row.label || row.surface },
            {
              key: 'limit',
              label: 'Limit',
              render: (row) => (
                <MachineValue>
                  {row.limit} / {row.window_seconds}s
                </MachineValue>
              ),
            },
            {
              key: 'soft',
              label: 'Enforced',
              render: (row) => (
                <StatusBadge tone="neutral" word={row.soft ? 'soft, not enforced' : 'published only'} />
              ),
            },
          ]}
          rows={limits}
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
