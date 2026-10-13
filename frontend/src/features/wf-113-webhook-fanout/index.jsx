import WebhookFanout from './WebhookFanout'
import { INGEST_ICON } from './primitives'

/**
 * Webhook ingest and fan-out (WF-113).
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in App.jsx, main.jsx, or
 * lib/features.js learns this feature's name, and no shared file has to be
 * touched to add or change it.
 *
 * `id` matches `FEATURE["id"]` in
 * `backend/dsr/features/wf113_ingest_third_party_data_by_webhook_and_fan_it.py`
 * exactly, so the two halves of the feature are findable by one name.
 *
 * The nav glyph is passed as `iconPath` rather than as an `icon` name: an inbound
 * arrow entering a bracket is not in the shared `PATHS` map, and that file is not
 * ours to edit. `icon` carries the closest shared name as a fallback for any
 * consumer that reads only that field; the nav itself renders `iconPath`.
 */
export default {
  id: 'wf-113-webhook-fanout',
  label: 'Webhook ingest',
  icon: 'database',
  iconPath: INGEST_ICON,
  order: 1130,
  Component: WebhookFanout,
}
