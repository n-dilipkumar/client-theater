/**
 * Two primitives this feature needs that `components/ui.jsx` does not carry.
 *
 * The branch added `Notice` and `Checkbox` to the shared file, which is the one
 * thing a feature may not do. They are built here instead, and that is a finding
 * for the integrator rather than a preference: `Notice` in particular is generic
 * enough that the next workflow will want it too, and twelve features each
 * shipping a subtly different inline notice is the same fragmentation problem
 * twelve workflows appending to one routes array was. Promoting these two into
 * `ui.jsx` is a one-off platform change, and it belongs to whoever owns that
 * file.
 *
 * The contract they meet, from the design system: a 44px minimum touch target,
 * a visible focus ring (inherited globally from `index.css`), a text label
 * beside every glyph so meaning survives with icons off, no emoji, and no motion
 * beyond the global `prefers-reduced-motion` rule.
 */

import { Icon } from '@/components/ui'

import { ICONS } from './icons'

/**
 * Draw one of this feature's glyphs.
 *
 * Wraps the shared `Icon` with `path=` so the name is resolved here rather than
 * in the shared `PATHS` map. A name this feature does not define falls through
 * to the shared set, so `close` and the other glyphs the product already has are
 * not carried a second time.
 */
export function Glyph({ name, size = 18, className = '' }) {
  return <Icon name={name} path={ICONS[name]} size={size} className={className} />
}

/**
 * Inline notice for a message the user needs to act on.
 *
 * `tone` picks the semantic colour and `icon` the glyph. The title is always
 * text, never an icon on its own, so the meaning survives with icons off.
 */
export function Notice({ tone = 'info', title, children, icon, onDismiss }) {
  const tones = {
    info: 'border-sky-500/40 bg-sky-500/10 text-sky-200',
    success: 'border-accent/40 bg-accent/10 text-accent',
    warning: 'border-amber-500/40 bg-amber-500/10 text-amber-200',
    danger: 'border-destructive/40 bg-destructive/10 text-destructive',
  }
  // A warning or a failure is announced; a confirmation is not. Both would be
  // noise if every notice interrupted whatever the user was doing.
  const role = tone === 'danger' || tone === 'warning' ? 'alert' : 'status'
  return (
    <div role={role} className={`rounded-lg border p-4 ${tones[tone] || tones.info}`}>
      <div className="flex items-start gap-3">
        {icon && (
          <span className="mt-0.5 shrink-0">
            <Glyph name={icon} />
          </span>
        )}
        <div className="min-w-0 flex-1">
          {title && <p className="text-sm font-semibold">{title}</p>}
          <div className="mt-0.5 text-sm text-muted-foreground">{children}</div>
        </div>
        {onDismiss && (
          <button
            type="button"
            onClick={onDismiss}
            className="flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-lg text-muted-foreground transition-colors duration-150 hover:bg-muted hover:text-foreground"
          >
            <span className="sr-only">Dismiss</span>
            <Glyph name="close" size={16} />
          </button>
        )}
      </div>
    </div>
  )
}

/**
 * A labelled checkbox with a 44px target.
 *
 * A switch would be the honest control for a boolean that changes future
 * behaviour rather than current state, but this is built from a real
 * `<input type="checkbox">` so the keyboard and screen-reader behaviour comes
 * from the platform rather than from a re-implementation.
 */
export function Checkbox({ id, label, hint, checked, onChange, disabled }) {
  return (
    <div className="flex flex-col gap-1.5">
      <label
        htmlFor={id}
        className={`flex min-h-11 cursor-pointer items-center gap-3 text-sm ${disabled ? 'opacity-60' : ''}`}
      >
        <input
          id={id}
          type="checkbox"
          checked={checked}
          disabled={disabled}
          onChange={(event) => onChange(event.target.checked)}
          className="h-5 w-5 shrink-0 cursor-pointer accent-[#22c55e]"
        />
        <span className="text-foreground">{label}</span>
      </label>
      {hint && <p className="pl-8 text-xs text-muted-foreground/80">{hint}</p>}
    </div>
  )
}
