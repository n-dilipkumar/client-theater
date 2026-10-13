/**
 * WF-121's own primitives.
 *
 * Everything here is built from `@/components/ui`, and every control meets the
 * accessibility floor: `min-h-11` on each button (the shared `Button` applies it),
 * a visible text label beside each icon, and no status carried by colour alone.
 *
 * These are local because `ui.jsx` has no equivalent, and building them here is
 * the contract's instruction rather than an oversight. Promotion work for the
 * integrator, not a reason to edit a shared file:
 *
 *   - `InlineNote`, because `ui.jsx` has no `Notice` and this page needs a line
 *     that states a fact about what it did or could not do.
 *   - `StateBadge`, because a link's state is a finding and the word beside the
 *     colour is what makes it a finding.
 *   - `PageDwellBars`, because the per-page dwell is a chart and no shared
 *     primitive is one. It is bars rather than a canvas so every value is a
 *     readable number a screen reader reaches.
 *   - `DropOffList`, because the drop-off curve is a per-page list of counts.
 *   - `ViewerRow` and `AlertRow`, because the two panels are rows of facts.
 */

import { Badge, Icon } from '@/components/ui'

import { ALERT_LABELS, alertTone, formatSeconds, shortTimestamp, stateTone } from './api'

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

/** A link's state as a word plus a tone. The word is always present. */
export function StateBadge({ state }) {
  const label = state ? state.charAt(0).toUpperCase() + state.slice(1) : 'Unknown'
  return <Badge tone={stateTone(state)}>{label}</Badge>
}

/** An alert trigger as a word plus a tone. */
export function AlertTriggerBadge({ trigger }) {
  return <Badge tone={alertTone(trigger)}>{ALERT_LABELS[trigger] || trigger || 'Alert'}</Badge>
}

/**
 * Time spent per page, as bars with the number beside each one.
 *
 * A page nobody read is drawn as an empty bar with the word "Not read" rather
 * than as a zero-height row that looks like the chart ended. The point of this
 * metric is to find the pages that did not land.
 */
export function PageDwellBars({ entries = [] }) {
  if (entries.length === 0) {
    return (
      <p className="rounded-sm border border-border-subtle bg-muted px-3 py-6 text-center text-sm text-muted-foreground">
        No page has been read yet, so there is no per-page dwell to show.
      </p>
    )
  }
  const highest = Math.max(...entries.map((entry) => Number(entry.average_seconds) || 0), 1)
  return (
    <ul className="flex flex-col gap-2" aria-label="Average time spent per page">
      {entries.map((entry) => {
        const average = Number(entry.average_seconds) || 0
        const width = entry.reads ? Math.max(4, Math.round((average / highest) * 100)) : 0
        return (
          <li key={entry.page} className="flex flex-col gap-1">
            <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
              <span className="text-foreground">Page {entry.page}</span>
              <span className="font-mono text-muted-foreground">
                {entry.reads ? `${formatSeconds(average)} average, ${entry.reads} read(s)` : 'Not read'}
              </span>
            </div>
            <div
              className="h-2.5 rounded-sm bg-muted"
              style={{ maxWidth: `${width}%` }}
              aria-hidden="true"
            />
            {!entry.reads && (
              <span className="h-2.5 rounded-sm border border-dashed border-border-subtle bg-transparent" />
            )}
          </li>
        )
      })}
    </ul>
  )
}

/** Drop off per page: where readers stopped, on the page they stopped at. */
export function DropOffList({ curve = [], summary }) {
  if (curve.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No reading has reached this deck, so there is no drop-off to show.
      </p>
    )
  }
  const loss = curve.filter((point) => point.dropped > 0)
  return (
    <div className="flex flex-col gap-2">
      <p className="text-xs text-muted-foreground">
        {summary?.sessions ?? 0} reading(s); {summary?.completed_sessions ?? 0} reached the last
        page.
      </p>
      {loss.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          Every reading reached the last page, so nobody has stopped part-way.
        </p>
      ) : (
        <ul className="flex flex-col gap-1">
          {loss.map((point) => (
            <li key={point.page} className="flex items-center justify-between gap-2 text-sm">
              <span className="text-foreground">Stopped at page {point.page}</span>
              <span className="font-mono text-xs text-muted-foreground">
                {point.dropped} reader(s), {Math.round(point.drop_off_rate * 100)}% of those who
                reached it
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/** One buyer behind the link, with the revisits the timings group into. */
export function ViewerRow({ viewer, onOpen }) {
  return (
    <li className="flex flex-wrap items-center justify-between gap-2 border-b border-border-subtle py-2 last:border-b-0">
      <span className="flex min-w-0 flex-col">
        <span className="truncate font-mono text-sm text-foreground">{viewer.viewer}</span>
        <span className="text-xs text-muted-foreground">
          {viewer.revisits} revisit(s), deepest page {viewer.deepest_page}, last seen{' '}
          {shortTimestamp(viewer.last_seen_at)}
        </span>
      </span>
      <button
        type="button"
        className="inline-flex min-h-11 items-center gap-2 rounded-sm border border-border-subtle px-3 text-sm text-foreground hover:border-accent hover:text-accent"
        onClick={() => onOpen(viewer.viewer)}
      >
        <Icon name="search" size={16} />
        Show pages
      </button>
    </li>
  )
}

/** One rep alert. The trigger is named in words beside its tone. */
export function AlertRow({ alert }) {
  return (
    <li className="flex flex-col gap-1 border-b border-border-subtle py-2 last:border-b-0">
      <span className="flex flex-wrap items-center gap-2">
        <AlertTriggerBadge trigger={alert.trigger} />
        <span className="font-mono text-xs text-muted-foreground">
          {alert.viewer || 'anonymous'} | revisit {alert.revisit} |{' '}
          {shortTimestamp(alert.notified_at)}
        </span>
      </span>
      <span className="text-sm text-foreground">{alert.detail}</span>
    </li>
  )
}
