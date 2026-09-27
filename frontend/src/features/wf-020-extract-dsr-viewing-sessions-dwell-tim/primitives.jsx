/**
 * Small presentational pieces this feature needs, built here rather than added
 * to `components/ui.jsx`.
 *
 * `components/ui.jsx` is shared and is not edited: a hundred features each adding
 * a primitive to it is the conflict the feature host exists to prevent. These are
 * deliberately narrow. A table, a flag chip and a number formatter are needed by
 * every data page; when a second feature needs one of them, the integrator
 * promotes it once, as platform work.
 *
 * Design floor, from design-system/digital-sales-room/MASTER.md: no emoji as
 * icons, text beside every glyph, 44px touch targets, visible focus, and motion
 * limited to colour transitions the global stylesheet already reduces under
 * prefers-reduced-motion.
 */

import { Badge, Icon } from '@/components/ui'
import { FLAG } from './icons'

/** A table with a sticky header, horizontal scroll on small screens. */
export function DataTable({ columns, rows, empty, rowKey }) {
  if (!rows.length) {
    return <p className="px-1 py-6 text-sm text-muted-foreground">{empty}</p>
  }
  return (
    <div className="-mx-1 overflow-x-auto">
      <table className="w-full min-w-max border-collapse text-left text-sm">
        <thead>
          <tr className="border-b border-border-subtle/40">
            {columns.map((column) => (
              <th
                key={column.key}
                scope="col"
                className="px-3 py-2 text-xs font-medium tracking-wide text-muted-foreground uppercase"
              >
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr
              key={rowKey ? rowKey(row) : index}
              className="border-b border-border-subtle/15 transition-colors duration-150 hover:bg-muted/30"
            >
              {columns.map((column) => (
                <td key={column.key} className="px-3 py-2 align-middle">
                  {column.render ? column.render(row) : row[column.key]}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/**
 * The quality flags on a row.
 *
 * A flag is never shown without its name, and the whole list is always rendered:
 * a chip that hides flags until hovered is how a "clean" number gets believed.
 */
export function FlagList({ flags }) {
  if (!flags || flags.length === 0) {
    return <span className="text-xs text-muted-foreground/70">clean</span>
  }
  return (
    <ul className="flex flex-wrap items-center gap-1.5">
      {flags.map((flag) => (
        <li key={flag}>
          <span className="inline-flex items-center gap-1 rounded-md border border-amber-500/30 bg-amber-500/10 px-1.5 py-0.5 font-mono text-[11px] text-amber-300">
            <Icon path={FLAG} size={11} />
            {flag}
          </span>
        </li>
      ))}
    </ul>
  )
}

/** Internal / external / unknown, the split the flow says BI computes. */
export function AudienceBadge({ internal }) {
  if (internal === true) return <Badge tone="update">internal</Badge>
  if (internal === false) return <Badge>external</Badge>
  return <Badge tone="neutral">unlabelled</Badge>
}

/** Seconds, as a human reads dwell: 0, 45s, 12m, 1h 10m. */
export function dwell(seconds) {
  if (seconds === null || seconds === undefined) return '—'
  const total = Math.max(0, Math.round(Number(seconds) || 0))
  if (total < 60) return `${total}s`
  if (total < 3600) return `${Math.round(total / 60)}m`
  const hours = Math.floor(total / 3600)
  const minutes = Math.round((total % 3600) / 60)
  return minutes ? `${hours}h ${minutes}m` : `${hours}h`
}

/** A definition row for a term whose meaning is not obvious from its value. */
export function Fact({ term, children }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-xs tracking-wide text-muted-foreground uppercase">{term}</dt>
      <dd className="font-mono text-sm text-foreground">{children}</dd>
    </div>
  )
}

/** A labelled section, so the page reads as a sequence rather than a wall. */
export function Section({ title, hint, action, children }) {
  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h2 className="font-mono text-sm font-semibold text-foreground">{title}</h2>
          {hint && <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  )
}
