/**
 * The three things this feature needs that `@/components/ui` does not carry.
 *
 * All three are built here rather than added to `ui.jsx`, because that file is shared and
 * this feature may not edit it. The contract says to build what is missing inside the
 * feature folder and say so in the pull request, which this comment and the pull
 * request both do.
 *
 * 1. `GateVerdict` renders the gate's decision. `Badge` takes a tone and a child, which
 *    is enough to show a word, but the decision also carries what the room will actually
 *    do - a persistent identifier, cookies, tracking - and those booleans are the whole
 *    point of the control. A badge alone would say "denied" without saying what denied
 *    means.
 * 2. `WindowTable` renders the retention classes as configured window beside researched
 *    ceiling. `StatCard` shows one number and the design system has no table primitive,
 *    and the difference between the two numbers is the fact a legal reviewer reads.
 * 3. `ResidueList` renders what an erasure could not remove. `EmptyState` says nothing
 *    is there, and the point here is the opposite: something is always left, and it has
 *    to be named.
 *
 * Nothing here uses an emoji as an icon, hardcodes a colour, or conveys a state by
 * colour alone. Every state carries its word as text, every number is rendered, and every
 * row that is interactive is at least 44px tall.
 */

import { Badge } from '@/components/ui'
import { consentState, gateOutcome } from './api'

/**
 * The gate's decision, with what it means in practice.
 *
 * No status is carried by colour alone: the outcome word is always present as text, and
 * each effect is rendered as its own labelled word, so the distinction between "no
 * cookies" and "no persistent identifier" survives a monochrome display and a
 * colour-blind reader alike.
 */
