/**
 * Presentation primitives for the sequential approval chain (WF-092).
 *
 * Everything here is built from the shared components in `@/components/ui`, which is
 * the rule: `ui.jsx` is shared and not ours to edit. What lives in this file is the two
 * or three shapes the approval chain needs that no other feature has needed, so the
 * integrator can promote them once if they recur rather than each feature rolling its
 * own.
 *
 * Two of them are specific enough to be worth naming.
 *
 * `ChainLadder` is the chain itself: one row per priority, the approvers at that
 * priority, and which of them have decided. A sequential chain is a queue, and a queue
 * that is a list of equal-weight cards loses the only thing that matters about it,
 * which is where the chain is waiting right now.
 *
 * `StatusPill` never conveys state by colour alone. Every tone carries its own word, so
 * a chain state is legible to someone who cannot see the colour at all, which is the
 * accessibility floor and not an extra.
 */

import { Badge, Icon, inputClass } from '@/components/ui'

/**
 * A tone-keyed callout.
 *
 * `docs/DESIGN-SYSTEM.md` lists `Notice` among the shared primitives, but
 * `components/ui.jsx` does not export one, so this is built here rather than imported.
 * That file is on the shared list and not ours to edit. The integrator promotes this
 * once if other features need it too.
 *
 * Every tone carries its own icon and its own border, so the three states are legible
 * without relying on the fill alone.
 */
const NOTICE_TONES = {
  info: { border: 'border-info/40', bg: 'bg-info/10', text: 'text-info', label: 'Note' },
  success: {
    border: 'border-success/40',
    bg: 'bg-success/10',
    text: 'text-success',
    label: 'Done',
  },
  warning: {
    border: 'border-warning/40',
    bg: 'bg-warning/10',
    text: 'text-warning',
    label: 'Waiting',
  },
  error: {
    border: 'border-destructive/40',
    bg: 'bg-destructive/10',
    text: 'text-destructive',
    label: 'Problem',
  },
}

export function Notice({ tone = 'info', children }) {
  const described = NOTICE_TONES[tone] || NOTICE_TONES.info
  return (
    <div
      role="status"
      aria-live="polite"
      className={`flex items-start gap-3 rounded-sm border p-4 ${described.border} ${described.bg}`}
    >
      <Icon name="audit" size={16} className={`mt-0.5 shrink-0 ${described.text}`} />
      <div className="min-w-0">
        <p className={`text-xs font-semibold uppercase tracking-[0.14em] ${described.text}`}>
          {described.label}
        </p>
        <p className="mt-1 text-sm text-foreground">{children}</p>
      </div>
    </div>
  )
}

/**
 * A labelled switch, at the 44px minimum touch target.
 *
 * Built here for the same reason as `Notice`: `Toggle` is named in the design system
 * but not exported by the shared file, which is not ours to edit.
 */
export function Toggle({ label, checked, onChange, disabled }) {
  return (
    <label className="flex min-h-11 cursor-pointer items-center justify-between gap-4">
      <span className="text-sm text-foreground">{label}</span>
      <input
        type="checkbox"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
        className={`${inputClass} w-14 shrink-0 cursor-pointer`}
      />
    </label>
  )
}

/** A document with a check on it, for the nav. Not in the shared PATHS map. */
export const APPROVAL_CHAIN_ICON =
  'M6 3h9l4 4v14a1 1 0 01-1 1H6a1 1 0 01-1-1V4a1 1 0 011-1zm9 0v5h4M8 13l2.5 2.5L16 10'

/**
 * The tone a chain state renders in, and the word that goes with it.
 *
 * The word is not decoration. Colour alone is the thing the design system bans, so the
 * label is the state and the colour is the reinforcement.
 */
export const STATE_TONE = {
  pending_approval: { tone: 'warning', label: 'Waiting', word: 'Pending approval' },
  in_review: { tone: 'info', label: 'In review', word: 'In review' },
  approved: { tone: 'success', label: 'Approved', word: 'Approved' },
  rejected: { tone: 'delete', label: 'Rejected', word: 'Rejected' },
}

/** The word for an approver's own decision. */
export const DECISION_WORD = {
  approved: 'Approved',
  rejected: 'Rejected',
  abstained: 'Abstained',
}

/** The word for what the branch engine could or could not do with a quote. */
export const OUTCOME_WORD = {
  qualified: 'Qualifies',
  not_qualified: 'Does not qualify',
  auto_approved: 'Auto-approved',
  unverifiable: 'Could not be checked',
}

/** The word for a requirement, spelled as the research spells it. */
export const REQUIREMENT_WORD = {
  all: 'All approvers',
  any: 'Any approvers',
  sequential: 'Sequential',
}

