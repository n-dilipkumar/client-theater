/**
 * Icon paths this feature needs that the shared `PATHS` map does not carry.
 *
 * The branch appended `share`, `mail`, `pencil`, `users`, `warning`, `clock`,
 * `send`, `shield` and `inbox` to `components/ui.jsx`. That file is shared, so a
 * hundred features each appending to it is the same collision the plugin host
 * removes. The contract's answer is to pass a path instead: `<Icon path="..." />`,
 * and `iconPath` in the descriptor. This module is the feature's copy of that
 * idea, so the glyphs are declared once and named rather than inlined at each
 * use, and `PATHS` is not touched.
 *
 * The glyphs already in the shared set (`plus`, `trash`, `refresh`, `close`,
 * `chevron`, `schema`) are not duplicated here; callers use those by name.
 *
 * If a second feature needs the same glyph, the integrator can promote it into
 * `PATHS` once as platform work rather than each feature keeping a copy. Three
 * ports have now built a local set of their own, which is the signal for that
 * platform change.
 */

export const ICONS = {
  share: 'M18 8a3 3 0 100-6 3 3 0 000 6zM6 15a3 3 0 100-6 3 3 0 000 6zm12 7a3 3 0 100-6 3 3 0 000 6zM8.6 13.5l6.8 4M15.4 6.5l-6.8 4',
  mail: 'M3 7a2 2 0 012-2h14a2 2 0 012 2v10a2 2 0 01-2 2H5a2 2 0 01-2-2V7zm0 0l9 6 9-6',
  pencil: 'M12 20h9M16.5 3.5a2.1 2.1 0 013 3L7 19l-4 1 1-4L16.5 3.5z',
  users: 'M16 21v-2a4 4 0 00-4-4H6a4 4 0 00-4 4v2M9 11a4 4 0 100-8 4 4 0 000 8zm13 10v-2a4 4 0 00-3-3.87M16 3.13a4 4 0 010 7.75',
  warning: 'M12 9v4m0 4h.01M10.3 3.9L1.8 18a2 2 0 001.7 3h17a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z',
  clock: 'M12 21a9 9 0 100-18 9 9 0 000 18zm0-14v5l3 2',
  send: 'M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z',
  shield: 'M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z',
  inbox: 'M22 12h-6l-2 3h-4l-2-3H2M5.4 5.1L2 12v6a2 2 0 002 2h16a2 2 0 002-2v-6l-3.4-6.9A2 2 0 0016.8 4H7.2a2 2 0 00-1.8 1.1z',
  info: 'M12 8h.01M11 12h1v5h1M12 3a9 9 0 100 18 9 9 0 000-18z',
}

/** The nav glyph for the feature descriptor, which takes a path rather than a name. */
export const SHARE_ICON = ICONS.share
