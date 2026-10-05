/**
 * Icons this feature draws that are not in the shared `PATHS` map.
 *
 * `docs/FEATURE-CONTRACT.md` is explicit about the rule: pass a `path` rather than appending to
 * `PATHS`, because `components/ui.jsx` is shared and a hundred features editing it is the
 * collision the plugin host exists to prevent. So these are path strings here.
 *
 * They are drawn as decorative SVGs with `aria-hidden`, and every one of them sits beside a text
 * label. The descriptor's `icon` falls back to a shared mark and `iconPath` carries the glyph, so
 * the sidebar entry reads "Audience permissions" and the shape beside it is a group of figures
 * rather than a generic document.
 */

/** A pair of figures: an audience. The sidebar mark for this workflow. */
export const AUDIENCE_ICON =
  'M17 20h5v-2a3 3 0 00-5.4-1.8M17 20H7m10 0v-2c0-.7-.1-1.3-.4-1.8M7 20H2v-2a3 3 0 015.4-1.8M7 20v-2c0-.7.1-1.3.4-1.8m0 0a5 5 0 019.2 0M15 7a3 3 0 11-6 0 3 3 0 016 0z'

/** An eye with a bar: the view flag, and a viewer who is not admitted. */
export const VIEW_ICON = 'M2 12s4-7 10-7 10 7 10 7-4 7-10 7-10-7-10-7z'

/** A downward arrow into a tray: the download flag. */
export const DOWNLOAD_ICON = 'M12 3v12m0 0l-4-4m4 4l4-4M4 17v2a1 1 0 001 1h14a1 1 0 001-1v-2'

/** A folder tree: the folder item type and the ancestor auto-open. */
export const FOLDER_ICON =
  'M3 7a1 1 0 011-1h5l2 2h8a1 1 0 011 1v9a1 1 0 01-1 1H4a1 1 0 01-1-1V7z'

/** A document: the document item type. */
export const DOCUMENT_ICON = 'M9 12h6m-6 4h6M9 8h6M5 3h14a1 1 0 011 1v16a1 1 0 01-1 1H5a1 1 0 01-1-1V4a1 1 0 011-1z'

/** A link: a chain of two loops. */
export const LINK_ICON =
  'M13.8 10.2a4 4 0 010 5.7l-2.9 2.9a4 4 0 01-5.7-5.7l1.5-1.4m5.5-1.5a4 4 0 010-5.7L9.3 4.5a4 4 0 015.7 5.7l-1.5 1.4'

/** An at sign: the email gate and the domain match. */
export const EMAIL_ICON =
  'M16 12a4 4 0 11-8 0 4 4 0 018 0zm0 0l6 3v-3a9 9 0 10-3 6.7M16 12v5'

/** A shield with a check: a granted state. Never used alone; it always has a label. */
export const GRANTED_ICON = 'M9 12l2 2 4-4m7 2a9 9 0 11-18 0 9 9 0 0118 0z'
