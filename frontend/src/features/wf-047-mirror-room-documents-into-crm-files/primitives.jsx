/**
 * Glyphs and the four primitives this feature needs, built here rather than
 * added to `components/ui.jsx`.
 *
 * `components/ui.jsx` is shared and a hundred features each appending to it is
 * the collision the feature host exists to prevent, so the glyphs are passed as
 * paths through `<Icon path=...>` and the chip, the section, the fact row and the
 * table are built in this folder. The chip is the strongest candidate for
 * promotion: every workflow with a status per row will want it, and promoting it
 * into `ui.jsx` is a one-off platform change belonging to whoever owns that file.
 *
 * The floor they meet, from the design system: a 44px minimum touch target, a
 * visible focus ring (inherited globally from `index.css`), a text label beside
 * every glyph so meaning survives with icons off, no emoji, and no motion beyond
 * the global `prefers-reduced-motion` rule.
 */

import { Badge, Icon } from '@/components/ui'

/**
 * A document leaving the room for a CRM: a sheet with an arrow.
 *
 * The nav wants "the room pushed a document out to a CRM", which is not in the
 * shared `PATHS` map, and that map is not ours to edit. `CRM_FILE_ICON` is the
 * fallback for a consumer that reads only the shared name.
 */
export const CRM_FILE_ICON =
  'M6 2h8l4 4v16a1 1 0 01-1 1H6a1 1 0 01-1-1V3a1 1 0 011-1zm7 1.5V7h3.5L13 3.5zM8 12h8v1.6H8V12zm0 3.4h8V17H8v-1.6zm0-6.8h4v1.6H8V8.6zm7.6 11.6l4-4-1.2-1.2-2.8 2.8-1.2-1.2-1.2 1.2 3.4 3.4z'

/** A folder the vendor recommends but does not require. */
export const FOLDER_ICON =
  'M3 6h6l2 2h10v11H3V6zm2 2v9h14V10H9.8L8.8 8H5z'

/** A clock over the reconciliation floor: the pass is scheduled, not running. */
export const RECONCILE_ICON =
  'M12 3a9 9 0 100 18 9 9 0 000-18zm0 2a7 7 0 110 14 7 7 0 010-14zm-1 2.2v5.3l4.2 2.5-1 1.7-5.2-3.1V7.2h2z'

const ICONS = { file: CRM_FILE_ICON, folder: FOLDER_ICON, reconcile: RECONCILE_ICON }

/** Draw one of this feature's glyphs, falling through to the shared set. */
export function Glyph({ name, size = 18, className = '' }) {
  return <Icon name={name} path={ICONS[name]} size={size} className={className} />
}

/**
 * The researched "Push to CRM" chip.
 *
 * The product surface the research names is a status chip per file on the room's
 * Documents page, so this is the centre of the page rather than an aside. It
 * always carries three things: the state word, the tone, and the state's own
 * sentence. A chip that showed only the word, or only the colour, would fail the
 * design floor, and a chip that showed no sentence would leave the rep to guess
 * what to do next.
 */
export function PushChip({ state, note, tone }) {
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <Badge tone={tone}>{state}</Badge>
      {note ? <span className="text-[13px] text-muted-foreground">{note}</span> : null}
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

/**
 * A short note that carries a finding in words.
 *
 * `components/ui.jsx` names a `Notice` in the design system's component list, but
 * the shipped file does not export one, and that file is not ours to edit. This is
 * the local stand-in, built from semantic tokens so it follows a theme change. It
 * is a promotion candidate: a workflow that has something to say and no room for an
 * error will want it, and promoting it once is platform work.
 */
const CALLOUT_TONES = {
  info: 'border-info/40 bg-info/10 text-foreground',
  warning: 'border-warning/40 bg-warning/10 text-foreground',
  error: 'border-destructive/40 bg-destructive/10 text-foreground',
}

export function Callout({ tone = 'info', children }) {
  return (
    <div
      role={tone === 'error' ? 'alert' : 'status'}
      aria-live={tone === 'error' ? 'assertive' : 'polite'}
      className={`flex items-start gap-3 rounded-sm border p-4 text-sm ${CALLOUT_TONES[tone] || CALLOUT_TONES.info}`}
    >
      <span className="min-w-0">{children}</span>
    </div>
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

/** A table that says so when it is empty, rather than rendering an empty grid. */
export function DataTable({ columns, rows, rowKey, empty }) {
  if (!rows.length) {
    return <p className="py-6 text-center text-sm text-muted-foreground">{empty}</p>
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[44rem] border-collapse text-left">
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