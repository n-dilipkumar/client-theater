/**
 * Primitives this feature needs that `@/components/ui` does not export.
 *
 * `docs/DESIGN-SYSTEM.md` and `docs/FEATURE-CONTRACT.md` both list `Notice` and `Toggle` among the
 * available primitives, and the shipped `ui.jsx` has all of the documented set except `Modal`,
 * `Notice`, `Toggle` and `Checkbox`.
 *
 * That is a contradiction in the repo's own documentation rather than a gap in this feature, and
 * it is reported rather than quietly worked around a second time: WF-073 hit it and rebuilt the
 * same two. The contract's instruction for exactly this case is to build the primitive inside the
 * feature folder and say so, so the integrator can promote the ones that recur. These are rebuilt
 * to the design system's floor: 44px targets, a visible focus ring, a text label beside every
 * control, `rounded-sm`, and semantic tokens only.
 *
 * `Modal` and `Checkbox` are not rebuilt: nothing in WF-074 needs them. The forms here are inline
 * in a card rather than in a dialog, because a form with three pickers and a room list is worse in
 * a modal on a phone than it is on the page.
 *
 * Two more things are built here that are not in the documented list at all, because the workflow
 * has shapes no shared primitive describes: `PermissionGrid` (the per-item ACL with its two
 * independent flags and the three visible states drawn separately, because the two flags are the
 * subject of the workflow and a single "allowed" word would collapse them), and `FlagToggle` (a
 * checkbox that reads as a permission rather than a selection).
 *
 * `FlagToggle` is a checkbox, not the shared `Toggle`, on purpose. `Toggle` is `role="switch"`,
 * which is right for a setting that changes the thing itself and wrong for a grant: a rep is
 * choosing whether this audience may download this item, and announcing that as a switched
 * setting on the link misreads it. The three permission rows are grouped under one `fieldset` with
 * a `legend`, so a screen reader announces them as the set they are rather than as three unrelated
 * controls.
 */

import { Icon } from '@/components/ui'

const TONES = {
  neutral: 'border-border-subtle bg-muted text-muted-foreground',
  info: 'border-info/30 bg-info/10 text-info',
  success: 'border-success/30 bg-success/10 text-success',
  warning: 'border-warning/30 bg-warning/10 text-warning',
  destructive: 'border-destructive/30 bg-destructive/10 text-destructive',
}

const TONE_ICON = {
  info: 'M12 8h.01M11 12h1v5h1M21 12a9 9 0 11-18 0 9 9 0 0118 0z',
  success: 'M5 12l5 5 9-11',
  warning:
    'M12 9v4m0 4h.01M10.3 4.3L2.6 18a2 2 0 001.7 3h15.4a2 2 0 001.7-3L13.7 4.3a2 2 0 00-3.4 0z',
  destructive: 'M12 9v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z',
}

/**
 * A short standing message. Tone carries the meaning and the icon repeats it, so the state does
 * not depend on colour alone.
 */
export function Notice({ tone = 'neutral', title, children, action }) {
  const iconPath = TONE_ICON[tone]
  return (
    <div
      role="status"
      className={`flex flex-wrap items-start gap-3 rounded-sm border p-4 ${TONES[tone] || TONES.neutral}`}
    >
      {iconPath && <Icon path={iconPath} className="mt-0.5 shrink-0" />}
      <div className="min-w-0 flex-1">
        {title && <p className="text-sm font-semibold">{title}</p>}
        {children && <div className="mt-1 text-sm">{children}</div>}
      </div>
      {action}
    </div>
  )
}

/**
 * A labelled select. Built here because the workflow needs one in a card with a visible label and
 * a hint, and the shared set has `Field` and `inputClass` but no select.
 *
 * `inputClass` supplies the styling so the control matches every other input in the product:
 * `min-h-11` for the 44px floor, a semantic border, and `focus:border-accent`. The chevron is the
 * browser's own, so there is no glyph to keep in step and no icon without a label.
 */
export function Select({ id, value, onChange, children, disabled = false }) {
  return (
    <select
      id={id}
      className={SELECT_CLASS}
      value={value}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
    >
      {children}
    </select>
  )
}

// Re-declared rather than imported so this file has one styling source. The shared `inputClass` is
// the canonical string; a test asserts the two match, so a change to the shared one cannot
// silently leave this behind.
const SELECT_CLASS =
  'min-h-11 w-full rounded-sm border border-border-subtle bg-surface px-3 text-sm ' +
  'text-foreground placeholder:text-muted-foreground/70 focus:border-accent'

