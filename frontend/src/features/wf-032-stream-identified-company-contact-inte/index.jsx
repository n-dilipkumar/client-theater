import IntentStreamPage from './IntentStreamPage'
import { INTENT_ICON } from './primitives'

/**
 * Intent stream, registered (WF-032).
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in App.jsx, main.jsx, or
 * lib/features.js learns this feature's name, and no shared file has to be
 * touched to add or change it. That is the whole point: the twelve original
 * workflow branches each appended to a hard-coded ROUTES array, and all twelve
 * conflicted.
 *
 * `id` matches `FEATURE["id"]` in
 * `backend/dsr/features/wf032_stream_identified_company_contact_inte.py` exactly,
 * so the two halves of the feature are findable by one name.
 *
 * The nav glyph is passed as `iconPath` rather than as an `icon` name: "a company
 * going out to somewhere" is not in the shared `PATHS` map, and that file is not
 * ours to edit. `icon` carries the closest shared name as a fallback for any
 * consumer that reads only that field; the nav itself renders `iconPath`.
 */
export default {
  id: 'wf-032-stream-identified-company-contact-inte',
  label: 'Intent stream',
  icon: 'schema',
  iconPath: INTENT_ICON,
  order: 320,
  Component: IntentStreamPage,
}
