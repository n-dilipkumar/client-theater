/**
 * Primitives the nurture series page needs and the shared set cannot express.
 *
 * The shared `components/ui.jsx` exports Button, Card, StatCard, Badge, Field,
 * Spinner, ErrorNote, EmptyState, Icon, inputClass, useAsync and JsonView. Three
 * things this page needs are missing, and all three are built here rather than
 * added to the shared file, which a feature may not edit. The integrator promotes
 * a recurring one into `ui.jsx` as platform work, once.
 *
 * The design floor is met in this file rather than only in the page: 44px touch
 * targets on every control, a visible text label beside every glyph, and no
 * status conveyed by colour alone.
 */

import { Badge, Icon } from '@/components/ui'

/**
 * The nav glyph: a path of blocks with the loop that re-enters it, drawn as the
 * recurring series. Passed as `path` rather than added to the shared `PATHS` map,
 * because that map is a shared file this feature may not edit.
 */
export const NURTURE_SERIES_ICON =
  'M4 5h6v5H4V5zm0 9h6v5H4v-5zm10 0h6v5h-6v-5zM7 10v4m10-4V7a3 3 0 00-3-3h-1m4 4l-2-2m2 2l-2 2'

const GLYPHS = {
  loop: 'M4 12a8 8 0 108 8M4 12V7m0 5h5',
  tag: 'M3 12l9-9h9v9l-9 9-9-9zM16 8h.01',
  exit: 'M10 3H5a2 2 0 00-2 2v14a2 2 0 002 2h5M16 17l5-5-5-5M21 12H9',
  goal: 'M12 21a9 9 0 100-18 9 9 0 000 18zm0-4a5 5 0 100-10 5 5 0 000 10zm0-4a1 1 0 100-2 1 1 0 000 2z',
}

/** A short heading for a group of related fields, with an optional count. */
export function Subhead({ children, count }) {
  return (
    <h3 className="mb-2 flex items-center gap-2 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
      <span>{children}</span>
      {count !== undefined && <span className="font-mono text-foreground/70">{count}</span>}
    </h3>
  )
}

/** A key/value pair on one line, used in the detail panels. */
export function Fact({ label, children, mono = true }) {
  return (
    <div className="flex min-w-0 gap-2 text-xs">
      <span className="shrink-0 text-muted-foreground">{label}</span>
      <span className={`min-w-0 truncate ${mono ? 'font-mono text-foreground' : 'text-foreground'}`}>
        {children}
      </span>
    </div>
  )
}

/**
 * The researched sentence a behaviour came from, quoted.
 *
 * Every published constant in this workflow carries the sentence that fixes it, so
 * the page shows the sentence rather than asserting the value.
 */
export function Quote({ children, source }) {
  return (
    <figure className="rounded-sm border-l-2 border-accent bg-background/60 px-3 py-2">
      <blockquote className="text-xs text-foreground/90 italic">{children}</blockquote>
      {source && <figcaption className="mt-1 text-xs text-muted-foreground">{source}</figcaption>}
    </figure>
  )
}

/**
 * An inline note, for a warning beside a count or an explanation under a rule.
 *
 * `Notice` is named in `docs/FEATURE-CONTRACT.md` as a shared primitive, but
 * `components/ui.jsx` does not export one. Rather than edit that shared file the
 * primitive is built here and the discrepancy is named in the pull request.
 */
export function Notice({ children, tone = 'info' }) {
  const tones = {
    info: 'border-border-subtle bg-muted text-muted-foreground',
    warning: 'border-warning/40 bg-warning/10 text-foreground',
    danger: 'border-destructive/40 bg-destructive/10 text-foreground',
    good: 'border-accent/30 bg-accent-soft text-foreground',
  }
  const labels = { info: 'Note', warning: 'Warning', danger: 'Problem', good: 'OK' }
  return (
    <div
      className={`flex items-start gap-2 rounded-sm border px-3 py-2 text-sm ${tones[tone] || tones.info}`}
      role={tone === 'danger' ? 'alert' : 'status'}
    >
      <span className="shrink-0 text-[11px] font-semibold tracking-[0.14em] uppercase opacity-80">
        {labels[tone] || labels.info}
      </span>
      <span className="min-w-0">{children}</span>
    </div>
  )
}

/**
 * One filter, rendered as `field is value`.
 *
 * A filter is the unit the research describes ("Tag is ExitUpgradeSeries"), so it
 * is shown as words rather than as JSON a reader has to decode.
 */
export function FilterChip({ filter, glyph }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-xs border border-border-subtle bg-muted px-2 py-1 text-xs text-foreground">
      {glyph && <Icon path={glyph} size={13} />}
      <span className="font-medium">{filter.field}</span>
      <span className="text-muted-foreground">is</span>
      <span className="font-mono">{String(filter.value)}</span>
    </span>
  )
}

/** A block in the path, showing its position, kind and title. */
export function BlockRow({ block, position, last }) {
  const glyph = block.kind === 'tag' ? GLYPHS.tag : GLYPHS.loop
  return (
    <li className="flex min-h-11 items-center gap-3 border-b border-border-subtle py-2 last:border-b-0">
      <span className="font-mono text-xs text-muted-foreground">{position + 1}</span>
      <Icon path={glyph} size={15} />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm text-foreground">{block.title}</span>
        <span className="block font-mono text-xs text-muted-foreground">
          {block.kind}
          {block.tag ? ` / ${block.tag}` : ''}
        </span>
      </span>
      {last && <Badge tone="neutral">end of path</Badge>}
    </li>
  )
}

export const FILTER_GLYPHS = { start: GLYPHS.loop, exit: GLYPHS.exit, goal: GLYPHS.goal }
