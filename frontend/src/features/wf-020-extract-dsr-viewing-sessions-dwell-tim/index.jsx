import ViewingSessions from './ViewingSessions'
import { TABS } from './icons'

/**
 * WF-020, registered.
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in App.jsx, main.jsx or
 * lib/features.js learns this feature's name, and no shared file has to be
 * touched to add or change it. That is the whole point: the workflow this was
 * built from would otherwise have appended an entry to the hard-coded `ROUTES`
 * array in App.jsx, which is what made all twelve original workflow branches
 * conflict and none of them merge.
 *
 * `id` matches `FEATURE["id"]` in
 * `backend/dsr/features/wf020_extract_dsr_viewing_sessions_dwell_tim.py`
 * exactly, which is what lets the two halves of a feature be found by one name.
 *
 * The glyph is not in the shared `PATHS` map, so `iconPath` carries the path and
 * `icon` falls back to the shared `audit` mark. `Icon` prefers `path`, and
 * `components/ui.jsx` is not edited.
 */
export default {
  id: 'wf-020-extract-dsr-viewing-sessions-dwell-tim',
  label: 'Viewing sessions',
  icon: 'audit',
  iconPath: TABS,
  order: 230,
  Component: ViewingSessions,
}
