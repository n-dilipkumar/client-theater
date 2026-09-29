/**
 * Glyphs for the meeting changes page (WF-064).
 *
 * Drawn here rather than appended to the shared `PATHS` map in
 * `components/ui.jsx`, which is a shared file this feature may not edit. Each is
 * a plain SVG path string passed to `<Icon path=... />`, so nothing is registered
 * anywhere - the same escape hatch the nav glyph uses.
 */

/**
 * A calendar with an arrow leaving it: the move. Distinct from the shared
 * `refresh` loop, which reads as a retry rather than a reschedule.
 */
export const RESCHEDULE_ICON = 'M7 3v3m10-3v3M4 8h16M5 5h14a1 1 0 011 1v13a1 1 0 01-1 1H5a1 1 0 01-1-1V6a1 1 0 011-1zm6 5h8m-3-3l3 3-3 3'

/** A calendar with a struck-through entry: the cancel. */
export const CANCEL_ICON = 'M7 3v3m10-3v3M4 8h16M5 5h14a1 1 0 011 1v13a1 1 0 01-1 1H5a1 1 0 01-1-1V6a1 1 0 011-1zM9 14l6-4m0 4l-6-4'

/** A clock: the link, and whether it has expired. */
export const CLOCK_ICON = 'M12 21a9 9 0 100-18 9 9 0 000 18zm0-13v4l3 2'

/** A closed padlock: an expired link. */
export const EXPIRED_ICON = 'M7 11V8a5 5 0 0110 0v3M5 11h14v9H5v-9zm7 4v2'

/** An open padlock: a link that still works. */
export const OPEN_ICON = 'M7 11V8a5 5 0 019.6-2M5 11h14v9H5v-9zm7 4v2'

/** A paper plane: a notice the workflow trigger sent. */
export const SEND_ICON = 'M3 11l18-8-8 18-2-7-8-3z'

/** A cloud with an arrow: the downstream fan-out. */
export const FANOUT_ICON = 'M7 18a4 4 0 010-8 5 5 0 019.6-1.4A3.5 3.5 0 0117 18H7zm4-1l-2 2 2 2m4-4l2 2-2 2'

/** A CRM object: the Event row. */
export const CRM_ICON = 'M4 7c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3zm0 0v10c0 1.7 3.6 3 8 3s8-1.3 8-3V7M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3'

/** A question mark over a page: the inferences a reviewer can disagree with. */
export const INFERENCE_ICON = 'M12 8a4 4 0 116 3c-1 .7-1 1-1 2M12 17h.01M7 3h10a1 1 0 011 1v16a1 1 0 01-1 1H7a1 1 0 01-1-1V4a1 1 0 011-1z'

/** A three-dot chain: a recurring series. */
export const SERIES_ICON = 'M8 6a2 2 0 100-4 2 2 0 000 4zm8 20a2 2 0 100-4 2 2 0 000 4zM8 6v4a4 4 0 004 4h4a4 4 0 014 4v4'

export const GLYPHS = {
  reschedule: RESCHEDULE_ICON,
  cancel: CANCEL_ICON,
  clock: CLOCK_ICON,
  expired: EXPIRED_ICON,
  open: OPEN_ICON,
  send: SEND_ICON,
  fanout: FANOUT_ICON,
  crm: CRM_ICON,
  inference: INFERENCE_ICON,
  series: SERIES_ICON,
}

export default GLYPHS
