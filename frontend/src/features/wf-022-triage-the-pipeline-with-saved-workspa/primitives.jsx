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
 *   - `Modal` and `Notice` are platform-shaped. The contract already lists both
 *     among the primitives a feature may use, and other features have written
 *     their own copies, so both belong in `components/ui.jsx` once, as platform
 *     work. This page needs a dialog for the column picker and an inline
 *     confirmation for the writes it makes.
 *   - `DataTable` is a fourth thing and should **not** be promoted as-is. What a
 *     generic table needs is a `path` prop on the shared `Icon`, and column
 *     metadata in `column_meta` this API already returns; a generic table that
 *     understands neither is a liability, not a primitive.
 *
 * All three meet the same floor as the shared primitives: a 44px minimum hit
 * target, a real focusable control, a visible text label, a name on every
 * control, and no emoji as an icon.
 */

const FOCUS =
  'focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-background'

/**
 * A labelled on/off control.
 *
 * `role="switch"` and `aria-checked` carry the state rather than colour or the
 * thumb's position, so it reads correctly with a screen reader and in
 * forced-colours mode. `label` is required: a switch with no name says nothing
 * about which setting it belongs to, and this one decides whether a view is
 * visible to the whole team.
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
        transition-colors duration-200 ${FOCUS}
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
          className={`absolute left-0.5 h-4 w-4 rounded-full transition-transform duration-200 ${
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
 * An inline note reporting a completed action.
 *
 * `role="status"` and `aria-live="polite"`, because it appears after the request
 * that caused it has already finished: it is a confirmation, not an alert. The
 * meaning is always in the text, never in the colour alone.
 */
export function Note({ tone = 'good', children }) {
  const tones = {
    good: 'border-accent/40 bg-accent/10 text-accent',
    warn: 'border-amber-500/40 bg-amber-500/10 text-amber-300',
    info: 'border-sky-500/40 bg-sky-500/10 text-sky-300',
  }
  const glyph = { good: 'check', warn: 'inference', info: 'table' }[tone]

  return (
    <div
      role="status"
      aria-live="polite"
      className={`flex items-center gap-2 rounded-lg border p-3 text-sm ${tones[tone]}`}
    >
      <Glyph name={glyph} size={16} />
      <span className="min-w-0">{children}</span>
    </div>
  )
}

/**
 * A single headline number with its label, hint and glyph.
 *
 * Structurally the shared `StatCard`, which is the first choice. It cannot be
 * used for the tiles on this page because every stat here is about something the
 * shared set has no glyph for, and `StatCard` only accepts a `name` from the
 * shared `PATHS` map. Adding a `path` prop that falls back to `name` is the
 * platform fix, and then this component is two components instead of three.
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
            <Icon path={path} size={20} />
          </span>
        )}
      </div>
    </Card>
  )
}
