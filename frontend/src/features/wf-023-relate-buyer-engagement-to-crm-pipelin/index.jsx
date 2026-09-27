import SalesImpact from './SalesImpact'
import { PIPELINE_ICON } from './icons'

/**
 * WF-023, registered.
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in App.jsx, main.jsx, or
 * lib/features.js learns this feature's name, and no shared file has to be touched to add
 * or change it. That is the whole point: the twelve original workflow branches each
 * appended to a hard-coded `ROUTES` array in App.jsx, and all twelve conflicted.
 *
 * `id` matches `FEATURE["id"]` in
 * `backend/dsr/features/wf023_relate_buyer_engagement_to_crm_pipelin.py` exactly, which is
 * what lets the two halves of a feature be found by one name.
 *
 * The glyph is a pipeline of stages with a rising bar over it, which is not in the shared
 * `PATHS` map, so `iconPath` carries the path and `icon` falls back to the shared `schema`
 * mark for any consumer that reads only that field. `components/ui.jsx` is not edited.
 */
export default {
  id: 'wf-023-relate-buyer-engagement-to-crm-pipelin',
  label: 'Sales impact',
  icon: 'schema',
  iconPath: PIPELINE_ICON,
  order: 230,
  Component: SalesImpact,
}
