/**
 * WF-117's local primitives.
 *
 * They are drawn from `@/components/ui` rather than re-implemented: `Badge`,
 * `Card` and `Icon` come from the shared set. Anything here only composes them,
 * so this feature cannot drift from the product's badge or card style.
 */

import { Badge, Card, Icon } from '@/components/ui'

import {
  idlePhrase,
  outcomeLabel,
  outcomeTone,
  reasonLabel,
  reasonTone,
  stepLabel,
} from './api'

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

/** A decision's reason, always as a word. The tone is a second signal. */
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

/** One sweep in a list. The whole row is a button so the target is large. */
export function SweepRow({ sweep, selected, onSelect }) {
  return (
    <li>
      <button
        type="button"
        onClick={() => onSelect(sweep.id)}
        aria-pressed={selected}
        className={`flex min-h-11 w-full flex-col gap-1 rounded-sm border px-3 py-2 text-left
          ${selected ? 'border-accent bg-accent-soft' : 'border-border-subtle bg-surface hover:border-accent'}`}
      >
        <span className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium text-foreground">{sweep.name || sweep.id}</span>
          <Badge tone={sweep.published ? 'insert' : 'neutral'}>
            {sweep.published ? 'Published' : 'Draft'}
          </Badge>
        </span>
        <span className="text-xs text-muted-foreground">
          {sweep.threshold_days} idle days · {sweep.cadence_steps} cadence step
          {sweep.cadence_steps === 1 ? '' : 's'} · {sweep.inactivity_basis}
        </span>
      </button>
    </li>
  )
}

/** The decisions one sweep evaluation produced, as a list. */
export function DecisionList({ decisions }) {
  if (!decisions || decisions.length === 0) {
    return <p className="text-sm text-muted-foreground">No opportunity is in this room yet.</p>
  }
  return (
    <ul className="flex flex-col gap-2">
      {decisions.map((decision) => (
        <li
          key={decision.opportunity_id}
          className="flex flex-wrap items-center justify-between gap-2 rounded-sm border border-border-subtle bg-surface px-3 py-2"
        >
          <span className="flex flex-col">
            <span className="text-sm text-foreground">{decision.name || decision.opportunity_id}</span>
            <span className="text-xs text-muted-foreground">
              {decision.stage || 'No stage'} · {idlePhrase(decision)}
              {decision.matched && decision.step ? ` · ${stepLabel(decision.step.action)}` : ''}
            </span>
          </span>
          <ReasonBadge reason={decision.reason} />
        </li>
      ))}
    </ul>
  )
}

/** One run's per-opportunity results. */
export function RunResults({ results }) {
  if (!results || results.length === 0) {
    return <p className="text-sm text-muted-foreground">This run produced no result rows.</p>
  }
  return (
    <ul className="flex flex-col gap-2">
      {results.map((result) => (
        <li
          key={`${result.opportunity_id}-${result.outcome}`}
          className="flex flex-wrap items-center justify-between gap-2 rounded-sm border border-border-subtle bg-surface px-3 py-2"
        >
          <span className="flex flex-col">
            <span className="text-sm text-foreground">{result.name || result.opportunity_id}</span>
            <span className="text-xs text-muted-foreground">
              {idlePhrase(result)}
              {result.step ? ` · ${stepLabel(result.step)}` : ''}
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

/** A glyph for the Salesforce-style pipeline. Passed by path, never added to PATHS. */
export function PipelineGlyph(props) {
  return <Icon path="M4 6h16M4 12h16M4 18h10" {...props} />
}
