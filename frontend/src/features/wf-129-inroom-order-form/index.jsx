/**
 * WF-129: quote-to-order-form inside the room, with e-signature.
 *
 * The page reads its words, statuses and refusal reasons from the server's
 * `/vocabulary` route rather than repeating them, so a panel cannot drift from
 * the rules it enforces.
 *
 * Two things this page deliberately does not do, both from the research:
 *
 *   - It offers no way to author a quote. WF-086 owns that, so the only control
 *     is "Render in room", which keys the document on the quote the seller
 *     picks.
 *   - It takes no payment and files no MAP. Taking money is a separate flow and
 *     this repository owns no MAP surface, so the page states both as out of
 *     scope rather than showing a control that does nothing.
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

import { canAccept, canEdit, canFile, canSign, listQuotes, listRooms, orderFormApi } from './api'
import { CertificatePanel, Fact, InlineNote, LinesTable, Money, StatusBadge } from './primitives'

const SIGNER_DEFAULT = { name: '', email: '' }

function CreatePanel({ quotes, roomId, onCreated }) {
  const [quoteId, setQuoteId] = useState('')
  const [title, setTitle] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  async function create() {
    setBusy(true)
    setError(null)
    try {
      await orderFormApi.create(roomId, {
        quote_id: quoteId,
        ...(title ? { title } : {}),
      })
      setQuoteId('')
      setTitle('')
      onCreated()
    } catch (problem) {
      setError(problem)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card className="flex flex-col gap-4">
      <h2 className="text-lg font-semibold text-foreground">Render a quote in the room</h2>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Quote" id="wf129-quote" hint="A quote from WF-086 to render and sign.">
          <select
            id="wf129-quote"
            className={inputClass}
            value={quoteId}
            onChange={(event) => setQuoteId(event.target.value)}
          >
            <option value="">Select a quote</option>
            {quotes.map((quote) => (
              <option key={quote.id} value={quote.id}>
                {quote.data?.title || quote.id}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Order form name" id="wf129-title" hint="Optional. Defaults to the quote title.">
          <input
            id="wf129-title"
            className={inputClass}
            value={title}
            onChange={(event) => setTitle(event.target.value)}
          />
        </Field>
      </div>
      {error && <ErrorNote error={error} />}
      <div>
        <Button variant="primary" icon="plus" disabled={busy || !quoteId} onClick={create}>
          Render in room
        </Button>
      </div>
    </Card>
  )
}

function RevisionList({ form, onChanged }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  async function resolve(revisionId, decision) {
    setBusy(true)
    setError(null)
    try {
      await orderFormApi.resolveRevision(revisionId, decision)
      onChanged()
    } catch (problem) {
      setError(problem)
    } finally {
      setBusy(false)
    }
  }

  const revisions = form.revisions || []
  if (revisions.length === 0) return null

  return (
    <div className="flex flex-col gap-2">
      <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
        Redlines
      </p>
      {revisions.map((revision) => (
        <div
          key={revision.id}
          className="flex flex-wrap items-center justify-between gap-2 rounded-sm border border-border-subtle px-3 py-2"
        >
          <p className="text-xs text-foreground">
            {revision.proposed_by === 'buyer' ? 'Buyer' : 'Seller'} proposed{' '}
            <span className="font-mono">{revision.field}</span> ={' '}
            <span className="font-mono">{String(revision.proposed_value)}</span>{' '}
            <span className="text-muted-foreground">({revision.state})</span>
          </p>
          {revision.state === 'proposed' && (
            <div className="flex flex-wrap gap-2">
              <Button
                icon="restore"
                disabled={busy}
                aria-label={`Accept redline on ${revision.field}`}
                onClick={() => resolve(revision.id, 'accept')}
              >
                Accept redline
              </Button>
              <Button
                variant="ghost"
                icon="close"
                disabled={busy}
                aria-label={`Reject redline on ${revision.field}`}
                onClick={() => resolve(revision.id, 'reject')}
              >
                Reject redline
              </Button>
            </div>
          )}
        </div>
      ))}
      {error && <ErrorNote error={error} />}
    </div>
  )
}

function EditLinePanel({ form, onChanged }) {
  const [lineId, setLineId] = useState('')
  const [quantity, setQuantity] = useState('')
  const [field, setField] = useState('quantity')
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [note, setNote] = useState(null)

  async function run(action) {
    setBusy(true)
    setError(null)
    setNote(null)
    try {
      await action()
      onChanged()
    } catch (problem) {
      setError(problem)
    } finally {
      setBusy(false)
    }
  }

  const selected = form.lines.find((line) => line.id === lineId) || null

  return (
    <div className="flex flex-col gap-3">
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Line" id={`wf129-line-${form.id}`} hint="The buyer may still edit a draft line.">
          <select
            id={`wf129-line-${form.id}`}
            className={inputClass}
            value={lineId}
            onChange={(event) => setLineId(event.target.value)}
          >
            <option value="">Select a line</option>
            {form.lines.map((line) => (
              <option key={line.id} value={line.id}>
                {line.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Quantity" id={`wf129-qty-${form.id}`}>
          <input
            id={`wf129-qty-${form.id}`}
            className={inputClass}
            inputMode="numeric"
            value={quantity}
            onChange={(event) => setQuantity(event.target.value)}
          />
        </Field>
      </div>
      <div>
        <Button
          icon="refresh"
          disabled={busy || !lineId || quantity === ''}
          aria-label="Save the line quantity"
          onClick={() =>
            run(async () => {
              await orderFormApi.updateLine(form.id, lineId, { quantity: Number(quantity) })
              setNote('Line updated. The totals were recomputed.')
            })
          }
        >
          Save quantity
        </Button>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Redline field" id={`wf129-field-${form.id}`}>
          <select
            id={`wf129-field-${form.id}`}
            className={inputClass}
            value={field}
            onChange={(event) => setField(event.target.value)}
          >
            <option value="quantity">quantity</option>
            <option value="unit_price">unit_price</option>
            <option value="tax_rate">tax_rate</option>
          </select>
        </Field>
        <Field label="Proposed value" id={`wf129-value-${form.id}`}>
          <input
            id={`wf129-value-${form.id}`}
            className={inputClass}
            value={value}
            onChange={(event) => setValue(event.target.value)}
          />
        </Field>
      </div>
      <div>
        <Button
          icon="audit"
          disabled={busy || !lineId || value === ''}
          aria-label="Propose a redline"
          onClick={() =>
            run(async () => {
              await orderFormApi.proposeRevision(form.id, {
                line_id: lineId,
                field,
                proposed_value: Number(value),
                proposed_by: 'buyer',
              })
              setNote('Redline proposed. It applies only when accepted.')
            })
          }
        >
          Propose a redline
        </Button>
      </div>
      {selected && (
        <p className="font-mono text-xs text-muted-foreground">
          {selected.name}: {selected.quantity} x {Number(selected.unit_price || 0).toFixed(2)}
        </p>
      )}
      {note && <InlineNote tone="success">{note}</InlineNote>}
      {error && <ErrorNote error={error} />}
    </div>
  )
}

function SignPanel({ form, signer, onChanged }) {
  const [mode, setMode] = useState('typed')
  const [text, setText] = useState('')
  const [image, setImage] = useState('')
  const [strokes, setStrokes] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  async function sign() {
    setBusy(true)
    setError(null)
    try {
      const payload = { mode, signer }
      if (mode === 'typed') payload.text = text
      if (mode === 'typed_photo') {
        payload.text = text
        payload.image = image
      }
      if (mode === 'drawn') {
        payload.strokes = strokes
          .split(',')
          .map((pair) => pair.trim())
          .filter(Boolean)
          .map((pair) => pair.split(':').map((n) => Number(n)))
      }
      await orderFormApi.sign(form.id, payload)
      onChanged()
    } catch (problem) {
      setError(problem)
    } finally {
      setBusy(false)
    }
  }

  const ready =
    signer.name &&
    signer.email &&
    (mode === 'drawn' ? strokes.trim() !== '' : text.trim() !== '')

  return (
    <div className="flex flex-col gap-3">
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Sign as" id={`wf129-sign-mode-${form.id}`}>
          <select
            id={`wf129-sign-mode-${form.id}`}
            className={inputClass}
            value={mode}
            onChange={(event) => setMode(event.target.value)}
          >
            <option value="typed">Type a name</option>
            <option value="drawn">Draw strokes</option>
            <option value="typed_photo">Type and photograph</option>
          </select>
        </Field>
        {mode === 'drawn' ? (
          <Field label="Strokes" id={`wf129-strokes-${form.id}`} hint="Pairs like 0:0, 10:12, 22:8.">
            <input
              id={`wf129-strokes-${form.id}`}
              className={inputClass}
              value={strokes}
              onChange={(event) => setStrokes(event.target.value)}
            />
          </Field>
        ) : (
          <Field label="Typed name" id={`wf129-sign-text-${form.id}`}>
            <input
              id={`wf129-sign-text-${form.id}`}
              className={inputClass}
              value={text}
              onChange={(event) => setText(event.target.value)}
            />
          </Field>
        )}
        {mode === 'typed_photo' && (
          <Field label="Signature image" id={`wf129-sign-image-${form.id}`}>
            <input
              id={`wf129-sign-image-${form.id}`}
              className={inputClass}
              value={image}
              onChange={(event) => setImage(event.target.value)}
            />
          </Field>
        )}
      </div>
      {error && <ErrorNote error={error} />}
      <div>
        <Button variant="primary" icon="audit" disabled={busy || !ready} onClick={sign}>
          Sign in the room
        </Button>
      </div>
    </div>
  )
}

function OrderFormCard({ form, onChanged }) {
  const [signer, setSigner] = useState(SIGNER_DEFAULT)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  async function run(action) {
    setBusy(true)
    setError(null)
    try {
      await action()
      onChanged()
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
          <h3 className="text-base font-semibold text-foreground">{form.title || form.id}</h3>
          <p className="mt-0.5 font-mono text-xs text-muted-foreground">{form.id}</p>
        </div>
        <StatusBadge status={form.status} />
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Fact label="Currency">{form.currency || 'Not set'}</Fact>
        <Fact label="Version" hint="Bumped by each accepted redline">
          {form.version}
        </Fact>
        <Fact label="Lines">{form.line_count}</Fact>
        <Fact label="Total" hint="The figure the signature binds to">
          <Money amount={form.totals?.total} currency={form.currency} />
        </Fact>
      </div>

      <div>
        <p className="mb-2 text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
          Itemised document
        </p>
        <LinesTable lines={form.lines} />
      </div>

      {form.status === 'draft' && (
        <InlineNote tone="info">
          This draft is buyer-editable. A redline applies only when the other side accepts it.
        </InlineNote>
      )}
      {form.status === 'accepted' && (
        <InlineNote tone="info">
          The buyer accepted this form. Its lines are frozen and it waits on a signature, in the
          room.
        </InlineNote>
      )}

      <RevisionList form={form} onChanged={onChanged} />
      {canEdit(form) && <EditLinePanel form={form} onChanged={onChanged} />}

      {canAccept(form) && (
        <div>
          <Button
            icon="restore"
            disabled={busy}
            aria-label="Accept the order form"
            onClick={() =>
              run(() =>
                orderFormApi.accept(form.id, {
                  accepted_by: signer.name || 'Buyer',
                }),
              )
            }
          >
            Accept the order form
          </Button>
        </div>
      )}

      {canSign(form) && (
        <div className="flex flex-col gap-3 border-t border-border-subtle pt-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Signer name" id={`wf129-signer-name-${form.id}`}>
              <input
                id={`wf129-signer-name-${form.id}`}
                className={inputClass}
                value={signer.name}
                onChange={(event) => setSigner({ ...signer, name: event.target.value })}
              />
            </Field>
            <Field label="Signer email" id={`wf129-signer-email-${form.id}`}>
              <input
                id={`wf129-signer-email-${form.id}`}
                className={inputClass}
                value={signer.email}
                onChange={(event) => setSigner({ ...signer, email: event.target.value })}
              />
            </Field>
          </div>
          <SignPanel form={form} signer={signer} onChanged={onChanged} />
        </div>
      )}

      {canFile(form) && (
        <div>
          <Button
            icon="database"
            disabled={busy}
            aria-label="File the order form on the deal"
            onClick={() => run(() => orderFormApi.file(form.id))}
          >
            File on the deal
          </Button>
        </div>
      )}

      {form.status === 'signed' && (
        <InlineNote tone="success">
          Signed in the room. Filing writes the total and the close date onto the deal.
        </InlineNote>
      )}
      {form.status === 'filed' && (
        <InlineNote tone="success">
          Filed on the deal. MAP tasks are not advanced; this repository owns no MAP surface.
        </InlineNote>
      )}

      <CertificatePanel certificate={form.certificate} />
      {error && <ErrorNote error={error} />}
    </Card>
  )
}

function OrderFormPage() {
  const [roomId, setRoomId] = useState('')
  const [nonce, setNonce] = useState(0)

  const rooms = useAsync(() => listRooms(), [])
  const vocabulary = useAsync(() => orderFormApi.vocabulary(), [])
  const quotes = useAsync(() => listQuotes(), [])
  const summary = useAsync(() => orderFormApi.summary(roomId), [roomId, nonce])
  const forms = useAsync(() => orderFormApi.orderForms(roomId), [roomId, nonce])

  if (vocabulary.loading || rooms.loading) return <Spinner label="Loading order forms" />
  if (vocabulary.error) return <ErrorNote error={vocabulary.error} onRetry={vocabulary.refetch} />
  if (forms.error) return <ErrorNote error={forms.error} onRetry={forms.refetch} />

  const entries = forms.data?.order_forms || []
  const roomOptions = rooms.data?.records || []
  const quoteOptions = quotes.data?.records || []

  function refresh() {
    setNonce((value) => value + 1)
  }

  return (
    <div className="space-y-6">
      <header className="flex flex-col gap-1">
        <h1 className="font-display text-2xl font-semibold text-foreground">In-room order forms</h1>
        <p className="text-sm text-muted-foreground">
          Render a quote as an itemised, buyer-editable document inside the room. The buyer accepts
          it, redlines a line, and signs there without a redirect. No e-sign provider is reached: the
          signature is recorded with an audit certificate that says so.
        </p>
      </header>

      <Card className="flex flex-col gap-3">
        <Field label="Room" id="wf129-room" hint="An order form is signed inside a room.">
          <select
            id="wf129-room"
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
      </Card>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Order forms"
          value={summary.data?.order_forms ?? 0}
          icon="database"
          hint="Rendered from quotes"
        />
        <StatCard
          label="Editable"
          value={summary.data?.editable ?? 0}
          icon="schema"
          hint="Drafts a buyer may redline"
        />
        <StatCard
          label="Signed"
          value={summary.data?.signed ?? 0}
          icon="audit"
          hint="Signature recorded in the room"
        />
        <StatCard
          label="Filed"
          value={summary.data?.filed ?? 0}
          icon="database"
          hint="Written back on the deal"
        />
      </div>

      <CreatePanel quotes={quoteOptions} roomId={roomId} onCreated={refresh} />

      <section className="space-y-4">
        <h2 className="font-display text-lg font-semibold text-foreground">Order forms</h2>
        {entries.length === 0 ? (
          <EmptyState
            title="No order forms yet"
            description="Render a quote above and the signable document appears here."
          />
        ) : (
          <div className="space-y-4">
            {entries.map((form) => (
              <OrderFormCard key={form.id} form={form} onChanged={refresh} />
            ))}
          </div>
        )}
      </section>
    </div>
  )
}

export default {
  id: 'wf-129-inroom-order-form',
  label: 'In-room order form',
  icon: 'schema',
  order: 130,
  Component: OrderFormPage,
}
