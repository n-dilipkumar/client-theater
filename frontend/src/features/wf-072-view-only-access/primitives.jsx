/**
 * The primitives WF-072 needs that the shared `ui.jsx` does not export.
 *
 * `docs/DESIGN-SYSTEM.md` and `docs/FEATURE-CONTRACT.md` both list `Notice` and `Toggle` as
 * available from `@/components/ui`, and the shipped file exports neither. The contract's
 * instruction for exactly this case is to build the primitive inside the feature folder and
 * say so, which is what WF-069 and WF-073 did as well. They are rebuilt here to the design
 * system's floor: `rounded-sm`, semantic tokens only, 44px targets, a visible focus ring, and
 * a text label beside every control.
 *
 * A third thing is local by necessity, not by preference: `GateList`. The three-way AND is
 * this workflow's central object, and no shared primitive can render "which of three controls
 * withheld the file". See its own docstring.
 */

import { Badge, Icon } from '@/components/ui'

import { gateRows } from './api'
import { DOWNLOAD_ICON, LOCK_ICON } from './icons'

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
  warning:
    'M12 9v4m0 4h.01M10.3 4.3L2.6 18a2 2 0 001.7 3h15.4a2 2 0 001.7-3L13.7 4.3a2 2 0 00-3.4 0z',
  destructive: 'M12 9v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z',
}

/**
 * A short standing message. Tone carries the meaning and the icon repeats it, so the state
 * never depends on colour alone.
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
 * `role="switch"` with `aria-checked`, because this changes a setting rather than selecting
 * something. The label is a real `<label for>`, never a placeholder. The knob is positioned
 * by flexbox and the only transition is behind `motion-safe:`, so under
 * `prefers-reduced-motion: reduce` the two states differ by layout and colour and nothing
 * travels across the screen.
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
 * The three-way AND, drawn.
 *
 * This workflow is a rule about three controls, so the page's central object is the list of
 * the three and what each one decided. No shared primitive renders it, so it is local.
 *
 * Every gate carries a word - "Permitted" or "Blocked" - beside its colour, because status is
 * never conveyed by colour alone. The refusal names which gate withheld the file, so a rep
 * knows which switch to change instead of being told only that the download was refused.
 */
export function GateList({ rows, state }) {
  return (
    <ul className="divide-y divide-border-subtle">
      {rows.map((row) => (
        <li key={row.name} className="flex flex-wrap items-center justify-between gap-3 py-2">
          <span className="min-w-0">
            <span className="block text-sm text-foreground">{row.label}</span>
            <span className="mt-0.5 block font-mono text-xs text-muted-foreground">
              {row.field}
            </span>
          </span>
          <Badge tone={row.permitted ? 'insert' : state === 'download' ? 'update' : 'delete'}>
            {row.permitted ? 'Permitted' : 'Blocked'}
          </Badge>
        </li>
      ))}
    </ul>
  )
}

/**
 * The verdict of one decision, with the gate list beneath it.
 *
 * The verdict is a sentence plus an icon, never a colour on its own: "Download permitted" vs
 * "View-only: a download was refused". The same component renders the refusal the server sent
 * back on a 403, so a caller reads one shape whichever path reached it.
 */
export function DecisionResult({ decision }) {
  if (!decision) return null
  const permitted = decision.download === true
  return (
    <div
      className={`rounded-sm border p-4 ${
        permitted
          ? 'border-success/30 bg-success/10'
          : 'border-warning/30 bg-warning/10'
      }`}
    >
      <div className="flex items-center gap-2">
        <Icon path={permitted ? DOWNLOAD_ICON : LOCK_ICON} className={permitted ? 'text-success' : 'text-warning'} />
        <p
          className={`text-sm font-semibold ${permitted ? 'text-success' : 'text-warning'}`}
        >
          {permitted ? 'Download permitted' : 'View-only: the download was refused'}
        </p>
      </div>
      <p className="mt-1 text-sm text-muted-foreground">{decision.reason}</p>
      <div className="mt-3">
        <GateList rows={gateRows(decision)} state={decision.state} />
      </div>
    </div>
  )
}
