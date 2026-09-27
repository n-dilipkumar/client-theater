import { Icon } from '@/components/ui'

/**
 * The glyphs this feature needs that the shared `PATHS` map in
 * `components/ui.jsx` does not carry.
 *
 * The contract's answer to a missing glyph is a path, not an entry in the shared
 * map: `<Icon path="..." />`, and `iconPath` in the descriptor. `components/ui.jsx`
 * is shared, and a hundred features each appending to it is the same collision
 * the plugin host exists to remove - worse, `Icon` falls back to the `schema`
 * glyph for a name it does not recognise, so leaving the names as they were would
 * have rendered a page of identical "list of lines" icons and told nobody.
 *
 * The paths sit on the same 24px grid at the same stroke weight as the shared set,
 * so they read next to a shared glyph without looking borrowed. Feature-private,
 * and deliberately not a promotion candidate on its own: `check` and `warning` are
 * platform-shaped and an integrator will promote them once. The two things that
 * *are* this feature's own vocabulary - a package going into a CRM, and a key
 * whose index is still building - belong next to the code that draws them.
 * See `./primitives.jsx` for the promotion set that is unambiguous.
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
  package: 'M12 2l8 4v12l-8 4-8-4V6l8-4zm0 2.3L6.5 7 12 9.7 17.5 7 12 4.3zM6 8.5v8.6l5 2.5v-8.6l-5-2.5zm7 11.1l5-2.5V8.5l-5 2.5v8.6z',
  key: 'M14.5 3a6.5 6.5 0 00-6.2 8.5L3 16.8V21h4.2l1.3-1.3v-2h2v-2h2l1.5-1.5A6.5 6.5 0 1014.5 3zm2 5.5a1.5 1.5 0 110-3 1.5 1.5 0 010 3z',
  check: 'M4 12.5l5 5L20 6.5',
  warning: 'M12 4l9 16H3l9-16zm0 6v4m0 3v.01',
  install: 'M12 3v9m0 0l-4-4m4 4l4-4M4 17v2a2 2 0 002 2h12a2 2 0 002-2v-2',
  diff: 'M7 4v16M7 4L3 8m4-4l4 4M17 20V4m0 16l4-4m-4 4l-4-4',
  clock: 'M12 21a9 9 0 100-18 9 9 0 000 18zm0-14v5l3.5 2',
  link: 'M10 13a4 4 0 006 .5l2-2a4 4 0 10-6-6l-1 1M14 11a4 4 0 00-6-.5l-2 2a4 4 0 106 6l1-1',
  building: 'M4 21V6l7-3v18M11 10h9v11M4 21h17M7 9h1m-1 4h1m-1 4h1m6-3h1m-1 4h1',
}

export default function Glyph({ name, size = 18, className = '' }) {
  return <Icon name={name} size={size} className={className} path={PATHS[name]} />
}

/** The descriptor's nav glyph, exported so `index.jsx` and the page agree. */
export const PACKAGE_ICON = PATHS.package

export { PATHS as ICON_PATHS }
