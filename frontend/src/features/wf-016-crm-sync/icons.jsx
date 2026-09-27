import { Icon } from '@/components/ui'

/**
 * The glyphs this feature needs that the shared `PATHS` map in
 * `components/ui.jsx` does not carry.
 *
 * The branch appended seven of them - `webhook`, `automation`, `activity`,
 * `power`, `link`, `warning`, `check` - to that file. It is shared, so twelve
 * features each appending to it is the same collision the plugin host exists to
 * remove. Worse, `Icon` falls back to the `schema` glyph for a name it does not
 * recognise, so leaving the names as they were would have rendered seven
 * identical "list of lines" glyphs and told nobody. The contract's answer is to
 * pass a path: `<Icon path="..." />`, and `iconPath` in the descriptor. That is
 * what this module is.
 *
 * The paths are the branch's own, on the same 24px grid at the same stroke
 * weight as the shared set, so they sit next to a shared glyph without looking
 * borrowed. Feature-private, and deliberately not a promotion candidate on its
 * own: `warning` and `check` are platform-shaped and an integrator will promote
 * them once. The three things that *are* this feature's own vocabulary - the
 * webhook, the automation rule, the activity log - belong next to the code that
 * draws them. See `./primitives.jsx` for the promotion set that is unambiguous.
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
  webhook: 'M9 8a3 3 0 116 0 3 3 0 01-6 0zM6.7 10.7L4 15h7l-2.7-4.3zM17.3 10.7L14.6 15H21l-2.7-4.3zM7.5 17.5v2a2 2 0 002 2h5a2 2 0 002-2v-2',
  automation: 'M13 3L5 14h6l-1 7 8-11h-6l1-7z',
  activity: 'M3 12h4l2.5-7 5 14L17 12h4',
  power: 'M12 4v8M7.5 6.2a7 7 0 109 0',
  link: 'M10 13a4 4 0 006 .5l2-2a4 4 0 10-6-6l-1 1M14 11a4 4 0 00-6-.5l-2 2a4 4 0 106 6l1-1',
  warning: 'M12 4l9 16H3l9-16zm0 6v4m0 3v.01',
  check: 'M4 12.5l5 5L20 6.5',
}

export default function Glyph({ name, size = 18, className = '' }) {
  return <Icon name={name} size={size} className={className} path={PATHS[name]} />
}

/** The descriptor's nav glyph, exported so `index.jsx` and the page agree. */
export const WEBHOOK_ICON = PATHS.webhook

export { PATHS as ICON_PATHS }
