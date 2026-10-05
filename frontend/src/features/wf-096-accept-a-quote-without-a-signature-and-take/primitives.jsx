/**
 * Primitives drawn from the shared components in `@/components/ui`.
 *
 * Nothing here redefines a control `components/ui.jsx` already provides. `Button`, `Card`,
 * `Badge`, `Field`, `inputClass`, `Icon` and `EmptyState` all come from the shared module, and
 * only the arrangements this page needs are built here:
 *
 * * `Dialog`, because `components/ui.jsx` exports no modal on this branch. The feature contract
 *   says to build a primitive that genuinely does not exist inside the feature folder and say so
 *   in the pull request, rather than editing the shared file. It meets the same accessibility
 *   floor as the rest of the product: `role="dialog"` with `aria-modal`, a visible title, Escape
 *   closes, and the backdrop is a button so it is keyboard reachable.
 * * `PaymentBadge`, because the shared `Badge` carries audit-action tones and this workflow needs
 *   a payment tone. The state word is always inside the badge, so no state is colour alone.
 * * `AcceptanceRail`, because the research puts the accepted state on a three-step chain
 *   (published -> accepted -> paid) and three equal badges would not say where a quote is.
 * * The row primitives (`InvoiceRow`, `ChargeRow`, `ActivityRow`), which carry the fields each
 *   of this workflow's own rows has.
 */

import { Badge, Button, Card, Field, Icon, inputClass } from '@/components/ui'

import {
  FACTS,
  RAIL,
  formatDate,
  formatInstant,
  formatMoney,
  railStep,
} from './api'

/**
 * A dialog, built here rather than imported.
 *
 * `components/ui.jsx` does not export a modal on this branch, and this feature may not edit it.
 */
export function Dialog({ open, title, description, onClose, children }) {
  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-foreground/40 p-4 sm:p-6">
      <button
        type="button"
        aria-label="Close the dialog"
        onClick={onClose}
        className="fixed inset-0 h-full w-full cursor-default"
        tabIndex={-1}
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onKeyDown={(event) => {
          if (event.key === 'Escape') onClose()
        }}
        className="relative z-10 w-full max-w-lg rounded-sm border border-border-subtle bg-surface p-5"
      >
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="text-lg font-semibold text-foreground">{title}</h2>
            {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-sm border border-border-subtle bg-surface text-foreground hover:border-accent hover:text-accent"
          >
            <Icon name="close" />
          </button>
        </div>
        <div className="mt-4">{children}</div>
      </div>
    </div>
  )
}

/** A payment state, with the state word always beside the colour. */
export function PaymentBadge({ state, tone = 'neutral' }) {
  return <Badge tone={tone}>{state}</Badge>
}

/**
 * The three-step acceptance-to-payment chain, with the current step marked in text and not only
 * in colour: a completed step reads "Done", the current step "Now", a later step "Waiting".
 */
export function AcceptanceRail({ detail }) {
  const current = railStep(detail)
  return (
    <ol className="flex flex-wrap items-center gap-x-2 gap-y-2">
      {RAIL.map((step, index) => {
        const done = current > index
        const now = current === index
        return (
          <li key={step.key} className="flex items-center gap-2">
            <span
              aria-current={now ? 'step' : undefined}
              className={`inline-flex items-center gap-2 rounded-sm border px-2 py-1 ${
                done || now
                  ? 'border-accent bg-accent-soft text-accent'
                  : 'border-border-subtle bg-muted text-muted-foreground'
              }`}
            >
              <span className="font-mono text-xs">{index + 1}</span>
              <span className="text-xs font-medium">{step.label}</span>
              <span className="text-xs">{done ? 'Done' : now ? 'Now' : 'Waiting'}</span>
            </span>
            {index < RAIL.length - 1 ? (
              <Icon name="chevron" size={14} className="text-muted-foreground" />
            ) : null}
          </li>
        )
      })}
    </ol>
  )
}

/**
 * One invoice. The first invoice is `sent` immediately whatever its scheduled date; each later
 * one is `scheduled` and carries the ten-day send lead as a concrete date.
 */
export function InvoiceRow({ invoice }) {
  const first = invoice.kind === 'first'
  return (
    <li className="flex flex-wrap items-start justify-between gap-3 border-b border-border-subtle py-3 last:border-b-0">
      <div className="min-w-0">
        <p className="flex flex-wrap items-center gap-2 text-sm font-medium text-foreground">
          <span className="font-mono text-xs text-muted-foreground">{invoice.number}</span>
          {first ? <Badge tone="insert">First invoice</Badge> : <Badge>Scheduled</Badge>}
          {first ? (
            <span className="text-xs text-muted-foreground">Sent {formatInstant(invoice.sent_at)}</span>
          ) : (
            <span className="text-xs text-muted-foreground">
              Sends {formatDate(invoice.send_on)}
            </span>
          )}
        </p>
        <p className="mt-1 text-xs text-muted-foreground">
          Invoice date {formatDate(invoice.invoice_date)}
          <span className="mx-2 text-border-subtle">|</span>
          {invoice.billing_frequency || 'one time'}
        </p>
      </div>
      <p className="shrink-0 font-mono text-sm font-semibold text-foreground">
        {formatMoney(invoice.amount, invoice.currency)}
      </p>
    </li>
  )
}

