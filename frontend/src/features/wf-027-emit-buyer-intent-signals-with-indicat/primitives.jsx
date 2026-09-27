/**
 * Glyphs and two primitives this feature needs, built here rather than added to
 * `components/ui.jsx`.
 *
 * `components/ui.jsx` is shared and a hundred features each appending to it is
 * the collision the feature host exists to prevent, so the signal glyph is passed
 * as a path through `<Icon path=...>` and the inline `Notice` is built in this
 * folder. Both are findings for the integrator: `Notice` in particular is generic
 * enough that the next workflow will want it too, and promoting it into `ui.jsx`
 * is a one-off platform change belonging to whoever owns that file.
 *
 * The floor they meet, from the design system: a 44px minimum touch target, a
 * visible focus ring (inherited globally from `index.css`), a text label beside
 * every glyph so meaning survives with icons off, no emoji, and no motion beyond
 * the global `prefers-reduced-motion` rule.
 */

import { Icon } from '@/components/ui'

/**
 * A signal going out: a dot with two rings, like something radiating.
 *
 * The `ICON` shape of "an event goes out to somewhere" is what the nav wants, and
 * it is not in the shared `PATHS` map, which is why this folder defines it and
 * `Icon` receives it as a path. `SIGINT_ICON` is the fallback for a consumer that
 * reads only the shared name.
 */
export const SIGNAL_ICON =
  'M12 8a4 4 0 100 8 4 4 0 000-8-8zm0 2a2 2 0 110 4 2 2 0 010-4zM12 2a10 10 0 100 20 10 10 0 000-20zm0 2.5A7.5 7.5 0 1112 19.5 7.5 7.5 0 0112 4.5z'

const ICONS = { signal: SIGNAL_ICON }

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
