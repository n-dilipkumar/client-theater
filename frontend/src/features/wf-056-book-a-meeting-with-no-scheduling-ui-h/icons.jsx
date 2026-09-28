/**
 * Glyphs for the headless booking page (WF-056).
 *
 * Drawn here rather than appended to the shared `PATHS` map in
 * `components/ui.jsx`, which is a shared file this feature may not edit. Each is
 * a plain SVG path string passed to `<Icon path=... />`, so nothing is
 * registered anywhere - the same escape hatch the nav glyph uses.
 *
 * No emoji anywhere: the design system forbids it, and a credential screen is
 * the worst possible place for one.
 */

/**
 * Two calls joined by a bracket: the two-step flow, which is the whole point.
 *
 * Deliberately not a calendar glyph. A calendar reads as "pick a date", which is
 * the *scheduling UI* this workflow exists to remove - the page's subject is
 * the absence of that.
 */
export const HEADLESS_ICON =
  'M4 7h5l2 5h3l2-5h4M4 17h6M15 17h5M6 4h4M11 4h4'

/** A key, for the scoped credential. */
export const TOKEN_ICON = 'M14 7a4 4 0 100 8 4 4 0 000-8zM11 10H7v3M7 13v3'

/** A clock face, for a session and its TTL. */
export const SESSION_ICON = 'M12 21a9 9 0 100-18 9 9 0 000 18zm0-14v5l3 2'

/** A calendar grid, for the bookable assets. */
export const ASSET_ICON = 'M4 6h16v15H4V6zm0 5h16M8 3v5M16 3v5'

/** A tick inside a circle, for a committed meeting. */
export const BOOKED_ICON = 'M12 21a9 9 0 100-18 9 9 0 000 18zm-3.5-9l2.5 2.5 4.5-4.5'

/** A cross inside a circle, for a refused call. */
export const REFUSED_ICON = 'M12 21a9 9 0 100-18 9 9 0 000 18zM9 9l6 6M15 9l-6 6'

/** A barred clock, for a spent or expired session. */
export const SPENT_ICON = 'M12 21a9 9 0 100-18 9 9 0 000 18zm0-14v5l3 2M4 4l16 16'

/** An envelope, for the recorded-but-not-transmitted invite. */
export const INVITE_ICON = 'M3 6h18v12H3V6zm0 0l9 7 9-7'

/** A bolt, for the immediate webhook emission. */
export const WEBHOOK_ICON = 'M13 3L5 14h6l-1 7 8-11h-6l1-7z'

/** A page with code, for the researched `Custom API` instructions. */
export const INSTRUCTIONS_ICON = 'M6 3h8l4 4v14H6V3zm8 0v5h4M9 12h6M9 16h4'

/** A question mark, for the judgement calls this build makes. */
export const INFERENCE_ICON = 'M12 18h.01M9.5 9a2.5 2.5 0 114 2c-1 .7-1.5 1-1.5 2M12 21a9 9 0 100-18 9 9 0 000 18z'

export const GLYPHS = {
  headless: HEADLESS_ICON,
  token: TOKEN_ICON,
  session: SESSION_ICON,
  asset: ASSET_ICON,
  booked: BOOKED_ICON,
  refused: REFUSED_ICON,
  spent: SPENT_ICON,
  invite: INVITE_ICON,
  webhook: WEBHOOK_ICON,
  instructions: INSTRUCTIONS_ICON,
  inference: INFERENCE_ICON,
}

export default GLYPHS
