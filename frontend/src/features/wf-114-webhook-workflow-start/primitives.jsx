/**
 * Glyphs and the small primitives this feature needs, built here rather than
 * added to `components/ui.jsx`.
 *
 * `components/ui.jsx` is shared and a hundred features each appending to it is
 * the collision the feature host exists to prevent, so the glyphs are passed as
 * paths through `<Icon path=...>` and the table shell and key/value block are
 * built in this folder. Both are findings for the integrator: a scrollable table
 * and a key/value block are generic, and promoting them into `ui.jsx` is a
 * one-off platform change belonging to whoever owns that file.
 *
 * The floor they meet, from `docs/DESIGN-SYSTEM.md`: a 44px minimum touch
 * target, a visible focus ring (inherited globally from `index.css`, and never
 * suppressed without a replacement), a text label beside every glyph so meaning
 * survives with icons off, no emoji as an icon, and no motion beyond the global
 * `prefers-reduced-motion` rule.
 */

import { Badge, Icon } from '@/components/ui'

/**
 * An arrow arriving at a starting bracket: the shape of "outside data starts
 * something here".
 *
 * The nav glyph, passed as a path because it is not in the shared `PATHS` map
 * and that file is not ours to edit.
 */
export const WEBHOOK_ICON = 'M4 6h10a3 3 0 013 3v9M13 15l4 3 4-3M19 3v9'

/** A play triangle inside a bracket: the shape of a run starting. */
export const START_ICON = 'M6 4l12 8-12 8V4zM20 3v18'

/** Two lists joined by a link: the shape of a mapping row. */
export const MAPPING_ICON = 'M4 6h6M4 12h6M4 18h6M14 6h6M14 12h6M14 18h6M10 12h4'

const ICONS = { webhook: WEBHOOK_ICON, start: START_ICON, mapping: MAPPING_ICON }

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
 * The design system bans horizontal scroll on mobile, and a delivery log with
 * five columns will overflow a phone whatever the layout. Letting the table
 * scroll inside a card keeps the page itself still, which is what the rule is
 * for.
 */
export function DataTable({ columns, rows, empty, rowKey, caption }) {
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
        {caption && <caption className="sr-only">{caption}</caption>}
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
 * fingerprint, URL and id in this feature goes through here rather than through
 * a paragraph.
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
 * `Badge` tinted by tone is a colour a screen reader and a colour-blind reader
 * both miss. So every badge on this page carries its own word and this component
 * is the only way one is rendered.
 */
export function StatusBadge({ tone, word }) {
  return <Badge tone={tone}>{word}</Badge>
}

const BUTTON_VARIANTS = {
  primary: 'bg-accent font-semibold text-on-accent hover:bg-primary',
  secondary:
    'bg-surface text-foreground border border-border-subtle hover:border-accent hover:text-accent',
  ghost: 'text-muted-foreground hover:text-foreground hover:bg-muted',
}

/**
 * A button carrying a glyph this feature owns.
 *
 * The shared `Button` resolves its `icon` only through the shared `PATHS` map,
 * which is a file this feature may not edit, so the glyph is injected as a child
 * instead. `variant` and `className` are destructured out of `props` rather than
 * read off it: spreading them onto a DOM `<button>` would put an unknown
 * attribute on the element and would overwrite the computed class.
 */
export function GlyphButton({ glyph, variant = 'secondary', className = '', children, ...props }) {
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

/** A key/value block, for the signing contract and a trigger's mapping. */
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

const NOTE_TONES = {
  info: 'border-info/30 bg-info/10',
  danger: 'border-destructive/40 bg-destructive/10',
  success: 'border-success/40 bg-success/10',
}

/**
 * A one-line note for a success, a wait or a refusal.
 *
 * Built here rather than imported, because the shared `ui.jsx` in this tree does
 * not ship a `Notice`: the feature contract lists one, the file does not export
 * one, and a feature may not edit the file to add it. This is a finding for the
 * integrator.
 */
export function InlineNote({ tone = 'info', children }) {
  return (
    <div
      role={tone === 'danger' ? 'alert' : 'status'}
      className={`rounded-sm border p-3 text-sm text-foreground ${NOTE_TONES[tone] || NOTE_TONES.info}`}
    >
      {children}
    </div>
  )
}

/** A bulleted list of short lines, for the constraints this workflow carries. */
export function BulletList({ items }) {
  return (
    <ul className="space-y-2 text-sm text-foreground">
      {items.map((item) => (
        <li key={item} className="flex gap-2">
          <span aria-hidden="true" className="text-muted-foreground">
            &bull;
          </span>
          <span>{item}</span>
        </li>
      ))}
    </ul>
  )
}
