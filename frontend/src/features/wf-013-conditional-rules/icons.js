/**
 * Icon paths this feature needs that the shared `PATHS` map does not carry.
 *
 * The branch appended `rule`, `eye`, `eyeOff`, `branch` and `check` to
 * `components/ui.jsx`. That file is shared, so a hundred features each appending
 * to it is the same collision the plugin host removes. The contract's answer is
 * to pass a path instead: `<Icon path="..." />`, and `iconPath` in the
 * descriptor. This module is the feature's copy of that idea, so the glyphs are
 * declared once and named rather than inlined at each use.
 *
 * The glyphs already in the shared set (`plus`, `trash`, `refresh`, `close`) are
 * not duplicated here; this page uses those by name.
 *
 * If a second feature needs the same glyph, the integrator can promote it into
 * `PATHS` once as platform work rather than each feature keeping a copy.
 */

export const ICONS = {
  rule: 'M4 6h6M4 6a2 2 0 100 4 2 2 0 000-4zm0 4v8m0 0a2 2 0 100 4 2 2 0 000-4zm0-4h4m10 8h2m-2 0a2 2 0 100 4 2 2 0 000-4zm2 0V10m0 0a2 2 0 100-4 2 2 0 000 4zm-2 4h-4',
  eye: 'M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7zm10 3a3 3 0 100-6 3 3 0 000 6z',
  check: 'M4 12l5 5L20 6',
  library: 'M4 4h5v16H4zM11 4h4v16h-4zM17.5 4.5l3 15-3.4.7-3-15z',
  info: 'M12 8h.01M11 12h1v5h1M12 3a9 9 0 100 18 9 9 0 000-18z',
  warning: 'M12 4l9 16H3l9-16zm0 6v4m0 3h.01',
}

export const RULE_ICON = ICONS.rule
