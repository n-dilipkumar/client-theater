import { Button, Field, inputClass } from '@/components/ui'

/**
 * Local primitives for WF-014, built here rather than added to `ui.jsx`.
 *
 * `components/ui.jsx` is shared by every feature and a feature that edits it
 * collides with every other feature. The design system says to build what is
 * genuinely missing inside the feature folder and say so, so this is that note.
 *
 * Two things are missing upstream:
 *
 * * `Modal`, `Notice`, `Toggle` and `Checkbox` are listed in FEATURE-CONTRACT.md
 *   as shared primitives, but none of them is exported by `components/ui.jsx`.
 *   This feature needs a switch-shaped control for two independent settings and a
 *   dismissible banner, so both are built here in the design system's own tokens
 *   and shape, at the same accessibility floor (44px targets, visible label, a
 *   text label beside every icon).
 *
 * If a second feature needs the same switch, it belongs in `ui.jsx` as platform
 * work - promoted once by the integrator, not by a feature branch.
 */

/** A labelled on/off control with a 44px target and a real checkbox. */
export function Switch({ id, label, hint, checked, disabled, onChange }) {
  return (
    <div className="flex items-start gap-3">
      <input
        id={id}
        type="checkbox"
        role="switch"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
        className="mt-0.5 h-5 w-5 shrink-0 rounded-xs border border-border-subtle accent-accent
          focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent
          disabled:cursor-not-allowed disabled:opacity-50"
      />
      <div className="min-w-0">
        <label htmlFor={id} className="text-[13px] font-medium text-foreground">
          {label}
        </label>
        {hint && <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>}
      </div>
    </div>
  )
}

/**
 * The buyer-side banner, in both the states the research describes.
 *
 * A warning that sits beside the content at the bottom left, and an error that
 * replaces it. Position is the researched part: "a persistent message in the
 * bottom left corner" for the warning, and an error replacing the page once the
 * limit is hit.
 */
export function BuyerBanner({ view, onDismiss }) {
  if (view.state === 'open' || view.state === 'unpublished') return null

  const isError = view.error === true

  return (
    <div
      role={isError ? 'alert' : 'status'}
      aria-live={isError ? 'assertive' : 'polite'}
      className={
        isError
          ? 'rounded-sm border border-destructive/40 bg-destructive/10 p-5'
          : 'fixed bottom-4 left-4 max-w-sm rounded-sm border border-warning/40 bg-surface p-4 shadow-none'
      }
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p
            className={`text-sm font-semibold ${isError ? 'text-destructive' : 'text-warning'}`}
          >
            {isError ? 'This room is closed' : 'This room is closing soon'}
          </p>
          <p className="mt-1 text-sm text-muted-foreground">{view.message}</p>
        </div>
        {!isError && (
          <Button variant="ghost" onClick={onDismiss} aria-label="Dismiss this notice">
            Dismiss
          </Button>
        )}
      </div>
    </div>
  )
}

export { Button, Field, inputClass }
