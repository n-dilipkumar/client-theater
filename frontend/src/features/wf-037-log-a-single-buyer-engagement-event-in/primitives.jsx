import { useEffect, useState } from 'react'

import { ICONS } from './icons'

/**
 * Three UI primitives this feature needs that the shared set in `components/ui.jsx` does
 * not export: `Notice`, `StatePill` and `usePrefersReducedMotion`.
 *
 * The original workflow branches added their own to `components/ui.jsx`. That file is
 * shared, so a hundred features each appending to it is the collision the plugin host
 * exists to prevent. The contract's answer is explicit: build what you need inside your
 * own feature folder and say so in the report, and the integrator promotes recurring ones
 * into `ui.jsx` as platform work, once.
 *
 * The promotion set, stated unambiguously so an integrator knows what to take:
 *
 *   - `Notice` is a platform-shaped gap. `docs/FEATURE-CONTRACT.md` already lists
 *     `Notice` among the primitives a feature may use, and it is not exported from
 *     `ui.jsx`, which is a documentation/implementation gap worth closing rather than
 *     widening. The ports on `main` have each built their own copy independently.
 *   - `StatePill` is the second. The shared set has `Badge`, but a Badge carries a tone and
 *     an arbitrary string; a queue state is a closed vocabulary of four that must read the
 *     same in every feature that shows one, and a workflow author picking a tone per row
 *     is how two identical states end up looking like two different problems. Small enough
 *     to promote, specific enough that not every feature needs it.
 *   - `usePrefersReducedMotion` is **not** a promotion candidate on its own. The global
 *     block in `index.css` already collapses animation and transition durations for
 *     everyone; this hook exists because a `width`-based bar cannot be fixed by shortening
 *     its duration to 0.01ms without leaving the element half-applied, so the widths are
 *     set outright instead.
 *   - The glyphs in `./icons.js` are **not** promotion candidates. They are this feature's
 *     own visual vocabulary, and the contract already has the mechanism for shared glyphs:
 *     `Icon path=` and `iconPath` in the descriptor.
 *
 * All three meet the same floor as the shared primitives: a 44px minimum target, a real
 * focusable control, a visible focus ring (the global `:focus-visible` in `index.css` does
 * the work and is never removed here), a visible text label, and no emoji used as an icon.
 */

const NOTICE_TONES = {
  info: { box: 'border-sky-500/40 bg-sky-500/10', text: 'text-sky-200', icon: ICONS.alert },
  warn: { box: 'border-amber-500/40 bg-amber-500/10', text: 'text-amber-200', icon: ICONS.alert },
  danger: { box: 'border-destructive/40 bg-destructive/10', text: 'text-destructive', icon: ICONS.alert },
  good: { box: 'border-accent/40 bg-accent/10', text: 'text-accent', icon: ICONS.synced },
}

/**
 * Inline explanatory note, used where the interface has to justify a constraint rather
 * than merely report a failure: a blocked row and the ground it is blocked on, a mapping
 * finding, a vendor error the connector's preferences made readable.
 *
 * The meaning is always carried by the body text. Tone is a second channel, never the only
 * one - which is why every caller passes a sentence rather than relying on the colour to
 * say what happened.
 */
export function Notice({ tone = 'info', title, icon, children }) {
  const palette = NOTICE_TONES[tone] || NOTICE_TONES.info
  const glyph = icon || palette.icon
  return (
    <div className={`flex gap-3 rounded-lg border p-3 text-sm ${palette.box} ${palette.text}`}>
      <span className="mt-0.5 shrink-0" aria-hidden="true">
        <svg
          width="18"
          height="18"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d={glyph} />
        </svg>
      </span>
      <div className="min-w-0">
        {title && <p className="font-semibold">{title}</p>}
        <div className={title ? 'mt-0.5' : ''}>{children}</div>
      </div>
    </div>
  )
}

/**
 * The four queue states, in the wording the domain uses.
 *
 * One closed vocabulary, one colour, one label. A rep learning "blocked means this one
 * needs a person" should not have to learn it again on the next workflow, and two features
 * picking different colours for the same state is how a log stops being readable.
 */
const STATE_PILLS = {
  synced: { label: 'Synced', box: 'border-accent/40 bg-accent/15 text-accent' },
  pending: { label: 'Pending', box: 'border-sky-500/40 bg-sky-500/15 text-sky-200' },
  failed: { label: 'Failed', box: 'border-destructive/40 bg-destructive/15 text-destructive' },
  blocked: { label: 'Blocked', box: 'border-amber-500/40 bg-amber-500/15 text-amber-200' },
}

