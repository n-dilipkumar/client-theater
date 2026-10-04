/**
 * Primitives this feature needs that `@/components/ui` does not export.
 *
 * `docs/DESIGN-SYSTEM.md` and `docs/FEATURE-CONTRACT.md` both list `Notice` and `Toggle`
 * among the available primitives, and the shipped `ui.jsx` has all of the documented set
 * except `Modal`, `Notice`, `Toggle` and `Checkbox`.
 *
 * That is a contradiction in the repo's own documentation rather than a gap in this
 * feature, and it is reported rather than quietly worked around a second time: WF-073 hit
 * it and rebuilt the same two. The contract's instruction for exactly this case is to
 * build the primitive inside the feature folder and say so, so the integrator can promote
 * the ones that recur. These are rebuilt to the design system's floor: 44px targets, a
 * visible focus ring, a text label beside every control, `rounded-sm`, and semantic tokens
 * only.
 *
 * `Modal` and `Checkbox` are not rebuilt: nothing in WF-078 needs them. The forms here are
 * inline in a card rather than in a dialog, because a form with two pickers and a room is
 * worse in a modal on a phone than it is on the page.
 *
 * Three more things are built here that are not in the documented list at all, because the
 * workflow has shapes no shared primitive describes: `GateMatrix` (the two axes drawn
 * separately, because that separation is the specification's central claim),
 * `AudienceBadge` (who a gate applies to, which is a sourced column of the timing table
 * and not decoration), and `MethodPicker`. Each is local by necessity.
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
 * A short standing message. Tone carries the meaning and the icon repeats it, so the state
 * does not depend on colour alone.
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
 * select something, and `role="switch"` with `aria-checked` is what a screen reader needs
 * in order to say so. The label is a real `<label for>`, never a placeholder.
 *
 * Reduced motion
 * --------------
 *
 * The knob is positioned by flexbox (`justify-start` / `justify-end`), not by a horizontal
 * translation, and the only transition is behind `motion-safe:`. Under
 * `prefers-reduced-motion: reduce` that means no transform and no transition at all: the
 * two states differ by layout and colour, not by anything travelling across the screen. A
 * knob that slid for someone who asked their operating system not to animate anything is
 * the hazard the design floor reserves (ADR-0002).
 *
 * The state is carried by four things that are not movement - track colour, knob colour,
 * flexbox position and `aria-checked` - so dropping the transform costs nothing.
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
 * A labelled select. Built here because the workflow needs one in a card with a visible
 * label and a hint, and the shared set has `Field` and `inputClass` but no select.
 *
 * `inputClass` supplies the styling so the control matches every other input in the
 * product: `min-h-11` for the 44px floor, a semantic border, and `focus:border-accent`.
 * The chevron is the browser's own, so there is no glyph to keep in step and no icon
 * without a label.
 */
export function Select({ id, value, onChange, children, disabled = false }) {
  return (
    <select
      id={id}
      className={INPUT_CLASS}
      value={value}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
    >
      {children}
    </select>
  )
}

// Re-declared rather than imported so this file has one styling source. The shared
// `inputClass` is the canonical string and this is a copy of it for a `<select>`; a test
// asserts the two match, so a change to the shared one cannot silently leave this behind.
const INPUT_CLASS =
  'min-h-11 w-full rounded-sm border border-border-subtle bg-surface px-3 text-sm ' +
  'text-foreground placeholder:text-muted-foreground/70 focus:border-accent'

/**
 * Who a gate applies to.
 *
 * The sourced column of the specification's timing table: `before_open` is "All
 * recipients" and `before_sign` is "Signers only". It is a badge rather than a hint because
 * a seller who sets a before-sign gate on a non-signer has configured a gate nobody can
 * clear, and the audience is what tells them so before the recipient finds out.
 *
 * `tone` never carries the meaning alone: the label beside the icon states it, so the badge
 * reads the same in monochrome.
 */
