/**
 * The few shapes this workflow needs that the shared primitives do not carry.
 *
 * Everything that exists in `@/components/ui` is imported from there and never copied.
 * `Badge`, `Button`, `Card`, `EmptyState`, `ErrorNote`, `Field`, `Icon`, `Spinner`,
 * `StatCard`, `inputClass` and `useAsync` are all shared and all imported from there. This
 * folder holds only what that file genuinely does not export, and it says so rather than
 * quietly reimplementing a primitive somebody else owns.
 *
 * Four rules every component here obeys, because they are the accessibility floor rather
 * than styling:
 *
 * - **No status is conveyed by colour alone.** Every badge renders its status as text. A
 *   badge whose only difference is its colour tells a screen-reader user nothing.
 * - **Every icon sits beside a text label** or carries an `aria-label`. An icon alone is
 *   never the only label.
 * - **Every control is at least 44px tall** (`min-h-11`), because it is a target somebody
 *   has to hit with a finger.
 * - **No emoji as an icon.** Every glyph here is an SVG path.
 */

import { Badge, Icon, inputClass } from '@/components/ui'

/**
 * The nav glyph: a document with a check mark and a second signature line across it,
 * which is a quote waiting for a second pair of eyes.
 *
 * Passed as `iconPath` rather than as an `icon` name, because the shared `PATHS` map is
 * not ours to edit and this glyph is not in it.
 */
export const QUOTE_APPROVAL_ICON =
  'M6 3h8l4 4v14H6V3zm8 0v4h4M9 15l2 2 4-4M9 8h3M9 11h3'

/**
 * A short block of text that carries a tone: an exemption reason, a refusal, a quote
 * from the research, the self-approval reading, a recorded API gap. Not an error:
 * `ErrorNote` is the error, and it is used instead of this wherever the request
 * actually failed.
 */
export function Notice({ tone = 'neutral', title, children }) {
  const tones = {
    neutral: 'border-border-subtle bg-muted text-foreground',
    info: 'border-border-subtle bg-accent-soft text-foreground',
    warning: 'border-warning/40 bg-warning/10 text-foreground',
    danger: 'border-destructive/40 bg-destructive/10 text-foreground',
    success: 'border-success/40 bg-success/10 text-foreground',
  }
  return (
    <div className={`rounded-sm border p-4 ${tones[tone] || tones.neutral}`}>
      {title && <p className="text-sm font-semibold">{title}</p>}
      {children && <div className="mt-1 text-sm text-muted-foreground">{children}</div>}
    </div>
  )
}

/**
 * One approval state, as text.
 *
 * The tone is a second signal, never the only one. The label is always rendered, so
 * "Pending approval" is readable with no colour perception at all.
 */
export function StateBadge({ state, vocabulary }) {
  const label =
    (vocabulary?.quote_states || []).find((row) => row.state === state)?.label || state || 'Unknown'
  const tone = (
    {
      PENDING_APPROVAL: 'warning',
      APPROVED: 'success',
      REJECTED: 'destructive',
      SHARED: 'info',
    }
  )[state] || 'neutral'
  return (
    <Badge tone={tone}>
      <span className="font-mono">{state || 'unknown'}</span>
      <span className="ml-1 font-sans">{label}</span>
    </Badge>
  )
}

/**
 * One condition from the "View approval conditions" panel.
 *
 * Shows the property, the operator in words, what the filter compared against and what
 * it actually read. A seller who is told "approval is required" and nothing else has no
 * way to check it, and a condition that reads "did not match" without saying what it
 * looked at is the same as no condition at all.
 */
export function ConditionRow({ condition }) {
  const matched = condition?.matched === true
  return (
    <div className="flex flex-col gap-1 border-b border-border-subtle py-2 last:border-b-0">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={matched ? 'success' : 'neutral'}>
          <span className="font-sans">{matched ? 'Matched' : 'Did not match'}</span>
        </Badge>
        <span className="font-mono text-xs text-foreground">
          {condition?.object}.{condition?.property}
        </span>
      </div>
      <p className="text-xs text-muted-foreground">
        Operator <span className="font-mono">{condition?.operator}</span>. Compared with{' '}
        <span className="font-mono">{JSON.stringify(condition?.expected)}</span>. Read{' '}
        <span className="font-mono">
          {condition?.present ? JSON.stringify(condition?.actual) : 'nothing, the property is absent'}
        </span>
        {condition?.reason ? `. ${condition.reason}` : ''}
      </p>
    </div>
  )
}

/**
 * The self-approval rule, shown as prose.
 *
 * This build's reading of two sourced sentences that do not agree was chosen by Jev, and
 * the panel shows the sentences and the audit id rather than asking a seller to trust a
 * docstring. A control that silently self-approves is the thing a reviewer must be able
 * to find here.
 */
export function SelfApprovalPanel({ reading }) {
  if (!reading) return null
  return (
    <div className="flex flex-col gap-2 rounded-sm border border-border-subtle bg-muted p-3">
      <p className="text-sm font-semibold text-foreground">How this workspace handles self approval</p>
      <p className="text-sm text-foreground">{reading.rule}</p>
      <p className="text-xs text-muted-foreground">{reading.why}</p>
      <ul className="flex flex-col gap-1">
        {(reading.sourced || []).map((sentence) => (
          <li key={sentence} className="text-xs italic text-muted-foreground">
            {sentence}
          </li>
        ))}
      </ul>
      <p className="text-xs text-muted-foreground">
        Decided by Jev, audit <span className="font-mono">{reading.jev_audit_id}</span>, confidence{' '}
        {reading.jev_confidence}. The rejected alternative: {reading.rejected}
      </p>
    </div>
  )
}

