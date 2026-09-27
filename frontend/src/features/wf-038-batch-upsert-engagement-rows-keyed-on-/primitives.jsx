/**
 * Primitives the shared UI set does not carry (WF-038).
 *
 * The researched UI for this workflow is a **progress bar** and a **per-row error
 * list**, and `components/ui.jsx` has neither. Per the feature contract, a
 * primitive a feature genuinely needs is built inside that feature's folder and
 * named in the PR description, so the integrator can promote it into `ui.jsx` as
 * platform work once, rather than twelve features each shipping a subtly different
 * one.
 *
 * The bar is a `role="progressbar"` with `aria-valuenow`, because the research says
 * the rep watches a run in progress and a coloured bar alone says nothing to a
 * screen reader. It also animates width, and the animation is disabled under
 * `prefers-reduced-motion` per the design system's floor.
 */

import { Badge, JsonView } from '@/components/ui'

const TONES = {
  created: 'insert',
  updated: 'update',
  submitted: 'neutral',
  failed: 'delete',
  rejected: 'delete',
  rolled_back: 'delete',
  synced: 'insert',
  unconfirmed: 'neutral',
  pending: 'neutral',
}

/** The one place an outcome is turned into a word, a tone and a colour. */
export function outcomeMeta(outcome) {
  const tone = TONES[outcome] || 'neutral'
  return {
    tone,
    colour: tone === 'insert' ? 'bg-accent' : tone === 'update' ? 'bg-sky-500' : tone === 'delete' ? 'bg-destructive' : 'bg-muted-foreground',
    label: String(outcome || 'unknown').replace(/_/g, ' '),
    // Why this row is where it is, in the reader's terms rather than the code's.
    meaning: OUTCOME_MEANING[outcome] || '',
  }
}

export const OUTCOME_MEANING = {
  created: 'The CRM had no record for this key, so it created one.',
  updated: 'The CRM already had a record for this key and updated it.',
  submitted: 'Sent. This CRM returns no per-item result, so nothing confirms it either way.',
  failed: 'The CRM refused this row. Its own error text is below.',
  rejected: 'The connector refused to send this row. It never reached the CRM.',
  rolled_back: 'A sibling row failed under allOrNone, so the whole request was rolled back.',
}

/**
 * A run's progress, as the research describes it: "Sync -> Run upsert with a
 * progress bar".
 *
 * `percent` comes from the server, which computes it over the rows it actually put
 * on the wire. Computing it here instead would mean this file had a second copy of
 * a rule that belongs to the connector.
 */
export function ProgressBar({ progress, label = 'Run progress' }) {
  if (!progress) return null
  const percent = Math.max(0, Math.min(100, Number(progress.percent) || 0))
  return (
    <div
      role="progressbar"
      aria-valuenow={percent}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={label}
      className="space-y-2"
    >
      <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
        <div
          className="h-full rounded-full bg-accent transition-[width] duration-300 motion-reduce:transition-none"
          style={{ width: `${percent}%` }}
        />
      </div>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-4">
        <Stat label="Progress" value={`${percent}%`} />
        <Stat label="Chunks" value={`${progress.chunks_done ?? 0}/${progress.chunks_total ?? 0}`} />
        <Stat label="Rows sent" value={progress.rows_sent ?? 0} />
        <Stat label="Confirmed" value={progress.rows_confirmed ?? 0} />
      </dl>
    </div>
  )
}

function Stat({ label, value }) {
  return (
    <div className="flex items-baseline gap-2">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="font-mono text-foreground">{value}</dd>
    </div>
  )
}

/**
 * The researched "per-row error list".
 *
 * Every row is listed, not only the failures, because a row that reads `submitted`
 * is as much a thing to look at as one that reads `failed`: neither is confirmed,
 * and a list that showed only errors would hide the ones a rep has to check in the
 * CRM by hand.
 */
export function OutcomeList({ outcomes = [], empty = 'No rows in this run.' }) {
  if (!outcomes.length) {
    return <p className="text-sm text-muted-foreground">{empty}</p>
  }
  return (
    <ul className="divide-y divide-border-subtle/15">
      {outcomes.map((outcome) => {
        const meta = outcomeMeta(outcome.outcome)
        return (
          <li key={`${outcome.record_id}-${outcome.chunk_index}-${outcome.row_index}`} className="py-2.5">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={meta.tone}>{meta.label}</Badge>
              <span className="font-mono text-[13px] text-foreground">
                {outcome.key || <span className="text-muted-foreground">(no key)</span>}
              </span>
              {outcome.crm_record_id && (
                <span className="font-mono text-[11px] text-muted-foreground">
                  crm_record_id {outcome.crm_record_id}
                </span>
              )}
              <span className="ml-auto font-mono text-[11px] text-muted-foreground">
                chunk {outcome.chunk_index} · row {outcome.row_index}
                {outcome.status ? ` · HTTP ${outcome.status}` : ''}
              </span>
            </div>
            {meta.meaning && <p className="mt-1 text-xs text-muted-foreground">{meta.meaning}</p>}
            {outcome.errors?.map((text) => (
              <p
                key={text}
                className="mt-1 rounded-md border border-destructive/30 bg-destructive/10 px-2 py-1 font-mono text-[12px] text-destructive"
              >
                {text}
              </p>
            ))}
          </li>
        )
      })}
    </ul>
  )
}

/** One chunk's request, expanded, so a run can be checked against the vendor's docs. */
export function ChunkRequest({ chunk }) {
  return (
    <details className="border-b border-border-subtle/15 last:border-0">
      <summary className="flex min-h-11 cursor-pointer items-center gap-2 py-2 text-sm transition-colors duration-150 hover:bg-muted/40">
        <span className="font-mono text-[13px] text-foreground">
          {chunk.method} {chunk.path}
        </span>
        <span className="font-mono text-[11px] text-muted-foreground">
          {chunk.size} rows · HTTP {chunk.status}
        </span>
        {chunk.error && <span className="font-mono text-[11px] text-destructive">chunk error</span>}
      </summary>
      <div className="pb-3 pl-2 text-xs">
        {Object.keys(chunk.query || {}).length > 0 && (
          <p className="mb-2 font-mono text-muted-foreground">
            query: {Object.entries(chunk.query).map(([k, v]) => `${k}=${v}`).join('&')}
          </p>
        )}
        <JsonView value={chunk.body} />
      </div>
    </details>
  )
}
