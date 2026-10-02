/**
 * Glyphs for WF-069, as SVG path data.
 *
 * `components/ui.jsx` is shared and must not be edited to add an icon - a hundred
 * features each appending to `PATHS` is the collision the plugin host exists to
 * prevent. `Icon` accepts a `path` prop instead, and the descriptor carries
 * `iconPath` so the sidebar can draw the same mark.
 *
 * All three are drawn on the same 24x24 grid, with `stroke`, no fill, to match the
 * shared set's weight.
 */

/** A padlock: the gate's headline idea. */
export const GATE_ICON = 'M7 11V8a5 5 0 0110 0v3M5 11h14v10H5V11zm7 4v3'

/** A shield with a tick: the verified-buyer state. */
export const VERIFIED_ICON = 'M12 3l7 3v6c0 4-3 7-7 9-4-2-7-5-7-9V6l7-3zm-3 9l2 2 4-4'

/** A clock: expiry, and the countdown on a link that closes today. */
export const CLOCK_ICON = 'M12 7v5l3 2M21 12a9 9 0 11-18 0 9 9 0 0118 0z'

/** An envelope: the email step. */
export const MAIL_ICON = 'M3 6h18v12H3V6zm0 1l9 7 9-7'
