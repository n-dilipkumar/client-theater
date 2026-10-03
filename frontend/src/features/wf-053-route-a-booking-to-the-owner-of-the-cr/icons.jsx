/**
 * Glyphs the shared `PATHS` map does not carry (WF-053).
 *
 * `Icon path=` exists precisely so a feature can draw a glyph it needs without
 * editing `components/ui.jsx`, which is shared by every feature and which this one
 * may not touch. Each glyph is decorative: the control beside it always carries a
 * text label, which is what the design floor requires and what keeps this page
 * readable in a screenshot printed in black and white.
 */
const GLYPHS = {
  /** A person with a branch off to the side: an owner, resolved from a record. */
  owner: 'M12 12a4 4 0 100-8 4 4 0 000 8zm-7 8a7 7 0 0114 0M19 5v6m0 0l-2-2m2 2l2-2',
  /** A calendar with a clock: availability read from a connected calendar. */
  availability: 'M7 2v3m10-3v3M3 9h18M5 5h14a2 2 0 012 2v12a2 2 0 01-2 2H5a2 2 0 01-2-2V7a2 2 0 012-2zm7 7v3l2 1',
  /** A funnel over a branch: a routing chain ending in a catch-all. */
  route: 'M3 4h18l-7 8v7l-4 2v-9L3 4z',
  /** A shield with a bar: the node guardrail. */
  guard: 'M12 2l8 3v6c0 5-3.5 9-8 11-4.5-2-8-6-8-11V5l8-3zM9 12l2 2 4-4',
  /** A bookmark on a meeting: a booked slot. */
  booking: 'M6 3h12a1 1 0 011 1v17l-7-4-7 4V4a1 1 0 011-1z',
}

/**
 * The glyph the nav renders, named as its own export so the descriptor reads
 * `iconPath: OWNER_ICON` rather than reaching into the map.
 */
export const OWNER_ICON = GLYPHS.owner

export default GLYPHS
