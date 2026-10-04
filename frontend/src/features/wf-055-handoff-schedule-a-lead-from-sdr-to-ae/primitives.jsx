/**
 * Primitives the handoff scheduler page needs and the shared set cannot express.
 *
 * The shared `components/ui.jsx` carries Button, Card, StatCard, Badge, Field,
 * Spinner, ErrorNote, EmptyState, Icon, inputClass, useAsync and JsonView. Two
 * things are missing for this page, and both are built here rather than added to
 * the shared file, which a feature may not edit. The integrator promotes a
 * recurring one into `ui.jsx` as platform work, once.
 *
 * The design floor is met in this file rather than only in the page: 44px touch
 * targets on every control, a visible text label beside every glyph, and no
 * status conveyed by colour alone.
 */

import { Icon } from '@/components/ui'

/**
 * The nav glyph: one SDR handing a slot to one AE, drawn as two figures with the
 * slot passing between them.
 *
 * Passed as `path` rather than added to the shared `PATHS` map, because that map is
 * a shared file this feature may not edit.
 */
export const HANDOFF_ICON =
  'M3 17h4l3-5 3 5h4M3 7h4l2 3M17 10h4M15 4h6v6h-6zM8 4v3M6 5.5h4'

const GLYPHS = {
  handoff: 'M3 17h4l3-5 3 5h4M3 7h4l2 3M17 10h4M15 4h6v6h-6z',
  person: 'M8 11a3 3 0 100-6 3 3 0 000 6zm8 0a3 3 0 100-6 3 3 0 000 6zM2 19c0-3 2.7-5 6-5s6 2 6 5H2zm14-4.6c2.4.5 4 2.2 4 4.6h-5c0-1.6-.5-3-1.4-4.1.8-.3 1.6-.5 2.4-.5z',
  gate: 'M12 2l8 3v6c0 5-3.4 9.4-8 11-4.6-1.6-8-6-8-11V5l8-3zm-1 13.6l6-6-1.4-1.4-4.6 4.6-2-2L7.6 12l3.4 3.6z',
  warn: 'M12 2l10 18H2L12 2zm0 5v7m0 3v.5',
  clock: 'M12 3a9 9 0 100 18 9 9 0 000-18zm0 4.2v5.3l3.4 2',
  book: 'M7 3v3m10-3v3M4 9h16M5 5h14a1 1 0 011 1v14a1 1 0 01-1 1H5a1 1 0 01-1-1V6a1 1 0 011-1z',
}

/**
 * Whether a path is bookable, as a chip with the reason in words.
 *
 * A path with no free time is not an error and is not a colour. It is the busy-week
 * case, and an SDR needs to read *why* before they give up on it, so the chip
 * carries the people whose calendars emptied it. Every state carries a glyph and
 * words, so the state never depends on the reader telling two shades apart.
 */
const AVAILABILITY_SPEC = {
  open: { tone: 'good', glyph: 'clock', label: 'times on offer' },
  empty: { tone: 'warn', glyph: 'warn', label: 'no free time' },
  unresolved: { tone: 'warn', glyph: 'warn', label: 'a named user left the pod' },
}

function availabilityClasses(tone) {
  if (tone === 'good') return 'border-accent/30 bg-accent-soft text-foreground'
  if (tone === 'warn') return 'border-warning/30 bg-warning/10 text-foreground'
  return 'border-border-subtle bg-muted text-muted-foreground'
}

export function AvailabilityChip({ path }) {
  if (!path) return null
  const unresolved = path.window?.unresolved_user_ids?.length || 0
  const key = unresolved ? 'unresolved' : path.slot_count ? 'open' : 'empty'
  const spec = AVAILABILITY_SPEC[key]
  const busy = path.window?.busy_user_ids || []
  const gated = path.window?.gating_user_ids || []
  const ignored = path.window?.ignored_user_ids || []
  const detail =
    key === 'empty'
      ? `no free time. Busy: ${busy.length ? busy.join(', ') : 'the window'}`
      : key === 'unresolved'
        ? `not on this pod any more: ${unresolved.join(', ')}`
        : `${path.slot_count} start times. Gated by: ${gated.join(', ') || 'nobody'}` +
          (ignored.length ? `. Calendar not read: ${ignored.join(', ')}` : '')
  return (
    <span
      className={`inline-flex items-start gap-1.5 rounded-xs border px-2 py-1 text-xs ${availabilityClasses(spec.tone)}`}
    >
      <Icon path={GLYPHS[spec.glyph]} size={13} />
      <span>
        <span className="font-medium">{spec.label}</span>
        <span className="block text-muted-foreground">{detail}</span>
      </span>
    </span>
  )
}

