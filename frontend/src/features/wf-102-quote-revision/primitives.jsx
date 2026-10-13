/**
 * WF-102's own primitives.
 *
 * Everything here is built from `@/components/ui`, `ui.jsx` has no equivalent,
 * and every control still meets the accessibility floor: a visible text label
 * beside every value, and no status carried by colour alone.
 *
 * Three are local because the shared set has no equivalent, which the contract
 * names as the reason to build them here rather than edit `ui.jsx`:
 *
 *   - `StatusBadge`, because a revision status needs a word beside its tone.
 *   - `RevisionChain`, because a revision is a lineage and a seller has to read
 *     the whole history, not one row.
 *   - `LineItemsTable`, because a revision's copied prices are the point.
 */

import { Badge } from '@/components/ui'

import { statusLabel, statusTone } from './api'

/** An inline note whose meaning never rests on colour alone. */
export function InlineNote({ tone = 'neutral', children }) {
  const tones = {
    neutral: 'border-border-subtle bg-muted text-muted-foreground',
    warning: 'border-warning/40 bg-warning/10 text-warning',
    info: 'border-info/40 bg-info/10 text-info',
    success: 'border-success/40 bg-success/10 text-success',
    destructive: 'border-destructive/40 bg-destructive/10 text-destructive',
  }
  return (
    <p
      className={`rounded-sm border px-3 py-2 text-xs ${tones[tone] || tones.neutral}`}
      role="status"
      aria-live="polite"
    >
      {children}
    </p>
  )
}

/** A labelled cell whose value is rendered as children. */
export function Fact({ label, hint, children }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
        {label}
      </p>
      <div className="min-w-0 truncate text-sm text-foreground">{children}</div>
      {hint && <p className="truncate text-xs text-muted-foreground">{hint}</p>}
    </div>
  )
}

/** A revision status as a word plus a tone. */
export function StatusBadge({ status }) {
  return <Badge tone={statusTone(status)}>{statusLabel(status)}</Badge>
}

/** A currency amount, formatted, or a spelled-out absence. */
export function Money({ amount, currency }) {
  if (amount === undefined || amount === null || amount === '') {
    return <span className="font-mono text-sm text-muted-foreground">Not set</span>
  }
  return (
    <span className="font-mono text-sm text-foreground">
      {Number(amount).toFixed(2)}
      {currency ? ` ${currency}` : ''}
    </span>
  )
}

/** The revision lineage, oldest first, as a table of versions. */
export function RevisionChain({ revisions }) {
  if (!revisions || revisions.length === 0) {
    return <p className="text-xs text-muted-foreground">No recorded revisions yet.</p>
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <caption className="sr-only">The revision lineage, oldest first</caption>
        <thead>
          <tr className="border-b border-border-subtle text-left">
            <th scope="col" className="py-2 pr-4 text-xs font-medium text-muted-foreground">
              Revision
            </th>
            <th scope="col" className="py-2 pr-4 text-xs font-medium text-muted-foreground">
              Status
            </th>
            <th scope="col" className="py-2 pr-4 text-xs font-medium text-muted-foreground">
              Closed on
            </th>
            <th scope="col" className="py-2 text-left text-xs font-medium text-muted-foreground">
              Quote id
            </th>
          </tr>
        </thead>
        <tbody>
          {revisions.map((step) => (
            <tr key={step.id} className="border-b border-border-subtle/60">
              <td className="py-2 pr-4 font-mono text-xs text-foreground">{step.revision_number}</td>
              <td className="py-2 pr-4">
                <StatusBadge status={step.status} />
              </td>
              <td className="py-2 pr-4 font-mono text-xs text-muted-foreground">
                {step.closed_on || 'Open'}
              </td>
              <td className="py-2 font-mono text-xs text-muted-foreground">{step.id}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** The line items of a quote, as a small table. */
export function LineItemsTable({ lines }) {
  if (!lines || lines.length === 0) {
    return <p className="text-xs text-muted-foreground">No line items on this quote.</p>
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <caption className="sr-only">The line items on this quote</caption>
        <thead>
          <tr className="border-b border-border-subtle text-left">
            <th scope="col" className="py-2 pr-4 text-xs font-medium text-muted-foreground">
              Item
            </th>
            <th scope="col" className="py-2 pr-4 text-right text-xs font-medium text-muted-foreground">
              Qty
            </th>
            <th scope="col" className="py-2 pr-4 text-right text-xs font-medium text-muted-foreground">
              Unit price
            </th>
            <th scope="col" className="py-2 text-right text-xs font-medium text-muted-foreground">
              Total
            </th>
          </tr>
        </thead>
        <tbody>
          {lines.map((line, index) => (
            <tr key={line.id || index} className="border-b border-border-subtle/60">
              <td className="py-2 pr-4 text-foreground">
                {line.name || 'Unnamed'}
                {line.price_source ? (
                  <span className="ml-2 text-xs text-muted-foreground">({line.price_source})</span>
                ) : null}
              </td>
              <td className="py-2 pr-4 text-right font-mono text-xs text-foreground">
                {line.quantity}
              </td>
              <td className="py-2 pr-4 text-right font-mono text-xs text-foreground">
                {Number(line.unit_price || 0).toFixed(2)}
              </td>
              <td className="py-2 text-right font-mono text-xs text-foreground">
                {Number((line.amounts || {}).total || 0).toFixed(2)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** The close, recall and re-publish trail, newest first. */
export function ActivityList({ entries }) {
  if (!entries || entries.length === 0) {
    return <p className="text-xs text-muted-foreground">No activity on this quote yet.</p>
  }
  return (
    <ul className="flex flex-col gap-2">
      {entries.map((entry, index) => (
        <li
          key={entry.id || index}
          className="flex flex-wrap items-center justify-between gap-2 border-b border-border-subtle/60 pb-2 last:border-b-0 last:pb-0"
        >
          <span className="text-sm text-foreground">{entry.label || entry.action}</span>
          <span className="font-mono text-xs text-muted-foreground">
            {entry.reason ? `${entry.reason} · ` : ''}
            r{entry.revision_number} · {entry.at}
          </span>
        </li>
      ))}
    </ul>
  )
}
