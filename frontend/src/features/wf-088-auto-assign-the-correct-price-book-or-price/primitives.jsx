/**
 * The few shapes this workflow needs that the shared primitives do not carry.
 *
 * Everything that exists in `@/components/ui` is imported from there and never copied.
 * `Badge`, `Button`, `Card`, `EmptyState`, `ErrorNote`, `Field`, `Icon`, `Spinner`,
 * `StatCard`, `inputClass` and `useAsync` are all shared and all imported from there.
 * This folder holds only what that file genuinely does not export, and it says so rather
 * than quietly reimplementing a primitive somebody else owns.
 *
 * Four rules every component here obeys, because they are the accessibility floor rather
 * than styling:
 *
 * - **No status is conveyed by colour alone.** Every badge renders its status as text. A
 *   badge whose only difference is its colour tells a screen-reader user nothing.
 * - **Every icon sits beside a text label** or carries an `aria-label`. An icon alone is
 *   never the only label.
 * - **Every control is at least 44px tall** (`min-h-11`), because it is a target somebody
 *   has to hit with a finger.
 * - **No emoji as an icon.** Every glyph here is an SVG path.
 */

import { Badge, Icon, inputClass } from '@/components/ui'

import { bookLabel } from './api'

/**
 * The nav glyph: a price tag with a check mark, which is a price book that has been
 * assigned rather than one that is merely offered.
 *
 * Passed as `iconPath` rather than as an `icon` name, because the shared `PATHS` map is
 * not ours to edit and this glyph is not in it.
 */
export const PRICE_BOOK_ICON = 'M3 6h18l-2 12H5L3 6zm6 3h6M12 9v3'

/**
 * A short block of text that carries a tone: a refusal, an explanation, a quote from
 * the research, a recorded reading. Not an error: `ErrorNote` is the error, and it is
 * used instead of this wherever the request actually failed.
 */
export function Notice({ tone = 'neutral', title, children }) {
  const tones = {
    neutral: 'border-border-subtle bg-muted text-foreground',
    info: 'border-border-subtle bg-accent-soft text-foreground',
    warning: 'border-warning/40 bg-warning/10 text-foreground',
    danger: 'border-destructive/40 bg-destructive/10 text-foreground',
    success: 'border-success/40 bg-success/10 text-foreground',
  }
  return (
    <div className={`rounded-sm border p-4 ${tones[tone] || tones.neutral}`}>
      {title && <p className="text-sm font-semibold">{title}</p>}
      {children && <div className="mt-1 text-sm text-muted-foreground">{children}</div>}
    </div>
  )
}

/**
 * One reason, as text.
 *
 * The tone is a second signal, never the only one. The label is always rendered, so a
 * reason is readable with no colour perception at all, and the code is rendered in mono
 * beside it so the log and the panel can be read against each other.
 */
export function ReasonBadge({ reason, vocabulary, writes }) {
  const label = (vocabulary?.assignments || []).find((row) => row.reason === reason)?.label
  const tone =
    writes === true
      ? 'success'
      : reason === 'needs_choice'
        ? 'warning'
        : 'neutral'
  return (
    <Badge tone={tone}>
      <span className="font-mono">{reason || 'unknown'}</span>
      {label && <span className="ml-1 font-sans">{label}</span>}
    </Badge>
  )
}

/**
 * One deal state, as text.
 *
 * Same rule as the reason badge: the words are the signal, the colour is the second one.
 */
export function BookStateBadge({ state, vocabulary }) {
  const label = (vocabulary?.book_states || []).find((row) => row.state === state)?.label
  const tone = state === 'assigned' ? 'success' : state === 'needs_choice' ? 'warning' : 'neutral'
  return (
    <Badge tone={tone}>
      <span className="font-mono">{state || 'unassigned'}</span>
      {label && <span className="ml-1 font-sans">{label}</span>}
    </Badge>
  )
}

/**
 * One filter from the reviewed rules panel.
 *
 * Shows the property, the operator in words, what the filter compared against and what
 * it actually read. A panel that says "no rule matched" without saying which of six
 * filters missed is the same as no panel, and this is the research's own right panel:
 * "review the matching deals in the right panel".
 */
export function FilterRow({ filter }) {
  const matched = filter?.matched === true
  return (
    <div className="flex flex-col gap-1 border-b border-border-subtle py-2 last:border-b-0">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={matched ? 'success' : 'neutral'}>
          <span className="font-sans">{matched ? 'Matched' : 'Did not match'}</span>
        </Badge>
        <span className="font-mono text-xs text-foreground">
          {filter?.object}.{filter?.property}
        </span>
      </div>
      <p className="text-xs text-muted-foreground">
        Operator <span className="font-mono">{filter?.operator}</span>. Compared with{' '}
        <span className="font-mono">{JSON.stringify(filter?.expected)}</span>. Read{' '}
        <span className="font-mono">
          {filter?.present ? JSON.stringify(filter?.actual) : 'nothing, the property is absent'}
        </span>
        {filter?.reason ? `. ${filter.reason}` : ''}
      </p>
    </div>
  )
}

/**
 * One rule's row in the reviewed list, with what it read and whether it matched.
 *
 * The switches are shown as words rather than as bare booleans, because "enabled: true"
 * says nothing to a seller and "active, and it assigns on a match" says what will happen
 * to the next deal.
 */
