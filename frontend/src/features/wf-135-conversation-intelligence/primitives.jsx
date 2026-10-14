/**
 * WF-135's own small primitives.
 *
 * `docs/DESIGN-SYSTEM.md` and `docs/FEATURE-CONTRACT.md` both list `Notice` and
 * `Modal` among the primitives available from `@/components/ui`, and the shipped
 * `ui.jsx` has neither. That is a contradiction in the repository's own
 * documentation rather than a gap in this feature, and the contract's instruction
 * for exactly this case is to build what is needed inside the feature folder and
 * say so, so the integrator can promote the ones that recur.
 *
 * `Notice` is rebuilt here. `Modal` is not: nothing in WF-135 needs a dialog. A
 * conversation is recorded in a panel that is already on screen, because a rep
 * recording a call needs to see the room it belongs to.
 *
 * Every state is carried by a word first and a colour second, so nothing here
 * depends on colour alone, and the floor is met: `rounded-sm`, semantic tokens
 * only, a real `<label>` or a text label beside every control, and no motion.
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
 * A short standing message. Tone carries the meaning and the icon repeats it, so
 * the state does not depend on colour alone.
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

function Chip({ tone, word }) {
  return (
    <span
      className={`inline-flex items-center rounded-xs border px-2 py-0.5 font-mono text-xs
        font-medium ${TONES[tone] || TONES.neutral}`}
    >
      {word}
    </span>
  )
}

/** How one fan-out row stands, as a badge that carries its own word. */
export function StateBadge({ state }) {
  const words = { written: 'Written', failed: 'Failed' }
  const tones = { written: 'success', failed: 'destructive' }
  return <Chip tone={tones[state] || 'neutral'} word={words[state] || state || 'Unknown'} />
}

/** Whether one entry reached every destination. */
export function AgreementBadge({ agreement }) {
  if (!agreement) return <Chip tone="neutral" word="Unknown" />
  if (agreement.agreed) return <Chip tone="success" word="Agreed" />
  const failed = (agreement.failed || []).length
  const missing = (agreement.missing || []).length
  return <Chip tone="warning" word={`Partial (${failed} failed, ${missing} missing)`} />
}

/** One MEDDICC letter, covered or not. */
export function LetterBadge({ row }) {
  return (
    <Chip
      tone={row.covered ? 'success' : 'neutral'}
      word={`${row.letter} ${row.label} - ${row.covered ? 'evidence' : 'no evidence'}`}
    />
  )
}

/** A CRM outcome, in words. */
export function OutcomeBadge({ outcome }) {
  const words = {
    created: 'Created',
    updated: 'Updated',
    submitted: 'Submitted',
    failed: 'Failed',
    rejected: 'Rejected',
    rolled_back: 'Rolled back',
    not_sent: 'Not sent',
  }
  const tones = {
    created: 'success',
    updated: 'success',
    submitted: 'info',
    failed: 'destructive',
    rejected: 'destructive',
    rolled_back: 'warning',
    not_sent: 'neutral',
  }
  return <Chip tone={tones[outcome] || 'neutral'} word={words[outcome] || outcome || 'Not sent'} />
}
