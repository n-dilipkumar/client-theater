/**
 * WF-134's own small primitives.
 *
 * `docs/DESIGN-SYSTEM.md` and `docs/FEATURE-CONTRACT.md` both list `Notice`
 * among the primitives available from `@/components/ui`, and the shipped
 * `ui.jsx` has no such export. That is a contradiction in the repository's own
 * documentation rather than a gap in this feature, and the contract's
 * instruction for exactly this case is to build what is needed inside the
 * feature folder and say so, so the integrator can promote the ones that recur.
 *
 * Everything here follows the design system: semantic tokens only, `rounded-sm`,
 * a hairline instead of a shadow, no emoji as an icon, and every state carried by
 * a word first and a colour second. `prefers-reduced-motion` is honoured globally
 * in `index.css`; nothing below adds motion of its own.
 *
 * A `Button` from `@/components/ui` already meets the 44px touch target, so every
 * control here is that component rather than a hand-rolled `<button>`.
 */

import { Badge, Button, Card, Icon } from '@/components/ui'

import {
  bandTone,
  bandWord,
  daysSince,
  factorPercent,
  factorWord,
  outcomeWord,
  reasonWord,
} from './api.js'

const TONES = {
  neutral: 'border-border-subtle bg-muted text-muted-foreground',
  info: 'border-info/30 bg-info/10 text-info',
  success: 'border-success/30 bg-success/10 text-success',
  warning: 'border-warning/30 bg-warning/10 text-warning',
  destructive: 'border-destructive/30 bg-destructive/10 text-destructive',
}

const TONE_ICON = {
  info: 'M12 8h.01M11 12h1v5h1M21 12a9 9 0 11-18 0 9 9 0 0118 0z',
  success: 'M5 12l5 5 9-11',
  warning: 'M12 9v4m0 4h.01M10.3 4.3L2.6 18a2 2 0 001.7 3h15.4a2 2 0 001.7-3L13.7 4.3a2 2 0 00-3.4 0z',
  destructive: 'M12 9v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z',
}

/**
 * A short standing message. The tone sets the colour and the icon repeats it, so
 * the meaning survives without colour.
 */
export function Notice({ tone = 'neutral', title, children, action }) {
  const iconPath = TONE_ICON[tone]
  return (
    <div
      role="status"
      className={`flex flex-wrap items-start gap-3 rounded-sm border p-4 ${TONES[tone] || TONES.neutral}`}
    >
      {iconPath && <Icon path={iconPath} className="mt-0.5 shrink-0" />}
      <div className="min-w-0 flex-1">
        {title && <p className="text-sm font-semibold">{title}</p>}
        {children && <div className="mt-1 text-sm">{children}</div>}
      </div>
      {action}
    </div>
  )
}

/** A band as a badge whose word is the state and whose colour only repeats it. */
export function BandBadge({ band, value }) {
  const tone = bandTone(band)
  const shown = typeof value === 'number' ? `${value} / 100` : null
  return (
    <span className="inline-flex items-center gap-2">
      <Badge tone={tone}>{bandWord(band)}</Badge>
      {shown && <span className="font-mono text-sm text-muted-foreground">{shown}</span>}
    </span>
  )
}

/**
 * One factor of the score: its name, how full it is, and what it contributes.
 *
 * The bar is decorative; the numbers beside it are the state. A reader who cannot
 * see the bar still reads "Engagement 94 percent, contributing 47 of 100".
 */
export function FactorRow({ name, factor, weight, contribution }) {
  const percent = factorPercent(factor)
  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="text-sm font-medium text-foreground">{factorWord(name)}</span>
        <span className="font-mono text-xs text-muted-foreground">
          {percent}% of full, weight {Math.round((Number(weight) || 0) * 100)}%, adds{' '}
          {contribution ?? 0} points
        </span>
      </div>
      <div
        role="img"
        aria-label={`${factorWord(name)} ${percent} percent of full`}
        className="h-2 w-full overflow-hidden rounded-xs bg-muted"
      >
        <div className="h-full bg-accent" style={{ width: `${percent}%` }} />
      </div>
      <Working factor={factor} name={name} />
    </div>
  )
}

