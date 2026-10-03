import SlotApproval from './SlotApproval'
import { APPROVAL_ICON } from './primitives'

/**
 * Slot approval (WF-062).
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in App.jsx, main.jsx, or
 * lib/features.js learns this feature's name, and no shared file has to be
 * touched to add or change it. That is the whole point: the twelve original
 * workflow branches each appended to a hard-coded ROUTES array, and all twelve
 * conflicted.
 *
 * `id` matches `FEATURE["id"]` in
 * `backend/dsr/features/wf062_route_a_requested_slot_for_host_approv.py` exactly,
 * so the two halves of the feature are findable by one name.
 *
 * The nav glyph is passed as `iconPath` rather than as an `icon` name: "a clock
 * with a hand hovering over it" - a slot waiting for someone to say yes - is not
 * in the shared `PATHS` map, and that file is not ours to edit. `icon` carries
 * the closest shared name as a fallback for any consumer that reads only that
 * field; the nav itself renders `iconPath`.
 */
export default {
  id: 'wf-062-route-a-requested-slot-for-host-approv',
  label: 'Slot approval',
  icon: 'rooms',
  iconPath: APPROVAL_ICON,
  order: 620,
  Component: SlotApproval,
}