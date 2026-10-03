/**
 * Glyphs and the small primitives this feature needs, built here rather than
 * added to `components/ui.jsx`.
 *
 * `components/ui.jsx` is shared and a hundred features each appending to it is
 * the collision the feature host exists to prevent, so the glyphs are passed as
 * paths through `<Icon path=...>` and the titled section, the definition-list
 * fact, the inline notice and the empty-aware table are built in this folder.
 * They repeat patterns already on main, which is itself the finding for the
 * integrator: a recurring shape is platform work, promoted into `ui.jsx` once.
 *
 * The floor they meet, from the design system: a 44px minimum touch target, a
 * visible focus ring, a text label beside every glyph so meaning survives with
 * icons off, no emoji, and no motion beyond the global `prefers-reduced-motion`
 * rule.
 */

import { Icon } from '@/components/ui'

/**
 * "A plug mid-way into a socket, with a check beside it" - the nav glyph for
 * sandbox validation. Not in the shared PATHS map, and that file is not ours
 * to edit, so it is passed as a path. `icon` in the descriptor carries the
 * closest shared name as a fallback.
 */
export const VALIDATE_ICON =
  'M7 3v6a5 5 0 0010 0V3h-2v6a3 3 0 01-6 0V3H7zm5 13v6m-4-2h8m-9-9h10M12 12a2 2 0 110-4 2 2 0 010 4z'

/** A run in flight, or one that stopped before it could prove anything. */
export const PROBE_ICON =
  'M12 4a8 8 0 018 8h-2a6 6 0 00-6-6V4zm0 4a4 4 0 014 4h-2a2 2 0 00-2-2V8zM4 12a8 8 0 018-8v2a6 6 0 006 6h2a8 8 0 01-8 8v-2a6 6 0 006-6H4z'

/** The gate: two posts and a bar that only lifts on green. */
export const GATE_ICON =
  'M5 21V7l7-4 7 4v14h-6v-6H11v6H5zm4-10a2 2 0 104 0 2 2 0 00-4 0z'

const ICONS = { validate: VALIDATE_ICON, probe: PROBE_ICON, gate: GATE_ICON }

/** Draw one of this feature's glyphs, falling through to the shared set. */
export function Glyph({ name, size = 18, className = '' }) {
  return <Icon name={name} path={ICONS[name]} size={size} className={className} />
}

const BUTTON_VARIANTS = {
  primary: 'bg-accent font-semibold text-on-accent hover:brightness-110',
  secondary: 'bg-muted text-foreground hover:bg-border-subtle',
  danger: 'bg-destructive/15 text-destructive hover:bg-destructive/25',
}

/**
 * A shared `Button` carrying a glyph this feature owns.
 *
 * The shared `Button` resolves its `icon` only through the shared `PATHS` map,
 * which is a file this feature may not edit, so the glyph is injected as a
 * child instead. `variant` and `className` are destructured out of `props`
 * rather than read off it: spreading them onto a DOM `<button>` would put an
 * unknown attribute on the element and overwrite the computed class.
 */
export function PathButton({ glyph, variant = 'secondary', className = '', children, ...props }) {
  return (
    <button
      type="button"
      className={`inline-flex min-h-11 cursor-pointer items-center gap-2 rounded-lg px-4 text-sm
        transition-colors duration-200 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none
        disabled:cursor-not-allowed disabled:opacity-50 ${BUTTON_VARIANTS[variant] || BUTTON_VARIANTS.secondary}
        ${className}`}
      {...props}
    >
      {glyph && <Glyph name={glyph} />}
      {children}
    </button>
  )
}

/** An inline notice for a message the user needs to act on. */
export function Notice({ tone = 'info', title, children }) {
  const tones = {
    info: 'border-sky-500/40 bg-sky-500/10 text-sky-200',
    success: 'border-accent/40 bg-accent/10 text-accent',
    warning: 'border-amber-500/40 bg-amber-500/10 text-amber-200',
    danger: 'border-destructive/40 bg-destructive/10 text-destructive',
  }
  const role = tone === 'danger' || tone === 'warning' ? 'alert' : 'status'
  return (
    <div role={role} className={`rounded-lg border p-4 ${tones[tone] || tones.info}`}>
      {title && <p className="text-sm font-semibold">{title}</p>}
      <div className="mt-0.5 text-sm text-muted-foreground">{children}</div>
    </div>
  )
}

/** A titled block, with a hint underneath that says why it reads the way it does. */
export function Section({ title, hint, action, children }) {
  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="font-mono text-sm font-semibold text-foreground">{title}</h2>
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
      <dt className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{term}</dt>
      <dd className={`mt-0.5 text-[13px] text-foreground ${mono ? 'font-mono' : ''}`}>{children}</dd>
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
      <table className="w-full min-w-[40rem] border-collapse text-left">
        <thead>
          <tr className="border-b border-border-subtle/40">
            {columns.map((column) => (
              <th
                key={column.key}
                scope="col"
                className="py-2 pr-4 text-xs font-medium tracking-wide text-muted-foreground uppercase"
              >
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={rowKey(row)} className="border-b border-border-subtle/20 align-top">
              {columns.map((column) => (
                <td key={column.key} className="py-2.5 pr-4">
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
