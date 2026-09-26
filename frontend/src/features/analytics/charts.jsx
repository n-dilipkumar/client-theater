/**
 * Chart and status primitives for the Analytics view.
 *
 * The design system has no chart library in it, and the two researched charts
 * (Recent Engagement, Visit Frequency) are simple dense series, so they are
 * drawn as inline SVG bars rather than pulling in a dependency.
 *
 * Accessibility notes, because these are real widgets rather than decoration:
 * * the SVG is `aria-hidden` and the numbers are repeated in a real table
 *   inside a `details` element, so a screen reader gets the same data;
 * * every bar has a `title` element for pointer users;
 * * colour is never the only signal - the bar height, the value, and the
 *   textual label all carry the same information.
 */

import { useId } from 'react'

const CHART_HEIGHT = 96

/** Trend tone per researched state: Cold / Warm / Hot. */
const TREND_TONES = {
  hot: 'bg-accent/70 text-accent border-accent/40',
  warm: 'bg-amber-300/20 text-amber-300 border-amber-300/40',
  cold: 'bg-sky-300/20 text-sky-300 border-sky-300/40',
}

/**
 * Chart bars are filled from a gradient whose stops are `currentColor`, so
 * these are `text-*` utilities rather than `bg-*`: setting `background-color`
 * leaves `currentColor` inherited from the parent and every bar renders white.
 */
const TREND_STROKE = {
  hot: 'text-accent',
  warm: 'text-amber-300',
  cold: 'text-sky-300',
}

/** Used where a solid background is what is wanted (the share bars). */
const TREND_FILL = {
  hot: 'bg-accent',
  warm: 'bg-amber-300',
  cold: 'bg-sky-300',
}

export function TrendBadge({ classification = 'cold', children }) {
  const tone = TREND_TONES[classification] || TREND_TONES.cold
  return (
    <span
      className={`inline-flex items-center rounded-md border px-2 py-0.5 font-mono text-xs font-medium ${tone}`}
    >
      {children || classification}
    </span>
  )
}

/**
 * Dense bar chart. `points` are `{ bucket_start, ...metrics }` in ascending
 * bucket order, which is what the API returns.
 */
export function BarChart({ points = [], metric = 'actions', label, tone = 'hot', grain = 'day' }) {
  const gradientId = useId()
  const values = points.map((point) => Number(point[metric]) || 0)
  const peak = Math.max(1, ...values)
  const total = values.reduce((sum, value) => sum + value, 0)
  const stroke = TREND_STROKE[tone] || TREND_STROKE.hot

  if (points.length === 0) {
    return <p className="text-sm text-muted-foreground">No buckets in this window.</p>
  }

  return (
    <div>
      <div className="flex items-baseline justify-between gap-3">
        <p className="text-sm text-muted-foreground">{label}</p>
        <p className="font-mono text-sm text-foreground">
          {total} total <span className="text-muted-foreground">· peak {peak}</span>
        </p>
      </div>

      <svg
        aria-hidden="true"
        focusable="false"
        viewBox={`0 0 ${points.length * 14} ${CHART_HEIGHT}`}
        preserveAspectRatio="none"
        // The tone lives on the <svg> because a gradient's `currentColor`
        // stops resolve against the element the gradient is *defined* in, not
        // the shape that references it. Setting it per-rect leaves every bar
        // painted in the inherited foreground colour instead.
        className={`mt-3 h-24 w-full ${stroke}`}
      >
        <defs>
          <linearGradient id={gradientId} x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="currentColor" stopOpacity="0.75" />
            <stop offset="100%" stopColor="currentColor" stopOpacity="0.25" />
          </linearGradient>
        </defs>
        {points.map((point, index) => {
          const value = Number(point[metric]) || 0
          const height = Math.round((value / peak) * (CHART_HEIGHT - 4))
          return (
            <rect
              key={point.bucket_start}
              x={index * 14}
              y={CHART_HEIGHT - height}
              width={10}
              height={height}
              rx={2}
              // An empty bucket still draws a stub, so a gap in the series is
              // visible as a gap rather than as a missing column.
              fill={value > 0 ? `url(#${gradientId})` : 'var(--color-muted)'}
            >
              <title>{`${point.bucket_start}: ${value}`}</title>
            </rect>
          )
        })}
      </svg>

      <div className="mt-1 flex justify-between text-xs text-muted-foreground">
        <span>{points[0]?.bucket_start}</span>
        <span>{points[points.length - 1]?.bucket_start}</span>
      </div>

      <details className="mt-2 text-xs text-muted-foreground">
        <summary className="min-h-11 cursor-pointer py-2 text-accent">Show the numbers</summary>
        <table className="w-full text-left">
          <caption className="sr-only">
            {label}, by {grain}
          </caption>
          <thead>
            <tr>
              <th scope="col" className="py-1 font-medium">
                {grain === 'week' ? 'Week of' : 'Day'}
              </th>
              <th scope="col" className="py-1 text-right font-medium">
                {metric}
              </th>
            </tr>
          </thead>
          <tbody>
            {points
              .filter((point) => Number(point[metric]) || 0)
              .map((point) => (
                <tr key={point.bucket_start} className="border-t border-border-subtle/20">
                  <td className="py-1 font-mono">{point.bucket_start}</td>
                  <td className="py-1 text-right font-mono text-foreground">{point[metric]}</td>
                </tr>
              ))}
          </tbody>
        </table>
      </details>
    </div>
  )
}

/** Horizontal proportion bar, used for the per-visitor and per-document share. */
export function ShareBar({ value, max, tone = 'hot' }) {
  const peak = Math.max(1, max || 1)
  const width = Math.max(2, Math.round((value / peak) * 100))
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted" role="presentation">
      <div
        className={`h-full rounded-full transition-[width] duration-300 ${TREND_FILL[tone] || TREND_FILL.hot}`}
        style={{ width: `${width}%` }}
      />
    </div>
  )
}
