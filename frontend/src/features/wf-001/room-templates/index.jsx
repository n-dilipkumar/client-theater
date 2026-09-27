import RoomTemplates from './RoomTemplates'
import { WIZARD_ICON } from './icons'

/**
 * WF-001, registered.
 *
 * This is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in `App.jsx`,
 * `main.jsx` or `lib/features.js` learns this feature's name and no shared file
 * has to be touched. That is the whole point: the branch this was ported from put
 * the wizard on `frontend/src/pages/Rooms.jsx`, a file every workflow branch also
 * edited, which is what made twelve of them conflict and none merge.
 *
 * `id` matches `FEATURE["id"]` in `backend/dsr/features/wf001_rooms.py` exactly,
 * which is what lets the two halves of a feature be found by one name.
 *
 * The glyph is not in the shared `PATHS` map, so `iconPath` carries the path and
 * `icon` falls back to the shared `rooms` mark. `Icon` prefers `path`, and
 * `components/ui.jsx` is not edited.
 */
export default {
  id: 'wf-001-room-templates',
  label: 'Create a room',
  icon: 'rooms',
  iconPath: WIZARD_ICON,
  order: 110,
  Component: RoomTemplates,
}
