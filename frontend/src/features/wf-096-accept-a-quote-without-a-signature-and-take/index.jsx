/**
 * WF-096: accept a quote without a signature and take payment in the quote.
 *
 * The page has five jobs, in this order, and the order is the design.
 *
 * **Say where every quote stands first.** A seller opening this page is asking one question:
 * which quotes are accepted and which are paid. So the board, the three-step rail and the
 * payment state come before any configuration form, and the rail writes "Done", "Now" or
 * "Waiting" in words so no state is carried by colour alone.
 *
 * **Keep acceptance and payment two separate acts.** The research puts them in that order: the
 * buyer "clicks Accept (click-to-accept), then optionally Set up payment". Acceptance writes the
 * acceptance and creates the first invoice; payment is a second, optional, revisitable step. The
 * page never wires them into one button, because a declined charge must never undo an acceptance
 * the buyer already gave.
 *
 * **Say what the research decided and what it left open.** The strict `$0.50` minimum, the
 * whole-number quantity rule, the first-invoice rule, the three-tax-ID cap and the
 * irreversible-after-acceptance rule each carry the sentence they came from, and every judgement
 * call this workflow made is listed with the alternative it rejected.
 *
 * **Say what this workflow does not own.** The quote and its line items are WF-086's, the
 * e-signature acceptance is WF-095's, and the Connected CPQ contract and order are out of scope.
 * Each is stated on the page rather than left in a docstring.
 *
 * **Render every state this page can be in.** Loading, error, empty, and the outcome of every
 * write. A board that goes blank when the API is down reads as "there is nothing here", which
 * for a payments page is the one reading that must never be possible.
 */

import { useCallback, useState } from 'react'

import {
  Badge,
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
  FACTS,
  amountDue,
  formatDate,
  formatMoney,
  paymentState,
  paymentTone,
  paymentsApi,
  plural,
} from './api'
import {
  AcceptanceRail,
  ActivityRow,
  ChargeRow,
  Dialog,
  EvidenceNote,
  InvoiceRow,
  MinimumChargePanel,
  PaymentBadge,
  TaxIdPanel,
  ToggleRow,
  evidenceFor,
} from './primitives'

/** The seeded room this demo drives. WF-086 supplies the quote in production. */
const ROOM = 'room_a'

/**
 * The status line after a write. One shape for every outcome, including a declined charge,
 * because a declined charge is an outcome and not an error.
 */
function Outcome({ outcome, onDismiss }) {
  if (!outcome) return null
  const tones = {
    ok: 'border-accent/30 bg-accent/10 text-accent',
    declined: 'border-warning/40 bg-warning/10 text-warning',
    error: 'border-destructive/40 bg-destructive/10 text-destructive',
  }
  return (
    <div
      role="status"
      className={`flex flex-wrap items-start justify-between gap-3 rounded-sm border p-4 ${
        tones[outcome.status] || tones.ok
      }`}
    >
      <div className="min-w-0">
        <p className="text-sm font-semibold">{outcome.title}</p>
        <p className="mt-0.5 text-sm text-muted-foreground">{outcome.detail}</p>
        {outcome.reason ? (
          <p className="mt-1 font-mono text-xs text-muted-foreground">reason: {outcome.reason}</p>
        ) : null}
        {outcome.errors ? (
          <ul className="mt-2 space-y-1">
            {Object.entries(outcome.errors).map(([field, message]) => (
              <li key={field} className="text-xs text-foreground">
                <span className="font-mono">{field}</span>: {message}
              </li>
            ))}
          </ul>
        ) : null}
      </div>
      <Button icon="close" onClick={onDismiss}>
        Dismiss
      </Button>
    </div>
  )
}

