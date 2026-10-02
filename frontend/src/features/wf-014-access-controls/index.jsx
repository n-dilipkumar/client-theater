import AccessControlsPage from './AccessControlsPage'

/**
 * WF-014, registered.
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in App.jsx, main.jsx, or
 * lib/features.js learns this feature's name and no shared file has to be touched
 * to add or change it.
 *
 * The folder is `wf-014-access-controls`, one segment deep, because that is the
 * shape `lib/features.js` globs (`../features/*\/index.jsx`). A deeper folder
 * would compile and then be silently undiscovered, which is the worst kind of
 * failure to debug.
 *
 * `id` matches `FEATURE["id"]` in `backend/dsr/features/wf014_access_controls.py`
 * exactly, which is what lets the two halves of a feature be found by one name.
 *
 * The glyph is not in the shared `PATHS` map, so `iconPath` carries the path and
 * `icon` falls back to the shared `schema` mark. `Icon` prefers `path`, and
 * `components/ui.jsx` is not edited.
 */
export default {
  id: 'wf-014-access-controls',
  label: 'Access controls',
  icon: 'schema',
  iconPath: 'M12 2v20M2 12h20M5 5l14 14M19 5L5 19',
  order: 140,
  Component: AccessControlsPage,
}
