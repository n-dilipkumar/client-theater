/**
 * Glyphs and the two primitives this feature needs, built here rather than added
 * to `components/ui.jsx`.
 *
 * `components/ui.jsx` is shared and a hundred features each appending to it is the
 * collision the feature host exists to prevent, so the glyphs are passed as paths
 * through `<Icon path=...>` and the inline notice is built in this folder. The
 * notice is generic enough that the next workflow will want it too, and promoting
 * it into `ui.jsx` is a one-off platform change belonging to whoever owns that file.
 *
 * The floor they meet, from the design system: a 44px minimum touch target, a
 * visible focus ring (inherited globally from `index.css`), a text label beside
 * every glyph so meaning survives with icons off, no emoji, and no motion beyond
 * the global `prefers-reduced-motion` rule.
 */

import { Badge, Icon } from '@/components/ui'

/** A stream with one broken link in it: the reason this workflow exists. */
export const GAP_ICON =
  'M3 12h4m10 0h4M12 5v3m0 8v3M7.5 7.5l2 2m7 0l-2-2m0 11l-2-2m-7 2l2-2M12 9.5a2.5 2.5 0 100 5 2.5 2.5 0 000-5z'

/** Two positions to resume from, which is what the research calls two cursors. */
export const CURSOR_ICON =
  'M4 7h10a3 3 0 010 6h-1m7-6h-3a3 3 0 000 6h3M4 7v10m16-10v10M7 20h10'

/** A record awaiting a full re-read. */
export const DIRTY_ICON =
  'M12 3a9 9 0 100 18 9 9 0 000-18zm0 4.5v5.5m0 3v.01'

const ICONS = { gap: GAP_ICON, cursor: CURSOR_ICON, dirty: DIRTY_ICON }

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
 * `variant` and `className` are destructured out of `props` rather than read off
 * it: spreading them onto a DOM `<button>` would put an unknown attribute on the
 * element, and a reader would be looking at `variant="primary"` in the markup
 * wondering what it does.
 */
export function PathButton({
  glyph,
  variant = 'secondary',
  className = '',
  children,
  ...props
}) {
  return (
    <button
      type="button"
      className={`inline-flex min-h-11 cursor-pointer items-center gap-2 rounded-sm px-4 text-sm
        transition-colors duration-200 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none
        motion-reduce:transition-none disabled:cursor-not-allowed disabled:opacity-50
        ${BUTTON_VARIANTS[variant] || BUTTON_VARIANTS.secondary} ${className}`}
      {...props}
    >
      {glyph && <Glyph name={glyph} />}
      {children}
    </button>
  )
}

/**
 * Inline notice for a message the user needs to act on.
 *
 * A failure is announced and a confirmation is not, because a screen reader
 * interrupting a page load to say "it worked" is noise. The words are always
 * present, so the tone is decoration and never the only carrier of meaning.
 */
export function Notice({ tone = 'info', title, children, icon, onDismiss }) {
  const tones = {
    info: 'border-info/30 bg-info/10 text-foreground',
    success: 'border-accent/40 bg-accent-10 text-foreground',
    warning: 'border-warning/40 bg-warning/10 text-foreground',
    danger: 'border-destructive/40 bg-destructive/10 text-foreground',
  }
  const role = tone === 'danger' || tone === 'warning' ? 'alert' : 'status'
  return (
    <div role={role} className={`rounded-sm border p-4 ${tones[tone] || tones.info}`}>
      <div className="flex items-start gap-3">
        {icon && (
          <span className="mt-0.5 shrink-0 text-muted-foreground">
            <Glyph name={icon} />
          </span>
        )}
        <div className="min-w-0 flex-1">
          {title && <p className="text-sm font-semibold">{title}</p>}
          <div className="mt-0.5 text-sm text-foreground">{children}</div>
        </div>
        {onDismiss && (
          <button
            type="button"
            onClick={onDismiss}
            className="flex min-h-11 min-w-11 shrink-0 cursor-pointer items-center justify-center rounded-sm
              text-muted-foreground transition-colors duration-150 hover:bg-muted hover:text-foreground
              focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none motion-reduce:transition-none"
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
 * The `min-w` and the scroll container are what keep a wide table from forcing a
 * horizontal scroll on the page at a narrow width.
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

/** A state shown as words first and a badge second. */
export function StateLine({ tone = 'neutral', label, children }) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Badge tone={tone}>{label}</Badge>
      <span className="text-[13px] text-foreground">{children}</span>
    </div>
  )
}