/** A refusal read from a thrown error, so every dialog renders a refusal the same way. */
function Refusal({ error }) {
  if (!error) return null
  return (
    <div className="rounded-sm border border-destructive/40 bg-destructive/10 p-3">
      <p className="text-sm font-semibold text-destructive">{error.message}</p>
      {error.reason ? (
        <p className="mt-1 font-mono text-xs text-foreground">reason: {error.reason}</p>
      ) : null}
      {error.errors ? (
        <ul className="mt-1 space-y-1">
          {Object.entries(error.errors).map(([field, message]) => (
            <li key={field} className="text-xs text-foreground">
              <span className="font-mono">{field}</span>: {message}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}

/** A labelled select, so every configuration choice reads the same way. */
function Select({ id, label, value, onChange, options, hint }) {
  return (
    <Field label={label} id={id} hint={hint}>
      <select
        id={id}
        className="min-h-11 w-full rounded-sm border border-border-subtle bg-surface px-3 text-sm text-foreground focus:border-accent"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        {options.map((choice) => (
          <option key={choice.value} value={choice.value}>
            {choice.label}
          </option>
        ))}
      </select>
    </Field>
  )
}

/**
 * Publish a quote for online payments: the researched payment configuration. The acceptance
 * method, the payment methods, the billing frequency, the net terms and the effective date are
 * the values the sidebar sets; the rest is derived server-side.
 */
function PublishDialog({ quote, busy, error, onClose, onSubmit }) {
  const [acceptance, setAcceptance] = useState('clickwrap')
  const [methods, setMethods] = useState(['CREDIT_OR_DEBIT_CARD'])
  const [frequency, setFrequency] = useState('one_time')
  const [netTerms, setNetTerms] = useState('NET_30')
  const [mode, setMode] = useState('on_agreement')
  const [customDate, setCustomDate] = useState('2026-11-01')
  const [delay, setDelay] = useState('30')

  const payload = {
    acceptance_method: acceptance,
    allowed_payment_methods: methods,
    billing_frequency: frequency,
    net_payment_terms: netTerms,
    effective_date_mode: mode,
  }
  if (mode === 'custom_date') payload.effective_date = customDate
  if (mode === 'delayed_days') payload.effective_delay_days = Number(delay)
  if (mode === 'delayed_months') payload.effective_delay_months = Number(delay)

  return (
    <Dialog
      open
      title="Publish for online payments"
      description={`Quote ${quote.id} — acceptance method, payment methods and schedule.`}
      onClose={onClose}
    >
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault()
          onSubmit(payload)
        }}
      >
        <Select
          id="wf096-acceptance"
          label="Acceptance method"
          value={acceptance}
          onChange={setAcceptance}
          options={FACTS.acceptanceMethods}
          hint="Online payments need E-signature or Accept without signature. This workflow owns clickwrap; e-signature belongs to WF-095."
        />

        <fieldset className="rounded-sm border border-border-subtle p-3">
          <legend className="px-1 text-[13px] font-medium text-foreground">
            Payment methods
          </legend>
          <div className="mt-1 flex flex-wrap gap-3">
            {FACTS.paymentMethods.map((method) => (
              <label key={method.value} className="flex min-h-11 items-center gap-2 text-sm text-foreground">
                <input
                  type="checkbox"
                  className="h-5 w-5 rounded-xs border border-border-subtle"
                  checked={methods.includes(method.value)}
                  onChange={(event) =>
                    setMethods((current) =>
                      event.target.checked
                        ? [...current, method.value]
                        : current.filter((value) => value !== method.value),
                    )
                  }
                />
                {method.label}
              </label>
            ))}
          </div>
        </fieldset>

        <Select
          id="wf096-frequency"
          label="Billing frequency"
          value={frequency}
          onChange={setFrequency}
          options={FACTS.billingFrequencies}
          hint="Per line item in the research; this is the quote-level default."
        />
        <Select
          id="wf096-net"
          label="Net payment terms"
          value={netTerms}
          onChange={setNetTerms}
          options={FACTS.netTerms}
        />
        <Select
          id="wf096-effective"
          label="Effective date"
          value={mode}
          onChange={setMode}
          options={FACTS.effectiveDateModes}
        />
        {mode === 'custom_date' ? (
          <Field label="Effective date" id="wf096-custom-date">
            <input
              id="wf096-custom-date"
              type="date"
              className={inputClass}
              value={customDate}
              onChange={(event) => setCustomDate(event.target.value)}
            />
          </Field>
        ) : null}
        {mode === 'delayed_days' || mode === 'delayed_months' ? (
          <Field
            label={mode === 'delayed_days' ? 'Delay (days)' : 'Delay (months)'}
            id="wf096-delay"
          >
            <input
              id="wf096-delay"
              type="number"
              min="1"
              className={inputClass}
              value={delay}
              onChange={(event) => setDelay(event.target.value)}
            />
          </Field>
        ) : null}

        <Refusal error={error} />

        <div className="flex flex-wrap gap-2">
          <Button type="submit" variant="primary" disabled={busy} icon="plus">
            Publish for payments
          </Button>
          <Button onClick={onClose}>Cancel</Button>
        </div>
      </form>
    </Dialog>
  )
}

/** Accept without a signature: the click-to-accept, and the invoices it triggers. */
function AcceptDialog({ quote, busy, error, onClose, onSubmit }) {
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  return (
    <Dialog
      open
      title="Accept without a signature"
      description={`Quote ${quote.id} — this writes the acceptance and the first invoice. No money moves.`}
      onClose={onClose}
    >
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault()
          onSubmit({ accepted_by: name, accepted_by_email: email })
        }}
      >
        <Field
          label="Accepted by"
          id="wf096-accepted-by"
          hint={`Leave blank to record "${FACTS.anonymousBuyer}". A contact is not required, because the research says a clickwrap quote needs none.`}
        >
          <input
            id="wf096-accepted-by"
            className={inputClass}
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Ada Byron"
          />
        </Field>
        <Field label="Email" id="wf096-accepted-email">
          <input
            id="wf096-accepted-email"
            type="email"
            className={inputClass}
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="ada@northwind.example"
          />
        </Field>

        <Refusal error={error} />

        <div className="flex flex-wrap gap-2">
          <Button type="submit" variant="primary" disabled={busy} icon="audit">
            Accept the quote
          </Button>
          <Button onClick={onClose}>Cancel</Button>
        </div>
      </form>
    </Dialog>
  )
}

/**
 * Take payment for the amount due. The payment method must be one the quote allows, and a charge
 * below the strict minimum comes back as a 200 decline rather than an error.
 */
function PaymentDialog({ detail, busy, error, onClose, onSubmit }) {
  const allowed = detail.setup?.allowed_payment_methods || FACTS.paymentMethods.map((m) => m.value)
  const [method, setMethod] = useState(allowed[0] || 'CREDIT_OR_DEBIT_CARD')
  const [store, setStore] = useState(false)
  const due = amountDue(detail.quote)

  return (
    <Dialog
      open
      title="Take payment"
      description={`Quote ${detail.quote.id} — ${formatMoney(due.total, detail.quote.currency)} due.`}
      onClose={onClose}
    >
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault()
          onSubmit({ payment_method: method, store_payment_method: store })
        }}
      >
        <Select
          id="wf096-payment-method"
          label="Payment method"
          value={method}
          onChange={setMethod}
          options={FACTS.paymentMethods.filter((choice) => allowed.includes(choice.value))}
          hint="Only the methods enabled when the quote was published."
        />
        <ToggleRow
          id="wf096-store-method"
          label="Store the payment method"
          hint="Saves the method on the buyer's record for the next invoice."
          checked={store}
          onChange={setStore}
        />

        <div className="rounded-sm border border-border-subtle bg-muted/40 p-3">
          <p className="text-xs text-muted-foreground">
            A charge at or below {formatMoney(FACTS.minimumChargeUsd, 'USD')} is recorded as
            declined with the reason <span className="font-mono">amount_due_below_minimum</span>.
          </p>
        </div>

        <Refusal error={error} />

        <div className="flex flex-wrap gap-2">
          <Button type="submit" variant="primary" disabled={busy} icon="plus">
            Charge {formatMoney(due.total, detail.quote.currency)}
          </Button>
          <Button onClick={onClose}>Cancel</Button>
        </div>
      </form>
    </Dialog>
  )
}

