/**
 * Icon paths this feature needs that the shared `PATHS` map does not carry.
 *
 * The contract's answer to "I need a glyph that isn't in the set" is to pass a path
 * rather than append to `components/ui.jsx`: that file is shared, and a hundred features
 * each appending to it is the exact collision the feature host exists to prevent. This
 * module is the feature's copy of that idea, so the glyphs are declared once and named
 * rather than inlined at each use.
 *
 * The glyphs already in the shared set (`plus`, `trash`, `refresh`, `chevron`, `search`)
 * are not duplicated here; this page uses those by name.
 *
 * If a second feature needs one of these, the integrator can promote it into `PATHS` once
 * as platform work, rather than each feature keeping a copy.
 */

/** The report's mark: a pipeline of three stages with a rising bar over it. */
export const PIPELINE_ICON =
  'M3 20h18M5 17V9m5 8V5m5 12v-6m5 6V7'

/** A buyer: a person with a view indicator. */
export const BUYER_ICON =
  'M16 20v-1.5a4 4 0 00-4-4H7a4 4 0 00-4 4V20M9.5 10.5a3.5 3.5 0 100-7 3.5 3.5 0 000 7zM17 8l2 2 3.5-3.5'

/** The coverage warning: a triangle with an exclamation. */
export const WARNING_ICON = 'M12 4l9 16H3l9-16zm0 6v4m0 3h.01'

/** An eye, for the views counter. */
export const EYE_ICON = 'M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7zm10 3a3 3 0 100-6 3 3 0 000 6z'

/** A pointer, for the actions counter. */
export const ACTION_ICON = 'M6 3l12 9-6 1.5L9.5 19 6 3z'

/** A shield-with-a-tick, for a workspace that is correctly in the report. */
export const IN_SCOPE_ICON = 'M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6l7-3zm-3 9l2 2 4-4'

/** A circle-slash, for a workspace that is excluded. */
export const OUT_OF_SCOPE_ICON = 'M12 3a9 9 0 100 18 9 9 0 000-18zM5.6 5.6l12.8 12.8'

export const ICONS = {
  pipeline: PIPELINE_ICON,
  buyer: BUYER_ICON,
  warning: WARNING_ICON,
  eye: EYE_ICON,
  action: ACTION_ICON,
  inScope: IN_SCOPE_ICON,
  outOfScope: OUT_OF_SCOPE_ICON,
  info: 'M12 8h.01M11 12h1v5h1M12 3a9 9 0 100 18 9 9 0 000-18z',
  calendar: 'M4 6h16v14H4zM4 10h16M8 3v4M16 3v4',
  filter: 'M3 5h18l-7 8v6l-4 2v-8L3 5z',
}
