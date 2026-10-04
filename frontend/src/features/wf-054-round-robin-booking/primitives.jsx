/**
 * Primitives the round robin page needs and the shared set cannot express.
 *
 * The shared `components/ui.jsx` carries Button, Card, StatCard, Badge, Field,
 * Modal, Notice, Toggle, Checkbox, Spinner, ErrorNote, EmptyState, inputClass,
 * useAsync and JsonView. Three things are missing for this page, and all three are
 * built here rather than added to the shared file, which a feature may not edit.
 * The integrator promotes a recurring one into `ui.jsx` as platform work, once.
 *
 * The design floor is met in this file rather than only in the page: 44px touch
 * targets on every control, a visible text label beside every glyph, and no
 * status conveyed by colour alone.
 */

import { Icon } from '@/components/ui'

/**
 * The nav glyph: three reps arranged around a ring, with the ring itself drawn
 * as the rotation they move along.
 *
 * Passed as `path` rather than added to the shared `PATHS` map, because that map
 * is a shared file this feature may not edit.
 */
export const ROUND_ROBIN_ICON =
  'M12 3a9 9 0 100 18 9 9 0 000-18zm0 3.6a5.4 5.4 0 100 10.8 5.4 5.4 0 000-10.8zM12 6.6a2.4 2.4 0 100 4.8 2.4 2.4 0 000-4.8z'

const GLYPHS = {
  team: 'M8 11a3 3 0 100-6 3 3 0 000 6zm8 0a3 3 0 100-6 3 3 0 000 6zM2 19c0-3 2.7-5 6-5s6 2 6 5H2zm14-4.6c2.4.5 4 2.2 4 4.6h-5c0-1.6-.5-3-1.4-4.1.8-.3 1.6-.5 2.4-.5z',
  ring: 'M12 3a9 9 0 100 18 9 9 0 000-18zm0 4.2a4.8 4.8 0 100 9.6 4.8 4.8 0 000-9.6z',
  gate: 'M12 2l8 3v6c0 5-3.4 9.4-8 11-4.6-1.6-8-6-8-11V5l8-3zm-1 13.6l6-6-1.4-1.4-4.6 4.6-2-2L7.6 12l3.4 3.6z',
  warn: 'M12 2l10 18H2L12 2zm0 5v7m0 3v.5',
  booking: 'M7 3v3m10-3v3M4 9h16M5 5h14a1 1 0 011 1v14a1 1 0 01-1 1H5a1 1 0 01-1-1V6a1 1 0 011-1z',
  weight: 'M12 3a9 9 0 100 18 9 9 0 000-18zm0 5.4a3.6 3.6 0 110 7.2 3.6 3.6 0 010-7.2z',
  return: 'M3 12a9 9 0 109-9 9 9 0 00-6.4 2.7L3 8m0-5v5h5',
}

/**
 * A round robin mode chip: the mode's own glyph plus its own words.
 *
 * Strict and Flexible differ by meaning rather than by hue, which is exactly
 * where colour alone fails. Each carries a glyph and a label, so the mode never
 * depends on the reader distinguishing two shades.
 */
const MODE_SPEC = {
  strict: { label: 'Strict', hint: 'equal turns', glyph: 'ring' },
  flexible: { label: 'Flexible', hint: 'weighted by availability', glyph: 'weight' },
}

export function ModeChip({ mode }) {
  const spec = MODE_SPEC[mode] || { label: mode, hint: '', glyph: 'ring' }
  return (
    <span className="inline-flex items-center gap-1.5 rounded-xs border border-border-subtle bg-muted px-2 py-1 text-xs text-foreground">
      <Icon path={GLYPHS[spec.glyph]} size={13} />
      <span className="font-medium">{spec.label}</span>
      <span className="text-muted-foreground">{spec.hint}</span>
    </span>
  )
}

/**
 * Why a member cannot be assigned, as a chip with the reason in words.
 *
 * The researched licensing rule is a hard gate, not a warning, so an excluded
 * member is shown with the words "no Concierge license" rather than a red dot. A
 * colour alone would leave an admin unable to tell a licensing failure from a
 * calendar that is not connected, and those need different fixes.
 */
const EXCLUSION_TONE = {
  'no Concierge license': { tone: 'delete', glyph: 'gate' },
  'calendar not connected': { tone: 'restore', glyph: 'warn' },
}

