import ApprovalGatePage from './ApprovalGatePage'

/**
 * WF-119, registered.
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in App.jsx, main.jsx, or
 * lib/features.js learns this feature's name and no shared file is touched to
 * add or change it.
 *
 * The folder is one segment deep because that is the shape `lib/features.js`
 * globs (`../features/*\/index.jsx`). A deeper folder would compile and then be
 * silently undiscovered, which is the worst kind of failure to debug.
 *
 * `id` matches `FEATURE["id"]` in
 * `backend/dsr/features/wf119_approval_gate.py` exactly, which is what lets the
 * two halves of a feature be found by one name.
 *
 * The glyph is not in the shared `PATHS` map, so `iconPath` carries the path and
 * `icon` falls back to the shared `schema` mark. `Icon` prefers `path`, and
 * `components/ui.jsx` is not edited.
 */
export default {
  id: 'wf-119-approval-gate',
  label: 'Approval gate',
  icon: 'schema',
  iconPath: 'M12 3a9 9 0 100 18 9 9 0 000-18zM12 7v5l3 2',
  order: 119,
  Component: ApprovalGatePage,
}
