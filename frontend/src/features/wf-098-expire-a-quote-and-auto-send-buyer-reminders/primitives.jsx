/**
 * The few shapes this workflow needs that the shared primitives do not carry.
 *
 * Everything that exists in `@/components/ui` is imported from there and never copied.
 * `Badge`, `Card`, `Button`, `EmptyState`, `ErrorNote`, `Field`, `Icon`, `Spinner`,
 * `StatCard`, `inputClass` and `useAsync` are all shared and all imported from there. This
 * folder holds only what that file genuinely does not export, and it says so rather than
 * quietly reimplementing a primitive somebody else owns.
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
 */

import { Badge, Card, EmptyState, Icon, inputClass } from '@/components/ui'

/**
 * The nav glyph: a document with a clock across it, which is a quote with a deadline.
 * Passed as `iconPath` rather than as an `icon` name, because the shared `PATHS` map is
 * not ours to edit and this glyph is not in it.
 */
export const QUOTE_EXPIRY_ICON =
  'M6 3h8l4 4v14H6V3zm8 0v4h4M9 13l2 2 4-4M9 17h6M14 8v3l2 1'

/**
 * A short block of text that carries a tone: the invariants, a refusal reason, a quote
 * from the research, a recorded API gap. Not an error - `ErrorNote` is the error, and it is
 * used instead of this wherever the request actually failed.
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
 * A labelled input built on the shared `inputClass`, so an input on this page cannot drift
 * from the other hundred in the product.
 *
 * `min-h-11` is on every control here. It is the accessibility floor and it is checked in
 * review.
 */
export function LabeledInput({ id, label, hint, error, className = '', ...props }) {
  return (
    <div className={`flex flex-col gap-1.5 ${className}`}>
      <label htmlFor={id} className="text-[13px] font-medium text-foreground">
        {label}
      </label>
      <input
        id={id}
        className={error ? `${inputClass} border-destructive` : inputClass}
        aria-invalid={error ? 'true' : undefined}
        {...props}
      />
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  )
}

/**
 * A switch, built here because the shared module exports no `Toggle` on this branch.
 *
 * The feature contract says to build a primitive that genuinely does not exist inside my
 * own folder and say so in the pull request, rather than editing the shared file.
 *
 * It is a real `button` with `role="switch"` and `aria-checked`, so it is reachable by
 * keyboard and announces its state. The label is visible text, never a placeholder, and
 * the hit target is `min-h-11`.
 */
export function Switch({ checked, onChange, label, hint, id, disabled }) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div className="min-w-0">
        <label htmlFor={id} className="text-[13px] font-medium text-foreground">
          {label}
        </label>
        {hint && <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>}
      </div>
      <button
        id={id}
        type="button"
        role="switch"
        aria-checked={checked ? 'true' : 'false'}
        aria-label={label}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={`inline-flex min-h-11 min-w-11 shrink-0 items-center justify-center
          rounded-sm border px-3 text-xs font-medium
          ${checked ? 'border-accent bg-accent-soft text-accent' : 'border-border-subtle bg-surface text-muted-foreground'}
          hover:border-accent disabled:cursor-not-allowed disabled:opacity-50`}
      >
        {checked ? 'On' : 'Off'}
      </button>
    </div>
  )
}

/**
 * A select, built here for the same reason as the switch: the shared module exports no
 * `Select`, and the two researched pickers - the offset kind and the acceptance method -
 * are choices from a fixed vocabulary, not free text.
 *
 * `min-h-11` and a visible `Field` label, so it meets the same floor as every other
 * control on the page.
 */
export function LabeledSelect({ id, label, hint, error, children, className = '', ...props }) {
  return (
    <div className={`flex flex-col gap-1.5 ${className}`}>
      <label htmlFor={id} className="text-[13px] font-medium text-foreground">
        {label}
      </label>
      <select
        id={id}
        className={error ? `${inputClass} border-destructive` : inputClass}
        aria-invalid={error ? 'true' : undefined}
        {...props}
      >
        {children}
      </select>
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  )
}

/**
 * The heading and the label beside it, in the micro-label style the design system
 * prescribes.
 */
export function SectionHeading({ title, hint, count }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-2">
      <div>
        <h2 className="text-lg font-semibold text-foreground">{title}</h2>
        {hint && <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>}
      </div>
      {count === undefined ? null : (
        <span className="font-mono text-xs text-muted-foreground">{count}</span>
      )}
    </div>
  )
}

