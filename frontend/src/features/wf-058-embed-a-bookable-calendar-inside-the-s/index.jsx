import BookableCalendar from './BookableCalendar'
import { CALENDAR_ICON } from './icons'

/**
 * Bookable calendar, registered (WF-058).
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
 * calendar-with-a-clock-hand glyph is not in the shared `PATHS` map, and that file
 * is not ours to edit. `icon` carries the closest shared name as a fallback for any
 * consumer that reads only that field; the nav itself renders `iconPath`. See
 * `./icons.js`.
 */
export default {
  id: 'wf-058-embed-a-bookable-calendar-inside-the-s',
  label: 'Bookable calendar',
  icon: 'rooms',
  iconPath: CALENDAR_ICON,
  order: 260,
  Component: BookableCalendar,
}
