import CrmChangeStream from './CrmChangeStream'

/**
 * A signal going out over a stream, with the buffer behind it.
 *
 * "Change Data Capture channel" is not in the shared `PATHS` map, and that file
 * is not ours to edit: the contract's own rule is that a new glyph is passed as
 * a path rather than appended to the shared map, precisely so a hundred features
 * do not collide on one file. The shape is a channel with a transaction key
 * turning into commits underneath it, which is the thing this page is about.
 */
const CHANGE_STREAM_ICON =
  'M3 7h18M6 12h12M9 17h6M4 4v16a1 1 0 001 1h14a1 1 0 001-1V4'

/**
 * CRM change stream, buffered and committed (WF-043).
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in App.jsx, main.jsx, or
 * lib/features.js learns this feature's name, and no shared file has to be
 * touched to add or change it. That is the whole point: the twelve original
 * workflow branches each appended to a hard-coded ROUTES array, and all twelve
 * conflicted.
 *
 * `id` matches `FEATURE["id"]` in
 * `backend/dsr/features/wf043_stream_crm_record_changes_into_the_roo.py`
 * exactly, so the two halves of the feature are findable by one name.
 */
export default {
  id: 'wf-043-stream-crm-record-changes-into-the-roo',
  label: 'CRM change stream',
  icon: 'refresh',
  iconPath: CHANGE_STREAM_ICON,
  order: 280,
  Component: CrmChangeStream,
}
