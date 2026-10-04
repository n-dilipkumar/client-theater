import IntentAlerts from './IntentAlerts'
import { ALERT_ICON } from './primitives'

/**
 * Intent alerts and routing (WF-133).
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in `App.jsx`, `main.jsx`
 * or `lib/features.js` learns this feature's name. That is the property that lets
 * a hundred features merge without three-way conflicts, and it is why a folder
 * without an `index.jsx` is invisible rather than broken.
 *
 * `id` matches `FEATURE["id"]` in
 * `backend/dsr/features/wf133_real_time_buyer_intent_alerting_and_routing.py`
 * exactly. The host reports a duplicate id in the UI rather than silently
 * dropping one, so a drift here is visible rather than a missing page.
 *
 * The nav glyph is passed as `iconPath` rather than as an `icon` name, because
 * `PATHS` in `components/ui.jsx` is module-private and appending to it from a
 * hundred features is the collision the feature host exists to prevent. `icon`
 * still carries the closest shared name so a client that ignores `iconPath`
 * renders a bell rather than a broken glyph.
 *
 * `order: 330` sorts this feature after the CRM and visitor-identification pages
 * it reads, and before the workflows that sit further down the buyer's journey.
 */
export default {
  id: 'wf-133-real-time-buyer-intent-alerting-and-routing',
  label: 'Intent alerts',
  icon: 'rooms',
  iconPath: ALERT_ICON,
  order: 330,
  Component: IntentAlerts,
}
