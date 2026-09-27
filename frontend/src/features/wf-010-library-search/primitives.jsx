/**
 * Two primitives this page needs that `components/ui.jsx` does not carry.
 *
 * The branch added `Checkbox` and `ChoiceGroup` to the shared `ui.jsx`, plus a
 * `className` prop on `Field` and six new entries in the `PATHS` icon map. All of
 * that file is shared, and twelve features each appending to it is the same
 * collision the feature host removes. The contract's answer is explicit: build
 * the primitive inside the feature folder and say so in the PR description, so an
 * integrator can promote the recurring ones into `ui.jsx` once, as platform work.
 *
 * These are therefore this feature's private copies. They are behaviourally the
 * branch's versions - a real `<input>` in both cases, so keyboard and screen
 * reader support come for free, and a 44px minimum target in both. What changed is
 * ownership, and that they are not yet on anyone's critical path.
 */

import { Field, Icon } from '@/components/ui'

/**
 * Checkbox with a real input, so keyboard and screen readers get it for free.
 * The label wraps the control, giving the whole row the 44px target.
 */
export function Checkbox({ label, hint, checked, onChange, disabled = false, className = '' }) {
  return (
    <label
      className={`flex min-h-11 cursor-pointer items-center gap-3 rounded-lg px-2 text-sm
        transition-colors duration-150 hover:bg-muted/40 ${disabled ? 'opacity-50' : ''} ${className}`}
    >
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
        className="h-4 w-4 shrink-0 accent-[var(--color-accent)]"
      />
      <span className="min-w-0">
        <span className="text-foreground">{label}</span>
        {hint && <span className="block text-xs text-muted-foreground">{hint}</span>}
      </span>
    </label>
  )
}

/**
 * A small labelled set of mutually exclusive options, as a radio group.
 * Used for and/or, asc/desc, and anything else the API models as a choice.
 */
export function ChoiceGroup({ legend, options, value, onChange, name, className = '' }) {
  return (
    <fieldset className={className}>
      <legend className="mb-1.5 text-xs font-medium text-muted-foreground">{legend}</legend>
      <div className="flex flex-wrap gap-1">
        {options.map((option) => {
          const active = value === option.value
          return (
            <label
              key={option.value}
              className={`flex min-h-11 cursor-pointer items-center rounded-lg px-3 text-sm
                transition-colors duration-150 ${active
                  ? 'bg-accent/15 font-medium text-accent'
                  : 'text-muted-foreground hover:bg-muted hover:text-foreground'}`}
            >
              <input
                type="radio"
                name={name}
                value={option.value}
                checked={active}
                onChange={() => onChange(option.value)}
                className="sr-only"
              />
              {option.label}
            </label>
          )
        })}
      </div>
    </fieldset>
  )
}

/**
 * `Field` with a className, which the shared one does not take.
 *
 * The branch widened the shared `Field` for exactly this. Rather than fork the
 * whole component for one prop, this wraps it: the label/hint wiring, the `htmlFor`
 * association and the 44px control height all still come from the shared primitive,
 * so there is one `Field` in the product rather than two that drift.
 */
export function SpacedField({ className = '', ...props }) {
  return (
    <div className={className}>
      <Field {...props} />
    </div>
  )
}

/** A decorative glyph from this feature's own icon set. */
export function Glyph({ name, size = 18, className = '' }) {
  return <Icon path={name} size={size} className={className} />
}
