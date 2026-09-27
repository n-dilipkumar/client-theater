import CrmEngagementLog from './CrmEngagementLog'
import { ENGAGEMENT_LOG_ICON } from './icons'

/**
 * WF-037, registered.
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in App.jsx, main.jsx, or
 * lib/features.js learns this feature's name, and no shared file has to be touched to add
 * or change it. That is the whole point: the twelve original workflow branches each
 * appended to a hard-coded `ROUTES` array in App.jsx, and all twelve conflicted.
 *
 * `id` matches `FEATURE["id"]` in
 * `backend/dsr/features/wf037_log_a_single_buyer_engagement_event_in.py` exactly, which is
 * what lets the two halves of a feature be found by one name.
 *
 * The glyph is a row on the left, an arrow, and a bracket on the right — a single event
 * being written out to a system — which is not in the shared `PATHS` map, so `iconPath`
 * carries the path and `icon` falls back to the shared `database` mark for any consumer
 * that reads only that field. `components/ui.jsx` is not edited.
 */
export default {
  id: 'wf-037-log-a-single-buyer-engagement-event-in',
  label: 'CRM engagement log',
  icon: 'database',
  iconPath: ENGAGEMENT_LOG_ICON,
  order: 240,
  Component: CrmEngagementLog,
}
