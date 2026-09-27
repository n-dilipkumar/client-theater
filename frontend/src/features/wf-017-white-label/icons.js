/**
 * Glyphs this feature needs that the shared `PATHS` map does not carry.
 *
 * The branch appended ten of them to `components/ui.jsx`. That file is shared, so
 * ten features each appending to it is the same collision the plugin host
 * removes. The contract's answer is to pass a path instead -- `<Icon path="..." />`
 * for a glyph in a body, `iconPath` in the descriptor for a glyph in the nav --
 * and this module is the feature's copy of that idea, so each glyph is declared
 * and named once rather than inlined at every use.
 *
 * The glyphs already in the shared set (`refresh`, `close`, `trash`, `database`)
 * are not duplicated here; this page uses those by name.
 *
 * None of these is a promotion candidate on its own. `check`, `alert` and
 * `clock` are generic enough that a second feature will want them, and when it
 * does the integrator should add them to `PATHS` once, as platform work, rather
 * than leaving two copies of the same mark in two feature folders.
 */

export const ICONS = {
  globe: 'M12 21a9 9 0 100-18 9 9 0 000 18zm0 0c2.5-2.4 3.8-5.5 3.8-9S14.5 5.4 12 3m0 18c-2.5-2.4-3.8-5.5-3.8-9S9.5 5.4 12 3M3.6 9h16.8M3.6 15h16.8',
  link: 'M10 13a5 5 0 007.5.5l3-3a5 5 0 00-7-7l-1.7 1.7M14 11a5 5 0 00-7.5-.5l-3 3a5 5 0 007 7l1.7-1.7',
  shield: 'M12 3l8 3v6c0 4.5-3.2 8.4-8 9.5-4.8-1.1-8-5-8-9.5V6l8-3z',
  copy: 'M9 9h10v12H9V9zM5 15V3h10v2',
  palette: 'M12 21a9 9 0 110-18c4.5 0 8 3 8 7 0 2.5-2 4-4 4h-2a2 2 0 00-1.5 3.3A1.8 1.8 0 0111 21h-1a9 9 0 01-8-9 9 9 0 018-9zM7.5 11.5h.01M10 7.5h.01M14.5 7.5h.01',
  check: 'M4 12.5l5 5L20 6.5',
  alert: 'M12 8v5m0 3h.01M10.3 3.9L2.5 17.5A2 2 0 004.2 20.5h15.6a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z',
  unlink: 'M17 7l3-3m-2 8.5l1.5-1.5a5 5 0 00-7-7L11 5.5M7 17l-3 3m2-8.5L4.5 13a5 5 0 007 7L13 18.5M3 3l18 18',
  clock: 'M12 21a9 9 0 100-18 9 9 0 000 18zm0-13.5V12l3.5 2',
  eye: 'M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7zm10 3a3 3 0 100-6 3 3 0 000 6z',
}

/** The nav glyph, and the one this feature is named for: a custom domain. */
export const WHITE_LABEL_ICON = ICONS.globe
