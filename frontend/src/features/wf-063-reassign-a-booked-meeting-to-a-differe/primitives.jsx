/**
 * Primitives the meeting reassignment page needs and the shared set cannot express.
 *
 * The shared `components/ui.jsx` carries Button, Card, StatCard, Badge, Field,
 * Spinner, ErrorNote, EmptyState, inputClass, useAsync and JsonView, and this
 * page uses all of them. Three things are missing, and each is built here rather
 * than added to the shared file, which a feature may not edit. The integrator
 * promotes a recurring one into `ui.jsx` as platform work, once.
 *
 * Every interactive element here meets the design floor in
 * `design-system/digital-sales-room/MASTER.md`: 44px minimum touch targets, a
 * visible focus ring, a text label beside every glyph, no emoji as an icon, and
 * no motion that ignores `prefers-reduced-motion` (there is none).
 */

import { Icon } from '@/components/ui'

/**
 * The nav glyph: a meeting moving from one person to another.
 *
 * Passed as a path rather than a name because the shared `PATHS` map is not ours
 * to edit, and the plain two-person "swap" glyph is not among its names.
 */
export const REASSIGN_ICON =
  'M7 8h10M7 8l3-3M7 8l3 3M17 16H7M17 16l-3-3M17 16l-3 3'

/**
 * An outcome chip: a word, and a colour that is decoration rather than the message.
 *
 * The design floor requires a text label beside every icon, and a decision log is
 * where that matters most: nine refusals and one success that differ only by hue
 * would be unreadable to a colour-blind rep and in a black-and-white printout.
 * The word carries the whole meaning; the tone only helps you scan.
 */
const OUTCOME_TONES = {
  reassigned: {
    tone: 'insert',
    label: 'reassigned',
  },
  refused_locked_field: { tone: 'delete', label: 'locked field' },
  refused_addon_not_ready: { tone: 'delete', label: 'add-on not ready' },
  refused_distribution_context: { tone: 'delete', label: 'other distribution' },
  refused_same_host: { tone: 'neutral', label: 'already the host' },
  refused_not_in_distribution: { tone: 'delete', label: 'out of distribution' },
  refused_host_unavailable: { tone: 'delete', label: 'not free' },
  refused_not_round_robin: { tone: 'delete', label: 'not round robin' },
  refused_no_eligible_host: { tone: 'delete', label: 'nobody eligible' },
  refused_inactive_host: { tone: 'delete', label: 'inactive host' },
}

export function OutcomeChip({ outcome }) {
  const spec = OUTCOME_TONES[outcome] || { tone: 'neutral', label: outcome }
  return (
    <span
      className={`inline-flex min-h-6 items-center rounded-md border px-2 py-0.5 font-mono
        text-xs font-medium ${
          spec.tone === 'insert'
            ? 'border-accent/30 bg-accent/15 text-accent'
            : spec.tone === 'delete'
              ? 'border-destructive/30 bg-destructive/15 text-destructive'
              : 'border-border-subtle/40 bg-muted text-muted-foreground'
        }`}
    >
      {spec.label}
    </span>
  )
}

/**
 * The two fields the research locks, shown as locked rather than merely absent.
 *
 * "You cannot change the Meeting Type or Workspace" - so a reviewer looking at
 * the page should see that they are *deliberately* uneditable rather than
 * wonder where the inputs went. Rendered read-only, with a lock glyph and a
 * text label beside it, which is what the design floor requires.
 */
export function LockedField({ label, value, quote }) {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="inline-flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
        <svg
          aria-hidden="true"
          focusable="false"
          width="13"
          height="13"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
          className="text-amber-500/80"
        >
          <path d="M5 11h14v10H5V11zM8 11V7a4 4 0 018 0v4" />
        </svg>
        {label} (locked)
      </span>
      <div
        className="min-h-11 rounded-lg border border-border-subtle/30 bg-muted/40 px-3 py-2.5
          font-mono text-sm text-muted-foreground"
        title={quote}
      >
        {value || '—'}
      </div>
      {quote && <p className="text-xs text-muted-foreground/80">&ldquo;{quote}&rdquo;</p>}
    </div>
  )
}

