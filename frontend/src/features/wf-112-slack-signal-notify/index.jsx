import SlackSignalNotify from './SlackSignalNotify'
import { SLACK_ICON } from './primitives'

/**
 * Slack notification from a buyer signal (WF-112).
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in `App.jsx`, `main.jsx` or
 * `lib/features.js` learns this feature's name. That is the property that lets a
 * hundred features merge without three-way conflicts, and it is why a folder without
 * an `index.jsx` is invisible rather than broken.
 *
 * `id` matches `FEATURE["id"]` in
 * `backend/dsr/features/wf112_notify_the_internal_team_in_slack_from_a_buyer.py`
 * exactly. The host reports a duplicate id in the UI rather than silently dropping
 * one, so a drift here is visible rather than a missing page.
 *
 * The nav glyph is passed as `iconPath` rather than as an `icon` name, because
 * `PATHS` in `components/ui.jsx` is module-private and appending to it from a hundred
 * features is the collision the feature host exists to prevent. `icon` still carries
 * the closest shared name so a client that ignores `iconPath` renders a room rather
 * than a broken glyph.
 *
 * `order: 335` sorts this feature just after the intent-alerting page it reads, in
 * the same automation-and-engagement cluster.
 */
export default {
  id: 'wf-112-slack-signal-notify',
  label: 'Slack signals',
  icon: 'rooms',
  iconPath: SLACK_ICON,
  order: 335,
  Component: SlackSignalNotify,
}
