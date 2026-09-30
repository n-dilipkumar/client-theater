import { Card, Icon } from '@/components/ui'
import Glyph from './icons'

/**
 * The three UI pieces this feature needs that the shared set in
 * `components/ui.jsx` cannot express. They are in their own module, and apart from
 * the glyphs, on purpose: the contract tells a feature that needs a primitive the
 * shared set lacks to build it locally and report it, so this file is the whole of
 * what an integrator has to look at.
 *
 * The promotion set, unambiguously:
 *
 *   - `Toggle` and `Note` are platform-shaped. The contract already lists both
 *     among the primitives a feature may use, and this is now the third feature
 *     to write its own copy of them, so both belong in `components/ui.jsx` once,
 *     as platform work.
 *   - `StatTile` should *not* be promoted as a fourth stat card. The shared
 *     `StatCard` is fine; what it lacks is a `path` prop, so it can only be handed
 *     a name from the shared `PATHS` map. Adding `path` to `StatCard` and falling
 *     back to `name` is the platform fix, and then this file is two components
 *     instead of three.
 *
 * All three meet the same floor as the shared primitives: a 44px minimum hit
 * target, a real focusable control, a visible text label, and no emoji as an icon.
 */

/**
 * A labelled on/off control.
 *
 * The state is carried by `role="switch"` and `aria-checked` rather than by colour
 * or by the thumb's position, so it reads correctly with a screen reader and in
 * forced-colours mode. `label` is the accessible name and is required: switching
 * a CRM connection off stops the room from using its token at all, and an
 * unnamed switch says nothing about which connection it belongs to.
 */
export function Toggle({ checked, onChange, label, disabled = false, className = '' }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`inline-flex min-h-11 items-center gap-2 rounded-lg px-2 text-sm
        transition-colors duration-200 focus-visible:ring-2 focus-visible:ring-accent
        focus-visible:ring-offset-2 focus-visible:ring-offset-background
        disabled:cursor-not-allowed disabled:opacity-50 ${className}`}
    >
      <span
        aria-hidden="true"
        className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full border
          transition-colors duration-200 ${
            checked ? 'border-accent/40 bg-accent/25' : 'border-border-subtle/50 bg-muted'
          }`}
      >
        <span
          className={`absolute left-0.5 h-4 w-4 rounded-full transition-transform duration-200 motion-reduce:transition-none ${
            checked ? 'translate-x-5 bg-accent' : 'translate-x-0 bg-muted-foreground'
          }`}
        />
      </span>
      <span className={checked ? 'text-foreground' : 'text-muted-foreground'}>
        {checked ? 'On' : 'Off'}
      </span>
    </button>
  )
}

/**
 * An inline note reporting a completed action, a warning, or a blocker to clear.
 *
 * `role="status"` and `aria-live="polite"` on the good and info tones, because they
 * appear after the request that caused them has already finished: they are
 * confirmations, not alerts. The warn tone is `role="alert"` instead, because a
 * blocker is something to act on now. The tone picks the colour and the glyph;
 * the meaning is always in the text, never in the colour alone.
 */
export function Note({ tone = 'good', children }) {
  const tones = {
    good: 'border-accent/40 bg-accent/10 text-accent',
    warn: 'border-amber-500/40 bg-amber-500/10 text-amber-300',
    info: 'border-sky-500/40 bg-sky-500/10 text-sky-300',
  }
  const live = tone === 'warn' ? 'assertive' : 'polite'
  return (
    <div
      role={tone === 'warn' ? 'alert' : 'status'}
      aria-live={live}
      className={`flex items-start gap-2 rounded-lg border p-3 text-sm ${tones[tone]}`}
    >
      <span className="mt-0.5 shrink-0" aria-hidden="true">
        <Icon name="schema" size={16} path="M12 4l9 16H3l9-16zm0 6v4m0 3v.01" />
      </span>
      <span className="min-w-0">{children}</span>
    </div>
  )
}

/**
 * A single headline number with its label, hint and glyph.
 *
 * Structurally the shared `StatCard`, which is the first choice and is used
 * wherever a shared glyph fits. It cannot be used here because it only accepts a
 * `name` from the shared `PATHS` map, and every stat on this page is about
 * something the shared set has no glyph for. This takes a `path` instead.
 */
export function StatTile({ label, value, hint, path }) {
  return (
    <Card className="card-hover">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{label}</p>
          <p className="mt-2 font-mono text-3xl font-semibold text-foreground">{value}</p>
          {hint && <p className="mt-1 truncate text-xs text-muted-foreground">{hint}</p>}
        </div>
        {path && (
          <span className="rounded-lg bg-muted p-2 text-accent">
            <Glyph name={path} size={20} />
          </span>
        )}
      </div>
    </Card>
  )
}
