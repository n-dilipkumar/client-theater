import PageOutreach from './PageOutreach'
import { PAGE_OUTREACH_ICON } from './primitives'

/**
 * Page outreach (WF-106).
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in `App.jsx`, `main.jsx` or
 * `lib/features.js` learns this feature's name. That is the property that lets a
 * hundred features merge without three-way conflicts, and it is why a folder without
 * an `index.jsx` is invisible rather than broken.
 *
 * `id` matches `FEATURE["id"]` in
 * `backend/dsr/features/wf106_trigger_outreach_on_high_intent_page_visits.py` exactly.
 * The host reports a duplicate id in the UI rather than silently dropping one, so a
 * drift here is visible rather than a missing page.
 *
 * The folder name is the brief's, and it is deliberately not truncated to forty
 * characters the way the older feature folders are. The brief says its names are exact,
 * and the backend module sits at the untruncated name for the same reason.
 *
 * The nav glyph is passed as `iconPath` rather than as an `icon` name, because `PATHS`
 * in `components/ui.jsx` is module-private and appending to it from a hundred features
 * is the collision the feature host exists to prevent. `icon` still carries the closest
 * shared name so a client that ignores `iconPath` renders a magnifier rather than a
 * broken glyph.
 *
 * `order: 340` sorts this feature after the visitor-identification and intent-alerting
 * pages it reads from, and before the workflows that sit further down the buyer's
 * journey.
 */
export default {
  id: 'wf-106-trigger-outreach-on-high-intent-page-visits',
  label: 'Page outreach',
  icon: 'search',
  iconPath: PAGE_OUTREACH_ICON,
  order: 340,
  Component: PageOutreach,
}
