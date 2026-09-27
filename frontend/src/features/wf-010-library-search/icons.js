/**
 * Icon paths this feature needs that the shared `PATHS` map does not carry.
 *
 * The branch appended `library`, `filter`, `bookmark`, `check`, `tray` and `clock`
 * to `components/ui.jsx`. That file is shared, so twelve features each appending
 * to it is the same collision the plugin host removes. The contract's answer is to
 * pass a path instead: `<Icon path="..." />` in the page, and `iconPath` in the
 * descriptor. This module is the feature's copy of that idea, so the glyphs are
 * declared once and named rather than inlined at every use.
 *
 * If a second feature needs one of these, the integrator can promote it into
 * `PATHS` once as platform work, rather than each feature keeping a copy. That is
 * raised in this port's report.
 *
 * Glyphs the shared map already has - `search`, `plus`, `trash`, `close`, `chevron`,
 * `refresh` - are used by name and are deliberately not duplicated here.
 */

export const ICONS = {
  library: 'M4 19.5A2.5 2.5 0 016.5 17H20M6.5 2H20v20H6.5A2.5 2.5 0 014 19.5v-15A2.5 2.5 0 016.5 2z',
  filter: 'M22 3H2l8 9.46V19l4 2v-8.54L22 3z',
  bookmark: 'M19 21l-7-5-7 5V5a2 2 0 012-2h10a2 2 0 012 2v16z',
  tray: 'M3 13h4l2 3h6l2-3h4M3 13l3-9h12l3 9v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6z',
  clock: 'M12 22a10 10 0 100-20 10 10 0 000 20zm0-16v6l4 2',
}

export const LIBRARY_ICON = ICONS.library
