import OwnershipRouting from './OwnershipRouting'
import { OWNER_ICON } from './icons'

/**
 * Ownership routing, registered (WF-053).
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in App.jsx, main.jsx, or
 * lib/features.js learns this feature's name, and no shared file has to be
 * touched to add or change it. That is the whole point: the twelve original
 * workflow branches each appended to a hard-coded ROUTES array, and all twelve
 * conflicted.
 *
 * `id` matches the backend module's `FEATURE["id"]` exactly, so the two halves of
 * the feature are findable by one name.
 *
 * The nav glyph is passed as `iconPath` rather than as an `icon` name: the
 * "person inside a routing ring" glyph is not in the shared `PATHS` map, and that
 * file is not ours to edit. `icon` carries the closest shared name as a fallback
 * for any consumer that reads only that field; the nav itself renders `iconPath`.
 */
export default {
  id: 'wf-053-route-a-booking-to-the-owner-of-the-cr',
  label: 'Ownership routing',
  icon: 'rooms',
  iconPath: OWNER_ICON,
  order: 260,
  Component: OwnershipRouting,
}
