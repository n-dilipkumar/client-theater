/**
 * Glyphs this feature needs that the shared `PATHS` map in
 * `components/ui.jsx` does not carry.
 *
 * The contract is explicit: a feature that needs a new glyph passes a `path`,
 * because that map is shared and a hundred features editing it is exactly the
 * conflict the feature host exists to prevent. Each is a stroke on the shared
 * 24x24 grid so it lines up with the ones already in the set.
 */

/** A column: the table a saved view is. */
export const TABLE_ICON = 'M3 5h18v14H3V5zm0 5h18M9 10v9'

/** A filter funnel, for the filter builder. */
export const FILTER_ICON = 'M3 5h18l-7 8v6l-4 2v-8L3 5z'

/** A column being moved, for "edit and rearrange columns". */
export const REORDER_ICON = 'M4 8h10M4 12h7M4 16h4M17 6v12m0 0l-3-3m3 3l3-3'

/** A shared view, for public versus private. */
export const SHARED_ICON = 'M8 12a3 3 0 100-6 3 3 0 000 6zm8-1a2.5 2.5 0 100-5 2.5 2.5 0 000 5zM2 19a6 6 0 0112 0m1-6a5 5 0 019 0'

/** A duplicated view, for Clone. */
export const CLONE_ICON = 'M9 9h11v11H9V9zM4 15V4h11'

/** A judgment call, for the inferences list. */
export const INFERENCE_ICON = 'M12 3a6 6 0 00-3.5 10.9V17h7v-3.1A6 6 0 0012 3zM9.5 20h5'

/** A disconnected CRM: the provider gate, made visible. */
export const UNPLUGGED_ICON = 'M8 8V6a4 4 0 018 0v2m-9 4v6h10v-6'

/** A completed action, for the confirmation note. */
export const CHECK_ICON = 'M4 13l5 5L20 7'

const GLYPHS = {
  table: TABLE_ICON,
  filter: FILTER_ICON,
  reorder: REORDER_ICON,
  shared: SHARED_ICON,
  clone: CLONE_ICON,
  inference: INFERENCE_ICON,
  unplugged: UNPLUGGED_ICON,
  check: CHECK_ICON,
}

/** A glyph by feature-local name, falling back to the shared schema glyph. */
export default function Glyph({ name, size = 16, className = '' }) {
  const path = GLYPHS[name] || GLYPHS.table
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
    >
      <path d={path} />
    </svg>
  )
}
