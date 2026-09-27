/**
 * Glyphs for the Play automations page, built here rather than added to
 * `components/ui.jsx`.
 *
 * `components/ui.jsx` is shared and a hundred features each appending to it is
 * the collision the feature host exists to prevent, so every glyph this page needs
 * that is not in the shared `PATHS` map is passed as a path through
 * `<Icon path=...>`. See the feature contract, "Shared UI".
 */

import { Icon } from '@/components/ui'

/**
 * A Play: a trigger on the left, an arrow, an action on the right.
 *
 * "A Play is an automation that generates a one-off action in response to an
 * internal or external signal" - so the shape is signal, then arrow, then the
 * action a seller ends up holding. `PLAY_ICON` is the fallback for a consumer that
 * reads only the shared `icon` name.
 */
export const PLAY_ICON =
  'M3 12h4l2-5 3 10 2-5h7M3 7h4M3 17h4'

/** The switch the research names: Settings -> Workflow -> Plays -> Edit Play. */
export const SWITCH_ICON = 'M8 5h8M8 5a3 3 0 106 0 3 3 0 10-6 0zm0 14h8m-8 0a3 3 0 106 0 3 3 0 10-6 0z'

/** A one-off task: a single item, not a list - the word "one-off" matters. */
export const TASK_ICON = 'M5 4h9l5 5v11H5V4zm9 0v5h5'

/** A webhook envelope, for the subscription and delivery rows. */
export const DELIVERY_ICON = 'M3 7l9 6 9-6M3 7v10h18V7'

/** A clock face, for the researched 15 second retry spacing. */
export const RETRY_ICON = 'M12 7v5l3 2M21 12a9 9 0 11-18 0 9 9 0 0118 0z'

const ICONS = {
  play: PLAY_ICON,
  switch: SWITCH_ICON,
  task: TASK_ICON,
  delivery: DELIVERY_ICON,
  retry: RETRY_ICON,
}

/** Draw one of this feature's glyphs, falling through to the shared set. */
export function Glyph({ name, size = 18, className = '' }) {
  return <Icon name={name} path={ICONS[name]} size={size} className={className} />
}
