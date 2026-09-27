import CrmWorkflows from './CrmWorkflows'
import { WORKFLOW_ICON } from './primitives'

/**
 * CRM workflows, fired by DSR activity (WF-030).
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in App.jsx, main.jsx, or
 * lib/features.js learns this feature's name, and no shared file has to be
 * touched to add or change it. That is the whole point: the twelve original
 * workflow branches each appended to a hard-coded ROUTES array, and all twelve
 * conflicted.
 *
 * `id` matches `FEATURE["id"]` in
 * `backend/dsr/features/wf030_fire_crm_workflows_off_dsr_activity.py` exactly, so
 * the two halves of the feature are findable by one name.
 *
 * The nav glyph is passed as `iconPath` rather than as an `icon` name: "a workflow
 * firing continuously off activity" is not in the shared `PATHS` map, and that
 * file is not ours to edit. `icon` carries the closest shared name as a fallback
 * for any consumer that reads only that field; the nav itself renders `iconPath`.
 *
 * `order: 300` sorts this feature after the eleven already on main, whose orders
 * run 40 to 270, so the twelfth lands at the end of the group rather than
 * reshuffling everybody else's position.
 */
export default {
  id: 'wf-030-fire-crm-workflows-off-dsr-activity',
  label: 'CRM workflows',
  icon: 'schema',
  iconPath: WORKFLOW_ICON,
  order: 300,
  Component: CrmWorkflows,
}
