import { ICONS } from './icons'

/**
 * Four UI pieces this page needs that the shared set in `components/ui.jsx` does
 * not export: `Notice`, `Segmented`, `SortableTh` and `TrendChart`.
 *
 * The contract is explicit about what to do about that. Reach for the shared
 * primitives first; where one genuinely does not exist, build it inside this
 * feature folder and say so in the PR description, and the integrator promotes
 * the recurring ones into `ui.jsx` once, as platform work. `ui.jsx` is shared and
 * a hundred features each appending to it is the exact collision the plugin host
 * exists to prevent.
 *
 * What is a promotion candidate, stated unambiguously for the integrator:
 *
 *   - `Notice`, `Segmented` and `SortableTh` are all platform-shaped.
 *     `docs/FEATURE-CONTRACT.md` already lists `Notice` among the primitives a
 *     feature may use and does not export it, which is a documentation gap worth
 *     closing rather than widening. `Segmented` is the standard control for
 *     mutually exclusive options that should stay visible, and two features have
 *     now built it independently. `SortableTh` is a table header that reports
 *     `aria-sort` and stays a real button.
 *   - `TrendChart` is *not*. A hand-rolled SVG chart is this page's own
 *     visualisation of a researched graph; the shared set has no charting
 *     primitive and inventing one is platform work, not something to promote
 *     from a feature.
 *
 * Every one of them meets the same floor as the shared primitives: a 44px
 * minimum target, a real focusable control, a visible focus ring (the global
 * `:focus-visible` in `index.css` does that work and is never removed here), a
 * visible text label, and no emoji used as an icon.
 */

const NOTICE_TONES = {
  info: { box: 'border-sky-500/40 bg-sky-500/10', text: 'text-sky-200', icon: ICONS.info },
  warn: { box: 'border-amber-500/40 bg-amber-500/10', text: 'text-amber-200', icon: ICONS.warning },
  danger: { box: 'border-destructive/40 bg-destructive/10', text: 'text-destructive', icon: ICONS.warning },
}

/**
 * Inline explanatory note, for where the interface has to justify a constraint
 * rather than merely report a failure: a library that has not been built out, or
 * a Content & Sales Influence report whose CRM links are not connected.
 *
 * The meaning is always carried by `title` or the body. Tone is a second channel,
 * never the only one - a colour alone is not an accessible signal.
 */
export function Notice({ tone = 'info', title, children }) {
  const palette = NOTICE_TONES[tone] || NOTICE_TONES.info
  return (
    <div className={`flex gap-3 rounded-lg border p-3 text-sm ${palette.box} ${palette.text}`}>
      <span className="mt-0.5 shrink-0" aria-hidden="true">
        <svg
          width="18"
          height="18"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d={palette.icon} />
        </svg>
      </span>
      <div className="min-w-0">
        {title && <p className="font-semibold">{title}</p>}
        <div className={title ? 'mt-0.5' : ''}>{children}</div>
      </div>
    </div>
  )
}

/**
 * A mutually exclusive set of options, rendered as a real radio group.
 *
 * Radios rather than buttons: every alternative stays visible instead of hiding
 * behind a toggle, and arrow-key navigation comes free. The input is visually
 * hidden but stays in the tab order, and the label carries the focus ring, so
 * keyboard focus is never invisible.
 */
export function Segmented({ label, value, options, onChange, name, hint }) {
  return (
    <fieldset className="flex min-w-0 flex-col gap-1.5">
      {label && <legend className="text-xs font-medium text-muted-foreground">{label}</legend>}
      <div className="flex flex-wrap gap-1 rounded-lg border border-border-subtle/40 bg-background/40 p-1">
        {options.map((option) => {
          const active = option.value === value
          const id = `${name}-${option.value}`
          return (
            <div key={option.value} className="min-w-0 flex-1">
              <input
                id={id}
                type="radio"
                name={name}
                value={option.value}
                checked={active}
                onChange={() => onChange(option.value)}
                className="peer sr-only"
              />
              <label
                htmlFor={id}
                className={`flex min-h-11 cursor-pointer items-center justify-center rounded-md px-3
                  text-center text-sm transition-colors duration-150
                  peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2
                  peer-focus-visible:outline-accent ${
                    active
                      ? 'bg-accent/20 font-medium text-accent'
                      : 'text-muted-foreground hover:bg-muted hover:text-foreground'
                  }`}
              >
                {option.label}
              </label>
            </div>
          )
        })}
      </div>
      {hint && <p className="text-xs text-muted-foreground/80">{hint}</p>}
    </fieldset>
  )
}

/**
 * A table header that sorts.
 *
 * A real `<button>` inside the `<th>`, not a click handler on the cell: it needs
 * to be reachable by keyboard, and the sort state has to be announced rather than
 * being only a colour change. `aria-sort` on the `<th>` is what a screen reader
 * reads out, so it is set here rather than left to the cell's styling.
 */
