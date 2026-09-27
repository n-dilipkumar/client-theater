/**
 * Glyphs and the two primitives this feature needs, built here rather than added
 * to `components/ui.jsx`.
 *
 * `components/ui.jsx` is shared and a hundred features each appending to it is
 * the collision the feature host exists to prevent, so the glyphs are passed as
 * paths through `<Icon path=...>` and the progress bar and the inline notice are
 * built in this folder. Both are findings for the integrator: `Notice` in
 * particular is generic enough that the next workflow will want it too, and
 * promoting it into `ui.jsx` is a one-off platform change belonging to whoever
 * owns that file.
 *
 * The floor they meet, from the design system: a 44px minimum touch target, a
 * visible focus ring (inherited globally from `index.css`), a text label beside
 * every glyph so meaning survives with icons off, no emoji, and no motion beyond
 * the global `prefers-reduced-motion` rule.
 */

import { Icon } from '@/components/ui'

/**
 * A resumable cursor: a history with a marker partway through it, and a tick
 * where the last read stopped.
 *
 * The `ICON` shape of "a schedule you can pick up where you left off" is what the
 * nav wants, and it is not in the shared `PATHS` map, which is why this folder
 * defines it and `Icon` receives it as a path. `BACKFILL_ICON` is the fallback
 * for a consumer that reads only the shared name.
 */
export const BACKFILL_ICON =
  'M12 6a6 6 0 100 12 6 6 0 000-12zm0 3a3 3 0 110 6 3 3 0 010-6zM4 4a1 1 0 011 1v3a1 1 0 11-2 0V5a1 1 0 011-1zm16 0a1 1 0 011 1v3a1 1 0 11-2 0V5a1 1 0 011-1zM12 2a1 1 0 011 1v1a1 1 0 11-2 0V3a1 1 0 011-1zm0 18a1 1 0 011 1v1a1 1 0 11-2 0v-1a1 1 0 011-1zM3 12a1 1 0 011-1h1a1 1 0 110 2H4a1 1 0 01-1-1zm16 0a1 1 0 011-1h1a1 1 0 110 2h-1a1 1 0 01-1-1z'

/** A poller waiting on something it cannot hurry. */
export const POLL_ICON =
  'M12 4a8 8 0 018 8h-2a6 6 0 00-6-6V4zm0 4a4 4 0 014 4h-2a2 2 0 00-2-2V8zM4 12a8 8 0 018-8v2a6 6 0 006 6h2a8 8 0 01-8 8v-2a6 6 0 01-6-6H4z'

/** A day rolling over at local midnight, which is when a daily limit refills. */
export const CLOCK_ICON =
  'M12 3a9 9 0 100 18 9 9 0 000-18zm0 2a7 7 0 110 14 7 7 0 010-14zm-1 2v5.6l4 2.4-1 1.7-5-3V7h2z'

const ICONS = { backfill: BACKFILL_ICON, poll: POLL_ICON, clock: CLOCK_ICON }

/** Draw one of this feature's glyphs, falling through to the shared set. */
export function Glyph({ name, size = 18, className = '' }) {
  return <Icon name={name} path={ICONS[name]} size={size} className={className} />
}

const BUTTON_VARIANTS = {
  primary: 'bg-accent font-semibold text-on-accent hover:brightness-110',
  secondary: 'bg-muted text-foreground hover:bg-border-subtle',
}

/**
 * A shared `Button` carrying a glyph this feature owns.
 *
 * The shared `Button` resolves its `icon` only through the shared `PATHS` map,
 * which is a file this feature may not edit, so the glyph is injected as a child
 * instead. `variant` and `className` are destructured out of `props` rather than
 * read off it: spreading them onto a DOM `<button>` would put an unknown
 * attribute on the element and would overwrite the computed class with the
 * caller's, which is a button that silently lost its variant.
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

/**
 * The progress bar the research's step 6 asks for, and the honesty about it.
 *
 * `percent === null` renders a striped, unhurried track with the words
 * "no total reported yet" beside it, because a bar sitting at 0% for a run that
 * is halfway through a range is a lie told with a component. The width animates
 * over 300ms, inside the design system's motion budget and inside the global
 * `prefers-reduced-motion` rule that turns transitions off for people who asked
 * for that.
 */
export function ProgressBar({ percent, indeterminate, label, note }) {
  const width = percent === null || percent === undefined ? 0 : Math.max(0, Math.min(100, percent))
  return (
    <div>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
          Progress
        </span>
        <span className="font-mono text-sm text-foreground">
          {indeterminate ? 'no total yet' : `${width.toFixed(1)}%`}
        </span>
      </div>
      <div
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={indeterminate ? undefined : width}
        aria-valuetext={indeterminate ? 'Progress unknown: the vendor has not reported a total' : `${width.toFixed(1)} percent`}
        className="mt-2 h-2 w-full overflow-hidden rounded-full bg-muted"
      >
        <div
          className={`h-full rounded-full bg-accent transition-[width] duration-300 motion-reduce:transition-none ${
            indeterminate ? 'w-1/3 opacity-50' : ''
          }`}
          style={indeterminate ? undefined : { width: `${width}%` }}
        />
      </div>
      {note && <p className="mt-1.5 text-xs text-muted-foreground">{note}</p>}
    </div>
  )
}

/**
 * Inline notice for a message the user needs to act on.
 *
 * `tone` picks the semantic colour and `icon` the glyph. The title is always
 * text, never an icon on its own.
 */
export function Notice({ tone = 'info', title, children, icon, onDismiss }) {
  const tones = {
    info: 'border-sky-500/40 bg-sky-500/10 text-sky-200',
    success: 'border-accent/40 bg-accent/10 text-accent',
    warning: 'border-amber-500/40 bg-amber-500/10 text-amber-200',
    danger: 'border-destructive/40 bg-destructive/10 text-destructive',
  }
  // A warning or a failure is announced; a confirmation is not. Both would be
  // noise if every notice interrupted whatever the user was doing.
  const role = tone === 'danger' || tone === 'warning' ? 'alert' : 'status'
  return (
    <div role={role} className={`rounded-lg border p-4 ${tones[tone] || tones.info}`}>
      <div className="flex items-start gap-3">
        {icon && (
          <span className="mt-0.5 shrink-0">
            <Glyph name={icon} />
          </span>
        )}
        <div className="min-w-0 flex-1">
          {title && <p className="text-sm font-semibold">{title}</p>}
          <div className="mt-0.5 text-sm text-muted-foreground">{children}</div>
        </div>
        {onDismiss && (
          <button
            type="button"
            onClick={onDismiss}
            className="flex min-h-11 min-w-11 shrink-0 cursor-pointer items-center justify-center rounded-lg text-muted-foreground transition-colors duration-150 hover:bg-muted hover:text-foreground"
          >
            <span className="sr-only">Dismiss</span>
            <Icon name="close" size={16} />
          </button>
        )}
      </div>
    </div>
  )
}

/** A titled block, with a hint underneath it that says why it reads the way it does. */
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

/**
 * A table that says so when it is empty.
 *
 * A blank grid is the worst of both worlds: it looks broken and it says nothing.
 * Every list on this page renders through here so an empty state is always a
 * sentence explaining what would put something in it.
 */
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
