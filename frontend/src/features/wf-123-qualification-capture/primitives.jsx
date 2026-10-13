/**
 * WF-123's local primitives.
 *
 * They compose the shared set in `@/components/ui` rather than re-implementing
 * it, so this feature cannot drift from the product's badge, card or button
 * style. Anything here is a layout that only this page needs.
 *
 * The buyer preview is deliberately the plainest thing on the page: it renders
 * the questions and their options and nothing else, because the research's one
 * hard rule is that the qualification summary is visible to the seller only.
 */

import { Badge, Card, Icon } from '@/components/ui'

import {
  answerLabel,
  bandLabel,
  bandTone,
  dimensionLabel,
  formatDateTime,
  formStatusLabel,
  routeLabel,
  scorePercent,
  submissionStatusLabel,
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

/** A form's status, always as a word. */
export function FormStatusBadge({ status }) {
  const tone = status === 'live' ? 'insert' : 'neutral'
  return <Badge tone={tone}>{formStatusLabel(status)}</Badge>
}

/** A submission's status, always as a word. */
export function SubmissionStatusBadge({ status }) {
  const tone = status === 'submitted' ? 'insert' : 'restore'
  return <Badge tone={tone}>{submissionStatusLabel(status)}</Badge>
}

/** The qualification band. The tone is a second signal, never the only one. */
export function BandBadge({ band }) {
  return <Badge tone={bandTone(band)}>{bandLabel(band)}</Badge>
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

/** A titled card shell. */
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

/**
 * The four researched dimensions as a checklist. A seller narrows a form by
 * unticking one; the labels come from the vocabulary so they cannot drift.
 */
export function DimensionChecklist({ dimensions, selected, onToggle }) {
  return (
    <fieldset className="flex flex-col gap-2">
      <legend className="text-[13px] font-medium text-foreground">Questions to ask</legend>
      {dimensions.map((dimension) => (
        <label
          key={dimension}
          htmlFor={`dimension-${dimension}`}
          className="flex min-h-11 items-center gap-3 rounded-sm border border-border-subtle bg-surface px-3 text-sm text-foreground"
        >
          <input
            id={`dimension-${dimension}`}
            type="checkbox"
            checked={selected.includes(dimension)}
            onChange={() => onToggle(dimension)}
            className="h-4 w-4 accent-accent"
          />
          {dimensionLabel(dimension)}
        </label>
      ))}
    </fieldset>
  )
}

/** One form in a list. The whole row is a button so the target is large. */
export function FormRow({ form, selected, onSelect, onPublish }) {
  return (
    <li className="flex flex-wrap items-center justify-between gap-3 rounded-sm border border-border-subtle bg-surface px-3 py-2">
      <button
        type="button"
        onClick={() => onSelect(form.id)}
        aria-pressed={selected}
        className="flex min-h-11 min-w-0 flex-1 flex-col items-start gap-1 text-left"
      >
        <span className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium text-foreground">{form.name}</span>
          <FormStatusBadge status={form.status} />
        </span>
        <span className="text-xs text-muted-foreground">
          {form.dimensions?.length || 0} questions · {form.respondents || 0} respondents
        </span>
      </button>
      {form.status !== 'live' && (
        <button
          type="button"
          onClick={() => onPublish(form.id)}
          className="inline-flex min-h-11 items-center gap-2 rounded-sm border border-border-subtle bg-surface px-4 text-sm text-foreground hover:border-accent hover:text-accent"
        >
          <Icon name="plus" />
          Publish
        </button>
      )}
    </li>
  )
}

/** One submission in the seller's list, with its score and band. */
export function SubmissionRow({ submission, selected, onSelect }) {
  return (
    <li>
      <button
        type="button"
        onClick={() => onSelect(submission.id)}
        aria-pressed={selected}
        className={`flex min-h-11 w-full flex-col gap-1 rounded-sm border px-3 py-2 text-left
          ${
            selected
              ? 'border-accent bg-accent-soft'
              : 'border-border-subtle bg-surface hover:border-accent'
          }`}
      >
        <span className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium text-foreground">
            {submission.form_name || 'Qualification'}
          </span>
          <SubmissionStatusBadge status={submission.status} />
          <BandBadge band={submission.band} />
        </span>
        <span className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <span className="font-mono">
            {submission.score === null || submission.score === undefined
              ? 'unscored'
              : `${submission.score}/${submission.max_score}`}
          </span>
          <span>{routeLabel(submission.route_target)}</span>
          <span>{formatDateTime(submission.submitted_at || submission.updated_at)}</span>
        </span>
      </button>
    </li>
  )
}

/** The seller's reading of one submission: its answers, score and events. */
export function SubmissionDetail({ submission, questions }) {
  if (!submission) return null
  const answered = Object.entries(submission.answers || {})
  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-4 sm:grid-cols-3">
        <Fact label="Score" hint={`${scorePercent(submission.score, submission.max_score)}% of the ceiling`}>
          <span className="font-mono">
            {submission.score === null || submission.score === undefined
              ? 'Unscored'
              : `${submission.score} / ${submission.max_score}`}
          </span>
        </Fact>
        <Fact label="Band">
          <BandBadge band={submission.band} />
        </Fact>
        <Fact label="Routed to">{routeLabel(submission.route_target)}</Fact>
      </div>

      <div className="flex flex-col gap-2">
        <h3 className="text-[13px] font-medium text-foreground">Answers</h3>
        {answered.length === 0 ? (
          <p className="text-sm text-muted-foreground">No answers saved yet.</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {answered.map(([dimension, value]) => (
              <li key={dimension} className="flex flex-wrap items-baseline gap-2 text-sm">
                <span className="text-muted-foreground">{dimensionLabel(dimension)}</span>
                <span className="text-foreground">
                  {answerLabel(
                    (questions || []).find((question) => question.id === dimension),
                    value,
                  )}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="flex flex-col gap-2">
        <h3 className="text-[13px] font-medium text-foreground">Lifecycle</h3>
        {(submission.events || []).length === 0 ? (
          <p className="text-sm text-muted-foreground">No events yet.</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {submission.events.map((event) => (
              <li key={event.id} className="flex flex-wrap items-center gap-2">
                <Icon name="refresh" />
                <span className="text-sm text-foreground">{event.event}</span>
                <span className="font-mono text-xs text-muted-foreground">
                  {formatDateTime(event.at)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

/**
 * What the buyer sees inside the room: the questions and their options, and a
 * sentence saying the qualification is the seller's. There is no score and no
 * band here, by construction: this component never reads them.
 */
export function BuyerPreview({ form }) {
  if (!form) {
    return <p className="text-sm text-muted-foreground">No live form is embedded in this room yet.</p>
  }
  return (
    <div className="flex flex-col gap-4">
      <div>
        <p className="font-display text-base font-semibold text-foreground">{form.name}</p>
        {form.intro && <p className="mt-1 text-sm text-muted-foreground">{form.intro}</p>}
      </div>
      <ol className="flex flex-col gap-4">
        {(form.questions || []).map((question) => (
          <li key={question.id} className="flex flex-col gap-2">
            <p className="text-sm font-medium text-foreground">
              {question.label}
              {question.required && <span className="ml-2 text-xs text-muted-foreground">required</span>}
            </p>
            <p className="text-sm text-muted-foreground">{question.question}</p>
            <ul className="flex flex-col gap-1">
              {(question.options || []).map((option) => (
                <li key={option.value} className="text-sm text-foreground">
                  {option.label}
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ol>
      <InlineNote tone="info">
        Your answers are visible to the seller. The score, the band and the routing decision are
        visible to the seller only.
      </InlineNote>
    </div>
  )
}