/**
 * A machine value in the mono face.
 *
 * Mono is for IDs, timestamps, counts and JSON, never for prose, which is what the type
 * table in the design system says. Every value this renders is a machine value.
 */
export function Mono({ children, className = '' }) {
  return <span className={`font-mono text-xs ${className}`}>{children}</span>
}

/**
 * One quote row: its title, its state as text, its deadline, and what is left to do.
 *
 * Status is a word in a badge, never a colour on its own. A quote whose switch is off
 * says "No expiration date" rather than showing a zero, because a missing deadline is not
 * a deadline of zero and the research is explicit that such a quote never expires.
 */
export function QuoteRow({ quote, onSelect, busy }) {
  const tone = toneFor(quote.state)
  const overdue = quote.expiration_enabled && (quote.days_remaining ?? 0) < 0

  return (
    <button
      type="button"
      onClick={() => onSelect(quote)}
      disabled={busy}
      className="flex w-full min-h-11 flex-col gap-2 rounded-sm border border-border-subtle
        bg-surface p-4 text-left hover:border-accent disabled:cursor-not-allowed disabled:opacity-60"
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <p className="min-w-0 text-sm font-medium text-foreground">{quote.title}</p>
        <Badge tone={tone}>{quote.state}</Badge>
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <span>
          Deadline:{' '}
          {quote.expiration_enabled ? (quote.expiration_date_only || 'Not set') : 'Switch off'}
        </span>
        {quote.expiration_enabled && quote.days_remaining !== null && (
          <span className={overdue ? 'text-destructive' : ''}>
            {overdue
              ? `Past due by ${Math.abs(Math.round(quote.days_remaining))} day(s)`
              : `${Math.max(0, Math.round(quote.days_remaining))} day(s) left`}
          </span>
        )}
        {quote.sign_by_deadline && <span>Reads as a sign-by deadline</span>}
        {quote.reminder_count > 0 && (
          <span>
            {quote.reminder_count} reminder(s) due now
          </span>
        )}
        <span>
          Sends: <Mono>{quote.send_count ?? 0}</Mono>
        </span>
      </div>
    </button>
  )
}

/**
 * The tone a state is drawn in.
 *
 * Deliberately a local map rather than a lookup in the served vocabulary. The badge always
 * renders the state as text, so the tone is decoration only, and a state this build has
 * not seen falls through to `neutral` rather than to a colour that implies something.
 */
function toneFor(state) {
  if (state === 'expired') return 'restore'
  if (state === 'accepted' || state === 'signed') return 'insert'
  if (state === 'voided' || state === 'archived') return 'delete'
  if (state === 'draft') return 'neutral'
  return 'update'
}

/**
 * One reminder ledger row: what was decided, why, and which recipients it covered.
 *
 * A skip is rendered exactly like a send, with its reason. The suppression rules are the
 * ones hardest to verify, and a skip nobody can see is a skip nobody can check.
 */
export function LedgerRow({ row }) {
  return (
    <div className="flex flex-col gap-1 border-t border-border-subtle py-3 first:border-t-0">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-foreground">{row.rule_label || row.rule_id}</p>
        <Badge tone={row.outcome === 'sent' ? 'insert' : 'neutral'}>{row.outcome}</Badge>
      </div>
      <p className="text-xs text-muted-foreground">{row.detail}</p>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <span>
          Offset: <Mono>{row.offset_kind}</Mono> <Mono>{row.offset_days}</Mono> day(s)
        </span>
        <span>
          Recipients: <Mono>{(row.recipients || []).join(', ') || 'none recorded'}</Mono>
        </span>
        <span>Delivered by this product: no</span>
      </div>
      {row.reason && row.reason !== 'window_open' && (
        <p className="text-xs text-muted-foreground">
          Reason: <Mono>{row.reason}</Mono>
        </p>
      )}
    </div>
  )
}

/**
 * The empty state for a room with no quotes yet.
 *
 * `EmptyState` is shared. What is added here is the sentence that says what will happen
 * once there is one, because an empty page that only says "nothing here" leaves the
 * reader guessing whether the workflow is broken.
 */
export function NoQuotes({ onCreate, action }) {
  return (
    <EmptyState
      title="No quotes are being tracked in this room yet"
      description="Track a quote and give it an expiration date. A quote with no date and no account default never expires, and one whose Expiration date switch is off never expires either."
      action={onCreate ? action : undefined}
    />
  )
}

/**
 * A row in the decision register: the question, the reading taken, and the alternative
 * that was rejected.
 *
 * Rendered verbatim so a reviewer reads the list rather than reconstructing it from the
 * diff. The rejected alternative is not collapsed by default because a derivation with no
 * rejected alternative is a guess wearing a derivation's clothes.
 */
