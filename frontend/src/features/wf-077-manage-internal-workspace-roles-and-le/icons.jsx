/**
 * Glyphs for WF-077, passed as paths rather than added to the shared `PATHS` map.
 *
 * `src/components/ui.jsx` is on the shared-file list, so a glyph that is not
 * already in it is passed with `<Icon path="..." />` and declared with `iconPath`
 * in the descriptor. See `docs/FEATURE-CONTRACT.md`.
 *
 * All of them are single-path, 24x24, stroke-based, and drawn at the same
 * weight as the shared set so a row of them does not look assembled from two
 * different icon families.
 */

/** Two people with a badge: internal workspace roles. */
export const ROLES_ICON =
  'M9 11a3 3 0 100-6 3 3 0 000 6zm-7 9a7 7 0 0114 0H2zM17 11a2.5 2.5 0 100-5 2.5 2.5 0 000 5zm-1 9h8a6 6 0 00-4-5.7V13h-4v1.3A6 6 0 0016 20z'

/** A key over a gate: a token, and what it is allowed through. */
export const SCOPE_ICON =
  'M14 2a6 6 0 00-5.7 8L2 16.3V22h5.7l1.3-1.3V19H11v-2h2v-2h2.3A6 6 0 1014 2zm2 5a1.5 1.5 0 110-3 1.5 1.5 0 010 3z'

/** A padlock over a document: least privilege, and the least it may reach. */
export const LEAST_PRIVILEGE_ICON =
  'M7 2h8l4 4v3h-2V7h-4V4H7v5H5V6a2 2 0 012-2h0zM5 11h14a2 2 0 012 2v7a2 2 0 01-2 2H5a2 2 0 01-2-2v-7a2 2 0 012-2zm6 3v3h2v-3h-2z'

/** A directory tree reaching a person: directory SSO. */
export const SSO_ICON =
  'M12 3a3 3 0 013 3H9a3 3 0 013-3zM3 8h7v3H3V8zm0 5h7v3H3v-3zm14-5h4v3h-4V8zm0 5h4v3h-4v-3zM12 15l5 7H7l5-7z'

/** A gauge: throttling, and the reset signal that goes with it. */
export const THROTTLE_ICON =
  'M12 14a2 2 0 100-4 2 2 0 000 4zm1.4-6.9l-1.4 1.4A4 4 0 1112 20a4 4 0 01-1.4-12.9zM11 2h2v3h-2V2zm-8 3l1.4 1.4-2.1 2.1L1 7.1 3 3zm18 0l2 4.1-1.3 1.4-2.1-2.1L20 5z'

/** A shield with a notch: the refusal catalogue. */
export const GUARD_ICON = 'M12 2l8 3v6c0 5-3.4 9.3-8 11-4.6-1.7-8-6-8-11V5l8-3zm-1 13l5-5-1.4-1.4L11 12.2 9.4 10.6 8 12l3 3z'

/** A branching arrow: a judgement call, argued rather than assumed. */
export const INFERENCE_ICON =
  'M6 3a3 3 0 013 3c0 1.3-.8 2.4-2 2.8V11a3 3 0 003 3h3.3a3 3 0 10.4 1.6l1.6-.7A5 5 0 0110 15.6V8.8A3 3 0 016 3zm12 15a3 3 0 100 6 3 3 0 000-6z'