/**
 * Icon paths this feature needs that the shared `PATHS` map does not carry.
 *
 * The contract's answer to a missing glyph is a path, not an edit:
 * `<Icon path="..." />` at the use, and `iconPath` in the descriptor. This module
 * is the feature's copy of that idea, so the glyphs are declared once and named
 * rather than inlined at each use.
 *
 * If a second feature needs one of these, the integrator promotes it into
 * `PATHS` once as platform work, rather than every feature keeping a copy.
 */

export const ICONS = {
  /** A ranked bar chart: content influence, which is what the page ranks. */
  influence: 'M4 20h16M7 20V11M12 20V5M17 20v-6',
  /** A page turning: the content-engagement trend. */
  trend: 'M3 17l5-6 4 4 4-6 5 5M3 20h18',
  /** A currency sign beside a document: revenue associated with content. */
  revenue: 'M7 3h7l4 4v14H7zM14 3v4h4M10.5 12h4M10.5 15h3',
  info: 'M12 8h.01M11 12h1v5h1M12 3a9 9 0 100 18 9 9 0 000-18z',
  warning: 'M12 4l9 16H3l9-16zm0 6v4m0 3h.01',
  link: 'M10 14a4 4 0 005.7 0l3-3a4 4 0 00-5.7-5.7L11.5 7M14 10a4 4 0 00-5.7 0l-3 3a4 4 0 005.7 5.7L12.5 17',
}

export const INFLUENCE_ICON = ICONS.influence
