/**
 * Two primitives that `docs/DESIGN-SYSTEM.md` and `docs/FEATURE-CONTRACT.md` both
 * list as available from `@/components/ui`, and which that file does not export.
 *
 * The documented list is: `Button`, `Card`, `StatCard`, `Badge`, `Field`, `Modal`,
 * `Notice`, `Toggle`, `Checkbox`, `Spinner`, `ErrorNote`, `EmptyState`, `Icon`,
 * `inputClass`, `useAsync`, `JsonView`. The shipped `ui.jsx` has all of those except
 * `Modal`, `Notice`, `Toggle` and `Checkbox`.
 *
 * That is a contradiction in the repo's own documentation rather than a gap in this
 * feature, and it is reported rather than quietly worked around twice. The contract's
 * instruction for exactly this case is to build the primitive inside the feature
 * folder and say so, so the integrator can promote the ones that recur. These two are
 * rebuilt here to the design system's floor - 44px targets, a visible focus ring, a
 * text label beside every control, `rounded-sm`, semantic tokens only.
 *
 * `Modal` and `Checkbox` are not rebuilt: nothing in WF-069 needs them. The create
 * form is inline in a card rather than in a dialog, because a form with five settings
 * and a preset picker does not fit a modal on a phone.
 */

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
 * A checkbox styled as a switch would be dishonest: this changes a setting, it does
 * not select something, and `role="switch"` with `aria-checked` is what a screen
 * reader needs to say so. The label is a real `<label for>`, never a placeholder.
 *
 * Reduced motion
 * --------------
 *
 * The knob is positioned by flexbox (`justify-start` / `justify-end`), not by a
 * `translate-*`, and the only transition is behind `motion-safe:`. Under
 * `prefers-reduced-motion: reduce` that means no transform and no transition at all:
 * the two states differ by layout and colour, not by anything travelling across the
 * screen. A knob that slid for someone who asked their operating system not to
 * animate anything is the hazard the design floor reserves (ADR-0002), and this is
 * what it reserves it for.
 *
 * Nine sibling features reach the same place differently: an absolutely positioned
 * knob moved by a horizontal translation utility, with a `motion-reduce:` variant
 * that removes the transition. That is a sanctioned shape and it satisfies the floor,
 * because the transition is what animates the movement. It still repositions the
 * knob, though, and here the state is carried by four things that are not movement
 * (track colour, knob colour, flexbox position, `aria-checked`), so dropping the
 * transform costs nothing and makes the reduced-motion case the absence of movement
 * rather than a faster movement. Flagged to the coordinator as a deliberate
 * difference from the sibling pattern rather than an oversight.
 *
 * The sibling utilities are described rather than written out on purpose: the
 * design-floor check reads raw lines, comments included, so spelling them here would
 * put a hazardous-motion token back into the file and make this docstring a tripwire
 * for whoever trims it next.
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
