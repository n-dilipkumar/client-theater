import QualificationCapture from './QualificationCapture'

/**
 * In-room qualification capture (WF-123).
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in `App.jsx`, `main.jsx`
 * or `lib/features.js` learns this feature's name.
 *
 * `id` matches `FEATURE["id"]` in
 * `backend/dsr/features/wf123_in_room_qualification_capture.py` exactly. The host
 * reports a duplicate id in the UI rather than silently dropping one, so a drift
 * here is visible rather than a missing page.
 *
 * The nav glyph is passed as `iconPath` rather than as an `icon` name, because
 * `PATHS` in `components/ui.jsx` is module-private and appending to it from a
 * hundred features is the collision the feature host exists to prevent. `icon`
 * still carries the closest shared name so a client that ignores `iconPath`
 * renders a document rather than a broken glyph.
 *
 * `order: 268` sorts this feature next to the lead-qualification pages it reads
 * (WF-052 sits at 262), and before the routing and CRM features that consume the
 * band.
 */
export default {
  id: 'wf-123-qualification-capture',
  label: 'Qualification capture',
  icon: 'audit',
  iconPath: 'M9 3h6v3H9V3zM7 5H5a1 1 0 00-1 1v14a1 1 0 001 1h14a1 1 0 001-1V6a1 1 0 00-1-1h-2M8 13l3 3 5-5',
  order: 268,
  Component: QualificationCapture,
}