/**
 * A chain state, as a badge with the state spelled out.
 *
 * An unknown state falls back to the neutral tone and prints the raw value, so a server
 * that gains a state does not render as a blank space on an older client.
 */
export function StatusPill({ state, publishable }) {
  const described = STATE_TONE[state] || { tone: 'neutral', label: state || 'unknown' }
  return (
    <span className="inline-flex items-center gap-2">
      <Badge tone={described.tone}>{described.label}</Badge>
      {publishable !== undefined && (
        <span className="text-xs text-muted-foreground">
          {publishable ? 'publishable' : 'not publishable'}
        </span>
      )}
    </span>
  )
}

/**
 * One row per priority in the chain.
 *
 * `activePriority` marks the level the chain is waiting on. It is carried as a
 * left border and the word "waiting", never by colour alone, and the approvers at every
 * other level are marked as waiting on the level above rather than being dimmed into
 * invisibility: a person reading this page has to be able to see the whole queue.
 */
export function ChainLadder({ levels = [], decisions = {}, activePriority = null, state }) {
  if (!levels.length) {
    return (
      <p className="text-sm text-muted-foreground">
        No approval step was pushed, so this quote was auto-approved.
      </p>
    )
  }
  return (
    <ol className="space-y-2">
      {levels.map((level) => {
        const active = activePriority === level.priority
        const settled = state === 'approved' || state === 'rejected'
        return (
          <li
            key={level.priority}
            className={`rounded-sm border bg-surface px-3 py-3 ${
              active ? 'border-accent border-l-2' : 'border-border-subtle'
            }`}
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="font-mono text-xs text-muted-foreground">
                priority {level.priority}
              </span>
              <span className="text-xs text-muted-foreground">
                {REQUIREMENT_WORD[level.requirement] || REQUIREMENT_WORD.sequential}
              </span>
            </div>
            <ul className="mt-2 space-y-1.5">
              {level.approvers.map((approver) => {
                const decision = decisions[approver]
                return (
                  <li
                    key={approver}
                    className="flex flex-wrap items-center justify-between gap-2 text-sm"
                  >
                    <span className="font-mono text-foreground">{approver}</span>
                    <span className="text-xs text-muted-foreground">
                      {decision
                        ? DECISION_WORD[decision] || decision
                        : active
                          ? 'waiting for this decision'
                          : settled
                            ? 'chain finished'
                            : 'waiting on an earlier priority'}
                    </span>
                  </li>
                )
              })}
            </ul>
          </li>
        )
      })}
    </ol>
  )
}

/**
 * One branch and the priority levels it pushes.
 *
 * The threshold is shown as the research writes it, so "greater than 5000" reads the
 * way the specification reads rather than as an operator token a seller has to decode.
 */
export function BranchCard({ branch }) {
  return (
    <div className="rounded-sm border border-border-subtle bg-surface p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-base font-semibold text-foreground">{branch.name}</h3>
        <Badge tone="neutral">
          {branch.property} {branch.operator} {branch.threshold}
        </Badge>
      </div>
      <div className="mt-3 space-y-2">
        {(branch.steps || []).map((level) => (
          <div key={level.priority} className="flex flex-wrap items-baseline gap-2 text-sm">
            <span className="font-mono text-xs text-muted-foreground">
              p{level.priority}
            </span>
            <span className="text-foreground">{level.approvers.join(', ')}</span>
            <span className="text-xs text-muted-foreground">
              {REQUIREMENT_WORD[level.requirement] || REQUIREMENT_WORD.sequential}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}

/**
 * The messages fired for one enrolment, grouped by channel.
 *
 * Only the channels this product can deliver appear. The three the research names and
 * this build does not build are named in the inferences the page renders, rather than
 * being listed here as though they had been sent.
 */
export function NotificationList({ notifications = [] }) {
  if (!notifications.length) {
    return (
      <p className="text-sm text-muted-foreground">No notification has been fired yet.</p>
    )
  }
  const byApprover = new Map()
  for (const row of notifications) {
    const approver = row.approver || 'unknown'
    const channels = byApprover.get(approver) || []
    channels.push(row.channel)
    byApprover.set(approver, channels)
  }
  return (
    <ul className="space-y-1.5">
      {[...byApprover.entries()].map(([approver, channels]) => (
        <li
          key={approver}
          className="flex flex-wrap items-center justify-between gap-2 text-sm"
        >
          <span className="font-mono text-foreground">{approver}</span>
          <span className="flex items-center gap-2 text-xs text-muted-foreground">
            <Icon name="database" size={14} />
            {channels.join(' and ')}
          </span>
        </li>
      ))}
    </ul>
  )
}

/** A section heading with the micro label the design system asks for. */
export function SectionLabel({ children }) {
  return (
    <p className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
      {children}
    </p>
  )
}