import { Badge } from '@/components/ui'

import Glyph from './icons'

/**
 * The UI pieces this feature needs that the shared set in `components/ui.jsx`
 * cannot express. They are in their own module, and this file is the whole of what
 * an integrator has to look at.
 *
 * The promotion candidate, unambiguously:
 *
 *   - `StatusChip` is platform-shaped. It maps a status *and* a waiting state to a
 *     glyph and a shared `Badge` tone, and it is the first thing in this product
 *     that shows two orthogonal facts about one row - "this failed" and "nobody is
 *     doing anything about it yet" - which is a distinction any sync status column
 *     in this product will eventually need. It belongs in `components/ui.jsx` once
 *     the next feature needs it too.
 *
 * `StatusFilter` is this feature's own: it filters on two axes at once, and the
 * counts it shows are the server's summary rather than anything computed here.
 *
 * All of them meet the same floor as the shared primitives: a 44px minimum hit
 * target, a real focusable control, a visible text label beside every glyph, and
 * no emoji used as an icon.
 */

/**
 * Status to a shared `Badge` tone.
 *
 * Only the four shared tones are used, so this feature adds nothing to the design
 * system. The mapping is meaningful rather than decorative: green for a write that
 * landed, red for one that did not, amber for one the queue will keep retrying on
 * its own, and neutral for a row nobody is touching.
 */
export const STATUS_TONES = { succeeded: 'insert', failed: 'delete' }

/** Status to a glyph. The tick closes, the cross does not. */
export const STATUS_GLYPHS = { succeeded: 'succeeded', failed: 'failed' }

/** A waiting state to a glyph, drawn from `icons.jsx`. */
export const DISPOSITION_GLYPHS = {
  queued: 'queued',
  needs_action: 'needsAction',
  resolved: 'succeeded',
}

/**
 * A per-row status chip: the outcome, and what it is waiting for.
 *
 * The two are shown together because they are two different facts and a rep acts on
 * them differently. "Failed" alone does not say whether to wait or to fix something;
 * "Needs a person" alone does not say whether the write landed. The label is always
 * the meaning and the glyph is the fast read, so neither is the only signal - which
 * is the accessibility floor the design system asks for and which a colour-only
 * status column fails every time.
 */
export function StatusChip({ status, disposition, labels, dispositions, size = 16 }) {
  const statusLabel = labels?.[status] || status
  const dispositionLabel = disposition ? dispositions?.[disposition] || disposition : null

  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      <span className="inline-flex items-center gap-1.5">
        <Glyph
          name={STATUS_GLYPHS[status] || 'failed'}
          size={size}
          className="text-muted-foreground"
        />
        <Badge tone={STATUS_TONES[status] || 'neutral'}>{statusLabel}</Badge>
      </span>
      {dispositionLabel && (
        <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
          <Glyph
            name={DISPOSITION_GLYPHS[disposition] || 'queued'}
            size={size - 3}
            className="text-muted-foreground"
          />
          {dispositionLabel}
        </span>
      )}
    </span>
  )
}

/**
 * A status filter.
 *
 * `aria-pressed` rather than a colour, and the count is in the label rather than in
 * a badge beside it, so the control reads the same with no colour, in forced colours
 * mode, and to a screen reader.
 */
export function StatusFilter({ active, onClick, tone = 'neutral', children }) {
  const activeTone = {
    neutral: 'bg-muted text-foreground border-border-subtle/50',
    insert: 'bg-accent/20 text-accent border-accent/40',
    delete: 'bg-destructive/20 text-destructive border-destructive/40',
    update: 'bg-sky-500/20 text-sky-300 border-sky-500/40',
  }[tone]

  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={`inline-flex min-h-11 cursor-pointer items-center gap-1.5 rounded-lg border px-3
        text-sm transition-colors duration-200 focus-visible:ring-2 focus-visible:ring-accent
        focus-visible:ring-offset-2 focus-visible:ring-offset-background ${
          active ? activeTone : 'border-transparent bg-muted/50 text-muted-foreground hover:text-foreground'
        }`}
    >
      {children}
    </button>
  )
}

/**
 * The offending property and what was expected of it, in one cell.
 *
 * The three facts the research asks an admin to be able to see - which property,
 * what was sent, what was expected - are three rows here rather than one sentence,
 * because a long reason and a long expected value in a table cell is a cell nobody
 * can read. A missing property is stated rather than left blank, because a blank
 * cell here reads as "nothing was wrong".
 */
export function PropertyCell({ row }) {
  const expected = row.expected || []

  return (
    <div className="min-w-0">
      {row.field ? (
        <span className="inline-flex items-center gap-1.5">
          <Glyph name="property" size={14} className="text-muted-foreground" />
          <span className="font-mono text-[13px] text-foreground">{row.field}</span>
          {row.field_basis && (
            <span className="text-xs text-muted-foreground/80">({row.field_basis} said this)</span>
          )}
        </span>
      ) : (
        <span className="text-xs text-muted-foreground/80">no property named</span>
      )}
      {expected.length > 0 && (
        <ul className="mt-1 flex flex-col gap-0.5 text-xs text-muted-foreground">
          {expected.map((item) => (
            <li key={item.rule_id} className="font-mono">
              {item.expected}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/**
 * A vendor's documentation link, when the vendor gave one.
 *
 * Only Dataverse is documented to produce a ``HelpLink`` annotation, so this
 * renders for one connector out of three and shows nothing at all for the other two.
 * Inventing a link for the others would be a fabricated URL pointing somewhere a
 * rep would lose an afternoon.
 */
export function DocLink({ href }) {
  if (!href) return null

  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer noopener"
      className="inline-flex min-h-11 cursor-pointer items-center gap-1.5 rounded-md px-1 text-sm
        text-accent hover:underline focus-visible:ring-2 focus-visible:ring-accent
        focus-visible:ring-offset-2 focus-visible:ring-offset-background"
    >
      <Glyph name="doc" size={15} />
      Vendor guidance
      <span className="sr-only">(opens in a new tab)</span>
    </a>
  )
}
