/**
 * Two primitives this page needs, built here rather than added to
 * `components/ui.jsx`.
 *
 * `components/ui.jsx` is shared and a hundred features each appending to it is
 * the collision the feature host exists to prevent. Both of these are generic
 * enough that the next workflow will want them too, and promoting them into
 * `ui.jsx` is a one-off platform change belonging to whoever owns that file. They
 * are reported in the PR description as candidates for that promotion.
 *
 * The floor they meet, from the design system: a 44px minimum touch target, a
 * visible focus ring (inherited globally from `index.css`), a text label beside
 * every glyph so meaning survives with icons off, no emoji, and no motion beyond
 * the global `prefers-reduced-motion` rule.
 */

import { Icon } from '@/components/ui'
import { Glyph } from './icons'

/**
 * Inline notice for a message the user needs to act on.
 *
 * `tone` picks the semantic colour and `icon` the glyph. The title is always
 * text, never an icon on its own.
 */
export function Notice({ tone = 'info', title, children, icon, onDismiss }) {
  const tones = {
    info: 'border-sky-500/40 bg-sky-500/10 text-foreground',
    success: 'border-accent/40 bg-accent/10 text-foreground',
    warning: 'border-amber-500/40 bg-amber-500/10 text-foreground',
    danger: 'border-destructive/40 bg-destructive/10 text-foreground',
  }
  // A warning or a failure is announced; a confirmation is not. Both would be
  // noise if every notice interrupted whatever the user was doing.
  const role = tone === 'danger' || tone === 'warning' ? 'alert' : 'status'
  return (
    <div role={role} className={`rounded-lg border p-4 ${tones[tone] || tones.info}`}>
      <div className="flex items-start gap-3">
        {icon && (
          <span className="mt-0.5 shrink-0 text-foreground">
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
            className="flex min-h-11 min-w-11 shrink-0 cursor-pointer items-center justify-center rounded-lg text-muted-foreground transition-colors duration-150 hover:bg-muted hover:text-foreground"
          >
            <span className="sr-only">Dismiss</span>
            <Icon name="close" size={16} />
          </button>
        )}
      </div>
    </div>
  )
}

/**
 * The one-line outcome of an action, in the flow of the page.
 *
 * Separate from `Notice` because it is not advice - it is what just happened, and
 * it should read as a receipt rather than as a warning.
 */
export function Banner({ outcome }) {
  if (!outcome) return null
  const tones = {
    ok: 'border-accent/40 bg-accent/10 text-foreground',
    bad: 'border-destructive/40 bg-destructive/10 text-foreground',
  }
  return (
    <div
      role={outcome.tone === 'bad' ? 'alert' : 'status'}
      className={`rounded-lg border p-3 text-sm ${tones[outcome.tone] || tones.ok}`}
    >
      <p className="font-medium">{outcome.text}</p>
      {outcome.detail && <p className="mt-1 text-xs text-muted-foreground">{outcome.detail}</p>}
    </div>
  )
}

/** A labelled group with a heading, so every block says what it is. */
export function Section({ title, hint, children, actions }) {
  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-foreground">{title}</h2>
          {hint && <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>}
        </div>
        {actions}
      </div>
      {children}
    </section>
  )
}
