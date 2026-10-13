import DataRoomTiers from './DataRoomTiers'

/**
 * WF-122, registered.
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in `App.jsx`, `main.jsx`
 * or `lib/features.js` learns this feature's name and no shared file has to be
 * touched to add or change it.
 *
 * `id` matches `FEATURE["id"]` in
 * `backend/dsr/features/wf122_tiered_disclosure.py` exactly, so the two halves of
 * the feature are findable by one name.
 *
 * The glyph is not in the shared `PATHS` map, so `iconPath` carries the path and
 * `components/ui.jsx` is not edited.
 *
 * The page is the seller's view. A counterparty reaching the room through an
 * access link is served by `GET /api/wf-122/me`, which reads the HttpOnly cookie
 * the redemption route set. That viewer surface has no route of its own in this
 * product yet, so this descriptor registers only the seller page.
 */
export default {
  id: 'wf-122-tiered-disclosure',
  label: 'Tiered disclosure',
  icon: 'rooms',
  iconPath: 'M4 6h16M4 12h16M4 18h10',
  order: 122,
  Component: DataRoomTiers,
}
