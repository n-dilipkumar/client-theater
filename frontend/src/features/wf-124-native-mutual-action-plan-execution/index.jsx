import MutualActionPlan from './ActionPlanBoard'

/**
 * WF-124, registered.
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in `App.jsx`, `main.jsx` or
 * `lib/features.js` learns this feature's name and no shared file has to be touched
 * to add or change it.
 *
 * `id` matches `FEATURE["id"]` in
 * `backend/dsr/features/wf124_native_mutual_action_plan_execution.py` exactly, so the
 * two halves of the feature are findable by one name.
 *
 * The component is the seller's board, and only the seller needs it in the sidebar.
 * The buyer's view is reached with the switch at the top of the page rather than at a
 * separate route, because the buyer and the seller are looking at the same plan and
 * the difference between their two views is which tasks are in the response. Giving
 * them two routes would mean the seller could not check what the client sees, which is
 * the one thing a seller most needs before they send a plan.
 *
 * A viewer arriving from a link has no route of their own in this product, so there
 * is no viewer hash to claim here. That is a deliberate absence rather than an
 * oversight: the research puts the plan inside the room, and the room's access is
 * WF-070's and WF-069's business, not this workflow's.
 */
export default {
  id: 'wf-124-native-mutual-action-plan-execution',
  label: 'Action plan',
  // The glyph is not in the shared PATHS map, so `iconPath` carries the path and
  // `icon` falls back to the shared mark. `components/ui.jsx` is not edited.
  icon: 'audit',
  iconPath: 'M9 5h6M9 12h6M9 19h6M4 5h.01M4 12h.01M4 19h.01',
  order: 124,
  Component: MutualActionPlan,
}
