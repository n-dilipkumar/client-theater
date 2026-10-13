/**
 * Nurture series (WF-104).
 *
 * The page renders one room's nurture series, their blocks, their runs and the poll that moves
 * them. Every picker and every label renders from the published vocabulary, so a rule changed on
 * the server reaches the page without a change here.
 *
 * Two honest statements sit in the header because they are consequences of decisions rather than
 * facts about a running system: nothing fires until the poll is run, and no message leaves this
 * product. Both are the same things the backend states on every dispatch row.
 *
 * The audience is a demo audience. The research's rule blocks filter contact and company
 * attributes, and the feature stores no contacts - the caller supplies them - so this page sends
 * a small representative audience rather than pretend to read a CRM it does not own.
 */

import { useCallback, useState } from 'react'

import { Button, Card, EmptyState, ErrorNote, Field, Icon, JsonView, Spinner, StatCard, inputClass, useAsync } from '@/components/ui'

import seriesApi from './api'
import { BlockCard, NURTURE_ICON, Notice, StateBadge, humaniseDuration } from './primitives'

const DAY = 86400

/** The blocks "Start from scratch" lays down: one of each of the researched four kinds. */
const TEMPLATE_BLOCKS = [
  {
    kind: 'rule',
    id: 'who_enters',
    filters: [{ field: 'plan', op: 'eq', value: 'enterprise' }],
    match: 'all',
  },
  {
    kind: 'content',
    id: 'intro_post',
    channel: 'in_app',
    content_type: 'post',
    body: 'Here is the 2-minute product tour.',
    goal: 'Get a reply on the tour',
    scheduling: { timezone: 'UTC' },
    send_window_seconds: 30 * DAY,
    fallback_channel: 'email',
    fallback_enabled: true,
  },
  { kind: 'wait', id: 'give_it_a_day', duration_seconds: DAY },
  {
    kind: 'rule',
    id: 'clicked_link',
    condition: 'clicked_link',
    wait_mode: 'for',
    match_within_seconds: 3 * DAY,
  },
  { kind: 'tag', id: 'engaged', tag: 'nurtured' },
]

const DEMO_AUDIENCE = [
  {
    contact_id: 'buyer_online',
    attributes: { email: 'a.buyer@northwind.example', plan: 'enterprise' },
    company: { name: 'Northwind Traders' },
    online: true,
    channels: ['in_app', 'email'],
  },
  {
    contact_id: 'buyer_offline',
    attributes: { email: 'ops@northwind.example', plan: 'enterprise' },
    company: { name: 'Northwind Traders' },
    online: false,
    channels: ['in_app', 'email'],
  },
  {
    contact_id: 'buyer_starter',
    attributes: { email: 'trial@other.example', plan: 'starter' },
    company: { name: 'Other Co' },
    online: true,
    channels: ['email'],
  },
]

