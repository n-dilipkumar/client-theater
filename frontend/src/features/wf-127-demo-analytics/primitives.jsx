/**
 * WF-127's own primitives.
 *
 * Everything here is built from `@/components/ui`, and every control meets the
 * accessibility floor: `min-h-11` on each button (the shared `Button` applies
 * it), a visible text label beside each icon, and no status carried by colour
 * alone.
 *
 * These are local because `ui.jsx` has no equivalent, and building them here is
 * the contract's instruction rather than an oversight. Promotion work for the
 * integrator, not a reason to edit a shared file:
 *
 *   - `InlineNote`, because `ui.jsx` has no `Notice` and this page needs a line
 *     that states a fact about what it did or could not do.
 *   - `StateBadge` and `RoleBadge`, because a state and a role are findings and
 *     the word beside the colour is what makes them findings.
 *   - `HeatmapGrid`, because the activity heatmap is a chart and no shared
 *     primitive is one. It is a table so every cell is a readable number a
 *     screen reader reaches, not a canvas.
 *   - `SectionCounts`, `StakeholderRow` and `RollupTable`, because the three
 *     panels are lists of facts.
 */

import { Badge, Icon } from '@/components/ui'

import { ROLE_LABELS, formatRate, roleTone, shortTimestamp, stateTone } from './api'

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

/** A demo's state as a word plus a tone. The word is always present. */
export function StateBadge({ state }) {
  const label = state ? state.charAt(0).toUpperCase() + state.slice(1) : 'Unknown'
  return <Badge tone={stateTone(state)}>{label}</Badge>
}

/** A stakeholder's role as a word plus a tone. */
export function RoleBadge({ role }) {
  return <Badge tone={roleTone(role)}>{ROLE_LABELS[role] || role || 'Unknown'}</Badge>
}

const INTENSITY = ['bg-muted', 'bg-accent/10', 'bg-accent/25', 'bg-accent/45', 'bg-accent/70']

function intensityClass(count, highest) {
  if (!count || !highest) return INTENSITY[0]
  const step = Math.min(
    INTENSITY.length - 1,
    1 + Math.floor((count / highest) * (INTENSITY.length - 2)),
  )
  return INTENSITY[step]
}

/**
 * Activity by weekday and hour slot.
 *
 * A table rather than a canvas: every cell prints its own count, so the busiest
 * slot is readable as a number and not only as a darker square. The legend says
 * what the shading means, so the colour is a second signal.
 */
