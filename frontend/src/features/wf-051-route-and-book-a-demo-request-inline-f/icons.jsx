/**
 * WF-051 glyphs.
 *
 * `src/components/ui.jsx` is on the shared-file list, so a glyph that is not
 * already in it is passed with `<Icon path="..." />` and declared with
 * `iconPath` in the descriptor. All of them are single-path, 24x24, and
 * stroke-based at the same weight as the shared set, so a row of them does not
 * look assembled from two different icon families.
 */

/** A calendar with a route arrow through it: book without the email thread. */
export const ROUTER_ICON =
  'M4 8h16M8 3v4m8-4v4M5 6h14a1 1 0 011 1v13a1 1 0 01-1 1H5a1 1 0 01-1-1V7a1 1 0 011-1zm4 9h2m3 0h2m-7 4h2m3 0h2'

/** A seller with a connected calendar. */
export const SELLER_ICON = 'M16 21v-2a4 4 0 00-4-4H6a4 4 0 00-4 4v2m7-10a4 4 0 100-8 4 4 0 000 8z'

/** The Time Elapsed timer: a clock on a session that is waiting out its deadline. */
export const TIMER_ICON = 'M12 22a10 10 0 100-20 10 10 0 000 20zm0-16v6l4 2'

export const GLYPHS = {
  router: ROUTER_ICON,
  seller: SELLER_ICON,
  timer: TIMER_ICON,
}

export default GLYPHS