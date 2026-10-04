import { useState } from 'react'

import {
  Badge,
  Button,
  Card,
  ErrorNote,
  Field,
  Icon,
  Spinner,
  StatCard,
  useAsync,
} from '@/components/ui'

import {
  PRICING_ERROR_CODES,
  RECALCULATION_TRIGGERS,
  money,
  plural,
  pricingErrorCode,
  pricingOutcome,
  quoteCurrencyApi,
  rate,
  refusalSummary,
} from './api'
import {
  CurrencyRow,
  MoneyRow,
  Notice,
  NothingYet,
  OutcomeBadge,
  QuoteRow,
  QUOTE_CURRENCY_ICON,
  RateStamp,
  RefusalBadge,
  RunRow,
  Select,
  TextInput,
} from './primitives'

/**
 * WF-089: quote in a transaction currency with FX conversion.
 *
 * The page has five jobs, in this order, and the order is the design.
 *
 * **Name the base currency first.** The research says `*_Base` is "organization base
 * currency" and `ExchangeRate` converts "to the system's default currency", so every base
 * figure on this page is only meaningful against a named base. A board that showed a
 * converted total without saying what it was converted to would be unreadable.
 *
 * **Show both stored figures of every total, and say which is authoritative.** The issue
 * asks which of the two is the truth and demands the answer be recorded. It is the
 * transaction figure, so every total row is labelled rather than left for a reader to
 * guess from position.
 *
 * **Render a refusal as a refusal.** The specification says the platform "refuses to price
 * and sets pricingerrorcode", so code 34 and code 38 are first-class states on this page,
 * each rendered with its own wording and a detail line. A refusal that rendered as an empty
 * totals table would be indistinguishable from a quote nobody priced.
 *
 * **Let the price list be chosen wrongly, so the refusal can be seen.** The mismatch is
 * allowed at stamping and refused at pricing, which is the only shape in which both of the
 * research's sentences are true at once. The page therefore offers every price list on a
 * quote, including the one in the wrong currency.
 *
 * **Say when the rate was read.** The rate source is an event rather than a poll, so the
 * page shows the rate stamped on the quote and the instant it was read. That is what makes
 * a stored base figure a record rather than a view.
 *
 * Every state this page can be in is rendered: loading, error, and empty. A board that
 * goes blank when the API is down reads as "nothing is priced", which is the one reading
 * that must never be possible.
 */

/** The status line after a write. One shape, so every outcome reads the same way. */
function Outcome({ outcome, onDismiss }) {
  if (!outcome) return null
  const failed = outcome.status === 'error'
  return (
    <div
      role="status"
      className={`flex flex-wrap items-center justify-between gap-3 rounded-sm border p-4 ${
        failed ? 'border-destructive/40 bg-destructive/10' : 'border-accent/30 bg-accent/10'
      }`}
    >
      <div className="min-w-0">
        <p className={`text-sm font-semibold ${failed ? 'text-destructive' : 'text-accent'}`}>
          {outcome.title}
        </p>
        <p className="mt-0.5 text-sm text-muted-foreground">{outcome.detail}</p>
      </div>
      <Button icon="close" onClick={onDismiss}>
        Dismiss
      </Button>
    </div>
  )
}

/** A labelled text field. The label is always visible; placeholder-only is banned. */
function TextField({ id, label, hint, value, onChange, mono = true, placeholder }) {
  return (
    <Field label={label} hint={hint} id={id}>
      <TextInput
        id={id}
        value={value}
        onChange={onChange}
        placeholder={placeholder}
        className={mono ? 'font-mono' : 'font-sans'}
      />
    </Field>
  )
}

/** A labelled select. The option list is served, so it is never written here twice. */
function SelectField({ id, label, hint, value, onChange, options }) {
  return (
    <Field label={label} hint={hint} id={id}>
      <Select id={id} value={value} onChange={onChange} options={options} describedBy={hint ? `${id}-hint` : undefined} />
    </Field>
  )
}

