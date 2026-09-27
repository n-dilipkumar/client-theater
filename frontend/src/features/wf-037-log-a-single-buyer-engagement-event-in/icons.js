/**
 * WF-037's own glyphs.
 *
 * The shared `PATHS` map in `components/ui.jsx` has no mark for a CRM write reaching a
 * third party, and the contract is explicit that a feature must not append to it. So these
 * live here, and the descriptor carries the one it needs as `iconPath`.
 *
 * Every path is drawn on the same 24-unit grid with `fill="none"` and `stroke`, which is
 * what the shared `Icon` renders, so they sit beside the shared marks without a seam. None
 * of them is an emoji and each is paired with a text label by the control that uses it.
 */

/** A row going out to a system: a record on the left, an arrow, a bracket on the right. */
export const WRITE_ICON =
  'M4 5h7v14H4zM14 12h4m0 0l-2.5-2.5M18 12l-2.5 2.5M20 5v14'

/** A queue: three rows, the middle one still waiting. */
export const QUEUE_ICON = 'M4 6h16M4 12h10M4 18h13M18 11v6m0 0l-2-2m2 2l2-2'

/** A warning triangle, for the rows that will not fix themselves. */
export const ALERT_ICON = 'M12 4l9 16H3l9-16zm0 6v4m0 3v.5'

/** A tick in a circle, for a create that landed. */
export const SYNCED_ICON = 'M12 3a9 9 0 100 18 9 9 0 000-18zm-2 9l2 2 4-4'

/** A plug, for a connector: a CRM this installation writes to. */
export const CONNECTOR_ICON = 'M9 3v6M15 3v6M6 9h12v3a6 6 0 01-12 0V9zM12 18v3'

/** The three vendors this build speaks, as one mark each. */
export const HUBSPOT_ICON = 'M4 8h5l3 4 3-4h5M4 16h16M7 12h10'
export const SALESFORCE_ICON = 'M5 18a4 4 0 01.6-7.95A5.5 5.5 0 0116.7 9 4 4 0 0117 18H5z'
export const DATAVERSE_ICON = 'M12 3c4.4 0 8 1.3 8 3s-3.6 3-8 3-8-1.3-8-3 3.6-3 8-3zM4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6'

export const ICONS = {
  alert: ALERT_ICON,
  connector: CONNECTOR_ICON,
  dataverse: DATAVERSE_ICON,
  hubspot: HUBSPOT_ICON,
  queue: QUEUE_ICON,
  salesforce: SALESFORCE_ICON,
  synced: SYNCED_ICON,
  write: WRITE_ICON,
}

/** The mark the descriptor uses. A row on the left, an arrow, a CRM on the right. */
export const ENGAGEMENT_LOG_ICON = WRITE_ICON
