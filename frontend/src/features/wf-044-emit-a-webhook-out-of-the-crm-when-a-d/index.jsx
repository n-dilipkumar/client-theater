import CrmWebhook from './CrmWebhook'
import { WEBHOOK_ICON } from './primitives'

/**
 * A CRM-side webhook, aimed at one room (WF-044).
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in App.jsx, main.jsx, or
 * lib/features.js learns this feature's name, and no shared file has to be
 * touched to add or change it. That is the point: the twelve original workflow
 * branches each appended to a hard-coded ROUTES array, and all twelve conflicted.
 *
 * `id` matches `FEATURE["id"]` in
 * `backend/dsr/features/wf044_emit_a_webhook_out_of_the_crm_when_a_d.py` exactly,
 * so the two halves of the feature are findable by one name.
 *
 * The nav glyph is passed as `iconPath` rather than as an `icon` name: "the CRM
 * sends a signed request at this room" is not in the shared `PATHS` map, and that
 * file is not ours to edit. `icon` carries the closest shared name as a fallback
 * for any consumer that reads only that field; the nav itself renders `iconPath`.
 *
 * `order: 310` sorts this feature after the ones already on main, whose orders
 * run 40 to 300, so this one lands at the end of the group rather than
 * reshuffling everybody else's position.
 */
export default {
  id: 'wf-044-emit-a-webhook-out-of-the-crm-when-a-d',
  label: 'CRM webhook',
  icon: 'schema',
  iconPath: WEBHOOK_ICON,
  order: 310,
  Component: CrmWebhook,
}
