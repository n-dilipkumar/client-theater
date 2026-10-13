import StartFromWebhook from './StartFromWebhook'
import { WEBHOOK_ICON } from './primitives'

/**
 * Start a workflow from an external system's webhook (WF-114).
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in App.jsx, main.jsx, or
 * lib/features.js learns this feature's name, and no shared file has to be
 * touched to add or change it.
 *
 * `id` matches `FEATURE["id"]` in
 * `backend/dsr/features/wf114_start_a_workflow_from_an_external_systems_webhook.py`
 * exactly, so the two halves of the feature are findable by one name.
 *
 * The nav glyph is passed as `iconPath` rather than as an `icon` name: an arrow
 * arriving at a starting bracket is not in the shared `PATHS` map, and that file
 * is not ours to edit. `icon` carries the closest shared name as a fallback for
 * any consumer that reads only that field; the nav itself renders `iconPath`.
 */
export default {
  id: 'wf-114-webhook-workflow-start',
  label: 'Start from webhook',
  icon: 'schema',
  iconPath: WEBHOOK_ICON,
  order: 1140,
  Component: StartFromWebhook,
}
