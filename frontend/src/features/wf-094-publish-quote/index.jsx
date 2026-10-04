/**
 * WF-094: publish a quote to a hosted link, then share it by link, PDF or email.
 *
 * The page is built from `@/components/ui` and from three primitives in this
 * folder. It reads its statuses, its caps and its ceilings from the server's
 * `/vocabulary` route rather than repeating them, so the panel cannot tell a
 * seller a cap the rules do not enforce.
 *
 * Two things this page deliberately does not do, both quoted from
 * `docs/research/digital-sales-room-workflows/wf/WF-094.md`:
 *
 *   - It offers no field for the public link. `hs_quote_link` "is a read-only
 *     property and cannot be set through the API after publishing", so a seller
 *     publishes and then copies the link the server computed.
 *   - It does not claim to have sent an email. There is no mail transport in
 *     this product; what the page shows is the recorded send event.
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
  exceedsAttachmentCap,
  isLocked,
  isShareable,
  listRooms,
  megabytes,
  quoteApi,
} from './api'
import { CopyField, Fact, FallbackNote, FrozenAmount, InlineNote, StatusBadge } from './primitives'

function QuoteRow({ quote, vocabulary, onRefresh }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [note, setNote] = useState(null)

  const locked = isLocked(quote)
  const shareable = isShareable(quote)
  const derived = quote?.derived || {}
  const capBytes = vocabulary?.limits?.email_attachment_cap_bytes
  const ccLimit = vocabulary?.limits?.cc

  async function run(action, successNote) {
    setBusy(true)
    setError(null)
    setNote(null)
    try {
      await action()
      setNote(successNote)
      onRefresh()
    } catch (problem) {
      setError(problem)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card className="flex flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-base font-semibold text-foreground">
            {quote?.data?.title || quote?.data?.quote_number || quote?.id}
          </h3>
          <p className="mt-0.5 font-mono text-xs text-muted-foreground">
            {quote?.data?.hs_quote_number || quote?.data?.quote_number || 'No quote number'}
          </p>
        </div>
        <StatusBadge quote={quote} />
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Fact label="Total" hint={locked ? 'Locked at publish' : 'Editable'}>
          <FrozenAmount amount={quote?.data?.locked_amount} locked={locked} />
        </Fact>
        <Fact
          label="Domain"
          hint={quote?.data?.domain_fallback ? 'Default fallback domain' : null}
        >
          <span className="font-mono">{quote?.data?.hs_domain || 'Not published'}</span>
        </Fact>
        <Fact label="Slug">
          <span className="font-mono">{quote?.data?.hs_slug || 'Not set'}</span>
        </Fact>
        <Fact label="Locale">
          <span className="font-mono">{quote?.data?.hs_locale || 'Not set'}</span>
        </Fact>
      </div>

      {shareable && (
        <CopyField
          label="Public link"
          value={quote.data.hs_quote_link}
          hint="Computed when the quote was published. A caller cannot set it."
        />
      )}

      {note && (
        <p role="status" aria-live="polite" className="text-xs text-success">
          {note}
        </p>
      )}
      {error && <ErrorNote error={error} />}

      <div className="flex flex-wrap gap-2">
        {derived?.can_publish && (
          <>
            <Button
              variant="primary"
              icon="audit"
              disabled={busy}
              onClick={() =>
                run(
                  () => quoteApi.publish(quote.id, { sharedOnly: false }),
                  'Published. The total is now locked and the link is live.',
                )
              }
            >
              Publish and lock
            </Button>
            <Button
              icon="search"
              disabled={busy}
              onClick={() =>
                run(
                  () => quoteApi.publish(quote.id, { sharedOnly: true }),
                  'Shared. The quote is viewable and the total is still editable.',
                )
              }
            >
              Share only
            </Button>
          </>
        )}

        {shareable && (
          <Button
            icon="audit"
            disabled={busy}
            onClick={() =>
              run(() => quoteApi.copyLink(quote.id), 'Recorded. The link is ready to send.')
            }
          >
            Record a copy
          </Button>
        )}

        <EmailButton quote={quote} capBytes={capBytes} ccLimit={ccLimit} onRefresh={onRefresh} />

        <Button
          icon="database"
          disabled={busy}
          onClick={() =>
            run(
              () => quoteApi.requestPdf(quote.id),
              'Recorded. The file is named by the record name and the record id.',
            )
          }
        >
          Record a PDF
        </Button>
      </div>

      {derived?.can_unlock && (
        <UnlockRow quote={quote} targets={derived.unlock_targets} onRefresh={onRefresh} />
      )}
    </Card>
  )
}

function UnlockRow({ quote, targets, onRefresh }) {
  const [target, setTarget] = useState(targets?.[0] || 'DRAFT')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  async function unlock() {
    setBusy(true)
    setError(null)
    try {
      await quoteApi.unlock(quote.id, target)
      onRefresh()
    } catch (problem) {
      setError(problem)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-2 rounded-sm border border-border-subtle bg-muted p-3">
      <Field
        label="Unlock by moving the status"
        id={`wf094-unlock-${quote.id}`}
        hint="The research names three targets. Nothing else releases the total."
      >
        <select
          id={`wf094-unlock-${quote.id}`}
          className={inputClass}
          value={target}
          onChange={(event) => setTarget(event.target.value)}
        >
          {(targets || []).map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      </Field>
      <div>
        <Button disabled={busy} onClick={unlock}>
          Unlock the total
        </Button>
      </div>
      {error && <ErrorNote error={error} />}
    </div>
  )
}

function EmailButton({ quote, capBytes, ccLimit, onRefresh }) {
  const [open, setOpen] = useState(false)
  const [to, setTo] = useState('')
  const [cc, setCc] = useState('')
  const [size, setSize] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [sent, setSent] = useState(null)

  const ccList = cc
    .split(',')
    .map((entry) => entry.trim())
    .filter(Boolean)
  const overCap = exceedsAttachmentCap(size, capBytes)

  async function send() {
    setBusy(true)
    setError(null)
    setSent(null)
    try {
      const event = await quoteApi.sendEmail(quote.id, {
        to: [to.trim()],
        cc: ccList,
        ...(size === '' ? {} : { pdf_size_bytes: Number(size) }),
      })
      setSent(event.data)
      setOpen(false)
      onRefresh()
    } catch (problem) {
      setError(problem)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <Button icon="audit" disabled={!isShareable(quote)} onClick={() => setOpen(!open)}>
        {open ? 'Cancel' : 'Record an email'}
      </Button>

      {sent && (
        <p role="status" aria-live="polite" className="text-xs text-success">
          Recorded for {sent.to}.{' '}
          {sent.pdf_attached
            ? 'The PDF was attached.'
            : sent.attachment_note || 'No PDF was attached.'}
        </p>
      )}

      {open && (
        <div className="flex flex-col gap-3 rounded-sm border border-border-subtle bg-muted p-3">
          <Field label="To" id={`wf094-to-${quote.id}`} hint="One address. It fills from the contact.">
            <input
              id={`wf094-to-${quote.id}`}
              className={inputClass}
              value={to}
              onChange={(event) => setTo(event.target.value)}
              type="email"
            />
          </Field>

          <Field
            label="Cc"
            id={`wf094-cc-${quote.id}`}
            hint={`Comma separated. Up to ${ccLimit ?? 9} addresses.`}
          >
            <input
              id={`wf094-cc-${quote.id}`}
              className={inputClass}
              value={cc}
              onChange={(event) => setCc(event.target.value)}
            />
          </Field>

          {ccList.length > (ccLimit ?? 9) && (
            <p className="text-xs text-destructive">
              {ccList.length} addresses. The cap is {ccLimit ?? 9} and the send is refused.
            </p>
          )}

          <Field
            label="PDF size in bytes"
            id={`wf094-size-${quote.id}`}
            hint="Optional. It decides whether the attachment is kept."
          >
            <input
              id={`wf094-size-${quote.id}`}
              className={inputClass}
              value={size}
              onChange={(event) => setSize(event.target.value)}
              inputMode="numeric"
            />
          </Field>

          {overCap && (
            <InlineNote tone="warning">
              {megabytes(size)} MB is above the {megabytes(capBytes)} MB cap. The email is
              still recorded and the PDF is left out of it.
            </InlineNote>
          )}

          {error && <ErrorNote error={error} />}

          <div>
            <Button
              variant="primary"
              disabled={busy || to.trim() === ''}
              onClick={send}
            >
              Record the send
            </Button>
          </div>
        </div>
      )}
    </>
  )
}

function PublishQuotePage() {
  const [roomId, setRoomId] = useState('')
  const [status, setStatus] = useState('')

  const rooms = useAsync(() => listRooms(), [])
  const vocabulary = useAsync(() => quoteApi.vocabulary(), [])

  // An empty roomId means every room, which is what the selector's first option
  // says. The routes take room_id as optional, so the request is made either
  // way and the page is never empty just because nobody has picked a filter.
  const quotes = useAsync(() => quoteApi.quotes(roomId, status), [roomId, status])
  const settings = useAsync(() => quoteApi.settings(roomId), [roomId])

  if (vocabulary.loading || rooms.loading) return <Spinner label="Loading quote publishing" />
  if (vocabulary.error) return <ErrorNote error={vocabulary.error} onRetry={vocabulary.refetch} />
  if (quotes.error) return <ErrorNote error={quotes.error} onRetry={quotes.refetch} />

  const entries = quotes.data?.entries || []
  const roomOptions = rooms.data?.entries || []

  return (
    <div className="space-y-6">
      <header className="flex flex-col gap-1">
        <h1 className="font-display text-2xl font-semibold text-foreground">
          Publish and share a quote
        </h1>
        <p className="text-sm text-muted-foreground">
          Publishing computes a public link and locks the total. Sharing makes the quote
          viewable and leaves the total editable. The link is never typed in by a caller.
        </p>
      </header>

      {settings.data?.domain_fallback && (
        <FallbackNote>
          No quote domain is connected, so published quotes are served from the default
          domain {settings.data?.domain}. Connect a domain to serve them from your own.
        </FallbackNote>
      )}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Quotes"
          value={quotes.data?.count ?? 0}
          icon="database"
          hint="In the selected room"
        />
        <StatCard
          label="Locked"
          value={entries.filter((entry) => entry?.derived?.locked).length}
          icon="audit"
          hint="Total frozen at publish"
        />
        <StatCard
          label="Shareable"
          value={entries.filter((entry) => isShareable(entry)).length}
          icon="search"
          hint="Have a public link"
        />
        <StatCard
          label="Sent"
          value={entries.filter((entry) => entry?.derived?.sent).length}
          icon="plus"
          hint="Have an email record"
        />
      </div>

      <Card className="flex flex-col gap-3">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Room" id="wf094-room" hint="Quotes belong to a room.">
            <select
              id="wf094-room"
              className={inputClass}
              value={roomId}
              onChange={(event) => setRoomId(event.target.value)}
            >
              <option value="">All rooms</option>
              {roomOptions.map((room) => (
                <option key={room.id} value={room.id}>
                  {room.data?.name || room.id}
                </option>
              ))}
            </select>
          </Field>

          <Field label="Status" id="wf094-status" hint="Filter to one quote status.">
            <select
              id="wf094-status"
              className={inputClass}
              value={status}
              onChange={(event) => setStatus(event.target.value)}
            >
              <option value="">Every status</option>
              {(vocabulary.data?.statuses || []).map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </Field>
        </div>
      </Card>

      {entries.length === 0 && (
        <EmptyState
          title="No quotes to publish"
          description="No quote matched this filter. Publishing works on a quote that already exists, with its lines and its total."
        />
      )}

      <div className="space-y-4">
        {entries.map((quote) => (
          <QuoteRow
            key={quote.id}
            quote={quote}
            vocabulary={vocabulary.data}
            onRefresh={quotes.refetch}
          />
        ))}
      </div>
    </div>
  )
}

export default {
  id: 'wf-094-publish-quote',
  label: 'Publish a quote',
  icon: 'audit',
  order: 940,
  Component: PublishQuotePage,
}