export function DecisionCard({ decision }) {
  return (
    <div className="rounded-sm border border-border-subtle bg-surface p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-semibold text-foreground">{decision.question}</p>
        <Mono>{decision.id}</Mono>
      </div>
      <p className="mt-2 text-xs text-muted-foreground">{decision.evidence}</p>
      <dl className="mt-3 space-y-2 text-sm">
        <div>
          <dt className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
            Chosen
          </dt>
          <dd className="text-foreground">{decision.chosen}</dd>
        </div>
        <div>
          <dt className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
            Rejected
          </dt>
          <dd className="text-muted-foreground">{decision.rejected}</dd>
        </div>
        <div>
          <dt className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
            What it costs
          </dt>
          <dd className="text-muted-foreground">{decision.consequence}</dd>
        </div>
      </dl>
      {decision.unsourced && (
        <p className="mt-3 text-xs text-warning">
          The research left this open. The reading above is this build's, not a sourced
          requirement.
        </p>
      )}
    </div>
  )
}

/**
 * A reminder rule row: which of the two offsets it counts from, how many days, whether it
 * is on, and the instant it fires in the account's own time zone.
 *
 * The vendor's own label is shown beside the stored id, because "Days after sending quote"
 * is what a seller recognises and `after_send` is what the store holds.
 */
export function RuleRow({ rule, dueLabel, onPatch, onDelete, onPreview, busy }) {
  return (
    <div className="flex flex-col gap-2 border-t border-border-subtle py-3 first:border-t-0">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-foreground">{rule.label}</p>
        <Badge tone={rule.enabled ? 'insert' : 'neutral'}>
          {rule.enabled ? 'On' : 'Off'}
        </Badge>
      </div>
      <p className="text-xs text-muted-foreground">
        {rule.offset_quote}, {rule.days} day(s). Fires {dueLabel}.
      </p>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => onPatch(rule.id, { enabled: !rule.enabled })}
          disabled={busy}
          className="inline-flex min-h-11 items-center gap-2 rounded-sm border border-border-subtle
            bg-surface px-3 text-sm text-foreground hover:border-accent hover:text-accent
            disabled:cursor-not-allowed disabled:opacity-50"
        >
          <Icon name="refresh" />
          {rule.enabled ? 'Turn off' : 'Turn on'}
        </button>
        <button
          type="button"
          onClick={() => onPreview(rule.id)}
          disabled={busy}
          className="inline-flex min-h-11 items-center gap-2 rounded-sm border border-border-subtle
            bg-surface px-3 text-sm text-foreground hover:border-accent hover:text-accent
            disabled:cursor-not-allowed disabled:opacity-50"
        >
          <Icon name="search" />
          Preview reminder
        </button>
        <button
          type="button"
          onClick={() => onDelete(rule.id)}
          disabled={busy}
          className="inline-flex min-h-11 items-center gap-2 rounded-sm border border-destructive/30
            bg-destructive/10 px-3 text-sm text-destructive hover:bg-destructive/15
            disabled:cursor-not-allowed disabled:opacity-50"
        >
          <Icon name="trash" />
          Delete rule
        </button>
      </div>
    </div>
  )
}

/**
 * One activity row: the name the research quotes, the quote it belongs to, and its payload.
 *
 * `Quote expired` is written verbatim because that is the sentence the specification
 * quotes, and an integration written against the vendor's documentation searches for it.
 */
export function ActivityRow({ row }) {
  return (
    <div className="flex flex-col gap-1 border-t border-border-subtle py-3 first:border-t-0">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-foreground">{row.activity}</p>
        <Mono>{row.at}</Mono>
      </div>
      {row.quote_id && (
        <p className="text-xs text-muted-foreground">
          Quote: <Mono>{row.quote_id}</Mono>
        </p>
      )}
      {row.payload && Object.keys(row.payload).length > 0 && (
        <pre className="overflow-x-auto rounded-sm bg-muted p-2 font-mono text-xs text-foreground">
          {JSON.stringify(row.payload, null, 2)}
        </pre>
      )}
    </div>
  )
}

/**
 * A card wrapper with a heading, for the sections this page needs.
 */
export function Section({ title, hint, action, children, className = '' }) {
  return (
    <Card className={className}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <SectionHeading title={title} hint={hint} />
        {action}
      </div>
      <div className="mt-4">{children}</div>
    </Card>
  )
}