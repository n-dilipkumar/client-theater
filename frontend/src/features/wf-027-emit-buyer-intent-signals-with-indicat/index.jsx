import IntentSignals from './IntentSignals'
import { SIGNAL_ICON } from './primitives'

/**
 * Intent signals, registered (WF-027).
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in App.jsx, main.jsx, or
 * lib/features.js learns this feature's name, and no shared file has to be
 * touched to add or change it. That is the whole point: the twelve original
 * workflow branches each appended to a hard-coded ROUTES array, and all twelve
 * conflicted.
 *
 * `id` matches `FEATURE["id"]` in
 * `backend/dsr/features/wf027_emit_buyer_intent_signals_with_indicat.py` exactly,
 * so the two halves of the feature are findable by one name.
 *
 * The nav glyph is passed as `iconPath` rather than as an `icon` name: "a signal
 * going out to somewhere" is not in the shared `PATHS` map, and that file is not
 * ours to edit. `icon` carries the closest shared name as a fallback for any
 * consumer that reads only that field; the nav itself renders `iconPath`.
 */
export default {
  id: 'wf-027-emit-buyer-intent-signals-with-indicat',
  label: 'Intent signals',
  icon: 'schema',
  iconPath: SIGNAL_ICON,
  order: 270,
  Component: IntentSignals,
}
