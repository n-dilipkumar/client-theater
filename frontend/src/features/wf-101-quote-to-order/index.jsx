/**
 * WF-101: convert an accepted quote into an order and lock the price.
 *
 * The page reads its modes, settings and refusal reasons from the server's
 * `/vocabulary` route rather than repeating them, so a panel cannot drift from
 * the rules it enforces.
 *
 * Two things this page deliberately does not do, both from the research:
 *
 *   - It offers no way to activate or accept a quote. Both are upstream events
 *     that other workflows own, so the only control is "Convert quote", which
 *     the server keys the conversion on.
 *   - It raises no invoice. Billing is a separate workflow; the page only shows
 *     that an invoice is expected once an order is fulfilled.
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
  blockedReason,
  canConvert,
  listQuotes,
  listRooms,
  orderApi,
} from './api'
import { Fact, InlineNote, LineItemsTable, Money, PriceLockBadge, StatusBadge } from './primitives'

function ToggleRow({ label, hint, enabled, busy, onToggle }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border-subtle py-3 last:border-b-0">
      <div className="min-w-0">
        <p className="text-sm text-foreground">{label}</p>
        {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
      </div>
      <Button
        disabled={busy}
        onClick={onToggle}
        aria-pressed={Boolean(enabled)}
        aria-label={`${label}: ${enabled ? 'on' : 'off'}`}
        icon="schema"
      >
        {enabled ? 'On' : 'Off'}
      </Button>
    </div>
  )
}

function SettingsPanel({ settings, onSaved }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  async function save(patch) {
    setBusy(true)
    setError(null)
    try {
      await orderApi.updateSettings(settings?.room_id, patch)
      onSaved()
    } catch (problem) {
      setError(problem)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg font-semibold text-foreground">Order settings</h2>
        {!settings?.explicit && <span className="text-xs text-muted-foreground">Default</span>}
      </div>
      <ToggleRow
        label="Enable creation of orders from quotes"
        hint="In Sales Professional, Create Order is available only while this is on."
        enabled={settings?.enable_creation_of_orders_from_quotes}
        busy={busy}
        onToggle={() =>
          save({
            enable_creation_of_orders_from_quotes:
              !settings?.enable_creation_of_orders_from_quotes,
          })
        }
      />
      <ToggleRow
        label="Sales order processing integration"
        hint="Off, Prices Locked becomes Yes. On, it takes the Order table default below."
        enabled={settings?.enable_sales_order_processing_integration}
        busy={busy}
        onToggle={() =>
          save({
            enable_sales_order_processing_integration:
              !settings?.enable_sales_order_processing_integration,
          })
        }
      />
      <ToggleRow
        label="Order table default for ispricelocked"
        hint="Read only when the integration is on. The documented default is No."
        enabled={settings?.ispricelocked_default}
        busy={busy}
        onToggle={() => save({ ispricelocked_default: !settings?.ispricelocked_default })}
      />
      {error && <ErrorNote error={error} />}
    </Card>
  )
}

function QuotePanel({ quotes, onConverted }) {
  const [selected, setSelected] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [note, setNote] = useState(null)
  const [nonce, setNonce] = useState(0)
  const [dateWon, setDateWon] = useState('')

  const readiness = useAsync(
    () => (selected ? orderApi.quoteReadiness(selected) : Promise.resolve(null)),
    [selected, nonce],
  )

  async function convert() {
    setBusy(true)
    setError(null)
    setNote(null)
    try {
      await orderApi.convert(selected, dateWon ? { date_won: dateWon } : {})
      setNote('Order created from the accepted quote.')
      setNonce((value) => value + 1)
      onConverted()
    } catch (problem) {
      setError(problem)
    } finally {
      setBusy(false)
    }
  }

  const data = readiness.data
  const ready = canConvert(data)
  const blocked = data ? blockedReason(data) : null

  return (
    <Card className="flex flex-col gap-4">
      <h2 className="text-lg font-semibold text-foreground">Convert a quote</h2>

      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Quote" id="wf101-quote" hint="An accepted and activated quote in this room.">
          <select
            id="wf101-quote"
            className={inputClass}
            value={selected}
            onChange={(event) => setSelected(event.target.value)}
          >
            <option value="">Select a quote</option>
            {quotes.map((quote) => (
              <option key={quote.id} value={quote.id}>
                {quote.data?.title || quote.id}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Date won" id="wf101-date-won" hint="ISO date. Defaults to today.">
          <input
            id="wf101-date-won"
            className={inputClass}
            value={dateWon}
            onChange={(event) => setDateWon(event.target.value)}
          />
        </Field>
      </div>

      {selected && readiness.loading && <Spinner label="Reading the quote" />}
      {readiness.error && <ErrorNote error={readiness.error} onRetry={readiness.refetch} />}

      {data && (
        <>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Fact label="Activated" hint={data.activated_by}>
              {data.activated ? 'Yes' : 'No'}
            </Fact>
            <Fact label="Accepted" hint={data.accepted_by}>
              {data.accepted ? 'Yes' : 'No'}
            </Fact>
            <Fact label="Price lock now" hint={data.price_lock?.reason}>
              {data.price_lock?.locked ? 'Yes' : 'No'}
            </Fact>
            <Fact label="Quote lines" hint="Copied onto the order">
              {data.line_item_count}
            </Fact>
          </div>

          {blocked && <InlineNote tone={ready ? 'success' : 'warning'}>{blocked}</InlineNote>}
          {data.existing_order_id && (
            <InlineNote tone="info">Order {data.existing_order_id} already exists.</InlineNote>
          )}
          {note && <InlineNote tone="success">{note}</InlineNote>}
          {error && <ErrorNote error={error} />}

          <div>
            <Button
              variant="primary"
              icon="plus"
              disabled={busy || !ready}
              onClick={convert}
            >
              Convert quote to order
            </Button>
          </div>
        </>
      )}
    </Card>
  )
}

function OrderActions({ order, onChanged }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [note, setNote] = useState(null)
  const [lineName, setLineName] = useState('')
  const [linePrice, setLinePrice] = useState('')

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

  const locked = order.locked

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap gap-2">
        <Button
          icon="schema"
          disabled={busy || locked}
          onClick={() => run(() => orderApi.setPricingMode(order.id, 'lock_pricing'))}
        >
          Lock Pricing
        </Button>
        <Button
          icon="restore"
          disabled={busy || !locked}
          onClick={() => run(() => orderApi.setPricingMode(order.id, 'use_current_pricing'))}
        >
          Use Current Pricing
        </Button>
        <Button
          icon="refresh"
          disabled={busy}
          onClick={() =>
            run(async () => {
              const result = await orderApi.reprice(order.id)
              setNote(
                result.applied_lines > 0
                  ? `Repriced ${result.applied_lines} line(s).`
                  : 'No line was repriced. Prices are locked or nothing changed.',
              )
            })
          }
        >
          Apply current price list
        </Button>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Write-in product" id={`wf101-write-in-${order.id}`} hint="Addable only while Prices Locked is No.">
          <input
            id={`wf101-write-in-${order.id}`}
            className={inputClass}
            value={lineName}
            disabled={busy || locked}
            onChange={(event) => setLineName(event.target.value)}
          />
        </Field>
        <Field label="Write-in price" id={`wf101-write-in-price-${order.id}`}>
          <input
            id={`wf101-write-in-price-${order.id}`}
            className={inputClass}
            inputMode="decimal"
            value={linePrice}
            disabled={busy || locked}
            onChange={(event) => setLinePrice(event.target.value)}
          />
        </Field>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button
          icon="plus"
          disabled={busy || locked || lineName.trim() === ''}
          onClick={() =>
            run(async () => {
              await orderApi.addLine(order.id, {
                name: lineName,
                quantity: 1,
                unit_price: Number(linePrice || 0),
              })
              setLineName('')
              setLinePrice('')
            })
          }
        >
          Add write-in product
        </Button>
        <Button
          icon="close"
          disabled={busy}
          onClick={() => run(() => orderApi.closeOrder(order.id, 'fulfilled'))}
        >
          Close as fulfilled
        </Button>
        <Button
          variant="danger"
          icon="close"
          disabled={busy}
          onClick={() => run(() => orderApi.closeOrder(order.id, 'cancelled'))}
        >
          Cancel order
        </Button>
      </div>

      {note && <InlineNote tone="success">{note}</InlineNote>}
      {error && <ErrorNote error={error} />}
    </div>
  )
}

function OrdersPanel({ orders, onChanged }) {
  if (orders.length === 0) {
    return (
      <EmptyState
        title="No orders yet"
        description="Convert an accepted quote above and the order appears here."
      />
    )
  }
  return (
    <div className="space-y-4">
      {orders.map((order) => (
        <Card key={order.id} className="flex flex-col gap-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <h3 className="text-base font-semibold text-foreground">{order.name || order.id}</h3>
              <p className="mt-0.5 font-mono text-xs text-muted-foreground">{order.id}</p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <StatusBadge status={order.status} />
              <PriceLockBadge locked={order.locked} />
              {order.invoice_expected && (
                <span className="text-xs text-muted-foreground">Invoice expected</span>
              )}
            </div>
          </div>

          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Fact label="Currency" hint="Copied from the quote">
              {order.currency || 'Not set'}
            </Fact>
            <Fact label="Price list" hint="Copied from the quote">
              {order.price_list || 'None'}
            </Fact>
            <Fact label="Date won">
              {order.won_on || 'Not set'}
            </Fact>
            <Fact label="Total" hint="Recomputed from the order's lines">
              <Money amount={order.totals?.total} currency={order.currency} />
            </Fact>
          </div>

          <div>
            <p className="mb-2 text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
              Line items
            </p>
            <LineItemsTable lines={order.line_items} locked={order.locked} />
          </div>

          {order.status === 'fulfilled' && (
            <InlineNote tone="info">
              Fulfilment is recorded. An invoice is expected, and the order is now read-only.
            </InlineNote>
          )}
          {order.status === 'cancelled' && (
            <InlineNote tone="warning">This order is cancelled and read-only.</InlineNote>
          )}

          {!order.closed && <OrderActions order={order} onChanged={onChanged} />}
        </Card>
      ))}
    </div>
  )
}

function QuoteToOrderPage() {
  const [roomId, setRoomId] = useState('')

  const rooms = useAsync(() => listRooms(), [])
  const vocabulary = useAsync(() => orderApi.vocabulary(), [])
  const settings = useAsync(() => orderApi.settings(roomId), [roomId])
  const orders = useAsync(() => orderApi.orders(roomId), [roomId])
  const summary = useAsync(() => orderApi.summary(roomId), [roomId])
  const quotes = useAsync(() => listQuotes(), [])

  if (vocabulary.loading || rooms.loading) return <Spinner label="Loading orders" />
  if (vocabulary.error) return <ErrorNote error={vocabulary.error} onRetry={vocabulary.refetch} />
  if (orders.error) return <ErrorNote error={orders.error} onRetry={orders.refetch} />

  const entries = orders.data?.entries || []
  const roomOptions = rooms.data?.records || []
  const quoteOptions = quotes.data?.records || []

  function refresh() {
    orders.refetch()
    summary.refetch()
    settings.refetch()
  }

  return (
    <div className="space-y-6">
      <header className="flex flex-col gap-1">
        <h1 className="font-display text-2xl font-semibold text-foreground">Quotes to orders</h1>
        <p className="text-sm text-muted-foreground">
          An accepted and activated quote converts into an order. The order copies the quote&apos;s
          price list, currency, products, addresses, shipping and totals, and its price lock
          decides whether a later price-list edit applies.
        </p>
      </header>

      <Card className="flex flex-col gap-3">
        <Field label="Room" id="wf101-room" hint="Orders and settings belong to a room.">
          <select
            id="wf101-room"
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
          label="Orders"
          value={summary.data?.orders ?? 0}
          icon="database"
          hint="Converted from quotes"
        />
        <StatCard
          label="Prices locked"
          value={summary.data?.locked ?? 0}
          icon="schema"
          hint="A price-list edit is ignored"
        />
        <StatCard
          label="Current pricing"
          value={summary.data?.unlocked ?? 0}
          icon="refresh"
          hint="A price-list edit applies"
        />
        <StatCard
          label="Closed"
          value={summary.data?.closed ?? 0}
          icon="close"
          hint="Fulfilled or cancelled"
        />
      </div>

      <SettingsPanel
        settings={settings.data ? { ...settings.data, room_id: roomId } : { room_id: roomId }}
        onSaved={refresh}
      />

      <QuotePanel quotes={quoteOptions} onConverted={refresh} />

      <section className="space-y-4">
        <h2 className="font-display text-lg font-semibold text-foreground">Orders</h2>
        <OrdersPanel orders={entries} onChanged={refresh} />
      </section>
    </div>
  )
}

export default {
  id: 'wf-101-quote-to-order',
  label: 'Quote to order',
  icon: 'schema',
  order: 120,
  Component: QuoteToOrderPage,
}
