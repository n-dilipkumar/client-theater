/**
 * Glyphs this page draws, as path data.
 *
 * The shared `PATHS` map in `components/ui.jsx` is not ours to edit - a hundred
 * features each appending to it is exactly the conflict the feature host exists
 * to remove - so every glyph here is passed as a `path` instead. Each is
 * decorative and hidden from assistive technology; the control that wraps it
 * always carries its own text label, which is what the design floor requires.
 */

/** The nav glyph: a clock face with a message tail. */
export const REMINDER_ICON = 'M12 3a9 9 0 100 18 9 9 0 000-18zm0 4v5l3 2M3 20l3-1 1 3'

/** A clock: the researched offset, and the `Scheduled` status. */
export const clock = 'M12 3a9 9 0 100 18 9 9 0 000-18zm0 4v5l3 2'

/** An envelope: an email reminder. */
export const envelope =
  'M3 7l9-4 9 4-9 4-9-4zm0 5l9 4 9-4M3 17l9 4 9-4'

/** A phone: an SMS reminder. */
export const phone =
  'M7 3h10a1 1 0 011 1v16a1 1 0 01-1 1H7a1 1 0 01-1-1V4a1 1 0 011-1zm3 15h4'

/** A check: the reminder went out. */
export const sent = 'M4 12l5 5L20 6'

/** A cross: the reminder was skipped. */
export const skipped = 'M6 6l12 12M18 6L6 18'

/** A pause: a reminder whose moment has not arrived. */
export const pending = 'M9 6v12M15 6v12'

/** A gate: a restriction that refused. */
export const gate = 'M12 3l8 4v5c0 5-3.5 8-8 9-4.5-1-8-4-8-9V7l8-4z'

/** A reply: an inbound SMS forwarded by email. */
export const reply = 'M21 12a8 8 0 01-8 8H8l-5 3 1.5-5A8 8 0 1121 12z'

/** The person who books. */
export const booker = 'M12 12a4 4 0 100-8 4 4 0 000 8zm-8 8a8 8 0 0116 0'

/** Everyone at the meeting. */
export const guests = 'M9 11a3 3 0 100-6 3 3 0 000 6zm-7 8a7 7 0 0114 0M17 11a3 3 0 100-6M18 19h5a6 6 0 00-4-5.7'

/** The Cal.com workflow this reminder projects onto. */
export const workflow =
  'M4 6h5v5H4V6zm11 0h5v5h-5V6zM4 13h5v5H4v-5zm11 0h5v5h-5v-5zM9 8.5h6M6.5 11v2M17.5 11v2M9 15.5h6'

export default {
  reminder: REMINDER_ICON,
  clock,
  envelope,
  phone,
  sent,
  skipped,
  pending,
  gate,
  reply,
  booker,
  guests,
  workflow,
}
