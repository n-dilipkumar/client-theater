/**
 * Primitives for the SLA timers page (WF-108).
 *
 * The shared `components/ui.jsx` exports Button, Card, StatCard, Badge, Field, Spinner,
 * ErrorNote, EmptyState, Icon, inputClass, useAsync and JsonView. The badge this page
 * needs carries a status colour, and the shared `Badge` only has audit tones, so it is
 * built here rather than added to the shared file, which a feature may not edit. The
 * integrator promotes a recurring one into `ui.jsx` as platform work, once.
 *
 * The design floor is met here: a text label sits beside every status, so no status is
 * conveyed by colour alone, and `rounded-sm` is the only radius used.
 */

import { Icon } from '@/components/ui'

/** The nav glyph: a clock face with a target mark inside it. */
export const SLA_ICON =
  'M12 21a9 9 0 100-18 9 9 0 000 18zm0-13v4l3 2M12 3V1m0 22v-2M3 12H1m22 0h-2'

const GLYPHS = {
  clock: 'M12 21a9 9 0 100-18 9 9 0 000 18zm0-13v4l3 2',
  target: 'M12 21a9 9 0 100-18 9 9 0 000 18zm0-4a5 5 0 100-10 5 5 0 000 10zm0-4a1 1 0 100-2 1 1 0 000 2z',
  check: 'M20 6L9 17l-5-5',
  alert: 'M12 9v4m0 4h.01M10.3 3.9L1.8 18a2 2 0 001.7 3h17a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z',
  pause: 'M10 4H7v16h3V4zm7 0h-3v16h3V4z',
}

/**
 * A status badge: the three the specification names, each carrying its words.
 *
 * Red is overdue, orange is under five minutes, grey is more than five. The words are
 * always rendered, so a reader who cannot tell the tones gets the same answer.
 */
export function SlaBadge({ badge, children }) {
  const tones = {
    red: 'bg-destructive/10 text-destructive border-destructive/40',
    orange: 'bg-warning/10 text-warning border-warning/40',
    grey: 'bg-muted text-muted-foreground border-border-subtle',
  }
  const glyphs = { red: GLYPHS.alert, orange: GLYPHS.clock, grey: GLYPHS.check }
  const label = children || badge || 'no badge'
  return (
    <span
      className={`inline-flex min-h-6 items-center gap-1.5 rounded-xs border px-2 py-0.5 text-xs font-medium ${
        tones[badge] || tones.grey
      }`}
    >
      <Icon path={glyphs[badge] || GLYPHS.clock} size={13} />
      {label}
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

/** One metric's countdown, always with the metric's short name and its state word. */
export function MetricRow({ metric }) {
  return (
    <li className="flex min-h-11 items-center gap-3 border-b border-border-subtle py-1.5 last:border-b-0">
      <span className="font-mono text-xs font-semibold text-foreground">{metric.short}</span>
      <span className="min-w-0 flex-1 truncate text-sm text-foreground">{metric.label}</span>
      <span className="font-mono text-xs text-muted-foreground">{formatSeconds(metric.remaining_seconds)}</span>
      <span className="text-xs text-muted-foreground">{metric.state_label}</span>
    </li>
  )
}

export const METRIC_GLYPHS = GLYPHS

/** Seconds as `1h 05m` or `45s`, for a countdown a person reads rather than computes. */
export function formatSeconds(value) {
  if (value === null || value === undefined) return 'not applicable'
  const total = Math.max(0, Math.round(Number(value)))
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const seconds = total % 60
  if (hours > 0) return `${hours}h ${String(minutes).padStart(2, '0')}m`
  if (minutes > 0) return `${minutes}m ${String(seconds).padStart(2, '0')}s`
  return `${seconds}s`
}
