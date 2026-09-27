/**
 * Icon paths this feature needs that the shared `PATHS` map does not carry.
 *
 * The branch appended `cloud`, `link`, `snapshot`, `alert` and `info` to
 * `components/ui.jsx`. That file is shared, and twelve features each appending
 * to it is the same collision the plugin host removes. The contract's answer is
 * to pass a path instead: `<Icon path="..." />` and `iconPath` in the
 * descriptor. This module is the feature's copy of that idea, so the glyphs are
 * declared once and named rather than inlined at every use.
 *
 * One limitation follows from that and is worth knowing: the shared `Button`
 * takes an icon *name* and looks it up in `PATHS`, so it cannot be handed one
 * of these. Buttons on this page therefore either use a glyph the shared set
 * already has (`plus`, `close`, `refresh`) or show text alone, and the glyphs
 * below are used with `<Icon path=... />` directly.
 *
 * If a second feature needs the same glyph, the integrator can promote it into
 * `PATHS` once, as platform work, rather than each feature keeping a copy.
 */

export const ICONS = {
  cloud: 'M7 18a4 4 0 01-.5-7.97A5.5 5.5 0 0117.4 9.2 3.9 3.9 0 0117 18H7z',
  link: 'M10 13a5 5 0 007.07 0l2-2A5 5 0 1012.5 4.5l-1 1m2.5 4.5a5 5 0 00-7.07 0l-2 2A5 5 0 1011.5 19.5l1-1',
  alert: 'M12 9v4m0 4h.01M10.3 3.9L1.8 18a2 2 0 001.7 3h17a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z',
}

/** The glyph this feature is filed under in the nav. */
export const EXTERNAL_SYNC_ICON = ICONS.cloud
