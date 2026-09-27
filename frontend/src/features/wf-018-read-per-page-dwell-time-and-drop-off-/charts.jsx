/**
 * The three charts WF-018 shows, drawn as inline SVG.
 *
 * The design system has no chart library in it and these are simple dense
 * series, so they are drawn here rather than pulling in a dependency. Building
 * them inside this feature folder is deliberate: `components/ui.jsx` is shared,
 * and the contract says a primitive that does not exist yet belongs in the
 * feature that needs it, to be promoted later as platform work.
 *
 * Accessibility, because these are real widgets rather than decoration:
 *
 * * the SVG is `aria-hidden` and every figure is repeated in a real `<table>`
 *   on the page, so a screen reader gets the same data;
 * * every mark carries a `<title>`, so a pointer user gets the exact number;
 * * colour is never the only signal: bar height, the printed value, and the
 *   row label all carry the same information;
 * * nothing animates, so `prefers-reduced-motion` needs no special case here.
 *   The shared stylesheet already collapses transitions globally, and a chart
 *   that animates its bars in would be exactly the "instant state change" the
 *   design system forbids.
 */

import { formatCount, formatDuration, formatRate } from './format'

/** Chart geometry in viewBox units. Stretched to the panel by preserveAspectRatio. */
const VB_WIDTH = 100
const VB_HEIGHT = 40
const PADDING = 2

/**
 * Bars are filled with a gradient whose stops are `currentColor`, so these are
 * `text-*` utilities rather than `bg-*`: setting `background-color` leaves
 * `currentColor` inherited from the parent and every bar renders white.
 */
const FRAME_PROPS = {
  viewBox: `0 0 ${VB_WIDTH} ${VB_HEIGHT}`,
  preserveAspectRatio: 'none',
  className: 'h-24 w-full',
  'aria-hidden': 'true',
  focusable: 'false',
}

function Empty({ children }) {
  return <p className="py-6 text-center text-sm text-muted-foreground">{children}</p>
}

/**
 * **Time spent per page**: one bar per page, height is the average dwell.
 *
 * A page nobody read is drawn as a hairline at the floor rather than omitted,
 * because "pages 19 to 24 were not read" is the finding this chart exists to
 * produce.
 */
export function DwellBars({ points = [] }) {
  if (points.length === 0) return <Empty>No page timings recorded yet.</Empty>

  const values = points.map((point) => Number(point.average_seconds) || 0)
  const peak = Math.max(1, ...values)
  const span = VB_HEIGHT - PADDING * 2
  const slot = VB_WIDTH / points.length
  const width = Math.max(0.12, slot * 0.66)

  return (
    <svg {...FRAME_PROPS}>
      {points.map((point, index) => {
        const value = values[index]
        const height = value ? Math.max(0.6, (value / peak) * span) : 0.4
        return (
          <rect
            key={point.page}
            x={index * slot + (slot - width) / 2}
            y={PADDING + (span - height)}
            width={width}
            height={height}
            rx={0.4}
            className={value ? 'fill-accent/70' : 'fill-muted-foreground/40'}
          >
            <title>
              {`Page ${point.page}: ${
                value ? `${formatDuration(point.average_seconds)} average across ${point.reads} reads` : 'never read'
              }`}
            </title>
          </rect>
        )
      })}
    </svg>
  )
}

/**
 * **Drop off per page**: the retention curve, drawn as the staircase it is.
 *
 * A straight line between pages would draw a drop that did not happen. A
 * horizontal run to each page and then a vertical drop says precisely what the
 * data says: everyone who reached page N also reached everything before it, and
 * this is the page where the remaining readers left.
 */
export function RetentionCurve({ points = [] }) {
  if (points.length === 0) return <Empty>No reading sessions to draw a curve from.</Empty>

  const span = VB_HEIGHT - PADDING * 2
  const last = Math.max(1, points.length - 1)
  const placed = points.map((point, index) => ({
    ...point,
    x: (index / last) * VB_WIDTH,
    y: PADDING + (1 - (Number(point.retained_rate) || 0)) * span,
  }))

  let path = ''
  placed.forEach((point, index) => {
    if (index === 0) {
      path += `M${point.x.toFixed(2)} ${point.y.toFixed(2)}`
    } else {
      path += `H${point.x.toFixed(2)}V${point.y.toFixed(2)}`
    }
  })

  return (
    <svg {...FRAME_PROPS}>
      <path
        d={path}
        fill="none"
        className="stroke-sky-300"
        strokeWidth={2}
        strokeLinejoin="round"
        strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
      />
      {placed.map((point) => (
        <circle key={point.page} cx={point.x} cy={point.y} r={0.7} className="fill-sky-300">
          <title>{`Page ${point.page}: ${formatRate(point.retained_rate)} of readers still reading`}</title>
        </circle>
      ))}
    </svg>
  )
}

/**
 * **Core Analytics**: views, downloads and shares per bucket.
 *
 * Three bars per bucket rather than three charts, because the question the
 * source's bar charts answer is comparative: did views go up while shares went
 * up too, or did the team share something nobody opened?
 */
const SERIES_METRICS = [
  { key: 'views', label: 'Views', className: 'fill-accent/70' },
  { key: 'downloads', label: 'Downloads', className: 'fill-sky-300/70' },
  { key: 'shares', label: 'Shares', className: 'fill-amber-300/70' },
]

export function EventSeries({ series = [] }) {
  if (series.length === 0) return <Empty>No asset events recorded yet.</Empty>

  const peak = Math.max(1, ...series.flatMap((bucket) => SERIES_METRICS.map((m) => Number(bucket[m.key]) || 0)))
  const span = VB_HEIGHT - PADDING * 2
  const slot = VB_WIDTH / series.length
  const width = Math.max(0.08, (slot * 0.74) / SERIES_METRICS.length)

  return (
    <>
      <div className="mb-2 flex flex-wrap gap-4 text-xs text-muted-foreground">
        {SERIES_METRICS.map((metric) => (
          <span key={metric.key} className="inline-flex items-center gap-1.5">
            <span className={`inline-block h-2.5 w-2.5 rounded-sm ${metric.className}`} aria-hidden="true" />
            {metric.label}
          </span>
        ))}
      </div>
      <svg {...FRAME_PROPS}>
        {series.map((bucket, index) => (
          <g key={bucket.bucket}>
            {SERIES_METRICS.map((metric, position) => {
              const value = Number(bucket[metric.key]) || 0
              const height = value ? Math.max(0.6, (value / peak) * span) : 0.3
              return (
                <rect
                  key={metric.key}
                  x={index * slot + slot * 0.13 + position * width}
                  y={PADDING + (span - height)}
                  width={width}
                  height={height}
                  className={value ? metric.className : 'fill-muted-foreground/30'}
                >
                  <title>{`${bucket.bucket} · ${metric.label}: ${formatCount(value)}`}</title>
                </rect>
              )
            })}
          </g>
        ))}
      </svg>
    </>
  )
}

/** The axis labels under a per-page chart. Pages are 1..N, so a stride is enough. */
export function PageAxis({ count }) {
  if (!count) return null
  const stride = Math.max(1, Math.ceil(count / 24))
  const pages = []
  for (let page = 1; page <= count; page += stride) pages.push(page)
  if (pages[pages.length - 1] !== count) pages.push(count)
  return (
    <div className="mt-1 flex justify-between font-mono text-[10px] text-muted-foreground">
      {pages.map((page) => (
        <span key={page}>{page}</span>
      ))}
    </div>
  )
}