export function HeatmapGrid({ heatmap }) {
  const cells = heatmap?.cells || []
  if (!heatmap || heatmap.total === 0) {
    return (
      <p className="rounded-sm border border-border-subtle bg-muted px-3 py-6 text-center text-sm text-muted-foreground">
        No buyer activity yet, so there is no heatmap to draw.
      </p>
    )
  }
  const lookup = new Map(cells.map((cell) => [`${cell.weekday_index}:${cell.bucket}`, cell]))
  const weekdays = heatmap.weekdays || []
  const buckets = heatmap.buckets || []
  return (
    <div className="flex flex-col gap-2">
      <table className="w-full border-separate border-spacing-1 text-xs">
        <caption className="sr-only">
          Buyer activity by weekday and hour slot. Each cell shows the number of events.
        </caption>
        <thead>
          <tr>
            <th scope="col" className="text-left font-medium text-muted-foreground">
              Day
            </th>
            {buckets.map((bucket) => (
              <th
                key={bucket.index}
                scope="col"
                className="font-mono font-normal text-muted-foreground"
              >
                {bucket.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {weekdays.map((weekday, index) => (
            <tr key={weekday}>
              <th scope="row" className="text-left font-normal text-foreground">
                {weekday}
              </th>
              {buckets.map((bucket) => {
                const cell = lookup.get(`${index}:${bucket.index}`)
                const count = cell?.count || 0
                return (
                  <td key={bucket.index} className="p-0">
                    <div
                      className={`flex h-8 items-center justify-center rounded-xs border border-border-subtle font-mono text-[11px] text-foreground ${intensityClass(
                        count,
                        heatmap.max_count,
                      )}`}
                      title={`${weekday} ${bucket.label}: ${count} event(s), ${
                        cell?.viewers || 0
                      } viewer(s)`}
                    >
                      {count || ''}
                    </div>
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-xs text-muted-foreground">
        {heatmap.total} buyer event(s) in {cells.length} slot(s). The busiest slot is{' '}
        {heatmap.busiest
          ? `${heatmap.busiest.weekday} ${heatmap.buckets[heatmap.busiest.bucket]?.label}`
          : 'none'}
        . Darker shading means more events; the number in each cell is the count.
      </p>
    </div>
  )
}

/** The features/sections selected, one row per click-path node. */
export function SectionCounts({ sections }) {
  const rows = sections?.sections || []
  if (rows.length === 0) {
    return <p className="text-sm text-muted-foreground">This demo has no click-path nodes.</p>
  }
  const highest = Math.max(...rows.map((row) => row.count), 1)
  return (
    <ul className="flex flex-col gap-2" aria-label="Sections selected">
      {rows.map((row) => {
        const width = row.count ? Math.max(4, Math.round((row.count / highest) * 100)) : 0
        return (
          <li key={row.section} className="flex flex-col gap-1">
            <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
              <span className="text-foreground">{row.label}</span>
              <span className="font-mono text-muted-foreground">
                {row.count} selection(s), {row.viewers} viewer(s)
              </span>
            </div>
            <div
              className="h-2.5 rounded-sm bg-accent/40"
              style={{ maxWidth: `${width}%` }}
              aria-hidden="true"
            />
          </li>
        )
      })}
    </ul>
  )
}

/** One stakeholder, with the engagement the event stream implies. */
export function StakeholderRow({ stakeholder }) {
  const dql = stakeholder.dql || {}
  return (
    <li className="flex flex-wrap items-center justify-between gap-2 border-b border-border-subtle py-2 last:border-b-0">
      <span className="flex min-w-0 flex-col gap-0.5">
        <span className="truncate font-mono text-sm text-foreground">{stakeholder.viewer}</span>
        <span className="text-xs text-muted-foreground">
          {stakeholder.views} view(s), {stakeholder.sections?.length || 0} section(s),{' '}
          {stakeholder.downloads} download(s), last seen {shortTimestamp(stakeholder.last_seen_at)}
        </span>
        {stakeholder.invited_by && (
          <span className="text-xs text-muted-foreground">
            Discovered through {stakeholder.invited_by}
          </span>
        )}
      </span>
      <span className="flex flex-wrap items-center gap-2">
        <RoleBadge role={stakeholder.role} />
        <Badge tone={dql.qualified ? 'insert' : 'neutral'}>
          {`DQL ${dql.score ?? 0}/${dql.max ?? 100}`}
        </Badge>
        <span className="text-xs text-muted-foreground">
          {dql.qualified ? 'Qualified' : 'Not qualified'}
        </span>
      </span>
    </li>
  )
}

/** One drill-down level as a table of metric groups. */
export function RollupTable({ rollup }) {
  const groups = rollup?.groups || []
  if (groups.length === 0) {
    return <p className="text-sm text-muted-foreground">This level has no group to show yet.</p>
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-xs">
        <thead>
          <tr className="border-b border-border-subtle text-muted-foreground">
            <th scope="col" className="py-2 pr-3 font-medium">
              Group
            </th>
            <th scope="col" className="py-2 pr-3 font-medium">
              Demos
            </th>
            <th scope="col" className="py-2 pr-3 font-medium">
              Sent
            </th>
            <th scope="col" className="py-2 pr-3 font-medium">
              Views
            </th>
            <th scope="col" className="py-2 pr-3 font-medium">
              View rate
            </th>
            <th scope="col" className="py-2 pr-3 font-medium">
              Discovered
            </th>
            <th scope="col" className="py-2 font-medium">
              Discovery rate
            </th>
          </tr>
        </thead>
        <tbody>
          {groups.map((group) => (
            <tr key={group.key} className="border-b border-border-subtle last:border-b-0">
              <td className="py-2 pr-3 text-foreground">{group.label}</td>
              <td className="py-2 pr-3 font-mono text-foreground">{group.demos}</td>
              <td className="py-2 pr-3 font-mono text-foreground">{group.metrics.demos_sent}</td>
              <td className="py-2 pr-3 font-mono text-foreground">{group.metrics.views}</td>
              <td className="py-2 pr-3 font-mono text-foreground">
                {formatRate(group.metrics.view_rate)}
              </td>
              <td className="py-2 pr-3 font-mono text-foreground">
                {group.metrics.stakeholders_discovered}
              </td>
              <td className="py-2 font-mono text-foreground">
                {formatRate(group.metrics.discovery_rate)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