/**
 * A host row in the "known and free" picker.
 *
 * A radio group rather than a list of buttons, because exactly one host can
 * take the booking and a screen-reader user needs to be told that. The whole row
 * is the label, so the click target is the full 44px minimum and the name, the
 * reason and the credit count are all one announcement.
 */
export function HostOption({ row, selected, onSelect, name }) {
  const disabled = !row.eligible
  return (
    <label
      className={`flex min-h-11 cursor-pointer items-start gap-3 rounded-lg border px-3 py-2.5
        transition-colors duration-200 ${
          disabled
            ? 'cursor-not-allowed border-border-subtle/30 bg-muted/20 opacity-60'
            : selected
              ? 'border-accent/50 bg-accent/10 focus-within:ring-2 focus-within:ring-accent'
              : 'border-border-subtle/40 hover:bg-muted/50 focus-within:ring-2 focus-within:ring-accent'
        }`}
    >
      <input
        type="radio"
        name={name}
        value={row.id}
        checked={selected}
        disabled={disabled}
        onChange={() => onSelect(row)}
        className="mt-1 h-4 w-4 shrink-0 accent-[var(--accent)]"
      />
      <span className="min-w-0 flex-1">
        <span className="flex flex-wrap items-baseline gap-x-2">
          <span className="font-mono text-sm text-foreground">{row.name}</span>
          <span className="text-xs text-muted-foreground">{row.team}</span>
          <span className="ml-auto font-mono text-xs text-muted-foreground">
            {row.round_robin_credits} credit{row.round_robin_credits === 1 ? '' : 's'}
          </span>
        </span>
        {disabled && (
          <span className="mt-1 block text-xs text-destructive">
            {ineligibleSentence(row)}
          </span>
        )}
      </span>
    </label>
  )
}

/**
 * Why a host cannot take this booking, in one sentence.
 *
 * Built from the reasons rather than the other way round, so adding a reason to
 * the backend cannot leave this showing a bare list of codes.
 */
export function ineligibleSentence(row) {
  const parts = []
  if (row.ineligible_because.includes('already_the_host')) {
    parts.push('already hosts this meeting')
  }
  if (row.ineligible_because.includes('inactive')) {
    parts.push('is not taking bookings')
  }
  if (row.ineligible_because.includes('not_in_distribution')) {
    parts.push('is outside this distribution, which does not allow any team member')
  }
  if (row.ineligible_because.includes('busy')) {
    const clash = (row.conflicts || [])[0]
    parts.push(`is busy${clash?.label ? ` — ${clash.label}` : ''}`)
  }
  return parts.join('; ') || 'cannot take this booking'
}

/**
 * A quote block for the researched sentence behind a decision.
 *
 * The page shows the research's own words rather than a paraphrase, because the
 * paraphrases are what drift: a rule quoted as "you can change the host" stops
 * saying which two fields are locked.
 */
export function Quote({ children, source }) {
  if (!children) return null
  return (
    <blockquote className="border-l-2 border-accent/40 pl-3 text-sm italic text-muted-foreground">
      {children}
      {source && <footer className="mt-1 text-xs not-italic text-muted-foreground/70">— {source}</footer>}
    </blockquote>
  )
}

/**
 * A key/value row, mono, for the "before and after" of a reassignment.
 *
 * A changed field is marked with a bullet and named, because the interesting
 * question on this page is *what moved*, and a diff nobody has to compute is the
 * difference between reading it and not.
 */
export function DiffRow({ field, before, after, changed }) {
  return (
    <li className="grid grid-cols-[minmax(0,10rem)_1fr] gap-3 py-1.5">
      <span className="font-mono text-[13px] text-muted-foreground">{field}</span>
      <span className="min-w-0 font-mono text-[13px]">
        {changed ? (
          <>
            <span className="text-muted-foreground/60 line-through">{before ?? '—'}</span>
            <span className="px-1 text-muted-foreground/50">→</span>
            <span className="text-foreground">{after ?? '—'}</span>
          </>
        ) : (
          <span className="text-muted-foreground">{after ?? '—'}</span>
        )}
      </span>
    </li>
  )
}
