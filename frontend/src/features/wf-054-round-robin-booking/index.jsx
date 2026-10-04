import RoundRobinBooking from './RoundRobinBooking'
import { ROUND_ROBIN_ICON } from './primitives'

/**
 * Round robin booking, registered (WF-054).
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in App.jsx, main.jsx or
 * lib/features.js learns this feature's name, and no shared file has to be
 * touched to add or change it.
 *
 * `id` matches the backend module's `FEATURE["id"]` exactly, so the two halves of
 * the feature are findable by one name.
 *
 * The nav glyph is passed as `iconPath` rather than as an `icon` name: the
 * "three reps on a ring" glyph is not in the shared `PATHS` map, and that file is
 * not ours to edit. `icon` carries the closest shared name as a fallback for any
 * consumer that reads only that field; the nav itself renders `iconPath`.
 */
export default {
  id: 'wf-054-round-robin-booking',
  label: 'Round robin booking',
  icon: 'rooms',
  iconPath: ROUND_ROBIN_ICON,
  order: 270,
  Component: RoundRobinBooking,
}