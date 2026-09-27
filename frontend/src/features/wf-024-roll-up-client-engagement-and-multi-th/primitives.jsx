/**
 * Three UI primitives this feature needs that the shared set in
 * `components/ui.jsx` does not export.
 *
 * `components/ui.jsx` is shared, and a hundred features each appending to it is
 * the collision the plugin host exists to prevent. `docs/FEATURE-CONTRACT.md`
 * says to build what is genuinely missing inside the feature folder and say so,
 * so the integrator can promote the recurring ones as platform work once.
 * Promotion candidates, stated unambiguously:
 *
 *   - `Tile` is the clickable metric tile. The researched flow is "Click a
 *     metric tile to expand the full list of accounts", so the tile itself is the
 *     control. `StatCard` is the same shape of thing and is not interactive, and
 *     wrapping a non-interactive card in a button nests interactive elements;
 *     a `Tile` is the interactive sibling of `StatCard`, not a variant of it.
 *   - `ToggleChip` is the multi-select filter control. The researched filter is
 *     "date range, owners, and/or teams" - plural - and a single-select dropdown
 *     would not express "and/or".
 *   - `SortHeader` is a sortable column header that reports `aria-sort` on the
 *     `<th>`. A sortable table is a platform-shaped control, not a local one.
 *
 * Not a promotion candidate: the glyph in `./icons.jsx`. That is this feature's
 * own visual vocabulary, and the contract already has the mechanism for shared
 * glyphs (`Icon path=`, and `iconPath` in the descriptor).
 *
 * All three meet the floor in `design-system/digital-sales-room/MASTER.md`:
 * 44px minimum target, a real focusable control, a visible text label, no emoji
 * as an icon, and no motion at all - so `prefers-reduced-motion` is respected by
 * there being nothing to reduce.
 */

import { Icon } from '@/components/ui'

const CHIP_BASE =
  'inline-flex min-h-11 cursor-pointer items-center gap-2 rounded-lg border px-3 text-sm ' +
  'transition-colors duration-200'

/**
 * Sort arrows, as SVG rather than as typographic characters.
 *
 * The design system forbids emoji as icons; `▲` and `↕` are typographic symbols
 * that render inconsistently across platforms and read as emoji in several font
 * stacks. Passing a `path` to the shared `Icon` is the mechanism the contract
 * provides for a glyph the shared map does not carry, and it keeps this feature
 * from appending to `PATHS`.
 */
const ARROW_UP = 'M12 7v10m0 0l-4-4m4 4l4-4'
const ARROW_DOWN = 'M12 17V7m0 0l-4 4m4-4l4 4'
const ARROW_BOTH = 'M8 10l4-4 4 4M8 14l4 4 4-4'

/**
 * A metric tile that is also the control that expands it.
 *
 * The researched flow is a click on the tile, so the whole card is the target
 * rather than a small link inside it: a 44px minimum hit area is a design-system
 * requirement, and a link in the corner of a tile is not one.
 */
export function Tile({ label, value, display, hint, expanded, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={expanded}
      className={`glass card-hover rounded-xl p-5 text-left transition-colors duration-200 cursor-pointer ${
        expanded ? 'border-accent/60' : 'hover:border-border-subtle'
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
            {label}
          </p>
          <p className="mt-2 font-mono text-3xl font-semibold text-foreground">
            {display ?? value}
          </p>
          {hint && <p className="mt-1 text-xs text-muted-foreground">{hint}</p>}
        </div>
        <span className="rounded-lg bg-muted p-2 text-accent">
          <Icon name="chevron" size={20} />
        </span>
      </div>
      <p className="mt-3 text-xs font-medium text-accent">Expand to the account list</p>
    </button>
  )
}

/** One selectable value in a multi-select filter. */
export function ToggleChip({ active, children, ...props }) {
  return (
    <button
      type="button"
      aria-pressed={active}
      className={`${CHIP_BASE} ${
        active
          ? 'border-accent/60 bg-accent/15 text-accent'
          : 'border-border-subtle/50 bg-muted/40 text-muted-foreground hover:text-foreground'
      }`}
      {...props}
    >
      {active && <Icon name="schema" size={14} />}
      {children}
    </button>
  )
}

/**
 * A sortable column header.
 *
 * `aria-sort` goes on the `<th>`, not on the button: that is where assistive
 * technology looks for it, and a button labelled "sort by views" that never
 * says which direction it will sort is the accessibility defect this avoids.
 */
export function SortHeader({ column, label, active, direction, onSort, numeric = false }) {
  const ariaSort = active ? (direction === 'descending' ? 'descending' : 'ascending') : 'none'
  return (
    <th
      scope="col"
      aria-sort={ariaSort}
      className={`px-3 py-2 text-xs font-medium tracking-wide uppercase ${
        numeric ? 'text-right' : 'text-left'
      } text-muted-foreground`}
    >
      <button
        type="button"
        onClick={() => onSort(column)}
        className={`inline-flex min-h-11 cursor-pointer items-center gap-1.5 rounded transition-colors duration-200 hover:text-foreground ${
          numeric ? 'flex-row-reverse' : ''
        } ${active ? 'text-accent' : ''}`}
      >
        {label}
        <Icon
          size={14}
          path={active ? (direction === 'descending' ? ARROW_DOWN : ARROW_UP) : ARROW_BOTH}
        />
      </button>
    </th>
  )
}

/** A group of mutually exclusive tabs, for the three reports in the family. */
export function Tabs({ tabs, active, onChange }) {
  return (
    <div role="tablist" aria-label="Reports" className="flex flex-wrap gap-2">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          type="button"
          role="tab"
          id={`wf024-tab-${tab.id}`}
          aria-selected={active === tab.id}
          aria-controls="wf024-panel"
          onClick={() => onChange(tab.id)}
          className={`${CHIP_BASE} ${
            active === tab.id
              ? 'border-accent/60 bg-accent/15 text-accent'
              : 'border-border-subtle/50 bg-muted/40 text-muted-foreground hover:text-foreground'
          }`}
        >
          {tab.label}
        </button>
      ))}
    </div>
  )
}
