import { useCallback, useState } from 'react'
import { Card, ErrorNote, Field, Spinner, inputClass, useAsync } from '@/components/ui'
import {
  deliveryNote,
  meetingWebhookApi,
  signingRuleLine,
  subscriptionTone,
  subscriptionWord,
  urlIsAcceptable,
} from './api'
import {
  DataTable,
  FactList,
  Glyph,
  MachineValue,
  PathButton,
  Section,
  StatusBadge,
} from './primitives'

/**
 * Meeting webhook fan-out (WF-066).
 *
 * The researched surface: *"Command Center > Integrations > Webhooks"* - a table
 * of subscriber URLs, each bound to one of three event types, each with a status
 * toggle and a per-row delete - beside the meeting events this room signed and the
 * attempts it made.
 *
 * **Four rules this page holds to, and each one is a place a page like this
 * normally lies.**
 *
 * 1. *No status by colour alone.* Every row's badge carries its own word. A
 *    `Badge` tinted by tone is a colour a screen reader and a colour-blind reader
 *    both miss, so the word is never optional.
 * 2. *"Reached nobody" and "reached them and they refused it" are different
 *    problems.* Both would render as "failed", and the reader would not know
 *    whether to enable a row or to fix a subscriber. `deliveryNote` says which.
 * 3. *The fan-out is unbounded, so the table says so.* A reader looking for a cap
 *    will not find one, and the research's reason is on the page rather than in a
 *    commit message: *"You are not limited by the number of webhooks you have"*.
 * 4. *Replay protection belongs to the consumer.* The page publishes the window
 *    and says the room refuses nothing on age, because a page that implied the
 *    sender enforced it would send someone looking for a control that does not
 *    exist.
 */
