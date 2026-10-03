/**
 * Primitives the ownership routing page needs and the shared set cannot express.
 *
 * The shared `components/ui.jsx` carries Button, Card, StatCard, Badge, Field,
 * Modal, Notice, Toggle, Checkbox, Spinner, ErrorNote, EmptyState, inputClass,
 * useAsync and JsonView. Two things are missing for this page, and both are built
 * here rather than added to the shared file, which a feature may not edit. The
 * integrator promotes a recurring one into `ui.jsx` as platform work, once.
 */

import { Icon } from '@/components/ui'

/**
 * A routing outcome chip: a glyph plus its label, so the outcome never relies on
 * colour or on the glyph alone.
 *
 * Two outcomes exist - the CRM owner took it, or the catch-all did because nobody
 * did - and they differ by meaning rather than by hue. That is exactly where colour
 * alone fails, so each carries a glyph and its own words.
 */
const OUTCOME_TONES = {
  resolved: { tone: 'insert', glyph: 'owner' },
  catch_all: { tone: 'restore', glyph: 'route' },
  unroutable: { tone: 'delete', glyph: 'guard' },
}

const OUTCOME_LABELS = {
  resolved: 'CRM owner',
  catch_all: 'catch-all',
  unroutable: 'no host',
}

export function OutcomeChip({ outcome, glyphs }) {
  const spec = OUTCOME_TONES[outcome] || { tone: 'neutral', glyph: 'route' }
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 font-mono
        text-xs font-medium ${
          spec.tone === 'insert'
            ? 'border-accent/30 bg-accent/15 text-accent'
            : spec.tone === 'delete'
              ? 'border-destructive/30 bg-destructive/15 text-destructive'
              : 'border-amber-500/30 bg-amber-500/15 text-amber-300'
        }`}
    >
      <Icon path={glyphs[spec.glyph]} size={13} />
      {OUTCOME_LABELS[outcome] || outcome}
    </span>
  )
}

/**
 * A key/value pair on one line, used in the detail panels.
 *
 * Not a `<dt>`/`<dd>` pair so it can be used in a flex row without a `<dl>` around
 * it, and it keeps the label from wrapping away from its value.
 */
export function Fact({ label, children, mono = true }) {
  return (
    <div className="flex min-w-0 gap-2 text-xs">
      <dt className="shrink-0 text-muted-foreground">{label}</dt>
      <dd className={`min-w-0 truncate ${mono ? 'font-mono text-foreground' : 'text-foreground'}`}>
        {children}
      </dd>
    </div>
  )
}

/** An inline note, for a success message or a caveat beside a field. */
export function Note({ children, tone = 'info' }) {
  const tones = {
    info: 'border-border-subtle/30 bg-background/60 text-muted-foreground',
    good: 'border-accent/30 bg-accent/10 text-accent',
    warn: 'border-amber-500/30 bg-amber-500/10 text-amber-300',
    bad: 'border-destructive/40 bg-destructive/10 text-destructive',
  }
  return (
    <p className={`rounded-lg border px-3 py-2 text-xs ${tones[tone]}`}>{children}</p>
  )
}

/**
 * The researched sentence a value came from, quoted.
 *
 * Every published constant in this workflow carries the sentence that fixes it, so
 * the page shows it rather than asserting the value. A reviewer can then disagree
 * with the *research* on screen instead of in a diff.
 */
export function Quote({ children, source }) {
  return (
    <figure className="rounded-lg border-l-2 border-accent/50 bg-background/40 px-3 py-2">
      <blockquote className="text-xs text-foreground/90 italic">{children}</blockquote>
      {source && <figcaption className="mt-1 text-xs text-muted-foreground">{source}</figcaption>}
    </figure>
  )
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