/**
 * One charge attempt. A declined charge is a recorded outcome with a named reason, not an error,
 * so it renders exactly like a recorded one and differs only in its badge and its reason.
 */
export function ChargeRow({ charge }) {
  const recorded = charge.outcome === 'recorded'
  return (
    <li className="flex flex-wrap items-start justify-between gap-3 border-b border-border-subtle py-3 last:border-b-0">
      <div className="min-w-0">
        <p className="flex flex-wrap items-center gap-2 text-sm font-medium text-foreground">
          {recorded ? <Badge tone="insert">Charge recorded</Badge> : <Badge tone="delete">Declined</Badge>}
          <span className="font-mono text-xs text-muted-foreground">{charge.payment_method}</span>
          <span className="text-xs text-muted-foreground">{charge.payment_type}</span>
        </p>
        <p className="mt-1 text-xs text-muted-foreground">
          {formatInstant(charge.initiated_at)}
          {recorded && charge.settled_at ? (
            <>
              <span className="mx-2 text-border-subtle">|</span>
              settled {formatInstant(charge.settled_at)}
            </>
          ) : null}
        </p>
        {charge.detail ? <p className="mt-1 text-xs text-foreground">{charge.detail}</p> : null}
      </div>
      <p className="shrink-0 font-mono text-sm font-semibold text-foreground">
        {formatMoney(charge.amount, charge.currency)}
      </p>
    </li>
  )
}

/** One activity row, labelled with this workflow's own vocabulary. */
export function ActivityRow({ entry }) {
  return (
    <li className="flex flex-wrap items-start justify-between gap-3 border-b border-border-subtle py-2 last:border-b-0">
      <div className="min-w-0">
        <p className="text-sm text-foreground">
          <span className="font-medium">{entry.label || entry.activity}</span>
          {entry.detail ? <span className="text-muted-foreground"> — {entry.detail}</span> : null}
        </p>
      </div>
      <p className="shrink-0 font-mono text-xs text-muted-foreground">{formatInstant(entry.at)}</p>
    </li>
  )
}

/** A quoted sentence from the research, set so it reads as evidence and not as a headline. */
export function EvidenceNote({ quote, label = 'Evidence' }) {
  if (!quote) return null
  return (
    <figure className="rounded-sm border-l-2 border-accent bg-accent-soft/40 px-3 py-2">
      <figcaption className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
        {label}
      </figcaption>
      <blockquote className="mt-1 text-xs text-foreground">{quote}</blockquote>
    </figure>
  )
}

/** One configuration switch, bound to its label by id so it has an accessible name. */
export function ToggleRow({ id, label, hint, checked, onChange }) {
  return (
    <div className="flex items-start justify-between gap-3 rounded-sm border border-border-subtle p-3">
      <div className="min-w-0">
        <label htmlFor={id} className="text-sm font-medium text-foreground">
          {label}
        </label>
        {hint && <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>}
      </div>
      <input
        id={id}
        type="checkbox"
        role="switch"
        aria-label={label}
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="mt-1 h-6 w-11 shrink-0 cursor-pointer appearance-none rounded-sm border border-border-subtle bg-surface checked:border-accent checked:bg-accent focus:border-accent"
      />
    </div>
  )
}

/** The strict-minimum block, so the rule reads beside the number it governs. */
export function MinimumChargePanel({ summary, evidence }) {
  const minimum = summary?.minimum_charge_usd ?? FACTS.minimumChargeUsd
  return (
    <Card>
      <h3 className="text-base font-semibold text-foreground">The strict minimum</h3>
      <p className="mt-2 font-mono text-2xl font-semibold text-foreground">
        {formatMoney(minimum, 'USD')}
      </p>
      <p className="mt-1 text-sm text-muted-foreground">
        A charge at or below this is recorded as declined, not refused, and it never undoes an
        acceptance. {pluralMaybe(summary?.charges_declined)} declined so far.
      </p>
      <div className="mt-3 space-y-2">
        <EvidenceNote label="The rule" quote={evidence || summary?.minimum_charge_quote} />
        <EvidenceNote label="The first invoice" quote={summary?.first_invoice_quote} />
      </div>
    </Card>
  )
}

function pluralMaybe(count) {
  const value = count || 0
  return `${value} charge${value === 1 ? '' : 's'}`
}

/** The three-tax-ID cap, with the research sentence beside it. */
export function TaxIdPanel({ summary, evidence }) {
  const limit = summary?.tax_id_limit ?? FACTS.taxIdLimit
  return (
    <Card>
      <h3 className="text-base font-semibold text-foreground">Buyer tax IDs</h3>
      <p className="mt-2 font-mono text-2xl font-semibold text-foreground">{limit}</p>
      <p className="mt-1 text-sm text-muted-foreground">
        The fourth is refused with a published reason code. {summary?.tax_ids || 0} recorded across
        the room.
      </p>
      <div className="mt-3">
        <EvidenceNote label="The cap" quote={evidence} />
      </div>
    </Card>
  )
}

/** An evidence sentence from the server's vocabulary, or `null` while it loads. */
export function evidenceFor(vocabulary, key) {
  return vocabulary?.evidence?.[key] || ''
}

export { Field, inputClass }
