import MeetingLinks from './MeetingLinks'
import { VIDEO_ICON } from './icons'

/**
 * Meeting links, provisioned per booking (WF-059).
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in App.jsx, main.jsx, or
 * lib/features.js learns this feature's name, and no shared file has to be
 * touched to add or change it. That is the whole point: the twelve original
 * workflow branches each appended to a hard-coded ROUTES array, and all twelve
 * conflicted.
 *
 * `id` matches `FEATURE["id"]` in
 * `backend/dsr/features/wf059_provision_a_per_booking_video_conferen.py`
 * exactly, so the two halves of the feature are findable by one name.
 *
 * The nav glyph is passed as `iconPath` rather than as an `icon` name: a camera
 * on a stand is not in the shared `PATHS` map, and that file is not ours to
 * edit. `icon` carries the closest shared name as a fallback for any consumer
 * that reads only that field; the nav itself renders `iconPath`. See
 * `./icons.jsx`.
 */
export default {
  id: 'wf-059-provision-a-per-booking-video-conferen',
  label: 'Meeting links',
  icon: 'schema',
  iconPath: VIDEO_ICON,
  order: 290,
  Component: MeetingLinks,
}
