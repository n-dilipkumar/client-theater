/**
 * Glyphs this feature draws that are not in the shared `PATHS` map.
 *
 * `components/ui.jsx` is shared and a hundred features each appending to it is
 * the conflict the feature host exists to remove, so a feature that needs a new
 * glyph passes it as `path=` instead. The nav reads `iconPath`; `icon` on the
 * descriptor carries the closest shared name for any consumer that only reads
 * that field.
 */

/** A camera on a stand: a meeting that happens over video. */
export const VIDEO_ICON =
  'M4 7h9a2 2 0 012 2v6a2 2 0 01-2 2H4a2 2 0 01-2-2V9a2 2 0 012-2zm12 4l6-3v10l-6-3v-4z'

/** Two links, one moving to the other: the researched swap. */
export const SWAP_ICON =
  'M7 7h10l-3-3m3 3l-3 3M17 17H7l3 3m-3-3l3-3'

/** A plug: the Integrations tab, where connecting a provider is mandatory. */
export const PLUG_ICON =
  'M9 3v5m6-5v5M6 8h12v3a6 6 0 01-12 0V8zm6 9v4'

/** A warning triangle: a provision that failed, or a Location that cannot run. */
export const WARN_ICON = 'M12 3l9 16H3l9-16zm0 6v5m0 3v.5'

/** A clock: a booking still waiting on the guest to say where. */
export const WAIT_ICON = 'M12 21a9 9 0 100-18 9 9 0 000 18zm0-13v5l3 2'