export function SortableTh({ column, label, sort, direction, onSort, numeric = false, className = '' }) {
  const active = sort === column
  const next = active && direction === 'desc' ? 'asc' : 'desc'
  return (
    <th
      scope="col"
      aria-sort={active ? (direction === 'asc' ? 'ascending' : 'descending') : 'none'}
      className={`px-3 py-2 font-medium ${numeric ? 'text-right' : 'text-left'} ${className}`}
    >
      <button
        type="button"
        onClick={() => onSort(column, next)}
        className={`flex min-h-11 w-full items-center gap-1.5 rounded-md px-1 text-xs uppercase
          tracking-wide transition-colors duration-150 hover:text-foreground
          focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent
          ${numeric ? 'justify-end' : ''} ${active ? 'text-accent' : 'text-muted-foreground'}`}
      >
        {label}
        {/* The arrow is decorative: the direction is already announced by
            aria-sort, and a glyph alone would be the only signal otherwise. */}
        <span aria-hidden="true" className="text-[10px] leading-none">
          {active ? (direction === 'asc' ? '▲' : '▼') : '↕'}
        </span>
      </button>
    </th>
  )
}

/**
 * The researched "Content engagement over time" graph.
 *
 * Grouped bars, drawn as inline SVG rather than through a charting library: the
 * product's stack has no chart dependency, the graph is three series over at
 * most a few hundred buckets, and adding a library for it is a decision that
 * belongs to the platform, not to one feature page.
 *
 * Two accessibility properties the visual alone does not carry:
 *
 *  * the whole chart is one `role="img"` with a text summary, because a screen
 *    reader reading 400 `<rect>`s is worse than no chart;
 *  * every data point is in a `<table>` below the chart, which is also what a
 *    reader copies values out of.
 *
 * The axis ticks honour the series scale rather than being decorative. The
 * drawing animates nothing, so `prefers-reduced-motion` needs no special case -
 * there is no transition to disable.
 */
export function TrendChart({ points, grain, series }) {
  if (!points.length) {
    return (
      <p className="px-1 py-6 text-sm text-muted-foreground">
        No content activity in this range, so there is nothing to graph.
      </p>
    )
  }

  const width = 720
  const height = 180
  const padding = { top: 12, right: 8, bottom: 28, left: 36 }
  const plotWidth = width - padding.left - padding.right
  const plotHeight = height - padding.top - padding.bottom
  const peak = Math.max(
    1,
    ...points.flatMap((point) => series.map((key) => Number(point[key]) || 0)),
  )
  const slot = plotWidth / points.length
  const barWidth = Math.max(2, Math.min(14, (slot - 2) / series.length))
  const ticks = [0, Math.round(peak / 2), peak]

  const summary = `Content engagement by ${grain}: ${points.length} buckets, peak ${peak} per bucket, ` +
    `${points.reduce((sum, point) => sum + (Number(point.shares) || 0), 0)} shares and ` +
    `${points.reduce((sum, point) => sum + (Number(point.views) || 0), 0)} client views in total.`

  return (
    <figure className="min-w-0">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="h-auto w-full"
        role="img"
        aria-label={summary}
        preserveAspectRatio="none"
      >
        {ticks.map((tick) => {
          const y = padding.top + plotHeight - (tick / peak) * plotHeight
          return (
            <g key={tick}>
              <line
                x1={padding.left}
                x2={width - padding.right}
                y1={y}
                y2={y}
                className="stroke-border-subtle/40"
                strokeWidth="1"
              />
              <text
                x={padding.left - 6}
                y={y + 3}
                textAnchor="end"
                className="fill-muted-foreground text-[10px]"
              >
                {tick}
              </text>
            </g>
          )
        })}
        {points.map((point, index) =>
          series.map((key, position) => {
            const value = Number(point[key]) || 0
            const barHeight = (value / peak) * plotHeight
            return (
              <rect
                key={`${point.bucket}-${key}`}
                x={padding.left + index * slot + position * barWidth}
                y={padding.top + plotHeight - barHeight}
                width={Math.max(1, barWidth - 1)}
                height={barHeight}
                className={
                  key === 'shares' ? 'fill-accent/70' : key === 'downloads' ? 'fill-sky-400/50' : 'fill-muted-foreground/60'
                }
              />
            )
          }),
        )}
        {points.map((point, index) =>
          index % Math.ceil(points.length / 6) === 0 ? (
            <text
              key={point.bucket}
              x={padding.left + index * slot + slot / 2}
              y={height - 10}
              textAnchor="middle"
              className="fill-muted-foreground text-[10px]"
            >
              {point.bucket}
            </text>
          ) : null,
        )}
      </svg>
      <figcaption className="mt-2 flex flex-wrap items-center gap-4 text-xs text-muted-foreground">
        {series.map((key) => (
          <span key={key} className="flex items-center gap-1.5">
            <span
              aria-hidden="true"
              className={`inline-block h-2.5 w-2.5 rounded-sm ${
                key === 'shares'
                  ? 'bg-accent/70'
                  : key === 'downloads'
                    ? 'bg-sky-400/50'
                    : 'bg-muted-foreground/60'
              }`}
            />
            {key === 'shares' ? 'Shares' : key === 'views' ? 'Client views' : 'Downloads'}
          </span>
        ))}
      </figcaption>
    </figure>
  )
}
