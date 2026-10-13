/**
 * The Slack-notification page (WF-112).
 *
 * One room at a time. The page shows the connected workspace, the Slack blocks, and
 * the recorded decisions - what WOULD be posted to which channel, or which named rule
 * refused it. The Send-test form is the modal's equivalent: it records a `test`
 * decision, judged against the app-API limit rather than the slower broadcast one.
 *
 * Three things this page must never do, and each is why a branch below exists: it
 * must not imply a message was sent (there is no Slack transport), it must not render
 * a status by colour alone (every state pairs its tone with a word), and it must not
 * reach outside its own prefix (every call is `slackApi`).
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
  blockLabel,
  dispatchReason,
  dispatchSentence,
  messageText,
  refusalMessage,
  retrySentence,
  slackApi,
} from './api'
import { ActionabilityNote, Dash, Fact, Glyph, Notice, StatusPill } from './primitives'

function roomLabel(record) {
  return record?.data?.name || record?.id || 'Unnamed room'
}

function RoomPicker({ rooms, value, onChange }) {
  return (
    <Field label="Room" hint="A connection, a block and a decision all belong to one room." id="wf112-room">
      <select id="wf112-room" className={inputClass} value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="">Choose a room</option>
        {rooms.map((record) => (
          <option key={record.id} value={record.id}>
            {roomLabel(record)}
          </option>
        ))}
      </select>
    </Field>
  )
}

function ConnectionPanel({ connection }) {
  if (!connection) {
    return (
      <Card>
        <div className="flex items-center gap-2">
          <Glyph name="slack" />
          <h2 className="font-display text-lg font-semibold">Slack connection</h2>
        </div>
        <p className="mt-2 text-sm text-muted-foreground">
          No workspace is connected for this room. A notification needs a bot with the bot scope
          and a channel chosen at connect time.
        </p>
      </Card>
    )
  }
  return (
    <Card>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Glyph name="slack" />
          <h2 className="font-display text-lg font-semibold">Slack connection</h2>
        </div>
        <StatusPill state={connection.state} />
      </div>
      <dl className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Fact label="Workspace">{connection.workspace || <Dash />}</Fact>
        <Fact label="Channel">{connection.channel ? `#${connection.channel}` : <Dash />}</Fact>
        <Fact label="Channel kind">{connection.channel_kind || <Dash />}</Fact>
        <Fact label="Scopes">{(connection.scopes || []).join(', ') || <Dash />}</Fact>
      </dl>
      {connection.channel_kind === 'private' && !connection.bot_in_channel ? (
        <p className="mt-4 text-sm text-muted-foreground">
          This is a private channel and the bot is not in it. Invite it with /invite before a
          notification can be recorded as deliverable.
        </p>
      ) : null}
    </Card>
  )
}

function BlockList({ blocks, onTest, busyId }) {
  if (!blocks.length) {
    return (
      <EmptyState
        title="No Slack block yet"
        description="A block holds the message. Add one and a buyer signal can render it into a channel."
      />
    )
  }
  return (
    <ul className="space-y-3">
      {blocks.map((block) => (
        <li key={block.id} className="rounded-sm border border-border-subtle bg-surface p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex min-w-0 items-center gap-2">
              <Glyph name="channel" />
              <span className="truncate text-sm font-medium text-foreground">{blockLabel(block)}</span>
            </div>
            <StatusPill state={block.state} />
          </div>
          <p className="mt-2 text-sm text-muted-foreground">{block.message}</p>
          <dl className="mt-3 grid gap-3 sm:grid-cols-3">
            <Fact label="Destination">{block.channel ? `#${block.channel}` : <Dash />}</Fact>
            <Fact label="Trigger">{block.trigger}</Fact>
            <Fact label="Block kind">{block.block_kind}</Fact>
          </dl>
          <div className="mt-3">
            <Button icon="refresh" onClick={() => onTest(block)} disabled={busyId === block.id}>
              Send test
            </Button>
          </div>
        </li>
      ))}
    </ul>
  )
}

function DispatchList({ dispatches }) {
  if (!dispatches.length) {
    return (
      <EmptyState
        title="No notification recorded yet"
        description="Send a test, or let a buyer signal fire, and the decision appears here with the channel it would use."
      />
    )
  }
  return (
    <ul className="space-y-3">
      {dispatches.map((dispatch) => (
        <li key={dispatch.id} className="rounded-sm border border-border-subtle bg-surface p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="font-mono text-xs text-muted-foreground">{dispatch.id}</span>
            <StatusPill state={dispatch.state} />
          </div>
          <p className="mt-2 text-sm text-foreground">{dispatchSentence(dispatch)}</p>
          {dispatch.state === 'refused' ? (
            <div className="mt-2">
              <Notice tone="error" title="Refused:">
                {dispatchReason(dispatch)} {retrySentence(dispatch)}
              </Notice>
            </div>
          ) : (
            <pre className="mt-2 overflow-x-auto rounded-sm border border-border-subtle bg-muted p-3 font-mono text-xs text-foreground">
              {messageText(dispatch)}
            </pre>
          )}
          <p className="mt-2 text-xs text-muted-foreground">
            Kind {dispatch.kind}
            {dispatch.signal?.type ? ` · signal ${dispatch.signal.type}` : ''}
            {dispatch.delivery ? ` · ${dispatch.delivery}` : ''}
          </p>
        </li>
      ))}
    </ul>
  )
}

export default function SlackSignalNotify() {
  const rooms = useAsync(() => slackApi.rooms(), [])
  const [roomId, setRoomId] = useState('')
  const [channel, setChannel] = useState('')
  const [username, setUsername] = useState('')
  const [busyId, setBusyId] = useState('')
  const [notice, setNotice] = useState(null)

  const roomRecords = rooms.data?.records || []
  const selected = roomId || roomRecords[0]?.id || ''

  const page = useAsync(async () => {
    if (!selected) return null
    const [vocabulary, summary, connection, automations, dispatches] = await Promise.all([
      slackApi.vocabulary(),
      slackApi.summary(selected),
      slackApi.connection(selected),
      slackApi.automations(selected),
      slackApi.dispatches({ room_id: selected, limit: 25 }),
    ])
    return { vocabulary, summary, connection, automations, dispatches }
  }, [selected])

  async function sendTest(block) {
    setBusyId(block.id)
    setNotice(null)
    try {
      const decision = await slackApi.sendTest(
        block.id,
        { channel: channel || undefined, customer: { customer: { username } } },
        selected,
        undefined,
      )
      setNotice({
        tone: decision.state === 'recorded' ? 'ok' : 'error',
        text: decisionSentence(decision),
      })
      page.refetch()
    } catch (error) {
      setNotice({ tone: 'error', text: refusalMessage(error) })
    } finally {
      setBusyId('')
    }
  }

  if (rooms.loading) return <Spinner label="Loading rooms" />
  if (rooms.error) return <ErrorNote error={rooms.error} onRetry={rooms.refetch} />

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl font-semibold text-foreground">Slack signals</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Tell the internal team when a buyer signal fires, without anyone opening the room.
          </p>
        </div>
        <div className="w-full max-w-xs">
          <RoomPicker rooms={roomRecords} value={selected} onChange={setRoomId} />
        </div>
      </header>

      {notice ? (
        <Notice tone={notice.tone} onDismiss={() => setNotice(null)}>
          {notice.text}
        </Notice>
      ) : null}

      {!selected ? (
        <EmptyState title="Choose a room" description="Pick a room to see its Slack connection." />
      ) : page.loading ? (
        <Spinner label="Loading the Slack connection" />
      ) : page.error ? (
        <ErrorNote error={page.error} onRetry={page.refetch} />
      ) : page.data ? (
        <>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard
              label="Connected"
              value={page.data.summary.connected ? 'Yes' : 'No'}
              hint={page.data.summary.workspace || 'No workspace'}
              icon="dashboard"
            />
            <StatCard
              label="Slack blocks"
              value={page.data.automations.count}
              hint={`${page.data.summary.live_automations} live`}
              icon="channel"
            />
            <StatCard
              label="Decisions"
              value={page.data.summary.dispatches}
              hint={(page.data.summary.dispatch_states?.recorded || 0) + ' would post'}
              icon="audit"
            />
            <StatCard
              label="Refused"
              value={page.data.summary.dispatch_states?.refused || 0}
              hint="Named rule stopped them"
              icon="limit"
            />
          </div>

          <ConnectionPanel connection={page.data.connection.connection} />

          <section className="space-y-3">
            <h2 className="font-display text-lg font-semibold">Slack blocks</h2>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Test channel (optional)" hint="Defaults to the block's channel." id="wf112-test-channel">
                <input
                  id="wf112-test-channel"
                  className={inputClass}
                  value={channel}
                  placeholder="sales-signals"
                  onChange={(event) => setChannel(event.target.value)}
                />
              </Field>
              <Field label="Test buyer name" hint="Fills {{customer.username}} in the test message." id="wf112-test-username">
                <input
                  id="wf112-test-username"
                  className={inputClass}
                  value={username}
                  placeholder="Dana Okafor"
                  onChange={(event) => setUsername(event.target.value)}
                />
              </Field>
            </div>
            <BlockList blocks={page.data.automations.automations} onTest={sendTest} busyId={busyId} />
          </section>

          <section className="space-y-3">
            <h2 className="font-display text-lg font-semibold">Recorded decisions</h2>
            <DispatchList dispatches={page.data.dispatches.dispatches} />
          </section>

          <ActionabilityNote />
        </>
      ) : null}
    </div>
  )
}

function decisionSentence(decision) {
  const channel = decision.channel ? `#${decision.channel}` : 'no channel'
  if (decision.state === 'recorded') return `Recorded: this message would post to ${channel}.`
  return `Refused: ${dispatchReason(decision)} ${retrySentence(decision)}`.trim()
}
