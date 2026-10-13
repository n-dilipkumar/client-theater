import { ESCALATION_ICON } from './primitives'
import SlaEscalation from './SlaEscalation'

export { SlaEscalation }

/**
 * SLA escalation (WF-109).
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/<id>/index.jsx` at build time, so nothing in App.jsx, main.jsx or
 * lib/features.js learns this feature's name, and no shared file has to be touched to add
 * or change it.
 *
 * `id` matches `FEATURE["id"]` in
 * `backend/dsr/features/wf109_escalate_on_sla_breach_with_a_pre_warning_head.py` exactly, so
 * the two halves of the feature are findable by one name.
 *
 * The nav glyph is passed as `iconPath` rather than as an `icon` name: a bell with a clock
 * hand is not in the shared `PATHS` map, and that file is not ours to edit. `icon` carries
 * the closest shared name as a fallback for any consumer that reads only that field.
 */
export default {
  id: 'wf-109-sla-escalation',
  label: 'SLA escalation',
  icon: 'audit',
  iconPath: ESCALATION_ICON,
  order: 177,
  Component: SlaEscalation,
}