export function ReviewedRule({ entry }) {
  const state = entry?.matched
    ? entry?.enabled
      ? entry?.auto_assign
        ? 'Matched, and it will assign the price book'
        : 'Matched, but Auto-assigned is off, so nothing is written'
      : 'Matched, but the price book is inactive, so nothing is written'
    : 'Did not match this deal'

  return (
    <div className="flex flex-col gap-2 border-b border-border-subtle py-3 last:border-b-0">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-foreground">{entry?.rule_label}</p>
          <p className="text-xs text-muted-foreground">
            Assigns <span className="font-mono">{bookLabel(entry?.price_book)}</span>
          </p>
        </div>
        <Badge tone={entry?.matched ? 'success' : 'neutral'}>
          <span className="font-sans">{state}</span>
        </Badge>
      </div>
      {(entry?.filters || []).map((filter, index) => (
        <FilterRow key={index} filter={filter} />
      ))}
    </div>
  )
}

/**
 * A filter row in the rule builder.
 *
 * The property is a free-text dotted path rather than a picker of names this repository
 * does not declare. That is deliberate: the flow picks one from the object's property
 * list at runtime, and a team adding a property needs no change here.
 */
export function BuilderFilter({ index, filter, objects, operators, onChange, onRemove, disabled }) {
  const id = `wf088-filter-${index}`
  const numeric = (operators || []).some((row) => row.operator === filter.operator)
  return (
    <div className="flex flex-col gap-2 rounded-sm border border-border-subtle bg-muted p-3">
      <div className="grid gap-2 sm:grid-cols-3">
        <div className="flex flex-col gap-1">
          <label htmlFor={`${id}-object`} className="text-[13px] font-medium text-foreground">
            Filtering on
          </label>
          <select
            id={`${id}-object`}
            className={inputClass}
            value={filter.object}
            disabled={disabled}
            onChange={(event) => onChange(index, { object: event.target.value })}
          >
            {(objects || []).map((row) => (
              <option key={row.object} value={row.object}>
                {row.object}
              </option>
            ))}
          </select>
        </div>

        <div className="flex flex-col gap-1">
          <label htmlFor={`${id}-property`} className="text-[13px] font-medium text-foreground">
            Property
          </label>
          <input
            id={`${id}-property`}
            className={inputClass}
            value={filter.property}
            disabled={disabled}
            placeholder="segment, amount, industry, territory"
            onChange={(event) => onChange(index, { property: event.target.value })}
          />
        </div>

        <div className="flex flex-col gap-1">
          <label htmlFor={`${id}-operator`} className="text-[13px] font-medium text-foreground">
            Operator
          </label>
          <select
            id={`${id}-operator`}
            className={inputClass}
            value={filter.operator}
            disabled={disabled}
            onChange={(event) => onChange(index, { operator: event.target.value })}
          >
            {(operators || []).map((row) => (
              <option key={row.operator} value={row.operator}>
                {row.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="flex flex-col gap-1">
        <label htmlFor={`${id}-value`} className="text-[13px] font-medium text-foreground">
          Value
        </label>
        <input
          id={`${id}-value`}
          className={inputClass}
          value={Array.isArray(filter.value) ? filter.value.join(', ') : (filter.value ?? '')}
          disabled={disabled}
          inputMode={numeric ? 'numeric' : 'text'}
          placeholder={filter.operator === 'in' || filter.operator === 'not_in' ? 'a, b, c' : '25'}
          onChange={(event) => onChange(index, { value: event.target.value })}
        />
        <p className="text-xs text-muted-foreground">
          A property is a dotted path into the record's own JSON, so a team adds one
          without changing this page.
        </p>
      </div>

      <div>
        <button
          type="button"
          className="inline-flex min-h-11 items-center gap-2 rounded-sm border border-border-subtle px-3 text-sm text-foreground disabled:opacity-50"
          onClick={() => onRemove(index)}
          disabled={disabled}
        >
          <Icon name="audit" size={16} />
          Remove this filter
        </button>
      </div>
    </div>
  )
}

/**
 * One open reading, shown as prose with its audit beside it.
 *
 * The research left eight joints open and this build took a reading on each. A seller
 * meeting "nothing was assigned, choose one" needs the evidence next to that message,
 * not in a document they have to go and find. The audit id is rendered so the decision
 * is traceable rather than merely asserted.
 */
export function ReadingPanel({ reading, title }) {
  if (!reading) return null
  return (
    <div className="flex flex-col gap-2 rounded-sm border border-border-subtle bg-muted p-3">
      {title && <p className="text-sm font-semibold text-foreground">{title}</p>}
      {reading.sourced?.map((sentence) => (
        <p key={sentence} className="text-xs italic text-muted-foreground">
          {sentence}
        </p>
      ))}
      <p className="text-sm text-foreground">{reading.why}</p>
      {reading.jev_audit_id && (
        <p className="text-xs text-muted-foreground">
          Decided by Jev, audit <span className="font-mono">{reading.jev_audit_id}</span>,
          confidence {reading.jev_confidence}. The rejected alternative: {reading.rejected}
        </p>
      )}
    </div>
  )
}

/**
 * The confirmation shown before a destructive override.
 *
 * The sourced sentence is destructive: "If the price book is changed, any line items
 * associated with the previous price book will be removed". A page that offers that
 * without saying so is asking a seller to lose pricing work by clicking a dropdown, so
 * the count and the sentence are both on screen before the button that does it.
 */
export function DestructiveWarning({ removed, quote }) {
  if (!removed?.length) return null
  return (
    <Notice tone="danger" title="This change removes line items">
      {quote}
      <span className="ml-1 font-mono">{removed.length}</span> line item
      {removed.length === 1 ? '' : 's'} will be removed.
    </Notice>
  )
}