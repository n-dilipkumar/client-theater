import NextStepsBoard from './NextStepsBoard'

/**
 * WF-136, registered.
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in `App.jsx`, `main.jsx`
 * or `lib/features.js` learns this feature's name and no shared file has to be
 * touched to add or change it.
 *
 * `id` matches `FEATURE["id"]` in
 * `backend/dsr/features/wf136_inroom_booking.py` exactly, so the two halves of
 * the feature are findable by one name.
 *
 * The page is the seller's side of the room integration, and only the seller
 * needs it in the sidebar: the buyer's half of the same workflow - the slot
 * grid and the one-click accept - is reached from the proposal row rather than
 * from a second route, because the seller needs to see what the buyer sees
 * before they send it.
 */
export default {
  id: 'wf-136-inroom-booking',
  label: 'Next steps',
  // The glyph is a calendar-plus, which is not in the shared PATHS map, so
  // `iconPath` carries the path and `components/ui.jsx` stays unedited.
  icon: 'audit',
  iconPath: 'M8 2v4M16 2v4M3 9h18M5 4h14a1 1 0 011 1v14a1 1 0 01-1 1H5a1 1 0 01-1-1V5a1 1 0 011-1zM12 13v4M10 15h4',
  order: 136,
  Component: NextStepsBoard,
}
