/**
 * WF-137's own primitives.
 *
 * Everything here is built from `@/components/ui`, and every control meets the
 * accessibility floor: `min-h-11` on each button (the shared `Button` applies
 * it), a visible text label beside each icon, and no status carried by colour
 * alone.
 *
 * These are local because `ui.jsx` has no equivalent, and building them here is
 * the contract's instruction rather than an oversight. Promotion work for the
 * integrator, not a reason to edit a shared file:
 *
 *   - `InlineNote`, because `ui.jsx` has no `Notice` and this page needs a line
 *     that states a fact about what it did or could not do.
 *   - `StateBadge`, because a lifecycle or retention state is a finding and the
 *     word beside the colour is what makes it a finding.
 *   - `FindingList`, because an off-brand room is a list of named findings.
 *   - `VersionRow`, `PhaseList` and `MilestoneRow`, because the history and the
 *     onboarding plan are lists of facts a seller acts on.
 */

import { Badge, Button, Icon } from '@/components/ui'

import {
  FINDING_LABELS,
  LIFECYCLE_LABELS,
  RETENTION_LABELS,
  TRIGGER_LABELS,
  shortTimestamp,
  stateTone,
} from './api'

/**
 * An inline note about something this page cannot do, or did not do.
 *
 * Local rather than shared, because `ui.jsx` has no `Notice`. Each tone names
 * its meaning in words beside the colour, so the meaning never rests on the
 * colour alone.
 */
export function InlineNote({ tone = 'neutral', children }) {
  const tones = {
    neutral: 'border-border-subtle bg-muted text-muted-foreground',
    warning: 'border-warning/40 bg-warning/10 text-warning',
    info: 'border-info/40 bg-info/10 text-info',
    success: 'border-success/40 bg-success/10 text-success',
    danger: 'border-destructive/40 bg-destructive/10 text-destructive',
  }
  return (
    <p
      className={`flex items-start gap-2 rounded-sm border px-3 py-2 text-xs ${
        tones[tone] || tones.neutral
      }`}
      role="status"
      aria-live="polite"
    >
      <Icon name="schema" size={14} className="mt-0.5 shrink-0" />
      <span>{children}</span>
    </p>
  )
}

/** A lifecycle, retention or brand state as a word plus a tone. */
export function StateBadge({ state, kind = 'lifecycle' }) {
  const labels = kind === 'retention' ? RETENTION_LABELS : LIFECYCLE_LABELS
  const label = labels[state] || (state ? state.charAt(0).toUpperCase() + state.slice(1) : 'Unknown')
  return <Badge tone={stateTone(state)}>{label}</Badge>
}

/** Every finding that keeps a room off brand, one row each. */
export function FindingList({ findings }) {
  if (!findings || findings.length === 0) {
    return <p className="text-sm text-muted-foreground">This room is on brand. No findings.</p>
  }
  return (
    <ul className="flex flex-col gap-2" aria-label="Brand findings">
      {findings.map((finding) => (
        <li
          key={`${finding.token}-${finding.code}`}
          className="flex flex-col gap-0.5 border-b border-border-subtle pb-2 last:border-b-0 last:pb-0"
        >
          <span className="flex flex-wrap items-center gap-2">
            <Badge tone="restore">{FINDING_LABELS[finding.code] || finding.code}</Badge>
            <span className="font-mono text-xs text-muted-foreground">{finding.token}</span>
          </span>
          <span className="text-xs text-muted-foreground">{finding.message}</span>
        </li>
      ))}
    </ul>
  )
}

