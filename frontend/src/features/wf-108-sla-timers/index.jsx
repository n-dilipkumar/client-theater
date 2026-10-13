import SlaTimers from './SlaTimers'
import { SLA_ICON } from './primitives'

export { SlaTimers }

/**
 * SLA response timers (WF-108).
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/<id>/index.jsx` at build time, so nothing in App.jsx, main.jsx or
 * lib/features.js learns this feature's name, and no shared file has to be touched to add
 * or change it.
 *
 * `id` matches `FEATURE["id"]` in
 * `backend/dsr/features/wf108_run_sla_response_timers_with_office_hours.py` exactly, so the
 * two halves of the feature are findable by one name.
 *
 * The nav glyph is passed as `iconPath` rather than as an `icon` name: a clock with a
 * target mark is not in the shared `PATHS` map, and that file is not ours to edit. `icon`
 * carries the closest shared name as a fallback for any consumer that reads only that
 * field; the nav itself renders `iconPath`.
 */
export default {
  id: 'wf-108-sla-timers',
  label: 'SLA timers',
  icon: 'audit',
  iconPath: SLA_ICON,
  order: 175,
  Component: SlaTimers,
}
