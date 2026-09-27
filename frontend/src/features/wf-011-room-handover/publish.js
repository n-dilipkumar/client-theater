/**
 * Presentation logic for the publish flow, kept out of the components so the
 * board and the share dialog cannot drift apart.
 *
 * The status vocabulary and its meaning come from the researched workflow: a
 * room starts in draft with its public link disabled, and `live` and
 * `accepting` are mutually exclusive.
 *
 * This was `src/lib/publish.js` on the branch. It is a shared directory, so it
 * moved into the feature folder; nothing outside it imported the module.
 */

/** Badge tone per status. Tones reuse the shared Badge palette. */
const STATUS_TONES = {
  draft: 'neutral',
  live: 'insert',
  accepting: 'update',
  accepted: 'insert',
  disabled: 'restore',
  declined: 'delete',
}

export function statusTone(status) {
  return STATUS_TONES[status] || 'neutral'
}

/**
 * Short access-setting markers for a room card, as glyph/label pairs.
 *
 * A view limit is shown as a setting rather than as a working limit: counting
 * views is buyer-engagement work (WF-006) that this screen does not do, and
 * pretending otherwise would be a lie in the UI.
 */
export function accessMarkers(access) {
  if (!access) return []
  const markers = []
  if (access.expires_at) {
    markers.push({
      glyph: 'clock',
      label: access.expired ? `Expired ${access.expires_at}` : `Expires ${access.expires_at}`,
      tone: access.expired ? 'delete' : 'neutral',
    })
  }
  if (access.max_views) {
    markers.push({ glyph: 'eye', label: `Up to ${access.max_views} views`, tone: 'neutral' })
  }
  if (access.password_protected) {
    markers.push({ glyph: 'lock', label: 'Password protected', tone: 'update' })
  }
  if (access.require_identity_verification) {
    markers.push({ glyph: 'shield', label: 'Identity check', tone: 'update' })
  }
  return markers
}

/**
 * Copy text to the clipboard, reporting whether it actually worked.
 *
 * The Clipboard API needs a secure context and can be refused by the browser,
 * so the caller always shows the URL in a field as well: a seller must be able
 * to copy the link even when this returns `false`.
 */
export async function copyToClipboard(text) {
  try {
    if (!navigator.clipboard?.writeText) return false
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    return false
  }
}

/** Statuses that mean the public link is open, mirrored for the UI's own copy. */
export const PUBLIC_STATUSES = ['live', 'accepting', 'accepted']
