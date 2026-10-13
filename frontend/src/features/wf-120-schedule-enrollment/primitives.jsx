/**
 * WF-120's local primitives.
 *
 * They are drawn from `@/components/ui` rather than re-implemented: `Badge`,
 * `Card` and `Icon` come from the shared set. Anything here only composes them,
 * so this feature cannot drift from the product's badge or card style.
 */

import { Badge, Card, Icon } from '@/components/ui'

import {
  filterPhrase,
  formatDateTime,
  outcomeLabel,
  outcomeTone,
  reasonLabel,
  reasonTone,
  runKindLabel,
  statusLabel,
  statusTone,
} from './api'

/** A calendar glyph. Passed by path so `components/ui.jsx` is not edited. */
export const SCHEDULE_GLYPH =
  'M7 3v3M17 3v3M4 8h16M5 5h14a1 1 0 011 1v13a1 1 0 01-1 1H5a1 1 0 01-1-1V6a1 1 0 011-1zM8 13h3v3H8z'

/** A labelled fact. The label sits beside the value, never in a tooltip. */
export function Fact({ label, hint, children }) {
  return (
    <div className="min-w-0">
      <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
        {label}
      </p>
      <p className="mt-1 text-sm text-foreground">{children}</p>
      {hint && <p className="mt-0.5 truncate text-xs text-muted-foreground">{hint}</p>}
    </div>
  )
}

/** An enrollment's state, always as a word. The tone is a second signal. */
export function StatusBadge({ status }) {
  return <Badge tone={statusTone(status)}>{statusLabel(status)}</Badge>
}

/** A decision's reason, always as a word. */
export function ReasonBadge({ reason }) {
  return <Badge tone={reasonTone(reason)}>{reasonLabel(reason)}</Badge>
}

/** A run result's outcome, always as a word. */
export function OutcomeBadge({ outcome }) {
  return <Badge tone={outcomeTone(outcome)}>{outcomeLabel(outcome)}</Badge>
}

/** An inline message. Tones use semantic tokens only. */
export function InlineNote({ tone = 'info', children }) {
  const tones = {
    info: 'border-info/30 bg-info/10 text-info',
    success: 'border-success/30 bg-success/10 text-success',
    warning: 'border-warning/30 bg-warning/10 text-warning',
    danger: 'border-destructive/30 bg-destructive/10 text-destructive',
  }
  return (
    <p className={`rounded-sm border px-3 py-2 text-sm ${tones[tone] || tones.info}`}>{children}</p>
  )
}

/** A small card shell for a titled block. */
export function Section({ title, action, children }) {
  return (
    <Card className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="font-display text-base font-semibold text-foreground">{title}</h2>
        {action}
      </div>
      {children}
    </Card>
  )
}

/** One schedule in a list. The whole row is a button so the target is large. */
export function ScheduleRow({ schedule, selected, onSelect }) {
  return (
    <li>
      <button
        type="button"
        onClick={() => onSelect(schedule.id)}
        aria-pressed={selected}
        className={`flex min-h-11 w-full flex-col gap-1 rounded-sm border px-3 py-2 text-left
          ${selected ? 'border-accent bg-accent-soft' : 'border-border-subtle bg-surface hover:border-accent'}`}
      >
        <span className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium text-foreground">{schedule.name || schedule.id}</span>
          <Badge tone={schedule.published ? 'insert' : 'neutral'}>
            {schedule.published ? 'Published' : 'Draft'}
          </Badge>
          <Badge tone="neutral">{schedule.object}</Badge>
        </span>
        <span className="text-xs text-muted-foreground">
          {filterPhrase(schedule)} · {schedule.filter_count} filter condition
          {schedule.filter_count === 1 ? '' : 's'} · {schedule.interval_minutes} minute interval
        </span>
      </button>
    </li>
  )
}

