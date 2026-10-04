/**
 * The few shapes this workflow needs that the shared primitives do not carry.
 *
 * Everything that exists in `@/components/ui` is imported from there and never copied:
 * `Badge`, `Button`, `Card`, `EmptyState`, `ErrorNote`, `Field`, `Icon`, `JsonView`,
 * `Spinner`, `StatCard`, `inputClass` and `useAsync` are all shared. This folder holds only
 * what that file genuinely does not export, and it says so rather than quietly
 * reimplementing a primitive somebody else owns.
 *
 * Three rules every component here obeys, because they are the accessibility floor rather
 * than styling:
 *
 * - **No status is conveyed by colour alone.** Every badge renders its status as text. A
 *   badge whose only difference is its colour tells a screen-reader user nothing.
 * - **Every icon sits beside a text label** or carries an `aria-label`. An icon alone is
 *   never the only label.
 * - **Every control is at least 44px tall** (`min-h-11`), because it is a target somebody
 *   has to hit with a finger.
 *
 * The one primitive here that a reviewer should look at hardest is `MoneyRow`. It renders
 * both stored figures of one total beside each other, because the whole point of this
 * workflow is that a money value is stored twice and one of the two is authoritative. A row
 * that showed only the transaction figure would hide the question the workflow exists to
 * answer.
 */

import { Badge, Card, EmptyState, Icon, inputClass } from '@/components/ui'

import { money, pricingErrorCode, pricingOutcome, rate } from './api'

/**
 * The nav glyph: two currency marks exchanging, one above the other.
 *
 * Passed as `iconPath` rather than as an `icon` name, because the shared `PATHS` map is not
 * ours to edit and this glyph is not in it. No glyph is hand-rolled for an *interface* icon;
 * this is the page's own mark, which is what `iconPath` exists for.
 */
export const QUOTE_CURRENCY_ICON =
  'M12 3v18M8 7h6a3 3 0 010 6H9a3 3 0 000 6h7M15 7l-2-2m2 2l-2 2M9 19l2-2m-2 2l2 2'

/**
 * A short block of text that carries a tone: an invariant, a refusal, a note.
 *
 * Not an error. `ErrorNote` is the error and is used instead of this wherever the request
 * actually failed.
 */
export function Notice({ tone = 'neutral', title, children }) {
  const tones = {
    neutral: 'border-border-subtle bg-muted text-foreground',
    info: 'border-border-subtle bg-accent-soft text-foreground',
    warning: 'border-warning/40 bg-warning/10 text-foreground',
    danger: 'border-destructive/40 bg-destructive/10 text-foreground',
    success: 'border-success/40 bg-success/10 text-foreground',
  }
  return (
    <div className={`rounded-sm border p-4 ${tones[tone] || tones.neutral}`}>
      {title && <p className="text-sm font-semibold">{title}</p>}
      {children && <div className="mt-1 text-sm text-muted-foreground">{children}</div>}
    </div>
  )
}

/**
 * A labelled text input built on the shared `inputClass`, so an input on this page cannot
 * drift from the other hundred in the product.
 *
 * `min-h-11` is on every control here. It is the accessibility floor and it is checked in
 * review.
 */
export function TextInput({ id, value, onChange, type = 'text', ...rest }) {
  return (
    <input
      id={id}
      type={type}
      value={value}
      onChange={(event) => onChange(event.target.value)}
      className={`${inputClass} min-h-11 font-mono`}
      {...rest}
    />
  )
}

/**
 * A labelled dropdown, also on `inputClass`.
 *
 * The native `select` is kept rather than replaced by a custom listbox, because it is
 * keyboard- and screen-reader-correct for free.
 */
export function Select({ id, value, onChange, options, describedBy }) {
  return (
    <select
      id={id}
      value={value}
      onChange={(event) => onChange(event.target.value)}
      aria-describedby={describedBy}
      className={`${inputClass} min-h-11 font-mono`}
    >
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  )
}

/**
 * A quote's pricing outcome, as a badge that always names itself.
 *
 * The label comes from the served vocabulary rather than from a map written here, so an
 * outcome this build has not heard of renders as its own name instead of an empty badge.
 */
export function OutcomeBadge({ outcome }) {
  const entry = pricingOutcome(outcome)
  return (
    <Badge tone={entry.tone}>
      <span className="inline-flex items-center gap-1.5">
        <Icon name="audit" size={14} />
        <span className="font-mono">{entry.label}</span>
      </span>
    </Badge>
  )
}

/**
 * A refusal, rendered as the specification's code and its own wording.
 *
 * Code first, then the label. A refusal that rendered only as a coloured badge would be the
 * failure this workflow exists to prevent, so the code is text and the label is text and
 * both are on the page.
 */
export function RefusalBadge({ code }) {
  const entry = pricingErrorCode(code)
  return (
    <Badge tone={entry.tone}>
      <span className="inline-flex items-center gap-1.5">
        <Icon name="close" size={14} />
        <span className="font-mono">{entry.label}</span>
      </span>
    </Badge>
  )
}

/**
 * One total, in both the currencies it is stored in, with the authoritative one named.
 *
 * This is the component the page exists to make renderable. The research requires both
 * figures and asks which is authoritative; the answer is that the transaction figure is, so
 * the row labels it as such rather than letting a reader assume the base figure is the
 * one an issued quote agrees to.
 *
 * A figure the server has not computed renders as a dash. A refused quote has no figure in
 * either currency, and rendering that as zero would say the quote cost nothing.
 */
