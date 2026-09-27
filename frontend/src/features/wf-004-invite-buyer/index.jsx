import InviteBuyer from './InviteBuyer'
import { SHARE_ICON } from './icons'

/**
 * WF-004, registered.
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in `App.jsx`,
 * `main.jsx` or `lib/features.js` learns this feature's name and no shared file
 * has to be touched to add or change it. That is the whole point: the branch this
 * was ported from put a Share button on the rooms page and added a *Viewing as*
 * control inside `frontend/src/pages/Rooms.jsx`, a file every workflow branch
 * also edited, which is what made twelve of them conflict and none merge.
 *
 * `id` matches `FEATURE["id"]` in `backend/dsr/features/wf_004-invite-buyer.py`
 * exactly, which is what lets the two halves of a feature be found by one name.
 *
 * The glyph is not in the shared `PATHS` map, so `iconPath` carries the path and
 * `icon` falls back to the shared `rooms` mark. `Icon` prefers `path`, and
 * `components/ui.jsx` is not edited.
 */
export default {
  id: 'wf-004-invite-buyer',
  label: 'Invite buyers',
  icon: 'rooms',
  iconPath: SHARE_ICON,
  order: 210,
  Component: InviteBuyer,
}
