/**
 * Icon paths this feature needs that the shared `PATHS` map does not carry.
 *
 * The branch added `check`, `back` and `steps` to `components/ui.jsx`.
 * That file is shared, so a hundred features each appending to it is the exact
 * collision the plugin host removes, and CI refuses a diff that touches it. The
 * contract's answer is to pass a path instead - `<Icon path="..." />` and
 * `iconPath` in the descriptor - so the glyphs are declared once and named here
 * rather than inlined at each use.
 *
 * The glyphs the shared set already carries (`plus`, `refresh`, `close`,
 * `chevron`, `search`, `rooms`) are not duplicated; callers use those by name.
 *
 * `check` and `back` in particular look like shared candidates, because a wizard
 * is not a local idea. If a second port needs them, the integrator can promote
 * them into `PATHS` once as platform work rather than each feature keeping a
 * copy - that is what the promotion note in this directory is for.
 */

export const ICONS = {
  /** Confirm / done. Paired with a text label everywhere it is used. */
  check: 'M20 6L9 17l-5-5',
  /** Step backwards through the wizard. */
  back: 'M19 12H5m6-7l-7 7 7 7',
  /** A three-step wizard. The nav glyph for this feature. */
  wizard: 'M4 6h4v4H4V6zm0 8h4v4H4v-4zm8-8h8v4h-8V6zm0 8h8v4h-8v-4zM8 8h4M8 16h4',
}

/** The nav glyph for the feature descriptor, which takes a path rather than a name. */
export const WIZARD_ICON = ICONS.wizard