/**
 * Whether a meeting role is filled, as a chip.
 *
 * The two role names come from one researched sentence, "meeting created with SDR
 * as Booker and AE as Assignee", so both are shown with their own words rather than
 * as an avatar and a colour.
 */
export function RoleChip({ role, person }) {
  const label = role === 'booker' ? 'Booker, the SDR' : 'Assignee, the AE'
  return (
    <span className="inline-flex items-center gap-1.5 rounded-xs border border-border-subtle bg-muted px-2 py-1 text-xs text-foreground">
      <Icon path={GLYPHS.person} size={13} />
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium">{person || 'unassigned'}</span>
    </span>
  )
}

/**
 * Whether a user may be assigned a handoff, as a chip with the reason in words.
 *
 * The fix for a missing role is a workspace edit and the fix for an unconnected
 * calendar is a calendar connection, and those need different fixes. Colour alone
 * could not tell an SDR which one they are looking at.
 */
export function AssignableChip({ user }) {
  if (!user) return null
  if (user.assignable) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-xs border border-accent/30 bg-accent-soft px-2 py-1 text-xs text-foreground">
        <Icon path={GLYPHS.gate} size={13} />
        may be assigned a handoff
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1.5 rounded-xs border border-warning/30 bg-warning/10 px-2 py-1 text-xs text-foreground">
      <Icon path={GLYPHS.warn} size={13} />
      {user.assignable_reason || 'may not be assigned a handoff'}
    </span>
  )
}

/**
 * A key/value pair on one line, used in the detail panels.
 *
 * Not a `<dt>`/`<dd>` pair so it can be used in a flex row without a `<dl>` around
 * it, and it keeps the label from wrapping away from its value.
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

/** A short heading for a group of related fields, with an optional count. */
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
 * `components/ui.jsx` exports no Notice. Rather than edit that shared file, which a
 * feature may not touch, the primitive is built here and the discrepancy is named
 * in the pull request so the integrator can promote it once.
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

/** One routing path as a selectable row, with its gate set spelled out. */
export function PathRow({ path, selected, onSelect, disabled }) {
  const match = Object.entries(path.match || {})
    .map(([key, value]) => `${key}=${Array.isArray(value) ? value.join('|') : value}`)
    .join(' and ')
  return (
    <button
      type="button"
      onClick={onSelect}
      disabled={disabled}
      aria-pressed={selected}
      className={`min-h-11 w-full rounded-sm border px-3 py-2 text-left disabled:cursor-not-allowed disabled:opacity-60 ${
        selected ? 'border-accent bg-accent-soft' : 'border-border-subtle bg-surface hover:border-accent'
      }`}
    >
      <span className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-sm font-medium text-foreground">{path.path_name || path.path_id}</span>
        <span className="font-mono text-xs text-muted-foreground">{path.path_id}</span>
      </span>
      <span className="mt-1 block text-xs text-muted-foreground">
        Assignee {path.assignee_name || path.assignee_ref}. Match: {match || 'any request'}
      </span>
      <span className="mt-1.5 block">
        <AvailabilityChip path={path} />
      </span>
    </button>
  )
}

/** One booking row: the two researched roles, the time, and the state in words. */
export function MeetingRow({ meeting, onCancel }) {
  return (
    <li className="flex min-h-11 flex-wrap items-center justify-between gap-3 rounded-sm border border-border-subtle px-3 py-2">
      <div className="min-w-0">
        <p className="truncate text-sm text-foreground">
          {meeting.data.assignee_name || meeting.data.assignee_ref}
        </p>
        <p className="truncate font-mono text-xs text-muted-foreground">
          {meeting.data.start_at} to {meeting.data.end_at}
        </p>
        <p className="truncate font-mono text-xs text-muted-foreground">
          booked by {meeting.data.booker_name || meeting.data.booker_ref}, path{' '}
          {meeting.data.path_id}
        </p>
      </div>
      <div className="flex shrink-0 flex-wrap items-center gap-2">
        <RoleChip role="booker" person={meeting.data.booker_name || meeting.data.booker_ref} />
        <RoleChip role="assignee" person={meeting.data.assignee_name || meeting.data.assignee_ref} />
        {meeting.data.state === 'confirmed' && onCancel ? (
          <button
            type="button"
            onClick={onCancel}
            className="min-h-11 rounded-sm border border-border-subtle bg-surface px-3 text-sm text-foreground hover:border-accent hover:text-accent"
          >
            Cancel this meeting
          </button>
        ) : (
          <span className="inline-flex items-center gap-1.5 rounded-xs border border-border-subtle bg-muted px-2 py-1 text-xs text-muted-foreground">
            <Icon path={GLYPHS.book} size={13} />
            {meeting.data.state}
          </span>
        )}
      </div>
    </li>
  )
}
