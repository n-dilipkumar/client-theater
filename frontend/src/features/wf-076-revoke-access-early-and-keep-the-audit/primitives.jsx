/**
 * Small local primitives for the revocation page (WF-076).
 *
 * `ui.jsx` carries `Button`, `Card`, `Badge`, `Field`, `inputClass`, `Spinner`,
 * `ErrorNote`, `EmptyState`, `Icon`, `JsonView` and `useAsync`. What it does not
 * carry is a label/value pair - and this page is mostly label/value pairs,
 * because the researched output *is* an audit trail. Rather than four hundred
 * features each growing their own `Fact`, these three live in this feature's
 * folder; the integrator promotes them into `ui.jsx` as platform work, once.
 *
 * These are additive. Nothing here replaces a shared primitive.
 */

import { Button, Icon } from '@/components/ui'

/** One label and one value. Mono for machine values, body for anything a person reads. */
export function Fact({ label, value, mono = false, tone = null }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] tracking-[0.14em] text-muted-foreground uppercase">{label}</dt>
      <dd
        className={`mt-0.5 text-[13px] break-words text-foreground ${
          tone === 'destructive' ? 'text-destructive' : ''
        } ${mono ? 'font-mono' : ''}`}
      >
        {value === undefined || value === null || value === '' ? '—' : value}
      </dd>
    </div>
  )
}

/**
 * A notice in one of the three states the design system has tokens for.
 *
 * `tone` is `success`, `warning` or `destructive`, so a reviewer is never left
 * guessing whether a message is informational or an error.
 */
export function Note({ tone = 'info', title, children }) {
  const toneClass = {
    success: 'border-success/30 bg-success/5',
    warning: 'border-warning/30 bg-warning/5',
    destructive: 'border-destructive/30 bg-destructive/5',
    info: 'border-border-subtle bg-muted/40',
  }[tone]
  const titleClass = {
    success: 'text-success',
    warning: 'text-warning',
    destructive: 'text-destructive',
    info: 'text-foreground',
  }[tone]

  return (
    <div className={`rounded-sm border px-3 py-2 text-[13px] ${toneClass}`}>
      {title && <p className={`mb-0.5 font-medium ${titleClass}`}>{title}</p>}
      <div className="text-foreground/90">{children}</div>
    </div>
  )
}

/**
 * A control that confirms a destructive act by echoing the id it destroys.
 *
 * The backend refuses both irreversible routes unless `confirm` equals the
 * target id, so this gate is not decoration: it is the only thing that makes
 * those two calls reachable. A dialog whose "yes" button hard-codes `true` would
 * satisfy neither.
 */
export function ConfirmPanel({ label, target, onConfirm, busy, disabled, glyph, danger = true }) {
  return (
    <div className="rounded-sm border border-border-subtle bg-background/60 p-3">
      <p className="text-[13px] text-foreground">{label}</p>
      <p className="mt-1 font-mono text-xs break-all text-muted-foreground">{target}</p>
      <Button
        variant={danger ? 'danger' : 'secondary'}
        className="mt-2"
        disabled={disabled || busy}
        onClick={onConfirm}
      >
        {glyph && <Icon path={glyph} />}
        {busy ? 'Working…' : 'Confirm'}
      </Button>
    </div>
  )
}

/** A fixed-width definition-list grid, collapsing to one column under `sm`. */
export function Facts({ children, columns = 3 }) {
  const grid = { 2: 'sm:grid-cols-2', 3: 'sm:grid-cols-3', 4: 'sm:grid-cols-2 lg:grid-cols-4' }[
    columns
  ]
  return <dl className={`grid gap-3 ${grid}`}>{children}</dl>
}