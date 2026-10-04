/**
 * Glyphs and the two primitives this feature needs, built here rather than added
 * to `components/ui.jsx`.
 *
 * `components/ui.jsx` is shared and a hundred features each appending to it is
 * collision the feature host exists to prevent, so the glyphs are passed as paths
 * through `<Icon path=...>` and the two table shells are built in this folder.
 * Both are findings for the integrator: a scrollable table and a key/value block
 * are generic, and promoting them into `ui.jsx` is a one-off platform change
 * belonging to whoever owns that file.
 *
 * The floor they meet, from `docs/DESIGN-SYSTEM.md`: a 44px minimum touch target,
 * a visible focus ring (inherited globally from `index.css`), a text label beside
 * every glyph so meaning survives with icons off, no emoji as an icon, and no
 * motion beyond the global `prefers-reduced-motion` rule.
 */

import { Badge, Icon } from '@/components/ui'

/**
 * A webhook: an outbound arrow leaving a bracket, which is the shape of "this room
 * pushes data to something else".
 *
 * The nav glyph, passed as a path because it is not in the shared `PATHS` map and
 * that file is not ours to edit.
 */
export const WEBHOOK_ICON =
  'M7 4H4a1 1 0 00-1 1v4a1 1 0 001 1h3M17 4h3a1 1 0 011 1v4a1 1 0 01-1 1h-3M12 14v7M9 18l3 3 3-3'

/** A seal: the shape of a signature. */
export const SIGNATURE_ICON = 'M4 12l4 4 8-9M4 19h16'

/** A clock: the shape of the replay window, which is the consumer's to apply. */
export const WINDOW_ICON = 'M12 2a10 10 0 100 20 10 10 0 000-20zm0 2a8 8 0 110 16 8 8 0 010-16zm-1 3h2v5.1l3.6 2.1-1 1.7L11 13V7z'

const ICONS = { webhook: WEBHOOK_ICON, signature: SIGNATURE_ICON, window: WINDOW_ICON }

/** Draw one of this feature's glyphs, falling through to the shared set. */
export function Glyph({ name, size = 18, className = '' }) {
  return <Icon name={name} path={ICONS[name]} size={size} className={className} />
}

/** A section heading with a short rule under it, at the design system's h2 size. */
export function Section({ title, count, children }) {
  return (
    <section className="space-y-3">
      <div className="flex items-baseline justify-between gap-3 border-b border-border-subtle pb-2">
        <h2 className="font-display text-lg font-semibold text-foreground">{title}</h2>
        {count !== undefined && (
          <span className="font-mono text-xs text-muted-foreground">{count}</span>
        )}
      </div>
      {children}
    </section>
  )
}

/**
 * A data table that scrolls inside its own box rather than the page.
 *
 * The design system bans horizontal scroll on mobile, and a delivery log with six
 * columns will overflow a phone whatever the layout. Letting the table scroll
 * inside a card keeps the page itself still, which is what the rule is for.
 */
export function DataTable({ columns, rows, empty, rowKey }) {
  if (!rows || rows.length === 0) {
    return (
      <p className="rounded-sm border border-dashed border-border-subtle bg-surface p-6 text-center text-sm text-muted-foreground">
        {empty}
      </p>
    )
  }
  return (
    <div className="overflow-x-auto rounded-sm border border-border-subtle bg-surface">
      <table className="w-full border-collapse text-left text-sm">
        <thead>
          <tr className="border-b border-border-subtle">
            {columns.map((column) => (
              <th
                key={column.key}
                scope="col"
                className="whitespace-nowrap px-3 py-2 text-[11px] font-medium tracking-[0.14em] text-muted-foreground uppercase"
              >
                {column.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={rowKey(row)} className="border-b border-border-subtle last:border-b-0">
              {columns.map((column) => (
                <td key={column.key} className="px-3 py-2 align-top text-sm text-foreground">
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

/**
 * A machine value in mono, beside its label.
 *
 * The design system says mono is for machine values and not prose, so every
 * signature, timestamp, URL and id in this feature goes through here rather than
 * through a paragraph.
 */
export function MachineValue({ children, className = '' }) {
  if (children === null || children === undefined || children === '') {
    return <span className="font-mono text-[13px] text-muted-foreground/70">none</span>
  }
  return (
    <span className={`font-mono text-[13px] break-all text-foreground ${className}`}>
      {children}
    </span>
  )
}

/**
 * A status word beside its badge.
 *
 * The accessibility floor says no status is conveyed by colour alone, and a
 * `Badge` tinted by tone is a colour a screen reader and a colour-blind reader both
 * miss. So every badge on this page carries its own word and this component is the
 * only way one is rendered.
 */
export function StatusBadge({ tone, word }) {
  return <Badge tone={tone}>{word}</Badge>
}

/**
 * A button carrying a glyph this feature owns.
 *
 * The shared `Button` resolves its `icon` only through the shared `PATHS` map,
 * which is a file this feature may not edit, so the glyph is injected as a child
 * instead. `variant` and `className` are destructured out of `props` rather than
 * read off it: spreading them onto a DOM `<button>` would put an unknown attribute
 * on the element and would overwrite the computed class with the caller's.
 */
const BUTTON_VARIANTS = {
  primary: 'bg-accent font-semibold text-on-accent hover:bg-primary',
  secondary: 'bg-surface text-foreground border border-border-subtle hover:border-accent hover:text-accent',
  danger: 'bg-destructive/10 text-destructive border border-destructive/30 hover:bg-destructive/15',
}

export function PathButton({ glyph, variant = 'secondary', className = '', children, ...props }) {
  return (
    <button
      type="button"
      className={`inline-flex min-h-11 cursor-pointer items-center gap-2 rounded-sm px-4 text-sm
        transition-colors duration-150 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none
        disabled:cursor-not-allowed disabled:opacity-50 ${BUTTON_VARIANTS[variant] || BUTTON_VARIANTS.secondary} ${className}`}
      {...props}
    >
      {glyph && <Glyph name={glyph} />}
      {children}
    </button>
  )
}

/** A key/value block, for the sample's bytes and the register's entries. */
export function FactList({ facts }) {
  return (
    <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-[minmax(0,14rem)_minmax(0,1fr)]">
      {facts.map(([term, value]) => (
        <div key={term} className="contents">
          <dt className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
            {term}
          </dt>
          <dd className="min-w-0 text-sm text-foreground">
            {value === null || value === undefined || value === '' ? (
              <span className="text-muted-foreground/70">none</span>
            ) : typeof value === 'string' || typeof value === 'number' ? (
              <MachineValue>{value}</MachineValue>
            ) : (
              value
            )}
          </dd>
        </div>
      ))}
    </dl>
  )
}
