/**
 * Glyphs for WF-070, as SVG path data.
 *
 * `components/ui.jsx` is shared and must not be edited to add an icon - a hundred
 * features each appending to `PATHS` is the collision the plugin host exists to
 * prevent. `Icon` accepts a `path` prop instead, and the descriptor carries
 * `iconPath` so the sidebar can draw the same mark.
 *
 * All are drawn on the same 24x24 grid, with `stroke`, no fill, to match the shared
 * set's weight.
 */

/** A document with a tick: the NDA and the acceptance. The headline idea. */
export const NDA_ICON = 'M7 3h7l5 5v13H7V3zm7 0v5h5M10 13l2 2 4-4'

/** A seal: an agreement is bound. Distinct from the document, which is the text. */
export const SEAL_ICON = 'M12 3l2.5 2 3-.3.8 2.9 2.4 1.7-1.2 2.7 1.2 2.7-2.4 1.7-.8 2.9-3-.3L12 21l-2.5-2-3 .3-.8-2.9L3.3 14.7 4.5 12 3.3 9.3l2.4-1.7.8-2.9 3 .3L12 3z'

/** A gate, for the link that is not asking for anything. */
export const OPEN_ICON = 'M4 8h16v12H4V8zm4 12v-6h8v6M8 8V6a4 4 0 018 0v2'