export default function Series() {
  const [roomId, setRoomId] = useState('')
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState(null)
  const [preview, setPreview] = useState(null)
  const [exported, setExported] = useState(null)
  const [selectedRunId, setSelectedRunId] = useState('')

  const rooms = useAsync(() => seriesApi.rooms(), [])
  const vocabulary = useAsync(() => seriesApi.vocabulary(), [])
  const inferences = useAsync(() => seriesApi.inferences(), [])

  const series = useAsync(() => (roomId ? seriesApi.series(roomId) : Promise.resolve(null)), [roomId])
  const summary = useAsync(() => (roomId ? seriesApi.summary(roomId) : Promise.resolve(null)), [roomId])
  const runs = useAsync(() => (roomId ? seriesApi.runs(roomId) : Promise.resolve(null)), [roomId])
  const runDetail = useAsync(
    () => (roomId && selectedRunId ? seriesApi.run(roomId, selectedRunId) : Promise.resolve(null)),
    [roomId, selectedRunId]
  )

  const refresh = useCallback(() => {
    series.refetch()
    summary.refetch()
    runs.refetch()
    if (selectedRunId) runDetail.refetch()
  }, [series, summary, runs, runDetail, selectedRunId])

  /** Run one call and surface its own failure, rather than a page-wide error. */
  const act = useCallback(
    async (work) => {
      setBusy(true)
      setActionError(null)
      try {
        const result = await work()
        refresh()
        return result
      } catch (error) {
        setActionError(error)
        return null
      } finally {
        setBusy(false)
      }
    },
    [refresh]
  )

  if (vocabulary.loading || rooms.loading) return <Spinner label="Loading nurture series" />
  if (vocabulary.error) return <ErrorNote error={vocabulary.error} onRetry={vocabulary.refetch} />
  if (rooms.error) return <ErrorNote error={rooms.error} onRetry={rooms.refetch} />

  const vocab = vocabulary.data
  const roomOptions = (rooms.data?.records || []).map((room) => ({
    value: room.id,
    label: room.data?.name || room.id,
  }))

  return (
    <div className="space-y-6">
      <header className="space-y-2">
        <h1 className="flex items-center gap-2 font-display text-2xl font-semibold text-foreground">
          <Icon path={NURTURE_ICON} size={22} />
          Nurture series
        </h1>
        <p className="max-w-3xl text-sm text-muted-foreground">
          Rule, content, wait and tag blocks drip content to a buyer across channels. A block&apos;s
          send window retries while the buyer is offline; if they still have not come online, the
          offline branch falls back to email.
        </p>
        <div className="grid gap-3 sm:grid-cols-2">
          <Notice tone="info" title="The poll is explicit">
            {vocab.poll.quote}
          </Notice>
          <Notice tone="neutral" title="Nothing leaves this product">
            Every dispatch row carries sent_by_this_product: false. The channel decision and the
            fallback are real, recorded state; the send is a stand-in.
          </Notice>
        </div>
      </header>

      <Card>
        <Field label="Room" id="wf104-room" hint="A series belongs to a room.">
          <select
            id="wf104-room"
            className={inputClass}
            value={roomId}
            onChange={(event) => {
              setRoomId(event.target.value)
              setSelectedRunId('')
              setPreview(null)
              setExported(null)
            }}
          >
            <option value="">Choose a room…</option>
            {roomOptions.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </Field>
      </Card>

      {actionError && <ErrorNote error={actionError} onRetry={() => setActionError(null)} />}

      {!roomId ? (
        <EmptyState
          title="Pick a room"
          description="Choose a room above to see its nurture series, its runs and the poll."
        />
      ) : (
        <>
          <SummaryGrid summary={summary.data} />

          <SeriesSection
            vocab={vocab}
            series={series}
            busy={busy}
            preview={preview}
            exported={exported}
            onCreate={() =>
              act(async () => {
                const created = await seriesApi.createSeries(roomId, {
                  name: 'Enterprise evaluation nurture',
                  template: 'from_scratch',
                  blocks: TEMPLATE_BLOCKS,
                })
                setPreview(null)
                setExported(null)
                return created
              })
            }
            onGoLive={(id) => act(() => seriesApi.goLive(roomId, id))}
            onPreview={(id) =>
              act(async () => {
                const result = await seriesApi.preview(roomId, id, { audience: DEMO_AUDIENCE })
                setPreview(result)
                return result
              })
            }
            onExport={(id) =>
              act(async () => {
                const result = await seriesApi.exportSeries(roomId, id)
                setExported(result)
                return result
              })
            }
            onPoll={() => act(() => seriesApi.evaluate(roomId, { audience: DEMO_AUDIENCE }))}
          />

          <RunsSection
            roomId={roomId}
            vocab={vocab}
            runs={runs}
            runDetail={runDetail}
            selectedRunId={selectedRunId}
            busy={busy}
            onSelect={setSelectedRunId}
            onAdvance={(id) => act(() => seriesApi.advance(roomId, id))}
            onResolve={(id) => act(() => seriesApi.resolve(roomId, id))}
            onRecordClick={(id) => act(() => seriesApi.recordEvent(roomId, id, 'click'))}
          />

          <InferencesSection inferences={inferences} />
        </>
      )}
    </div>
  )
}

function SummaryGrid({ summary }) {
  const data = summary || {}
  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <StatCard label="Live series" value={data.series_by_state?.live ?? 0} hint="Draft series fire nothing" />
      <StatCard label="Runs" value={data.runs ?? 0} hint={`${data.due ?? 0} due now`} />
      <StatCard label="Dispatches" value={data.dispatches ?? 0} hint="Recorded channel attempts" />
      <StatCard label="Fallbacks" value={data.fallbacks ?? 0} hint="Offline buyers moved to email" />
    </div>
  )
}

