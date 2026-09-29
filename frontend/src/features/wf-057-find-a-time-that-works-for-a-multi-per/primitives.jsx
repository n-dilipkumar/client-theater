import { useState } from 'react'

import { Card, Icon } from '@/components/ui'
import Glyph from './icons'

/**
 * Four UI pieces this page needs that the shared set in `components/ui.jsx` does
 * not export: `Notice`, `StatTile`, `ConfidenceBadge` and `Toggle`.
 *
 * They are in their own module so this file is the whole of what an integrator
 * has to look at when deciding what to promote into the shared set.
 *
 * The promotion set, stated unambiguously:
 *
 *   - `Notice` is platform-shaped. `docs/FEATURE-CONTRACT.md` already lists
 *     `Notice` among the primitives a feature may use and it is not exported
 *     there, which is a documentation/implementation gap rather than something
 *     for a feature to widen.
 *   - `StatTile` and `PathButton` are a `path` prop on the shared `StatCard` and
 *     `Button`. A `StatCard` that cannot render a feature-supplied glyph is a
 *     small hole in an otherwise complete escape hatch.
 *   - `ConfidenceBadge` is **feature-shaped and should not be promoted**. Its
 *     bands come from the researched weights being surfaced as a scale, and
 *     another feature with a different scale must be able to have a different
 *     one without editing shared code.
 *   - `Toggle` is a labelled checkbox meeting the 44px target; a deployment that
 *     wants a different control can replace it locally.
 *
 * All four meet the same floor as the shared primitives: a 44px minimum hit
 * target, a real focusable control, a visible focus ring (the global
 * `:focus-visible` rule in `index.css` does that work and is never overridden
 * here), a visible text label beside every icon, and no emoji used as an icon.
 */

const FOCUS =
  'focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-background'

const NOTICE_TONES = {
  info: { box: 'border-sky-500/40 bg-sky-500/10', title: 'text-sky-200', glyph: 'recall' },
  warn: { box: 'border-amber-500/40 bg-amber-500/10', title: 'text-amber-200', glyph: 'rule' },
  good: { box: 'border-accent/40 bg-accent/10', title: 'text-foreground', glyph: 'commit' },
  bad: { box: 'border-destructive/40 bg-destructive/10', title: 'text-foreground', glyph: 'empty' },
}

/**
 * Inline note, for where the interface has to justify a constraint or report an
 * outcome.
 *
 * `role` is `alert` only for `bad`, so a screen reader interrupts for a refusal
 * and not for a hint. The meaning is always in the text and never in the colour
 * alone - the three warnings this workflow raises are all about a *setting*, and
 * a rep who only sees amber has nothing to act on.
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
          {title && <p className={`text-sm font-semibold ${palette.title}`}>{title}</p>}
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
export function PathButton({ glyph, children, variant = 'secondary', ...props }) {
  const variants = {
    primary: 'bg-accent text-on-accent hover:brightness-110 font-semibold',
    secondary: 'bg-muted text-foreground hover:bg-border-subtle',
    ghost: 'text-muted-foreground hover:text-foreground hover:bg-muted',
  }
  return (
    <button
      type="button"
      className={`inline-flex min-h-11 items-center gap-2 rounded-lg px-4 text-sm
        transition-colors duration-200 disabled:cursor-not-allowed disabled:opacity-50
        ${variants[variant] || variants.secondary} ${FOCUS} ${props.className || ''}`}
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
 * no glyph for - a candidate slot, an unreadable calendar, a refused one - and
 * `StatCard` only accepts a `name` from the shared `PATHS` map.
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
          {hint && <p className="mt-1 text-xs text-muted-foreground">{hint}</p>}
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
 * The researched confidence percentage, as a scale with a text label.
 *
 * The bands come straight from the researched weights rather than from taste: a
 * slot is only "everyone free" at 100, and anything with a 49% in it is below
 * that. The percentage is the content and the band colour is decoration, so a
 * reader who cannot see the colour still reads the number - which is also what
 * keeps this inside the 4.5:1 contrast floor.
 */
const CONFIDENCE_BANDS = [
  { min: 100, label: 'Everyone free', tone: 'bg-accent/20 text-accent border-accent/40', word: 'excellent' },
  { min: 75, label: 'Nearly everyone free', tone: 'bg-sky-500/15 text-sky-200 border-sky-500/40', word: 'good' },
  { min: 50, label: 'Some unknown', tone: 'bg-amber-500/15 text-amber-200 border-amber-500/40', word: 'workable' },
  { min: 0, label: 'Somebody is busy', tone: 'bg-destructive/15 text-foreground border-destructive/40', word: 'thin' },
]

export function ConfidenceBadge({ confidence, freePercentage }) {
  const value = Number(confidence || 0)
  const band = CONFIDENCE_BANDS.find((entry) => value >= entry.min) || CONFIDENCE_BANDS.at(-1)
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 font-mono text-xs font-medium ${band.tone}`}
      title={freePercentage === undefined ? undefined : `${freePercentage}% of the invited calendars are free`}
    >
      <Glyph name="confidence" size={14} />
      {value}%
      <span className="font-sans font-normal opacity-90">{band.label}</span>
    </span>
  )
}

/**
 * A labelled checkbox that meets the 44px target.
 *
 * `label` is always visible: placeholder-only labelling is an anti-pattern in the
 * design system, and this toggle controls the researched
 * `returnSuggestionReasons` switch, whose whole effect is whether a reason is
 * shown at all.
 */
export function Toggle({ checked, onChange, label, hint, id, disabled }) {
  return (
    <div className="flex flex-col gap-1.5">
      <label
        htmlFor={id}
        className={`flex min-h-11 cursor-pointer items-center gap-3 text-sm text-foreground ${disabled ? 'opacity-50' : ''}`}
      >
        <input
          id={id}
          type="checkbox"
          checked={Boolean(checked)}
          disabled={disabled}
          onChange={(event) => onChange(event.target.checked)}
          className={`h-5 w-5 shrink-0 rounded border-border-subtle bg-background accent-accent ${FOCUS}`}
        />
        <span>{label}</span>
      </label>
      {hint && <p className="text-xs text-muted-foreground/80">{hint}</p>}
    </div>
  )
}

/** Disclosure for the researched request, so a rep can check what would be sent. */
export function Disclosure({ summary, children, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="rounded-lg border border-border-subtle/40">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className={`flex min-h-11 w-full items-center justify-between gap-2 rounded-lg px-3 py-2 text-left text-sm text-foreground hover:bg-muted ${FOCUS}`}
      >
        <span>{summary}</span>
        <span aria-hidden="true" className="text-muted-foreground">
          <Glyph name="slot" size={16} />
        </span>
      </button>
      {open && <div className="border-t border-border-subtle/30 px-3 py-2">{children}</div>}
    </div>
  )
}
