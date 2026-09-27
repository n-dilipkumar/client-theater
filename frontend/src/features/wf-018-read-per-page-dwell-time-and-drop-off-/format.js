/**
 * Formatting for WF-018, kept in one place so a dwell of 41 seconds reads the
 * same way in a bar's tooltip, in a table cell, and in a stat card.
 *
 * Two rules the design system cares about:
 *
 * * a number a reader must compare across rows gets a consistent unit, so
 *   `formatDuration` never mixes "41s" and "1m 24s" within one column without
 *   saying which it is doing;
 * * nothing here invents precision. The API rounds dwell to one decimal and a
 *   rate to four, and this renders what it is given rather than adding digits
 *   that are not in the data.
 */

/** Human duration from a count of seconds. `null` becomes an em dash. */
export function formatDuration(seconds) {
  if (seconds === null || seconds === undefined || Number.isNaN(Number(seconds))) return '—'
  const total = Math.max(0, Math.round(Number(seconds)))
  if (total === 0) return '0s'
  if (total < 60) return `${total}s`
  const minutes = Math.floor(total / 60)
  const rest = total % 60
  if (minutes < 60) return rest ? `${minutes}m ${rest}s` : `${minutes}m`
  const hours = Math.floor(minutes / 60)
  return `${hours}h ${minutes % 60}m`
}

/**
 * A rate as a percentage. One decimal only when the whole number would hide
 * something: 0.2 stays "20%", 0.1667 becomes "16.7%".
 */
export function formatRate(rate) {
  if (rate === null || rate === undefined) return '—'
  const percent = Number(rate) * 100
  if (!Number.isFinite(percent)) return '—'
  const rounded = Math.round(percent * 10) / 10
  return Number.isInteger(rounded) ? `${rounded}%` : `${rounded.toFixed(1)}%`
}

/** A count, with a thousands separator so a six-figure view count is readable. */
export function formatCount(count) {
  const value = Number(count)
  if (!Number.isFinite(value)) return '—'
  return value.toLocaleString()
}

/** A short label for an asset, including its page count or hosting. */
export function describeAsset(asset) {
  if (!asset) return 'Unknown asset'
  const kind = asset.type === 'video' ? 'Video' : 'PDF'
  if (asset.type === 'video') {
    return `${kind} · ${asset.selfHosted === false ? 'hosted elsewhere' : 'self-hosted'}`
  }
  const pages = Number(asset.pageCount || 0)
  return `${kind} · ${pages} page${pages === 1 ? '' : 's'}`
}

/**
 * Why a researched analytics block is not shown, in the seller's words.
 *
 * The backend sends a machine reason; a rep reads a sentence. Kept here rather
 * than in the component so the wording is in one place.
 */
export function unavailableReason(block, reason) {
  if (reason === 'single_page') {
    return 'PDF Analytics needs a multi-page document. This one has a single page, so there is no per-page curve.'
  }
  if (reason === 'unknown_asset_type') {
    return `${block} does not apply to this kind of asset.`
  }
  if (reason === 'not_self_hosted') {
    return 'Video Analytics covers self-hosted videos only. This one is played by another provider, so there is no watch time to average.'
  }
  return `${block} is not available for this asset.`
}
