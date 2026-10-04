/**
 * Glyphs for WF-073, as SVG path data.
 *
 * `components/ui.jsx` is shared and must not be edited to add an icon: a hundred
 * features each appending to `PATHS` is the collision the plugin host exists to
 * prevent. `Icon` accepts a `path` prop instead, and the descriptor carries
 * `iconPath` so the sidebar can draw the same mark.
 *
 * All of these are drawn on the same 24x24 grid, with `stroke` and no fill, to match
 * the shared set's weight.
 */

/** An eye with a band across it: the focus band, which is the workflow's central idea. */
export const CONFIDENTIAL_ICON = 'M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12z M12 14a2 2 0 100-4 2 2 0 000 4z M4 4l16 16'

/** A frame with one sharp stripe: a page where only a band resolves. */
export const BAND_ICON = 'M4 4h16v16H4V4z M4 9h16 M4 13h16 M4 17h16'