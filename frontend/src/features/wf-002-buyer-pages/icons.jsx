import { Icon } from '@/components/ui'

/**
 * WF-002's own glyphs, kept inside the feature folder.
 *
 * The fragment catalogue names an `icon` per fragment, and those names are not in
 * the shared set in `components/ui.jsx` - which is correct, because that file is
 * shared and a hundred features appending to its `PATHS` map is precisely the
 * collision the feature host exists to prevent. The contract's answer for a
 * glyph the set does not have is `<Icon path="..." />`, so that is what this does.
 *
 * This map is feature-private and is not a promotion candidate. It is the visual
 * identity of this feature's fragment catalogue: the 25 shipped fragments and the
 * console chrome below them. No other feature in the product has a fragment
 * catalogue, so putting these in a shared icon map would be adding vocabulary
 * nobody else can use. The two things in this feature that *are* platform-shaped
 * - `Note` and `Toggle` - live in `./primitives.jsx`, deliberately apart from
 * this, so an integrator knows exactly what to promote. See that file's comment
 * and the Jev gate recorded there.
 *
 * `Glyph` is one expression rather than a lookup table of the shared names: a
 * name this module knows uses `path`, and a name it does not know is passed
 * through as `name`, so `chevron`, `plus`, `trash` and the rest keep coming from
 * the shared set. An unrecognised name falls through to the shared component's
 * own `schema` glyph, exactly as it would anywhere else.
 *
 * Decorative and hidden from assistive technology, like every icon here: the
 * surrounding control always carries its own text label.
 */
const PATHS = {
  // -- chrome the editor itself uses --------------------------------------- //
  page: 'M6 3h9l4 4v14H6zM15 3v4h4',
  drag: 'M9 6h.01M9 12h.01M9 18h.01M15 6h.01M15 12h.01M15 18h.01',
  up: 'M12 19V5M6 11l6-6 6 6',
  down: 'M12 5v14M6 13l6 6 6-6',
  publish: 'M4 18v1a1 1 0 001 1h14a1 1 0 001-1v-1M12 3v12M8 7l4-4 4 4',
  eye: 'M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7zM12 15a3 3 0 100-6 3 3 0 000 6z',
  lock: 'M6 11h12v9H6zM9 11V8a3 3 0 016 0v3',
  back: 'M19 12H5M11 6l-6 6 6 6',
  check: 'M4 12l5 5L20 6',
  warning: 'M12 4l9 16H3zM12 10v4M12 17h.01',
  info: 'M12 21a9 9 0 100-18 9 9 0 000 18zM12 11v5M12 8h.01',

  // -- Digital Sales Room set (11) ----------------------------------------- //
  text: 'M4 6h16M4 10h16M4 14h10',
  welcome: 'M4 20l8-16 8 16M8 14h8',
  timeline: 'M7 4v16M7 7h5M7 12h5M7 17h5M18 5v14',
  video: 'M3 6h12a1 1 0 011 1v10a1 1 0 01-1 1H3a1 1 0 01-1-1V7a1 1 0 011-1zM16 10l6-3v10l-6-3',
  gallery: 'M3 5h18v14H3zM3 16l5-5 4 4 3-3 6 6',
  'document-gallery': 'M6 3h9l4 4v14H6zM15 3v4h4M9 12h6M9 16h6',
  'pdf-preview': 'M6 3h9l4 4v14H6zM15 3v4h4M10 12h5M10 16h5',
  'question-and-answer': 'M4 5h16v11H9l-5 4zM9 9a3 3 0 013 3',
  'header-main': 'M3 5h18v6H3zM3 13h18M3 17h12',
  'header-user': 'M4 5h16v6H4zM12 13a3 3 0 100 6 3 3 0 000-6zM6 20a6 6 0 0112 0',
  'our-team': 'M9 11a3 3 0 100-6 3 3 0 000 6zM3 20a6 6 0 0112 0M16 11a2.5 2.5 0 100-5M17 14a5 5 0 014 6',

  // -- Digital Sales Room Analytics set (10) ------------------------------- //
  'activity-log': 'M4 6h16M4 10h16M4 14h10M4 18h7',
  'documents-statistics': 'M6 3h9l4 4v14H6zM15 3v4h4M9 18v-4M12 18v-7M15 18v-3',
  'engagement-chart': 'M3 20h18M6 20v-8M11 20V7M16 20v-6',
  'frequency-chart': 'M3 20h18M6 20v-6M10 20V8M14 20v-9M18 20V4',
  'latest-activity': 'M12 21a9 9 0 100-18 9 9 0 000 18zM12 7v5l4 2',
  'most-active-visitors': 'M4 20V9M9 20V4M14 20v-8M19 20v-11',
  navigation: 'M4 12h16M14 6l6 6-6 6',
  'room-general': 'M4 5h16v14H4zM4 10h16M9 15h6',
  'room-statistics': 'M4 20h16M4 20V4M8 16v-4M12 16v-7M16 16v-3',
  'room-trend': 'M3 17l6-6 4 4 8-8M15 7h6v6',

  // -- DSR Fragments set: console chrome (4) ------------------------------ //
  'page-bar': 'M3 4h18v4H3zM3 12h8M3 16h14M3 20h6',
  sidebar: 'M4 4h16v16H4zM10 4v16',
  'sidebar-trigger': 'M4 4h16v16H4zM13 8l-3 4 3 4',
  'vertical-navigation': 'M6 3h4v6H6zM6 15h4v6H6zM14 3h4v6h-4zM14 15h4v6h-4z',
}

export default function Glyph({ name, size = 18, className = '' }) {
  return <Icon name={name} size={size} className={className} path={PATHS[name]} />
}

/** The descriptor's nav glyph, exported so `index.jsx` and the page agree. */
export const PAGE_GLYPH = PATHS.page
