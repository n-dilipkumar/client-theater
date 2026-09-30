import IntentCompanies from './IntentCompanies'
import { COMPANY_ICON } from './primitives'

/**
 * In-market companies, auto-added and tracked (WF-033).
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in App.jsx, main.jsx, or
 * lib/features.js learns this feature's name, and no shared file has to be
 * touched to add or change it. That is the whole point: the twelve original
 * workflow branches each appended to a hard-coded ROUTES array, and all twelve
 * conflicted.
 *
 * `id` matches `FEATURE["id"]` in
 * `backend/dsr/features/wf033_auto_add_and_continuously_track_in_mar.py` exactly,
 * so the two halves of the feature are findable by one name.
 *
 * The nav glyph is passed as `iconPath` rather than as an `icon` name: "a company
 * found in the market" is not in the shared `PATHS` map, and that file is not ours
 * to edit. `icon` carries the closest shared name as a fallback for any consumer
 * that reads only that field; the nav itself renders `iconPath`.
 */
export default {
  id: 'wf-033-auto-add-and-continuously-track-in-mar',
  label: 'Intent companies',
  icon: 'search',
  iconPath: COMPANY_ICON,
  order: 330,
  Component: IntentCompanies,
}