export function ExclusionChip({ reason }) {
  if (!reason) return null
  const spec = EXCLUSION_TONE[reason] || { tone: 'neutral', glyph: 'warn' }
  const classes =
    spec.tone === 'delete'
      ? 'border-destructive/30 bg-destructive/10 text-destructive'
      : spec.tone === 'restore'
        ? 'border-warning/30 bg-warning/10 text-warning'
        : 'border-border-subtle bg-muted text-muted-foreground'
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-xs border px-2 py-1 text-xs ${classes}`}
    >
      <Icon path={GLYPHS[spec.glyph]} size={13} />
      {reason}
    </span>
  )
}

/**
 * A key/value pair on one line, used in the detail panels.
 *
 * Not a `<dt>`/`<dd>` pair so it can be used in a flex row without a `<dl>`
 * around it, and it keeps the label from wrapping away from its value.
 */
export function Fact({ label, children, mono = true }) {
  return (
    <div className="flex min-w-0 gap-2 text-xs">
      <span className="shrink-0 text-muted-foreground">{label}</span>
      <span className={`min-w-0 truncate ${mono ? 'font-mono text-foreground' : 'text-foreground'}`}>
        {children}
      </span>
    </div>
  )
}

/**
 * The researched sentence a behaviour came from, quoted.
 *
 * Every published constant in this workflow carries the sentence that fixes it, so
 * the page shows the sentence rather than asserting the value. A reviewer can then
 * disagree with the research on screen instead of in a diff.
 */
export function Quote({ children, source }) {
  return (
    <figure className="rounded-sm border-l-2 border-accent bg-background/60 px-3 py-2">
      <blockquote className="text-xs text-foreground/90 italic">{children}</blockquote>
      {source && <figcaption className="mt-1 text-xs text-muted-foreground">{source}</figcaption>}
    </figure>
  )
}

/**
 * A short heading for a group of related fields, with an optional count.
 *
 * `count` is in mono because it is a machine value, not prose.
 */
export function Subhead({ children, count }) {
  return (
    <h3 className="mb-2 flex items-center gap-2 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
      <span>{children}</span>
      {count !== undefined && <span className="font-mono text-foreground/70">{count}</span>}
    </h3>
  )
}

/**
 * An inline note, for a warning beside a count or an explanation under a rule.
 *
 * `Notice` is named in `docs/FEATURE-CONTRACT.md` as a shared primitive, but
 * `components/ui.jsx` does not export one. Rather than edit that shared file,
 * which a feature may not touch, the primitive is built here and the discrepancy
 * is named in the pull request so the integrator can promote it once.
 *
 * The tone is carried by a border colour *and* by the words beside it, never by
 * colour alone.
 */
export function Notice({ children, tone = 'info' }) {
  const tones = {
    info: 'border-border-subtle bg-muted text-muted-foreground',
    warning: 'border-warning/40 bg-warning/10 text-foreground',
    danger: 'border-destructive/40 bg-destructive/10 text-foreground',
    good: 'border-accent/30 bg-accent-soft text-foreground',
  }
  const labels = { info: 'Note', warning: 'Warning', danger: 'Problem', good: 'OK' }
  return (
    <div
      className={`flex items-start gap-2 rounded-sm border px-3 py-2 text-sm ${tones[tone] || tones.info}`}
      role={tone === 'danger' ? 'alert' : 'status'}
    >
      <span className="shrink-0 text-[11px] font-semibold tracking-[0.14em] uppercase opacity-80">
        {labels[tone] || labels.info}
      </span>
      <span className="min-w-0">{children}</span>
    </div>
  )
}

/**
 * One member's row in a team: who they are, whether they can be assigned, and how
 * much free time they hold in the current window.
 *
 * The free-time bar is paired with its number in words. A bar alone is a chart the
 * reader has to interpret, and "3 of 5 hours free" is the fact an admin needs
 * when they are deciding whether a Flexible rotation is reaching the right rep.
 */
export function MemberRow({ member, freeMinutes, windowMinutes }) {
  const ratio =
    windowMinutes > 0 && typeof freeMinutes === 'number' ? freeMinutes / windowMinutes : 0
  const percent = Math.round(Math.max(0, Math.min(1, ratio)) * 100)
  return (
    <li className="flex min-h-11 items-center justify-between gap-3 border-b border-border-subtle py-2 last:border-b-0">
      <div className="min-w-0">
        <p className="truncate text-sm text-foreground">{member.name || member.member_id}</p>
        <p className="truncate font-mono text-xs text-muted-foreground">{member.member_id}</p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {typeof freeMinutes === 'number' && (
          <span className="text-xs text-muted-foreground">
            <span className="font-mono text-foreground">{freeMinutes}</span> min free
          </span>
        )}
        {member.eligible ? (
          <span
            className="h-2 w-16 overflow-hidden rounded-xs bg-muted"
            role="img"
            aria-label={`${percent} percent of the window is free for this member`}
          >
            <span className="block h-full bg-accent" style={{ width: `${percent}%` }} />
          </span>
        ) : (
          <ExclusionChip reason={member.excluded_reason} />
        )}
      </div>
    </li>
  )
}