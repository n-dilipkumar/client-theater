import DealHealthPage from './DealHealth'

/**
 * WF-134, registered.
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in `App.jsx`, `main.jsx`
 * or `lib/features.js` learns this feature's name and no shared file has to be
 * touched to add or change it.
 *
 * `id` matches `FEATURE["id"]` in
 * `backend/dsr/features/wf134_engagement_driven_deal_health_scoring_risk_detection_and_coaching.py`
 * exactly, so the two halves of the feature are findable by one name.
 *
 * The glyph is not in the shared `PATHS` map, so `iconPath` carries the path and
 * `components/ui.jsx` is not edited. The path draws a deal rising and falling,
 * which is what this page reads.
 */
export default {
  id: 'wf-134-deal-health',
  label: 'Deal health',
  icon: 'dashboard',
  iconPath: 'M3 17l5-6 4 3 5-8M3 21h18',
  order: 134,
  Component: DealHealthPage,
}
