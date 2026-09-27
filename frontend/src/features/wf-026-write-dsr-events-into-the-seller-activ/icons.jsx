import { Icon } from '@/components/ui'

/**
 * The glyphs this feature needs that the shared `PATHS` map in
 * `components/ui.jsx` does not carry.
 *
 * That file is shared, so a hundred features each appending to it is the same
 * collision the plugin host exists to remove. Worse, `Icon` falls back to the
 * `schema` glyph for a name it does not recognise, so leaving the names as they
 * were would render a page of identical "list of lines" glyphs and tell nobody.
 * The contract's answer is to pass a path: `<Icon path="..." />`, and `iconPath`
 * in the descriptor. That is what this module is.
 *
 * The paths are on the same 24px grid at the same stroke weight as the shared
 * set, so they sit next to a shared glyph without looking borrowed. They are this
 * feature's own vocabulary - an event card in a feed, a link out to a room, a
 * prospect - and belong next to the code that draws them. `warning` and `check`
 * are platform-shaped and are deliberately *not* re-declared here: the
 * integrator promotes those once, into `ui.jsx`, and a second copy in a feature
 * folder is exactly the duplication the contract warns about.
 *
 * `Glyph` is one expression rather than a table of shared names: a name this
 * module knows renders from `path`, and one it does not know passes through as
 * `name`, so `plus`, `trash`, `refresh` and the rest keep coming from the shared
 * set and this file stays a list of additions rather than a fork of it.
 *
 * Decorative and hidden from assistive technology, like every icon here: the
 * surrounding control always carries its own text label.
 */
const PATHS = {
  // An event card sitting in a chronological feed, with its link out.
  card: 'M4 5h16a1 1 0 011 1v9a1 1 0 01-1 1H4a1 1 0 01-1-1V6a1 1 0 011-1zm0 14h9v2H4v-2zM7 8h6v2H7V8zm0 4h10v1.5H7V12z',
  // A person, as Outreach's prospect object.
  prospect: 'M12 12a4 4 0 100-8 4 4 0 000 8zm-8 9a8 8 0 0116 0',
  // The two halves of the researched payload: a name and a body.
  template: 'M4 4h16v4H4V4zm0 6h10v2H4v-2zm0 4h16v2H4v-2zm0 4h7v2H4v-2z',
  // An event going out to somewhere, with the outbound arrow.
  outbound: 'M4 12h11M12 7l5 5-5 5M17 4h3v16h-3',
}

export default function Glyph({ name, size = 18, className = '' }) {
  return <Icon name={name} size={size} className={className} path={PATHS[name]} />
}

/** The descriptor's nav glyph, exported so `index.jsx` and the page agree. */
export const ACTIVITY_FEED_ICON = PATHS.card

export { PATHS as ICON_PATHS }
