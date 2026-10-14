/**
 * WF-130's own primitives.
 *
 * Everything here is built from `@/components/ui`, and every control meets the
 * accessibility floor: a visible text label beside each icon, `min-h-11` on the
 * shared `Button`, and no status carried by colour alone.
 *
 * These are local because `ui.jsx` has no equivalent, and building them here is
 * the contract's instruction rather than an oversight. Promotion work for the
 * integrator, not a reason to edit a shared file:
 *
 *   - `InlineNote`, because `ui.jsx` has no `Notice` and this page needs a line
 *     that states a fact about what it did or could not do.
 *   - `StateBadge` and `KindBadge`, because a state and a kind are findings and
 *     the word beside the colour is what makes them findings.
 *   - `SectionList`, because the content blocks are a list of facts and a
 *     hidden block must say which stage reveals it.
 */

import { Badge, Icon } from '@/components/ui'

import { SECTION_KIND_LABELS, kindLabel, sectionVisibility, stateLabel, stateTone } from './api'

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

/** An artefact's state as a word plus a tone. The word is always present. */
export function StateBadge({ state }) {
  return <Badge tone={stateTone(state)}>{stateLabel(state)}</Badge>
}

/** An artefact's kind as a word plus a tone. */
export function KindBadge({ kind }) {
  return <Badge tone="neutral">{kindLabel(kind)}</Badge>
}

/** A section's kind as a word. */
export function SectionKindBadge({ kind }) {
  return <Badge tone="update">{SECTION_KIND_LABELS[kind] || kind || 'Section'}</Badge>
}

/**
 * The content blocks, read-only.
 *
 * A hidden block is not dropped from the list: it is shown with the stage that
 * reveals it, because "present but hidden" is a fact about the deal and the
 * seller must be able to see it. The visibility word is printed, so the state
 * never rests on the colour.
 */
export function SectionList({ sections }) {
  const rows = sections || []
  if (rows.length === 0) {
    return <p className="text-sm text-muted-foreground">This artefact has no sections yet.</p>
  }
  return (
    <ol className="flex flex-col gap-3" aria-label="Content blocks">
      {rows.map((section) => (
        <li
          key={section.key}
          className={`flex flex-col gap-1 rounded-sm border px-3 py-2 ${
            section.visible ? 'border-border-subtle bg-surface' : 'border-border-subtle bg-muted'
          }`}
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-sm font-medium text-foreground">{section.title}</span>
            <span className="flex flex-wrap items-center gap-2">
              <SectionKindBadge kind={section.kind} />
              <Badge tone={section.visible ? 'insert' : 'restore'}>
                {sectionVisibility(section)}
              </Badge>
            </span>
          </div>
          <p className="whitespace-pre-line text-sm text-muted-foreground">{section.body}</p>
          <p className="font-mono text-[11px] text-muted-foreground">
            {section.key}
            {section.synced ? ` | synced from ${section.asset_ref} v${section.asset_version}` : ''}
            {section.unresolved_tokens?.length
              ? ` | no value for ${section.unresolved_tokens.join(', ')}`
              : ''}
          </p>
        </li>
      ))}
    </ol>
  )
}

/** The surfaces the research names that this build does not do. */
export function NotBuiltList({ items }) {
  return (
    <ul className="list-inside list-disc text-xs text-muted-foreground">
      {(items || []).map((item) => (
        <li key={item}>{item}</li>
      ))}
    </ul>
  )
}
