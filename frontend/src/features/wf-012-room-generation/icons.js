/**
 * Glyphs this page needs that `components/ui.jsx` does not carry (WF-012).
 *
 * `ui.jsx` is shared: a hundred features appending to its `PATHS` map is the
 * same collision the plugin host exists to prevent. The contract's answer is
 * that a feature passes `path` instead, so these are declared here and used
 * with `<Icon path={...} />`, and the nav glyph is declared as `iconPath` on
 * the feature descriptor. If the integrator promotes any of these into the
 * shared set, the local copies become redundant and can be deleted.
 *
 * Each control using one of these still carries a visible text label, so the
 * glyph is decoration rather than the only carrier of meaning.
 */

export const TEMPLATE =
  'M4 4h16v4H4V4zm0 8h7v8H4v-8zm11 0h5v8h-5v-8zM7 6h.01M7 14h.01M17 14h.01'

export const GENERATE = 'M13 2L4.5 13.5H11l-1 8.5 8.5-11.5H12l1-8.5z'

export const EYE = 'M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7zm10 3a3 3 0 100-6 3 3 0 000 6z'

export const SEND = 'M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z'

export const WARNING =
  'M12 9v4m0 4h.01M10.3 3.9L1.8 18a2 2 0 001.7 3h17a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z'

export const CLOCK = 'M12 8v4l3 2m6-2a9 9 0 11-18 0 9 9 0 0118 0z'
