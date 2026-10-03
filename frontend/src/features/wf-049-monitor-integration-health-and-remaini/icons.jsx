import { Icon } from '@/components/ui'

/**
 * The glyphs this feature needs that the shared `PATHS` map in
 * `components/ui.jsx` does not carry.
 *
 * The shared file is not ours to edit: a hundred features each appending a
 * glyph to it is the same collision the plugin host exists to remove. The
 * contract's answer is to pass a path.
 *
 * Every glyph here is decorative: each one sits beside its text label, and the
 * label is the meaning. On the same 24px grid and at the same stroke weight as
 * the shared set, so they sit next to a shared glyph without looking borrowed.
 */
export const PATHS = {
  // The nav glyph: a heartbeat over a baseline, because the thing this page
  // watches is whether the integrations are still alive.
  pulse: 'M2 12h4l2-7 4 14 2-7h8',
  // A quota gauge: a partially filled circle.
  gauge: 'M12 3a9 9 0 109 9M12 3a9 9 0 019 9M12 3v4M12 12h6',
  // A change stream: a delta between two marks.
  stream: 'M3 6h12M3 12h9M3 18h14M18 9v6l3-3z',
  // An alert: a bell.
  alert: 'M12 3a6 6 0 00-6 6v3l-2 4h16l-2-4V9a6 6 0 00-6-6zM10 19a2 2 0 004 0',
  // Pause: two bars.
  pause: 'M9 5v14M15 5v14',
  // Play: a triangle, for resuming.
  play: 'M7 4l13 8-13 8V4z',
}

export default function Glyph({ name, path, size = 18, className = '' }) {
  return <Icon name={name} size={size} className={className} path={path || PATHS[name]} />
}

/** The descriptor's nav glyph, exported so `index.jsx` and the page agree. */
export const PULSE_ICON = PATHS.pulse