/** Add a buyer tax ID, up to the researched cap of three. */
function TaxIdDialog({ quote, count, busy, error, onClose, onSubmit }) {
  const [value, setValue] = useState('')
  const [country, setCountry] = useState('')
  return (
    <Dialog
      open
      title="Add a buyer tax ID"
      description={`Quote ${quote.id} — ${count} of ${FACTS.taxIdLimit} recorded.`}
      onClose={onClose}
    >
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault()
          onSubmit({ value, country })
        }}
      >
        <Field label="Tax ID" id="wf096-tax-id">
          <input
            id="wf096-tax-id"
            className={inputClass}
            value={value}
            onChange={(event) => setValue(event.target.value)}
            placeholder="US-001"
          />
        </Field>
        <Field label="Country" id="wf096-tax-country">
          <input
            id="wf096-tax-country"
            className={inputClass}
            value={country}
            onChange={(event) => setCountry(event.target.value)}
            placeholder="US"
          />
        </Field>

        <Refusal error={error} />

        <div className="flex flex-wrap gap-2">
          <Button type="submit" variant="primary" disabled={busy} icon="plus">
            Add tax ID
          </Button>
          <Button onClick={onClose}>Cancel</Button>
        </div>
      </form>
    </Dialog>
  )
}

/** Create a demo quote and one line item, so the flow is demonstrable before WF-086 lands. */
function NewQuoteDialog({ busy, error, onClose, onSubmit }) {
  const [title, setTitle] = useState('Northwind renewal')
  const [currency, setCurrency] = useState('USD')
  const [lineName, setLineName] = useState('Platform seats')
  const [quantity, setQuantity] = useState('10')
  const [unitPrice, setUnitPrice] = useState('120')
  const [frequency, setFrequency] = useState('monthly')
  return (
    <Dialog
      open
      title="New demo quote"
      description="WF-086 provisions the quote in production. This route makes the flow demonstrable."
      onClose={onClose}
    >
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault()
          onSubmit({
            quote: { title, currency, company_name: 'Northwind Logistics' },
            line: {
              name: lineName,
              quantity: Number(quantity),
              unit_price: Number(unitPrice),
              billing_frequency: frequency,
            },
          })
        }}
      >
        <Field label="Title" id="wf096-new-title">
          <input
            id="wf096-new-title"
            className={inputClass}
            value={title}
            onChange={(event) => setTitle(event.target.value)}
          />
        </Field>
        <Field label="Currency" id="wf096-new-currency">
          <input
            id="wf096-new-currency"
            className={inputClass}
            value={currency}
            onChange={(event) => setCurrency(event.target.value.toUpperCase())}
          />
        </Field>
        <Field label="Line item" id="wf096-new-line">
          <input
            id="wf096-new-line"
            className={inputClass}
            value={lineName}
            onChange={(event) => setLineName(event.target.value)}
          />
        </Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Quantity" id="wf096-new-quantity" hint="Whole numbers only once billing is on.">
            <input
              id="wf096-new-quantity"
              type="number"
              min="1"
              step="1"
              className={inputClass}
              value={quantity}
              onChange={(event) => setQuantity(event.target.value)}
            />
          </Field>
          <Field label="Unit price" id="wf096-new-price">
            <input
              id="wf096-new-price"
              type="number"
              min="0"
              step="0.01"
              className={inputClass}
              value={unitPrice}
              onChange={(event) => setUnitPrice(event.target.value)}
            />
          </Field>
        </div>
        <Select
          id="wf096-new-frequency"
          label="Billing frequency"
          value={frequency}
          onChange={setFrequency}
          options={FACTS.billingFrequencies}
        />

        <Refusal error={error} />

        <div className="flex flex-wrap gap-2">
          <Button type="submit" variant="primary" disabled={busy} icon="plus">
            Create quote
          </Button>
          <Button onClick={onClose}>Cancel</Button>
        </div>
      </form>
    </Dialog>
  )
}