/** One labelled fact. The label is text and the value is mono, because it is machine data. */
function Fact({ label, value, mono = false }) {
  return (
    <div className="border-t border-border-subtle pt-2">
      <dt className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">{label}</dt>
      <dd className={`mt-1 text-sm text-foreground ${mono ? 'font-mono' : ''}`}>{value}</dd>
    </div>
  )
}

/** The currency board: which currencies exist, which one is the base, and where a rate is writable. */
function CurrencyCard({ payload, selected, onSelect, busy, onStamp }) {
  const currencies = payload?.currencies || []
  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <h2 className="text-lg font-semibold">Transaction currencies</h2>
        <Badge tone="neutral">base: {payload?.base_currency || 'not set'}</Badge>
      </div>
      <p className="mt-1 text-xs text-muted-foreground">
        Every money field is stored twice: once in the quote&apos;s currency and once in the
        organisation&apos;s base currency. The rate is resolved from the currency record, so
        only a Custom record accepts a rate written here.
      </p>

      {currencies.length === 0 ? (
        <NothingYet
          title="No transaction currency registered"
          description="A currency record carries the precision and the rate a quote needs."
        />
      ) : (
        <ul className="mt-4 divide-y divide-border-subtle">
          {currencies.map((currency) => (
            <CurrencyRow
              key={currency.id}
              currency={currency}
              selected={currency.id === selected}
              onSelect={onSelect}
            />
          ))}
        </ul>
      )}

      <div className="mt-4 border-t border-border-subtle pt-3">
        <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <Fact label="Rate policy" value={payload?.rate_policy || 'unknown'} mono />
          <Fact label="Rate event" value={payload?.rate_event || 'unknown'} mono />
          <Fact label="Rate sources" value={(payload?.rate_sources || []).join(', ')} mono />
        </dl>
        <p className="mt-2 text-xs text-muted-foreground">{payload?.rate_event_note}</p>
      </div>

      {onStamp && selected ? (
        <div className="mt-4 border-t border-border-subtle pt-4">
          <p className="text-sm text-foreground">Stamp a custom rate</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Only a Custom currency record accepts one. Existing quotes keep the base figures
            they were priced with, and the response names how many hold the superseded rate.
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button icon="plus" onClick={onStamp} disabled={busy}>
              {busy ? 'Stamping...' : `Stamp a rate on ${selected}`}
            </Button>
          </div>
        </div>
      ) : null}
    </Card>
  )
}

/** The price lists, each in one currency. This is where multi-currency is visible. */
function PriceListCard({ payload }) {
  const lists = payload?.price_lists || []
  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <h2 className="text-lg font-semibold">Price lists</h2>
        <Badge tone="neutral">{plural(lists.length, 'list')}</Badge>
      </div>
      <p className="mt-1 text-xs text-muted-foreground">
        A price list carries one currency, so multi-currency is modelled as several lists.
        That is what makes &quot;34 Invalid Price Level Currency&quot; reachable: a caller can
        pick the wrong one.
      </p>

      {lists.length === 0 ? (
        <NothingYet
          title="No price list yet"
          description="A price list in one currency, with a price row per product on it."
        />
      ) : (
        <ul className="mt-4 grid gap-3 sm:grid-cols-2">
          {lists.map((list) => (
            <li key={list.id} className="rounded-sm border border-border-subtle p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-sm text-foreground">{list.name}</span>
                <Badge tone="neutral">
                  <span className="font-mono">{list.iso_code}</span>
                </Badge>
              </div>
              <p className="mt-1 font-mono text-xs text-muted-foreground">
                {plural(list.price_rows, 'price row')} / {list.products.join(', ') || 'no products'}
              </p>
            </li>
          ))}
        </ul>
      )}

      <div className="mt-4 border-t border-border-subtle pt-3">
        <p className="text-xs text-muted-foreground">{payload?.single_currency_reason}</p>
      </div>
    </Card>
  )
}

