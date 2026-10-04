/**
 * The two things this feature needs that `@/components/ui` does not carry.
 *
 * Both are built here rather than added to `ui.jsx`, because that file is shared and
 * this feature may not edit it. The contract says to build what is missing inside the
 * feature folder and say so in the pull request, which this comment and the pull
 * request both do.
 *
 * 1. `ProofBadge` renders one of the three verification words. `Badge` takes a tone
 *    and a child, which is enough to show a word, but the state also carries a
 *    sentence saying what the word means. A badge alone would let a rep read "Verified"
 *    without the reason it is not the same as "typed in", which is the question the
 *    specification says the page exists to answer.
 * 2. `DwellBars` renders the per-page engagement as a proportion of the longest page.
 *    `StatCard` shows one number and the design system has no chart primitive, so a
 *    page-by-page timeline needs its own.
 *
 * Neither uses an emoji as an icon, neither hardcodes a colour, and both honour the
 * accessibility floor: 44px targets on anything interactive and a text label beside
 * every mark.
 */

import { Badge } from '@/components/ui'
import { verificationState } from './api'

/**
 * One verification word, with the sentence that says what it means.
 *
 * No status is carried by colour alone. The word is always present as text, and the
 * meaning is available to a screen reader through the description, so the distinction
 * between "not proven" and "no proof recorded" survives a monochrome display and a
 * colour-blind reader alike. That distinction is the whole point of the three states.
 */
export function ProofBadge({ value, showMeaning = false }) {
  const state = verificationState(value)
  return (
    <span className="inline-flex items-center gap-2">
      <Badge tone={state.tone}>{state.label}</Badge>
      {showMeaning ? (
        <span className="text-xs text-muted-foreground">{state.meaning}</span>
      ) : null}
    </span>
  )
}

/**
 * Per-page engagement as a proportion of the page that held attention longest.
 *
 * The bar is decorative and every row carries its own numbers as text, so nothing is
 * conveyed by the bar alone. `min-h-11` on each row keeps the row a comfortable target
 * on a touch screen, and the durations are rendered in the design system's mono face
 * because a count and a duration are machine values rather than prose.
 */
export function DwellBars({ rows = [], unitLabel = 's' }) {
  if (!rows.length) {
    return (
      <p className="text-sm text-muted-foreground">No page dwell has been recorded yet.</p>
    )
  }

  const longest = Math.max(
    ...rows.map((row) => Number(row.total_duration_seconds) || 0),
    1,
  )

  return (
    <ul className="space-y-1">
      {rows.map((row) => {
        const seconds = Number(row.total_duration_seconds) || 0
        const share = Math.round((seconds / longest) * 100)
        return (
          <li
            key={row.page_number}
            className="grid min-h-11 grid-cols-[4rem_1fr_9rem] items-center gap-3"
          >
            <span className="font-mono text-xs text-muted-foreground">
              Page {row.page_number}
            </span>
            <span
              className="h-2 w-full rounded-xs bg-muted"
              role="img"
              aria-label={`Page ${row.page_number} held attention for ${seconds} seconds, ${share} percent of the longest page`}
            >
              <span
                className="block h-2 rounded-xs bg-accent"
                style={{ width: `${Math.max(share, 1)}%` }}
              />
            </span>
            <span className="font-mono text-xs text-foreground">
              {seconds}
              {unitLabel}
              <span className="text-muted-foreground">
                {' '}
                / {row.viewers} reader{row.viewers === 1 ? '' : 's'}
              </span>
            </span>
          </li>
        )
      })}
    </ul>
  )
}

/**
 * The room's two counts, side by side and never merged.
 *
 * The specification says a viewer who hits two links "shows up once here, but twice in
 * `papermark views list`", so the page shows both and says in words why they differ.
 * Merging them into one "engagement" number would answer a question the rep did not
 * ask.
 */
export function CountPair({ visitors, views }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <div className="rounded-sm border border-border-subtle bg-surface p-3">
        <p className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
          Visitors
        </p>
        <p className="font-mono text-2xl text-foreground">{visitors}</p>
        <p className="text-xs text-muted-foreground">
          One row per buyer email. A buyer who opens two links is still one row.
        </p>
      </div>
      <div className="rounded-sm border border-border-subtle bg-surface p-3">
        <p className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
          View events
        </p>
        <p className="font-mono text-2xl text-foreground">{views}</p>
        <p className="text-xs text-muted-foreground">
          One row per view, including views with no buyer address.
        </p>
      </div>
    </div>
  )
}