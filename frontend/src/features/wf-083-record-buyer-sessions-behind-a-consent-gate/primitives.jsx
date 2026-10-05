/**
 * The three things this feature needs that `@/components/ui` does not carry.
 *
 * All three are built here rather than added to `ui.jsx`, because that file is shared and
 * this feature may not edit it. The contract says to build what is missing inside the
 * feature folder and say so in the pull request, which this comment and the pull request
 * both do.
 *
 * 1. `AxisMatrix` renders the consent decision as two independent axes. `Badge` takes a
 *    tone and a child, which is enough to show one word, but the whole point of this
 *    workflow is that the two axes are independent. A single "granted" badge cannot show
 *    that analytics was allowed while ad storage was refused, which is the case the issue
 *    asks a reviewer to be able to see.
 * 2. `RetentionTable` renders each recording beside the window that applies to it. The
 *    design system has no table primitive, and the difference between the 30-day window
 *    and the 270-day one is the fact a legal reviewer reads.
 * 3. `RefusalNote` renders what a sourced limit refuses. `Notice` says something is wrong,
 *    and the point here is the opposite: the refusal is the feature, so it has to be named
 *    with the evidence that makes it a refusal rather than a gap.
 *
 * Nothing here uses an emoji as an icon, hardcodes a colour, or conveys a state by colour
 * alone. Every state carries its word as text, every number is rendered, and every row
 * that is interactive is at least 44px tall.
 */

import { Badge } from '@/components/ui'
import { consentOutcome, formatWindow, ingestState, plural } from './api'

/**
 * The two consent axes, each as its own labelled word.
 *
 * No status is carried by colour alone: each axis shows `granted` or `denied` as text, and
 * the summary sentence underneath spells out the consequence. That matters most for the
 * partial grant, where one axis reads granted and the other denied: a reader who saw only
 * a colour would not know which storage was allowed.
 */
export function AxisMatrix({ axes = {}, granted = [], compact = false }) {
  const list = Object.entries(axes)
  if (!list.length) return null

  return (
    <div className="rounded-sm border border-border-subtle bg-surface p-4">
      <dl className="grid gap-3 sm:grid-cols-2">
        {list.map(([axis, value]) => {
          const isGranted = value === 'granted'
          return (
            <div key={axis} className="min-w-0">
              <dt className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
                {axis.replace(/_/g, ' ')}
              </dt>
              <dd className="mt-1 flex items-center gap-2">
                <Badge tone={isGranted ? 'insert' : 'delete'}>{isGranted ? 'granted' : 'denied'}</Badge>
                <span className="truncate font-mono text-xs text-muted-foreground">
                  {isGranted ? 'storage allowed' : 'storage refused'}
                </span>
              </dd>
            </div>
          )
        })}
      </dl>
      {!compact ? (
        <p className="mt-3 text-xs text-muted-foreground">
          {granted.length === Object.keys(axes).length
            ? 'Both axes granted. The visitor keeps a persistent identifier and the session is recorded.'
            : granted.length === 0
              ? 'No axis granted. The session is anonymous, no cookie persists it, and nothing is recorded.'
              : 'One axis granted. The session still ends, because a browser cookie cannot be scoped to one axis.'}
        </p>
      ) : null}
    </div>
  )
}

/**
 * One recorded or refused visit, with what happened to it in words.
 *
 * The blocked case renders the vendor's own console message, because that message is the
 * verification signal the specification names: an operator confirms the block works by
 * looking for it. Rendering the state word alone would leave that check unavailable.
 */
