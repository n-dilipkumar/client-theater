import MeetingWebhookFanout from './MeetingWebhookFanout'
import { WEBHOOK_ICON } from './primitives'

/**
 * Meeting webhook fan-out (WF-066).
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in App.jsx, main.jsx, or
 * lib/features.js learns this feature's name, and no shared file has to be
 * touched to add or change it.
 *
 * `id` matches `FEATURE["id"]` in
 * `backend/dsr/features/wf066_fan_out_meeting_events_via_signed_webhooks.py`
 * exactly, so the two halves of the feature are findable by one name.
 *
 * The nav glyph is passed as `iconPath` rather than as an `icon` name: an outbound
 * arrow leaving a bracket is not in the shared `PATHS` map, and that file is not
 * ours to edit. `icon` carries the closest shared name as a fallback for any
 * consumer that reads only that field; the nav itself renders `iconPath`.
 *
 * `order` sits after WF-065 so the meeting workflows read in ticket order on the
 * page: WF-061 through WF-065 are the meeting lifecycle, and this one is what
 * carries their events out of the product.
 */
export default {
  id: 'wf-066-fan-out-meeting-events-via-signed-webhooks',
  label: 'Meeting webhooks',
  icon: 'database',
  iconPath: WEBHOOK_ICON,
  order: 660,
  Component: MeetingWebhookFanout,
}
