import TrendHealth from './TrendHealth'
import { TREND_ICON } from './icons'

/**
 * Engagement health, registered (WF-021).
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in App.jsx, main.jsx, or
 * lib/features.js learns this feature's name, and no shared file has to be
 * touched to add or change it. That is the whole point: the twelve original
 * workflow branches each appended to a hard-coded ROUTES array, and all twelve
 * conflicted.
 *
 * `id` matches the backend module's `FEATURE["id"]` exactly, so the two halves of
 * the feature are findable by one name.
 *
 * The nav glyph is passed as `iconPath` rather than as an `icon` name: a trend
 * line over a baseline is not in the shared `PATHS` map, and that file is not ours
 * to edit. `icon` carries the closest shared name as a fallback for any consumer
 * that reads only that field; the nav itself renders `iconPath`. See `./icons.jsx`.
 */
export default {
  id: 'wf-021-classify-workspace-engagement-health-h',
  label: 'Trend',
  icon: 'dashboard',
  iconPath: TREND_ICON,
  order: 230,
  Component: TrendHealth,
}