/**
 * One approver's place in the requirement.
 *
 * Renders the name, what they did and what is still outstanding, in words. The tally is
 * the "All approvers required" or "At least one approver required" rule made visible, so
 * a seller can see who is holding the quote up rather than only that somebody is.
 */
export function TallyRow({ tally, requirementLabel }) {
  if (!tally) return null
  return (
    <div className="flex flex-col gap-2">
      <p className="text-xs text-muted-foreground">
        Requirement: <span className="text-foreground">{requirementLabel}</span>
      </p>
      <div className="flex flex-wrap gap-2">
        <Badge tone="success">
          <span className="font-sans">Approved</span>
          <span className="ml-1 font-mono">{tally.approved?.length ?? 0}</span>
        </Badge>
        <Badge tone="destructive">
          <span className="font-sans">Changes requested</span>
          <span className="ml-1 font-mono">{tally.rejected?.length ?? 0}</span>
        </Badge>
        <Badge tone="warning">
          <span className="font-sans">Outstanding</span>
          <span className="ml-1 font-mono">{tally.outstanding?.length ?? 0}</span>
        </Badge>
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <span>
          Approved by <span className="font-mono">{tally.approved?.join(', ') || 'nobody'}</span>
        </span>
        <span>
          Outstanding <span className="font-mono">{tally.outstanding?.join(', ') || 'nobody'}</span>
        </span>
      </div>
    </div>
  )
}

/**
 * A filter row in the rule builder.
 *
 * The property is a free-text dotted path rather than a picker of names this repository
 * does not declare. That is deliberate: "**[Object] properties** -> search and pick a
 * property" is populated from the properties an account actually has, and a team adding
 * one needs no change here.
 */
export function FilterRow({ index, filter, objects, operators, onChange, onRemove, disabled }) {
  const id = `wf091-filter-${index}`
  const numeric = (operators || []).some(
    (row) => row.operator === filter.operator && ['gt', 'gte', 'lt', 'lte'].includes(row.operator),
  )
  return (
    <div className="flex flex-col gap-2 rounded-sm border border-border-subtle bg-muted p-3">
      <div className="grid gap-2 sm:grid-cols-3">
        <div className="flex flex-col gap-1">
          <label htmlFor={`${id}-object`} className="text-[13px] font-medium text-foreground">
            Filtering on
          </label>
          <select
            id={`${id}-object`}
            className={inputClass}
            value={filter.object}
            disabled={disabled}
            onChange={(event) => onChange(index, { object: event.target.value })}
          >
            {(objects || []).map((row) => (
              <option key={row.object} value={row.object}>
                {row.object}
              </option>
            ))}
          </select>
        </div>

        <div className="flex flex-col gap-1">
          <label htmlFor={`${id}-property`} className="text-[13px] font-medium text-foreground">
            Property
          </label>
          <input
            id={`${id}-property`}
            className={inputClass}
            value={filter.property}
            disabled={disabled}
            placeholder="amount, discount, sku"
            onChange={(event) => onChange(index, { property: event.target.value })}
          />
        </div>

        <div className="flex flex-col gap-1">
          <label htmlFor={`${id}-operator`} className="text-[13px] font-medium text-foreground">
            Operator
          </label>
          <select
            id={`${id}-operator`}
            className={inputClass}
            value={filter.operator}
            disabled={disabled}
            onChange={(event) => onChange(index, { operator: event.target.value })}
          >
            {(operators || []).map((row) => (
              <option key={row.operator} value={row.operator}>
                {row.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="flex flex-col gap-1">
        <label htmlFor={`${id}-value`} className="text-[13px] font-medium text-foreground">
          Value
        </label>
        <input
          id={`${id}-value`}
          className={inputClass}
          value={Array.isArray(filter.value) ? filter.value.join(', ') : (filter.value ?? '')}
          disabled={disabled}
          inputMode={numeric ? 'numeric' : 'text'}
          placeholder={filter.operator === 'in' || filter.operator === 'not_in' ? 'a, b, c' : '25'}
          onChange={(event) => onChange(index, { value: event.target.value })}
        />
        <p className="text-xs text-muted-foreground">
          A property is a dotted path into the quote's own JSON, so a team adds one without
          changing this page.
        </p>
      </div>

      <div>
        <button
          type="button"
          className="inline-flex min-h-11 items-center gap-2 rounded-sm border border-border-subtle px-3 text-sm text-foreground disabled:opacity-50"
          onClick={() => onRemove(index)}
          disabled={disabled}
        >
          <Icon name="audit" size={16} />
          Remove this filter
        </button>
      </div>
    </div>
  )
}

/**
 * One recorded notification.
 *
 * Says "recorded, not delivered" in words, every time. This product has no mail
 * transport and no chat integration, and a row that read like a delivery receipt would
 * teach a seller that the approver has been told when nobody has been told.
 */
export function NotificationRow({ notification }) {
  return (
    <div className="flex flex-col gap-1 border-b border-border-subtle py-2 last:border-b-0">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs text-foreground">{notification?.recipient}</span>
        <Badge tone="neutral">
          <span className="font-sans">{notification?.channel_label || notification?.channel}</span>
        </Badge>
        <span className="text-xs text-muted-foreground">{notification?.recipient_role}</span>
      </div>
      <p className="text-xs text-foreground">{notification?.subject}</p>
      <p className="text-xs text-muted-foreground">
        Action: {notification?.action}. Recorded, not delivered. There is no mail transport in this
        product.
      </p>
    </div>
  )
}
