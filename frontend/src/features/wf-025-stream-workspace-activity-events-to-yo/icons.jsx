/**
 * The event stream's nav glyph.
 *
 * Passed as a path rather than added to the shared `PATHS` map in
 * `components/ui.jsx`, which is a shared file a hundred features would collide
 * on. See the feature contract: a new icon goes through `path`, never through an
 * edit to a shared map.
 *
 * The drawing: a box with a signed arrow leaving it. Three strokes, no fill, on
 * the same 24x24 grid and the same 1.8 stroke as every other icon in the set, so
 * it sits with its neighbours rather than beside them.
 */
export const STREAM_ICON =
  'M4 5h6v6H4V5zm0 9h6v5H4v-5zm10-9h6v3h-6V5zm0 6h3v3h-3v-3zm0 6h6v3h-6v-3zM20 14v3h-3v-3h3z'