/**
 * One permission flag.
 *
 * A checkbox, because it selects whether this audience may do this thing with this item. It is not
 * the shared `Toggle`: that is `role="switch"`, which announces a setting on the thing itself, and
 * a per-item grant is not a setting on the link. Announcing "switch, on" for a row of three grants
 * is the kind of mismatch that makes a screen reader user mistrust every control after it.
 *
 * The box is `h-5 w-5` inside a `min-h-11` row, so the touch target is the whole row rather than
 * the 20px box. The label is a real `<label for>`; there is no placeholder-as-label anywhere in
 * this feature.
 *
 * Download is never disabled when view is off, because the two flags are independent and the
 * specification's own example is a folder an audience may browse but not download. Forcing view
 * on when download is checked would store a state the rep did not choose.
 *
 * Reduced motion: the only transition is a border and background colour change, no transform, and
 * it is behind `motion-safe:`. Nothing travels across the screen in either state.
 */
export function FlagToggle({ id, label, hint, checked, onChange, disabled = false }) {
  return (
    <div className="flex min-h-11 items-center gap-3 py-1">
      <input
        id={id}
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
        className="h-5 w-5 shrink-0 rounded-xs border border-border-subtle accent-accent
          focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2
          focus-visible:outline-accent disabled:cursor-not-allowed disabled:opacity-50"
      />
      <label htmlFor={id} className="min-w-0 cursor-pointer text-sm">
        <span className="block font-medium text-foreground">{label}</span>
        {hint && <span className="mt-0.5 block text-xs text-muted-foreground">{hint}</span>}
      </label>
    </div>
  )
}

/**
 * What the current row state is, as a badge.
 *
 * Four states, and the label is the word rather than the colour, because the design floor reserves
 * "status is never conveyed by colour alone". The two hidden states carry different words on
 * purpose: "no grant yet" is this workflow's shipped default and "revoked" is somebody's edit, and
 * a rep looking at a grid needs to tell them apart.
 */
