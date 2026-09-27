/**
 * WF-018, registered.
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in `App.jsx`,
 * `main.jsx`, or `lib/features.js` learns this feature's name and no shared
 * file is touched to add or change it. That is the point: the twelve original
 * workflow branches each appended a `ROUTES` entry to the hard-coded array in
 * `App.jsx`, all twelve conflicted, and none of them merged.
 *
 * `id` matches `FEATURE["id"]` in
 * `backend/dsr/features/wf018_read_per_page_dwell_time_and_drop_off_.py`
 * exactly, including the trailing hyphen, which is what the build brief
 * specifies. The two halves of a feature are found by one name, so they have to
 * agree exactly rather than nearly.
 *
 * The glyph is not in the shared `PATHS` map, so `iconPath` carries the path and
 * `icon` falls back to the shared `schema` mark. `Icon` prefers `path`, and
 * `components/ui.jsx` is not edited.
 */
import PdfAnalytics from './PdfAnalytics'
import { PAGE_STACK_ICON } from './icons'

export default {
  id: 'wf-018-read-per-page-dwell-time-and-drop-off-',
  label: 'PDF analytics',
  icon: 'schema',
  iconPath: PAGE_STACK_ICON,
  order: 240,
  Component: PdfAnalytics,
}