export function GateVerdict({ decision, showEffect = true }) {
  if (!decision) return null
  const outcome = gateOutcome(decision.outcome)
  const effect = decision.effect || {}

  return (
    <div className="rounded-sm border border-border-subtle bg-surface p-4">
      <div className="flex flex-wrap items-center gap-3">
        <Badge tone={outcome.tone}>{outcome.label}</Badge>
        <span className="font-mono text-xs text-muted-foreground">
          {decision.region} / {decision.jurisdiction}
        </span>
      </div>
      <p className="mt-2 text-sm text-foreground">{outcome.meaning}</p>
      {decision.reason ? (
        <p className="mt-1 text-xs text-muted-foreground">{decision.reason}</p>
      ) : null}
      {showEffect ? (
        <dl className="mt-3 grid gap-2 sm:grid-cols-2">
          <EffectRow label="Persistent identifier" value={effect.persistent_identifier} />
          <EffectRow label="Cookies" value={effect.cookies} />
          <EffectRow label="Tracking" value={effect.tracking} />
          <EffectRow label="Session recording" value={effect.recording} />
        </dl>
      ) : null}
      {decision.signals_ignored?.length ? (
        <div className="mt-3">
          <p className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
            Signals this gate discarded
          </p>
          <ul className="mt-1 space-y-1">
            {decision.signals_ignored.map((row) => (
              <li key={row.signal} className="text-xs text-muted-foreground">
                <span className="font-mono text-foreground">{row.signal}</span>: {row.reason}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  )
}

/** One effect, as a word rather than a tick. */
function EffectRow({ label, value }) {
  const word = value === true ? 'on' : value === false ? 'off' : 'unknown'
  return (
    <div className="flex items-center justify-between gap-3 border-t border-border-subtle pt-2">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="font-mono text-xs text-foreground">{word}</dd>
    </div>
  )
}

/**
 * The retention classes: the configured window beside the researched ceiling.
 *
 * The two are separate columns because they are separate facts. A board that showed only
 * one of them would let an operator believe a longer window was agreed when the room
 * enforces the shorter one, which is the exact confusion the ceiling exists to prevent.
 */
export function WindowTable({ rows = [], windowUnit = 'days' }) {
  if (!rows.length) {
    return (
      <p className="text-sm text-muted-foreground">
        No retention class has been configured for this room yet.
      </p>
    )
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[34rem] border-collapse text-left">
        <thead>
          <tr className="border-b border-border-subtle">
            <th className="py-2 pr-3 text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
              Class
            </th>
            <th className="py-2 pr-3 text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
              Configured
            </th>
            <th className="py-2 pr-3 text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
              Ceiling
            </th>
            <th className="py-2 text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
              Records
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const shortened = Number(row.configured_days) < Number(row.ceiling_days)
            return (
              <tr key={row.class} className="border-b border-border-subtle align-top">
                <td className="py-3 pr-3">
                  <span className="block text-sm text-foreground">{row.label}</span>
                  <span className="block font-mono text-xs text-muted-foreground">
                    {row.class}
                  </span>
                  <span className="mt-1 block text-xs text-muted-foreground">{row.evidence}</span>
                </td>
                <td className="py-3 pr-3 font-mono text-sm text-foreground">
                  {row.configured_days} {windowUnit}
                  {shortened ? (
                    <Badge tone="neutral">shorter than the ceiling</Badge>
                  ) : null}
                </td>
                <td className="py-3 pr-3 font-mono text-sm text-foreground">
                  {row.ceiling_days} {windowUnit}
                </td>
                <td className="py-3 font-mono text-xs text-muted-foreground">
                  {(row.schedule?.collections || []).join(', ') || 'none aged'}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

/**
 * What an erasure could not remove, with the reason beside each count.
 *
 * The list is never empty in practice: the audit trail always keeps the rows the erasure
 * removed. Rendering the reasons is the point, because an erasure that reported a clean
 * sweep while the log still held the address would be the failure this workflow exists
 * to prevent.
 */
export function ResidueList({ residue }) {
  if (!residue) {
    return (
      <p className="text-sm text-muted-foreground">
        Nothing has been erased yet, so there is no residue to report.
      </p>
    )
  }

  const rows = [
    { key: 'audit_trail', label: 'Audit rows', value: residue.audit_rows },
    {
      key: 'erasure_record',
      label: 'The request row itself',
      value: residue.erasure_records_kept ?? 0,
    },
  ]

  return (
    <div className="rounded-sm border border-border-subtle bg-surface p-4">
      <p className="text-sm text-foreground">{residue.rule}</p>
      <ul className="mt-3 space-y-2">
        {rows.map((row) => (
          <li key={row.key} className="border-t border-border-subtle pt-2">
            <div className="flex items-baseline justify-between gap-3">
              <span className="text-xs text-muted-foreground">{row.label}</span>
              <span className="font-mono text-sm text-foreground">{row.value}</span>
            </div>
            <p className="mt-1 text-xs text-muted-foreground">{residue.notes?.[row.key]}</p>
          </li>
        ))}
      </ul>
      <p className="mt-3 text-xs text-muted-foreground">
        {residue.notes?.audit_mirror}
      </p>
      {residue.remaining === null ? (
        <p className="mt-2 font-mono text-xs text-foreground">
          A scan bound was reached, so this build cannot say whether anything survived.
        </p>
      ) : null}
    </div>
  )
}

/**
 * One consent record, as its state word plus the decision that produced it.
 *
 * `revoked` and `denied` are rendered as different words on purpose. They are different
 * facts about a buyer: one had tracking and lost it, the other never had it.
 */
export function ConsentRow({ record, onSelect, selectedId }) {
  const state = consentState(record.state)
  return (
    <button
      type="button"
      onClick={() => onSelect?.(record)}
      aria-pressed={selectedId === record.id}
      className={`flex min-h-11 w-full flex-wrap items-center justify-between gap-3 px-1 py-2 text-left hover:bg-muted ${
        selectedId === record.id ? 'bg-accent-soft' : ''
      }`}
    >
      <span className="min-w-0">
        <span className="block truncate font-mono text-sm text-foreground">{record.subject}</span>
        <span className="block font-mono text-xs text-muted-foreground">
          {record.region} / {record.signal || 'no signal sent'}
        </span>
      </span>
      <span className="flex items-center gap-3">
        <span className="font-mono text-xs text-muted-foreground">
          {record.cookies_cleared ? 'cookies cleared' : 'cookies kept'}
        </span>
        <Badge tone={state.tone}>{state.label}</Badge>
      </span>
    </button>
  )
}
