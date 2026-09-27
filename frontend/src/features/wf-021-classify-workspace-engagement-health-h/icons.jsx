import { Icon } from '@/components/ui'

/**
 * The glyphs this feature needs that the shared `PATHS` map in
 * `components/ui.jsx` does not carry.
 *
 * The shared file is not ours to edit: a hundred features each appending a glyph
 * to it is the same collision the plugin host exists to remove. Worse, `Icon`
 * falls back to the `schema` glyph for a name it does not recognise, so leaving
 * the names as they were would have rendered four identical "list of lines"
 * glyphs and told nobody. The contract's answer is to pass a path.
 *
 * The four bucket glyphs are a temperature scale read left to right, because the
 * thing the Trend column measures is engagement losing heat over time. They are
 * decorative: every one of them sits beside its text label, and the label is the
 * meaning. A rep who cannot distinguish the colours still reads "Cooling".
 *
 * `trend` is the nav glyph, a trend line over a baseline.
 *
 * On the same 24px grid and at the same stroke weight as the shared set, so they
 * sit next to a shared glyph without looking borrowed.
 */
const PATHS = {
  hot: 'M12 3c2.5 3.4 5 5.8 5 8.7A5 5 0 0112 17a5 5 0 01-5-5.3C7 8.8 9.5 6.4 12 3z',
  warm: 'M12 4.5a7.5 7.5 0 100 15 7.5 7.5 0 000-15zm0 3.5v6.2M9.2 8.5L12 11.2l2.8-2.7',
  cooling: 'M15.5 4.5a8 8 0 10-7 13.6 8 8 0 007-13.6zM9 15.5L12 12l3 3.5',
  cold: 'M12 3v18M4.2 7.5l15.6 9M19.8 7.5l-15.6 9M12 6.6l-2-2m2 2l2-2m-2 12.8l-2 2m2-2l2 2',
  trend: 'M3 17l5-6 4 3 5-7 4 4M3 21h18',
  caret: 'M8 10l4 4 4-4',
}

export default function Glyph({ name, size = 18, className = '' }) {
  return <Icon name={name} size={size} className={className} path={PATHS[name]} />
}

/** The descriptor's nav glyph, exported so `index.jsx` and the page agree. */
export const TREND_ICON = PATHS.trend

export { PATHS as ICON_PATHS }
