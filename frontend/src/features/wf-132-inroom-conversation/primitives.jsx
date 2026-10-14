/**
 * WF-132's own primitives.
 *
 * These are the small pieces the conversation page is built from. They are not in
 * `@/components/ui` because they are specific to this workflow's shapes, and the
 * contract is explicit that a feature builds what it needs inside its own folder
 * and says so, rather than editing the shared file.
 *
 * Every state carries a word as well as a tone, because the design floor says a
 * status is never conveyed by colour alone.
 */

import { Badge, Card } from '@/components/ui'

import { ANCHOR_WORDS, KIND_WORDS, SIDE_WORDS, STATE_TONES, STATE_WORDS } from './api'

/** A titled block. The heading is a real `h2` so the page has a readable outline. */
export function Section({ title, description, action, children }) {
  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="font-display text-lg font-semibold text-foreground">{title}</h2>
          {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  )
}

export function StateBadge({ state }) {
  return <Badge tone={STATE_TONES[state] || 'neutral'}>{STATE_WORDS[state] || state}</Badge>
}

/** The anchored room object, shown as a machine value so it matches its id. */
export function AnchorChip({ anchor }) {
  if (!anchor) return null
  const kind = ANCHOR_WORDS[anchor.type] || anchor.type
  return (
    <span className="inline-flex items-center gap-2 rounded-xs border border-border-subtle bg-muted px-2 py-0.5 font-mono text-xs text-muted-foreground">
      <span>{kind}</span>
      <span aria-hidden="true">/</span>
      <span className="text-foreground">{anchor.label || anchor.id}</span>
    </span>
  )
}

export function CommentRow({ comment }) {
  return (
    <li className="rounded-sm border border-border-subtle bg-surface p-3">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="font-mono text-foreground">{comment.author}</span>
        <span className="text-muted-foreground">{SIDE_WORDS[comment.author_side] || 'Unknown'}</span>
        <Badge tone="neutral">{KIND_WORDS[comment.kind] || comment.kind}</Badge>
        <span className="font-mono text-muted-foreground">{comment.at}</span>
      </div>
      <p className="mt-2 text-sm text-foreground">{comment.body}</p>
      {comment.mentions?.length > 0 && (
        <ul className="mt-2 flex flex-wrap gap-2">
          {comment.mentions.map((mention) => (
            <li key={mention.id}>
              <Badge tone={mention.notified ? 'info' : 'restore'}>
                {`@${mention.handle} ${mention.notified ? 'notified' : 'unresolved'}`}
              </Badge>
            </li>
          ))}
        </ul>
      )}
    </li>
  )
}

/** One row in the thread list. A button, so it is reachable by keyboard. */
export function ThreadRow({ thread, active, onSelect }) {
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        aria-current={active ? 'true' : undefined}
        className={`flex min-h-11 w-full flex-col gap-2 rounded-sm border p-3 text-left transition-colors
          ${
            active
              ? 'border-accent bg-accent-soft'
              : 'border-border-subtle bg-surface hover:border-accent'
          }`}
      >
        <span className="flex flex-wrap items-center gap-2">
          <StateBadge state={thread.state} />
          <AnchorChip anchor={thread.anchor} />
          {thread.unresolved_mentions > 0 && (
            <Badge tone="restore">{`${thread.unresolved_mentions} unresolved tag`}</Badge>
          )}
          {thread.unanswered_questions > 0 && (
            <Badge tone="warning">{`${thread.unanswered_questions} unanswered`}</Badge>
          )}
        </span>
        {thread.last_comment && (
          <span className="text-sm text-muted-foreground">
            {`${thread.last_comment.author}: ${thread.last_comment.body}`}
          </span>
        )}
        <span className="font-mono text-xs text-muted-foreground">
          {`${thread.message_count} message(s) - ${thread.last_activity_at}`}
        </span>
      </button>
    </li>
  )
}

export function EscalationRow({ escalation }) {
  return (
    <li className="rounded-sm border border-warning/30 bg-warning/10 p-3 text-sm">
      <p className="font-medium text-warning">{'Unanswered buyer question'}</p>
      <p className="mt-1 text-muted-foreground">
        {`Escalated to ${escalation.escalated_to || 'the account owner'} after ${escalation.hours_open} hours.`}
      </p>
      <p className="mt-1 font-mono text-xs text-muted-foreground">{`thread ${escalation.thread_ref}`}</p>
    </li>
  )
}

/** The before/after a champion's edit produced, field by field. */
export function DeltaList({ delta }) {
  const entries = Object.entries(delta || {})
  if (entries.length === 0) {
    return <p className="text-sm text-muted-foreground">{'No field changed in this edit.'}</p>
  }
  return (
    <ul className="space-y-1">
      {entries.map(([field, change]) => (
        <li key={field} className="font-mono text-xs text-muted-foreground">
          <span className="text-foreground">{field}</span>
          {`: ${String(change.from)} -> ${String(change.to)}`}
        </li>
      ))}
    </ul>
  )
}

export function ChampionOnlyNote({ children, className = '' }) {
  return (
    <Card className={className}>
      <p className="text-sm text-muted-foreground">{children}</p>
    </Card>
  )
}
