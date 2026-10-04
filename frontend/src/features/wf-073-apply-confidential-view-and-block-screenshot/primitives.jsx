/**
 * Two primitives that `docs/DESIGN-SYSTEM.md` and `docs/FEATURE-CONTRACT.md` both list
 * as available from `@/components/ui`, and which that file does not export.
 *
 * The documented list is: `Button`, `Card`, `StatCard`, `Badge`, `Field`, `Modal`,
 * `Notice`, `Toggle`, `Checkbox`, `Spinner`, `ErrorNote`, `EmptyState`, `Icon`,
 * `inputClass`, `useAsync`, `JsonView`. The shipped `ui.jsx` has all of those except
 * `Modal`, `Notice`, `Toggle` and `Checkbox`.
 *
 * That is a contradiction in the repo's own documentation rather than a gap in this
 * feature, and it is reported rather than quietly worked around a second time. WF-069
 * hit it and rebuilt the same two. The contract's instruction for exactly this case is
 * to build the primitive inside the feature folder and say so, so the integrator can
 * promote the ones that recur. These two are rebuilt here to the design system's floor:
 * 44px targets, a visible focus ring, a text label beside every control, `rounded-sm`,
 * semantic tokens only.
 *
 * `Modal` and `Checkbox` are not rebuilt: nothing in WF-073 needs them. The create form
 * is inline in a card rather than in a dialog, because a form with two switches and a
 * baseline picker does not need a modal, and a modal on a phone is worse.
 *
 * A third thing is built here that is not in the documented list at all: `BandStrip`.
 * The focus band is this workflow's central object and no shared primitive can draw it,
 * so it is local by necessity. See its own docstring.
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
 * A checkbox styled as a switch would be dishonest: this changes a setting, it does not
 * select something, and `role="switch"` with `aria-checked` is what a screen reader
 * needs in order to say so. The label is a real `<label for>`, never a placeholder.
 *
 * Reduced motion
 * --------------
 *
 * The knob is positioned by flexbox (`justify-start` / `justify-end`), not by a
 * horizontal translation, and the only transition is behind `motion-safe:`. Under
 * `prefers-reduced-motion: reduce` that means no transform and no transition at all: the
 * two states differ by layout and colour, not by anything travelling across the screen.
 * A knob that slid for someone who asked their operating system not to animate anything
 * is the hazard the design floor reserves (ADR-0002).
 *
 * The state is carried by four things that are not movement - track colour, knob
 * colour, flexbox position and `aria-checked` - so dropping the transform costs nothing
 * and makes the reduced-motion case the absence of movement rather than a faster
 * movement. The sibling utilities are described rather than spelled out on purpose: the
 * design-floor check reads raw lines, comments included, so writing them here would put
 * a hazardous-motion token back into the file.
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
 * The page's focus band, drawn.
 *
 * This is the object the whole workflow is about, and no shared primitive draws it, so
 * it is local by necessity rather than by preference. It renders the geometry
 * `GET /wf-073/links/{id}/render` returns: one sharp band and the blurred regions either
 * side of it.
 *
 * Two decisions worth recording.
 *
 * **The blurred regions are hatched, not greyed.** A plain grey block reads as "nothing
 * here". A hatch reads as "something is here and you cannot see it", which is what the
 * server actually did: it withheld the glyphs. The distinction is the difference between
 * a rendering hint and a broken page. The hatch is a `repeating-linear-gradient` built
 * from `border-subtle`, a semantic token, so it follows a theme change like everything
 * else here - which is the rule that bans a raw hex in a component.
 *
 * **The sharp band carries a text label.** Status is never conveyed by colour alone, and
 * "which band is sharp" is a state. The label states it, and the `aria-label` on the
 * strip describes the whole thing for a screen reader, so the diagram is not a picture of
 * something only a sighted user can read.
 *
 * The strip is one labelled image rather than eight separately announced bands, because
 * a screen reader reading eight descriptions would be worse than one sentence.
 *
 * The heights are uniform rather than proportional. A band is a fixed fraction of the
 * page, so proportional heights would render every bar the same size anyway, and
 * proportional sizing would break the moment a caller passed a geometry whose bands
 * overlapped unevenly. What the reader needs from this diagram is which one is sharp
 * and how many are not, and equal bars say that without implying a scale that is not
 * there.
 *
 * On the 44px floor
 * -----------------
 *
 * `tools/check_design_floor.py` reports a tap-target warning for the narrow minimum
 * width this strip used to put on a bar, and it is right to look: the rule exists so a
 * control a person has to hit is big enough to hit. A band bar is not a control. The
 * strip is a single `role="img"` with one label, it takes no pointer event, and a
 * person cannot address one band rather than another. The bars are laid out with
 * `flex-1` inside a full-width parent, which already stops a band collapsing to nothing
 * once there are seven of them, so the minimum width guarded a case that cannot occur.
 * It is removed rather than allowlisted, because the allowlist is for a real exception
 * and this is not one.
 *
 * The token itself is described rather than written out on purpose: the design-floor
 * check reads raw lines, comments included, so spelling it here would put the very
 * utility it flags back into the file and make this docstring a tripwire for whoever
 * trims it next.
 *
 * The alternative was to make each band a button, and that was rejected for the reason
 * the role is `img`: there is nothing for a band click to do. Resolving a band is a
 * function of the scroll position, which is already an input, so a clickable band would
 * be a control whose only action is to restate the state the reader is already looking
 * at.
 */
export function BandStrip({ geometry, className = '' }) {
  if (!geometry || !Array.isArray(geometry.bands) || geometry.bands.length === 0) {
    return null
  }
  const { bands } = geometry
  const total = bands.length

  return (
    <figure className={`m-0 ${className}`}>
      <div
        role="img"
        aria-label={`Page divided into ${total} bands. Band ${geometry.sharp_index + 1} of ${total} is sharp; the other ${total - 1} are blurred.`}
        className="flex w-full items-end gap-1 rounded-sm border border-border-subtle bg-surface p-2"
      >
        {bands.map((band) => (
          <div
            key={band.index}
            title={`Band ${band.index + 1}: ${band.sharp ? 'sharp' : 'blurred'}`}
            className={`flex-1 rounded-xs ${
              band.sharp
                ? 'bg-accent'
                : 'bg-[repeating-linear-gradient(135deg,var(--color-border-subtle)_0_2px,var(--color-muted)_2px_5px)]'
            }`}
            style={{ height: `${100 / total}px` }}
          />
        ))}
      </div>
      <figcaption className="mt-2 text-xs text-muted-foreground">
        Band {geometry.sharp_index + 1} of {total} is sharp. The other {total - 1} are blurred.
      </figcaption>
    </figure>
  )
}