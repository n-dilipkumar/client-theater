/**
 * Local presentation pieces for WF-029.
 *
 * The design system says a primitive that genuinely does not exist belongs in the
 * feature folder until the integrator promotes it, and these are the ones this
 * workflow needed: a signed score that states its sign in text as well as in tone, a
 * contribution row that names the criterion and the events that produced the points,
 * a run-finding list, and the banner that repeats the research's own sentence.
 * Everything else comes from `@/components/ui`.
 *
 * The accessibility floor is the same as everywhere else: 44px minimum touch
 * targets, visible focus, a text label beside every icon, and no emoji as an icon.
 * The one glyph here is an `<Icon path=...>` rather than an addition to the shared
 * `PATHS` map, because that map is not ours to edit.
 */

import { Badge, Icon } from '@/components/ui'

/**
 * 'A score being added to' - the whole workflow is DSR activity moving a number on a
 * contact property.
 *
 * Passed as a `path` rather than an `icon` name because this glyph is not in the
 * shared `PATHS` map and adding it there would be an edit to a shared file.
 */
export const LEAD_SCORE_ICON =
  'M12 3v18M7 7h7a3 3 0 010 6H9a3 3 0 000 6h8'

/** The tone for a signed score. Never the only signal: the sign is in the text too. */
function scoreTone(score) {
  if (score > 0) return 'insert'
  if (score < 0) return 'delete'
  return 'neutral'
}

const SCORE_MEANING = {
  positive: 'above zero. Matching activity added to this contact.',
  zero: 'at zero. Nothing this workflow recognises has moved it.',
  negative: 'below zero. Matching activity subtracted from this contact.',
}

/** The sentence behind a signed score, visible rather than screen-reader only. */
export function scoreMeaning(score) {
  if (score > 0) return SCORE_MEANING.positive
  if (score < 0) return SCORE_MEANING.negative
  return SCORE_MEANING.zero
}

/**
 * A contact's score, with its sign spelled out.
 *
 * The number alone is ambiguous at a glance - `-10` is a deduction and a rendering
 * artefact look alike - so the sign is a word and the meaning is a sentence. Tone is
 * the third signal, never the first.
 */
export function ScorePill({ score }) {
  const value = Number(score || 0)
  return (
    <span className="inline-flex flex-col items-start gap-0.5">
      <Badge tone={scoreTone(value)}>
        <span className="inline-flex items-center gap-1.5">
          <span className="font-mono">{value > 0 ? `+${value}` : String(value)}</span>
          <span className="sr-only">. {scoreMeaning(value)}</span>
        </span>
      </Badge>
      <span className="text-[11px] text-muted-foreground">{scoreMeaning(value)}</span>
    </span>
  )
}

/**
 * One contribution: which criterion scored, how many events it matched, and the
 * points that is worth.
 *
 * A total with no breakdown is a number a seller cannot act on, so the criterion is
 * named and the event count is shown beside the points.
 */
export function ContributionRow({ contribution }) {
  return (
    <li className="flex flex-col gap-1 border-t border-border-subtle/30 py-2 first:border-t-0">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[13px] text-foreground">
          {contribution.label || contribution.criterion_id || 'untitled criterion'}
        </span>
        <Badge tone={contribution.bucket === 'negative' ? 'delete' : 'insert'}>
          {contribution.bucket}
        </Badge>
        <span className="font-mono text-[13px] text-muted-foreground">
          {contribution.family}
        </span>
        <span className="font-mono text-[13px] text-foreground">
          {contribution.points > 0 ? `+${contribution.points}` : contribution.points} points
        </span>
      </div>
      <p className="text-xs text-muted-foreground">
        {contribution.events} matching event(s).{' '}
        {contribution.property_resolved
          ? `Written to the ${contribution.score_property} contact property.`
          : `Written to ${contribution.score_property}, which this build does not provision. The points are computed; no property of that name is written.`}
      </p>
    </li>
  )
}

