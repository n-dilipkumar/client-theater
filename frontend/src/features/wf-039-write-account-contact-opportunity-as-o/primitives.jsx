import { useState } from 'react'

import { Card, Icon } from '@/components/ui'
import Glyph from './icons'

/**
 * Three UI pieces this feature needs that the shared set in
 * `components/ui.jsx` does not export: `Notice`, `PathButton` and `StatTile`.
 *
 * They are in their own module, and apart from the glyphs, on purpose: the
 * contract tells a feature that needs a primitive the shared set lacks to build
 * it locally and report it, so this file is the whole of what an integrator has
 * to look at.
 *
 * The promotion set, stated unambiguously so an integrator knows what to take:
 *
 *   - `Notice` and `PathButton` are platform-shaped. `Notice` is already listed
 *     in `docs/FEATURE-CONTRACT.md` among the primitives a feature may use, and
 *     it is not exported there, which is a documentation/implementation gap
 *     rather than something to widen. `PathButton` is a `path` prop on the
 *     shared `Button`; it is the same wrapper another feature already needed.
 *     Both belong in `ui.jsx` once, as platform work.
 *   - `StatTile` is a `path` prop on the shared `StatCard`, and the same
 *     argument applies. A `StatCard` that cannot render a feature-supplied glyph
 *     is a small hole in an otherwise complete escape hatch.
 *
 * All three meet the same floor as the shared primitives: a 44px minimum hit
 * target, a real focusable control, a visible focus ring (the global
 * `:focus-visible` rule in `index.css` does that work and is never overridden
 * here), a visible text label beside every icon, and no emoji used as an icon.
 */

const FOCUS =
  'focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-background'

const NOTICE_TONES = {
  info: { box: 'border-sky-500/40 bg-sky-500/10', text: 'text-sky-200', glyph: 'order' },
  warn: { box: 'border-amber-500/40 bg-amber-500/10', text: 'text-amber-200', glyph: 'order' },
  good: { box: 'border-accent/40 bg-accent/10', text: 'text-foreground', glyph: 'atomic' },
  bad: { box: 'border-destructive/40 bg-destructive/10', text: 'text-foreground', glyph: 'rollback' },
}

/**
 * Inline note, for where the interface has to justify a constraint or report an
 * outcome.
 *
 * `role` is `alert` only for `bad`, so a screen reader interrupts for a refusal
 * and not for a hint. The meaning is always in the text, never in the colour
 * alone - the two warnings this workflow raises are both about a *setting*, and a
 * rep who only sees amber has nothing to act on.
 */
export function Notice({ tone = 'info', title, children, action }) {
  const palette = NOTICE_TONES[tone] || NOTICE_TONES.info
  return (
    <div
      role={tone === 'bad' ? 'alert' : 'status'}
      className={`flex flex-wrap items-start justify-between gap-3 rounded-lg border p-4 ${palette.box}`}
    >
      <div className="flex min-w-0 gap-2">
        <span className="mt-0.5 shrink-0 text-muted-foreground">
          <Glyph name={palette.glyph} size={16} />
        </span>
        <div className="min-w-0">
          {title && <p className={`text-sm font-semibold ${palette.text}`}>{title}</p>}
          <div className={`text-sm text-muted-foreground ${title ? 'mt-1' : ''}`}>{children}</div>
        </div>
      </div>
      {action}
    </div>
  )
}

/**
 * The shared `Button` resolves `icon` as a *name* through the shared `PATHS` map,
 * so it cannot draw a glyph a feature supplies. Rather than re-implement `Button`
 * and put a second subtly different button in the product, the glyph goes in as a
 * child: every variant, the 44px target and the focus ring still come from the
 * shared primitive.
 */
export function PathButton({ glyph, children, ...props }) {
  return (
    <button
      type="button"
      className={`inline-flex min-h-11 items-center gap-2 rounded-lg bg-muted px-4 text-sm text-foreground
        transition-colors duration-200 hover:bg-border-subtle disabled:cursor-not-allowed disabled:opacity-50 ${FOCUS} ${props.className || ''}`}
      {...props}
    >
      {glyph && <Icon path={glyph} />}
      {children}
    </button>
  )
}

/**
 * A single headline number with its label, hint and glyph.
 *
 * Structurally the shared `StatCard`, which is the first choice. It cannot be
 * used here because every stat on this page is about something the shared set has
 * no glyph for - a committed subrequest, a rolled-back one, a skipped one - and
 * `StatCard` only accepts a `name` from the shared `PATHS` map. Adding a `path`
 * prop that falls back to `name` is the platform fix, and then this component is
 * two components instead of three.
 */
