/**
 * Glyphs and the two primitives this feature needs, built here rather than added
 * to `components/ui.jsx`.
 *
 * `components/ui.jsx` is shared and a hundred features each appending to it is
 * the collision the feature host exists to prevent, so the glyphs are passed as
 * paths through `<Icon path=...>` and the value-and-label pair and the fact
 * table are built in this folder. Both are findings for the integrator: the
 * panel's own option-value display is generic enough that the next workflow will
 * want it too, and promoting it into `ui.jsx` is a one-off platform change
 * belonging to whoever owns that file.
 *
 * The floor they meet, from the design system: a 44px minimum touch target, a
 * visible focus ring (inherited globally from `index.css`), a text label beside
 * every glyph so meaning survives with icons off, no emoji, and no motion beyond
 * the global `prefers-reduced-motion` rule.
 */

import { Badge, Icon } from '@/components/ui'

/**
 * A panel reading a deal out of a CRM: a document with a signal arriving.
 *
 * The `ICON` shape of "the room fetched a record and holds it up" is what the nav
 * wants, and it is not in the shared `PATHS` map, which is why this folder
 * defines it and `Icon` receives it as a path. `CRM_READ_ICON` is the fallback
 * for a consumer that reads only the shared name.
 */
export const CRM_READ_ICON =
  'M6 3h8l4 4v14a1 1 0 01-1 1H6a1 1 0 01-1-1V4a1 1 0 011-1zm7 1.5V8h3.5L13 4.5zM8 12h8v1.6H8V12zm0 3.4h8V17H8v-1.6zm0-6.8h4v1.6H8V8.6z'

/** A clock over a cache: the read happened, and this is how long ago. */
export const CACHE_ICON =
  'M12 3a9 9 0 100 18 9 9 0 000-18zm0 2a7 7 0 110 14 7 7 0 010-14zm-1 2.2v5.3l4.2 2.5-1 1.7-5.2-3.1V7.2h2z'

/** A field map: two columns joined, which is what the read set is. */
export const FIELD_MAP_ICON =
  'M4 5h5v5H4V5zm6 0h5v5h-5V5zm6 0h4v5h-4V5zM4 14h5v5H4v-5zm6 0h5v5h-5v-5zm6 0h4v5h-4v-5zm-8-2.5h2v2.5H8v-2.5zm6 0h2v2.5h-2v-2.5z'

const ICONS = { read: CRM_READ_ICON, cache: CACHE_ICON, fieldmap: FIELD_MAP_ICON }

/** Draw one of this feature's glyphs, falling through to the shared set. */
export function Glyph({ name, size = 18, className = '' }) {
  return <Icon name={name} path={ICONS[name]} size={size} className={className} />
}

/**
 * One stored option value and its display label, side by side.
 *
 * The research's step five is that the panel shows "Proposal sent", not the
 * integer `2`. This component is that rule rendered, and it always shows both:
 * the label a reader wants and the stored value the seller recognises. When the
 * vendor annotated nothing and the room has no option set for the value, the
 * stored value is shown with the word "unlabelled" beside it, because the one
 * thing worse than a code is a code the reader does not know is a code.
 *
 * The state is carried in text and in a `Badge`, never in colour alone.
 */
export function OptionValue({ option, labelled }) {
  const value = option?.value
  const label = option?.label || ''
  const hasValue = value !== null && value !== undefined && value !== ''
  if (!hasValue && !label) {
    return <span className="font-mono text-[13px] text-muted-foreground">-</span>
  }
  // The stored value is printed beside the label only when a label exists. With
  // no label the two slots would hold the same word, which reads as a rendering
  // fault rather than as the pair the seller needs to see.
  const showStored = hasValue && Boolean(label)
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <span className="text-[13px] text-foreground">{label || String(value)}</span>
      {showStored ? (
        <span className="font-mono text-[11px] text-muted-foreground">{String(value)}</span>
      ) : null}
      {label || labelled ? null : <Badge tone="restore">unlabelled</Badge>}
    </span>
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