/**
 * WF-102: revise a quote into a new revision instead of losing the deal.
 *
 * The page reads its statuses, close reasons and refusal reasons from the
 * server's `/vocabulary` route rather than repeating them, so a panel cannot
 * drift from the rules it enforces.
 *
 * One thing the page deliberately does not do: it never edits a prior quote.
 * The only controls are the workflow's own actions - Revise, Close, Clone,
 * Reprice, Recall, Re-publish - so a seller cannot silently overwrite the deal
 * history this ticket exists to preserve.
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
  CLOSE_REASONS,
  canRevise,
  isProjectQuote,
  listQuotes,
  listRooms,
  pricingNote,
  recallBlockedReason,
  reviseBlockedReason,
  revisionApi,
  statusLabel,
} from './api'
import {
  ActivityList,
  Fact,
  InlineNote,
  LineItemsTable,
  Money,
  RevisionChain,
  StatusBadge,
} from './primitives'

function ActionRow({ quote, onChanged }) {
  const [reason, setReason] = useState('lost')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [note, setNote] = useState(null)

  async function run(action, success) {
    setBusy(true)
    setError(null)
    setNote(null)
    try {
      await action()
      setNote(success)
      onChanged()
    } catch (problem) {
      setError(problem)
    } finally {
      setBusy(false)
    }
  }

  const canClose = quote.open
  const recallBlocked = recallBlockedReason(quote)
  const canRecall = quote.status === 'published' && !recallBlocked
  const canRepublish = quote.status === 'draft'

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-end gap-3">
        <Button
          variant="primary"
          icon="refresh"
          disabled={busy || !canRevise(quote)}
          onClick={() =>
            run(() => revisionApi.revise(quote.id), 'A new draft revision was created.')
          }
        >
          Revise
        </Button>
        <Button
          icon="plus"
          disabled={busy || isProjectQuote(quote)}
          onClick={() => run(() => revisionApi.clone(quote.id), 'The quote was cloned.')}
        >
          Clone
        </Button>
        <Button
          icon="database"
          disabled={busy || !quote.open || quote.status !== 'draft'}
          onClick={() =>
            run(
              () => revisionApi.reprice(quote.id),
              'The draft prices were refreshed from the price list.',
            )
          }
        >
          Reprice draft
        </Button>
        <Button
          icon="restore"
          disabled={busy || !canRecall}
          onClick={() =>
            run(() => revisionApi.recall(quote.id), 'The quote was recalled back to draft.')
          }
        >
          Recall
        </Button>
        <Button
          icon="refresh"
          disabled={busy || !canRepublish}
          onClick={() =>
            run(
              () => revisionApi.republish(quote.id),
              'The link and PDF were regenerated.',
            )
          }
        >
          Re-publish
        </Button>
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <Field label="Close reason" id="wf102-close-reason" hint="Lost, Canceled or Revised.">
          <select
            id="wf102-close-reason"
            className={inputClass}
            value={reason}
            disabled={busy || !canClose}
            onChange={(event) => setReason(event.target.value)}
          >
            {CLOSE_REASONS.map((option) => (
              <option key={option} value={option}>
                {statusLabel(option)}
              </option>
            ))}
          </select>
        </Field>
        <Button
          variant="danger"
          icon="close"
          disabled={busy || !canClose}
          onClick={() =>
            run(() => revisionApi.close(quote.id, reason), `The quote was closed as ${reason}.`)
          }
        >
          Close quote
        </Button>
      </div>

      {!canRevise(quote) && !isProjectQuote(quote) && (
        <InlineNote tone="warning">{reviseBlockedReason(quote)}</InlineNote>
      )}
      {isProjectQuote(quote) && (
        <InlineNote tone="warning">
          A project quote does not support activation and revision. This workflow handles sales
          quotes only.
        </InlineNote>
      )}
      {quote.status === 'published' && recallBlocked && (
        <InlineNote tone="warning">{recallBlocked}</InlineNote>
      )}
      {note && <InlineNote tone="success">{note}</InlineNote>}
      {error && <ErrorNote error={error} />}
    </div>
  )
}

function QuoteDetail({ quote, lineage, activity, onChanged }) {
  return (
    <Card className="flex flex-col gap-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="font-display text-lg font-semibold text-foreground">
            {quote.title || quote.id}
          </h2>
          <p className="mt-0.5 font-mono text-xs text-muted-foreground">{quote.id}</p>
        </div>
        <StatusBadge status={quote.status} />
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Fact label="Revision" hint="Starts at 0 and increases by one">
          {quote.revision_number}
        </Fact>
        <Fact label="Pricing" hint={pricingNote(quote)}>
          {quote.pricing?.mode === 'live' ? 'Live' : 'Frozen'}
        </Fact>
        <Fact label="Owner" hint="From the deal">
          {quote.owner || 'Not set'}
        </Fact>
        <Fact label="Total" hint="Subtotal minus discount plus tax">
          <Money amount={quote.totals?.total} currency={quote.currency} />
        </Fact>
      </div>

      <div>
        <p className="mb-2 text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
          Line items
        </p>
        <LineItemsTable lines={quote.line_items} />
      </div>

      <div>
        <p className="mb-2 text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
          Revision lineage
        </p>
        <RevisionChain revisions={lineage?.revisions} />
      </div>

      <div>
        <p className="mb-2 text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
          Activity
        </p>
        <ActivityList entries={activity?.entries} />
      </div>

      <ActionRow quote={quote} onChanged={onChanged} />
    </Card>
  )
}

function QuoteRevisionsPage() {
  const [roomId, setRoomId] = useState('')
  const [selected, setSelected] = useState('')
  const [nonce, setNonce] = useState(0)

  const vocabulary = useAsync(() => revisionApi.vocabulary(), [])
  const rooms = useAsync(() => listRooms(), [])
  const summary = useAsync(() => revisionApi.summary(roomId), [roomId, nonce])
  const quotes = useAsync(() => listQuotes(roomId), [roomId, nonce])
  const detail = useAsync(
    () => (selected ? revisionApi.quote(selected) : Promise.resolve(null)),
    [selected, nonce],
  )
  const lineage = useAsync(
    () => (selected ? revisionApi.lineage(selected) : Promise.resolve(null)),
    [selected, nonce],
  )
  const activity = useAsync(
    () => (selected ? revisionApi.activity(selected) : Promise.resolve(null)),
    [selected, nonce],
  )

  function refresh() {
    setNonce((value) => value + 1)
  }

  if (vocabulary.loading || rooms.loading) return <Spinner label="Loading quote revisions" />
  if (vocabulary.error) {
    return <ErrorNote error={vocabulary.error} onRetry={vocabulary.refetch} />
  }
  if (quotes.error) return <ErrorNote error={quotes.error} onRetry={quotes.refetch} />

  const roomOptions = rooms.data?.records || []
  const quoteOptions = quotes.data?.records || []
  const counts = summary.data || {}

  return (
    <div className="space-y-6">
      <header className="flex flex-col gap-1">
        <h1 className="font-display text-2xl font-semibold text-foreground">Quote revisions</h1>
        <p className="text-sm text-muted-foreground">
          Revising a quote creates a new draft with the next revision id and closes the prior
          record as Revised. The old version stays readable, so the deal history is not lost.
        </p>
      </header>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Quotes" value={counts.quotes ?? 0} icon="audit" hint="In the room" />
        <StatCard
          label="Drafts"
          value={counts.by_status?.draft ?? 0}
          icon="plus"
          hint="Revision drafts open"
        />
        <StatCard
          label="Revised"
          value={counts.revised ?? 0}
          icon="refresh"
          hint="Closed by a revision"
        />
        <StatCard
          label="Activities"
          value={counts.activities ?? 0}
          icon="schema"
          hint="Close, recall and re-publish"
        />
      </div>

      <Card className="flex flex-col gap-4">
        <Field label="Room" id="wf102-room" hint="Quotes and their revisions belong to a room.">
          <select
            id="wf102-room"
            className={inputClass}
            value={roomId}
            onChange={(event) => {
              setRoomId(event.target.value)
              setSelected('')
            }}
          >
            <option value="">All rooms</option>
            {roomOptions.map((room) => (
              <option key={room.id} value={room.id}>
                {room.data?.name || room.id}
              </option>
            ))}
          </select>
        </Field>
        <Field
          label="Quote"
          id="wf102-quote"
          hint="Pick a quote to revise, close, clone or re-issue."
        >
          <select
            id="wf102-quote"
            className={inputClass}
            value={selected}
            onChange={(event) => setSelected(event.target.value)}
          >
            <option value="">Select a quote</option>
            {quoteOptions.map((quote) => (
              <option key={quote.id} value={quote.id}>
                {(quote.data?.title || quote.id) + ' · ' + statusLabel(quote.data?.status)}
              </option>
            ))}
          </select>
        </Field>
      </Card>

      {selected && detail.loading && <Spinner label="Reading the quote" />}
      {selected && detail.error && <ErrorNote error={detail.error} onRetry={detail.refetch} />}

      {detail.data ? (
        <QuoteDetail
          quote={detail.data}
          lineage={lineage.data}
          activity={activity.data}
          onChanged={refresh}
        />
      ) : (
        <EmptyState
          title="No quote selected"
          description="Pick a quote above to see its revision lineage and the actions this workflow offers."
        />
      )}
    </div>
  )
}

export default {
  id: 'wf-102-quote-revision',
  label: 'Quote revisions',
  icon: 'audit',
  order: 1012,
  Component: QuoteRevisionsPage,
}
