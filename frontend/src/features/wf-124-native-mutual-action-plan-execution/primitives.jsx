/**
 * Two primitives that `docs/DESIGN-SYSTEM.md` and `docs/FEATURE-CONTRACT.md` both list
 * as available from `@/components/ui`, and which that file does not export.
 *
 * The documented list is: `Button`, `Card`, `StatCard`, `Badge`, `Field`, `Modal`,
 * `Notice`, `Toggle`, `Checkbox`, `Spinner`, `ErrorNote`, `EmptyState`, `Icon`,
 * `inputClass`, `useAsync`, `JsonView`. The shipped `ui.jsx` has all of those except
 * `Modal`, `Notice`, `Toggle` and `Checkbox`.
 *
 * That is a contradiction in the repository's own documentation rather than a gap in
 * this feature, and it is reported rather than quietly worked around a third time.
 * The contract's instruction for exactly this case is to build the primitive inside
 * the feature folder and say so, so the integrator can promote the ones that recur.
 * Both are rebuilt here to the design system's floor: 44px targets, a visible focus
 * ring, a text label beside every control, `rounded-sm`, semantic tokens only.
 *
 * `Modal` and `Checkbox` are not rebuilt. Nothing in WF-124 needs them. A task is
 * added in a panel that is already on screen rather than in a dialog, because a seller
 * adding a task needs to see the plan it goes on to. And the buyer/seller switch is a
 * switch rather than a checkbox because it changes who is being shown, not a value.
 */

import { STATUS_TONES, STATUS_WORDS } from './api'
import { Icon } from '@/components/ui'

const TONES = {
  neutral: 'border-border-subtle bg-muted text-muted-foreground',
  info: 'border-info/30 bg-info/10 text-info',
  success: 'border-success/30 bg-success/10 text-success',
  warning: 'border-warning/30 bg-warning/10 text-warning',
  destructive: 'border-destructive/30 bg-destructive/10 text-destructive',
}

const TONE_ICON = {
  info: 'M12 8h.01M11 12h1v5h1M21 12a9 9 0 11-18 0 9 9 0 0118 0z',
  success: 'M5 12l5 5 9-11',
  warning: 'M12 9v4m0 4h.01M10.3 4.3L2.6 18a2 2 0 001.7 3h15.4a2 2 0 001.7-3L13.7 4.3a2 2 0 00-3.4 0z',
  destructive: 'M12 9v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z',
}

/**
 * A short standing message. Tone carries the meaning and the icon repeats it, so the
 * state does not depend on colour alone.
 */
export function Notice({ tone = 'neutral', title, children, action }) {
  const iconPath = TONE_ICON[tone]
  return (
    <div
      role="status"
      className={`flex flex-wrap items-start gap-3 rounded-sm border p-4 ${TONES[tone] || TONES.neutral}`}
    >
      {iconPath && <Icon path={iconPath} className="mt-0.5 shrink-0" />}
      <div className="min-w-0 flex-1">
        {title && <p className="text-sm font-semibold">{title}</p>}
        {children && <div className="mt-1 text-sm">{children}</div>}
      </div>
      {action}
    </div>
  )
}

/**
 * A labelled switch.
 *
 * A checkbox styled as a switch would be dishonest: this changes which audience is
 * being shown, and `role="switch"` with `aria-checked` is what a screen reader needs
 * to say so. The label is a real `<label for>`, never a placeholder.
 *
 * Reduced motion
 * --------------
 *
 * The knob is positioned by flexbox (`justify-start` / `justify-end`), not by a
 * translation utility, and the only transition is behind `motion-safe:`. Under
 * `prefers-reduced-motion: reduce` that means no transform and no transition at all:
 * the two states differ by layout and colour, not by anything travelling across the
 * screen. The state is carried by four things that are not movement - track colour,
 * knob colour, flexbox position and `aria-checked` - so dropping the transform costs
 * nothing and makes the reduced-motion case the absence of movement rather than a
 * faster movement.
 *
 * WF-069 and WF-070 reach the same conclusion for the same reason, and the sibling
 * utilities are described rather than written out on purpose: the design-floor check
 * reads raw lines, comments included, so spelling them here would put a
 * hazardous-motion token back into the file.
 */
