/**
 * Glyphs this feature needs that the shared `PATHS` map in
 * `components/ui.jsx` does not carry.
 *
 * The contract is explicit: a feature that needs a new glyph passes a `path`,
 * because that map is shared and a hundred features editing it is exactly the
 * conflict the feature host exists to prevent. Each is a stroke on the shared
 * 24x24 grid so it lines up with the ones already in the set.
 *
 * Every glyph here stands for a node or a state the *research* names, so the
 * icons and the words come from the same sentence: a calendar with a record
 * arrow for the create node, a case file for the Related Object node, a list with
 * a bar for Update Field, a tag for Add to Campaign, a person for Update
 * Ownership.
 */

/** The writeback: a meeting going into a record. The nav glyph. */
export const WRITEBACK_ICON = 'M4 8h4l2-2h4l2 2h4v11H4V8zm8 4v5m0-5l-2 2m2-2l2 2'

/** `Create or Update Record`: the node the others must follow. */
export const ANCHOR_ICON = 'M12 3v12m0-12l-3 3m3-3l3 3M5 19h14'

/** `Create Event` / `Create Engagement`: a meeting on a record. */
export const EVENT_ICON = 'M7 3v3m10-3v3M4 9h16M5 6h14a1 1 0 011 1v12a1 1 0 01-1 1H5a1 1 0 01-1-1V7a1 1 0 011-1z'

/** `Related Object`: a record hanging off another record. */
export const RELATED_ICON = 'M6 5h5v4H6zM13 15h5v4h-5zM8.5 9v3a2 2 0 002 2H13'

/** `Update Field` / `Update Property`: two fields and an arrow between them. */
export const FIELD_ICON = 'M4 6h6v12H4zM14 6h6v12h-6M10 12h4m0 0l-2-2m2 2l-2 2'

/** `Add to Campaign`: a member joining a campaign. */
export const CAMPAIGN_ICON = 'M3 7l9-4 9 4-9 4-9-4zm4 4v5m10-5v5M7 19h10'

/** `Update Ownership`: a person changing hands. */
export const OWNER_ICON = 'M12 4a3 3 0 100 6 3 3 0 000-6zM5 20a7 7 0 0114 0M16 8h5m-2.5-2.5L21 8l-2.5 2.5'

/** A path the router takes: scheduled, not-scheduled, disqualified. */
export const PATH_ICON = 'M6 4v6a2 2 0 002 2h8a2 2 0 012 2v6M4 4h4M4 4v4M20 20h-4M20 20v-4'

/** Events History: the list, with a clock on it. */
export const HISTORY_ICON = 'M9 5h11M9 12h11M9 19h11M4 5h.01M4 12h.01M4 19h.01'

/** A retry: the arrow the Events History offers on a failure. */
export const RETRY_ICON = 'M4 10h11a5 5 0 010 10h-3M4 10l4-4M4 10l4 4'

/** The Sync Meeting Type toggle, which is org-wide. */
export const TOGGLE_ICON = 'M12 3a9 9 0 100 18 9 9 0 000-18zm0 5v9m0-9l-3 2m3-2l3 2'

/** A judgment call, for the inferences list. */
export const INFERENCE_ICON = 'M12 3a6 6 0 00-3.5 10.9V17h7v-3.1A6 6 0 0012 3zM9.5 20h5'

/** The CRM on the other end. */
export const CRM_ICON = 'M4 6c0-1.1 3.6-2 8-2s8 .9 8 2-3.6 2-8 2-8-.9-8-2zm0 0v12c0 1.1 3.6 2 8 2s8-.9 8-2V6M4 12c0 1.1 3.6 2 8 2s8-.9 8-2'

const GLYPHS = {
  writeback: WRITEBACK_ICON,
  anchor: ANCHOR_ICON,
  event: EVENT_ICON,
  related: RELATED_ICON,
  field: FIELD_ICON,
  campaign: CAMPAIGN_ICON,
  owner: OWNER_ICON,
  path: PATH_ICON,
  history: HISTORY_ICON,
  retry: RETRY_ICON,
  toggle: TOGGLE_ICON,
  inference: INFERENCE_ICON,
  crm: CRM_ICON,
}

/** A glyph by feature-local name, falling back to the shared schema glyph. */
export default function Glyph({ name, size = 16, className = '' }) {
  const path = GLYPHS[name] || GLYPHS.writeback
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
    >
      <path d={path} />
    </svg>
  )
}

/**
 * The glyph for a node name, from the server's own palette.
 *
 * Both vendors' spellings map to the same glyph on purpose: `Create Event` and
 * `Create Engagement` are the same node in two vocabularies, and drawing them
 * differently would imply they are different steps.
 */
export const NODE_GLYPH = {
  create_or_update_record: 'anchor',
  create_or_update_contact: 'anchor',
  create_event: 'event',
  create_engagement: 'event',
  related_object: 'related',
  update_field: 'field',
  update_property: 'field',
  add_to_campaign: 'campaign',
  update_ownership: 'owner',
}

/** Per-outcome tone, so an applied step and a skipped one never look alike. */
export const OUTCOME_TONE = {
  applied: 'insert',
  skipped: 'neutral',
  failed: 'delete',
}