export function MoneyRow({ label, transactionValue, baseValue, isoCode, baseIsoCode, primary }) {
  return (
    <div className="flex min-h-11 flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b border-border-subtle py-2 last:border-0">
      <span className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
        {label}
        {primary && <span className="ml-1 normal-case text-accent">authoritative</span>}
      </span>
      <span className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <span className="font-mono text-sm text-foreground">
          {money(transactionValue, isoCode)}
        </span>
        <span className="font-mono text-xs text-muted-foreground">
          {money(baseValue, baseIsoCode)}
        </span>
      </span>
    </div>
  )
}

/**
 * The rate a quote was priced at, and where that rate came from.
 *
 * The source is rendered as words beside the number because "1.08" alone does not say
 * whether the deployment negotiated it or the platform published it, and the research
 * distinguishes the two.
 */
export function RateStamp({ quote }) {
  return (
    <div className="rounded-sm border border-border-subtle bg-muted p-3">
      <p className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
        Exchange rate stamped on this quote
      </p>
      <p className="mt-1 font-mono text-sm text-foreground">
        {rate(quote?.exchangerate)} base per 1 {quote?.iso_code || 'transaction currency'}
      </p>
      <p className="mt-1 text-xs text-muted-foreground">
        {quote?.rate_source
          ? `Source: ${quote.rate_source}. Read at ${quote.rate_read_at || 'no time recorded'}.`
          : 'No rate has been read for this quote yet.'}
      </p>
      <p className="mt-2 text-xs text-muted-foreground">
        A re-stamped currency rate does not move these figures. A quote is a record of what
        was priced.
      </p>
    </div>
  )
}

/** A quote in the list: its currency, its outcome, and both totals on one line. */
export function QuoteRow({ quote, selected, onSelect }) {
  const outcome = pricingOutcome(quote?.pricing_outcome)
  return (
    <li>
      <button
        type="button"
        onClick={() => onSelect(quote)}
        aria-pressed={quote.id === selected}
        className={`flex min-h-11 w-full flex-wrap items-center justify-between gap-3 px-1 py-2 text-left hover:bg-muted ${
          quote.id === selected ? 'bg-accent-soft' : ''
        }`}
      >
        <span className="min-w-0">
          <span className="block truncate text-sm text-foreground">{quote.name}</span>
          <span className="block font-mono text-xs text-muted-foreground">
            {quote.iso_code} / {pluralLines(quote.line_count)} /{' '}
            {quote.pricing_error_code
              ? `code ${quote.pricing_error_code}`
              : money(quote.totals?.totalamount, quote.iso_code)}
          </span>
        </span>
        <Badge tone={outcome.tone}>
          <span className="font-mono">{outcome.label}</span>
        </Badge>
      </button>
    </li>
  )
}

function pluralLines(count) {
  const total = Number(count) || 0
  return `${total} line${total === 1 ? '' : 's'}`
}

/** A currency record: its code, its type, and whether its rate may be written here. */
export function CurrencyRow({ currency, selected, onSelect }) {
  return (
    <li>
      <button
        type="button"
        onClick={() => onSelect(currency)}
        aria-pressed={currency.id === selected}
        className={`flex min-h-11 w-full flex-wrap items-center justify-between gap-3 px-1 py-2 text-left hover:bg-muted ${
          currency.id === selected ? 'bg-accent-soft' : ''
        }`}
      >
        <span className="min-w-0">
          <span className="block font-mono text-sm text-foreground">
            {currency.iso_code} {currency.currency_symbol || ''}
          </span>
          <span className="block text-xs text-muted-foreground">
            {currency.name} / {currency.currency_precision} decimal places
          </span>
        </span>
        <span className="flex flex-wrap items-center gap-2">
          {currency.is_base_currency && <Badge tone="insert">base currency</Badge>}
          <Badge tone={currency.rate_is_writable ? 'warning' : 'neutral'}>
            <span className="font-mono">
              {currency.rate_is_writable ? 'rate writable here' : 'rate from the platform'}
            </span>
          </Badge>
        </span>
      </button>
    </li>
  )
}

/**
 * A pricing run: when, why, at what rate, and what it decided.
 *
 * A refused run renders in this table with its code, not as an absent row. The run is the
 * evidence that the refusal was a decision rather than a gap.
 */
export function RunRow({ run }) {
  const outcome = pricingOutcome(run?.outcome)
  return (
    <li className="border-b border-border-subtle py-2 last:border-0">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-mono text-xs text-muted-foreground">{run?.ran_at}</span>
        <span className="flex flex-wrap items-center gap-2">
          <Badge tone="neutral">
            <span className="font-mono">{run?.trigger_label || run?.trigger}</span>
          </Badge>
          <Badge tone={outcome.tone}>
            <span className="font-mono">{outcome.label}</span>
          </Badge>
        </span>
      </div>
      <p className="mt-1 font-mono text-xs text-muted-foreground">
        rate {rate(run?.rate)} ({run?.rate_source}) / {pluralLines(run?.line_count)}
      </p>
      {run?.pricing_error_code ? (
        <p className="mt-1 text-xs text-foreground">{run.detail}</p>
      ) : run?.totals ? (
        <p className="mt-1 font-mono text-xs text-foreground">
          {money(run.totals.totalamount, run.iso_code)} /{' '}
          {money(run.totals_base?.totalamount_base, run.base_iso_code)}
        </p>
      ) : null}
    </li>
  )
}

/**
 * The empty state for a list that has nothing in it yet.
 *
 * The shared `EmptyState` inside a `Card`, so the dashed border and the copy are the same
 * everywhere in the product.
 */
export function NothingYet({ title, description, action }) {
  return (
    <Card>
      <EmptyState title={title} description={description} action={action} />
    </Card>
  )
}