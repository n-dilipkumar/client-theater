/**
 * Glyphs for WF-078, as SVG path data.
 *
 * `components/ui.jsx` is shared and must not be edited to add an icon: a hundred features
 * each appending to `PATHS` is the collision the plugin host exists to prevent. `Icon`
 * accepts a `path` prop instead, and the descriptor carries `iconPath` so the sidebar can
 * draw the same mark.
 *
 * All of these are drawn on the same 24x24 grid, with `stroke` and no fill, to match the
 * shared set's weight.
 */

/**
 * A shield with a keyhole. The workflow's subject: identity has to be proven before a
 * document opens, and the keyhole is the part that is checked rather than assumed.
 */
export const VERIFICATION_ICON =
  'M12 3l7 3v5c0 4.4-2.9 8.5-7 10-4.1-1.5-7-5.6-7-10V6l7-3z M12 11a2 2 0 100-4 2 2 0 000 4z M12 13v2'

/**
 * Two gates side by side on one line: a document and two locks at different points. The
 * two moments are the specification's central claim and the reason the object is keyed by
 * gate rather than carrying one method at one moment.
 */
export const TWO_AXIS_ICON = 'M4 5h16v14H4V5z M10 12h1 M13 12h1 M4 9h16 M10 12v2 M13 12v2'