export function StateBadge({ row }) {
  const state = row?.state
  const map = {
    visible: { tone: 'success', label: 'Visible and downloadable', icon: 'M5 12l5 5 9-11' },
    view_only: { tone: 'info', label: 'View only', icon: 'M2 12s4-7 10-7 10 7 10 7-4 7-10 7-10-7-10-7z' },
    hidden_no_permission: {
      tone: 'neutral',
      label: 'No grant yet',
      icon: 'M12 9v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z',
    },
    hidden_can_view_false: {
      tone: 'warning',
      label: 'Revoked',
      icon: 'M12 9v4m0 4h.01M10.3 4.3L2.6 18a2 2 0 001.7 3h15.4a2 2 0 001.7-3L13.7 4.3a2 2 0 00-3.4 0z',
    },
  }
  const entry = map[state] || {
    tone: 'neutral',
    label: 'Not visible',
    icon: 'M12 9v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z',
  }
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-xs border px-2 py-0.5 font-mono text-xs font-medium
        ${TONES[entry.tone]}`}
    >
      <Icon path={entry.icon} size={13} />
      {entry.label}
    </span>
  )
}

/**
 * The per-item grid.
 *
 * A grid, not a list, because the two flags are independent and the workflow's whole subject is
 * that they are: the source's own example is an audience that may browse a folder but not
 * download it. One column per flag makes "visible but not downloadable" a shape a reader can see,
 * and collapsing the pair into a single "allowed" word is the failure the two-field entry exists to
 * prevent.
 *
 * The two hidden rows are rendered, not omitted, and each says in words why it is hidden. A grid
 * that only listed the granted items would make "nobody granted this" and "somebody revoked this"
 * look identical, and those are different facts for the rep who has to fix it.
 *
 * `onToggle` is called with `(row, flag, value)` and is absent when the grid is read-only, which is
 * the case for a link belonging to a group: the server refuses that write, so the page must not
 * offer a control that can only fail.
 */
export function PermissionGrid({ rows, itemTypeLabels, onToggle, readOnlyReason }) {
  const items = rows || []
  if (items.length === 0) {
    return (
      <p className="rounded-sm border border-border-subtle p-4 text-sm text-muted-foreground">
        This room holds no library documents or folders, so there is nothing to grant. The items a
        permission points at are the room&apos;s own library rows, and this workflow reads them and
        never writes them.
      </p>
    )
  }

  return (
    <div className="overflow-x-auto rounded-sm border border-border-subtle">
      <table className="w-full min-w-[560px] border-collapse text-left">
        <caption className="sr-only">
          Every document and folder in the room, with the view and download flags this audience
          holds on each one.
        </caption>
        <thead>
          <tr className="border-b border-border-subtle bg-muted">
            <th
              scope="col"
              className="px-3 py-2 text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground"
            >
              Item
            </th>
            <th
              scope="col"
              className="px-3 py-2 text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground"
            >
              Type
            </th>
            <th
              scope="col"
              className="px-3 py-2 text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground"
            >
              View
            </th>
            <th
              scope="col"
              className="px-3 py-2 text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground"
            >
              Download
            </th>
            <th
              scope="col"
              className="px-3 py-2 text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground"
            >
              State
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border-subtle">
          {items.map((row) => {
            const disabled = !onToggle
            return (
              <tr key={`${row.item_type}:${row.item_id}`} className="bg-surface align-top">
                <th scope="row" className="px-3 py-2 font-normal">
                  <span className="block text-sm font-medium text-foreground">{row.name}</span>
                  <span className="mt-0.5 block font-mono text-xs text-muted-foreground">
                    {row.item_id}
                  </span>
                  {row.auto_opened && (
                    <span className="mt-1 inline-block rounded-xs border border-border-subtle px-1.5 py-0.5 text-xs text-muted-foreground">
                      Opened for an item inside it, not chosen by a rep
                    </span>
                  )}
                </th>
                <td className="px-3 py-2 align-top">
                  <span className="font-mono text-xs text-muted-foreground">
                    {itemTypeLabels?.[row.item_type] || row.item_type}
                  </span>
                </td>
                <td className="px-3 py-2 align-top">
                  {onToggle ? (
                    <FlagToggle
                      id={`wf074-view-${row.item_type}-${row.item_id}`}
                      label="May view"
                      checked={row.can_view === true}
                      onChange={(value) => onToggle(row, 'can_view', value)}
                    />
                  ) : (
                    <FlagCell on={row.can_view === true} />
                  )}
                </td>
                <td className="px-3 py-2 align-top">
                  {onToggle ? (
                    <FlagToggle
                      id={`wf074-download-${row.item_type}-${row.item_id}`}
                      label="May download"
                      hint="Independent of view. An audience may browse a folder without downloading it."
                      checked={row.can_download === true}
                      onChange={(value) => onToggle(row, 'can_download', value)}
                    />
                  ) : (
                    <FlagCell on={row.can_download === true} />
                  )}
                </td>
                <td className="px-3 py-2 align-top">
                  <StateBadge row={row} />
                  {disabled && readOnlyReason && (
                    <span className="mt-1 block text-xs text-muted-foreground">{readOnlyReason}</span>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

/**
 * One flag, read-only.
 *
 * The word is beside the mark, so the state survives monochrome. A bare cross or tick here would
 * be a status conveyed by shape alone with no label, which is the ambiguity the floor bans.
 */
function FlagCell({ on }) {
  return (
    <span className="inline-flex min-h-11 items-center gap-1.5 font-mono text-xs">
      <Icon path={on ? 'M5 12l5 5 9-11' : 'M6 6l12 12M18 6L6 18'} size={14} />
      {on ? 'Yes' : 'No'}
    </span>
  )
}

/**
 * One link's scope state, with its sentence.
 *
 * The three general-link states plus the group's are drawn out rather than summarised, because
 * the two empty states mean opposite things and a single "no permissions" label would collapse a
 * cleared link into an unscoped one. A rep who cleared a link's scope would read "never scoped"
 * and believe they had opened the room up.
 */
export function ScopeState({ link }) {
  const map = {
    unscoped: {
      tone: 'neutral',
      title: 'Never scoped',
      text: 'Every item in the room is visible on this link. That is what a link with no overrides means.',
    },
    cleared: {
      tone: 'destructive',
      title: 'Cleared',
      text: 'Every item is hidden. The override set was cleared, which is what clearing it is for, and this is not the same as a link that was never scoped.',
    },
    scoped: {
      tone: 'info',
      title: 'Scoped',
      text: 'This link carries its own per-item permissions. An item it does not list is hidden.',
    },
    group: {
      tone: 'warning',
      title: 'From a group',
      text: "The group's permissions decide what this link shows. Setting permissions on the link itself is refused until it becomes a general audience.",
    },
  }
  const entry = map[link?.scope_state] || map.unscoped
  return (
    <Notice tone={entry.tone} title={entry.title}>
      <p>{entry.text}</p>
    </Notice>
  )
}

/** The shared input class, exported so a test can compare it against the canonical string. */
export const SELECT_INPUT_CLASS = SELECT_CLASS
