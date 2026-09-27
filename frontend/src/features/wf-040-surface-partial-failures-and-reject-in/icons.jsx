import { Icon } from '@/components/ui'

/**
 * The glyphs this feature needs that the shared `PATHS` map in
 * `components/ui.jsx` does not carry.
 *
 * The shared file is not ours to edit: a hundred features each appending a glyph to
 * it is the same collision the plugin host exists to remove. Worse, `Icon` falls
 * back to the `schema` glyph for a name it does not recognise, so leaving the names
 * as they were would have rendered four identical "list of lines" glyphs and told
 * nobody. The contract's answer is to pass a path.
 *
 * `sync-log` is the nav glyph: a stack of rows where one carries a warning mark,
 * which is the whole of this workflow - a batch that came back and one row in it
 * that needs attention.
 *
 * The two status glyphs are deliberately opposite: a tick that closes, and a
 * cross that does not. They are decorative, and every one of them sits beside its
 * text label, so the label is the meaning and a rep who cannot distinguish the
 * colours still reads "Failed".
 *
 * On the same 24px grid and at the same stroke weight as the shared set, so they sit
 * next to a shared glyph without looking borrowed.
 */
const PATHS = {
  'sync-log': 'M4 5h16M4 10h11M4 15h16M4 20h8M17.5 9.5l2 2 3.5-3.5',
  succeeded: 'M4 12.5l5 5 11-11',
  failed: 'M6 6l12 12M18 6L6 18',
  queued: 'M12 4a8 8 0 100 16 8 8 0 000-16zm0 4v4l3 2',
  needsAction: 'M12 8v5m0 3.5v.5M10.3 3.9L2.6 17.4A2 2 0 004.3 20.4h15.4a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z',
  property: 'M4 6h16M4 12h16M4 18h9M16.5 15.5l3 3 3-3',
  doc: 'M6 3h8l4 4v14H6V3zm8 0v4h4M9 12h6M9 16h6',
}

export default function Glyph({ name, size = 18, className = '' }) {
  return <Icon name={name} size={size} className={className} path={PATHS[name]} />
}

/** The descriptor's nav glyph, exported so `index.jsx` and the page agree. */
export const SYNC_LOG_ICON = PATHS['sync-log']

export { PATHS as ICON_PATHS }
