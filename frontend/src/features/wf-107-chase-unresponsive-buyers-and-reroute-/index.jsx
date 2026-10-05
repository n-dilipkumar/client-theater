import Chase from './Chase'
import { CHASE_ICON } from './primitives'

/**
 * Chase and reroute (WF-107).
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in App.jsx, main.jsx or
 * lib/features.js learns this feature's name, and no shared file has to be touched to add
 * or change it. That is the whole point: the original workflow branches each appended to
 * a hard-coded ROUTES array, and all of them conflicted.
 *
 * `id` matches `FEATURE["id"]` in
 * `backend/dsr/features/wf107_chase_unresponsive_buyers_and_reroute_.py` exactly, so the
 * two halves of the feature are findable by one name.
 *
 * The nav glyph is passed as `iconPath` rather than as an `icon` name: a speech bubble
 * with a clock inside it is not in the shared `PATHS` map, and that file is not ours to
 * edit. `icon` carries the closest shared name as a fallback for any consumer that reads
 * only that field; the nav itself renders `iconPath`.
 */
export default {
  id: 'wf-107-chase-unresponsive-buyers-and-reroute-',
  label: 'Chase and reroute',
  icon: 'rooms',
  iconPath: CHASE_ICON,
  order: 170,
  Component: Chase,
}
