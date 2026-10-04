import MapEsignature from './MapEsignature'
import { MAP_SIGNATURE_ICON } from './primitives'

/**
 * Mutual action plan e-signature (WF-067).
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in App.jsx, main.jsx, or
 * lib/features.js learns this feature's name, and no shared file has to be
 * touched to add or change it. That is the whole point: the twelve original
 * workflow branches each appended to a hard-coded ROUTES array, and all twelve
 * conflicted.
 *
 * `id` matches `FEATURE["id"]` in
 * `backend/dsr/features/wf067_send_a_mutual_action_plan_for_e_signature.py`
 * exactly, so the two halves of the feature are findable by one name.
 *
 * The nav glyph is passed as `iconPath` rather than as an `icon` name: a document
 * with a pen across it and a check in the corner - a plan that has been signed -
 * is not in the shared `PATHS` map, and that file is not ours to edit. `icon`
 * carries the closest shared name as a fallback for any consumer that reads only
 * that field; the nav itself renders `iconPath`.
 */
export default {
  id: 'wf-067-send-a-mutual-action-plan-for-e-signature',
  label: 'MAP signatures',
  icon: 'audit',
  iconPath: MAP_SIGNATURE_ICON,
  order: 670,
  Component: MapEsignature,
}