function SeriesSection({
  vocab,
  series,
  busy,
  preview,
  exported,
  onCreate,
  onGoLive,
  onPreview,
  onExport,
  onPoll,
}) {
  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg font-semibold text-foreground">Series</h2>
        <div className="flex flex-wrap gap-2">
          <Button icon="plus" variant="primary" disabled={busy} onClick={onCreate}>
            New series
          </Button>
          <Button icon="refresh" disabled={busy} onClick={onPoll}>
            Run poll
          </Button>
        </div>
      </div>

      {series.loading && <Spinner label="Loading series" />}
      {series.error && <ErrorNote error={series.error} onRetry={series.refetch} />}
      {series.data && series.data.count === 0 && (
        <EmptyState
          title="No series yet"
          description="Click New series to lay down one rule, content, wait and tag block."
        />
      )}

      {(series.data?.series || []).map((entry) => (
        <Card key={entry.id}>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <h3 className="text-base font-semibold text-foreground">{entry.name}</h3>
              <p className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                <StateBadge state={entry.state} labels={{ draft: 'Draft', live: 'Live' }} />
                <span className="font-mono">{entry.id}</span>
                <span>{entry.runs} run(s)</span>
                <span>{entry.blocks.length} block(s)</span>
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              {entry.state === 'draft' && (
                <Button icon="chevron" disabled={busy} onClick={() => onGoLive(entry.id)}>
                  Set live
                </Button>
              )}
              <Button disabled={busy} onClick={() => onPreview(entry.id)}>
                Preview audience
              </Button>
              <Button disabled={busy} onClick={() => onExport(entry.id)}>
                Export
              </Button>
            </div>
          </div>

          <ul className="mt-3 space-y-2">
            {entry.blocks.map((block) => (
              <BlockCard
                key={block.id}
                block={block}
                labels={vocab.block_labels}
                channelLabels={vocab.channel_labels}
                contentTypeLabels={vocab.content_type_labels}
              />
            ))}
          </ul>
        </Card>
      ))}

      {preview && (
        <Notice tone="info" title={`Audience preview: ${preview.entered} of ${preview.audience_size} will enter`}>
          <ul className="mt-1 space-y-1">
            {preview.contacts.map((contact) => (
              <li key={contact.contact_id} className="font-mono text-xs">
                {contact.contact_id} - {contact.entered ? 'enters' : `excluded (${contact.reasons.join('; ')})`}
              </li>
            ))}
          </ul>
        </Notice>
      )}

      {exported && (
        <Card>
          <h3 className="text-base font-semibold text-foreground">Export (EU Data Act)</h3>
          <p className="mt-1 text-xs text-muted-foreground">{exported.quote}</p>
          <div className="mt-2">
            <JsonView value={exported} />
          </div>
        </Card>
      )}
    </section>
  )
}

