import RoomConversationPage from './page'

/**
 * WF-132, registered.
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in `App.jsx`, `main.jsx` or
 * `lib/features.js` learns this feature's name and no shared file has to be touched
 * to add or change it.
 *
 * `id` matches `FEATURE["id"]` in
 * `backend/dsr/features/wf132_in_room_collaboration_comments_threads_tagging_and_champion.py`
 * exactly, so the two halves of the feature are findable by one name.
 *
 * The glyph is not in the shared `PATHS` map, so `iconPath` carries a speech
 * bubble and `icon` falls back to the shared mark. `components/ui.jsx` is not
 * edited.
 */
export default {
  id: 'wf-132-inroom-conversation',
  label: 'Room conversations',
  icon: 'schema',
  iconPath: 'M4 5h16v11H9l-5 4V5z',
  order: 132,
  Component: RoomConversationPage,
}