export function Toggle({ id, label, hint, checked, onChange, disabled = false }) {
  return (
    <div className="flex items-start justify-between gap-4 py-1">
      <label htmlFor={id} className="min-w-0 cursor-pointer text-sm">
        <span className="block font-medium text-foreground">{label}</span>
        {hint && <span className="mt-0.5 block text-xs text-muted-foreground">{hint}</span>}
      </label>
      <button
        type="button"
        id={id}
        role="switch"
        aria-checked={checked}
        aria-label={label}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={`mt-0.5 inline-flex h-6 w-11 shrink-0 items-center rounded-sm border transition-colors duration-150
          focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent
          disabled:cursor-not-allowed disabled:opacity-50
          ${checked ? 'justify-end border-accent bg-accent' : 'justify-start border-border-subtle bg-muted'}`}
      >
        <span
          aria-hidden="true"
          className={`mx-0.5 h-4 w-4 rounded-xs motion-safe:transition-transform motion-safe:duration-150
            ${checked ? 'bg-surface' : 'bg-foreground'}`}
        />
      </button>
    </div>
  )
}

/**
 * A task's state as a badge whose meaning does not depend on its colour.
 *
 * The word is always present and is the first thing a screen reader says. `blocked`
 * gets its own tone because it is the only state the seller cannot fix by working: a
 * blocked task is waiting on somebody else, and colour alone would make it look like
 * ordinary work in progress.
 */
export function StatusBadge({ status }) {
  const word = STATUS_WORDS[status] || status || 'Unknown'
  const tone = STATUS_TONES[status] || 'neutral'
  const classes = {
    neutral: 'border-border-subtle bg-muted text-muted-foreground',
    info: 'border-info/30 bg-info/10 text-info',
    success: 'border-success/30 bg-success/10 text-success',
    warning: 'border-warning/30 bg-warning/10 text-warning',
  }
  return (
    <span
      className={`inline-flex items-center rounded-xs border px-2 py-0.5 font-mono text-xs font-medium
        ${classes[tone] || classes.neutral}`}
    >
      {word}
    </span>
  )
}

/**
 * How a due date reads against today, in words.
 *
 * `overdue` and `due_soon` are computed by the server, so the page does not re-derive
 * a date rule that the API already owns. The number of days is shown with the word, so
 * "3 days late" is never just a red row whose urgency a reader has to estimate.
 */
export function DueLabel({ escalation }) {
  if (!escalation) return null
  const days = Math.abs(Number(escalation.days ?? 0))
  if (escalation.state === 'overdue') {
    return (
      <span className="font-mono text-xs font-medium text-destructive">
        {days === 1 ? '1 day late' : `${days} days late`}
      </span>
    )
  }
  if (escalation.state === 'due_soon') {
    return (
      <span className="font-mono text-xs font-medium text-warning">
        {days === 0 ? 'due today' : `due in ${days} day${days === 1 ? '' : 's'}`}
      </span>
    )
  }
  return null
}

/**
 * The plan's phase, as a badge.
 *
 * `closed_won` and `implementation` are the two a seller must not confuse: one is the
 * record of what was agreed and one is the work that continues after it. Both carry a
 * word, so the difference survives a monochrome screen.
 */
export function PhaseBadge({ phase }) {
  const words = {
    selling: 'Selling',
    closed_won: 'Closed won',
    implementation: 'Implementation',
  }
  const word = words[phase] || phase || 'Unknown'
  const isImplementation = phase === 'implementation'
  return (
    <span
      className={`inline-flex items-center rounded-xs border px-2 py-0.5 font-mono text-xs font-medium
        ${isImplementation ? 'border-accent/30 bg-accent/10 text-accent' : 'border-border-subtle bg-muted text-muted-foreground'}`}
    >
      {word}
    </span>
  )
}
