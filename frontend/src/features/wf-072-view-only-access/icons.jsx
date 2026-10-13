/**
 * The two glyphs WF-072 draws that are not in the shared `PATHS` map.
 *
 * `components/ui.jsx` is shared and may not be edited, so a feature passes a path with
 * `<Icon path={...} />` instead of appending to `PATHS`. The descriptor carries the same
 * path in `iconPath`.
 *
 * `DOWNLOAD_ICON` is a tray with an arrow into it: the download affordance. `LOCK_ICON` is a
 * padlock: the view-only state. Neither is an emoji, and both sit beside a text label on the
 * page, so the state is never carried by the glyph or by colour alone.
 */

export const DOWNLOAD_ICON =
  'M12 3v12m0 0l-4-4m4 4l4-4M4 17v2a2 2 0 002 2h12a2 2 0 002-2v-2'

export const LOCK_ICON =
  'M6 10V8a6 6 0 0112 0v2M5 10h14a1 1 0 011 1v9a1 1 0 01-1 1H5a1 1 0 01-1-1v-9a1 1 0 011-1z'

export const ROOM_ICON = 'M3 7l9-4 9 4-9 4-9-4zm0 5l9 4 9-4M3 17l9 4 9-4'
