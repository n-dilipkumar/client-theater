/**
 * Human-readable byte size.
 *
 * The branch added this to the shared client in `@/lib/api`. That file is read
 * by every feature, so it lives here instead; the integrator can promote it to
 * `lib/api.js` once as platform work if a second feature needs it.
 *
 * 1024-based, because that is what the API counts: `usage.bytes` and a stored
 * document's `size` are both sums of raw byte lengths.
 */
export function byteSize(bytes) {
  if (typeof bytes !== 'number' || Number.isNaN(bytes)) return '—'
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let value = bytes / 1024
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`
}