/**
 * A lint finding on a saved criterion, at the severity the server gave it.
 *
 * `info` and `warning` are distinguished by tone rather than by colour alone, and
 * every row carries its code in text, so the objection is readable without relying on
 * a hue.
 */
export function LintRow({ lint }) {
  const tone = lint.severity === 'warning' ? 'update' : 'neutral'
  return (
    <li className="flex flex-col gap-1 border-t border-border-subtle/30 py-2 first:border-t-0">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={tone}>{lint.severity}</Badge>
        <span className="font-mono text-[12px] text-muted-foreground">{lint.code}</span>
        {lint.field && (
          <span className="font-mono text-[12px] text-muted-foreground/70">{lint.field}</span>
        )}
      </div>
      <p className="text-xs text-muted-foreground">{lint.message}</p>
    </li>
  )
}

/**
 * A finding on a scoring run.
 *
 * A run that moved nothing is still the record of the rule having fired, so the
 * findings are on the run rather than only on the runs whose number changed.
 */
export function FindingRow({ finding }) {
  const tone = finding.severity === 'warning' ? 'update' : 'neutral'
  return (
    <li className="flex flex-col gap-1 border-t border-border-subtle/30 py-2 first:border-t-0">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={tone}>{finding.severity}</Badge>
        <span className="font-mono text-[12px] text-muted-foreground">{finding.code}</span>
      </div>
      <p className="text-xs text-muted-foreground">{finding.message}</p>
    </li>
  )
}

/**
 * The banner that repeats the research's own sentence.
 *
 * "Nothing happens on the seller's screen" is the most easily lost fact in this
 * workflow and the most expensive to lose, because a seller who believes a score
 * change is a task will stop acting on the ones that are. It is rendered at the top of
 * the page rather than in a footer, every time.
 */
export function ActionabilityNote({ note }) {
  if (!note) return null
  return (
    <div className="flex items-start gap-3 rounded-sm border border-border-subtle bg-muted p-4">
      <span className="mt-0.5 text-accent">
        <Icon path={LEAD_SCORE_ICON} size={18} />
      </span>
      <p className="text-sm text-muted-foreground">{note}</p>
    </div>
  )
}

/**
 * One row of the CRM write plan, with the reason it was not executed.
 *
 * The plan is the honest half of this workflow: this build holds no token for either
 * researched scope, so every request is recorded rather than sent. Saying that on the
 * row is what stops a recorded request reading as a completed one.
 */
export function PlanRow({ request }) {
  return (
    <li className="flex flex-col gap-1 border-t border-border-subtle/30 py-2 first:border-t-0">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-[12px] text-foreground">{request.method}</span>
        <span className="font-mono text-[12px] text-muted-foreground">{request.purpose}</span>
        <Badge tone="update">not executed</Badge>
      </div>
      <p className="font-mono text-[11px] text-muted-foreground">{request.path}</p>
      <p className="text-xs text-muted-foreground">{request.endpoint}</p>
    </li>
  )
}

/**
 * One criterion that matched nothing this run, with the reason and the tally.
 *
 * A criterion that moved nobody's score is a question, and this is the answer: which
 * of the five Dock properties its events actually were, and what stopped it.
 */
export function CriterionRunRow({ criterion }) {
  return (
    <li className="flex flex-col gap-1 border-t border-border-subtle/30 py-3 first:border-t-0">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[13px] text-foreground">{criterion.label || criterion.criterion_id}</span>
        <Badge tone={criterion.bucket === 'negative' ? 'delete' : 'insert'}>
          {criterion.bucket}
        </Badge>
        <span className="font-mono text-[12px] text-muted-foreground">{criterion.family}</span>
        <Badge tone={criterion.matched_count > 0 ? 'insert' : 'neutral'}>{criterion.reason}</Badge>
      </div>
      <p className="text-xs text-muted-foreground">{criterion.detail}</p>
    </li>
  )
}