/** One version in a room's history, with the actions a seller may take on it. */
export function VersionRow({ version, head, onPreview, onRestore, onBranch, busy }) {
  return (
    <li className="flex flex-wrap items-center justify-between gap-3 border-b border-border-subtle py-3 last:border-b-0">
      <span className="flex min-w-0 flex-col">
        <span className="flex items-center gap-2">
          <span className="font-mono text-sm text-foreground">v{version.version}</span>
          <Badge tone="update">{TRIGGER_LABELS[version.trigger] || version.trigger}</Badge>
          {head === version.version && <Badge tone="insert">Head</Badge>}
        </span>
        <span className="truncate text-xs text-muted-foreground">
          {version.label || 'untitled'} | {version.author || 'unknown'} |{' '}
          {shortTimestamp(version.at)}
          {version.restored_from ? ` | restored from v${version.restored_from}` : ''}
        </span>
      </span>
      <span className="flex flex-wrap items-center gap-2">
        <Button icon="search" disabled={busy} onClick={() => onPreview(version.version)}>
          Preview
        </Button>
        <Button icon="restore" disabled={busy} onClick={() => onRestore(version.version)}>
          Restore
        </Button>
        <Button icon="plus" disabled={busy} onClick={() => onBranch(version.version)}>
          Branch
        </Button>
      </span>
    </li>
  )
}

/** One milestone, with the control that closes it. */
export function MilestoneRow({ phaseKey, milestone, onComplete, busy }) {
  const done = milestone.state === 'complete'
  return (
    <li className="flex flex-wrap items-center justify-between gap-2 py-1.5">
      <span className="flex items-center gap-2">
        <span className="text-xs text-foreground">{milestone.name}</span>
        <Badge tone={done ? 'insert' : 'neutral'}>{done ? 'Complete' : 'Pending'}</Badge>
      </span>
      <Button
        icon="plus"
        disabled={busy || done}
        onClick={() => onComplete(phaseKey, milestone.key)}
      >
        {done ? 'Done' : 'Mark complete'}
      </Button>
    </li>
  )
}

/** The phased onboarding plan instantiated on a room. */
export function PhaseList({ plan, onComplete, busy }) {
  const phases = plan?.phases || []
  if (phases.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        This room has no onboarding plan yet. Repurpose it at closed-won to add one.
      </p>
    )
  }
  return (
    <ul className="flex flex-col gap-4" aria-label="Onboarding phases">
      {phases.map((phase) => (
        <li key={phase.phase_key} className="flex flex-col gap-1 border-b border-border-subtle pb-3 last:border-b-0 last:pb-0">
          <span className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium text-foreground">{phase.name}</span>
            <Badge tone={phase.state === 'complete' ? 'insert' : phase.state === 'active' ? 'update' : 'neutral'}>
              {phase.state}
            </Badge>
            <span className="font-mono text-xs text-muted-foreground">
              {phase.milestones.length - phase.open_milestones.length}/{phase.milestones.length}{' '}
              milestone(s)
            </span>
          </span>
          <ul className="flex flex-col">
            {phase.milestones.map((milestone) => (
              <MilestoneRow
                key={milestone.key}
                phaseKey={phase.phase_key}
                milestone={milestone}
                onComplete={onComplete}
                busy={busy}
              />
            ))}
          </ul>
        </li>
      ))}
    </ul>
  )
}

/** A retention window's two dates, each printed as a number and a date. */
export function RetentionWindow({ retention }) {
  if (!retention?.has_policy) {
    return <p className="text-sm text-muted-foreground">This room has no retention policy yet.</p>
  }
  return (
    <dl className="grid gap-4 sm:grid-cols-3">
      <div className="flex flex-col gap-0.5">
        <dt className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
          Archive at
        </dt>
        <dd className="font-mono text-sm text-foreground">{shortTimestamp(retention.archive_at)}</dd>
        <dd className="text-xs text-muted-foreground">After {retention.archive_after_days} day(s)</dd>
      </div>
      <div className="flex flex-col gap-0.5">
        <dt className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
          Delete at
        </dt>
        <dd className="font-mono text-sm text-foreground">{shortTimestamp(retention.delete_at)}</dd>
        <dd className="text-xs text-muted-foreground">After {retention.delete_after_days} day(s)</dd>
      </div>
      <div className="flex flex-col gap-0.5">
        <dt className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">Due now</dt>
        <dd className="text-sm text-foreground">
          {retention.due ? 'Yes' : 'No'}
          {retention.archive_due ? ' - archive' : ''}
          {retention.delete_due ? ' - delete' : ''}
        </dd>
        <dd className="text-xs text-muted-foreground">
          {retention.legal_basis ? `Basis: ${retention.legal_basis}` : 'No legal basis recorded'}
        </dd>
      </div>
    </dl>
  )
}
