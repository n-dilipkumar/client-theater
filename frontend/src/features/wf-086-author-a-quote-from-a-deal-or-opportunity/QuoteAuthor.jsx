import { useCallback, useMemo, useState } from 'react'
import {
  Button,
  Card,
  EmptyState,
  ErrorNote,
  Field,
  Icon,
  Spinner,
  StatCard,
  inputClass,
  useAsync,
} from '@/components/ui'
import {
  CONTRACT_VALUE_KEY,
  TOTAL_ROWS,
  conflictText,
  formatMoney,
  listDeals,
  listRooms,
  quoteApi,
} from './api'
import { ModuleRow, Notice, PriceSourceLabel, StatusBadge, UnpricedNote } from './primitives'

/**
 * WF-086: author a quote from a deal or opportunity.
 *
 * The page is the seller's side of the research's flow and nothing else. It opens
 * on a deal picker because the flow starts on a deal: `CRM > Deals`, open a deal,
 * on the *Quotes* card click `+ Add`, choose **Create quote**. A seller who has no
 * deal in front of them has nothing to quote from, so the deal is the entry point
 * rather than a filter over a list of quotes.
 *
 * Three states the page must render, because a page that shows none of them looks
 * broken rather than empty:
 *
 *   * **loading**, through `useAsync`;
 *   * **no deal to quote from**, which is a real state on a fresh database;
 *   * **no catalogue**, which is the state WF-087's absence produces. Every line
 *     then carries the unit price the deal had, and the page says so instead of
 *     showing a price that came from somewhere it will not name.
 *
 * What the page deliberately does not do
 * --------------------------------------
 *
 * It does not compute a total. `formatMoney` formats what the server sent, and the
 * server recomputes on every write. A page that added up its own column would show
 * a number that disagrees with the audit row the moment rounding differed, and the
 * person who has to reconcile those two is the seller.
 *
 * It does not filter line items by audience, because there is no audience on a
 * quote. Nothing here is hidden from a buyer; the quote is the seller's document
 * and the deal is the shared record.
 */

const SECTION = 'space-y-6'
const HEADING = 'text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground'

function DealPicker({ rooms, deals, busy, onCreate, errors }) {
  const [pickedRoom, setPickedRoom] = useState('')
  const [pickedDeal, setPickedDeal] = useState('')

  // The effective selection is derived, not stored through an effect.
  //
  // Writing "the first room" into state from a useEffect costs a second render
  // for every page that opens on a default, and React's own lint rule names the
  // pattern. Deriving it means the first render already carries a selection, so
  // there is no frame where the create button is disabled for a reason the page
  // cannot show.
  const roomId = pickedRoom || rooms[0]?.id || ''
  const dealId = pickedDeal || deals[0]?.id || ''
  const chosen = deals.find((deal) => deal.id === dealId)

  if (!deals.length) {
    return (
      <EmptyState
        title="No deal to quote from"
        description="A quote is authored from a deal or an opportunity, so this page needs one in the CRM mirror first. Mirror a deal, then come back."
      />
    )
  }

  return (
    <Card>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field id="wf086-room" label="Room" hint="Which sales room the quote belongs to.">
          <select
            id="wf086-room"
            className={inputClass}
            value={roomId}
            onChange={(event) => {
              setPickedRoom(event.target.value)
              setPickedDeal('')
            }}
          >
            {rooms.map((room) => (
              <option key={room.id} value={room.id}>
                {room.data?.name || room.id}
              </option>
            ))}
          </select>
        </Field>
        <Field
          id="wf086-deal"
          label="Deal"
          hint="The quote header is prefilled from the deal, and its line items are cloned."
        >
          <select
            id="wf086-deal"
            className={inputClass}
            value={dealId}
            onChange={(event) => setPickedDeal(event.target.value)}
          >
            {deals.map((deal) => (
              <option key={deal.id} value={deal.id}>
                {deal.data?.name || deal.id}
              </option>
            ))}
          </select>
        </Field>
      </div>

      {chosen && (
        <dl className="mt-4 grid gap-3 border-t border-border-subtle pt-4 sm:grid-cols-4">
          {[
            ['Account', chosen.data?.account],
            ['Owner', chosen.data?.owner],
            ['Currency', chosen.data?.currency || 'USD'],
            ['Stage', chosen.data?.stage],
          ].map(([label, value]) => (
            <div key={label}>
              <dt className={HEADING}>{label}</dt>
              <dd className="mt-1 text-sm text-foreground">{value || 'Not set'}</dd>
            </div>
          ))}
        </dl>
      )}

      {errors?.deal_id && (
        <p className="mt-4 text-sm font-medium text-destructive">{errors.deal_id}</p>
      )}

      <div className="mt-5 flex flex-wrap items-center gap-3">
        <Button
          variant="primary"
          disabled={!dealId || busy}
          onClick={() => onCreate(dealId, roomId)}
          icon="plus"
        >
          Create quote
        </Button>
        <p className="text-xs text-muted-foreground">
          Every line item on the deal becomes a line item on the quote, with its own record id.
        </p>
      </div>
    </Card>
  )
}