function RunsSection({
  vocab,
  runs,
  runDetail,
  selectedRunId,
  busy,
  onSelect,
  onAdvance,
  onResolve,
  onRecordClick,
}) {
  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold text-foreground">Runs</h2>
      {runs.loading && <Spinner label="Loading runs" />}
      {runs.error && <ErrorNote error={runs.error} onRetry={runs.refetch} />}
      {runs.data && runs.data.count === 0 && (
        <EmptyState
          title="No runs yet"
          description="Set a series live and run the poll to enter matching buyers."
        />
      )}

      {runs.data && runs.data.count > 0 && (
        <Card>
          <ul className="divide-y divide-border-subtle">
            {runs.data.runs.map((run) => (
              <li key={run.id} className="flex flex-wrap items-center justify-between gap-3 py-2">
                <div className="min-w-0">
                  <p className="flex flex-wrap items-center gap-2 text-sm text-foreground">
                    <StateBadge state={run.state} labels={vocab.run_state_labels} />
                    <span className="font-mono text-xs">{run.contact_id}</span>
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {run.next_block ? `next: ${run.next_block.kind}` : 'finished'} -{' '}
                    {run.tags.length ? `tagged ${run.tags.join(', ')}` : 'no tags'}
                    {run.waiting_kind ? ` - waiting on ${run.waiting_kind} until ${run.resume_at}` : ''}
                  </p>
                </div>
                <Button disabled={busy} onClick={() => onSelect(run.id)}>
                  Show run
                </Button>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {selectedRunId && runDetail.data && (
        <Card>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h3 className="text-base font-semibold text-foreground">
                Run <span className="font-mono text-sm">{runDetail.data.id}</span>
              </h3>
              <p className="mt-1 text-xs text-muted-foreground">
                cursor {runDetail.data.cursor}, {runDetail.data.state}
                {runDetail.data.resume_at ? `, resume ${runDetail.data.resume_at}` : ''}
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button icon="chevron" disabled={busy} onClick={() => onAdvance(selectedRunId)}>
                Advance
              </Button>
              <Button disabled={busy} onClick={() => onResolve(selectedRunId)}>
                Resolve wait
              </Button>
              <Button disabled={busy} onClick={() => onRecordClick(selectedRunId)}>
                Record click
              </Button>
            </div>
          </div>

          <div className="mt-3 grid gap-4 lg:grid-cols-2">
            <div>
              <h4 className="text-sm font-semibold text-foreground">Dispatches</h4>
              {runDetail.data.dispatches.length === 0 && (
                <p className="mt-1 text-xs text-muted-foreground">No channel attempt yet.</p>
              )}
              <ul className="mt-1 space-y-1">
                {runDetail.data.dispatches.map((dispatch) => (
                  <li key={dispatch.id} className="text-xs text-muted-foreground">
                    <span className="font-mono">{dispatch.channel}</span> {dispatch.state}
                    {dispatch.is_fallback ? ` (fallback: ${dispatch.reason})` : ''}
                  </li>
                ))}
              </ul>
              <h4 className="mt-3 text-sm font-semibold text-foreground">Engagement</h4>
              <ul className="mt-1 space-y-1">
                {runDetail.data.engagement.length === 0 && (
                  <p className="text-xs text-muted-foreground">No engagement event yet.</p>
                )}
                {runDetail.data.engagement.map((event) => (
                  <li key={event.id} className="font-mono text-xs text-muted-foreground">
                    {event.event} at {event.at}
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <h4 className="text-sm font-semibold text-foreground">History</h4>
              <ul className="mt-1 space-y-1">
                {runDetail.data.activity.map((entry) => (
                  <li key={entry.id} className="text-xs text-muted-foreground">
                    <span className="font-mono">{entry.code}</span> {entry.at}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </Card>
      )}
    </section>
  )
}

function InferencesSection({ inferences }) {
  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold text-foreground">
        Decisions this build recorded
      </h2>
      <p className="text-sm text-muted-foreground">
        The research fixes the block kinds and the block behaviour; it leaves the poll, the
        audience mechanism and the event vocabulary open. Each choice and the alternative it
        rejected is listed here rather than left to a diff.
      </p>
      {inferences.loading && <Spinner label="Loading decisions" />}
      {inferences.error && <ErrorNote error={inferences.error} onRetry={inferences.refetch} />}
      {inferences.data && (
        <Card>
          <ul className="space-y-3">
            {inferences.data.decisions.map((decision) => (
              <li key={decision.id} className="border-b border-border-subtle pb-3 last:border-0 last:pb-0">
                <p className="text-sm font-medium text-foreground">{decision.question}</p>
                <p className="mt-1 text-xs text-muted-foreground">{decision.chosen}</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  Rejected: {decision.rejected}
                </p>
                {decision.jev_audit && (
                  <p className="mt-1 font-mono text-[11px] text-muted-foreground">
                    Jev {decision.jev_audit} (confidence {decision.confidence})
                  </p>
                )}
              </li>
            ))}
          </ul>
        </Card>
      )}
    </section>
  )
}

export { humaniseDuration }
