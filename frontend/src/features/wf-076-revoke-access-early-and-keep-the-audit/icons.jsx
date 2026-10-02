/**
 * Glyphs for the revocation page (WF-076).
 *
 * Drawn here rather than appended to the shared `PATHS` map in
 * `components/ui.jsx`, which is a shared file this feature may not edit. Each is
 * a plain SVG path string passed to `<Icon path=... />`, so nothing is registered
 * anywhere - the same escape hatch the nav glyph uses.
 *
 * Every glyph sits beside a text label or an `aria-label` on the page; an icon
 * alone is never the only label, which is why the stat tiles here carry a
 * caption as well as a glyph.
 */

/**
 * A padlock with a struck shackle: revoke access. Distinct from the shared
 * `trash` glyph, which reads as removing a record - and the researched point is
 * that nothing is removed here.
 */
export const REVOKE_ICON =
  'M7 11V8a5 5 0 019.9-1M5 11h14v9H5v-9zm7 4v2M3 3l18 18'

/** A row kept behind a hairline: the retained audit row. */
export const RETAINED_ICON = 'M4 6h16M4 6v13a1 1 0 001 1h14a1 1 0 001-1V6M4 6l2-2h12l2 2M9 11h6'

/** An audience of three, one of them struck: a group and its members. */
export const GROUP_ICON =
  'M9 11a3 3 0 100-6 3 3 0 000 6zm-6 8a6 6 0 0112 0M17 11a3 3 0 100-6M3 3l18 18'

/** A single person: the underlying viewer, kept when the membership goes. */
export const VIEWER_ICON = 'M12 12a4 4 0 100-8 4 4 0 000 8zm-8 9a8 8 0 0116 0'

/** A page with an eye: the per-item view / download flags. */
export const PERMISSION_ICON =
  'M7 3h10a1 1 0 011 1v16a1 1 0 01-1 1H7a1 1 0 01-1-1V4a1 1 0 011-1zm2 5h6M9 12h6M9 16h4M2 12s3.6-6 10-6 10 6 10 6-3.6 6-10 6-10-6-10-6z'

/** Two stacked rooms with one detached: the attach / detach join rows. */
export const ATTACH_ICON =
  'M4 5h7v6H4V5zm9 8h7v6h-7v-6zM4 15h7M15 8V5M6 11l8 8'

/** A question mark over a page: the readings a reviewer can disagree with. */
export const INFERENCE_ICON =
  'M12 8a4 4 0 116 3c-1 .7-1 1-1 2M12 17h.01M7 3h10a1 1 0 011 1v16a1 1 0 01-1 1H7a1 1 0 01-1-1V4a1 1 0 011-1z'

/** A clock: the request-time guarantee, and the absence of any grace period. */
export const CLOCK_ICON = 'M12 21a9 9 0 100-18 9 9 0 000 18zm0-13v4l3 2'

/** A key: a slug, and whether it is still free to issue. */
export const SLUG_ICON =
  'M14 7a4 4 0 11-3.5 6L4 19.5V16h3.5v-3.5H11A4 4 0 0114 7zm1.5 2.5h.01'

/** A frozen dataroom: a grid held rigid. */
export const FROZEN_ICON =
  'M12 3v18M4 7l16 10M20 7L4 17M12 3l-8 4m8-4l8 4M12 21l-8-4m8 4l8-4M3 12h18'

/** A magnifying glass over a link: the resolution check. */
export const RESOLVE_ICON =
  'M11 18a7 7 0 100-14 7 7 0 000 14zm5.5-1.5L21 21M8 11h6'

export const GLYPHS = {
  revoke: REVOKE_ICON,
  retained: RETAINED_ICON,
  group: GROUP_ICON,
  viewer: VIEWER_ICON,
  permission: PERMISSION_ICON,
  attach: ATTACH_ICON,
  inference: INFERENCE_ICON,
  clock: CLOCK_ICON,
  slug: SLUG_ICON,
  frozen: FROZEN_ICON,
  resolve: RESOLVE_ICON,
}

export default GLYPHS