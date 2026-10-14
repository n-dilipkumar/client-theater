import RoomSync from './RoomSync'

/**
 * WF-125, registered.
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in `App.jsx`, `main.jsx`
 * or `lib/features.js` learns this feature's name and no shared file has to be
 * touched to add or change it.
 *
 * `id` matches `FEATURE["id"]` in
 * `backend/dsr/features/wf125_crm_room_sync.py` exactly, so the two halves of
 * the feature are findable by one name.
 *
 * The glyph is not in the shared `PATHS` map, so `iconPath` carries the path and
 * `components/ui.jsx` is not edited.
 */
export default {
  id: 'wf-125-room-sync',
  label: 'CRM room sync',
  icon: 'rooms',
  iconPath: 'M4 4v6h6M20 20v-6h-6M20 8a8 8 0 0 0-14.3-3M4 16a8 8 0 0 0 14.3 3',
  order: 125,
  Component: RoomSync,
}