/** One quote on the board: its rail, its amount due, its state and its two buyer steps. */
function QuoteCard({ view, busy, onOpen, onPublish, onAccept, onPay, onVoid, onDelete }) {
  const quote = view.quote
  const due = amountDue(quote)
  const detail = { quote, setup: view.setup, charges: view.charges }
  const state = paymentState(detail)
  const accepted = quote.accepted
  const paid = state === 'Paid'
  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="font-mono text-xs text-muted-foreground">{quote.id}</p>
          <h3 className="mt-1 text-base font-semibold text-foreground">
            {quote.title || 'Untitled quote'}
          </h3>
          <p className="mt-1 text-xs text-muted-foreground">
            {quote.company_name || 'No company'}
            <span className="mx-2 text-border-subtle">|</span>
            {plural(quote.line_items?.length || 0, 'line item')}
            {view.setup ? (
              <>
                <span className="mx-2 text-border-subtle">|</span>
                {view.setup.acceptance_method === 'clickwrap'
                  ? 'Accept without signature'
                  : view.setup.acceptance_method}
              </>
            ) : null}
          </p>
        </div>
        <div className="flex flex-col items-end gap-2">
          <PaymentBadge state={state} tone={paymentTone(detail)} />
          <p className="font-mono text-lg font-semibold text-foreground">
            {formatMoney(due.total, quote.currency)}
          </p>
        </div>
      </div>

      <div className="mt-4">
        <AcceptanceRail detail={detail} />
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        <Button onClick={() => onOpen(quote.id)} icon="schema">
          Open
        </Button>
        {!view.setup ? (
          <Button variant="primary" disabled={busy} onClick={() => onPublish(quote)} icon="plus">
            Publish for payments
          </Button>
        ) : null}
        {view.setup && !accepted ? (
          <Button variant="primary" disabled={busy} onClick={() => onAccept(quote)} icon="audit">
            Accept without a signature
          </Button>
        ) : null}
        {accepted && !paid ? (
          <Button variant="primary" disabled={busy} onClick={() => onPay(detail)} icon="plus">
            Take payment
          </Button>
        ) : null}
        {!accepted ? (
          <>
            <Button disabled={busy} onClick={() => onVoid(quote)} icon="close">
              Void
            </Button>
            <Button variant="danger" disabled={busy} onClick={() => onDelete(quote)} icon="trash">
              Delete
            </Button>
          </>
        ) : (
          <span className="self-center text-xs text-muted-foreground">
            Accepted quotes cannot be voided or deleted.
          </span>
        )}
      </div>
    </Card>
  )
}