function TotalsPanel({ totals, currency }) {
  return (
    <div className="rounded-sm border border-border-subtle bg-muted">
      <div className="border-b border-border-subtle p-4">
        <p className={HEADING}>Total contract value</p>
        <p className="mt-1 font-mono text-2xl font-semibold text-foreground">
          {formatMoney(totals?.[CONTRACT_VALUE_KEY], currency)}
        </p>
        <p className="mt-1 text-xs text-muted-foreground">
          The total plus the payments dated after today. Publishing copies this figure, and only
          this figure, onto the deal amount.
        </p>
      </div>
      <dl className="divide-y divide-border-subtle">
        {TOTAL_ROWS.map((row) => (
          <div key={row.key} className="flex items-baseline justify-between gap-4 px-4 py-3">
            <div className="min-w-0">
              <dt className="text-sm text-foreground">{row.label}</dt>
              <dt className="mt-0.5 text-xs text-muted-foreground">{row.hint}</dt>
            </div>
            <dd className="shrink-0 font-mono text-sm text-foreground">
              {formatMoney(totals?.[row.key], currency)}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  )
}

function LineRow({ line, currency, disabled, onQuantity, onRemove, errors }) {
  return (
    <tr className="border-b border-border-subtle last:border-b-0 align-top">
      <td className="py-3 pr-3">
        <p className="text-sm font-medium text-foreground">{line.name}</p>
        <p className="mt-1 flex flex-wrap items-center gap-2">
          <PriceSourceLabel line={line} />
          {line.sku && <span className="font-mono text-[11px] text-muted-foreground">{line.sku}</span>}
          {line.tier_label && line.price_source === 'catalog' && (
            <span className="font-mono text-[11px] text-muted-foreground">{line.tier_label}</span>
          )}
        </p>
        {Number(line.unit_price) === 0 && (
          <p className="mt-1">
            <UnpricedNote />
          </p>
        )}
        {errors?.name && <p className="mt-1 text-xs text-destructive">{errors.name}</p>}
      </td>
      <td className="py-3 pr-3">
        {disabled ? (
          <span className="font-mono text-sm">{line.quantity}</span>
        ) : (
          <input
            aria-label={`Quantity for ${line.name}`}
            className={`${inputClass} max-w-24 font-mono`}
            type="number"
            min="0"
            step="1"
            value={line.quantity ?? 0}
            onChange={(event) => onQuantity(line.id, event.target.value)}
          />
        )}
        {errors?.quantity && <p className="mt-1 text-xs text-destructive">{errors.quantity}</p>}
      </td>
      <td className="py-3 pr-3">
        <span className="font-mono text-sm">{formatMoney(line.unit_price, currency)}</span>
        {line.price_source === 'catalog' && (
          <p className="mt-1 text-[11px] text-muted-foreground">
            Resolves again when the quantity changes.
          </p>
        )}
      </td>
      <td className="py-3 pr-3">
        <span className="font-mono text-sm">
          {line.discount_type === 'currency'
            ? `${formatMoney(line.discount_value, currency)} off`
            : `${line.discount_value ?? 0}% off`}
        </span>
        {Number(line.tax_rate) > 0 && (
          <p className="mt-1 text-[11px] text-muted-foreground">{line.tax_rate}% tax</p>
        )}
      </td>
      <td className="py-3 pr-3">
        <span className="font-mono text-sm font-semibold">
          {formatMoney(line.amounts?.total ?? line.total, currency)}
        </span>
      </td>
      <td className="py-3 text-right">
        <Button
          disabled={disabled}
          onClick={() => onRemove(line.id)}
          aria-label={`Remove ${line.name} from the quote`}
        >
          Remove
        </Button>
      </td>
    </tr>
  )
}

function AddLineForm({ quoteId, catalogueAvailable, onAdded, setProblem }) {
  const [form, setForm] = useState({ name: '', quantity: 1, unit_price: 0, tax_rate: 0 })
  const [busy, setBusy] = useState(false)
  const [errors, setErrors] = useState({})

  const field = (key) => (event) => setForm((prev) => ({ ...prev, [key]: event.target.value }))

  async function submit(event) {
    event.preventDefault()
    setBusy(true)
    setErrors({})
    setProblem(null)
    try {
      const body = await quoteApi.addLineItem(quoteId, {
        ...form,
        quantity: Number(form.quantity),
        unit_price: Number(form.unit_price),
        tax_rate: Number(form.tax_rate),
      })
      onAdded(body.quote)
      setForm({ name: '', quantity: 1, unit_price: 0, tax_rate: 0 })
    } catch (error) {
      setErrors(error.errors || {})
      setProblem(error.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={submit} className="rounded-sm border border-border-subtle bg-surface p-4">
      <p className={HEADING}>Add a line item</p>
      <p className="mt-1 text-xs text-muted-foreground">
        {catalogueAvailable
          ? 'Type a name to add a custom line item. A line selected from the product library prices itself off its tier.'
          : 'No product library is provisioned yet, so every line is a custom line item priced from what is typed here.'}
      </p>
      <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Field id="wf086-line-name" label="Name" hint={errors.name}>
          <input
            id="wf086-line-name"
            className={inputClass}
            value={form.name}
            onChange={field('name')}
          />
        </Field>
        <Field id="wf086-line-quantity" label="Quantity" hint={errors.quantity}>
          <input
            id="wf086-line-quantity"
            className={`${inputClass} font-mono`}
            type="number"
            min="0"
            step="1"
            value={form.quantity}
            onChange={field('quantity')}
          />
        </Field>
        <Field id="wf086-line-price" label="Unit price" hint={errors.unit_price}>
          <input
            id="wf086-line-price"
            className={`${inputClass} font-mono`}
            type="number"
            min="0"
            step="0.01"
            value={form.unit_price}
            onChange={field('unit_price')}
          />
        </Field>
        <Field id="wf086-line-tax" label="Tax rate, percent" hint={errors.tax_rate}>
          <input
            id="wf086-line-tax"
            className={`${inputClass} font-mono`}
            type="number"
            min="0"
            max="100"
            step="0.1"
            value={form.tax_rate}
            onChange={field('tax_rate')}
          />
        </Field>
      </div>
      <div className="mt-4">
        <Button variant="primary" type="submit" disabled={busy} icon="plus">
          Add line item
        </Button>
      </div>
    </form>
  )
}

function ModuleEditor({ quote, onSaved }) {
  const [modules, setModules] = useState(quote.modules || [])
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState(null)

  const decorated = useMemo(
    () => modules.map((module, index) => ({ ...module, isLast: index === modules.length - 1 })),
    [modules],
  )

  async function save(next) {
    setBusy(true)
    setProblem(null)
    try {
      const body = await quoteApi.editQuote(quote.id, { modules: next })
      onSaved(body.quote)
    } catch (error) {
      setProblem(error.reason ? conflictText(error.reason) : error.message)
    } finally {
      setBusy(false)
    }
  }

  function toggle(module) {
    setModules(
      modules.map((row) =>
        row.key === module.key
          ? { ...row, visible: !row.visible, kind: undefined, authored_via: undefined }
          : { ...row, kind: undefined, authored_via: undefined },
      ),
    )
  }

  function move(module, direction) {
    const index = modules.findIndex((row) => row.key === module.key)
    const target = index + direction
    if (index < 0 || target < 0 || target >= modules.length) return
    const next = [...modules]
    ;[next[index], next[target]] = [next[target], next[index]]
    setModules(next)
  }

  return (
    <Card>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className={HEADING}>Quote sections</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Show, hide and reorder the modules the quote is built from. A custom coded module cannot
            be added here, because the vendor records that it is not possible through an API.
          </p>
        </div>
        <Button disabled={busy} onClick={() => save(modules)} icon="refresh">
          Save sections
        </Button>
      </div>

      {problem && (
        <div className="mt-4">
          <Notice tone="destructive">{problem}</Notice>
        </div>
      )}

      <ul className="mt-4 border-t border-border-subtle">
        {decorated.map((module) => (
          <ModuleRow
            key={module.key}
            module={module}
            disabled={busy}
            onToggle={(row) => {
              toggle(row)
            }}
            onMove={move}
          />
        ))}
      </ul>
    </Card>
  )
}

function QuoteHeader({ quote, onSaved }) {
  const [title, setTitle] = useState(quote.title || '')
  const [expiresOn, setExpiresOn] = useState(quote.expires_on || '')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState(null)

  async function save(event) {
    event.preventDefault()
    setBusy(true)
    setProblem(null)
    try {
      const body = await quoteApi.editQuote(quote.id, {
        title,
        expires_on: expiresOn,
      })
      onSaved(body.quote)
    } catch (error) {
      setProblem(
        Object.values(error.errors || {}).join(' ') || error.message,
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className={HEADING}>Quote header</p>
          <h2 className="mt-1 font-display text-lg font-semibold text-foreground">{quote.title}</h2>
        </div>
        <StatusBadge status={quote.status} />
      </div>

      <dl className="mt-4 grid gap-3 border-y border-border-subtle py-4 sm:grid-cols-4">
        {[
          ['Account', quote.account],
          ['Owner', quote.owner],
          ['Currency', quote.currency],
          ['Expires', quote.expires_on || 'No date'],
        ].map(([label, value]) => (
          <div key={label}>
            <dt className={HEADING}>{label}</dt>
            <dd className="mt-1 font-mono text-sm text-foreground">{value || 'Not set'}</dd>
          </div>
        ))}
      </dl>

      {quote.expired && (
        <div className="mt-4">
          <Notice tone="warning" title="This quote's expiration date has passed">
            Extend the date before publishing. A buyer cannot be held to a date that has gone.
          </Notice>
        </div>
      )}

      <form onSubmit={save} className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Field id="wf086-title" label="Title" hint={problem}>
          <input id="wf086-title" className={inputClass} value={title} onChange={(e) => setTitle(e.target.value)} />
        </Field>
        <Field id="wf086-expires" label="Expires on" hint="A date as YYYY-MM-DD.">
          <input
            id="wf086-expires"
            className={`${inputClass} font-mono`}
            type="date"
            value={expiresOn}
            onChange={(e) => setExpiresOn(e.target.value)}
          />
        </Field>
        <div className="flex items-end">
          <Button type="submit" disabled={busy} icon="refresh">
            Save header
          </Button>
        </div>
      </form>
    </Card>
  )
}

function QuoteEditor({ quoteId, catalogueAvailable, onBack, onChanged }) {
  const { data, loading, error, refetch } = useAsync(() => quoteApi.quote(quoteId), [quoteId])
  const [problem, setProblem] = useState(null)
  const [reason, setReason] = useState(null)
  const [busy, setBusy] = useState(false)

  const quote = data
  const frozen = quote?.status === 'published'

  const apply = useCallback(
    (next) => {
      onChanged()
      refetch(next)
    },
    [onChanged, refetch],
  )

  async function publish() {
    setBusy(true)
    setProblem(null)
    setReason(null)
    try {
      await quoteApi.publish(quoteId)
      apply()
    } catch (failure) {
      setReason(failure.reason || null)
      setProblem(failure.reason ? conflictText(failure.reason) : failure.message)
    } finally {
      setBusy(false)
    }
  }

  async function changeQuantity(lineId, value) {
    setProblem(null)
    setReason(null)
    try {
      await quoteApi.editLineItem(lineId, { quantity: Number(value) })
      apply()
    } catch (failure) {
      setProblem(failure.reason ? conflictText(failure.reason) : failure.message)
    }
  }

  async function removeLine(lineId) {
    setProblem(null)
    setReason(null)
    try {
      await quoteApi.removeLineItem(lineId)
      apply()
    } catch (failure) {
      setProblem(failure.reason ? conflictText(failure.reason) : failure.message)
    }
  }

  if (loading) return <Spinner label="Loading the quote" />
  if (error) return <ErrorNote error={error} onRetry={refetch} />
  if (!quote) return null

  const currency = quote.currency

  // Remount the two editors whenever the server's copy of the quote changes.
  //
  // Their fields hold unsaved edits, so they need to follow the record rather
  // than seed themselves from it. The alternative, syncing each field back
  // through an effect on every prop change, overwrites a half-typed title the
  // moment any other line is saved. The revision is the store's own counter and
  // moves on exactly when the record does, so this key is the record's identity
  // for the purpose of resetting local state.
  const revisionKey = `${quote.id}:${quote.revision ?? quote.updated_at ?? 0}`

  return (
    <div className={SECTION}>
      <div className="flex flex-wrap items-center gap-3">
        <Button onClick={onBack} icon="arrow-left">
          All quotes
        </Button>
        <p className="font-mono text-xs text-muted-foreground">
          {quote.id}
          {quote.published_at ? ` - published ${quote.published_at}` : ''}
        </p>
      </div>

      {problem && (
        <Notice tone="destructive" title={reason ? 'That change was refused' : undefined}>
          {problem}
        </Notice>
      )}

      <QuoteHeader key={revisionKey} quote={quote} onSaved={apply} />

      {frozen && (
        <Notice tone="info" title="This quote is published">
          Its line items are frozen, because the deal amount was computed from them. The payment
          schedule is still editable, since that is when money moves rather than what was quoted.
        </Notice>
      )}

      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className={HEADING}>Line items</p>
              <p className="mt-1 text-xs text-muted-foreground">
                {quote.line_items.length} on this quote, each with its own record id, separate from
                the deal's.
              </p>
            </div>
          </div>

          {quote.line_items.length === 0 ? (
            <div className="mt-4">
              <EmptyState
                title="No line items"
                description="The deal carried none. Add one below, or pick a different deal."
              />
            </div>
          ) : (
            <div className="mt-4 overflow-x-auto">
              <table className="w-full min-w-[640px] text-left">
                <thead>
                  <tr className="border-b border-border-subtle">
                    {['Item', 'Quantity', 'Unit price', 'Discount', 'Total', ''].map((label) => (
                      <th
                        key={label || 'actions'}
                        scope="col"
                        className={`py-2 pr-3 text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground ${label ? '' : 'text-right'}`}
                      >
                        {label}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {quote.line_items.map((line) => (
                    <LineRow
                      key={line.id}
                      line={line}
                      currency={currency}
                      disabled={frozen}
                      errors={line.errors}
                      onQuantity={changeQuantity}
                      onRemove={removeLine}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {!frozen && (
            <div className="mt-6">
              <AddLineForm
                quoteId={quote.id}
                catalogueAvailable={catalogueAvailable}
                onAdded={apply}
                setProblem={(message) => {
                  setReason(null)
                  setProblem(message)
                }}
              />
            </div>
          )}
        </Card>

        <div className={SECTION}>
          <TotalsPanel totals={quote.totals} currency={currency} />

          <Card>
            <p className={HEADING}>Publish</p>
            <p className="mt-1 text-sm text-muted-foreground">
              {frozen
                ? 'Already published. The deal amount and its line items have been updated.'
                : 'Copies the total contract value onto the deal amount, and replaces the deal line items with copies of these.'}
            </p>
            <div className="mt-4">
              <Button variant="primary" disabled={frozen || busy} onClick={publish} icon="check">
                {frozen ? 'Published' : 'Publish quote'}
              </Button>
            </div>
            {quote.deal_amount_written != null && (
              <p className="mt-3 font-mono text-xs text-muted-foreground">
                Wrote {formatMoney(quote.deal_amount_written, currency)} to the deal amount.
              </p>
            )}
          </Card>

          <ModuleEditor key={revisionKey} quote={quote} onSaved={apply} />
        </div>
      </div>
    </div>
  )
}

export default function AuthorQuotePage() {
  const [pickedRoom, setPickedRoom] = useState('')
  const [selected, setSelected] = useState(null)
  const [problem, setProblem] = useState(null)
  const [errors, setErrors] = useState({})
  const [busy, setBusy] = useState(false)
  const [nonce, setNonce] = useState(0)

  const rooms = useAsync(() => listRooms(), [])
  // Derived, not stored through an effect: the first render already has a room.
  const roomId = pickedRoom || rooms.data?.records?.[0]?.id || ''
  const board = useAsync(() => quoteApi.summary(roomId), [roomId, nonce])
  const deals = useAsync(() => listDeals(roomId), [roomId])

  const catalogueAvailable = board.data?.catalogue_available === true
  const refresh = () => setNonce((value) => value + 1)

  async function create(dealId, room) {
    setBusy(true)
    setProblem(null)
    setErrors({})
    try {
      const body = await quoteApi.createQuote({ deal_id: dealId }, room)
      setPickedRoom(room)
      setSelected(body.quote.id)
      refresh()
    } catch (failure) {
      setErrors(failure.errors || {})
      setProblem(failure.message)
    } finally {
      setBusy(false)
    }
  }

  if (board.loading && rooms.loading) return <Spinner label="Loading the quoting board" />

  return (
    <div className={SECTION}>
      <header>
        <h1 className="font-display text-2xl font-semibold text-foreground">
          Author a quote from a deal
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Create the quote, clone the deal's line items onto it, recompute the totals, and on
          publish copy the total contract value onto the deal amount.
        </p>
      </header>

      {board.error && <ErrorNote error={board.error} onRetry={refresh} />}
      {problem && !selected && <Notice tone="destructive">{problem}</Notice>}

      {board.data && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard
            label="Quotes"
            value={board.data.quotes}
            hint={`${board.data.drafts} draft, ${board.data.published} published`}
          />
          <StatCard
            label="Line items"
            value={board.data.line_items}
            hint="Each with its own record id"
          />
          <StatCard
            label="Contract value"
            value={formatMoney(board.data.total_contract_value)}
            hint="Sum across every quote"
          />
          <StatCard
            label="Product library"
            value={catalogueAvailable ? 'Ready' : 'Not provisioned'}
            hint={
              catalogueAvailable
                ? 'Tier prices resolve automatically'
                : 'Lines price from the deal until it exists'
            }
            icon={catalogueAvailable ? 'check' : undefined}
          />
        </div>
      )}

      {!catalogueAvailable && board.data && (
        <Notice tone="info" title="No product library yet">
          Every unit price on this page comes from the deal or from what the seller typed. The
          catalogue that resolves tier prices belongs to another workflow, so nothing here waits
          for it.
        </Notice>
      )}

      {selected ? (
        <QuoteEditor
          quoteId={selected}
          catalogueAvailable={catalogueAvailable}
          onBack={() => setSelected(null)}
          onChanged={refresh}
        />
      ) : deals.loading ? (
        <Spinner label="Loading deals" />
      ) : (
        <DealPicker
          rooms={rooms.data?.records || []}
          deals={deals.data?.records || []}
          busy={busy}
          onCreate={create}
          errors={errors}
        />
      )}

      {!selected && !deals.loading && deals.error && (
        <ErrorNote error={deals.error} onRetry={deals.refetch} />
      )}

      <p className="flex items-center gap-2 text-xs text-muted-foreground">
        <Icon name="info" size={14} />
        Totals are computed by the server on every write, so the figures here always match the
        audit row.
      </p>
    </div>
  )
}
