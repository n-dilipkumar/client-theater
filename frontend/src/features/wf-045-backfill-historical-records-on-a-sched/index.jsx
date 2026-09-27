import BackfillHistory from './BackfillHistory'
import { BACKFILL_ICON } from './primitives'

/**
 * Backfill history, resumable cursor and all (WF-045).
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in App.jsx, main.jsx, or
 * lib/features.js learns this feature's name, and no shared file has to be
 * touched to add or change it. That is the whole point: the twelve original
 * workflow branches each appended to a hard-coded ROUTES array, and all twelve
 * conflicted.
 *
 * `id` matches `FEATURE["id"]` in
 * `backend/dsr/features/wf045_backfill_historical_records_on_a_sched.py` exactly,
 * so the two halves of the feature are findable by one name.
 *
 * The nav glyph is passed as `iconPath` rather than as an `icon` name: "a
 * schedule with a resumable marker" is not in the shared `PATHS` map, and that
 * file is not ours to edit. `icon` carries the closest shared name as a fallback
 * for any consumer that reads only that field; the nav itself renders
 * `iconPath`.
 */
export default {
  id: 'wf-045-backfill-historical-records-on-a-sched',
  label: 'Backfill history',
  icon: 'database',
  iconPath: BACKFILL_ICON,
  order: 450,
  Component: BackfillHistory,
}