/** The detail panel for one quote: its rail, its invoices, its charges, its activity. */
function QuoteDetail({ detail, vocabulary, busy, onClose, onPay, onAddTaxId, onVoid, onDelete }) {
  const quote = detail.quote
  const due = amountDue(quote)
  const railDetail = { quote, setup: detail.setup, charges: detail.charges }
  const state = paymentState(railDetail)
  const accepted = quote.accepted
  const paid = state === 'Paid'
  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-lg font-semibold text-foreground">
            {quote.title || 'Untitled quote'}
          </h2>
          <p className="mt-1 font-mono text-xs text-muted-foreground">{quote.id}</p>
        </div>
        <div className="flex items-center gap-2">
          <PaymentBadge state={state} tone={paymentTone(railDetail)} />
          <Button onClick={onClose} icon="close">
            Close
          </Button>
        </div>
      </div>

      <div className="mt-4">
        <AcceptanceRail detail={railDetail} />
      </div>

      <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
        <div>
          <dt className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
            Amount due
          </dt>
          <dd className="mt-1 font-mono text-foreground">{formatMoney(due.total, quote.currency)}</dd>
        </div>
        <div>
          <dt className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
            Accepted by
          </dt>
          <dd className="mt-1 break-all font-mono text-xs text-foreground">
            {quote.acceptance?.accepted_by || 'not accepted'}
          </dd>
        </div>
        <div>
          <dt className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
            Payment type
          </dt>
          <dd className="mt-1 font-mono text-xs text-foreground">
            {detail.setup?.payment_type || 'not published'}
          </dd>
        </div>
        <div>
          <dt className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
            Payment status
          </dt>
          <dd className="mt-1 font-mono text-xs text-foreground">
            {detail.setup?.hs_payment_status || 'not published'}
          </dd>
        </div>
      </dl>

      <div className="mt-4 flex flex-wrap gap-2">
        {accepted && !paid ? (
          <Button variant="primary" disabled={busy} onClick={() => onPay(detail)} icon="plus">
            Take payment
          </Button>
        ) : null}
        {detail.setup ? (
          <Button disabled={busy} onClick={() => onAddTaxId(detail)} icon="plus">
            Add tax ID
          </Button>
        ) : null}
        {!accepted ? (
          <>
            <Button disabled={busy} onClick={() => onVoid(quote)} icon="close">
              Void
            </Button>
            <Button variant="danger" disabled={busy} onClick={() => onDelete(quote)} icon="trash">
              Delete
            </Button>
          </>
        ) : null}
      </div>

      {detail.line_items?.length || quote.line_items?.length ? (
        <>
          <h3 className="mt-6 text-base font-semibold text-foreground">Line items</h3>
          <ul className="mt-2">
            {(detail.line_items || quote.line_items || []).map((line) => (
              <li
                key={line.id}
                className="flex flex-wrap items-center justify-between gap-3 border-b border-border-subtle py-2 text-sm last:border-b-0"
              >
                <span className="text-foreground">{line.name}</span>
                <span className="font-mono text-xs text-muted-foreground">
                  {line.quantity} × {formatMoney(line.unit_price, quote.currency)} ={' '}
                  {formatMoney(line.amounts?.total ?? 0, quote.currency)}
                  {line.billing_frequency && line.billing_frequency !== 'one_time'
                    ? ` | ${line.billing_frequency}`
                    : ''}
                </span>
              </li>
            ))}
          </ul>
        </>
      ) : null}

      <h3 className="mt-6 text-base font-semibold text-foreground">Invoices</h3>
      {detail.invoices?.length ? (
        <ul className="mt-2">
          {detail.invoices.map((invoice) => (
            <InvoiceRow key={invoice.id} invoice={invoice} />
          ))}
        </ul>
      ) : (
        <p className="mt-2 text-sm text-muted-foreground">
          No invoices yet. Acceptance creates the first one immediately.
        </p>
      )}

      <h3 className="mt-6 text-base font-semibold text-foreground">Charges</h3>
      {detail.charges?.length ? (
        <ul className="mt-2">
          {detail.charges.map((charge) => (
            <ChargeRow key={charge.id} charge={charge} />
          ))}
        </ul>
      ) : (
        <p className="mt-2 text-sm text-muted-foreground">
          No charges yet. Payment is a separate step after acceptance.
        </p>
      )}

      {detail.subscriptions?.length ? (
        <>
          <h3 className="mt-6 text-base font-semibold text-foreground">Subscriptions</h3>
          <ul className="mt-2">
            {detail.subscriptions.map((subscription) => (
              <li
                key={subscription.id}
                className="flex flex-wrap items-center justify-between gap-3 border-b border-border-subtle py-2 text-sm last:border-b-0"
              >
                <span className="text-foreground">{subscription.name}</span>
                <span className="font-mono text-xs text-muted-foreground">
                  {subscription.frequency} from {formatDate(subscription.start_date)} |{' '}
                  {formatMoney(subscription.amount, subscription.currency)}
                </span>
              </li>
            ))}
          </ul>
        </>
      ) : null}

      {detail.tax_ids?.length ? (
        <>
          <h3 className="mt-6 text-base font-semibold text-foreground">Tax IDs</h3>
          <ul className="mt-2 flex flex-wrap gap-2">
            {detail.tax_ids.map((taxId) => (
              <li key={taxId.id}>
                <Badge>{taxId.value}</Badge>
              </li>
            ))}
          </ul>
        </>
      ) : null}

      <h3 className="mt-6 text-base font-semibold text-foreground">Activity</h3>
      {detail.activity?.length ? (
        <ul className="mt-2">
          {detail.activity.map((entry) => (
            <ActivityRow key={entry.id} entry={entry} />
          ))}
        </ul>
      ) : (
        <p className="mt-2 text-sm text-muted-foreground">No activity on this quote yet.</p>
      )}

      <div className="mt-6 space-y-2">
        <EvidenceNote label="Set up payment" quote={evidenceFor(vocabulary, 'set_up_payment')} />
        <EvidenceNote label="Irreversible" quote={evidenceFor(vocabulary, 'irreversible')} />
        <EvidenceNote
          label="Payment type"
          quote={evidenceFor(vocabulary, 'payment_type_automatic')}
        />
      </div>
    </Card>
  )
}

