import { Badge } from '@/components/ui'
import Glyph from './icons'

/**
 * The UI pieces this feature needs that the shared set in `components/ui.jsx`
 * cannot express. They are in their own module, and this file is the whole of
 * what an integrator has to look at.
 *
 * The promotion set, unambiguously:
 *
 *   - `SortHeader` is platform-shaped. Three features now have a sortable column
 *     header and this is the first that treats one as a real control - a button
 *     with `aria-sort` on the column it controls, not a clickable `<th>`. It
 *     belongs in `components/ui.jsx` once the next feature needs it too.
 *   - `BucketBadge` is platform-shaped for the same reason. It maps a bucket to a
 *     glyph and a shared `Badge` tone, which is a decision any status column in
 *     this product will eventually make.
 *
 * `TrendCell` is this feature's own: it is a bucket plus the arithmetic behind
 * it, and no other workflow has a reason to show both in one cell.
 *
 * All three meet the same floor as the shared primitives: a 44px minimum hit
 * target, a real focusable control, a visible text label beside every glyph, and
 * no emoji used as an icon.
 */

/** Bucket to glyph. The four values, hottest first. */
export const BUCKET_GLYPHS = { hot: 'hot', warm: 'warm', cooling: 'cooling', cold: 'cold' }

/**
 * Bucket to a shared `Badge` tone.
 *
 * Only the four shared tones are used, so this feature adds nothing to the
 * design system. The mapping is also meaningful rather than decorative: green for
 * a room worth protecting, blue for one still worth a touch, amber for one going
 * quiet, and neutral for one nobody has opened. Cold is neutral rather than red
 * on purpose - a dead deal is a fact about the deal, not an error, and colouring
 * it as a failure would train a rep to ignore the column.
 */
export const BUCKET_TONES = { hot: 'insert', warm: 'update', cooling: 'restore', cold: 'neutral' }

/**
 * A sortable column header.
 *
 * A real `<button>` inside the `<th>` with `aria-sort` on the cell, because a
 * clickable header that is not a control is unreachable by keyboard and announces
 * nothing about the column it sorts. The label is always visible, and the
 * direction is a word rather than a triangle: the meaning is never carried by the
 * glyph alone.
 */
export function SortHeader({ label, column, sort, order, onSort, className = '' }) {
  const active = sort === column
  const direction = active ? (order === 'desc' ? 'descending' : 'ascending') : 'none'

  return (
    <th
      scope="col"
      aria-sort={direction}
      className={`px-3 py-2 text-left font-mono text-xs font-medium tracking-wide uppercase ${
        active ? 'text-foreground' : 'text-muted-foreground'
      } ${className}`}
    >
      <button
        type="button"
        onClick={() => onSort(column)}
        className="inline-flex min-h-11 items-center gap-1.5 rounded-md px-2 hover:text-foreground
          focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2
          focus-visible:ring-offset-background"
      >
        <span>{label}</span>
        <Glyph
          name="caret"
          size={14}
          className={`transition-transform motion-reduce:transition-none ${
            active ? (order === 'desc' ? 'rotate-180' : '') : 'opacity-40'
          }`}
        />
        <span className="sr-only">
          {active ? `, sorted ${order === 'desc' ? 'descending' : 'ascending'}` : ', not sorted'}
        </span>
      </button>
    </th>
  )
}

/**
 * A bucket, its label, and a glyph.
 *
 * The label is the meaning. The tone is the fast read. Neither is the only signal.
 */
export function BucketBadge({ value, label, size = 16 }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <Glyph name={BUCKET_GLYPHS[value] || 'trend'} size={size} className="text-muted-foreground" />
      <Badge tone={BUCKET_TONES[value] || 'neutral'}>{label || value}</Badge>
    </span>
  )
}

/**
 * One Trend cell: the bucket, and the arithmetic that put it there.
 *
 * The reasoning is there because the researched rules quantify the first two
 * buckets ("tons", "a decent amount") and the numbers behind those words are this
 * build's judgement, published at `/inferences`. A rep acting on a bucket needs
 * to see what produced it, and a reviewer needs to see the same thing.
 */
export function TrendCell({ row }) {
  const events = row.events || {}
  return (
    <div className="min-w-0">
      <BucketBadge value={row.trend} label={row.label} />
      <p className="mt-1 text-xs text-muted-foreground">
        {events.qualifying_in_7_days ?? 0} external in 7d · {events.qualifying_in_14_days ?? 0} in 14d
      </p>
      {row.reasons && row.reasons.length > 0 && (
        <p className="mt-0.5 max-w-md text-xs text-muted-foreground/80">{row.reasons.join('; ')}</p>
      )}
    </div>
  )
}