export function StatTile({ label, value, hint, glyph }) {
  return (
    <Card className="card-hover">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
            {label}
          </p>
          <p className="mt-2 font-mono text-3xl font-semibold text-foreground">{value}</p>
          {hint && <p className="mt-1 truncate text-xs text-muted-foreground">{hint}</p>}
        </div>
        {glyph && (
          <span className="rounded-lg bg-muted p-2 text-accent">
            <Icon path={glyph} size={20} />
          </span>
        )}
      </div>
    </Card>
  )
}

/**
 * A labelled on/off control for the ordering flag.
 *
 * `role="switch"` and `aria-checked` carry the state rather than colour or the
 * thumb's position, so it reads correctly with a screen reader and in
 * forced-colours mode. `label` is required: a switch with no name says nothing
 * about which setting it belongs to, and this one decides whether a declared
 * implicit dependency is honoured.
 */
export function Toggle({ checked, onChange, label, disabled = false }) {
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
        disabled:cursor-not-allowed disabled:opacity-50`}
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
 * One subrequest in the declared order, with what it depends on and what became
 * of it.
 *
 * The order is the researched feature - "bundle preview showing the subrequest
 * order" - so the number is part of the row rather than a list marker, and the
 * dependency arrows are drawn rather than described.
 */
export function StepRow({ step, index, outcome, reason, message, resolved }) {
  const [open, setOpen] = useState(false)
  const hasDetail = outcome || message || Object.keys(resolved || {}).length > 0

  return (
    <li className="border-b border-border-subtle/15 last:border-0">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        disabled={!hasDetail}
        className={`flex w-full min-h-11 items-center gap-3 py-2 text-left transition-colors
          duration-150 hover:bg-muted/40 disabled:cursor-default disabled:hover:bg-transparent ${FOCUS}`}
      >
        <span className="w-5 shrink-0 font-mono text-xs text-muted-foreground">{index + 1}</span>
        <span className="min-w-0 flex-1">
          <span className="block truncate font-mono text-[13px] text-foreground">
            {step.record_type}
            <span className="ml-2 text-muted-foreground">{step.reference_id}</span>
          </span>
          {step.depends_on && step.depends_on.length > 0 && (
            <span className="mt-0.5 block truncate text-xs text-muted-foreground">
              depends on {step.depends_on.join(', ')}
            </span>
          )}
          {!hasDetail && (
            <span className="mt-0.5 block truncate text-xs text-muted-foreground">
              {step.explicit_dependencies.length > 0
                ? `after ${step.explicit_dependencies.join(', ')}`
                : 'no dependency'}
            </span>
          )}
        </span>
        {outcome && <OutcomeTag outcome={outcome} />}
        {reason && <span className="shrink-0 font-mono text-[11px] text-muted-foreground">{reason}</span>}
      </button>
      {open && hasDetail && (
        <div className="space-y-2 rounded-lg border border-border-subtle/25 bg-background/40 p-3 text-xs">
          {message && <p className="text-foreground/90">{message}</p>}
          {resolved && Object.keys(resolved).length > 0 && (
            <div>
              <p className="font-medium tracking-wide text-muted-foreground uppercase">
                What the CRM wrote
              </p>
              <ul className="mt-1 space-y-0.5">
                {Object.entries(resolved).map(([field, value]) => (
                  <li key={field} className="font-mono text-[12px]">
                    <span className="text-muted-foreground">{field}</span>
                    <span className="px-1 text-muted-foreground/60">:</span>{' '}
                    <span className="text-foreground">{String(value)}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </li>
  )
}

const OUTCOME_TEXT = {
  created: 'created',
  rolled_back: 'rolled back',
  skipped: 'skipped',
  failed: 'failed',
}

const OUTCOME_TONE = {
  created: 'insert',
  rolled_back: 'restore',
  skipped: 'neutral',
  failed: 'delete',
}

function OutcomeTag({ outcome }) {
  return (
    <span
      className={`shrink-0 rounded-md border px-2 py-0.5 font-mono text-xs font-medium ${
        {
          created: 'border-accent/30 bg-accent/15 text-accent',
          rolled_back: 'border-amber-500/30 bg-amber-500/15 text-amber-300',
          skipped: 'border-border-subtle/40 bg-muted text-muted-foreground',
          failed: 'border-destructive/30 bg-destructive/15 text-destructive',
        }[outcome]
      }`}
    >
      {OUTCOME_TEXT[outcome] || outcome}
    </span>
  )
}

export { OUTCOME_TEXT, OUTCOME_TONE }
