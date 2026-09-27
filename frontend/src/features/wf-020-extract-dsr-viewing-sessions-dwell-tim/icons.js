/**
 * Glyphs this feature owns.
 *
 * The shared `PATHS` map in `components/ui.jsx` is not edited: a hundred features
 * each appending to it is the conflict the feature host exists to prevent. These
 * are passed as `path` / `iconPath` instead, which `Icon` prefers.
 *
 * All are 24x24, stroke-only, `currentColor`, matching the shared set. Every one
 * that appears in a control sits beside a text label, so none of them is ever the
 * only thing a user has to go on.
 */

/** A globe: geography. */
export const GLOBE = 'M12 3a9 9 0 100 18 9 9 0 000-18zm0 0c-2.5 2.3-3.8 5.4-3.8 9s1.3 6.7 3.8 9m0-18c2.5 2.3 3.8 5.4 3.8 9s-1.3 6.7-3.8 9M3.5 9h17M3.5 15h17'

/** A clock over bars: dwell time. */
export const DWELL = 'M12 7v5l3 2M4 20h16M6.5 20V13M11 20V8M15.5 20v-4M20 20V11'

/** Two browser tabs: the documented row grain, one session per tab. */
export const TABS = 'M3 8h18M3 8a2 2 0 012-2h14a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V8zm5 0V5h8v3'

/** A circular arrow over a grid: the incremental sweep. */
export const SWEEP = 'M4 4v6h6M20 20v-6h-6M4.5 10a8 8 0 0113.7-3M19.5 14a8 8 0 01-13.7 3'

/** A page with an arrow leaving it: the flat-file load. */
export const EXPORT = 'M14 3v5h5M14 3H7a2 2 0 00-2 2v14a2 2 0 002 2h10a2 2 0 002-2V8l-5-5zm0 0v5h5M9 13h6m-6 4h6'

/** A link: the room join. */
export const JOIN = 'M10 13a5 5 0 007.5.5l2-2a5 5 0 00-7-7l-1 1m-1.5 8.5a5 5 0 01-7.5-.5l-2 2a5 5 0 007 7l1-1'

/** An exclamation in a triangle: a quality flag. */
export const FLAG = 'M12 4l9 16H3l9-16zm0 6v4m0 3v.5'

/** A person: a viewer. */
export const VIEWER = 'M12 12a4 4 0 100-8 4 4 0 000 8zm-8 8a8 8 0 0116 0'
