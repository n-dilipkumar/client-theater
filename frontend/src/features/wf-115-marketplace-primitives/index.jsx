import MarketplacePage from './MarketplacePage'

/**
 * WF-115, registered.
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
 * `backend/dsr/features/wf115_publish_custom_workflow_triggers_and_actions.py`
 * exactly, which is what lets the two halves of a feature be found by one name.
 *
 * The glyph is not in the shared `PATHS` map, so `iconPath` carries the path and
 * `icon` falls back to the shared `schema` mark. `Icon` prefers `path`, and
 * `components/ui.jsx` is not edited.
 */
export default {
  id: 'wf-115-marketplace-primitives',
  label: 'Marketplace primitives',
  icon: 'schema',
  iconPath: 'M4 4h7v7H4V4zm9 0h7v7h-7V4zM4 13h7v7H4v-7zm12 3l4 4m0-4l-4 4',
  order: 115,
  Component: MarketplacePage,
}
