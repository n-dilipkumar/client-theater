import GapReconcile from './GapReconcile'
import { GAP_ICON } from './primitives'

/**
 * The gap reconciliation ledger (WF-050).
 *
 * This file is the whole of the frontend registration. The host globs every
 * `index.jsx` under `src/features` at build time, so nothing in App.jsx,
 * main.jsx, or lib/features.js learns this feature's name, and no shared file has
 * to be touched to add or change it. That is the whole point: the first twelve
 * workflow branches each appended to a hard-coded ROUTES array, and all twelve
 * conflicted.
 *
 * `id` matches `FEATURE["id"]` in
 * `backend/dsr/features/wf050_reconcile_gaps_after_a_dropped_change_st.py`
 * exactly, so the two halves of the feature are findable by one name.
 *
 * The nav glyph is passed as `iconPath` rather than as an `icon` name: "a stream
 * with one broken link in it" is not in the shared `PATHS` map, and that file is
 * not ours to edit. `icon` carries the closest shared name as a fallback for any
 * consumer that reads only that field; the nav itself renders `iconPath`.
 */
export default {
  id: 'wf-050-reconcile-gaps-after-a-dropped-change-st',
  label: 'Gap reconciliation',
  icon: 'audit',
  iconPath: GAP_ICON,
  order: 500,
  Component: GapReconcile,
}