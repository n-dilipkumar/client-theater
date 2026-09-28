/**
 * Primitives the meeting changes page needs and the shared set cannot express.
 *
 * The shared `components/ui.jsx` carries Icon, Button, Card, StatCard, Badge,
 * Field, Spinner, ErrorNote, EmptyState, inputClass, useAsync and JsonView. Three
 * things are missing for this page, and all three are built here rather than added
 * to the shared file, which a feature may not edit. The integrator promotes a
 * recurring one into `ui.jsx` as platform work, once.
 */

import { Icon } from '@/components/ui'

/**
 * A change-type chip: a glyph plus its label, so the type never relies on colour
 * or on the glyph alone. The design floor requires a text label beside every icon,
 * and a change log is exactly where that matters most - four types that differ
 * only by hue would be unreadable to a colour-blind rep and in a screenshot
 * printed in black and white.
 */
const CHANGE_TONES = {
  rescheduled: { tone: 'insert', glyph: 'reschedule' },
  cancelled: { tone: 'delete', glyph: 'cancel' },
  reschedule_requested: { tone: 'restore', glyph: 'clock' },
  location_updated: { tone: 'update', glyph: 'fanout' },
}

const CHANGE_LABELS = {
  rescheduled: 'rescheduled',
  cancelled: 'cancelled',
  reschedule_requested: 'asked to reschedule',
  location_updated: 'location updated',
}

const TONE_CLASSES = {
  insert: 'border-accent/30 bg-accent/15 text-accent',
  update: 'border-sky-500/30 bg-sky-500/15 text-sky-300',
  delete: 'border-destructive/30 bg-destructive/15 text-destructive',
  restore: 'border-amber-500/30 bg-amber-500/15 text-amber-300',
  neutral: 'border-border-subtle/40 bg-muted text-muted-foreground',
}

export function ChangeChip({ type, glyphs }) {
  const spec = CHANGE_TONES[type] || { tone: 'neutral', glyph: 'clock' }
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 font-mono
        text-xs font-medium ${TONE_CLASSES[spec.tone]}`}
    >
      <Icon path={glyphs?.[spec.glyph]} size={13} />
      {CHANGE_LABELS[type] || type}
    </span>
  )
}

/**
 * A link's state: open or expired, with the glyph that says which.
 *
 * The label is the point. "Expired" is the researched setting working exactly as
 * an administrator asked, and a rep has to be able to tell that apart from a
 * broken link without reading the reason - so the chip says it, and the reason is
 * one click away.
 */
export function LinkChip({ state, glyphs }) {
  const open = !state?.expired
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 font-mono
        text-xs font-medium ${open ? TONE_CLASSES.insert : TONE_CLASSES.neutral}`}
    >
      <Icon path={open ? glyphs?.open : glyphs?.expired} size={13} />
      {state?.kind === 'cancel' ? 'cancel link' : 'reschedule link'} · {open ? 'open' : 'expired'}
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

/** An inline note for a success message, matching the shared ErrorNote's shape. */
export function Note({ children }) {
  return (
    <p
      role="status"
      aria-live="polite"
      className="rounded-lg border border-accent/30 bg-accent/10 p-3 text-sm text-foreground"
    >
      {children}
    </p>
  )
}

/**
 * A before/after pair of times, which is what a reschedule is for.
 *
 * Both instants are always rendered, including the "before" one that has just
 * stopped being true. A move shown only as its destination is a change nobody can
 * reconstruct the distance of, and a rep asked "did we move it?" needs the old
 * time to answer.
 */
export function MoveSummary({ from, to }) {
  return (
    <span className="flex flex-wrap items-center gap-1.5 font-mono text-xs">
      <span className="text-muted-foreground line-through">{from?.start_at || '—'}</span>
      <Icon path="M5 12h14m-4-4l4 4-4 4" size={12} />
      <span className="text-foreground">{to?.start_at || '—'}</span>
    </span>
  )
}
