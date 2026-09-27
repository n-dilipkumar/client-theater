/**
 * Icon paths this feature needs that the shared `PATHS` map does not carry.
 *
 * The branch appended `publish`, `schedule`, `check`, `folder`, `bell`,
 * `approve`, and `queue` to `components/ui.jsx`. That file is shared, so twelve
 * features each appending to it is the same collision the plugin host removes.
 * The contract's answer is to pass a path instead: `<Icon path="..." />` and
 * `iconPath` in the descriptor. This module is this feature's copy of that idea,
 * so the glyphs are declared once and named rather than inlined at each use.
 *
 * Only the four the page actually draws are carried over; `check`, `bell`,
 * `approve`, and `queue` were added by the branch and never used.
 *
 * The paths are the branch's, unchanged.
 *
 * If a second feature needs the same glyph, the integrator can promote it into
 * `PATHS` once as platform work rather than each feature keeping a copy.
 */

export const ICONS = {
  publish: 'M5 12l5 5L20 7M4 4h6M4 20h16',
  schedule: 'M12 8v4l3 2m6-2a9 9 0 11-18 0 9 9 0 0118 0z',
  folder: 'M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V7z',
}

export const PUBLISH_ICON = ICONS.publish
