import RolesAndScopes from './RolesAndScopes'
import { ROLES_ICON } from './icons'

/**
 * WF-077 registration. This file is the whole of the frontend registration.
 *
 * The host globs `src/features/*\/index.jsx` at build time, so nothing in
 * `App.jsx`, `main.jsx` or `lib/features.js` learns this feature's name and no
 * shared file has to be touched. `id` matches the backend module's
 * `FEATURE["id"]` exactly, so both halves of the feature are findable by one
 * name.
 *
 * The glyph is passed as `iconPath` rather than as an `icon` name, because a
 * two-person badge is not in the shared `PATHS` map and `components/ui.jsx` is
 * not this feature's to edit. `icon` carries the closest shared name as a
 * fallback for a consumer that reads only that field; the nav renders
 * `iconPath`. See `./icons.jsx`.
 */
export default {
  id: 'wf-077-manage-internal-workspace-roles-and-le',
  label: 'Roles and scopes',
  icon: 'audit',
  iconPath: ROLES_ICON,
  order: 280,
  Component: RolesAndScopes,
}