export function StatePill({ state, title }) {
  const pill = STATE_PILLS[state] || STATE_PILLS.pending
  return (
    <span
      className={`inline-flex items-center rounded-md border px-2 py-0.5 font-mono text-xs font-medium ${pill.box}`}
      title={title}
    >
      {pill.label}
    </span>
  )
}

/**
 * Whether the reader has asked for reduced motion.
 *
 * Defaults to `false` on the server and during the first render so the markup is identical
 * either way - a hydration mismatch here would mean the bar rendered twice with different
 * widths, which is worse than a bar that briefly does not animate.
 */
export function usePrefersReducedMotion() {
  const [reduced, setReduced] = useState(false)
  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return undefined
    const query = window.matchMedia('(prefers-reduced-motion: reduce)')
    const apply = () => setReduced(query.matches)
    apply()
    query.addEventListener('change', apply)
    return () => query.removeEventListener('change', apply)
  }, [])
  return reduced
}

/**
 * A disclosure, used for the researched contract and the inference registry.
 *
 * A real `<details>`/`<summary>` rather than a button and a `hidden` div: keyboard
 * operation, the expanded state, and the find-in-page behaviour all come for free, and a
 * reader who has collapsed it does not have it forced back open on every re-render of the
 * feed behind it.
 */
export function Disclosure({ summary, children, defaultOpen = false }) {
  return (
    <details className="rounded-lg border border-border-subtle/40 bg-background/40" open={defaultOpen}>
      <summary className="flex min-h-11 cursor-pointer items-center gap-2 px-4 py-3 text-sm text-foreground">
        {summary}
      </summary>
      <div className="border-t border-border-subtle/40 px-4 py-3">{children}</div>
    </details>
  )
}

/**
 * One labelled bar, for the per-type dwell totals.
 *
 * Three things are deliberate:
 *
 * 1. **The value is rendered as text, and the bar is decoration.** A bar whose only
 *    content is a width is unreadable to a screen reader and to anyone whose browser has a
 *    high zoom, so the number is always beside the bar and the bar carries `aria-hidden`.
 * 2. **The fill grows from zero without animating when the reader has asked for reduced
 *    motion.** The global CSS block collapses a transition's duration, which is not the
 *    same as not running the transition, so the width is set outright instead.
 * 3. **A zero-value bar is still a row.** A row that disappears when its value is zero
 *    makes a reader conclude the category does not exist, when the honest answer is that
 *    it exists and contributed nothing.
 */
export function BarRow({ label, value, display, hint, max, tone = 'accent' }) {
  const reduced = usePrefersReducedMotion()
  // Only the animation is state. Whether the bar is shown at its width is
  // derived: with reduced motion there is no animation to wait for, so the bar
  // is simply at full width on the first render. Writing `setGrown(true)` in the
  // reduced branch instead meant the bar spent one render at zero width for a
  // reader who asked for no animation at all.
  const [animated, setAnimated] = useState(false)
  const peak = max ?? value ?? 0
  const ratio = peak > 0 && value > 0 ? Math.max(0.02, Math.min(1, value / peak)) : 0
  const grown = reduced || animated

  useEffect(() => {
    if (reduced) return undefined
    const frame = requestAnimationFrame(() => setAnimated(true))
    return () => cancelAnimationFrame(frame)
  }, [reduced])

  const fills = {
    accent: 'bg-accent',
    sky: 'bg-sky-400',
    amber: 'bg-amber-400',
    muted: 'bg-muted-foreground/50',
  }

  return (
    <li className="flex flex-col gap-1 py-1.5">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
        <span className="min-w-0 truncate text-sm text-foreground">{label}</span>
        <span className="shrink-0 font-mono text-sm text-foreground">{display}</span>
      </div>
      <div className="h-2 w-full overflow-hidden rounded-full bg-muted" aria-hidden="true">
        <div
          className={`h-full rounded-full ${fills[tone] || fills.accent} ${
            reduced ? '' : 'transition-[width] duration-500 ease-out'
          }`}
          style={{ width: grown ? `${ratio * 100}%` : '0%' }}
        />
      </div>
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </li>
  )
}
