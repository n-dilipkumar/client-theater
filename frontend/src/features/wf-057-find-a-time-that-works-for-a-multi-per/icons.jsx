/**
 * Glyphs for the find-a-time page (WF-057).
 *
 * The shared `components/ui.jsx` `Icon` takes a `path`, so a feature can draw a
 * glyph the shared `PATHS` map does not carry without editing that file. It
 * cannot: it is shared, and a hundred features editing it is exactly the conflict
 * the feature host exists to prevent.
 *
 * Every glyph is a 24x24 single-path outline, so it strokes at the same weight
 * as the shared set. None is an emoji, and every control that uses one carries a
 * text label beside it - the icons are decorative and the label is the meaning.
 */

import { Icon } from '@/components/ui'

/** Several calendars with a shared slot - the workflow's subject. */
export const PANEL_ICON =
  'M8 3v3m8-3v3M4 8h16M5 6h14a1 1 0 011 1v13a1 1 0 01-1 1H5a1 1 0 01-1-1V7a1 1 0 011-1zm4 6h6v3H9v-3z'

/** A confidence badge: a gauge with a needle. */
export const CONFIDENCE_ICON =
  'M12 14l4-4M4.5 18a9 9 0 1115 0M12 18h.01'

/** A clock, for the candidate slot grid. */
export const SLOT_ICON = 'M12 7v5l3 2M21 12a9 9 0 11-18 0 9 9 0 0118 0z'

/** A distribution list expanding into people - the researched group expansion. */
export const EXPAND_ICON =
  'M4 5h6M4 5v6M20 19h-6M20 19v-6M14 8a3 3 0 100-6 3 3 0 000 6zm-9 4a3 3 0 100-6 3 3 0 000 6zm0 8a3 3 0 100-6 3 3 0 000 6zm9 4a3 3 0 100-6 3 3 0 000 6z'

/** A person whose availability is known and clear - the researched 100% case. */
export const FREE_ICON =
  'M12 11a3 3 0 100-6 3 3 0 000 6zm-7 9a7 7 0 0114 0M8.5 12.5l2.5 2.5 4.5-5'

/** A person whose availability is unknown - the researched 49% case. */
export const UNKNOWN_ICON =
  'M12 11a3 3 0 100-6 3 3 0 000 6zm-7 9a7 7 0 0114 0M16.5 4.5a3 3 0 010 5.8M18 14a7 7 0 013 6'

/** A busy person - the researched 0% case. */
export const BUSY_ICON =
  'M12 11a3 3 0 100-6 3 3 0 000 6zm-7 9a7 7 0 0114 0M4 4l16 16'

/** The empty-suggestions reason: a door with nothing behind it. */
export const EMPTY_ICON = 'M14 4h5v16h-5M14 4l-4 3v10l4 3M10 12h.01'

/** The re-call: the researched automation, drawn as a loop. */
export const RECALL_ICON =
  'M4 4v6h6M20 20v-6h-6M20 9a8 8 0 00-14.3-3M4 15a8 8 0 0014.3 3'

/** The commit: an event created on the organizer's calendar. */
export const COMMIT_ICON =
  'M12 5v14M5 12h14M8 3v4m8-4v4M8 17v4m8-4v4M3 8h4m-4 8h4m10-8h4m-4 8h4'

/** A house rule: something applied on top of the calendars' own availability. */
export const RULE_ICON = 'M4 6h16M4 12h10M4 18h7M17 15l3 3-3 3'

/** A room resource, as a third kind of calendar. */
export const ROOM_ICON = 'M3 21V8l9-5 9 5v13M9 21v-6h6v6M7 11h2m6 0h2'

const PATHS = {
  panel: PANEL_ICON,
  confidence: CONFIDENCE_ICON,
  slot: SLOT_ICON,
  expand: EXPAND_ICON,
  free: FREE_ICON,
  unknown: UNKNOWN_ICON,
  busy: BUSY_ICON,
  empty: EMPTY_ICON,
  recall: RECALL_ICON,
  commit: COMMIT_ICON,
  rule: RULE_ICON,
  room: ROOM_ICON,
}

export default function Glyph({ name, size = 18, className = '' }) {
  return <Icon path={PATHS[name] || PANEL_ICON} size={size} className={className} />
}