export function VisitVerdict({ visit, ingest = null }) {
  const state = ingestState(visit?.state || ingest?.state)
  const outcome = consentOutcome(visit?.outcome)
  return (
    <div className="rounded-sm border border-border-subtle bg-surface p-4">
      <div className="flex flex-wrap items-center gap-3">
        <Badge tone={state.tone}>{state.label}</Badge>
        {visit?.outcome ? <Badge tone={outcome.tone}>{outcome.label}</Badge> : null}
        {visit?.identity_kind ? (
          <span className="font-mono text-xs text-muted-foreground">{visit.identity_kind}</span>
        ) : null}
      </div>
      <p className="mt-2 text-sm text-foreground">{state.meaning}</p>
      {visit?.reason ? <p className="mt-1 text-xs text-muted-foreground">Reason: {visit.reason}</p> : null}
      {visit?.cookies_persist === false ? (
        <p className="mt-1 text-xs text-muted-foreground">
          No cookie persists this identity. One identifier is issued per page view.
        </p>
      ) : null}
      {visit?.console_signal || visit?.matched_range ? (
        <div className="mt-3 rounded-sm border border-border-subtle bg-muted p-3">
          <p className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
            Matched range {visit.matched_range}
          </p>
          <p className="mt-1 font-mono text-xs text-foreground">
            {visit.console_signal ||
              "Data from this session isn't being collected due to your configured project settings."}
          </p>
        </div>
      ) : null}
    </div>
  )
}

/**
 * Each recording beside the window that applies to it.
 *
 * Both windows appear because the favourite window outliving the ordinary one is the fact
 * a reviewer needs to see, and one number would hide it. A favourite row carries its own
 * word rather than a highlight, because a highlight is a colour and a colour is not a
 * retention class.
 */
export function RetentionTable({ rows = [], ordinaryDays, favouriteDays }) {
  if (!rows.length) {
    return <p className="text-sm text-muted-foreground">No recordings, so nothing ages out yet.</p>
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[34rem] border-collapse text-sm">
        <thead>
          <tr className="border-b border-border-subtle text-left">
            <th className="py-2 pr-3 text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
              Recorded
            </th>
            <th className="py-2 pr-3 text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
              Class
            </th>
            <th className="py-2 pr-3 text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
              Window
            </th>
            <th className="py-2 text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
              State
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id} className="border-b border-border-subtle/60">
              <td className="py-2 pr-3 font-mono text-xs text-foreground">
                {row.recorded_at || 'unknown'}
              </td>
              <td className="py-2 pr-3">
                <Badge tone={row.favourite ? 'update' : 'neutral'}>
                  {row.favourite ? 'favourite' : 'ordinary'}
                </Badge>
              </td>
              <td className="py-2 pr-3 text-xs text-muted-foreground">
                {formatWindow(row.retention_days)}
              </td>
              <td className="py-2 text-xs">
                {row.expired === true ? 'aged out' : row.expired === false ? 'retained' : 'unknown'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-3 text-xs text-muted-foreground">
        Ordinary {formatWindow(ordinaryDays)}. Favourite {formatWindow(favouriteDays)}. A favourite
        recording outlives the ordinary window, and the random sample the specification also
        mentions is not drawn here.
      </p>
    </div>
  )
}

/**
 * A sourced limit, shown as a limit.
 *
 * The refusal carries the evidence sentence and the supported alternative, so an operator
 * is told what to do and not only what did not work. This renders in the destructive tone
 * because it names a capability the product does not have, and the word "refused" is
 * always present as text.
 */
export function RefusalNote({ title, detail, evidence, remediation }) {
  return (
    <div
      role="note"
      className="rounded-sm border border-destructive/40 bg-destructive/10 p-4"
    >
      <p className="text-sm font-semibold text-destructive">{title}</p>
      {detail ? <p className="mt-1 text-sm text-foreground">{detail}</p> : null}
      {evidence ? (
        <p className="mt-2 font-mono text-xs text-muted-foreground">Evidence: {evidence}</p>
      ) : null}
      {remediation ? <p className="mt-1 text-xs text-foreground">{remediation}</p> : null}
    </div>
  )
}

/** The label count beside the cap, in words. */
export function LabelBudget({ held = [], cap = 5 }) {
  const count = Array.isArray(held) ? held.length : 0
  const limit = Number(cap) || 5
  const left = Math.max(0, limit - count)
  return (
    <p className="text-xs text-muted-foreground">
      {plural(count, 'label')} on this recording. {left === 0
        ? `The cap of ${limit} is reached, so the next label is refused.`
        : `${plural(left, 'slot')} left before the cap of ${limit}.`}
    </p>
  )
}
