import { Badge } from '@/components/ui'

import { stateLabel, stateTone } from './api'

/**
 * Local primitives for WF-119, built here rather than added to `ui.jsx`.
 *
 * `components/ui.jsx` is shared by every feature, and a feature that edits it
 * collides with every other feature. The design system says to build what is
 * genuinely missing inside the feature folder and to say so, so this is that
 * note.
 *
 * Missing upstream: a state badge whose label and tone travel together, a small
 * inline notice, and a labelled switch. All three use the design system's own
 * tokens and shape, at the same accessibility floor: 44px targets, a visible
 * label, and a text label beside every icon. If a second feature needs one, it
 * belongs in `ui.jsx` as platform work, promoted once by the integrator.
 */

/**
 * The state of a hold.
 *
 * Expiry is read before the stored state, because the server derives it lazily:
 * a hold past its window is expired even while its stored state still reads
 * `held`. The label carries the state and the tone only reinforces it, so state
 * is never colour alone.
 */
export function StateBadge({ hold }) {
  return <Badge tone={stateTone(hold)}>{stateLabel(hold)}</Badge>
}

/** A small inline notice. The title carries the tone; the body stays readable. */
export function Notice({ tone = 'info', title, children }) {
  const tones = {
    info: 'border-info/30 bg-info/10 text-info',
    warning: 'border-warning/30 bg-warning/10 text-warning',
    destructive: 'border-destructive/30 bg-destructive/10 text-destructive',
  }
  return (
    <div
      role="status"
      className={`rounded-sm border p-4 ${tones[tone] || tones.info}`}
    >
      {title && <p className="text-sm font-semibold">{title}</p>}
      <div className={`text-sm ${title ? 'mt-1' : ''} text-foreground/90`}>{children}</div>
    </div>
  )
}

/**
 * A labelled switch, built on a real `<button role="switch">`.
 *
 * A switch rather than a checkbox because the control acts immediately and the
 * label states what the switch turns on. `aria-checked` carries the state to
 * assistive tech, and the visible word "On"/"Off" carries it to everyone else,
 * so the position of the control is never the only signal.
 */
export function Toggle({ id, label, hint, checked, onChange, disabled = false }) {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-[13px] font-medium text-foreground" id={`${id}-label`}>
        {label}
      </span>
      <div className="flex items-center gap-3">
        <button
          type="button"
          id={id}
          role="switch"
          aria-checked={checked}
          aria-labelledby={`${id}-label`}
          disabled={disabled}
          onClick={() => onChange(!checked)}
          className={`inline-flex min-h-11 items-center gap-2 rounded-sm border px-4 text-sm
            disabled:cursor-not-allowed disabled:opacity-50
            ${
              checked
                ? 'border-accent/30 bg-accent/10 text-accent'
                : 'border-border-subtle bg-surface text-muted-foreground'
            }`}
        >
          <span className="font-mono text-xs">{checked ? 'ON' : 'OFF'}</span>
          <span>{checked ? 'On' : 'Off'}</span>
        </button>
        {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
      </div>
    </div>
  )
}

/** A label/value row for machine values, which are set in mono. */
export function ValueRow({ label, value, mono = false, title }) {
  return (
    <div className="flex flex-col gap-0.5 py-1.5">
      <span className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
        {label}
      </span>
      <span
        title={title}
        className={`break-all text-sm text-foreground ${mono ? 'font-mono text-[13px]' : ''}`}
      >
        {value}
      </span>
    </div>
  )
}

/** A URL the app serves, labelled so the reader knows which resume URL it is. */
export function UrlRow({ label, url }) {
  return <ValueRow label={label} value={url} mono title={url} />
}

export { Badge }