export function AcceptAndPayPage() {
  const summary = useAsync(() => paymentsApi.summary(ROOM), [])
  const quotes = useAsync(() => paymentsApi.quotes(ROOM), [])
  const vocabulary = useAsync(() => paymentsApi.vocabulary(), [])
  const decisions = useAsync(() => paymentsApi.decisions(), [])

  const [outcome, setOutcome] = useState(null)
  const [busy, setBusy] = useState(false)
  const [selected, setSelected] = useState(null)
  const [detail, setDetail] = useState(null)
  const [dialog, setDialog] = useState(null)
  const [dialogError, setDialogError] = useState(null)

  const reload = useCallback(async () => {
    await Promise.all([summary.refetch(), quotes.refetch()])
  }, [summary, quotes])

  const openQuote = useCallback(async (quoteId) => {
    setBusy(true)
    try {
      const body = await paymentsApi.quote(quoteId)
      setDetail(body)
      setSelected(quoteId)
    } catch (error) {
      setOutcome({ status: 'error', title: 'Could not open the quote', detail: error.message })
    } finally {
      setBusy(false)
    }
  }, [])

  const refreshDetail = useCallback(
    async (quoteId) => {
      if (selected === quoteId) {
        try {
          setDetail(await paymentsApi.quote(quoteId))
        } catch {
          setDetail(null)
        }
      }
    },
    [selected],
  )

  const run = useCallback(
    async (action, success) => {
      setBusy(true)
      setDialogError(null)
      try {
        const result = await action()
        setDialog(null)
        setOutcome(success(result))
        await reload()
        const quoteId = result?.quote_id || result?.quote?.id
        if (quoteId) await refreshDetail(quoteId)
      } catch (error) {
        setDialogError(error)
      } finally {
        setBusy(false)
      }
    },
    [reload, refreshDetail],
  )

  const createQuote = useCallback(
    async ({ quote, line }) => {
      setBusy(true)
      setDialogError(null)
      try {
        const created = await paymentsApi.createQuote(ROOM, quote)
        await paymentsApi.createLineItem(created.id, line)
        setDialog(null)
        setOutcome({
          status: 'ok',
          title: 'Demo quote created',
          detail: `${created.title || 'Untitled quote'} has one line item. Publish it for payments.`,
        })
        await reload()
      } catch (error) {
        setDialogError(error)
      } finally {
        setBusy(false)
      }
    },
    [reload],
  )

  const voidQuote = useCallback(
    async (quote) => {
      setBusy(true)
      try {
        await paymentsApi.voidQuote(quote.id, { actor: 'dana' })
        setOutcome({ status: 'ok', title: 'Quote voided', detail: `${quote.id} is void.` })
        await reload()
        await refreshDetail(quote.id)
      } catch (error) {
        setOutcome({ status: 'error', title: 'Could not void the quote', detail: error.message, reason: error.reason })
      } finally {
        setBusy(false)
      }
    },
    [reload, refreshDetail],
  )

  const deleteQuote = useCallback(
    async (quote) => {
      setBusy(true)
      try {
        await paymentsApi.deleteQuote(quote.id, { actor: 'dana' })
        setOutcome({ status: 'ok', title: 'Quote deleted', detail: `${quote.id} was removed.` })
        if (selected === quote.id) {
          setSelected(null)
          setDetail(null)
        }
        await reload()
      } catch (error) {
        setOutcome({
          status: 'error',
          title: 'Could not delete the quote',
          detail: error.message,
          reason: error.reason,
        })
      } finally {
        setBusy(false)
      }
    },
    [reload, selected],
  )

  if (summary.loading && quotes.loading) {
    return <Spinner label="Loading quote payments" />
  }
  if (summary.error) {
    return <ErrorNote error={summary.error} onRetry={summary.refetch} />
  }
  if (quotes.error) {
    return <ErrorNote error={quotes.error} onRetry={quotes.refetch} />
  }

  const board = summary.data || {}
  const rows = quotes.data?.quotes || []
  const facts = vocabulary.data || {}

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="font-display text-2xl font-semibold text-foreground">
            Accept a quote without a signature and take payment
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Clickwrap acceptance and the payment that follows it, with the researched minimum
            charge, first invoice and whole-number quantity rules.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="primary"
            icon="plus"
            onClick={() => {
              setDialogError(null)
              setDialog({ kind: 'new' })
            }}
          >
            New demo quote
          </Button>
          <Button icon="refresh" onClick={reload}>
            Refresh
          </Button>
        </div>
      </header>

      <Outcome outcome={outcome} onDismiss={() => setOutcome(null)} />

      <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Published quotes" value={board.setups || 0} icon="schema" />
        <StatCard
          label="Accepted"
          value={board.accepted || 0}
          hint={`${board.awaiting_acceptance || 0} awaiting acceptance`}
          icon="audit"
        />
        <StatCard
          label="Charged"
          value={formatMoney(board.charged_total || 0, 'USD')}
          hint={`${board.charges_recorded || 0} recorded, ${board.charges_declined || 0} declined`}
          icon="database"
        />
        <StatCard
          label="Awaiting payment"
          value={board.pending_payment || 0}
          hint={`${board.invoices_scheduled || 0} scheduled invoices`}
          icon="refresh"
        />
      </section>

      <section className="grid gap-4 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          {rows.length ? (
            rows.map((view) => (
              <QuoteCard
                key={view.quote.id}
                view={view}
                busy={busy}
                onOpen={openQuote}
                onPublish={(quote) => {
                  setDialogError(null)
                  setDialog({ kind: 'publish', quote })
                }}
                onAccept={(quote) => {
                  setDialogError(null)
                  setDialog({ kind: 'accept', quote })
                }}
                onPay={(d) => {
                  setDialogError(null)
                  setDialog({ kind: 'pay', detail: d })
                }}
                onVoid={voidQuote}
                onDelete={deleteQuote}
              />
            ))
          ) : (
            <EmptyState
              title="No quotes published for payments"
              description="Create a demo quote and publish it, or wait for WF-086 to provision one, then accept it without a signature and take payment."
              action={
                <Button
                  variant="primary"
                  icon="plus"
                  onClick={() => {
                    setDialogError(null)
                    setDialog({ kind: 'new' })
                  }}
                >
                  New demo quote
                </Button>
              }
            />
          )}

          {selected && detail ? (
            <QuoteDetail
              detail={detail}
              vocabulary={facts}
              busy={busy}
              onClose={() => {
                setSelected(null)
                setDetail(null)
              }}
              onPay={(d) => {
                setDialogError(null)
                setDialog({ kind: 'pay', detail: d })
              }}
              onAddTaxId={(d) => {
                setDialogError(null)
                setDialog({ kind: 'tax', detail: d })
              }}
              onVoid={voidQuote}
              onDelete={deleteQuote}
            />
          ) : null}
        </div>

        <div className="space-y-4">
          <MinimumChargePanel summary={board} evidence={evidenceFor(facts, 'minimum_charge')} />
          <TaxIdPanel summary={board} evidence={evidenceFor(facts, 'tax_id')} />
          <Card>
            <h3 className="text-base font-semibold text-foreground">
              What this workflow does not own
            </h3>
            <dl className="mt-2 space-y-3">
              {Object.entries(facts.not_owned || { quote: '', line_items: '' }).map(([key, value]) => (
                <div key={key}>
                  <dt className="font-mono text-xs text-muted-foreground">{key}</dt>
                  <dd className="mt-0.5 text-xs text-foreground">{value}</dd>
                </div>
              ))}
            </dl>
            <div className="mt-3 space-y-2">
              <EvidenceNote label="Whole-number quantity" quote={evidenceFor(facts, 'whole_number_quantity')} />
              <EvidenceNote
                label="Online payment acceptance"
                quote={evidenceFor(facts, 'online_payment_acceptance')}
              />
            </div>
          </Card>
        </div>
      </section>

      <section>
        <h2 className="text-lg font-semibold text-foreground">Decisions this workflow made</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          The research left these open. Each names the alternative that was rejected and what the
          rejection would have cost.
        </p>
        <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {(decisions.data?.decisions || []).map((decision) => (
            <Card key={decision.question}>
              <div className="flex flex-wrap items-start justify-between gap-2">
                <h3 className="text-sm font-semibold text-foreground">{decision.question}</h3>
                <Badge tone="insert">{decision.chosen}</Badge>
              </div>
              <p className="mt-2 text-xs text-muted-foreground">
                <span className="font-medium text-foreground">Rejected:</span>{' '}
                {decision.rejected_because}
              </p>
              {decision.left_open_by ? (
                <p className="mt-2 text-xs text-muted-foreground">{decision.left_open_by}</p>
              ) : null}
              {decision.audit_id ? (
                <p className="mt-2 font-mono text-xs text-muted-foreground">{decision.audit_id}</p>
              ) : null}
            </Card>
          ))}
        </div>
      </section>

      {dialog?.kind === 'new' ? (
        <NewQuoteDialog
          busy={busy}
          error={dialogError}
          onClose={() => setDialog(null)}
          onSubmit={createQuote}
        />
      ) : null}

      {dialog?.kind === 'publish' ? (
        <PublishDialog
          quote={dialog.quote}
          busy={busy}
          error={dialogError}
          onClose={() => setDialog(null)}
          onSubmit={(payload) =>
            run(
              () => paymentsApi.publish(dialog.quote.id, payload, { actor: 'dana' }),
              () => ({
                status: 'ok',
                title: 'Published for online payments',
                detail: 'The quote now carries the payment configuration and the whole-number rule.',
              }),
            )
          }
        />
      ) : null}

      {dialog?.kind === 'accept' ? (
        <AcceptDialog
          quote={dialog.quote}
          busy={busy}
          error={dialogError}
          onClose={() => setDialog(null)}
          onSubmit={(payload) =>
            run(
              () => paymentsApi.accept(dialog.quote.id, payload, { actor: 'ada' }),
              (result) => ({
                status: 'ok',
                title: 'Quote accepted without a signature',
                detail: `${result.acceptance.accepted_by}. ${plural(result.invoices.length, 'invoice')} created${
                  result.subscriptions.length
                    ? `, ${plural(result.subscriptions.length, 'subscription')}`
                    : ''
                }.`,
              }),
            )
          }
        />
      ) : null}

      {dialog?.kind === 'pay' ? (
        <PaymentDialog
          detail={dialog.detail}
          busy={busy}
          error={dialogError}
          onClose={() => setDialog(null)}
          onSubmit={(payload) =>
            run(
              () => paymentsApi.pay(dialog.detail.quote.id, payload, { actor: 'ada' }),
              (result) =>
                result.outcome === 'recorded'
                  ? {
                      status: 'ok',
                      title: 'Charge recorded',
                      detail: `${formatMoney(result.charge.amount, result.charge.currency)} via ${result.charge.payment_method}.`,
                    }
                  : {
                      status: 'declined',
                      title: 'Charge declined',
                      detail: result.detail || 'The processor declined the charge.',
                      reason: result.reason,
                    },
            )
          }
        />
      ) : null}

      {dialog?.kind === 'tax' ? (
        <TaxIdDialog
          quote={dialog.detail.quote}
          count={dialog.detail.tax_ids?.length || 0}
          busy={busy}
          error={dialogError}
          onClose={() => setDialog(null)}
          onSubmit={(payload) =>
            run(
              () => paymentsApi.addTaxId(dialog.detail.quote.id, payload, { actor: 'rui' }),
              (result) => ({
                status: 'ok',
                title: 'Tax ID added',
                detail: `${result.count} of ${result.limit} recorded.`,
              }),
            )
          }
        />
      ) : null}
    </div>
  )
}

export default {
  id: 'wf-096-accept-a-quote-without-a-signature-and-take',
  label: 'Accept and pay',
  icon: 'schema',
  order: 960,
  Component: AcceptAndPayPage,
}