function MeetingWebhookFanoutPage() {
  const [chosenRoomId, setChosenRoomId] = useState('')
  const [notice, setNotice] = useState('')
  const [url, setUrl] = useState('')
  const [chosenType, setChosenType] = useState('')
  const [formError, setFormError] = useState('')

  const rooms = useAsync(() => meetingWebhookApi.rooms(), [])
  const vocabulary = useAsync(() => meetingWebhookApi.vocabulary(), [])

  // The room and the event type are *derived*, not stored. A page that seeds them
  // with an effect calls setState on first paint, which React calls a cascading
  // render and eslint rightly warns about. Deriving gives the same result with no
  // second pass: an empty choice means "the first one the server offered", and the
  // moment a person picks something the choice stops being empty and stops moving.
  const roomOptions = rooms.data?.records || []
  const roomId = chosenRoomId || roomOptions[0]?.id || ''
  const types = vocabulary.data?.event_types || []
  const eventType = chosenType || types[0]?.id || ''
  const subscriptions = useAsync(
    () => (roomId ? meetingWebhookApi.subscriptions(roomId) : Promise.resolve({ subscriptions: [] })),
    [roomId],
  )
  const summary = useAsync(
    () => (roomId ? meetingWebhookApi.summary(roomId) : Promise.resolve(null)),
    [roomId],
  )
  const events = useAsync(
    () => (roomId ? meetingWebhookApi.events(roomId, { limit: 25 }) : Promise.resolve({ events: [] })),
    [roomId],
  )
  const deliveries = useAsync(
    () => (roomId ? meetingWebhookApi.deliveries(roomId, { limit: 60 }) : Promise.resolve({ deliveries: [] })),
    [roomId],
  )
  const sample = useAsync(
    () => (roomId ? meetingWebhookApi.sample(roomId) : Promise.resolve(null)),
    [roomId],
  )
  const inferences = useAsync(() => meetingWebhookApi.inferences(), [])

  const after = useCallback(
    (message) => {
      setNotice(message)
      subscriptions.refetch()
      summary.refetch()
      events.refetch()
      deliveries.refetch()
    },
    [subscriptions, summary, events, deliveries],
  )

  const subRows = subscriptions.data?.subscriptions || []
  const eventRows = events.data?.events || []
  const deliveryRows = deliveries.data?.deliveries || []
  // The mode a room validates URLs against is served per deployment, and the
  // sample route reports which one this room is on. Self-hosted accepts http and
  // private addresses, so that is what the form checks unless the room says
  // otherwise; a room on the SaaS rules is caught by the server either way.
  const mode = sample.data?.deployment_mode || 'self_hosted'
  const urlCheck = urlIsAcceptable(url, mode)

  if (rooms.loading || vocabulary.loading) {
    return <Spinner label="Loading meeting webhooks" />
  }
  if (rooms.error) return <ErrorNote error={rooms.error} onRetry={rooms.refetch} />
  if (vocabulary.error) {
    return <ErrorNote error={vocabulary.error} onRetry={vocabulary.refetch} />
  }

  async function createSubscription(event) {
    event.preventDefault()
    setFormError('')
    const check = urlIsAcceptable(url, mode)
    if (!check.ok) {
      setFormError(check.why)
      return
    }
    try {
      const created = await meetingWebhookApi.createSubscription(roomId, {
        url,
        event_type: eventType,
      })
      setUrl('')
      after(
        `Added ${created.url} for ${created.event_type}. It is disabled; enable it when the subscriber is ready.`,
      )
    } catch (error) {
      setFormError(String(error?.message || error))
    }
  }

  async function setStatus(row, status) {
    try {
      await meetingWebhookApi.patchSubscription(roomId, row.id, { status })
      after(`${row.url} is now ${status} for ${row.event_type}.`)
    } catch (error) {
      after(`Could not change the row: ${String(error?.message || error)}`)
    }
  }

  async function retire(row) {
    try {
      await meetingWebhookApi.retireSubscription(roomId, row.id)
      after(`${row.url} was retired. Its delivery history still resolves.`)
    } catch (error) {
      after(`Could not retire the row: ${String(error?.message || error)}`)
    }
  }

  async function redeliver(eventId) {
    try {
      const report = await meetingWebhookApi.redeliver(roomId, eventId)
      after(`Redelivered to ${report.delivered} subscriber(s), ${report.failed} failed.`)
    } catch (error) {
      after(`Could not redeliver: ${String(error?.message || error)}`)
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="font-display text-xl font-semibold text-foreground">
            Meeting webhooks
          </h1>
          <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
            Every subscriber URL this room pushes meeting lifecycle events to, the events it
            signed, and each attempt. One URL can serve several event types, and one event
            type can have several URLs.
          </p>
        </div>
        <label className="flex min-h-11 items-center gap-2 text-sm">
          <span className="text-muted-foreground">Room</span>
          <select
            value={roomId}
            onChange={(change) => setChosenRoomId(change.target.value)}
            className="min-h-11 rounded-sm border border-border-subtle bg-surface px-2 text-sm focus-visible:ring-2 focus-visible:ring-ring"
          >
            {(roomOptions || []).map((room) => (
              <option key={room.id} value={room.id}>
                {room.data?.name || room.id}
              </option>
            ))}
          </select>
        </label>
      </header>

      {notice && (
        <p role="status" className="rounded-sm border border-accent/40 bg-accent/10 p-3 text-sm">
          {notice}
        </p>
      )}

      {summary.error && <ErrorNote error={summary.error} onRetry={summary.refetch} />}

      {summary.data && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {[
            { term: 'Subscriptions', value: summary.data.subscriptions },
            { term: 'Enabled', value: summary.data.enabled },
            { term: 'Events', value: summary.data.events },
            { term: 'Attempts', value: summary.data.deliveries },
          ].map((card) => (
            <Card key={card.term} className="p-3">
              <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
                {card.term}
              </p>
              <p className="mt-1 font-mono text-2xl leading-none font-semibold text-foreground">
                {card.value}
              </p>
            </Card>
          ))}
        </div>
      )}

      {summary.data?.notes?.length > 0 && (
        <Card>
          <ul className="space-y-1.5 text-sm text-muted-foreground">
            {summary.data.notes.map((line) => (
              <li key={line} className="flex gap-2">
                <span aria-hidden="true" className="text-accent">
                  &middot;
                </span>
                <span>{line}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {/* Steps 1 and 2: the Webhooks table. */}
      <Section title="Subscriber URLs" count={`${subRows.length} row(s)`}>
        <Card>
          <form onSubmit={createSubscription} className="grid gap-4 sm:grid-cols-[1fr_auto_auto]">
            <Field
              label="Subscriber URL"
              id="wf066-url"
              hint={
                url && !urlCheck.ok ? (
                  <span className="text-destructive">{urlCheck.why}</span>
                ) : (
                  'The address this room POSTs the signed meeting payload to.'
                )
              }
            >
              <input
                id="wf066-url"
                value={url}
                onChange={(change) => {
                  setUrl(change.target.value)
                  setFormError('')
                }}
                placeholder="https://hooks.example/meetings/created"
                className={inputClass}
              />
            </Field>
            <Field label="Event type" id="wf066-type">
              <select
                id="wf066-type"
                value={eventType}
                onChange={(change) => setChosenType(change.target.value)}
                className={inputClass}
              >
                {types.map((row) => (
                  <option key={row.id} value={row.id}>
                    {row.label} ({row.payload_type})
                  </option>
                ))}
              </select>
            </Field>
            <div className="flex items-end">
              <PathButton glyph="webhook" variant="primary" type="submit" disabled={!roomId}>
                Create
              </PathButton>
            </div>
          </form>
          {formError && (
            <p role="alert" className="mt-3 text-sm text-destructive">
              {formError}
            </p>
          )}
          <p className="mt-3 text-xs text-muted-foreground">
            A new row lands <strong className="text-foreground">disabled</strong>. Enabling it is a
            deliberate second step, because the researched flow creates the row first and sets the
            status after. {subscriptions.data?.subscription_limit_note}
          </p>
        </Card>

        <DataTable
          rowKey={(row) => row.id}
          empty="This room has no subscriber URLs yet. Add one above."
          rows={subRows}
          columns={[
            {
              key: 'url',
              label: 'Subscriber URL',
              render: (row) => <MachineValue>{row.url}</MachineValue>,
            },
            {
              key: 'event_type',
              label: 'Event type',
              render: (row) => (
                <span>
                  <MachineValue>{row.event_type}</MachineValue>
                  <span className="ml-2 text-xs text-muted-foreground">
                    fires {row.payload_type}
                  </span>
                </span>
              ),
            },
            {
              key: 'status',
              label: 'Status',
              render: (row) => (
                <StatusBadge tone={subscriptionTone(row)} word={subscriptionWord(row)} />
              ),
            },
            {
              key: 'actions',
              label: 'Actions',
              render: (row) =>
                row.retired ? (
                  <span className="text-xs text-muted-foreground">Retired. History resolves.</span>
                ) : (
                  <div className="flex flex-wrap gap-2">
                    <PathButton
                      onClick={() => setStatus(row, row.status === 'enabled' ? 'disabled' : 'enabled')}
                    >
                      {row.status === 'enabled' ? 'Disable' : 'Enable'}
                    </PathButton>
                    <PathButton variant="danger" onClick={() => retire(row)}>
                      Delete
                    </PathButton>
                  </div>
                ),
            },
          ]}
        />
      </Section>

      {/* The events this room signed. */}
      <Section title="Signed events" count={`${eventRows.length} event(s)`}>
        <DataTable
          rowKey={(row) => row.id}
          empty="This room has signed no meeting events yet."
          rows={eventRows}
          columns={[
            {
              key: 'event_type',
              label: 'Type',
              render: (row) => (
                <span>
                  <MachineValue>{row.event_type}</MachineValue>
                  <span className="ml-2 text-xs text-muted-foreground">{row.payload_type}</span>
                </span>
              ),
            },
            {
              key: 'meeting_id',
              label: 'Meeting',
              render: (row) => <MachineValue>{row.meeting_id}</MachineValue>,
            },
            {
              key: 'timestamp',
              label: 'Timestamp',
              render: (row) => <MachineValue>{row.timestamp}</MachineValue>,
            },
            {
              key: 'missing',
              label: 'Fields left out',
              render: (row) =>
                (row.missing_fields || []).length === 0 ? (
                  <span className="text-xs text-muted-foreground">none</span>
                ) : (
                  <MachineValue>{(row.missing_fields || []).join(', ')}</MachineValue>
                ),
            },
            {
              key: 'actions',
              label: 'Redeliver',
              render: (row) => (
                <PathButton onClick={() => redeliver(row.id)}>Send again</PathButton>
              ),
            },
          ]}
        />
        <p className="text-xs text-muted-foreground">
          <strong className="text-foreground">Send again</strong> re-sends the same bytes with a fresh
          timestamp. The research names no retry ladder, so nothing retries on its own.
        </p>
      </Section>

      {/* One row per attempt. */}
      <Section title="Delivery attempts" count={`${deliveryRows.length} attempt(s)`}>
        <DataTable
          rowKey={(row) => row.id}
          empty="No delivery has been attempted yet."
          rows={deliveryRows}
          columns={[
            {
              key: 'url',
              label: 'Subscriber',
              render: (row) => <MachineValue>{row.url || 'no subscriber'}</MachineValue>,
            },
            {
              key: 'event_type',
              label: 'Event type',
              render: (row) => <MachineValue>{row.event_type}</MachineValue>,
            },
            {
              key: 'outcome',
              label: 'Outcome',
              render: (row) => (
                <StatusBadge
                  tone={
                    row.outcome === 'delivered'
                      ? 'insert'
                      : row.outcome === 'failed'
                        ? 'delete'
                        : 'neutral'
                  }
                  word={row.outcome}
                />
              ),
            },
            {
              key: 'note',
              label: 'What happened',
              render: (row) => <span className="text-sm">{deliveryNote(row)}</span>,
            },
          ]}
        />
      </Section>

      {/* Step 3 and step 4, served rather than described. */}
      <Section title="The signing rule" count={vocabulary.data?.headers?.signature}>
        <Card>
          <div className="mb-4 flex items-center gap-2 text-sm text-muted-foreground">
            <Glyph name="signature" className="text-accent" />
            <MachineValue>{signingRuleLine(vocabulary.data)}</MachineValue>
          </div>
          {sample.data ? (
            <FactList
              facts={[
                ['Signature header', sample.data.signature_header],
                ['Timestamp header', sample.data.timestamp_header],
                ['Timestamp (unix seconds)', sample.data.timestamp],
                ['Signing input', sample.data.signing_input],
                ['Raw body', sample.data.raw_body],
                ['Signature', sample.data.signature],
                ['Secret is set', sample.data.secret_is_set ? 'yes' : 'no, the room will mint one'],
                [
                  'Replay window',
                  `${sample.data.replay_window_seconds} s, applied by ${sample.data.replay_window_owner}`,
                ],
                ['Verify it yourself', sample.data.verify_snippet],
              ]}
            />
          ) : (
            <p className="text-sm text-muted-foreground">
              Pick a room to see one real body and the signature over it.
            </p>
          )}
          <p className="mt-4 text-xs text-muted-foreground">
            <Glyph name="window" size={14} className="mr-1 inline align-text-bottom" />
            The signature covers the bytes shown above and nothing else. A subscriber that parses the
            body and re-serialises it signs different bytes, and the research records that as the
            cause of a signature mismatch.
          </p>
        </Card>
      </Section>

      <Section title="Judgement calls" count={inferences.data?.count}>
        <DataTable
          rowKey={(row) => row.id}
          empty="The register did not load."
          rows={inferences.data?.inferences || []}
          columns={[
            { key: 'topic', label: 'Topic', render: (row) => row.topic },
            { key: 'basis', label: 'What the research says', render: (row) => row.basis },
            { key: 'change_it', label: 'How to change it', render: (row) => row.change_it },
          ]}
        />
      </Section>
    </div>
  )
}

export default MeetingWebhookFanoutPage
