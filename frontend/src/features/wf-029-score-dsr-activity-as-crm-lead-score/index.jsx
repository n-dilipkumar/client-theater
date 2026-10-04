import LeadScore from './LeadScore'
import { LEAD_SCORE_ICON } from './primitives'

/**
 * Lead score: DSR activity as CRM lead-score criteria (WF-029).
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in App.jsx, main.jsx or
 * lib/features.js learns this feature's name, and no shared file has to be
 * touched to add or change it.
 *
 * `id` matches `FEATURE["id"]` in
 * `backend/dsr/features/wf029_score_dsr_activity_as_crm_lead_score_cr.py` exactly,
 * so the two halves of the feature are findable by one name.
 *
 * The nav glyph is passed as `iconPath` rather than as an `icon` name: "a score being
 * added to by a buyer's activity" is not in the shared `PATHS` map, and that file is
 * not ours to edit. `icon` carries the closest shared name as a fallback for any
 * consumer that reads only that field; the nav itself renders `iconPath`.
 *
 * `order: 305` sorts this feature immediately after WF-030 (300), which is its
 * sibling from the same research file, and before WF-032 (320), so the two CRM
 * activity features read together rather than being split by six others.
 */
export default {
  id: 'wf-029-score-dsr-activity-as-crm-lead-score',
  label: 'Lead score',
  icon: 'schema',
  iconPath: LEAD_SCORE_ICON,
  order: 305,
  Component: LeadScore,
}
