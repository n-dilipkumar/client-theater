/**
 * Local presentation pieces for WF-030.
 *
 * The design system says a primitive that genuinely does not exist belongs in the
 * feature folder until the integrator promotes it, and these are the two this
 * workflow needed: a status pill that reads a workflow's lifecycle, and a decision
 * list that renders the *reasons* an evaluation reports rather than only its
 * counts. Everything else comes from `@/components/ui`.
 *
 * The accessibility floor is the same as everywhere else: 44px minimum touch
 * targets, visible focus, a text label beside every icon, and no emoji as an icon.
 * The one glyph here is a `<Icon path=...>` rather than an addition to the shared
 * `PATHS` map, because that map is not ours to edit.
 */

import { Badge, Icon } from '@/components/ui'

/**
 * "A signal going out on a timer" - the workflow fires continuously off activity.
 *
 * Passed as a `path` rather than an `icon` name because this glyph is not in the
 * shared `PATHS` map and adding it there would be an edit to a shared file.
 */
export const WORKFLOW_ICON =
  'M12 2a10 10 0 100 20 10 10 0 000-20-0zm1 5v5l4 2-1 1.7-5-2.5V7z'

/** The lifecycle vocabulary, mapped to the badge tones `ui.jsx` already defines. */
const STATUS_TONE = {
  published: 'insert',
  draft: 'neutral',
  withdrawn: 'delete',
}

const STATUS_MEANING = {
  published:
    'Published. DSR activity drives this workflow with no further setup, and it fires continuously on matching activity.',
  draft:
    'Draft. It is saved and listed, but nothing fires until it is published, and its definition may still be amended.',
  withdrawn:
    'Withdrawn. It will not fire and is not amended in place, but the enrollments taken under it still name it.',
}

export function StatusPill({ status }) {
  return (
    <Badge tone={STATUS_TONE[status] || 'neutral'}>
      <span className="inline-flex items-center gap-1.5">
        {status}
        <span className="sr-only">. {STATUS_MEANING[status] || ''}</span>
      </span>
    </Badge>
  )
}

/** The full sentence behind a status, visible rather than screen-reader only. */
export function StatusMeaning({ status }) {
  return <p className="text-xs text-muted-foreground">{STATUS_MEANING[status] || ''}</p>
}

const ACTION_TONE = {
  planned: 'insert',
  refused: 'delete',
  unresolved: 'update',
}

const ACTION_MEANING = {
  planned: 'Planned - recorded, not executed.',
  refused: 'Refused - this action could not apply this time; the rest of the workflow still did.',
  unresolved:
    'Unresolved - an action kind outside the four the research names. Stored and reported, not dropped, because the source says "and more!".',
}

/**
 * One resolved action: its status, the write it would make, and why.
 *
 * The status is a pill and the reason is a sentence, because "refused" with no
 * explanation is the shape of a bug report rather than the shape of an answer.
 */
export function ActionRow({ action }) {
  return (
    <li className="flex flex-col gap-1 border-t border-border-subtle/30 py-2 first:border-t-0">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-[13px] text-foreground">{action.kind}</span>
        <Badge tone={ACTION_TONE[action.status] || 'neutral'}>{action.status}</Badge>
        {action.executed === false && (
          <span className="text-xs text-muted-foreground">not executed</span>
        )}
      </div>
      {action.write && <WriteSummary write={action.write} />}
      <p className="text-xs text-muted-foreground">
        {action.reason || ACTION_MEANING[action.status]}
      </p>
      {action.api && (
        <p className="font-mono text-[11px] text-muted-foreground/70">would call {action.api}</p>
      )}
    </li>
  )
}

function WriteSummary({ write }) {
  const entries = Object.entries(write).filter(([key]) => key !== 'kind')
  if (entries.length === 0) return null
  return (
    <p className="font-mono text-[12px] text-sky-300">
      {entries
        .map(([key, value]) => `${key}=${value === null || value === undefined ? '—' : String(value)}`)
        .join('  ')}
    </p>
  )
}

/**
 * A lint finding, at the severity the server gave it.
 *
 * `info` and `warning` are distinguished by tone rather than by colour alone, and
 * every row carries its code in text, so the objection is readable without relying
 * on a hue.
 */
export function FindingRow({ finding }) {
  const tone = finding.severity === 'warning' ? 'update' : 'neutral'
  return (
    <li className="flex flex-col gap-1 border-t border-border-subtle/30 py-2 first:border-t-0">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={tone}>{finding.severity}</Badge>
        <span className="font-mono text-[12px] text-muted-foreground">{finding.code}</span>
        {finding.field && (
          <span className="font-mono text-[12px] text-muted-foreground/70">{finding.field}</span>
        )}
      </div>
      <p className="text-xs text-muted-foreground">{finding.message}</p>
    </li>
  )
}

/**
 * The banner that repeats the research's own sentence.
 *
 * "Nothing happens on the seller's screen" is the most easily lost fact in this
 * workflow and the most expensive to lose, because a seller who believes an
 * enrollment is a task will stop acting on the ones that are. It is rendered at the
 * top of the page rather than in a footer, every time.
 */
export function ActionabilityNote({ note }) {
  if (!note) return null
  return (
    <div className="flex items-start gap-3 rounded-lg border border-border-subtle/40 bg-muted/40 p-4">
      <span className="mt-0.5 text-accent">
        <Icon path={WORKFLOW_ICON} size={18} />
      </span>
      <p className="text-sm text-muted-foreground">{note}</p>
    </div>
  )
}

/**
 * One "why did this not fire" row.
 *
 * A workflow that enrolled nobody is a question, and this is the answer: the
 * reason, the count of how many events fell that way, and a sample of the events.
 */
export function MissRow({ miss }) {
  return (
    <li className="flex flex-col gap-2 border-t border-border-subtle/30 py-3 first:border-t-0">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-[13px] text-foreground">{miss.name}</span>
        <Badge tone="neutral">{miss.reason}</Badge>
        <span className="text-xs text-muted-foreground">
          {miss.events_considered} event(s) considered
        </span>
      </div>
      <p className="text-xs text-muted-foreground">{miss.detail}</p>
      {(miss.sample || []).length > 0 && (
        <ul className="ml-3 flex flex-col gap-1 border-l border-border-subtle/30 pl-3">
          {miss.sample.map((row) => (
            <li key={`${row.family}-${row.activity_id}-${row.reason}`} className="text-xs">
              <span className="font-mono text-[11px] text-muted-foreground">{row.family}</span>
              <span className="px-1 text-muted-foreground/60">·</span>
              <span className="font-mono text-[11px] text-sky-300">{row.reason}</span>
              <p className="text-muted-foreground/80">{row.detail}</p>
            </li>
          ))}
        </ul>
      )}
    </li>
  )
}

/**
 * One "this could not even be considered" row.
 *
 * Step 1 is a check a person performs before building a workflow, so its two
 * failures are reported per workflow rather than refused globally: one unconnected
 * room does not invalidate a workflow for every other room.
 */
export function SkipRow({ skip }) {
  return (
    <li className="flex flex-col gap-1 border-t border-border-subtle/30 py-2 first:border-t-0">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-[13px] text-foreground">{skip.name}</span>
        <Badge tone="update">{skip.reason}</Badge>
      </div>
      <p className="text-xs text-muted-foreground">{skip.detail}</p>
    </li>
  )
}
