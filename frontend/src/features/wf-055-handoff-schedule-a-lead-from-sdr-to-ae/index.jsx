/**
 * Handoff scheduler, registered (WF-055).
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in App.jsx, main.jsx or
 * lib/features.js learns this feature's name, and no shared file has to be
 * touched to add or change it.
 *
 * `id` matches the backend module's `FEATURE["id"]` exactly, so the two halves of
 * the feature are findable by one name.
 *
 * The nav glyph is passed as `iconPath` rather than as an `icon` name: the "one rep
 * handing a slot to another" glyph is not in the shared `PATHS` map, and that file
 * is not ours to edit. `icon` carries the closest shared name as a fallback for any
 * consumer that reads only that field; the nav itself renders `iconPath`.
 */

import HandoffScheduler from './HandoffScheduler'
import { HANDOFF_ICON } from './primitives'

export default {
  id: 'wf-055-handoff-schedule-a-lead-from-sdr-to-ae',
  label: 'Handoff scheduler',
  icon: 'rooms',
  iconPath: HANDOFF_ICON,
  order: 275,
  Component: HandoffScheduler,
}
