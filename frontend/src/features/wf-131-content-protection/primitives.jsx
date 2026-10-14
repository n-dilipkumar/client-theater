/**
 * WF-131's own primitives.
 *
 * Everything here is built from `@/components/ui`, and every control meets the
 * accessibility floor: the shared `Button` and `inputClass` carry `min-h-11`, a
 * visible text label sits beside each icon, and no state is carried by colour
 * alone - every tone is printed with its word.
 *
 * These are local because `ui.jsx` has no equivalent, and building them here is
 * the contract's instruction rather than an oversight. Promotion work for the
 * integrator, not a reason to edit a shared file:
 *
 *   - `InlineNote`, because the page states what it did or could not do.
 *   - `StatusBadge`, because a policy's state is a finding and the word beside
 *     the colour is what makes it one.
 *   - `ProtectionBadges`, because the matrix's active switches are a summary.
 *   - `DecisionCard`, because a refusal has to name the protection that refused.
 *   - `ActionRow`, because the action log is a list of facts with integer codes.
 */

import { Badge, Icon } from '@/components/ui'

import {
  ACTION_LABELS,
  ACTION_TONES,
  PROTECTION_LABELS,
  STATE_LABELS,
  shortTimestamp,
  stateTone,
} from './api'

/** An inline note about something this page did, or could not do. */
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

/** A policy's state as a word plus a tone. The word is always present. */
export function StatusBadge({ state }) {
  return <Badge tone={stateTone(state)}>{STATE_LABELS[state] || state || 'Unknown'}</Badge>
}

/** The matrix's active switches, as machine names with words beside them. */
export function ProtectionBadges({ fields = {} }) {
  const on = []
  if (fields.email_protected) on.push('email_protected')
  if (fields.email_authenticated) on.push('email_authenticated')
  if (fields.require_passcode) on.push('require_passcode')
  if (fields.enable_agreement) on.push('enable_agreement')
  if (fields.enable_watermark) on.push('enable_watermark')
  if (fields.allow_download) on.push('allow_download')
  if ((fields.ip_allowlist || []).length) on.push('ip_allowlist')
  if (on.length === 0) {
    return <span className="text-xs text-muted-foreground">No protection beyond the link&apos;s own gate.</span>
  }
  return (
    <ul className="flex flex-wrap gap-1.5" aria-label="Active protections">
      {on.map((name) => (
        <li key={name}>
          <Badge tone="insert">{name}</Badge>
        </li>
      ))}
    </ul>
  )
}

/** One evaluated protection: allowed or refused, and why in words. */
export function DecisionCard({ decision }) {
  if (!decision) return null
  const protections = decision.protections || {}
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={decision.allowed ? 'insert' : 'delete'}>
          {decision.allowed ? 'Allowed' : 'Refused'}
        </Badge>
        <span className="text-sm text-foreground">{decision.message}</span>
      </div>
      <ul className="flex flex-col">
        {Object.entries(protections).map(([name, entry]) => (
          <li
            key={name}
            className="flex flex-wrap items-baseline justify-between gap-2 border-b border-border-subtle py-1.5 last:border-b-0"
          >
            <span className="text-xs font-medium text-foreground">
              {PROTECTION_LABELS[name] || name}
            </span>
            <span className="text-xs text-muted-foreground">
              <span className={entry.allowed ? 'text-success' : 'text-destructive'}>
                {entry.allowed ? 'permitted' : 'refused'}
              </span>
              {entry.required ? ' (required)' : ' (not required)'} - {entry.detail}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}

/** One logged action. The code is the integer WF-079's export queries by. */
export function ActionRow({ action }) {
  const name = action.action
  return (
    <li className="flex flex-wrap items-center justify-between gap-2 border-b border-border-subtle py-2 last:border-b-0">
      <span className="flex min-w-0 items-center gap-2">
        <Badge tone={ACTION_TONES[name] || 'neutral'}>{name}</Badge>
        <span className="truncate text-sm text-foreground">
          {ACTION_LABELS[name] || name}
        </span>
        <span className="truncate font-mono text-xs text-muted-foreground">
          {action.recipient}
        </span>
      </span>
      <span className="font-mono text-xs text-muted-foreground">
        code {action.event_code}
        {action.allowed === false ? ' | refused' : ''} | {shortTimestamp(action.occurred_at)}
      </span>
    </li>
  )
}

/** The allow-list as a reader sees it: one address or block per line. */
export function AllowListPreview({ allowlist = [] }) {
  if (!allowlist.length) {
    return <span className="text-xs text-muted-foreground">Open: any address may open this link.</span>
  }
  return (
    <ul className="flex flex-wrap gap-1.5" aria-label="Allowed networks">
      {allowlist.map((entry) => (
        <li key={entry}>
          <Badge tone="update">{entry}</Badge>
        </li>
      ))}
    </ul>
  )
}
