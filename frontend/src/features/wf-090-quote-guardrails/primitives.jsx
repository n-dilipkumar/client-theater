/**
 * Presentation primitives for the quote guardrails page (WF-090).
 *
 * Everything here is built from the shared components in `@/components/ui`, which is the
 * rule: `ui.jsx` is shared and not ours to edit. What lives here is the small number of
 * shapes this page needs that no shared component provides, so the integrator can promote
 * one if it recurs rather than each feature rolling its own.
 *
 * Two are worth naming.
 *
 * `RuleCard` prints the rule as the specification stores it, definition included. A rule
 * page that showed only a human name would hide the one thing the specification says is
 * the rule: the text expression. The definition is rendered in mono because it is a
 * machine value, not prose.
 *
 * `VerdictList` never tells a seller "no" in colour alone. Each verdict carries a word,
 * a reason code, and the server's own sentence, so "cannot be checked" is legibly
 * different from "blocked" to someone who cannot see the tone.
 */

import { Badge, Icon, inputClass } from '@/components/ui'

/** A warning-scale glyph for the nav. Not in the shared PATHS map. */
export const GUARDRAIL_ICON =
  'M12 3v2m0 14v2M5 12H3m18 0h-2M7.5 7.5L6 6m12 12l-1.5-1.5M16.5 7.5L18 6M6 18l1.5-1.5M12 8a4 4 0 100 8 4 4 0 000-8z'

const NOTICE_TONES = {
  info: { border: 'border-info/40', bg: 'bg-info/10', text: 'text-info', label: 'Note', icon: 'schema' },
  success: {
    border: 'border-success/40',
    bg: 'bg-success/10',
    text: 'text-success',
    label: 'Clear',
    icon: 'restore',
  },
  warning: {
    border: 'border-warning/40',
    bg: 'bg-warning/10',
    text: 'text-warning',
    label: 'Warning',
    icon: 'audit',
  },
  error: {
    border: 'border-destructive/40',
    bg: 'bg-destructive/10',
    text: 'text-destructive',
    label: 'Blocked',
    icon: 'close',
  },
}

/**
 * A tone-keyed callout.
 *
 * `docs/DESIGN-SYSTEM.md` names `Notice` among the shared primitives, but
 * `components/ui.jsx` does not export one, so it is built here. Every tone carries its
 * own label and icon, never colour alone.
 */
export function Notice({ tone = 'info', title, children }) {
  const described = NOTICE_TONES[tone] || NOTICE_TONES.info
  return (
    <div
      role="status"
      aria-live="polite"
      className={`flex items-start gap-3 rounded-sm border p-4 ${described.border} ${described.bg}`}
    >
      <Icon name={described.icon} size={16} className={`mt-0.5 shrink-0 ${described.text}`} />
      <div className="min-w-0">
        <p className={`text-xs font-semibold uppercase tracking-[0.14em] ${described.text}`}>
          {title || described.label}
        </p>
        <div className="mt-1 text-sm text-foreground">{children}</div>
      </div>
    </div>
  )
}

/**
 * A labelled switch at the 44px minimum touch target.
 *
 * `Toggle` is named in the design system but not exported by the shared file, which is
 * not ours to edit, so it is built here for the same reason as `Notice`.
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

/** A section heading with the micro label the design system asks for. */
export function SectionLabel({ children }) {
  return (
    <p className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">{children}</p>
  )
}

/**
 * The word and tone for one verdict.
 *
 * The word is the state; the colour is reinforcement. `unverifiable` is deliberately its
 * own word, because the third state is the one a seller is most likely to misread: a rule
 * that cannot be checked is not a rule that passed, and it is not a rule that failed.
 */
export const VERDICT_STYLE = {
  violation: { tone: 'delete', word: 'Blocked' },
  clear: { tone: 'insert', word: 'Clear' },
  unverifiable: { tone: 'restore', word: 'Cannot check' },
  skipped: { tone: 'neutral', word: 'Disabled' },
}

export function VerdictPill({ verdict }) {
  const described = VERDICT_STYLE[verdict] || { tone: 'neutral', word: verdict || 'unknown' }
  return <Badge tone={described.tone}>{described.word}</Badge>
}

/** The word for a rule's configured outcome, spelled as the vendor spells it. */
export const OUTCOME_WORD = {
  show_warning: 'Show warning',
  block_publish: 'Block publish',
}

