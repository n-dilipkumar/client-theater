/**
 * WF-101's own primitives.
 *
 * Everything here is built from `@/components/ui`, `ui.jsx` has no equivalent,
 * and every control still meets the accessibility floor: `min-h-11`, a visible
 * label beside every icon, and no status carried by colour alone.
 *
 * Three are local because the shared set has no equivalent, which the contract
 * names as the reason to build them here rather than edit `ui.jsx`:
 *
 *   - `PriceLockBadge`, because "Prices Locked" is the field this whole workflow
 *     is about and it needs its Yes/No word beside a colour.
 *   - `StatusBadge`, for the same reason on the order status.
 *   - `LineItemsTable`, because a seller has to read the order's priced lines.
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

/** The Prices Locked field, as a word plus a colour. */
export function PriceLockBadge({ locked }) {
  return (
    <Badge tone={locked ? 'update' : 'insert'}>
      {locked ? 'Prices Locked: Yes' : 'Prices Locked: No'}
    </Badge>
  )
}

/** An order status as a word plus a colour. */
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

/** The order's priced line items, as a small table. */
export function LineItemsTable({ lines, locked }) {
  if (!lines || lines.length === 0) {
    return <p className="text-xs text-muted-foreground">No line items.</p>
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <caption className="sr-only">Line items on the order</caption>
        <thead>
          <tr className="border-b border-border-subtle text-left">
            <th scope="col" className="py-2 pr-4 text-xs font-medium text-muted-foreground">
              Item
            </th>
            <th scope="col" className="py-2 pr-4 text-xs font-medium text-muted-foreground">
              SKU
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
                {line.price_overridden && (
                  <span className="ml-2 text-xs text-muted-foreground">overridden</span>
                )}
                {line.write_in && <span className="ml-2 text-xs text-muted-foreground">write-in</span>}
              </td>
              <td className="py-2 pr-4 font-mono text-xs text-muted-foreground">
                {line.sku || 'n/a'}
              </td>
              <td className="py-2 pr-4 text-right font-mono text-xs text-foreground">
                {line.quantity}
              </td>
              <td className="py-2 pr-4 text-right font-mono text-xs text-foreground">
                {Number(line.unit_price || 0).toFixed(2)}
                {locked && <span className="ml-1 text-muted-foreground">locked</span>}
              </td>
              <td className="py-2 text-right font-mono text-xs text-foreground">
                {Number(line.amounts?.total || 0).toFixed(2)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