/** The working behind one factor, in the machine face. */
function Working({ factor, name }) {
  if (name === 'engagement') {
    return (
      <p className="font-mono text-[11px] text-muted-foreground">
        {factor?.events ?? 0} event(s), {factor?.events_in_window ?? 0} in the{' '}
        {factor?.window_days ?? 0}-day window, last {daysSince(factor?.days_since_last)}
      </p>
    )
  }
  if (name === 'content') {
    const unreviewed = factor?.unreviewed || []
    return (
      <p className="font-mono text-[11px] text-muted-foreground">
        {factor?.reviewed ?? 0} of {factor?.required ?? 0} required asset(s) reviewed
        {unreviewed.length ? `, still owed: ${unreviewed.join(', ')}` : ''}
      </p>
    )
  }
  return (
    <p className="font-mono text-[11px] text-muted-foreground">
      {factor?.engaged ?? 0} of {factor?.total ?? 0} engaged, {Math.round((factor?.coverage || 0) * 100)}
      % against a {Math.round((factor?.target || 0) * 100)}% target
    </p>
  )
}

/** The active risk reasons, each as a word and the sentence behind it. */
export function ReasonList({ score }) {
  const reasons = score?.reasons || []
  const sentences = score?.reason_sentences || []
  if (!reasons.length) {
    return (
      <p className="text-sm text-muted-foreground">
        No risk reason is active. The score is above the alerting band.
      </p>
    )
  }
  return (
    <ul className="space-y-2">
      {reasons.map((reason, index) => (
        <li key={reason} className="flex items-start gap-2">
          <Icon path={TONE_ICON.warning} className="mt-0.5 shrink-0 text-warning" />
          <div className="min-w-0">
            <p className="text-sm font-medium text-foreground">{reasonWord(reason)}</p>
            <p className="text-xs text-muted-foreground">{sentences[index] || ''}</p>
          </div>
        </li>
      ))}
    </ul>
  )
}

/**
 * The stakeholder coverage map: who engaged, and who has not.
 *
 * Both columns always render, including when one is empty, because "nobody has
 * engaged" is the finding and an absent column would hide it.
 */
export function CoverageColumns({ coverage }) {
  const engaged = coverage?.engaged || []
  const missing = coverage?.not_engaged || []
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
      <div>
        <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
          Engaged ({engaged.length})
        </p>
        <People rows={engaged} empty="Nobody in the buying group has engaged yet." />
      </div>
      <div>
        <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
          Not engaged ({missing.length})
        </p>
        <People rows={missing} empty="Every named stakeholder has engaged." />
      </div>
    </div>
  )
}

function People({ rows, empty }) {
  if (!rows.length) return <p className="mt-2 text-sm text-muted-foreground">{empty}</p>
  return (
    <ul className="mt-2 space-y-1.5">
      {rows.map((person) => (
        <li key={person.id} className="flex flex-wrap items-baseline gap-2">
          <span className="text-sm text-foreground">{person.name}</span>
          {person.role && <span className="text-xs text-muted-foreground">{person.role}</span>}
          <span className="font-mono text-[11px] text-muted-foreground">
            {person.engagement_count || 0} event(s)
          </span>
        </li>
      ))}
    </ul>
  )
}

