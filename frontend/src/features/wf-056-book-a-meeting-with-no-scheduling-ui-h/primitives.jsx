/**
 * Primitives the headless booking page needs and the shared set cannot express.
 *
 * The shared `components/ui.jsx` carries Button, Card, StatCard, Badge, Field,
 * Modal, Notice, Toggle, Checkbox, Spinner, ErrorNote, EmptyState, inputClass,
 * useAsync and JsonView. Three things are missing for this page, and all three
 * are built here rather than added to the shared file, which a feature may not
 * edit. The integrator promotes a recurring one into `ui.jsx` as platform work,
 * once.
 *
 * The theme of all three is the same: this page's subject is a *refusal*, and
 * the researched workflow is mostly about what happens when something goes
 * wrong. So a caller must be able to see a session's state, read a reason the
 * backend gave as a code rather than a sentence, and spot at a glance that an
 * invite was recorded and not sent.
 */

import { Icon } from '@/components/ui'

/** A cross inside a circle, for a refused call. */
const GLYPH_REFUSED = 'M12 21a9 9 0 100-18 9 9 0 000 18zM9 9l6 6M15 9l-6 6'

/**
 * A session-state chip: a glyph plus its label.
 *
 * A session has four states and three of them are terminal, so a rep scanning
 * the session list is mostly looking for "this one is spent". The states differ
 * by more than hue - and the design floor requires a text label beside every
 * icon regardless - so the label is the payload and the glyph is the accent.
 */
const STATE_TONES = {
  open: {
    label: 'open',
    glyph: 'session',
    className: 'border-accent/30 bg-accent/15 text-accent',
  },
  booked: {
    label: 'booked',
    glyph: 'booked',
    className: 'border-sky-500/30 bg-sky-500/15 text-sky-300',
  },
  failed: {
    label: 'failed — spent',
    glyph: 'refused',
    className: 'border-destructive/30 bg-destructive/15 text-destructive',
  },
  expired: {
    label: 'expired — spent',
    glyph: 'spent',
    className: 'border-amber-500/30 bg-amber-500/15 text-amber-300',
  },
}

export function StateChip({ state, glyphs }) {
  const spec = STATE_TONES[state] || { label: state, glyph: 'session', className: 'border-border-subtle/40 bg-muted text-muted-foreground' }
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1.5 rounded-md border px-2 py-0.5
        font-mono text-xs font-medium ${spec.className}`}
    >
      <Icon path={glyphs[spec.glyph]} size={13} />
      {spec.label}
    </span>
  )
}

/**
 * A call-outcome chip for the call log, where `session_consumed` is the one to
 * notice.
 *
 * It gets its own tone rather than reusing `StateChip`, because a *call* and a
 * *session* are different things and conflating them is exactly how a reviewer
 * would misread the log: `session_consumed` on a call row means somebody
 * retried a routeId, and it is the researched mistake this page exists to make
 * visible.
 */
const CALL_TONES = {
  opened: { label: 'session opened', className: 'border-accent/30 bg-accent/15 text-accent' },
  booked: { label: 'booked', className: 'border-sky-500/30 bg-sky-500/15 text-sky-300' },
  session_consumed: {
    label: 'routeId already spent',
    className: 'border-destructive/30 bg-destructive/15 text-destructive',
  },
  session_expired: {
    label: 'session expired',
    className: 'border-amber-500/30 bg-amber-500/15 text-amber-300',
  },
  no_availability: { label: 'no availability', className: 'border-border-subtle/40 bg-muted text-muted-foreground' },
}

export function CallChip({ outcome }) {
  const spec = CALL_TONES[outcome] || {
    label: outcome,
    className: 'border-border-subtle/40 bg-muted text-muted-foreground',
  }
  return (
    <span
      className={`inline-flex shrink-0 items-center rounded-md border px-2 py-0.5 font-mono
        text-xs font-medium ${spec.className}`}
    >
      {spec.label}
    </span>
  )
}

/**
 * The researched reason a call was refused, as code plus sentence.
 *
 * The code is the reason the backend returns both. The research's instruction
 * is a *procedure* - "start again from the discover or route step" - so a
 * client that only showed the sentence would leave a caller to infer it, and one
 * that only showed the code would leave a human to look it up. The remedy is
 * always stated next to the code.
 */
export function RefusalNote({ reason, detail, nextStep, failureTable }) {
  if (!detail) return null
  const known = failureTable?.[reason]
  const remedy = nextStep || known?.next_step
  return (
    <div
      role="alert"
      className="space-y-1.5 rounded-lg border border-destructive/40 bg-destructive/10 p-3"
    >
      <p className="flex flex-wrap items-center gap-2 text-sm font-semibold text-destructive">
        <Icon path={GLYPH_REFUSED} size={15} />
        Refused
        {reason && <span className="font-mono text-xs font-medium">{reason}</span>}
      </p>
      <p className="text-sm text-foreground">{detail}</p>
      {known?.summary && <p className="text-xs text-muted-foreground">{known.summary}</p>}
      {remedy && <p className="text-xs font-medium text-foreground">Next step: {remedy}</p>}
    </div>
  )
}

/** A key/value pair on one line, used in the detail panels. */
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

/**
 * A slot button. 44px tall, because the design floor's minimum touch target is
 * not negotiable and a slot grid is the most clicked thing on this page.
 *
 * The label is the slot's own UTC string, verbatim. That is deliberate: the
 * researched rule is that the caller passes this exact value back, so showing
 * a prettified "9:00 AM" and sending `2026-10-06T09:00:00Z` would make the
 * page demonstrate the rounding this workflow exists to refuse.
 */
export function SlotButton({ value, selected, onSelect, disabled }) {
  return (
    <button
      type="button"
      onClick={onSelect}
      disabled={disabled}
      aria-pressed={selected}
      className={`min-h-11 w-full rounded-lg border px-2 py-1.5 text-left font-mono text-xs
        transition-colors duration-150 focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-ring
        disabled:cursor-not-allowed disabled:opacity-40 ${
          selected
            ? 'border-accent bg-accent/15 text-accent'
            : 'border-border-subtle/40 bg-background/60 text-foreground hover:bg-muted/50'
        }`}
    >
      {value}
    </button>
  )
}
