import ContentInfluence from './ContentInfluence'
import { INFLUENCE_ICON } from './icons'

/**
 * WF-019, registered.
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in App.jsx, main.jsx, or
 * lib/features.js learns this feature's name, and no shared file has to be
 * touched to add or change it. That is the whole point: the twelve original
 * workflow branches each appended to a hard-coded ROUTES array, and all twelve
 * conflicted.
 *
 * `id` matches `FEATURE["id"]` in
 * `backend/dsr/features/wf019_rank_content_influence_and_associate_r.py`
 * exactly, which is what lets the two halves of the feature be found by one name.
 *
 * The ranked-bars glyph is not in the shared `PATHS` map, so `iconPath` carries
 * the path and `icon` falls back to the shared `database` mark. `Icon` prefers
 * `path`, and `components/ui.jsx` is not edited.
 */
export default {
  id: 'wf-019-rank-content-influence-and-associate-r',
  label: 'Content influence',
  icon: 'database',
  iconPath: INFLUENCE_ICON,
  order: 230,
  Component: ContentInfluence,
}