/** The decisions one evaluation produced, as a list. */
export function DecisionList({ decisions }) {
  if (!decisions || decisions.length === 0) {
    return <p className="text-sm text-muted-foreground">No record of this object is in the room.</p>
  }
  return (
    <ul className="flex flex-col gap-2">
      {decisions.map((decision) => (
        <li
          key={decision.record_id}
          className="flex flex-wrap items-center justify-between gap-2 rounded-sm border border-border-subtle bg-surface px-3 py-2"
        >
          <span className="flex flex-col">
            <span className="text-sm text-foreground">{decision.name || decision.record_id}</span>
            <span className="text-xs text-muted-foreground">
              {decision.object || 'No object'} · {decision.prior_status || 'no earlier enrolment'}
            </span>
          </span>
          <ReasonBadge reason={decision.reason} />
        </li>
      ))}
    </ul>
  )
}

/** The results one run produced. */
export function RunResults({ results }) {
  if (!results || results.length === 0) {
    return <p className="text-sm text-muted-foreground">This run produced no result rows.</p>
  }
  return (
    <ul className="flex flex-col gap-2">
      {results.map((result) => (
        <li
          key={`${result.record_id}-${result.outcome}`}
          className="flex flex-wrap items-center justify-between gap-2 rounded-sm border border-border-subtle bg-surface px-3 py-2"
        >
          <span className="flex flex-col">
            <span className="text-sm text-foreground">{result.name || result.record_id}</span>
            <span className="text-xs text-muted-foreground">
              {reasonLabel(result.reason)}
              {result.task_id ? ` · task ${result.task_id}` : ''}
              {result.error ? ` · ${result.error.detail || result.error.code}` : ''}
            </span>
          </span>
          <OutcomeBadge outcome={result.outcome} />
        </li>
      ))}
    </ul>
  )
}

/** One enrollment in a list. */
export function EnrollmentRow({ enrollment }) {
  return (
    <li className="flex flex-wrap items-center justify-between gap-2 rounded-sm border border-border-subtle bg-surface px-3 py-2">
      <span className="flex flex-col">
        <span className="text-sm text-foreground">
          {enrollment.object} · {enrollment.record_id}
        </span>
        <span className="text-xs text-muted-foreground">
          Enrolled {formatDateTime(enrollment.enrolled_at_ms)}
          {enrollment.re_enrollment ? ' · re-enrollment' : ''}
          {enrollment.delay_minutes ? ` · released ${formatDateTime(enrollment.release_at_ms)}` : ''}
        </span>
      </span>
      <span className="flex flex-wrap items-center gap-2">
        {enrollment.re_enrollment && <Badge tone="update">Re-enrollment</Badge>}
        <StatusBadge status={enrollment.status} />
      </span>
    </li>
  )
}

/** The workflow log, in the order the research writes it. */
export function EventLog({ events }) {
  if (!events || events.length === 0) {
    return <p className="text-sm text-muted-foreground">The workflow log is empty.</p>
  }
  return (
    <ul className="flex flex-col gap-2">
      {events.map((event) => (
        <li key={event.id} className="flex flex-wrap items-center justify-between gap-2 text-sm">
          <span className="text-foreground">
            {reasonLabel(event.kind)} · {event.record_id || event.schedule_id}
          </span>
          <span className="font-mono text-xs text-muted-foreground">
            {formatDateTime(event.at_ms)}
          </span>
        </li>
      ))}
    </ul>
  )
}

/** The researched user flow, numbered, from the vocabulary payload. */
export function UserFlow({ steps }) {
  if (!steps || steps.length === 0) return null
  return (
    <ol className="flex flex-col gap-2">
      {steps.map((entry) => (
        <li key={entry.step} className="flex gap-3 text-sm text-foreground">
          <span className="font-mono text-xs text-muted-foreground">{entry.step}.</span>
          <span>{entry.text}</span>
        </li>
      ))}
    </ol>
  )
}

/** A compact label for a run's kind. */
export function RunKindLabel({ kind }) {
  return <span className="text-sm text-foreground">{runKindLabel(kind)}</span>
}

/** A glyph for a clock. Passed by path, never added to PATHS. */
export function ClockGlyph(props) {
  return <Icon path="M12 7v5l3 2M12 21a9 9 0 100-18 9 9 0 000 18z" {...props} />
}