/** One key asset, with its review state and the control that changes it. */
export function ContentRow({ item, onToggle, busy }) {
  return (
    <li className="flex flex-wrap items-center justify-between gap-3 border-b border-border-subtle py-2 last:border-b-0">
      <div className="min-w-0">
        <p className="truncate text-sm text-foreground">{item.name}</p>
        <p className="font-mono text-[11px] text-muted-foreground">
          {item.kind} | {item.required ? 'required' : 'optional'} |{' '}
          {item.reviewed ? 'reviewed' : 'not reviewed'}
        </p>
      </div>
      <div className="flex items-center gap-2">
        <Badge tone={item.reviewed ? 'success' : item.required ? 'warning' : 'neutral'}>
          {item.reviewed ? 'Reviewed' : 'Not reviewed'}
        </Badge>
        <Button
          icon="restore"
          onClick={() => onToggle(item, !item.reviewed)}
          disabled={busy}
          aria-label={`Mark ${item.name} as ${item.reviewed ? 'not reviewed' : 'reviewed'}`}
        >
          {item.reviewed ? 'Mark unreviewed' : 'Mark reviewed'}
        </Button>
      </div>
    </li>
  )
}

/** One risk alert, with its state and the control that closes it. */
export function AlertRow({ alert, onResolve, busy }) {
  return (
    <li className="flex flex-wrap items-start justify-between gap-3 border-b border-border-subtle py-2 last:border-b-0">
      <div className="min-w-0">
        <p className="text-sm text-foreground">{alert.detail}</p>
        <p className="font-mono text-[11px] text-muted-foreground">
          {alert.kind} | value {alert.value} | band {alert.band} | raised {alert.raised_at}
        </p>
      </div>
      <div className="flex items-center gap-2">
        <Badge tone={alert.open ? 'destructive' : 'neutral'}>
          {alert.open ? 'Open' : 'Resolved'}
        </Badge>
        <Button
          onClick={() => onResolve(alert)}
          disabled={busy || !alert.open}
          aria-label={`Resolve the ${alert.kind} alert`}
        >
          Resolve
        </Button>
      </div>
    </li>
  )
}

/** One coaching cue, with its outcome state and the three closing controls. */
export function CueRow({ cue, onOutcome, busy }) {
  return (
    <li className="space-y-2 border-b border-border-subtle py-3 last:border-b-0">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-medium text-foreground">{reasonWord(cue.reason)}</p>
          <p className="text-sm text-muted-foreground">{cue.action}</p>
          <p className="font-mono text-[11px] text-muted-foreground">
            severity {cue.severity} | value {cue.value} | rep {cue.rep || 'unassigned'}
          </p>
        </div>
        <Badge tone={cue.open ? 'warning' : 'success'}>{outcomeWord(cue.state)}</Badge>
      </div>
      {cue.open && (
        <div className="flex flex-wrap gap-2">
          <Button icon="restore" onClick={() => onOutcome(cue, 'actioned')} disabled={busy}>
            Actioned
          </Button>
          <Button icon="restore" onClick={() => onOutcome(cue, 'acknowledged')} disabled={busy}>
            Acknowledged
          </Button>
          <Button icon="trash" onClick={() => onOutcome(cue, 'dismissed')} disabled={busy}>
            Dismissed
          </Button>
        </div>
      )}
    </li>
  )
}

/** The closed loop's readiness data, with its denominator named. */
export function ReadinessPanel({ readiness }) {
  const index = readiness || {}
  const hasData = typeof index.readiness === 'number'
  return (
    <div className="space-y-2">
      <p className="text-sm text-foreground">
        {hasData
          ? `${Math.round(index.readiness * 100)}% of coaching cues were actioned.`
          : 'No coaching cue has been closed yet, so there is no readiness reading.'}
      </p>
      <p className="font-mono text-[11px] text-muted-foreground">
        {index.actioned ?? 0} actioned, {index.acknowledged ?? 0} acknowledged,{' '}
        {index.dismissed ?? 0} dismissed of {index.outcomes ?? 0} outcome(s)
        {hasData ? `, participation ${Math.round((index.participation || 0) * 100)}%` : ''}
      </p>
    </div>
  )
}

/** A titled card, so the page reads as a stack of named findings. */
export function Section({ title, hint, action, children }) {
  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-lg font-semibold text-foreground">{title}</h2>
          {hint && <p className="mt-1 text-xs text-muted-foreground">{hint}</p>}
        </div>
        {action}
      </div>
      <div className="mt-4">{children}</div>
    </Card>
  )
}
