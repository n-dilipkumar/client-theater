/**
 * WF-068's primitives, and the small pure helpers the page and its tests share.
 *
 * Everything here is drawn from `@/components/ui`. The only thing built locally is
 * the nav glyph, passed as a path because it is not in the shared `PATHS` map and
 * that file is not ours to edit.
 *
 * Three rules from the design system shape what follows, and each one is a way this
 * page would otherwise be quietly wrong:
 *
 * * **No status by colour alone.** Every state carries its own word. The state
 *   machine has three statuses and a refusal has an error code, and a seller who
 *   reads only the colour cannot tell "the sequence is paused" from "the contact
 *   had no parent account".
 * * **Mono for machine values.** A flow instance id, a CRM id, a count and a
 *   `1/5` position label are machine values. A sentence is not.
 * * **Every icon sits beside a text label.** No glyph is ever the only label.
 */

import { Badge } from '@/components/ui'

/**
 * The nav glyph: a checklist moving into a queue.
 *
 * Passed as a path rather than added to the shared `PATHS` map, which is a shared
 * file and not this feature's to edit.
 */
export const FOLLOWUP_ICON =
  'M4 6h10M4 12h10M4 18h7m7-13l2 2 4-4M17 13v6m-3-3h6'

/**
 * The word for each of the three researched flow-instance statuses.
 *
 * Sourced rather than invented: the enum is `Running`, `Pending`, `Paused` and the
 * page shows the vendor's own value rather than a paraphrase of it. A caller
 * comparing our page with a Gong response should see the same three words.
 */
export const STATUS_WORD = {
  Running: 'Running',
  Pending: 'Pending',
  Paused: 'Paused',
}

/**
 * The tone for each status.
 *
 * A second signal beside the word, never instead of it. Paused is `warning` rather
 * than `destructive`: the sequence stopped, and nothing failed.
 */
export const STATUS_TONE = {
  Running: 'insert',
  Pending: 'update',
  Paused: 'restore',
}

/** The three statuses, in the order the research names them. */
export const STATUSES = ['Running', 'Pending', 'Paused']

/** The researched error codes, as words rather than as enum values alone. */
export const ASSIGN_ERROR_WORD = {
  InvalidArgument: 'the request named something that does not exist',
  InvalidState: 'the flow or the assignee was in the wrong state',
  UnexpectedError: 'the request failed for a reason we did not expect',
}

/** The word for each researched error code, for the split answer. */
export function assignErrorWord(code) {
  return ASSIGN_ERROR_WORD[code] || 'the request failed'
}

/**
 * The readable label for a trigger kind.
 *
 * Served by the backend and passed in rather than written here, because the
 * research supplies the wording ("is created", "is updated with") and a paraphrase
 * would put a second, drifting copy of the product's own language on the page.
 */
export function triggerLabel(automation, labels = {}) {
  const kind = automation?.trigger?.kind
  return labels[kind] || kind || 'unknown trigger'
}

/**
 * Whether an automation is a date trigger.
 *
 * A date trigger behaves differently from every other trigger: it does not run on
 * an event at all. A page that drew it the same way would suggest it fires the
 * moment it is saved.
 */
export function isDateTrigger(automation) {
  return automation?.trigger?.kind === 'current_date_is'
}

/**
 * The position label for one to-do, as the researched string.
 *
 * The backend already sends `position_label` as `1/5`. This is the fallback for a
 * to-do that reached the page through a path that did not build a queue, so the
 * column is never blank where the store knows the count.
 */
export function positionLabel(todo) {
  if (todo?.position_label) return todo.position_label
  if (!todo) return '0/0'
  const position = Number(todo.position || 0)
  const total = Number(todo.queue_total || 0)
  return total ? `${position}/${total}` : `${position}/?`
}

/**
 * The five researched activity kinds, as words for the feed headings.
 *
 * The backend sends the keys; these are the English. A heading a seller reads has
 * to be a sentence, and the API must not carry display copy.
 */
export const ACTIVITY_WORD = {
  calls: 'Calls',
  emails: 'Emails',
  digital_interactions: 'Digital interactions',
  crm_changes: 'CRM changes',
  texts: 'Texts',
}

/** One status, as a badge that names the state and never relies on its colour. */
export function StatusBadge({ status }) {
  return (
    <Badge tone={STATUS_TONE[status] || 'neutral'}>
      {STATUS_WORD[status] || String(status || 'unknown')}
    </Badge>
  )
}

/**
 * A gate quote, so the page shows the vendor's own sentence.
 *
 * The two researched gates are refusals whose whole point is that the failure is
 * not silent. A paraphrase would hide the sentence an operator has to act on.
 */
export function GateQuote({ children }) {
  return (
    <p className="rounded-sm border-l-2 border-border-subtle pl-3 text-sm italic text-muted-foreground">
      {children}
    </p>
  )
}

/** A machine value: ids, counts, timestamps. Mono, and never used for prose. */
export function MachineValue({ children, className = '' }) {
  return <span className={`font-mono text-[13px] ${className}`}>{children}</span>
}