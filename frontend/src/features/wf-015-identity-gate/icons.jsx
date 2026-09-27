/**
 * This feature's own glyphs.
 *
 * `components/ui.jsx` carries a `PATHS` map and a `path` prop, and the contract is
 * explicit: a feature that needs a glyph it does not have passes `path=` rather
 * than appending to `PATHS`. `PATHS` is shared, and a hundred features each
 * appending to it is the collision the plugin host exists to remove.
 *
 * None of these are promotion candidates either. They are this workflow's own
 * visual vocabulary - a shield for the gate, an envelope for the verification
 * message, a clock for expiry - and the contract already has the mechanism for
 * sharing one.
 */

export const ICONS = {
  /** The gate itself: the thing in front of the room. */
  shield: 'M12 3l8 3v6c0 4.5-3.2 8.3-8 9-4.8-.7-8-4.5-8-9V6l8-3z',
  /** The verification message, for the outbox. */
  mail: 'M3 6h18v12H3V6zm0 1l9 7 9-7',
  /** Confirmation: copied, verified. */
  check: 'M4 12l5 5L20 6',
  /** Refusal: not allowed through. */
  ban: 'M12 3a9 9 0 100 18 9 9 0 000-18zM5.6 5.6l12.8 12.8',
  /** The link a buyer is handed on a local install. */
  link: 'M10 13a5 5 0 007 0l3-3a5 5 0 00-7-7l-1 1m-2 8a5 5 0 01-7 0 5 5 0 010-7l3-3a5 5 0 017 0',
  /** Session expiry, which is a real failure mode and not an edge case. */
  clock: 'M12 3a9 9 0 100 18 9 9 0 000-18zm0 4v5l3 2',
  /** Copy affordance. */
  copy: 'M9 9h10v10H9V9zM5 15V5h10',
  /** "Use my existing account", the sourced alternative to typing. */
  inbox: 'M3 13h5l2 3h4l2-3h5M3 13l3-8h12l3 8v6H3v-6z',
}

/** The nav glyph for the whole workflow. */
export const ACCESS_ICON = ICONS.shield

/** Tone for a session status, matching the shared `Badge` vocabulary. */
export const STATUS_TONE = {
  verified: 'insert',
  identified: 'update',
  pending_verification: 'restore',
  refused: 'delete',
}
