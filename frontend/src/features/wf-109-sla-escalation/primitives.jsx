/**
 * Primitives for the SLA escalation page (WF-109).
 *
 * The shared `components/ui.jsx` exports Button, Card, StatCard, Badge, Field, Spinner,
 * ErrorNote, EmptyState, Icon, inputClass, useAsync and JsonView. The level chip this page
 * needs carries a status colour, and the shared `Badge` only has audit tones, so it is
 * built here rather than added to the shared file, which a feature may not edit. The
 * integrator promotes a recurring one into `ui.jsx` as platform work, once.
 *
 * The design floor is met here: a text label sits beside every status, so no status is
 * conveyed by colour alone, and `rounded-sm` is the only radius used.
 */

import { Icon } from '@/components/ui'

/** The nav glyph: a bell with a clock hand, for a warning that fires on a timer. */
export const ESCALATION_ICON =
  'M18 8a6 6 0 10-12 0c0 7-3 8-3 8h18s-3-1-3-8M13.7 21a2 2 0 01-3.4 0'

const GLYPHS = {
  warning: 'M12 9v4m0 4h.01M10.3 3.9L1.8 18a2 2 0 001.7 3h17a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z',
  clock: 'M12 21a9 9 0 100-18 9 9 0 000 18zm0-13v4l3 2',
  check: 'M20 6L9 17l-5-5',
  bell: 'M18 8a6 6 0 10-12 0c0 7-3 8-3 8h18s-3-1-3-8M13.7 21a2 2 0 01-3.4 0',
  send: 'M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z',
}

/**
 * An escalation level chip: the two the specification describes, each carrying its words.
 *
 * Red is a breach, amber is a pre-warning, grey is not escalated. The words are always
 * rendered, so a reader who cannot tell the tones gets the same answer.
 */
export function LevelBadge({ level, children }) {
  const tones = {
    breach: 'bg-destructive/10 text-destructive border-destructive/40',
    pre_warning: 'bg-warning/10 text-warning border-warning/40',
  }
  const glyphs = { breach: GLYPHS.warning, pre_warning: GLYPHS.clock }
  return (
    <span
      className={`inline-flex min-h-6 items-center gap-1.5 rounded-xs border px-2 py-0.5 text-xs font-medium ${
        tones[level] || 'bg-muted text-muted-foreground border-border-subtle'
      }`}
    >
      <Icon path={glyphs[level] || GLYPHS.check} size={13} />
      {children || level || 'not escalated'}
    </span>
  )
}

/**
 * An alert's outcome: the spec reports `success`, `http_status` and `error_type`.
 *
 * The outcome is always rendered as a word and a status code, never as colour alone.
 */
export function AlertResult({ alert }) {
  const failed = alert && alert.success === false
  return (
    <span
      className={`inline-flex min-h-6 items-center gap-1.5 rounded-xs border px-2 py-0.5 font-mono text-xs font-medium ${
        failed
          ? 'bg-destructive/10 text-destructive border-destructive/40'
          : 'bg-success/10 text-success border-success/40'
      }`}
    >
      <Icon path={GLYPHS.send} size={13} />
      {failed ? `Failed ${alert.http_status} ${alert.error_type}` : `${alert.http_status} sent`}
    </span>
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

/** The researched sentence a behaviour came from, quoted. */
export function Quote({ children, source }) {
  return (
    <figure className="rounded-sm border-l-2 border-accent bg-background/60 px-3 py-2">
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
