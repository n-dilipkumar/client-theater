import PublishBoard from './PublishBoard'
import { SHARE } from './icons'

/**
 * Take a room from draft to live and hand over the link (WF-011).
 *
 * This file is the whole registration. The frontend host globs every
 * `index.jsx` one folder under `features`, so the page appears in the nav by
 * this file existing and nothing in `App.jsx` or its `ROUTES` array is edited.
 * That is the property that let twelve workflow branches be written in twelve
 * worktrees without any of them conflicting.
 *
 * The `id` matches the backend module's `FEATURE["id"]` exactly, so the two
 * halves of the feature are findable by one name.
 *
 * `icon` is a name from the shared `PATHS` map; this feature's glyph is not in
 * it, so `iconPath` carries the path instead. `Icon` falls back to `path` when
 * given one, and `ui.jsx` is not edited. `icon` is the closest shared name and
 * only matters to a consumer that reads that field alone; the nav renders
 * `iconPath`.
 */
export default {
  id: 'wf-011-room-handover',
  label: 'Publish',
  icon: 'schema',
  iconPath: SHARE,
  order: 200,
  Component: PublishBoard,
}
