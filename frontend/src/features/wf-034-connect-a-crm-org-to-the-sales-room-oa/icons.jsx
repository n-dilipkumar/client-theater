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
 * These are this feature's own vocabulary: the plug-in, the two linked rings of
 * an authorization, a consent screen, a keyhole, and a vendor endpoint answering
 * a health check. `warning` and `check` are platform-shaped and are deliberately
 * *not* re-declared here: the integrator promotes those once, into `ui.jsx`, and a
 * second copy in a feature folder is exactly the duplication the contract warns
 * about.
 *
 * A name this module knows renders from `path`; one it does not know passes
 * through as `name`, so `plus`, `trash`, `refresh` and the rest keep coming from
 * the shared set and this file stays a list of additions rather than a fork.
 *
 * Decorative and hidden from assistive technology, like every icon here: the
 * surrounding control always carries its own text label.
 */
const PATHS = {
  // A plug: the researched claim that a vendor is a plug-in, not a fork.
  plug: 'M9 3v5M15 3v5M7 8h10v3a5 5 0 01-10 0V8zM12 16v5',
  // Two interlocked rings: the code handed back for a token.
  link: 'M9.5 14.5l5-5M8 12l-1.5 1.5a3.5 3.5 0 005 5L13 17M16 12l1.5-1.5a3.5 3.5 0 00-5-5L11 7',
  // A consent screen with the grant button on it.
  consent: 'M3 5h18v12H3V5zm5 16h8M8 9h8M8 12h5',
  // A keyhole: the sealed credential, which is never rendered.
  keyhole: 'M12 3a4 4 0 014 4c0 1.3-.6 2.4-1.5 3.1V13H9.5v-2.9A4 4 0 0112 3zM9 15h6v2H9v-2zm2 4h2v2h-2v-2z',
  // A vendor endpoint being polled for health.
  pulse: 'M3 12h4l2-5 4 10 2-5h6',
}

export default function Glyph({ name, size = 18, className = '' }) {
  return <Icon name={name} size={size} className={className} path={PATHS[name]} />
}

/** The descriptor's nav glyph, exported so `index.jsx` and the page agree. */
export const CRM_CONNECTIONS_ICON = PATHS.plug

export { PATHS as ICON_PATHS }
