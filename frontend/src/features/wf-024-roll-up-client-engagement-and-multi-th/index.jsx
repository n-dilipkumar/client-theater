import ClientEngagement from './ClientEngagement'

/**
 * Client engagement: WF-024.
 *
 * This file is the whole registration. The frontend host globs every
 * `index.jsx` one folder under `features`, so the page appears in the nav by this
 * file existing and nothing in `App.jsx` or a routes array is edited. That is the
 * property that lets a hundred features merge without a conflict on one shared
 * file.
 *
 * The `id` is the hash route and matches the backend module's `FEATURE` id, so
 * both halves of the feature are findable by one name.
 *
 * `iconPath` rather than `icon`: `components/ui.jsx` carries a fixed `PATHS` map
 * and is shared, so a feature passes a `path` for a glyph the map does not have
 * rather than appending to it. This glyph is a bar chart over a baseline - the
 * shape of the report's "Client views over time" tile, which is the thing this
 * page leads with.
 */
export default {
  id: 'wf-024-roll-up-client-engagement-and-multi-th',
  label: 'Client engagement',
  icon: 'dashboard',
  iconPath: 'M4 20V10m5 10V4m5 16v-7m5 7V7',
  order: 240,
  Component: ClientEngagement,
}
