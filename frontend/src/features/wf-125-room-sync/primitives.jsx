/**
 * WF-125's own small primitives.
 *
 * `docs/DESIGN-SYSTEM.md` and `docs/FEATURE-CONTRACT.md` both list `Notice`
 * among the primitives available from `@/components/ui`, and the shipped
 * `ui.jsx` has none. The contract's instruction for exactly this case is to
 * build what is needed inside the feature folder and say so, so the integrator
 * can promote the ones that recur.
 *
 * Every state is carried by a word first and a colour second, so nothing here
 * depends on colour alone, and the floor is met: `rounded-sm`, semantic tokens
 * only, a text label beside every control, and no motion a reader with
 * `prefers-reduced-motion` would have to sit through.
 */

import { Icon } from '@/components/ui'

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
  warning:
    'M12 9v4m0 4h.01M10.3 4.3L2.6 18a2 2 0 001.7 3h15.4a2 2 0 001.7-3L13.7 4.3a2 2 0 00-3.4 0z',
  destructive: 'M12 9v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z',
}

/**
 * A short standing message. Tone carries the meaning and the icon repeats it,
 * so the state does not depend on colour alone.
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

function Chip({ tone, word, mono = true }) {
  return (
    <span
      className={`inline-flex items-center rounded-xs border px-2 py-0.5 text-xs font-medium
        ${mono ? 'font-mono' : ''} ${TONES[tone] || TONES.neutral}`}
    >
      {word}
    </span>
  )
}

/** A write-back's state, as a badge whose word is the first thing read. */
export function StateBadge({ state }) {
  const words = { pending: 'Pending', synced: 'Synced', failed: 'Failed' }
  const tones = { pending: 'warning', synced: 'success', failed: 'destructive' }
  return <Chip tone={tones[state] || 'neutral'} word={words[state] || state || 'Unknown'} />
}

/** Whether a target object is standard or custom, as a badge with its word. */
export function ObjectKindBadge({ kind }) {
  const words = { standard: 'Standard', custom: 'Custom' }
  const tones = { standard: 'neutral', custom: 'info' }
  return <Chip tone={tones[kind] || 'neutral'} word={words[kind] || kind || 'Unknown'} />
}

/** How a room was made, as a badge with its word. */
export function CreatedBadge({ from }) {
  const words = { rule: 'Rule', seller: 'Seller pick' }
  return <Chip tone={from === 'rule' ? 'success' : 'info'} word={words[from] || from || 'Unknown'} />
}

/** A rule's condition set, one line per condition. */
export function ConditionList({ conditions = [] }) {
  if (!conditions.length) {
    return <span className="text-xs text-muted-foreground">No conditions</span>
  }
  return (
    <ul className="flex flex-wrap gap-1">
      {conditions.map((condition, index) => (
        <li key={`${condition.field}-${index}`}>
          <Chip
            tone="neutral"
            word={`${condition.field || 'field'} ${condition.op || 'eq'}${
              condition.op === 'exists' ? '' : ` ${formatValue(condition.value)}`
            }`}
          />
        </li>
      ))}
    </ul>
  )
}

function formatValue(value) {
  if (Array.isArray(value)) return value.join(',')
  return String(value)
}