/** The word for a rule's status switch. */
export const STATUS_WORD = {
  enabled: 'Enabled',
  disabled: 'Disabled',
}

/**
 * One rule, printed as the specification stores it.
 *
 * The id is mono because it is a machine value. The definition is mono for the same
 * reason: it is the rule, and a reader who wants to know what a rule does should not have
 * to translate a friendly name back into an expression.
 */
export function RuleCard({ rule, busy, onToggle, onDelete }) {
  const data = rule.data || rule
  const enabled = data.enabled ?? data.status === 'enabled'
  return (
    <div className="rounded-sm border border-border-subtle bg-surface p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-base font-semibold text-foreground">{data.name}</h3>
          <p className="font-mono text-xs text-muted-foreground">{rule.id || data.id}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={data.outcome === 'block_publish' ? 'delete' : 'restore'}>
            {OUTCOME_WORD[data.outcome] || data.outcome}
          </Badge>
          <Badge tone={enabled ? 'insert' : 'neutral'}>{STATUS_WORD[data.status] || data.status}</Badge>
        </div>
      </div>
      <pre className="mt-3 overflow-x-auto rounded-xs border border-border-subtle bg-muted/40 p-3 font-mono text-xs text-foreground">
        {data.rule_definition}
      </pre>
      <p className="mt-2 text-sm text-muted-foreground">{data.message}</p>
      {(onToggle || onDelete) && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {onToggle && (
            <button
              type="button"
              disabled={busy}
              onClick={() => onToggle(rule)}
              className="inline-flex min-h-11 items-center gap-2 rounded-sm border border-border-subtle px-4 text-sm text-foreground transition-colors hover:border-accent hover:text-accent disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Icon name="refresh" size={16} />
              {enabled ? 'Disable' : 'Enable'}
            </button>
          )}
          {onDelete && (
            <button
              type="button"
              disabled={busy}
              onClick={() => onDelete(rule)}
              className="inline-flex min-h-11 items-center gap-2 rounded-sm border border-destructive/30 px-4 text-sm text-destructive transition-colors hover:bg-destructive/10 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Icon name="trash" size={16} />
              Delete
            </button>
          )}
        </div>
      )}
    </div>
  )
}

/**
 * The verdicts for one quote, grouped by what each one means.
 *
 * Order is blocked, warned, unchecked, disabled: the order a seller needs them in. Each
 * row shows the server's own reason sentence, so the page never paraphrases a refusal.
 */
export function VerdictList({ evaluation }) {
  if (!evaluation) return null
  const groups = [
    { key: 'blocking', label: 'Blocking rules', tone: 'error' },
    { key: 'warnings', label: 'Warnings', tone: 'warning' },
    { key: 'unverifiable', label: 'Could not be checked', tone: 'info' },
    { key: 'skipped', label: 'Disabled rules', tone: 'info' },
  ]
  const total = groups.reduce((count, group) => count + (evaluation[group.key] || []).length, 0)
  if (!total) {
    return (
      <Notice tone="success" title="No rule fired">
        Every enabled rule evaluated false or could not be checked. The quote is publishable.
      </Notice>
    )
  }
  return (
    <div className="space-y-4">
      {groups.map((group) => {
        const items = evaluation[group.key] || []
        if (!items.length) return null
        return (
          <div key={group.key} className="space-y-2">
            <div className="flex items-center gap-2">
              <SectionLabel>{group.label}</SectionLabel>
              <span className="font-mono text-[11px] text-muted-foreground">{items.length}</span>
            </div>
            <ul className="space-y-2">
              {items.map((row, index) => (
                <li
                  key={`${row.rule_id}-${index}`}
                  className="rounded-sm border border-border-subtle bg-surface p-3"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="text-sm font-semibold text-foreground">{row.rule_name}</span>
                    <span className="flex items-center gap-2">
                      <VerdictPill verdict={row.verdict} />
                      <span className="font-mono text-[11px] text-muted-foreground">
                        {row.reason_code}
                      </span>
                    </span>
                  </div>
                  <p className="mt-1 text-sm text-muted-foreground">{row.reason || row.message}</p>
                </li>
              ))}
            </ul>
          </div>
        )
      })}
    </div>
  )
}
