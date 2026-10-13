import Series from './Series'
import { NURTURE_ICON } from './primitives'

/**
 * Nurture series (WF-104).
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in App.jsx, main.jsx or
 * lib/features.js learns this feature's name, and no shared file has to be touched to add or
 * change it.
 *
 * `id` matches `FEATURE["id"]` in `backend/dsr/features/wf104_nurture_series.py` exactly, so the
 * two halves of the feature are findable by one name.
 *
 * The nav glyph is passed as `iconPath` rather than as an `icon` name: a sequence of blocks with
 * a branch is not in the shared `PATHS` map, and that file is not ours to edit. `icon` carries
 * the closest shared name as a fallback for any consumer that reads only that field.
 */
export default {
  id: 'wf-104-nurture-series',
  label: 'Nurture series',
  icon: 'rooms',
  iconPath: NURTURE_ICON,
  order: 160,
  Component: Series,
}