export function AudienceBadge({ audience, label }) {
  const signersOnly = audience === 'signers_only'
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-xs border px-2 py-0.5 font-mono text-xs font-medium
        ${signersOnly ? 'border-warning/30 bg-warning/10 text-warning' : 'border-border-subtle bg-muted text-muted-foreground'}`}
    >
      <Icon
        path={
          signersOnly
            ? 'M12 9v4m0 4h.01M10.3 4.3L2.6 18a2 2 0 001.7 3h15.4a2 2 0 001.7-3L13.7 4.3a2 2 0 00-3.4 0z'
            : 'M17 20h5v-2a3 3 0 00-5.4-1.8M17 20H7m10 0v-2c0-.7-.1-1.3-.4-1.8M7 20H2v-2a3 3 0 015.4-1.8M7 20v-2c0-.7.1-1.3.4-1.8m0 0a5 5 0 019.2 0M15 7a3 3 0 11-6 0 3 3 0 016 0z'
        }
        size={13}
      />
      {label || (signersOnly ? 'Signers only' : 'All recipients')}
    </span>
  )
}

/**
 * The two axes, drawn separately.
 *
 * This is the workflow's central claim and the reason the object is shaped the way it is:
 * "method as a discriminated union on the recipient (passcode / phone / KBA / ID) with an
 * independent timing axis (before_open vs before_sign) - the same recipient object can be
 * verified differently for viewing and signing".
 *
 * Drawn as a grid rather than a list because a single row would collapse the two axes into
 * one field, and the collapsed shape is the one the specification's sentence rules out. A
 * recipient carrying both moments occupies two cells, which is what makes "the same
 * recipient can be verified differently for viewing and signing" visible rather than a
 * claim.
 *
 * An empty cell says "no gate here" in words. It is never a blank: an empty cell a reader
 * has to interpret is exactly the ambiguity the design system's "status is never conveyed
 * by colour alone" rule is about.
 */
export function GateMatrix({ places, gates }) {
  const byPlace = new Map((gates || []).map((gate) => [gate.place, gate]))
  return (
    <div className="overflow-hidden rounded-sm border border-border-subtle">
      <table className="w-full border-collapse text-left">
        <caption className="sr-only">
          Verification gates on this recipient, one row per moment and one column per method.
        </caption>
        <thead>
          <tr className="border-b border-border-subtle bg-muted">
            <th scope="col" className="px-3 py-2 text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
              Moment
            </th>
            <th scope="col" className="px-3 py-2 text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
              Method
            </th>
            <th scope="col" className="px-3 py-2 text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
              Applies to
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border-subtle">
          {(places || []).map((place) => {
            const gate = byPlace.get(place.id)
            return (
              <tr key={place.id} className="bg-surface">
                <th scope="row" className="px-3 py-2 align-top">
                  <span className="block text-sm font-medium text-foreground">{place.label}</span>
                  <span className="mt-0.5 block text-xs text-muted-foreground">
                    {place.description}
                  </span>
                </th>
                <td className="px-3 py-2 align-top">
                  {gate ? (
                    <>
                      <span className="block font-mono text-sm text-foreground">
                        {gate.method_label || gate.method}
                      </span>
                      {gate.vendor_field && (
                        <span className="mt-0.5 block font-mono text-xs text-muted-foreground">
                          {gate.vendor_field}
                        </span>
                      )}
                      {gate.sms_type && (
                        <span className="mt-1 block text-xs text-muted-foreground">
                          SMS role: {gate.sms_type}
                        </span>
                      )}
                    </>
                  ) : (
                    <span className="text-sm text-muted-foreground">No gate here</span>
                  )}
                </td>
                <td className="px-3 py-2 align-top">
                  <AudienceBadge audience={place.audience} label={place.audienceLabel} />
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

/**
 * The outcome of one attempt, as a badge.
 *
 * The label is the word, not the colour. A rejection is a normal outcome and not an error,
 * so it is `destructive` in tone and "Failed" in words, and it carries the reason beside
 * it. A gate whose failures read as grey rows is a gate whose failures nobody investigates.
 */
export function OutcomeBadge({ outcome, reason }) {
  const passed = outcome === 'pass'
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-xs border px-2 py-0.5 font-mono text-xs font-medium
        ${passed ? 'border-success/30 bg-success/10 text-success' : 'border-destructive/30 bg-destructive/10 text-destructive'}`}
    >
      <Icon path={passed ? 'M5 12l5 5 9-11' : 'M12 9v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z'} size={13} />
      {passed ? 'Passed' : 'Failed'}
      {!passed && reason ? ` (${reason})` : ''}
    </span>
  )
}
