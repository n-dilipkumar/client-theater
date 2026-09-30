/**
 * Glyphs for the bookable calendar page (WF-058).
 *
 * Drawn here rather than appended to the shared `PATHS` map in
 * `components/ui.jsx`, which is a shared file this feature may not edit. Each is
 * a plain SVG path string passed to `<Icon path=... />`, so nothing is registered
 * anywhere - the same escape hatch the nav glyph uses.
 */

/**
 * A calendar with a clock hand on one day: the embed, in one shape. Distinct
 * enough from the shared `rooms` glyph, which is a layered document.
 */
export const CALENDAR_ICON =
  'M4 8h16M7 3v3m10-3v3M5 6h14a1 1 0 011 1v13a1 1 0 01-1 1H5a1 1 0 01-1-1V7a1 1 0 011-1zm4 8h2m3 0h2m-7 4h2m3 0h2'

/** A hand on a clock face: the five-minute hold. */
export const HOLD_ICON = 'M12 7v5l3 2M12 3a9 9 0 100 18 9 9 0 000-18z'

/** A check in a circle: a booking taken. */
export const BOOKED_ICON = 'M9 12l2 2 4-4M12 3a9 9 0 100 18 9 9 0 000-18z'

/** Two arrows converging on one date: a reschedule. */
export const RESCHEDULE_ICON = 'M4 9h11l-3-3M20 15H9l3 3'

/** A bolt: an instant booking, which is team events only. */
export const INSTANT_ICON = 'M13 3L5 14h6l-1 7 8-11h-6l1-7z'

/** A repeated arrow: a recurring booking. */
export const RECURRING_ICON = 'M4 9a5 5 0 015-5h9m0 0l-3-3m3 3l-3 3M20 15a5 5 0 01-5 5H6m0 0l3 3m-3-3l3-3'

/** A person plus a plus: several people, one meeting. A seated event. */
export const SEATED_ICON = 'M9 7a2 2 0 100 4 2 2 0 000-4zm6 2v6m3-3h-6M5 20v-1a3 3 0 013-3h2a3 3 0 013 3v1'

/** A funnel: routing an answer to an event type. */
export const ROUTING_ICON = 'M3 5h18l-7 8v6l-4 2v-8L3 5z'

/** A camera: one of the four conference providers, and a video link. */
export const VIDEO_ICON = 'M3 8h11a1 1 0 011 1v6a1 1 0 01-1 1H3a1 1 0 01-1-1V9a1 1 0 011-1zm12 4l6-3v8l-6-3'

/** A question in a circle: what this build inferred rather than read. */
export const INFERENCE_ICON =
  'M12 8a2.5 2.5 0 113 2.5c-.8.5-1 1-1 2M12 16h.01M12 3a9 9 0 100 18 9 9 0 000-18z'

/** A key: the OAuth client, step 1. */
export const OAUTH_ICON = 'M14 8a4 4 0 11-3.9 5H8v3H5v-3l-1-1 3-3h1.1A4 4 0 0114 8z'

/** A calendar grid: the four documented slot selectors. */
export const GRID_ICON = 'M4 6h16M4 12h16M4 18h16M9 6v12M15 6v12'

export const GLYPHS = {
  calendar: CALENDAR_ICON,
  hold: HOLD_ICON,
  booked: BOOKED_ICON,
  reschedule: RESCHEDULE_ICON,
  instant: INSTANT_ICON,
  recurring: RECURRING_ICON,
  seated: SEATED_ICON,
  routing: ROUTING_ICON,
  video: VIDEO_ICON,
  inference: INFERENCE_ICON,
  oauth: OAUTH_ICON,
  grid: GRID_ICON,
}

export default GLYPHS
