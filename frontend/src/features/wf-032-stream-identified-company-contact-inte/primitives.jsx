/**
 * Glyphs and two primitives this feature needs, built here rather than added to
 * `components/ui.jsx`.
 *
 * `components/ui.jsx` is shared and a hundred features each appending to it is
 * the collision the feature host exists to prevent, so the intent glyph is passed
 * as a path through `<Icon path=...>` and the inline `Notice` is built in this
 * folder. Both are findings for the integrator: `Notice` in particular is generic
 * enough that the next workflow will want it too, and promoting it into `ui.jsx`
 * is a one-off platform change belonging to whoever owns that file. The same is
 * true of the `Modal` and the `Toggle` the feature contract lists - neither is in
 * the file, so the forms here are inline and the switches are real checkboxes
 * rather than a dialog.
 *
 * The floor they meet, from the design system: a 44px minimum touch target, a
 * visible focus ring (inherited globally from `index.css`), a text label beside
 * every glyph so meaning survives with icons off, no emoji, and no motion beyond
 * the global `prefers-reduced-motion` rule.
 */

import { Icon } from '@/components/ui'

/**
 * A company going out to somewhere: a building with a signal leaving it.
 *
 * The nav wants "identified intent, streamed out". That is not in the shared
 * `PATHS` map, which is why this folder defines it and `Icon` receives it as a
 * path. `INTENT_ICON` is the fallback for a consumer that reads only the shared
 * name.
 */
export const INTENT_ICON =
  'M3 21h18M5 21V8l5-3v16M14 21V11h5v10M8 11h.01M8 15h.01M17 15h.01M16.5 3.5l4.5-2.5M17 5h4v4'

const ICONS = { intent: INTENT_ICON }

/** Draw one of this feature's glyphs, falling through to the shared set. */
export function Glyph({ name, size = 18, className = '' }) {
  return <Icon name={name} path={ICONS[name]} size={size} className={className} />
}

/**
 * Inline notice for a message the user needs to act on.
 *
 * `tone` picks the semantic colour and `icon` the glyph. The title is always
 * text, never an icon on its own.
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
 * A labelled checkbox row that meets the touch-target floor on its own.
 *
 * The shared `Button` already carries `min-h-11`, so this only has to be a
 * checkbox people can actually hit: the whole row is the target, the label is
 * always visible, and the focus ring comes from the global rule rather than from
 * a class here that would be one more thing to keep in step.
 */
export function CheckRow({ checked, onChange, label, hint, disabled = false }) {
  return (
    <label
      className={`flex min-h-11 items-start gap-3 rounded-lg px-2 py-1.5 ${
        disabled ? 'opacity-50' : 'cursor-pointer hover:bg-muted'
      }`}
    >
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
        className="mt-0.5 h-4 w-4 shrink-0 accent-[var(--color-accent)]"
      />
      <span className="min-w-0">
        <span className="block text-sm text-foreground">{label}</span>
        {hint && <span className="block text-xs text-muted-foreground">{hint}</span>}
      </span>
    </label>
  )
}
