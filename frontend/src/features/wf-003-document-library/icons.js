/**
 * Icon paths this feature needs that the shared `PATHS` map does not carry.
 *
 * The branch appended `documents`, `gallery`, `upload`, `external`, `user`,
 * `archive`, and `lock` to `components/ui.jsx`. That file is shared, so twelve
 * features each appending to it is the same collision the plugin host removes.
 * The contract's answer is to pass a path instead: `<Icon path="..." />` and
 * `iconPath` in the descriptor. This module is the feature's copy of that idea,
 * so the glyphs are declared once and named rather than inlined at each use.
 *
 * If a second feature needs the same glyph, the integrator can promote it into
 * `PATHS` once as platform work rather than each feature keeping a copy.
 */

export const ICONS = {
  documents: 'M7 3h7l5 5v13a1 1 0 01-1 1H7a1 1 0 01-1-1V4a1 1 0 011-1zm7 0v5h5M9 13h6M9 17h6',
  gallery: 'M3 5h7v7H3V5zm11 0h7v4h-7V5zM3 14h7v5H3v-5zm11-2h7v7h-7v-7z',
  upload: 'M12 16V4m-5 5l5-5 5 5M4 17v2a1 1 0 001 1h14a1 1 0 001-1v-2',
  external: 'M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 01-1 1H5a1 1 0 01-1-1V7a1 1 0 011-1h5',
  user: 'M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2M12 11a4 4 0 100-8 4 4 0 000 8z',
  archive: 'M3 7h18v3H3V7zm1 3h16v10a1 1 0 01-1 1H5a1 1 0 01-1-1V10zm5 4h6',
  lock: 'M7 11V8a5 5 0 0110 0v3M5 11h14v10H5V11z',
}

export const DOCUMENT_ICON = ICONS.documents
