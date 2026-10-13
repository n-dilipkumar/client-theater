import { Badge, Field, inputClass } from '@/components/ui'

import { statusLabel, statusTone, tierLabel } from './api'

/**
 * Local primitives for WF-118, built here rather than added to `ui.jsx`.
 *
 * `components/ui.jsx` is shared by every feature, and a feature that edits it
 * collides with every other feature. The design system says to build what is
 * genuinely missing inside the feature folder and to say so, so this is that
 * note.
 *
 * Missing upstream: a labelled `Select`, a labelled number input, and a compact
 * tier badge. All three are built in the design system's own tokens and shape,
 * at the same accessibility floor (44px targets, a visible label, a text label
 * beside every icon). If a second feature needs the same control, it belongs in
 * `ui.jsx` as platform work, promoted once by the integrator.
 */

/** A labelled select with a 44px target and a real `<select>` element. */
export function Select({ id, label, hint, value, onChange, options, disabled }) {
  return (
    <Field label={label} hint={hint} id={id}>
      <select
        id={id}
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        className={`${inputClass} appearance-none disabled:cursor-not-allowed disabled:opacity-50`}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </Field>
  )
}

/** A labelled number input with a 44px target. */
export function NumberField({ id, label, hint, value, onChange, min, max, step = 1, disabled }) {
  return (
    <Field label={label} hint={hint} id={id}>
      <input
        id={id}
        type="number"
        inputMode="numeric"
        value={value}
        min={min}
        max={max}
        step={step}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        className={`${inputClass} font-mono disabled:cursor-not-allowed disabled:opacity-50`}
      />
    </Field>
  )
}

/**
 * The two-tier position of a guide.
 *
 * The label is the tier; the tone only reinforces it, so the tier is never
 * carried by colour alone.
 */
export function TierBadge({ tier }) {
  return <Badge tone={tier === 0 ? 'update' : 'neutral'}>{tierLabel(tier)}</Badge>
}

/** A guide's staging/public status, with the label beside the tone. */
export function StatusBadge({ status }) {
  return <Badge tone={statusTone(status)}>{statusLabel(status)}</Badge>
}

export { Badge, Field, inputClass }
