/**
 * Glyphs this feature needs that the shared `PATHS` map in
 * `components/ui.jsx` does not carry.
 *
 * The contract is explicit: a feature that needs a new glyph passes a `path`,
 * because that map is shared and a hundred features editing it is exactly the
 * conflict the feature host exists to prevent. Each is a stroke on the shared
 * 24x24 grid so it lines up with the ones already in the set.
 */

/** Three linked records: the bundle, which is a graph and not a list. */
export const BUNDLE_ICON = 'M12 4v4m0 0h-6a2 2 0 00-2 2v6m8-8h6a2 2 0 012 2v6M6 16v2a2 2 0 002 2h10a2 2 0 002-2v-2'

/** A transaction: everything in, everything out, or nothing out. */
export const ATOMIC_ICON = 'M4 7h16M4 7l3-3M4 7l3 3M20 17H4m16 0l-3-3m3 3l-3 3'

/** A record that was created and then undone. */
export const ROLLBACK_ICON = 'M4 10h11a5 5 0 010 10h-3M4 10l4-4M4 10l4 4'

/** A subrequest that never ran, because its input did not arrive. */
export const SKIPPED_ICON = 'M5 12h6m4 0h4M9 8l6 8m0-8l-6 8'

/** The ordering flag: the researched speed-for-ordering trade. */
export const ORDER_ICON = 'M4 6h10M4 12h7M4 18h4M18 8v9m0 0l-3-3m3 3l3-3'

/** The wire: the exact request that would go out. */
export const WIRE_ICON = 'M8 8a4 4 0 018 0m-9 4v6h10v-6M4 6h.01M20 18h.01'

/** A judgment call, for the inferences list. */
export const INFERENCE_ICON = 'M12 3a6 6 0 00-3.5 10.9V17h7v-3.1A6 6 0 0012 3zM9.5 20h5'

const GLYPHS = {
  bundle: BUNDLE_ICON,
  atomic: ATOMIC_ICON,
  rollback: ROLLBACK_ICON,
  skipped: SKIPPED_ICON,
  order: ORDER_ICON,
  wire: WIRE_ICON,
  inference: INFERENCE_ICON,
}

/** A glyph by feature-local name, falling back to the shared schema glyph. */
export default function Glyph({ name, size = 16, className = '' }) {
  const path = GLYPHS[name] || GLYPHS.bundle
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

/** Per-outcome tone, so a rolled-back row and a skipped row never look alike. */
export const OUTCOME_TONE = {
  created: 'insert',
  rolled_back: 'restore',
  skipped: 'neutral',
  failed: 'delete',
}
