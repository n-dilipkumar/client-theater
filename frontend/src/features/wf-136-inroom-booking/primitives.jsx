/**
 * The small primitives this feature draws for itself.
 *
 * `docs/DESIGN-SYSTEM.md` and `docs/FEATURE-CONTRACT.md` both list `Badge` as
 * available from `@/components/ui`, and the shipped `ui.jsx` has it, but its
 * tone set is the action vocabulary (insert / update / delete / restore), not a
 * status vocabulary with `success` and `warning`. Rather than edit the shared
 * file - which every parallel feature would then conflict on - this feature
 * builds one status badge of its own, to the design floor: `rounded-xs`,
 * semantic tokens only, and a visible word beside every colour.
 *
 * WF-124 reached the same conclusion for the same reason; the integrator can
 * promote one of the two once it recurs more than twice.
 */

import { STATE_TONES, STATE_WORDS } from './api'

const TONES = {
  neutral: 'border-border-subtle bg-muted text-muted-foreground',
  info: 'border-info/30 bg-info/10 text-info',
  success: 'border-success/30 bg-success/10 text-success',
  warning: 'border-warning/30 bg-warning/10 text-warning',
}

/**
 * A proposal's state as a badge whose meaning does not depend on its colour.
 *
 * The word is always present and is the first thing a screen reader says, so a
 * monochrome screen loses nothing. `expired` gets `warning` rather than
 * `destructive`, because nothing failed - the clock ran out.
 */
export function StateBadge({ state }) {
  const word = STATE_WORDS[state] || state || 'Unknown'
  const tone = STATE_TONES[state] || 'neutral'
  return (
    <span
      className={`inline-flex items-center rounded-xs border px-2 py-0.5 font-mono text-xs font-medium ${
        TONES[tone] || TONES.neutral
      }`}
    >
      {word}
    </span>
  )
}

/**
 * A meeting's link state, in words.
 *
 * `unlinked` is not a failure of the booking: WF-059's own rule is that a
 * booking whose Location cannot produce a conference is still a booking, so the
 * label says the link is missing rather than the meeting.
 */
export function LinkBadge({ state }) {
  const linked = state === 'linked'
  return (
    <span
      className={`inline-flex items-center rounded-xs border px-2 py-0.5 font-mono text-xs font-medium ${
        linked ? TONES.success : TONES.warning
      }`}
    >
      {linked ? 'Join link ready' : 'No join link yet'}
    </span>
  )
}

/** A micro label, the shared convention for a card's field name. */
export function MicroLabel({ children }) {
  return (
    <span className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
      {children}
    </span>
  )
}

/** A machine value: an id, a time, a count. Mono, per the type table. */
export function Machine({ children }) {
  return <span className="font-mono text-xs text-muted-foreground">{children}</span>
}
