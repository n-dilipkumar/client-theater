import IntegrationMonitorPage from './IntegrationMonitor'
import { PULSE_ICON } from './icons'

/**
 * Integration monitoring, registered (WF-049).
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in App.jsx, main.jsx,
 * or lib/features.js learns this feature's name, and no shared file has to be
 * touched to add or change it.
 *
 * `id` matches the backend module's `FEATURE["id"]` exactly, so the two halves
 * of the feature are findable by one name.
 *
 * The nav glyph is passed as `iconPath` rather than as an `icon` name: a
 * heartbeat over a baseline is not in the shared `PATHS` map, and that file is
 * not ours to edit. `icon` carries the closest shared name as a fallback for
 * any consumer that reads only that field. See `./icons.jsx`.
 */
export default {
  id: 'wf-049-monitor-integration-health-and-remaini',
  label: 'Integration monitor',
  icon: 'dashboard',
  iconPath: PULSE_ICON,
  order: 260,
  Component: IntegrationMonitorPage,
}
