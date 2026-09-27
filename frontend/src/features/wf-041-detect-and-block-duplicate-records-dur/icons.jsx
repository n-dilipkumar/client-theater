/**
 * Glyphs for the duplicate guard page (WF-041).
 *
 * Drawn here rather than appended to the shared `PATHS` map in
 * `components/ui.jsx`, which is a shared file this feature may not edit. Each is
 * a plain SVG path string passed to `<Icon path=... />`, so nothing is
 * registered anywhere - the same escape hatch the nav glyph uses.
 */

/**
 * Two records converging: the "this already exists" shape. Distinct enough from
 * the shared `refresh` loop to read as a merge rather than a retry.
 */
export const DUPLICATE_ICON = 'M7 4a3 3 0 100 6 3 3 0 000-6zm10 10a3 3 0 100 6 3 3 0 000-6zM9 10v3a2 2 0 002 2h4M6 13h5'

/** A shield over a record: a write that was refused. */
export const BLOCKED_ICON = 'M12 3l7 3v5c0 4-3 8-7 10-4-2-7-6-7-10V6l7-3zm-3 8l2 2 4-4'

/** Two arrows onto one row: policy (b), update instead of create. */
export const UPDATE_ICON = 'M4 8h11l-3-3M20 16H9l3 3'

/** Two records plus one: policy (c), the duplicate created anyway. */
export const ALLOW_ICON = 'M9 7a2 2 0 100 4 2 2 0 000-4zm6 0a2 2 0 100 4 2 2 0 000-4zM12 14a2 2 0 100 4 2 2 0 000-4z'

/** A question over a record: the merge escalation, which claims no action. */
export const ESCALATE_ICON = 'M12 8a2 2 0 113 2c-1 .7-1 1-1 2M12 16h.01M10 4a2 2 0 012 0l7 3v5c0 4-3 8-7 10-4-2-7-6-7-10V7l7-3z'

/** A magnifier over stacked rows: the matcher registry. */
export const MATCH_ICON = 'M11 15a4 4 0 100-8 4 4 0 000 8zm3 1l5 5M9 3h6'

/** A page, for the rows a duplicate rule matches against. */
export const RECORD_ICON = 'M6 3h8l4 4v14H6V3zm8 0v5h4'

export const GLYPHS = {
  duplicate: DUPLICATE_ICON,
  blocked: BLOCKED_ICON,
  update: UPDATE_ICON,
  allow: ALLOW_ICON,
  escalate: ESCALATE_ICON,
  match: MATCH_ICON,
  record: RECORD_ICON,
}

export default GLYPHS
