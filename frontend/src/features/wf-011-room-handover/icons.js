/**
 * Glyphs this feature needs that `components/ui.jsx` does not carry (WF-011).
 *
 * The branch appended `share`, `copy`, `check`, `clock`, `lock`, `eye`,
 * `shield`, `bell` and `send` to the shared `PATHS` map. That file is shared, so
 * a hundred features each appending to it is the same collision the plugin host
 * exists to prevent. The contract's answer is to pass a path instead -
 * `<Icon path="..." />`, and `iconPath` on the descriptor - so these are declared
 * here once and used by name rather than inlined at each use.
 *
 * If a second feature needs the same glyph, the integrator can promote it into
 * `PATHS` once as platform work rather than each feature keeping a copy. The
 * shared set already carries `search`, `plus`, `refresh`, `close` and `schema`,
 * which is why those names are not repeated here.
 *
 * Every control that uses one of these still carries a visible text label, so
 * the glyph decorates rather than being the only carrier of meaning.
 */

export const SHARE =
  'M10 13a5 5 0 007.07 0l2.83-2.83a5 5 0 00-7.07-7.07L11.5 4.5M14 11a5 5 0 00-7.07 0l4.1 2.83a5 5 0 007.07 7.07l1.43-1.43'

export const COPY = 'M9 9V6a1 1 0 011-1h8a1 1 0 011 1v8a1 1 0 01-1 1h-3M6 9h8a1 1 0 011 1v8a1 1 0 01-1 1H6a1 1 0 01-1-1v-8a1 1 0 011-1z'

export const CHECK = 'M5 13l4 4L19 7'

export const CLOCK = 'M12 7.5V12l3 2M21 12a9 9 0 11-18 0 9 9 0 0118 0z'

export const LOCK = 'M7.5 11V8a4.5 4.5 0 019 0v3M5.5 11h13a1 1 0 011 1v7a1 1 0 01-1 1h-13a1 1 0 01-1-1v-7a1 1 0 011-1z'

export const EYE = 'M12 9a3 3 0 100 6 3 3 0 000-6zM2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z'

export const SHIELD =
  'M12 3.5l7.5 2.8v5.4c0 4.6-3.2 7.4-7.5 8.3-4.3-.9-7.5-3.7-7.5-8.3V6.3L12 3.5z'

export const BELL =
  'M15.5 8.5a3.5 3.5 0 10-7 0c0 4.5-2 5.5-2 5.5h11s-2-1-2-5.5M10 20a2.2 2.2 0 004 0'

/**
 * Named glyphs for the access markers on a room card, so the presentation
 * helper in `publish.js` can return a name rather than an SVG string.
 */
export const ACCESS_GLYPHS = {
  clock: CLOCK,
  eye: EYE,
  lock: LOCK,
  shield: SHIELD,
}
