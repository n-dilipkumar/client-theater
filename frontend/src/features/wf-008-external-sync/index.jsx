import ExternalSync from './ExternalSync'
import { EXTERNAL_SYNC_ICON } from './icons'

/**
 * WF-008, registered.
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in App.jsx, main.jsx or
 * lib/features.js learns this feature's name, and no shared file has to be
 * touched to add or change it. On the original branch this page was reached by
 * appending one line to a hard-coded `ROUTES` array, which is the edit that made
 * twelve workflow branches mutually unmergeable.
 *
 * `id` is the hash route (`#/wf-008-external-sync`) and matches the backend
 * module's `FEATURE["id"]` exactly, so the two halves of the feature are
 * findable by one name.
 *
 * `icon` is a name the shared `PATHS` map already has, used as the fallback, and
 * `iconPath` carries this feature's own cloud glyph because `components/ui.jsx`
 * is shared and explicitly not for features to append to. See `./icons.js`.
 */
export default {
  id: 'wf-008-external-sync',
  label: 'External content',
  icon: 'database',
  iconPath: EXTERNAL_SYNC_ICON,
  order: 200,
  Component: ExternalSync,
}
