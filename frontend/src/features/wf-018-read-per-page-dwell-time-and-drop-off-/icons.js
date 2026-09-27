/**
 * Glyphs for WF-018, drawn here rather than added to the shared `PATHS` map.
 *
 * `components/ui.jsx` is shared by every feature in the product, so a hundred
 * agents each appending one path to one object is the exact conflict the plugin
 * host exists to prevent. `Icon` accepts a `path` prop for this reason, and
 * `index.jsx` passes the same string as `iconPath` for the nav.
 *
 * Every glyph is a 24x24 stroke path, matching the shared set's geometry, so
 * the nav and the page do not look like two different icon families.
 */

/** A stack of pages with the top one part-read: the workflow in one mark. */
export const PAGE_STACK_ICON =
  'M7 3h7l5 5v13a1 1 0 01-1 1H7a1 1 0 01-1-1V4a1 1 0 011-1zm7 0v5h5M9 13h7M9 17h5'

/** A falling line: the drop-off curve. */
export const DROP_OFF_ICON = 'M3 5l5 5 4-3 4 6 5-9'

/** A clock over a bar: time spent per page. */
export const DWELL_ICON = 'M12 7v5l3 2M4 20h16M6 20V13h3v7M11 20V9h3v11M16 20v-4h3v4'

/** A play triangle in a frame: the self-hosted video, and its watch time. */
export const WATCH_ICON = 'M4 5h16a1 1 0 011 1v12a1 1 0 01-1 1H4a1 1 0 01-1-1V6a1 1 0 011-1zm7 4l5 3-5 3V9z'
