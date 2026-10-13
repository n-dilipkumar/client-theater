import { Badge, Field, inputClass } from '@/components/ui'

import { stateLabel, stateTone } from './api'

/**
 * Local primitives for WF-115, built here rather than added to `ui.jsx`.
 *
 * `components/ui.jsx` is shared by every feature, and a feature that edits it
 * collides with every other feature. The design system says to build what is
 * genuinely missing inside the feature folder and to say so, so this is that
 * note.
 *
 * Missing upstream: a labelled `Select` and a labelled `Textarea`. Both are
 * built in the design system's own tokens and shape, at the same accessibility
 * floor (44px targets, a visible label, a text label beside every icon). If a
 * second feature needs the same control, it belongs in `ui.jsx` as platform
 * work, promoted once by the integrator.
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

/** A labelled multi-line input for JSON or prose. */
export function Textarea({ id, label, hint, value, onChange, rows = 5, spellCheck = false }) {
  return (
    <Field label={label} hint={hint} id={id}>
      <textarea
        id={id}
        value={value}
        rows={rows}
        spellCheck={spellCheck}
        onChange={(event) => onChange(event.target.value)}
        className={`${inputClass} py-2 font-mono text-[13px]`}
      />
    </Field>
  )
}

/**
 * A review-state badge in words.
 *
 * The label is the status; the tone only reinforces it, so the state is never
 * carried by colour alone.
 */
export function StateBadge({ state }) {
  return <Badge tone={stateTone(state)}>{stateLabel(state)}</Badge>
}

/** One labelled endpoint row, with the note that it is recorded, not called. */
export function EndpointRow({ label, value, note }) {
  return (
    <div className="rounded-sm border border-border-subtle bg-surface p-3">
      <p className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">{label}</p>
      <p className="mt-1 break-all font-mono text-[13px] text-foreground">{value}</p>
      {note && <p className="mt-1 text-xs text-muted-foreground">{note}</p>}
    </div>
  )
}

export { Badge, Field, inputClass }
