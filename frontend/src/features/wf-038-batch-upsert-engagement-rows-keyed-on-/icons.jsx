/**
 * Glyphs for the batch-upsert page (WF-038).
 *
 * Drawn here rather than added to `components/ui.jsx`'s `PATHS` map, because that
 * file is shared and a hundred features editing it is the exact conflict the
 * feature host exists to prevent. The host renders a descriptor's `iconPath`
 * when it has one and falls back to `icon` when it does not, so these are
 * opt-in per feature.
 */

/** Two rows merging into one stack: an upsert keyed on an external id. */
export const UPSERT_ICON =
  'M4 7h7m0 0L8.5 4.5M11 7L8.5 9.5M4 7v10m0 0h16M4 17l3-3m-3 3 3 3M20 7h-4m0 0 2.5-2.5M16 7l2.5 2.5M20 7v10m0 0h-7m0 0 2.5-2.5M13 17l-2.5 2.5'

/** A queue draining into a batch: the chunked send. */
export const CHUNK_ICON =
  'M3 6h18M3 12h18M3 18h18M7 3v18M17 3v18'

/** A circle half filled: "unconfirmed" - sent, but nothing can say so. */
export const UNCONFIRMED_ICON =
  'M12 3a9 9 0 100 18 9 9 0 000-18zm0 0v18'

/** A warning triangle for the documented gap. */
export const GAP_ICON = 'M12 4l9 16H3l9-16zm0 6v4m0 3h.01'
