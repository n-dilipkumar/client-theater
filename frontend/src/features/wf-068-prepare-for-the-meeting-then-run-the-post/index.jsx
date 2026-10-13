import MeetingFollowup from './MeetingFollowup'
import { FOLLOWUP_ICON } from './primitives'

/**
 * Meeting prep and the follow-up sequence (WF-068).
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in `App.jsx`, `main.jsx` or
 * `lib/features.js` learns this feature's name, and no shared file has to be
 * touched to add or change it.
 *
 * `id` matches `FEATURE["id"]` in
 * `backend/dsr/features/wf068_prepare_for_the_meeting_then_run_the_post.py`
 * exactly, so the two halves of the feature are findable by one name.
 *
 * The nav glyph is passed as `iconPath` rather than as an `icon` name: a
 * checklist moving into a queue is not in the shared `PATHS` map, and that file is
 * not ours to edit. `icon` carries the closest shared name as a fallback for any
 * consumer that reads only that field; the nav itself renders `iconPath`.
 *
 * `order` sits between WF-067 and WF-069 so the meeting workflows read in ticket
 * order on the page: WF-060 through WF-067 are the meeting lifecycle, and this one
 * is what carries the call's outcome into a follow-up sequence.
 */
export default {
  id: 'wf-068-prepare-for-the-meeting-then-run-the-post',
  label: 'Meeting follow-up',
  icon: 'rooms',
  iconPath: FOLLOWUP_ICON,
  order: 680,
  Component: MeetingFollowup,
}