/**
 * Icon paths this feature needs that the shared `PATHS` map does not carry.
 *
 * The branch appended `library`, `upload`, `download`, `folder`, `image`,
 * `clock`, `layers` and `info` to `components/ui.jsx`. That file is shared, so
 * twelve features each appending to it is the same collision the plugin host
 * removes. The contract's answer is to pass a path instead: `<Icon path="..." />`
 * and `iconPath` in the descriptor. This module is the feature's copy of that
 * idea, so the glyphs are declared once and named rather than inlined at each
 * use.
 *
 * If a second feature needs the same glyph, the integrator can promote it into
 * `PATHS` once as platform work rather than each feature keeping a copy.
 */

export const ICONS = {
  library: 'M4 5h6v14H4V5zm7 0h6v14h-6V5zm7 0h2v14h-2V5z',
  upload: 'M12 16V4m0 0L8 8m4-4l4 4M4 17v2a1 1 0 001 1h14a1 1 0 001-1v-2',
  download: 'M12 4v12m0 0l-4-4m4 4l4-4M4 17v2a1 1 0 001 1h14a1 1 0 001-1v-2',
  folder: 'M3 7a1 1 0 011-1h5l2 2h8a1 1 0 011 1v9a1 1 0 01-1 1H4a1 1 0 01-1-1V7z',
  image: 'M4 5h16v14H4V5zm0 10l4-4 4 4 3-3 5 5M15 9h.01',
  clock: 'M12 7v5l3 2m6-2a9 9 0 11-18 0 9 9 0 0118 0z',
  layers: 'M12 3l9 5-9 5-9-5 9-5zm9 9l-9 5-9-5m18 4l-9 5-9-5',
  info: 'M12 11v6m0-9h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z',
}

/** The glyph the nav shows for this feature. */
export const LIBRARY_ICON = ICONS.library
