/**
 * Glyphs and the small primitives this feature needs, built here rather than
 * added to `components/ui.jsx`.
 *
 * `components/ui.jsx` is shared and a hundred features each appending to it is
 * the collision the feature host exists to prevent, so the glyphs are passed as
 * paths through `<Icon path=...>` and the titled section, the definition-list
 * fact and the verdict badge are built in this folder. They repeat patterns
 * already on main, which is itself the finding for the integrator: a recurring
 * shape is platform work, promoted into `ui.jsx` once.
 *
 * The floor they meet, from the design system: a 44px minimum touch target, the
 * global focus ring left intact, a text label beside every glyph so meaning
 * survives with icons off, no emoji, and no motion beyond the global
 * `prefers-reduced-motion` rule. Every badge below carries its verdict as text,
 * so no state is conveyed by colour alone.
 */

import { Icon } from '@/components/ui'

/**
 * "A funnel narrowing to a name tag" - the nav glyph for qualifying a lead
 * without offering a calendar. Not in the shared PATHS map, and that file is not
 * ours to edit, so it is passed as a path. `icon` in the descriptor carries the
 * closest shared name as a fallback.
 */
export const QUALIFY_ICON =
  'M4 5h16l-6 7v6l-4 2v-8L4 5zm9 15h3v3h-3v-3z'

/** The researched call itself: a lead arriving at a decision point. */
export const ROUTE_ICON = 'M12 3l9 5-9 5-9-5 9-5zm-7 9l7 4 7-4M5 16l7 4 7-4'

/** The gate: schedulingAllowed, the signal the research tells a caller to gate on. */
export const GATE_ICON = 'M12 2l8 4v6c0 5-3.5 8.5-8 10-4.5-1.5-8-5-8-10V6l8-4zm-3.5 9.5l2.5 2.5 4.5-5'

const ICONS = { qualify: QUALIFY_ICON, route: ROUTE_ICON, gate: GATE_ICON }

/** Draw one of this feature's glyphs, falling through to the shared set. */
export function Glyph({ name, size = 18, className = '' }) {
  return <Icon name={name} path={ICONS[name]} size={size} className={className} />
}

/** A titled block, with a hint underneath that says why it reads the way it does. */
export function Section({ title, hint, action, children }) {
  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="font-display text-lg font-semibold text-foreground">{title}</h2>
          {hint && <p className="mt-1 max-w-3xl text-xs text-muted-foreground">{hint}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  )
}

/** A definition-list row. The term is always text, never a glyph on its own. */
export function Fact({ term, children, mono = false }) {
  return (
    <div>
      <dt className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">{term}</dt>
      <dd className={`mt-0.5 text-[13px] text-foreground ${mono ? 'font-mono' : ''}`}>{children}</dd>
    </div>
  )
}

/**
 * The three verdicts, as text first and colour second.
 *
 * The label is the verdict name itself, so the state survives with colour off and
 * in a screenshot printed in black and white.
 */
const VERDICT_TONES = {
  qualified: 'border-accent/30 bg-accent/10 text-accent',
  not_scheduled: 'border-warning/30 bg-warning/10 text-warning',
  unroutable: 'border-destructive/30 bg-destructive/10 text-destructive',
}

const VERDICT_LABELS = {
  qualified: 'Qualified',
  not_scheduled: 'Not scheduled',
  unroutable: 'Unroutable',
}

export function VerdictBadge({ verdict }) {
  const tone = VERDICT_TONES[verdict] || VERDICT_TONES.unroutable
  return (
    <span
      className={`inline-flex items-center rounded-xs border px-2 py-0.5 font-mono text-xs font-medium ${tone}`}
    >
      {VERDICT_LABELS[verdict] || 'Unknown'}
    </span>
  )
}

/** The gate signal, spelled out rather than shown as a bare boolean. */
export function SchedulingBadge({ allowed }) {
  return (
    <span
      className={`inline-flex items-center rounded-xs border px-2 py-0.5 font-mono text-xs font-medium ${
        allowed
          ? 'border-accent/30 bg-accent/10 text-accent'
          : 'border-border-subtle bg-muted text-muted-foreground'
      }`}
    >
      schedulingAllowed: {allowed ? 'true' : 'false'}
    </span>
  )
}

/** An inline notice for a message the reader needs to act on. */
export function Notice({ tone = 'info', title, children }) {
  const tones = {
    info: 'border-info/30 bg-info/10 text-foreground',
    success: 'border-accent/30 bg-accent/10 text-foreground',
    warning: 'border-warning/30 bg-warning/10 text-foreground',
    danger: 'border-destructive/30 bg-destructive/10 text-foreground',
  }
  const role = tone === 'danger' || tone === 'warning' ? 'alert' : 'status'
  return (
    <div role={role} className={`rounded-sm border p-4 ${tones[tone] || tones.info}`}>
      {title && <p className="text-sm font-semibold text-foreground">{title}</p>}
      <div className="mt-0.5 text-sm text-muted-foreground">{children}</div>
    </div>
  )
}

/** A table that says so when it is empty. */
export function DataTable({ columns, rows, rowKey, empty }) {
  if (!rows.length) {
    return <p className="py-6 text-center text-sm text-muted-foreground">{empty}</p>
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[36rem] border-collapse text-left">
        <thead>
          <tr className="border-b border-border-subtle">
            {columns.map((column) => (
              <th
                key={column.key}
                scope="col"
                className="py-2 pr-4 text-[11px] uppercase tracking-[0.14em] text-muted-foreground"
              >
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={rowKey(row)} className="border-b border-border-subtle/60 align-top">
              {columns.map((column) => (
                <td key={column.key} className="py-2.5 pr-4 text-[13px] text-foreground">
                  {column.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