/** One quote in full: both sets of totals, the stamped rate, its lines and its runs. */
function QuoteDetail({ quote, runs, reads, busy, trigger, onTriggerChange, onPrice }) {
  const outcome = pricingOutcome(quote?.pricing_outcome)
  const refusal = quote?.pricing_error_code ? pricingErrorCode(quote.pricing_error_code) : null
  const lines = quote?.lines || []
  const isoCode = quote?.iso_code
  const baseIsoCode = quote?.base_iso_code

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <h2 className="text-lg font-semibold">{quote?.name}</h2>
        <div className="flex flex-wrap items-center gap-2">
          <OutcomeBadge outcome={quote?.pricing_outcome} />
          {refusal ? <RefusalBadge code={quote.pricing_error_code} /> : null}
        </div>
      </div>

      <dl className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Fact label="Transaction currency" value={isoCode} mono />
        <Fact label="Base currency" value={baseIsoCode || 'not resolved'} mono />
        <Fact label="Price list" value={quote?.price_list_iso_code || 'none selected'} mono />
        <Fact
          label="Price list currency"
          value={
            quote?.price_list_currency_matches === null || quote?.price_list_currency_matches === undefined
              ? 'not checked'
              : quote.price_list_currency_matches
                ? 'matches the header'
                : 'does not match the header'
          }
        />
      </dl>

      <p className="mt-3 text-xs text-muted-foreground">{outcome.meaning}</p>

      {refusal ? (
        <div className="mt-3">
          <Notice tone="warning" title={refusal.label}>
            <p>{quote.pricing_error || refusal.meaning}</p>
            {quote.pricing_error_code === '34' ? (
              <p className="mt-1">
                A base record and all its line items must use the same currency. The currency
                on the header cannot change while this quote holds line items, so remove them
                first.
              </p>
            ) : null}
          </Notice>
        </div>
      ) : null}

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <div>
          <p className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
            Both stored figures, transaction currency first
          </p>
          <dl className="mt-2">
            {(quote?.totals ? Object.keys(quote.totals) : []).map((name) => (
              <MoneyRow
                key={name}
                label={name}
                transactionValue={quote.totals[name]}
                baseValue={quote.totals_base?.[`${name}_base`]}
                isoCode={isoCode}
                baseIsoCode={baseIsoCode}
                primary={name === 'totalamount'}
              />
            ))}
          </dl>
          <p className="mt-2 text-xs text-muted-foreground">{quote?.authoritative_reason}</p>
        </div>

        <div className="space-y-4">
          <RateStamp quote={quote} />

          <div>
            <p className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
              Line items
            </p>
            {lines.length === 0 ? (
              <p className="mt-1 text-sm text-muted-foreground">
                This quote holds no line items, so its currency can still be changed.
              </p>
            ) : (
              <ul className="mt-2 divide-y divide-border-subtle">
                {lines.map((line) => (
                  <li
                    key={line.id}
                    className="flex min-h-11 flex-wrap items-center justify-between gap-2 py-1"
                  >
                    <span className="font-mono text-sm text-foreground">
                      {line.quantity} x {line.product_code}
                    </span>
                    <span className="font-mono text-xs text-muted-foreground">
                      price from the list /{' '}
                      {line.discount_amount ? `discount ${line.discount_amount}` : 'no discount'}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </div>

      <div className="mt-6 border-t border-border-subtle pt-4">
        <div className="flex flex-wrap items-end gap-3">
          <div className="min-w-[16rem] flex-1">
            <SelectField
              id="wf089-trigger"
              label="Recalculation trigger"
              hint="The six triggers the research names. Each run raises the rate event and stamps the answer onto the quote."
              value={trigger}
              onChange={onTriggerChange}
              options={RECALCULATION_TRIGGERS.map((entry) => ({ value: entry, label: entry }))}
            />
          </div>
          <Button
            icon="refresh"
            onClick={onPrice}
            disabled={busy}
            variant={busy ? 'secondary' : 'primary'}
          >
            {busy ? 'Pricing...' : 'Price this quote'}
          </Button>
        </div>
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <div>
          <p className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
            Pricing runs, newest first
          </p>
          {runs.length === 0 ? (
            <p className="mt-1 text-sm text-muted-foreground">
              No pricing run has answered for this quote yet.
            </p>
          ) : (
            <ul className="mt-2">
              {runs.map((run) => (
                <RunRow key={run.id} run={run} />
              ))}
            </ul>
          )}
        </div>

        <div>
          <p className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
            Rate reads, newest first
          </p>
          {reads.length === 0 ? (
            <p className="mt-1 text-sm text-muted-foreground">
              No rate has been read for this quote yet.
            </p>
          ) : (
            <ul className="mt-2 divide-y divide-border-subtle">
              {reads.map((read) => (
                <li
                  key={read.id}
                  className="flex min-h-11 flex-wrap items-center justify-between gap-2 py-1"
                >
                  <span className="font-mono text-xs text-muted-foreground">{read.read_at}</span>
                  <span className="font-mono text-xs text-foreground">
                    {read.trigger_label || read.trigger} / rate {rate(read.rate)} ({read.source})
                  </span>
                </li>
              ))}
            </ul>
          )}
          <p className="mt-2 text-xs text-muted-foreground">
            A refused run reads here too. The event fires before the refusal is decided, so
            the figures a refusal cleared can be traced.
          </p>
        </div>
      </div>
    </Card>
  )
}

/** Every judgement call this workflow made, with what it rejected. */
function RecordedDecisions({ decisions }) {
  if (!decisions?.length) return null
  return (
    <Card>
      <h2 className="text-lg font-semibold">Recorded decisions</h2>
      <p className="mt-1 text-xs text-muted-foreground">
        The specification asks an implementer to record each derivation rather than assume
        it. These are the questions the research left open and what this build chose.
      </p>
      <ul className="mt-4 space-y-3">
        {decisions.map((decision) => (
          <li key={decision.id} className="rounded-sm border border-border-subtle p-3">
            <p className="font-mono text-xs text-muted-foreground">{decision.id}</p>
            <p className="text-sm text-foreground">{decision.question}</p>
            <p className="mt-1 text-xs text-muted-foreground">
              Chose <span className="font-mono text-foreground">{decision.chosen}</span>.{' '}
              {decision.rejected_because}
            </p>
            {decision.jev_audit_id ? (
              <p className="mt-1 font-mono text-xs text-muted-foreground">
                Jev {decision.jev_audit_id} at confidence {decision.jev_confidence}
              </p>
            ) : null}
          </li>
        ))}
      </ul>
    </Card>
  )
}

export function QuoteCurrencyPage() {
  const [roomId, setRoomId] = useState('')
  const [outcome, setOutcome] = useState(null)
  const [busy, setBusy] = useState(false)
  const [selectedCurrencyId, setSelectedCurrencyId] = useState('')
  const [selectedQuoteId, setSelectedQuoteId] = useState('')
  const [trigger, setTrigger] = useState('record_open')

  const board = useAsync(() => quoteCurrencyApi.summary(roomId || undefined), [roomId])
  const currencies = useAsync(() => quoteCurrencyApi.currencies(roomId || undefined), [roomId])
  const priceLists = useAsync(() => quoteCurrencyApi.priceLists(roomId || undefined), [roomId])
  const quotes = useAsync(() => quoteCurrencyApi.quotes(roomId || undefined), [roomId])
  const vocabulary = useAsync(() => quoteCurrencyApi.vocabulary(), [])
  const decisions = useAsync(() => quoteCurrencyApi.decisions(), [])

  const runs = useAsync(
    () =>
      selectedQuoteId
        ? quoteCurrencyApi.pricingRuns(selectedQuoteId, roomId || undefined)
        : Promise.resolve({ runs: [] }),
    [selectedQuoteId, roomId],
  )
  const reads = useAsync(
    () =>
      selectedQuoteId
        ? quoteCurrencyApi.rateReads(selectedQuoteId, roomId || undefined)
        : Promise.resolve({ reads: [] }),
    [selectedQuoteId, roomId],
  )

  const quoteRows = quotes.data?.quotes || []
  const selectedQuote = quoteRows.find((row) => row.id === selectedQuoteId) || null
  const summary = board.data || {}

  /** Refresh every read this page holds, so a write and its board never disagree. */
  function refreshAll() {
    board.refetch()
    currencies.refetch()
    priceLists.refetch()
    quotes.refetch()
    runs.refetch()
    reads.refetch()
  }

  /**
   * Run one write, report what happened, and refresh the reads.
   *
   * A refusal is not a failure here. The pricing endpoint answers 200 for a refusal, so
   * this function has to read the body to know which of the two happened; that is why
   * `describeResult` looks at `run.outcome` rather than assuming success.
   */
  async function act(title, call) {
    setBusy(true)
    try {
      const payload = await call()
      setOutcome({ status: 'ok', title, detail: describeResult(payload) })
      refreshAll()
    } catch (error) {
      setOutcome({ status: 'error', title: `${title} was refused`, detail: refusalSummary(error) })
      refreshAll()
    } finally {
      setBusy(false)
    }
  }

  if (board.loading && !board.data) return <Spinner label="Loading quote currency" />
  if (board.error) return <ErrorNote error={board.error} onRetry={board.refetch} />

  return (
    <div className="space-y-6">
      <header>
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-semibold">Quote currency</h1>
          <Badge tone="neutral">
            <span className="font-mono">{summary.product_model || 'dynamics_dual_currency_rows'}</span>
          </Badge>
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          Price a quote in a transaction currency, derive every total into the base currency
          from the rate stamped on the quote, and refuse a wrong-currency combination rather
          than return a partial total.
        </p>
      </header>

      <Outcome outcome={outcome} onDismiss={() => setOutcome(null)} />

      <Card>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard
            label="Quotes"
            value={summary.quotes ?? 0}
            hint={`${summary.priced_quotes ?? 0} priced`}
          />
          <StatCard
            label="Refused"
            value={summary.refused_quotes ?? 0}
            hint="Recorded outcomes, not faults"
          />
          <StatCard
            label="Base currency"
            value={summary.base_currency || 'not set'}
            hint="Every _Base figure is in this"
          />
          <StatCard
            label="Rate reads"
            value={summary.rate_reads ?? 0}
            hint={summary.rate_event || 'RetrieveExchangeRate'}
          />
        </div>

        {summary.totals_in_base_currency ? (
          <div className="mt-4 border-t border-border-subtle pt-3">
            <p className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
              Priced quotes together
            </p>
            <p className="mt-1 font-mono text-sm text-foreground">
              {money(
                summary.totals_in_base_currency.amount,
                summary.totals_in_base_currency.iso_code,
              )}{' '}
              from {plural(summary.totals_in_base_currency.quotes_counted, 'quote')}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              {summary.totals_in_base_currency.note}
            </p>
          </div>
        ) : null}
      </Card>

      <Card>
        <h2 className="text-lg font-semibold">What this page is pointed at</h2>
        <div className="mt-3 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <TextField
            id="wf089-room"
            label="Room id"
            hint="Leave empty to read every room."
            value={roomId}
            onChange={setRoomId}
            placeholder="room_a"
          />
        </div>
      </Card>

      {currencies.error ? (
        <ErrorNote error={currencies.error} onRetry={currencies.refetch} />
      ) : (
        <CurrencyCard
          payload={currencies.data}
          selected={selectedCurrencyId}
          onSelect={setSelectedCurrencyId}
          busy={busy}
          onStamp={() => {
            const currency = (currencies.data?.currencies || []).find(
              (row) => row.id === selectedCurrencyId,
            )
            if (!currency?.rate_is_writable) return
            const next = window.prompt(
              `Base-currency units per one ${currency.iso_code}`,
              String(currency.exchange_rate ?? '1.08'),
            )
            if (!next) return
            act(`Stamping a rate on ${currency.iso_code}`, () =>
              quoteCurrencyApi.stampRate(currency.id, next, {
                roomId: roomId || undefined,
                actor: 'finance-desk',
              }),
            )
          }}
        />
      )}

      {priceLists.error ? (
        <ErrorNote error={priceLists.error} onRetry={priceLists.refetch} />
      ) : (
        <PriceListCard payload={priceLists.data} />
      )}

      {quotes.error ? (
        <ErrorNote error={quotes.error} onRetry={quotes.refetch} />
      ) : (
        <>
          <Card>
            <div className="flex flex-wrap items-start justify-between gap-3">
              <h2 className="text-lg font-semibold">Quotes</h2>
              <Badge tone="neutral">{plural(quoteRows.length, 'quote')}</Badge>
            </div>
            <p className="mt-1 text-xs text-muted-foreground">
              A quote that was refused to price still holds its currency, its price list and
              its line items. It holds no totals, because a half-priced quote cannot be told
              from a priced one.
            </p>

            {quoteRows.length === 0 ? (
              <NothingYet
                title="No quote yet"
                description="A quote appears here once a header is stamped with a transaction currency."
              />
            ) : (
              <ul className="mt-4 divide-y divide-border-subtle">
                {quoteRows.map((quote) => (
                  <QuoteRow
                    key={quote.id}
                    quote={quote}
                    selected={quote.id === selectedQuoteId}
                    onSelect={(row) => setSelectedQuoteId(row.id)}
                  />
                ))}
              </ul>
            )}

            {(summary.refusals_by_code || {}) &&
            Object.entries(summary.refusals_by_code).some(([, count]) => count > 0) ? (
              <div className="mt-4 border-t border-border-subtle pt-3">
                <p className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
                  Refusals by pricing error code
                </p>
                <ul className="mt-2 space-y-2">
                  {Object.entries(summary.refusals_by_code)
                    .filter(([, count]) => count > 0)
                    .map(([code, count]) => (
                      <li key={code} className="flex flex-wrap items-start gap-2">
                        <RefusalBadge code={code} />
                        <span className="text-xs text-muted-foreground">
                          {plural(count, 'quote')} refused
                        </span>
                      </li>
                    ))}
                </ul>
                <p className="mt-2 text-xs text-muted-foreground">{summary.refusal_is_outcome}</p>
              </div>
            ) : null}
          </Card>

          {selectedQuote ? (
            <QuoteDetail
              quote={selectedQuote}
              runs={runs.data?.runs || []}
              reads={reads.data?.reads || []}
              busy={busy}
              trigger={trigger}
              onTriggerChange={setTrigger}
              onPrice={() =>
                act(`Pricing ${selectedQuote.name}`, () =>
                  quoteCurrencyApi.priceQuote(selectedQuote.id, { trigger }, {
                    roomId: roomId || undefined,
                    actor: 'finance-desk',
                  }),
                )
              }
            />
          ) : null}
        </>
      )}

      {vocabulary.data ? (
        <Card>
          <h2 className="text-lg font-semibold">The researched vocabulary</h2>
          <p className="mt-1 text-xs text-muted-foreground">
            Served rather than duplicated here, so this page cannot drift from the rules that
            compute the figures above it.
          </p>
          <dl className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Fact label="Product model" value={vocabulary.data.product_model} mono />
            <Fact label="Authoritative figure" value={vocabulary.data.authoritative} mono />
            <Fact label="Rate policy" value={vocabulary.data.rate_policy} mono />
            <Fact label="Rate event" value={vocabulary.data.rate_event} mono />
          </dl>

          <div className="mt-4 border-t border-border-subtle pt-3">
            <p className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
              Refusal codes
            </p>
            <ul className="mt-2 space-y-2">
              {PRICING_ERROR_CODES.map((entry) => (
                <li key={entry.value} className="text-xs text-muted-foreground">
                  <RefusalBadge code={entry.value} />
                  <span className="mt-1 block">{entry.meaning}</span>
                </li>
              ))}
            </ul>
          </div>

          <div className="mt-4 border-t border-border-subtle pt-3">
            <p className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
              Recalculation triggers
            </p>
            <ul className="mt-2 grid gap-2 sm:grid-cols-2">
              {(vocabulary.data.recalculation_triggers || []).map((entry) => (
                <li key={entry} className="text-xs text-muted-foreground">
                  <span className="font-mono text-foreground">{entry}</span> /{' '}
                  {vocabulary.data.recalculation_trigger_labels?.[entry]}
                </li>
              ))}
            </ul>
          </div>

          <div className="mt-4 border-t border-border-subtle pt-3">
            <p className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
              The two money field lists
            </p>
            <p className="mt-1 font-mono text-xs text-foreground">
              {(vocabulary.data.transaction_totals || []).join(', ')}
            </p>
            <p className="mt-1 font-mono text-xs text-foreground">
              {(vocabulary.data.base_totals || []).join(', ')}
            </p>
            <p className="mt-2 text-xs text-muted-foreground">
              {vocabulary.data.authoritative_reason}
            </p>
          </div>

          <div className="mt-4 border-t border-border-subtle pt-3">
            <p className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
              Dependencies
            </p>
            <ul className="mt-2 space-y-2">
              {(vocabulary.data.dependencies || []).map((entry) => (
                <li key={entry.ticket} className="text-xs text-muted-foreground">
                  <span className="font-mono text-foreground">{entry.ticket}</span> /{' '}
                  {entry.name} ({entry.status}). {entry.this_workflow_does}
                </li>
              ))}
            </ul>
          </div>
        </Card>
      ) : null}

      {decisions.data ? <RecordedDecisions decisions={decisions.data.decisions} /> : null}

      <Card>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <h2 className="text-base font-semibold">What this page does not claim</h2>
          <Icon name="audit" />
        </div>
        <p className="mt-2 text-sm text-muted-foreground">
          No rate is fetched from a market here. The rate comes from the deployment&apos;s own
          currency records, raised through the event the research names.
        </p>
        <p className="mt-2 text-sm text-muted-foreground">
          No base figure is recomputed on read. The figures shown are the ones stored when
          the quote was priced, which is why a re-stamped currency rate does not move them.
        </p>
      </Card>
    </div>
  )
}

/**
 * One sentence per write, so the status line says what happened rather than that something
 * did.
 *
 * A pricing run has two answers and both are successes from here: a run either priced or
 * refused, and the refused case is a state the seller needs to read about. It gets its own
 * sentence rather than being reported as done.
 */
function describeResult(payload) {
  if (!payload) return 'Done.'
  if (payload.run) {
    if (payload.run.outcome === 'refused') {
      return `Refused to price with code ${payload.run.pricing_error_code} ${payload.run.pricing_error}. ${payload.run.detail}`
    }
    return `Priced at a rate of ${rate(payload.run.rate)} (${payload.run.trigger_label || payload.run.trigger}). Transaction total ${payload.run.totals.totalamount}, base total ${payload.run.totals_base.totalamount_base}.`
  }
  if (payload.exchange_rate !== undefined && payload.previous_rate !== undefined) {
    return `Rate stamped: ${payload.previous_rate} is now ${payload.exchange_rate}. ${payload.quotes_holding_the_superseded_rate} quote(s) keep the figures they were priced with.`
  }
  if (payload.line) {
    return `Added ${payload.line.product_code}. ${payload.recalculate.note}`
  }
  if (payload.iso_code) {
    return `The quote is now in ${payload.iso_code}.`
  }
  if (payload.totalamount !== undefined || payload.is_base_currency !== undefined) {
    return 'Currency record registered.'
  }
  if (payload.name && payload.products) {
    return `Price list ${payload.name} created in ${payload.iso_code}.`
  }
  return 'Done.'
}

export default {
  id: 'wf-089-quote-in-a-transaction-currency-with-fx-conversion',
  label: 'Quote currency',
  // `audit` is the shared glyph that reads closest to a figures board, and it is a name
  // that already exists in the shared PATHS map. `components/ui.jsx` is not edited; the
  // page's own two-currency mark is passed as `iconPath` alongside it.
  icon: 'audit',
  iconPath: QUOTE_CURRENCY_ICON,
  order: 890,
  Component: QuoteCurrencyPage,
}